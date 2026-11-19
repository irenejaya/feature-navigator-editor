# -*- coding: utf-8 -*-
"""
FeatureNavEd - Dock Widget

Dockable panel for navigating and editing vector layer features one by one.

How the pieces fit together
---------------------------
* **Single feature** - the native ``QgsAttributeForm`` (SingleEditMode) is
  embedded in the panel. Edits go into the layer's *edit buffer* when you move
  to another feature, save, undo or stop editing - never straight to disk.
* **Multi-edit** - 2+ selected features on an editable layer switch the panel
  to :class:`~.multi_edit.MultiEditPanel`, which only writes on *Apply*.
* **Lock navigation** - decouples the navigator from the map selection:
  clicking around the canvas no longer moves it, the locked feature stays
  outlined, and multi-edit is disabled.
* **Field organizer** - hide fields you don't need (per layer, saved with the
  project) in both the single-feature form and the multi-edit panel.
* **List sync** - the navigation list follows the layer: features deleted
  (panel, Del key, attribute table), added, undone, committed or rolled back
  anywhere in QGIS are reflected without a manual reload.

Other modules: ``navigator_ui`` (widget construction), ``multi_edit``,
``find_bar``, ``field_organizer``, ``compat`` (Qt5/Qt6 shims), ``utils``.
"""

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:
    _xml_fromstring = None

import html

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QDockWidget, QMessageBox
from qgis.core import (
    QgsApplication, QgsFeatureRequest, QgsProject, QgsSettings, QgsVectorLayer,
)
from qgis.gui import QgsAttributeEditorContext, QgsAttributeForm, QgsHighlight

from .compat import (
    ALL_DOCK_AREAS, MB_NO, MB_YES, NO_GEOMETRY, SCROLL_AS_NEEDED, TOOLTIP_ROLE,
    form_mode, message_level,
)
from .field_organizer import (
    apply_field_order, apply_to_attribute_form, load_field_order,
    load_hidden_fields, organizable_fields, save_field_order, save_hidden_fields,
    uses_generated_layout,
)
from .measurements import describe_geometry
from .navigator_ui import NavigatorUiMixin
from .review import VisitedStore
from .utils import feature_exists, grey, is_null, make_scroll_area, values_equal

_LOCK_HIGHLIGHT_COLOR = QColor(255, 128, 0)


def _same_geometry(a, b):
    ga, gb = a.geometry(), b.geometry()
    if ga.isNull() or gb.isNull():
        return ga.isNull() and gb.isNull()
    # Exact equality: same type, vertices and order. isExactlyEqual()
    # replaces equals() from QGIS 4.2.
    exact = getattr(ga, 'isExactlyEqual', None) or ga.equals
    try:
        return exact(gb)
    except Exception:
        return ga.asWkt() == gb.asWkt()


class FeatureNavEdDockWidget(NavigatorUiMixin, QDockWidget):
    """Dock widget for navigating and editing features in a vector layer."""

    _SETTINGS_PREFIX = "featurenaved"

    def __init__(self, iface, parent=None):
        super().__init__("FeatureNavEd", parent)
        self.iface = iface

        self._layer = None                 # QgsVectorLayer being navigated
        self._layer_id = None
        self._layer_connections = []       # [(bound signal, slot)]
        self.feature_ids = []              # sorted navigation list
        self.current_index = -1            # -1 = no active feature ("NA")
        self._last_fid = None              # where to resume from the NA state
        self._last_index = -1
        self.sort_ascending = True
        self._layer_positions = {}         # layer id -> last fid
        self._layer_sort_fields = {}       # layer id -> sort field name

        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None
        self._form_error = None
        self._form_saving = False          # True while we push form values
        self._form_dirty = False           # typed values not yet in the buffer
        self._commit_snapshot = None       # new feature being committed
        self._fid_remap = {}               # temporary id -> committed id

        self._multi_edit_mode = False      # 2+ selected on an editable layer
        self._passive_count = 0            # 2+ selected on a read-only layer
        self._lock_navigation = False
        self._highlight = None
        self._suppress_selection = False   # True while we select features

        self._sort_keys = {}               # fid -> sort value when the list was built
        self._stale_sort = set()           # fids whose sort value changed since
        self._visited = VisitedStore()     # review tracking, saved on the layer
        self._seen_before = False          # current feature was visited before
        self._measure_text = ""
        self._hidden_fields = set()        # field names hidden in the panel
        self._field_order = []             # user's field order (empty = layer's)
        self._field_counts = (0, 0)        # (shown, total) for the hint
        self._content_mode = 'placeholder'

        # Structural layer changes (delete/add/commit/rollback) are coalesced
        # and processed once control returns to the event loop.
        self._pending_deleted = set()
        self._pending_full_reload = False
        self._structure_timer = self._single_shot(self._process_structure_changes)
        self._refresh_timer = self._single_shot(self._refresh_from_layer)
        self._field_visibility_timer = self._single_shot(self._apply_field_view)

        self.setAllowedAreas(ALL_DOCK_AREAS)
        self.setObjectName("FeatureNavEdDockWidget")
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._update_display()

        # Tear the form down BEFORE the C++ layer is destroyed.
        project = QgsProject.instance()
        try:
            self._layer_removed_signal = project.layerWillBeRemoved[str]
        except (KeyError, TypeError):
            self._layer_removed_signal = project.layerWillBeRemoved
        self._layer_removed_signal.connect(self._on_layer_will_be_removed)

    def _single_shot(self, slot):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(0)
        timer.timeout.connect(slot)
        return timer

    # =========================================================================
    # DRAG AND DROP
    # =========================================================================

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/qgis.layertreemodeldata'):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat('application/qgis.layertreemodeldata'):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        layer = self._extract_layer_from_drop(event.mimeData())
        if layer:
            self.layer_combo.setLayer(layer)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _extract_layer_from_drop(self, mime_data):
        if _xml_fromstring is None:
            return None
        if not mime_data.hasFormat('application/qgis.layertreemodeldata'):
            return None
        data = bytes(mime_data.data('application/qgis.layertreemodeldata'))
        try:
            root = _xml_fromstring(data.decode('utf-8'))
            for elem in root.iter():
                layer_id = elem.get('id')
                if layer_id:
                    layer = QgsProject.instance().mapLayer(layer_id)
                    if isinstance(layer, QgsVectorLayer):
                        return layer
        except Exception:
            pass
        return None

    # =========================================================================
    # SIGNALS / CLEANUP
    # =========================================================================

    def _connect_signals(self):
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self.active_layer_btn.clicked.connect(self._use_active_layer)
        self.sort_field_combo.currentIndexChanged.connect(self._on_sort_field_changed)
        self.sort_order_btn.clicked.connect(self._toggle_sort_order)

        self.toggle_edit_btn.clicked.connect(self._toggle_editing)
        self.save_edits_btn.clicked.connect(self._save_edits)
        self.undo_btn.clicked.connect(self._undo)
        self.redo_btn.clicked.connect(self._redo)
        self.revert_btn.clicked.connect(self._revert_current_feature)
        self.resort_btn.clicked.connect(self._reload_features)
        self.query_builder_btn.clicked.connect(self._toggle_query_builder)
        self.search_btn.toggled.connect(self._on_search_toggled)
        self._find_bar.findRequested.connect(self._find_match)
        self._fields_menu.aboutToShow.connect(self._refresh_field_organizer)
        self._fields_menu.aboutToHide.connect(self._update_fields_indicator)
        self._field_organizer.changed.connect(self._on_organizer_changed)
        self._fields_hint.linkActivated.connect(self._on_fields_hint_link)

        self.first_btn.clicked.connect(self._go_first)
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)
        self.last_btn.clicked.connect(self._go_last)
        self.feature_spin.valueChanged.connect(self._go_to_feature_number)
        self.flash_btn.clicked.connect(self._flash_current)
        self.del_btn.clicked.connect(self._delete_current_feature)
        self._form_placeholder.linkActivated.connect(self._on_placeholder_link)
        self.review_strip.jumpRequested.connect(self._go_to_index)
        self.next_unvisited_btn.clicked.connect(self._go_next_unvisited)

        self.scale_mode_combo.currentIndexChanged.connect(self._on_scale_mode_changed)
        self.iface.mapCanvas().scaleChanged.connect(self._on_canvas_scale_changed)

        self.lock_nav_cb.toggled.connect(self._on_lock_navigation_toggled)
        self.track_visited_cb.toggled.connect(self._on_track_visited_toggled)
        self.show_measurements_cb.toggled.connect(self._on_show_measurements_toggled)
        self.clear_visited_action.triggered.connect(self._clear_visited)
        self.auto_zoom_cb.toggled.connect(self._save_settings)
        self.enable_flash_cb.toggled.connect(self._save_settings)
        self.scale_spin.valueChanged.connect(self._save_settings)

        self._multi_panel.apply_btn.clicked.connect(self._on_multi_edit_apply)

    def cleanup(self):
        """Disconnect from QGIS before the plugin is unloaded."""
        for timer in (self._structure_timer, self._refresh_timer,
                      self._field_visibility_timer):
            timer.stop()
        self._clear_highlight()
        self._multi_edit_mode = False
        self._multi_panel.clear()
        self._accept_current_form()
        self._remove_current_form()
        self._disconnect_layer()
        self._layer = None
        self._layer_id = None
        for signal, slot in ((self._layer_removed_signal, self._on_layer_will_be_removed),
                             (self.iface.mapCanvas().scaleChanged, self._on_canvas_scale_changed)):
            try:
                signal.disconnect(slot)
            except Exception:
                pass

    # =========================================================================
    # LAYER HANDLING
    # =========================================================================

    def _use_active_layer(self):
        active = self.iface.activeLayer()
        if isinstance(active, QgsVectorLayer) and active is not self._layer:
            self.layer_combo.setLayer(active)
        else:
            self._reload_features()

    def _connect_layer(self, layer):
        slots = [
            ('selectionChanged', self._on_selection_changed),
            ('editingStarted', self._on_editing_state_changed),
            ('editingStopped', self._on_editing_state_changed),
            ('attributeValueChanged', self._on_attribute_value_changed),
            ('geometryChanged', self._on_geometry_changed),
            ('featuresDeleted', self._on_features_deleted),
            ('featureAdded', self._on_feature_added),
            ('afterRollBack', self._schedule_full_resync),
            ('afterCommitChanges', self._schedule_full_resync),
            ('subsetStringChanged', self._on_subset_changed),
            ('updatedFields', self._on_fields_updated),
            ('beforeCommitChanges', self._on_before_commit_changes),
            ('committedFeaturesAdded', self._on_committed_features_added),
            ('beforeModifiedCheck', self._on_before_commit),
        ]
        for name, slot in slots:
            signal = getattr(layer, name, None)
            if signal is None:
                continue
            try:
                signal.connect(slot)
                self._layer_connections.append((signal, slot))
            except Exception:
                pass
        try:
            stack = layer.undoStack()
            for signal in (stack.canUndoChanged, stack.canRedoChanged):
                signal.connect(self._update_toolbar_state)
                self._layer_connections.append((signal, self._update_toolbar_state))
        except Exception:
            pass

    def _disconnect_layer(self):
        connections, self._layer_connections = self._layer_connections, []
        for signal, slot in connections:
            try:
                signal.disconnect(slot)
            except Exception:
                pass

    def _on_layer_changed(self, layer):
        layer = layer if isinstance(layer, QgsVectorLayer) else None
        if layer is not None and layer is self._layer:
            return
        if self._layer is not None:
            self._remember_position()
            self._leave_multi_edit()
            self._accept_current_form()
            self._disconnect_layer()

        self._structure_timer.stop()
        self._refresh_timer.stop()
        self._pending_deleted.clear()
        self._pending_full_reload = False
        self._remove_current_form()
        self._multi_panel.clear()
        self._clear_highlight()

        self._layer = layer
        self._layer_id = layer.id() if layer is not None else None
        self.feature_ids = []
        self.current_index = -1
        self._last_fid = None
        self._last_index = -1
        self._passive_count = 0
        self._form_error = None
        self._seen_before = False
        self._stale_sort = set()
        self._sort_keys = {}
        self._visited.load(layer)
        self._hidden_fields = load_hidden_fields(layer)
        self._field_order = load_field_order(layer)
        self._multi_panel.set_hidden_fields(self._hidden_fields)
        self._multi_panel.set_field_order(self._field_order)
        self._recount_fields()

        self._populate_sort_combo(
            self._layer_sort_fields.get(self._layer_id) if layer else None)
        self._find_bar.set_layer(layer)
        if layer is not None:
            self._connect_layer(layer)
            self.feature_ids = self._query_feature_ids()
        self._restore_initial_position()

    def _remember_position(self):
        fid = self._current_fid()
        if fid is None:
            fid = self._last_fid
        if fid is not None and self._layer_id:
            self._layer_positions[self._layer_id] = fid

    def _restore_initial_position(self):
        """Pick the starting feature after a layer is loaded."""
        layer = self._layer
        if layer is None or not self.feature_ids:
            self._update_display()
            return
        # Follow an existing selection, unless navigation is locked.
        if not self._lock_navigation and layer.selectedFeatureCount() > 0:
            self._sync_with_selection()
            return
        saved = self._layer_positions.get(self._layer_id)
        idx = self._index_of(saved) if saved is not None else -1
        if idx >= 0:
            self.current_index = idx
            self._navigate_to_current()
            return
        self.current_index = -1
        self._update_display()

    def _populate_sort_combo(self, sort_field=None):
        layer = self._layer
        self.sort_field_combo.blockSignals(True)
        self.sort_field_combo.clear()
        if layer is not None:
            self.sort_field_combo.addItem("(Feature ID)", None)
            fields = layer.fields()
            for i, field in enumerate(fields):
                icon = None
                try:
                    icon = fields.iconForField(i)
                except Exception:
                    pass
                if icon is not None and not icon.isNull():
                    self.sort_field_combo.addItem(icon, field.name(), field.name())
                else:
                    self.sort_field_combo.addItem(field.name(), field.name())
                type_name = field.typeName() or ""
                self.sort_field_combo.setItemData(
                    self.sort_field_combo.count() - 1,
                    f"{field.name()} ({type_name})" if type_name else field.name(),
                    TOOLTIP_ROLE,
                )
            if sort_field:
                idx = self.sort_field_combo.findData(sort_field)
                if idx >= 0:
                    self.sort_field_combo.setCurrentIndex(idx)
        self.sort_field_combo.blockSignals(False)

    def _on_layer_will_be_removed(self, layer_id):
        if not isinstance(layer_id, str):
            try:
                layer_id = layer_id.id()
            except Exception:
                return
        self._layer_positions.pop(layer_id, None)
        self._layer_sort_fields.pop(layer_id, None)
        if layer_id != self._layer_id:
            return
        # The layer is still alive here: drop everything that points at it.
        # Pending edits are not pushed — QGIS has already asked the user.
        self._structure_timer.stop()
        self._refresh_timer.stop()
        self._field_visibility_timer.stop()
        self._multi_edit_mode = False
        self._multi_panel.clear()
        self._clear_highlight()
        self._remove_current_form()
        self._disconnect_layer()
        self._layer = None
        self._layer_id = None
        self.feature_ids = []
        self.current_index = -1
        self._last_fid = None
        self._last_index = -1
        self._passive_count = 0
        self._pending_deleted.clear()
        self._pending_full_reload = False
        self._hidden_fields = set()
        self._field_order = []
        self._sort_keys = {}
        self._stale_sort = set()
        self._visited.load(None)
        self._recount_fields()
        self._find_bar.set_layer(None)
        self._update_display()

    def _on_fields_updated(self, *args):
        layer = self._layer
        if layer is None:
            return
        sort_field = self.sort_field_combo.currentData()
        self._populate_sort_combo(sort_field)
        self._find_bar.refresh_fields()
        self._recount_fields()
        if self.sort_field_combo.currentData() != sort_field:
            self._reload_features()
        if self._multi_edit_mode:
            self._multi_panel.set_target(
                layer, layer.selectedFeatureIds(), force_rebuild=True)
        # The native form rebuilds its widgets after this signal: re-apply
        # our field order and hidden fields once it has.
        self._field_visibility_timer.start()

    # =========================================================================
    # FEATURE LIST
    # =========================================================================

    def _current_fid(self):
        if 0 <= self.current_index < len(self.feature_ids):
            return self.feature_ids[self.current_index]
        return None

    def _index_of(self, fid):
        try:
            return self.feature_ids.index(fid)
        except ValueError:
            return -1

    def _anchor_index(self):
        """Position to resume from when no feature is active."""
        if not self.feature_ids:
            return -1
        if self._last_fid is not None:
            idx = self._index_of(self._last_fid)
            if idx >= 0:
                return idx
        if self._last_index >= 0:
            return min(self._last_index, len(self.feature_ids) - 1)
        return -1

    def _fetch_sort_values(self):
        """``{fid: sort value}`` for every feature (None for NULL)."""
        layer = self._layer
        if layer is None:
            return {}
        sort_field = self.sort_field_combo.currentData()
        if sort_field and layer.fields().indexOf(sort_field) < 0:
            sort_field = None
        request = QgsFeatureRequest()
        if sort_field:
            request.setSubsetOfAttributes([sort_field], layer.fields())
        else:
            request.setNoAttributes()
        request.setFlags(NO_GEOMETRY)
        values = {}
        for feat in layer.getFeatures(request):
            if sort_field:
                value = feat[sort_field]
                values[feat.id()] = None if is_null(value) else value
            else:
                values[feat.id()] = feat.id()
        return values

    def _sorted_ids(self, keys):
        values = [(value, fid) for fid, value in keys.items() if value is not None]
        nulls = sorted(fid for fid, value in keys.items() if value is None)
        reverse = not self.sort_ascending
        try:
            values.sort(reverse=reverse)
        except TypeError:
            values.sort(key=lambda e: (str(e[0]), e[1]), reverse=reverse)
        # NULLs always go last, whatever the direction.
        return [fid for _, fid in values] + nulls

    def _query_feature_ids(self):
        """Fresh, sorted list. This is the only place the order is decided
        from current values (layer load, sort change, reload / re-sort)."""
        self._sort_keys = self._fetch_sort_values()
        self._stale_sort = set()
        self._update_resort_button()
        return self._sorted_ids(self._sort_keys)

    def _merge_feature_ids(self):
        """Pick up added / restored features without reordering anything.

        Every feature keeps the sort value it had when the list was built,
        so edits never move it and an undone delete returns to exactly the
        same place. Only features we have never seen use their current value.
        """
        current = self._fetch_sort_values()
        for fid, value in current.items():
            self._sort_keys.setdefault(fid, value)
        self._stale_sort &= set(current)
        self._update_resort_button()
        return self._sorted_ids({fid: self._sort_keys[fid] for fid in current})

    def _track_sort_change(self, fid, idx, value):
        sort_field = self.sort_field_combo.currentData()
        layer = self._layer
        if not sort_field or layer is None or fid not in self._sort_keys:
            return
        if layer.fields().indexOf(sort_field) != idx:
            return
        if values_equal(None if is_null(value) else value, self._sort_keys[fid]):
            self._stale_sort.discard(fid)
        else:
            self._stale_sort.add(fid)
        self._update_resort_button()

    def _update_resort_button(self):
        self.resort_btn.setVisible(bool(self._stale_sort))

    def _reload_features(self, *args):
        """Rebuild the list (sort/filter change), keeping the current feature."""
        self._structure_timer.stop()
        self._pending_deleted.clear()
        self._pending_full_reload = False
        current = self._current_fid()
        self.feature_ids = self._query_feature_ids()
        idx = self._index_of(current) if current is not None else -1
        if idx >= 0:
            self.current_index = idx
        elif current is not None:
            # Filtered out: park in the NA state, ready to resume nearby.
            self._last_fid = current
            self._last_index = self.current_index
            self.current_index = -1
            self._clear_highlight()
        self._update_display()

    def _on_sort_field_changed(self, *args):
        if self._layer_id:
            self._layer_sort_fields[self._layer_id] = self.sort_field_combo.currentData()
        self._reload_features()

    def _toggle_sort_order(self):
        self.sort_ascending = not self.sort_ascending
        self._update_sort_button()
        self._save_settings()
        self._reload_features()

    def _update_sort_button(self):
        if self.sort_ascending:
            self.sort_order_btn.setIcon(QgsApplication.getThemeIcon('/mActionArrowUp.svg'))
            self.sort_order_btn.setToolTip("Ascending — click to toggle")
        else:
            self.sort_order_btn.setIcon(QgsApplication.getThemeIcon('/mActionArrowDown.svg'))
            self.sort_order_btn.setToolTip("Descending — click to toggle")

    # --- keeping the list in sync with the layer ---------------------------

    def _on_features_deleted(self, fids):
        self._pending_deleted.update(fids)
        self._structure_timer.start()

    def _on_feature_added(self, *args):
        self._pending_full_reload = True
        self._structure_timer.start()

    def _schedule_full_resync(self, *args):
        self._pending_full_reload = True
        self._structure_timer.start()

    def _flush_structure_changes(self):
        if self._structure_timer.isActive():
            self._structure_timer.stop()
            self._process_structure_changes()

    def _process_structure_changes(self):
        layer = self._layer
        deleted, self._pending_deleted = self._pending_deleted, set()
        full, self._pending_full_reload = self._pending_full_reload, False
        if layer is None:
            return

        current = self._current_fid()
        old_index = self.current_index
        remap, self._fid_remap = self._fid_remap, {}
        if self._last_fid in remap:
            self._last_fid = remap[self._last_fid]
        for old, new in remap.items():
            if old in self._sort_keys:
                self._sort_keys.setdefault(new, self._sort_keys[old])
            if old in self._visited:
                self._visited.mark(new)
        if full:
            self.feature_ids = self._merge_feature_ids()
        elif deleted:
            self.feature_ids = [f for f in self.feature_ids if f not in deleted]
            self._stale_sort -= deleted
            self._update_resort_button()
        else:
            return

        if current in remap and self._index_of(remap[current]) >= 0:
            # Saved: the new feature got its permanent id - stay on it.
            self.current_index = self._index_of(remap[current])
            self._follow_current(layer)
        elif current is None:
            self._update_display()
        elif self._index_of(current) >= 0:
            self.current_index = self._index_of(current)
            self._update_display()
        elif current in deleted and self.feature_ids:
            # The feature on screen was deleted (panel button, Del key,
            # attribute table, ...): move on to the one that took its place.
            self.current_index = min(old_index, len(self.feature_ids) - 1)
            if self._multi_edit_mode:
                self._update_display()
            else:
                self._navigate_to_current(save_pending=False)
        else:
            # Gone for another reason (e.g. a new feature got a real id on
            # commit): don't jump to an unrelated feature.
            self._last_fid = None
            self._last_index = old_index
            self.current_index = -1
            self._clear_highlight()
            self._update_display()

        if full and self._multi_edit_mode:
            selected = layer.selectedFeatureIds()
            if len(selected) >= 2 and layer.isEditable():
                self._multi_panel.set_target(layer, selected)
            else:
                self._sync_with_selection()

    def _follow_current(self, layer):
        """Point the form/highlight at the current id without re-zooming."""
        fid = self._current_fid()
        feat = layer.getFeature(fid)
        self._last_fid = fid
        self._last_index = self.current_index
        if feat.isValid():
            self._show_feature_form(layer, feat, save_pending=False)
            self._update_highlight(feat)
        self._update_measurements(feat)
        self._update_display()

    def _on_before_commit_changes(self, *args):
        self._on_before_commit()
        # Remember a new (temporary id) feature so we can follow it to the
        # id it gets once committed.
        self._commit_snapshot = None
        layer = self._layer
        fid = self._current_fid()
        if fid is None:
            fid = self._last_fid
        if layer is not None and fid is not None and fid < 0:
            feat = layer.getFeature(fid)
            if feat.isValid():
                self._commit_snapshot = feat

    def _on_committed_features_added(self, layer_id, features):
        snapshot, self._commit_snapshot = self._commit_snapshot, None
        layer = self._layer
        if snapshot is None or layer is None or layer_id != self._layer_id:
            return
        try:
            skip = set(layer.primaryKeyAttributes())   # e.g. GeoPackage fid
        except Exception:
            skip = set()

        def comparable(feat):
            return [v for i, v in enumerate(feat.attributes()) if i not in skip]

        wanted = comparable(snapshot)
        for feat in features:
            candidate = comparable(feat)
            if (len(candidate) == len(wanted)
                    and all(values_equal(a, b) for a, b in zip(candidate, wanted))
                    and _same_geometry(feat, snapshot)):
                self._fid_remap[snapshot.id()] = feat.id()
                return

    def _on_attribute_value_changed(self, fid, idx=None, value=None, *args):
        self._track_sort_change(fid, idx, value)
        if self._form_saving:
            return
        if self._multi_edit_mode:
            if fid in self._multi_panel.fids:
                self._refresh_timer.start()
        elif fid == self._current_fid():
            self._refresh_timer.start()

    def _refresh_from_layer(self):
        """Show external edits (undo, field calculator, table, ...) on screen."""
        layer = self._layer
        if layer is None:
            return
        if self._multi_edit_mode:
            self._multi_panel.refresh_baseline()
            return
        fid = self._current_fid()
        form = self._feature_form
        if fid is None or form is None or self._feature_form_layer_id != layer.id():
            return
        feat = layer.getFeature(fid)
        if not feat.isValid():
            return
        self._form_saving = True
        try:
            form.setFeature(feat)
        except Exception:
            pass
        finally:
            self._form_saving = False
        if self._hidden_fields:
            apply_to_attribute_form(form, layer, self._hidden_fields)
        self._update_measurements(feat)
        self._update_display()

    def _on_geometry_changed(self, fid, *args):
        if fid != self._current_fid():
            return
        if self._highlight is not None:
            self._update_highlight()
        self._update_measurements()
        self._update_content()
        self._update_toolbar_state()

    def _on_subset_changed(self, *args):
        self._reload_features()

    # =========================================================================
    # NAVIGATION
    # =========================================================================

    def _go_to_index(self, idx):
        self._flush_structure_changes()
        if self._multi_edit_mode or not self.feature_ids:
            return
        idx = max(0, min(idx, len(self.feature_ids) - 1))
        if idx == self.current_index and self._passive_count < 2:
            return
        self.current_index = idx
        self._navigate_to_current()

    def _go_first(self):
        self._go_to_index(0)

    def _go_last(self):
        self._go_to_index(len(self.feature_ids) - 1)

    def _go_next(self):
        self._flush_structure_changes()
        if not self.feature_ids:
            return
        if self.current_index >= 0:
            if self.current_index + 1 < len(self.feature_ids):
                self._go_to_index(self.current_index + 1)
            return
        anchor = self._anchor_index()
        self._go_to_index(anchor + 1 if anchor >= 0 else 0)

    def _go_prev(self):
        self._flush_structure_changes()
        if not self.feature_ids:
            return
        if self.current_index >= 0:
            if self.current_index > 0:
                self._go_to_index(self.current_index - 1)
            return
        anchor = self._anchor_index()
        self._go_to_index(anchor - 1 if anchor > 0 else
                          (0 if anchor == 0 else len(self.feature_ids) - 1))

    def _go_to_feature_number(self, number):
        if number >= 1:
            self._go_to_index(number - 1)

    def _on_placeholder_link(self, link):
        if link == 'resume':
            anchor = self._anchor_index()
            if anchor >= 0:
                self._go_to_index(anchor)

    def _navigate_to_current(self, select=True, save_pending=True):
        """Show the feature at ``current_index``: select, zoom, form, highlight."""
        layer = self._layer
        fid = self._current_fid()
        if layer is None or fid is None:
            self._update_display()
            return
        feat = layer.getFeature(fid)
        if not feat.isValid():
            # Stale entry - drop it on the next sync.
            self._pending_deleted.add(fid)
            self._structure_timer.start()
            self._update_display()
            return

        self._last_fid = fid
        self._last_index = self.current_index
        self._seen_before = fid in self._visited
        if self.track_visited_cb.isChecked():
            self._visited.mark(fid)
        if select:
            self._select_only(layer, fid)
            self._passive_count = 0
        if feat.hasGeometry():
            if self.auto_zoom_cb.isChecked():
                self._zoom_to_feature(feat, layer)
            if self.enable_flash_cb.isChecked():
                self._flash_geometry(feat, layer)
        self._show_feature_form(layer, feat, save_pending)
        self._update_highlight(feat)
        self._update_measurements(feat)
        self._update_display()

    def _select_only(self, layer, fid):
        if list(layer.selectedFeatureIds()) == [fid]:
            return
        self._suppress_selection = True
        try:
            layer.selectByIds([fid])
        finally:
            self._suppress_selection = False

    def _scale_mode(self):
        return self.scale_mode_combo.currentData() or 'keep'

    def _zoom_to_feature(self, feature, layer):
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            return
        canvas = self.iface.mapCanvas()
        settings = canvas.mapSettings()
        mode = self._scale_mode()
        if mode == 'fit':
            try:
                extent = settings.layerExtentToOutputExtent(layer, geom.boundingBox())
            except Exception:
                extent = None
            if extent is not None and (extent.width() > 0 or extent.height() > 0):
                if extent.width() == 0 or extent.height() == 0:   # straight line
                    extent.grow(max(extent.width(), extent.height()) * 0.1)
                extent.scale(1.2)                                   # margin
                canvas.setExtent(extent)
                canvas.refresh()
                return
            # A single point has no extent: centre it at the current scale.
        try:
            center = settings.layerToMapCoordinates(layer, geom.centroid().asPoint())
        except Exception:
            return
        canvas.setCenter(center)
        if mode == 'fixed':
            canvas.zoomScale(self.scale_spin.value())
        canvas.refresh()

    def _flash_geometry(self, feature, layer):
        try:
            self.iface.mapCanvas().flashGeometries([feature.geometry()], layer.crs())
        except Exception:
            pass

    def _flash_current(self):
        layer = self._layer
        fid = self._current_fid()
        if layer is None or fid is None:
            return
        feat = layer.getFeature(fid)
        if not feat.isValid() or not feat.hasGeometry():
            return
        if self.auto_zoom_cb.isChecked():
            self._zoom_to_feature(feat, layer)
        self._flash_geometry(feat, layer)

    def _update_scale_controls(self, *args):
        fixed = self._scale_mode() == 'fixed'
        self.scale_spin.setEnabled(fixed)
        self.scale_spin.setToolTip(
            "Map scale used by auto-zoom" if fixed else
            "Current map scale (choose Fixed scale to set your own)")
        if not fixed:
            self._sync_scale_from_canvas()

    def _on_scale_mode_changed(self, *args):
        self._update_scale_controls()
        self._save_settings()

    def _on_canvas_scale_changed(self, scale):
        if self._scale_mode() != 'fixed':
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(round(scale))
            self.scale_spin.blockSignals(False)

    def _sync_scale_from_canvas(self):
        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(round(self.iface.mapCanvas().scale()))
        self.scale_spin.blockSignals(False)

    # =========================================================================
    # SELECTION SYNC  (and Lock navigation)
    # =========================================================================

    def _on_selection_changed(self, *args):
        if self._suppress_selection or self._lock_navigation:
            return
        self._sync_with_selection()

    def _sync_with_selection(self):
        """Follow the map selection: 1 → jump, 2+ → multi-edit, 0 → NA."""
        layer = self._layer
        if layer is None:
            return
        selected = list(layer.selectedFeatureIds())

        if len(selected) >= 2:
            if layer.isEditable():
                self._passive_count = 0
                self._enter_multi_edit_mode(layer, selected)
            else:
                self._leave_multi_edit()
                self._passive_count = len(selected)
                self._update_display()
            return

        self._passive_count = 0
        was_multi = self._multi_edit_mode
        self._leave_multi_edit()

        if not selected:
            current = self._current_fid()
            if current is not None and not feature_exists(layer, current):
                # Deleted rather than deselected: the list sync moves on.
                self._pending_deleted.add(current)
                self._structure_timer.start()
                return
            self._enter_na_state()
            return

        target = selected[0]
        idx = self._index_of(target)
        if idx < 0:
            self._flush_structure_changes()
            idx = self._index_of(target)
        if idx < 0:
            self.feature_ids = self._merge_feature_ids()
            idx = self._index_of(target)
            if idx < 0:          # e.g. hidden by the layer filter
                self._enter_na_state()
                return
        if idx == self.current_index and not was_multi:
            self._update_display()
            return
        self.current_index = idx
        self._navigate_to_current(select=False)

    def _enter_na_state(self):
        if self.current_index >= 0:
            self._accept_current_form()
            self._last_fid = self._current_fid()
            self._last_index = self.current_index
        self.current_index = -1
        self._clear_highlight()
        self._update_display()

    def _on_lock_navigation_toggled(self, checked):
        self._lock_navigation = bool(checked)
        self._save_settings()
        if self._lock_navigation:
            if self._multi_edit_mode:
                self._leave_multi_edit()
                if self._current_fid() is not None:
                    self._navigate_to_current(select=False)
            self._passive_count = 0
            self._update_highlight()
        else:
            self._clear_highlight()
        self._update_nav_title()
        self._update_display()

    def _update_nav_title(self):
        if self._lock_navigation:
            self._nav_group.setTitle("Navigate (locked)")
            self._nav_group.setToolTip(
                "Navigation is locked: map clicks and selections don't move the "
                "navigator.\nUnlock it from the Settings menu.")
        else:
            self._nav_group.setTitle("Navigate")
            self._nav_group.setToolTip("")

    # =========================================================================
    # LOCK HIGHLIGHT
    # =========================================================================

    def _update_highlight(self, feat=None):
        self._clear_highlight()
        layer = self._layer
        fid = self._current_fid()
        if (not self._lock_navigation or self._multi_edit_mode
                or layer is None or fid is None):
            return
        if feat is None or feat.id() != fid:
            feat = layer.getFeature(fid)
        if not feat.isValid() or not feat.hasGeometry():
            return
        try:
            highlight = QgsHighlight(self.iface.mapCanvas(), feat.geometry(), layer)
            highlight.setColor(_LOCK_HIGHLIGHT_COLOR)
            fill = QColor(_LOCK_HIGHLIGHT_COLOR)
            fill.setAlpha(40)
            highlight.setFillColor(fill)
            highlight.setWidth(3)
            highlight.show()
            self._highlight = highlight
        except Exception:
            self._highlight = None

    def _clear_highlight(self):
        highlight, self._highlight = self._highlight, None
        if highlight is not None:
            try:
                highlight.hide()
                self.iface.mapCanvas().scene().removeItem(highlight)
            except Exception:
                pass

    # =========================================================================
    # MULTI-EDIT  (2+ features selected on an editable layer)
    # =========================================================================

    def _enter_multi_edit_mode(self, layer, fids):
        if not self._multi_edit_mode:
            self._accept_current_form()
            self._multi_edit_mode = True
            self._clear_highlight()
        self._multi_panel.set_target(layer, fids)
        self._update_display()

    def _leave_multi_edit(self):
        """Close the multi-edit panel. Pending values are never applied."""
        if not self._multi_edit_mode:
            return
        pending = self._multi_panel.pending_count()
        self._multi_edit_mode = False
        self._multi_panel.reset()
        if pending:
            plural = "s" if pending != 1 else ""
            self._notify(
                f"Multi-edit closed — {pending} pending field change{plural} "
                "discarded (nothing was applied).")

    def _on_multi_edit_apply(self):
        if not self._multi_edit_mode or self._layer is None:
            return
        result = self._multi_panel.apply()
        if result is None:
            if self._layer is not None and not self._layer.isEditable():
                self._notify("The layer is not in edit mode.", "warning")
            return
        ok, n_fields, n_features = result
        if ok:
            plural = "s" if n_fields != 1 else ""
            self._notify(
                f"Updated {n_fields} field{plural} on {n_features} features. "
                "Use Save Edits to keep the changes.", "success")
        else:
            self._notify(
                "Some values could not be applied (check the field constraints). "
                "Nothing was changed.", "warning")

    # =========================================================================
    # FIELD ORGANIZER
    # =========================================================================

    def _refresh_field_organizer(self):
        layer = self._layer
        self._field_organizer.set_fields(
            organizable_fields(layer), self._hidden_fields, self._field_order,
            designed_form=layer is not None and not uses_generated_layout(layer))

    def _on_organizer_changed(self):
        layer = self._layer
        if layer is None:
            return
        self._hidden_fields = self._field_organizer.hidden_fields()
        save_hidden_fields(layer, self._hidden_fields)
        save_field_order(layer, self._field_organizer.field_order())
        self._field_order = load_field_order(layer)
        self._recount_fields()
        self._apply_field_view()

    def _apply_field_view(self):
        """Apply field order and hidden fields to the form and multi-edit."""
        layer = self._layer
        if layer is not None:
            if (self._feature_form is not None
                    and self._feature_form_layer_id == layer.id()):
                apply_field_order(self._feature_form, layer, self._field_order)
                apply_to_attribute_form(self._feature_form, layer, self._hidden_fields)
            self._multi_panel.set_field_order(self._field_order)
            self._multi_panel.set_hidden_fields(self._hidden_fields)
        self._update_fields_indicator()

    def _recount_fields(self):
        names = [name for name, _, _ in organizable_fields(self._layer)]
        hidden = sum(1 for name in names if name in self._hidden_fields)
        self._field_counts = (len(names) - hidden, len(names))

    def _update_fields_indicator(self, *args):
        shown, total = self._field_counts
        hidden = total - shown
        self.fields_btn.setEnabled(self._layer is not None)
        self.fields_btn.blockSignals(True)
        self.fields_btn.setChecked(hidden > 0)
        self.fields_btn.blockSignals(False)
        if hidden:
            self.fields_btn.setToolTip(
                f"Choose which fields to show ({hidden} of {total} hidden)")
        else:
            self.fields_btn.setToolTip("Choose which fields to show")
        show_hint = hidden > 0 and self._content_mode in ('form', 'multi')
        if show_hint:
            self._fields_hint.setText(
                grey(f"Showing {shown} of {total} fields") +
                " · <a href='organize'>Change</a>")
        self._fields_hint.setVisible(show_hint)

    def _on_fields_hint_link(self, link):
        if link == 'organize':
            self.fields_btn.showMenu()

    # =========================================================================
    # EDITING TOOLBAR
    # =========================================================================

    def _original_feature(self, layer, fid):
        """The feature as last saved (straight from the data provider)."""
        request = QgsFeatureRequest().setFilterFid(fid)
        for feat in layer.dataProvider().getFeatures(request):
            return feat if feat.isValid() else None
        return None

    def _current_unsaved_state(self):
        """'added', 'changed' or None for the current feature.

        QGIS keeps a feature flagged as changed even after a value is set
        back to what was saved, so changes are compared with the saved
        feature instead of trusting the flag.
        """
        layer = self._layer
        fid = self._current_fid()
        if (layer is None or fid is None or self._multi_edit_mode
                or not layer.isEditable()):
            return None
        buffer = layer.editBuffer()
        if buffer is None:
            return None
        try:
            if buffer.isFeatureAdded(fid):
                return 'added'
            attributes_changed = buffer.isFeatureAttributesChanged(fid)
            geometry_changed = buffer.isFeatureGeometryChanged(fid)
        except Exception:
            return None
        if self._form_dirty:
            return 'changed'
        if not (attributes_changed or geometry_changed):
            return None
        original = self._original_feature(layer, fid)
        if original is None:
            return 'changed'
        current = layer.getFeature(fid)
        fields = layer.fields()
        if attributes_changed:
            for idx in buffer.changedAttributeValues().get(fid, {}):
                source = fields.fieldOriginIndex(idx)
                if (0 <= source < len(original.attributes()) and not values_equal(
                        current.attribute(idx), original.attribute(source))):
                    return 'changed'
        if geometry_changed and not _same_geometry(current, original):
            return 'changed'
        return None

    def _revert_current_feature(self):
        """Discard unsaved changes to the current feature only (one undo step).
        A feature added in this editing session is removed, just like
        discarding all unsaved edits would."""
        layer = self._layer
        fid = self._current_fid()
        state = self._current_unsaved_state()
        if state is None:
            return
        if state == 'added':
            text = (f"Feature {fid} was added in this editing session.\n\n"
                    "Reverting removes it, as discarding unsaved edits would.")
        else:
            text = f"Discard the unsaved changes to feature {fid}?"
        reply = QMessageBox.question(self, "Revert Feature", text + "\n\nYou can Undo this.",
                                     MB_YES | MB_NO, MB_NO)
        if reply != MB_YES:
            return
        self._set_form_dirty(False)
        if state == 'added':
            layer.beginEditCommand(f"FeatureNavEd: revert feature {fid}")
            if layer.deleteFeature(fid):
                layer.endEditCommand()
                layer.triggerRepaint()
                self._pending_deleted.add(fid)
                self._flush_structure_changes()
            else:
                layer.destroyEditCommand()
                self._notify(f"Could not revert feature {fid}.", "warning")
            return

        buffer = layer.editBuffer()
        original = self._original_feature(layer, fid)
        current = layer.getFeature(fid)
        fields = layer.fields()
        attribute_changes = {}
        if original is not None:
            for idx in buffer.changedAttributeValues().get(fid, {}):
                source = fields.fieldOriginIndex(idx)
                if 0 <= source < len(original.attributes()):
                    value = original.attribute(source)
                    if not values_equal(current.attribute(idx), value):
                        attribute_changes[idx] = value
        geometry = None
        if (original is not None and buffer.isFeatureGeometryChanged(fid)
                and not _same_geometry(current, original)):
            geometry = original.geometry()
        if attribute_changes or geometry is not None:
            layer.beginEditCommand(f"FeatureNavEd: revert feature {fid}")
            ok = True
            for idx, value in attribute_changes.items():
                ok = layer.changeAttributeValue(fid, idx, value) and ok
            if geometry is not None:
                ok = layer.changeGeometry(fid, geometry) and ok
            if ok:
                layer.endEditCommand()
            else:
                layer.destroyEditCommand()
                self._notify(f"Could not revert feature {fid}.", "warning")
            layer.triggerRepaint()
        # Show the saved values (this also drops anything typed but unsaved).
        feat = layer.getFeature(fid)
        if self._feature_form is not None and feat.isValid():
            self._form_saving = True
            try:
                self._feature_form.setFeature(feat)
            finally:
                self._form_saving = False
            self._set_form_dirty(False)
            if self._hidden_fields:
                apply_to_attribute_form(self._feature_form, layer, self._hidden_fields)
        self._update_highlight(feat)
        self._update_measurements(feat)
        self._update_display()

    # --- review tracking ----------------------------------------------------

    def _go_next_unvisited(self):
        self._flush_structure_changes()
        if self._multi_edit_mode or not self.feature_ids:
            return
        total = len(self.feature_ids)
        start = self.current_index if self.current_index >= 0 else self._anchor_index()
        for offset in range(1, total + 1):
            idx = (start + offset) % total
            if self.feature_ids[idx] not in self._visited:
                self._go_to_index(idx)
                return
        self._notify(f"All {total} features have been visited.", "success")

    def _clear_visited(self, *args):
        layer = self._layer
        if layer is None:
            return
        reply = QMessageBox.question(
            self, "Clear Visited Marks",
            f"Forget which features of \"{layer.name()}\" you have visited?",
            MB_YES | MB_NO, MB_NO)
        if reply != MB_YES:
            return
        self._visited.clear()
        self._seen_before = False
        self._update_display()

    def _on_track_visited_toggled(self, checked):
        self._save_settings()
        if checked and self._current_fid() is not None:
            self._visited.mark(self._current_fid())
        self._update_display()

    def _update_review(self):
        tracking = self.track_visited_cb.isChecked() and self._layer is not None
        self.review_strip.setVisible(tracking)
        self._review_row.setVisible(tracking)
        if not tracking:
            self.seen_dot.setVisible(False)
            return
        visited = self._visited.ids
        flags = [fid in visited for fid in self.feature_ids]
        current = self.current_index if self.current_index >= 0 else self._anchor_index()
        self.review_strip.set_state(flags, current)
        done = sum(flags)
        self.visited_label.setText(f"Visited {done} / {len(flags)}")
        self.next_unvisited_btn.setEnabled(
            not self._multi_edit_mode and 0 < len(flags) and done < len(flags))
        show_dot = (self._current_fid() is not None and not self._multi_edit_mode
                    and self._passive_count < 2)
        self.seen_dot.setVisible(show_dot)
        if show_dot:
            color = "#2ea043" if self._seen_before else "gray"
            self.seen_dot.setStyleSheet(f"color: {color};")
            self.seen_dot.setToolTip("Seen before" if self._seen_before else "First visit")

    # --- measurements -------------------------------------------------------

    def _update_measurements(self, feat=None):
        layer = self._layer
        fid = self._current_fid()
        text = ""
        if (self.show_measurements_cb.isChecked() and layer is not None
                and fid is not None and not self._multi_edit_mode):
            if feat is None or not feat.isValid() or feat.id() != fid:
                feat = layer.getFeature(fid)
            if feat.isValid() and feat.hasGeometry():
                try:
                    text = describe_geometry(layer, feat.geometry())
                except Exception:
                    text = ""
        self._measure_text = text

    def _on_show_measurements_toggled(self, *args):
        self._save_settings()
        self._update_measurements()
        self._update_content()

    def _layer_tools(self):
        try:
            return self.iface.vectorLayerTools()
        except Exception:
            return None

    def _toggle_editing(self, *args):
        layer = self._layer
        if layer is None:
            self._update_toolbar_state()
            return
        tools = self._layer_tools()
        if not layer.isEditable():
            if tools is not None:
                tools.startEditing(layer)
            else:
                layer.startEditing()
        else:
            self._accept_current_form()
            if tools is not None:
                tools.stopEditing(layer, True)   # asks Save / Discard / Cancel
            elif layer.isModified():
                layer.commitChanges()
            else:
                layer.rollBack()
        self._update_toolbar_state()

    def _save_edits(self):
        layer = self._layer
        if layer is None or not layer.isEditable():
            return
        self._accept_current_form()
        tools = self._layer_tools()
        if tools is not None:
            tools.saveEdits(layer)
        else:
            try:
                layer.commitChanges(False)
            except TypeError:
                layer.commitChanges()
                layer.startEditing()
        layer.triggerRepaint()
        self._update_toolbar_state()

    def _undo(self):
        layer = self._layer
        if layer is None or not layer.isEditable():
            return
        self._accept_current_form()   # so a just-typed value is what gets undone
        if layer.undoStack().canUndo():
            layer.undoStack().undo()
        layer.triggerRepaint()

    def _redo(self):
        layer = self._layer
        if layer is None or not layer.isEditable():
            return
        layer.undoStack().redo()
        layer.triggerRepaint()

    def _on_editing_state_changed(self, *args):
        self._update_toolbar_state()
        layer = self._layer
        if layer is None:
            return
        if not self._lock_navigation and (
                self._multi_edit_mode or layer.selectedFeatureCount() >= 2):
            self._sync_with_selection()
        else:
            self._update_display()

    def _on_before_commit(self, *args):
        # Make sure values still sitting in the form widgets are part of the
        # save (also when the user saves from the main QGIS toolbar).
        self._accept_current_form()

    def _update_toolbar_state(self, *args):
        layer = self._layer
        valid = layer is not None
        editable = valid and layer.isEditable()
        try:
            can_edit = valid and layer.supportsEditing()
        except Exception:
            can_edit = valid
        self.toggle_edit_btn.setEnabled(can_edit)
        self.toggle_edit_btn.blockSignals(True)
        self.toggle_edit_btn.setChecked(editable)
        self.toggle_edit_btn.blockSignals(False)
        self.save_edits_btn.setEnabled(editable)
        can_undo = can_redo = False
        if editable:
            try:
                stack = layer.undoStack()
                can_undo, can_redo = stack.canUndo(), stack.canRedo()
                can_undo = can_undo or self._form_dirty
            except Exception:
                pass
        self.undo_btn.setEnabled(can_undo)
        self.redo_btn.setEnabled(can_redo)

        self.query_builder_btn.setEnabled(valid)
        self.query_builder_btn.blockSignals(True)
        self.query_builder_btn.setChecked(valid and bool(layer.subsetString()))
        self.query_builder_btn.blockSignals(False)
        self.search_btn.setEnabled(valid)

        has_feature = self._current_fid() is not None and not self._multi_edit_mode
        self.flash_btn.setEnabled(has_feature)
        self.del_btn.setEnabled(editable and has_feature)
        self.revert_btn.setEnabled(self._current_unsaved_state() is not None)

    # =========================================================================
    # QUERY BUILDER
    # =========================================================================

    def _toggle_query_builder(self, *args):
        """Checked = the layer has a filter. Click to set it, click again to clear."""
        layer = self._layer
        if layer is None:
            self._update_toolbar_state()
            return
        if layer.isEditable():
            self._notify("Stop editing the layer before changing its filter.", "warning")
            self._update_toolbar_state()
            return
        if layer.subsetString():
            layer.setSubsetString('')
        else:
            try:
                from qgis.gui import QgsQueryBuilder
            except ImportError:
                self._notify("QgsQueryBuilder is not available in this QGIS version.",
                             "warning")
                self._update_toolbar_state()
                return
            dlg = QgsQueryBuilder(layer, self.iface.mainWindow())
            dlg.setSql(layer.subsetString())
            (getattr(dlg, 'exec', None) or dlg.exec_)()
        self._update_toolbar_state()

    # =========================================================================
    # FIND
    # =========================================================================

    def _on_search_toggled(self, checked):
        self._find_bar.setVisible(checked)
        if checked:
            self._find_bar.focus_input()

    def _find_match(self, forward=True):
        layer = self._layer
        if layer is None:
            return
        self._flush_structure_changes()
        if self._multi_edit_mode or not self.feature_ids:
            return
        matches = self._find_bar.matching_ids(layer)
        if matches is None:
            return
        if not matches:
            field_name, text = self._find_bar.query()
            self._notify(f'No feature where {field_name} = "{text}".')
            return
        total = len(self.feature_ids)
        start = self.current_index
        if start < 0:
            start = -1 if forward else 0
        step = 1 if forward else -1
        for offset in range(1, total + 1):
            idx = (start + offset * step) % total
            if self.feature_ids[idx] in matches:
                self._go_to_index(idx)
                return

    # =========================================================================
    # DELETE
    # =========================================================================

    def _delete_current_feature(self):
        layer = self._layer
        fid = self._current_fid()
        if layer is None or fid is None or not layer.isEditable():
            return
        reply = QMessageBox.question(
            self,
            "Delete Feature",
            f"Delete feature {fid} from \"{layer.name()}\"?\n\n"
            "The change goes into the layer's edit buffer — Save Edits to "
            "keep it, or Undo to bring the feature back.",
            MB_YES | MB_NO,
            MB_NO,
        )
        if reply != MB_YES:
            return
        layer.beginEditCommand(f"FeatureNavEd: delete feature {fid}")
        if layer.deleteFeature(fid):
            layer.endEditCommand()
            layer.triggerRepaint()
            # Same path as a Del-key / attribute-table delete: the layer's
            # featuresDeleted signal moves the navigator on.
            self._pending_deleted.add(fid)
            self._flush_structure_changes()
        else:
            layer.destroyEditCommand()
            self._notify(f"Failed to delete feature {fid}.", "warning")

    # =========================================================================
    # NATIVE FEATURE FORM (single feature)
    # =========================================================================

    def _accept_current_form(self):
        """Push values still in the form widgets into the layer's edit buffer.

        Nothing is committed to disk; the layer still needs Save Edits.
        """
        form = self._feature_form
        layer = self._layer
        if form is None or layer is None or self._form_saving:
            return
        if not layer.isEditable():
            return
        try:
            fid = form.feature().id()
        except Exception:
            return
        if fid in self._pending_deleted or not feature_exists(layer, fid):
            return   # never write into a feature that was just deleted
        self._form_saving = True
        try:
            form.save()
        except Exception:
            pass
        finally:
            self._form_saving = False
        self._set_form_dirty(False)

    def _on_form_widget_changed(self, *args):
        if not self._form_saving:
            self._set_form_dirty(True)

    def _set_form_dirty(self, dirty):
        if dirty != self._form_dirty:
            self._form_dirty = dirty
            self._update_toolbar_state()

    def _remove_current_form(self):
        """Drop the cached form widget (does not save)."""
        container = self._feature_form_container or self._feature_form
        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None
        if container is None:
            return
        try:
            self._main_layout.removeWidget(container)
            container.hide()
            container.setParent(None)
            container.deleteLater()
        except Exception:
            pass

    def _show_feature_form(self, layer, feature, save_pending=True):
        """Show ``feature`` in the embedded native attribute form.

        The form widget is reused for every feature of the same layer
        (``setFeature``), which is much faster than rebuilding wide forms.
        """
        self._form_error = None
        if self._feature_form is not None and self._feature_form_layer_id == layer.id():
            if save_pending:
                self._accept_current_form()
            reused = False
            self._form_saving = True
            try:
                self._feature_form.setFeature(feature)
                reused = True
                self._set_form_dirty(False)
            except Exception:
                pass   # fall through to a full rebuild
            finally:
                self._form_saving = False
            if reused:
                if self._hidden_fields:
                    apply_to_attribute_form(self._feature_form, layer, self._hidden_fields)
                return

        if save_pending:
            self._accept_current_form()
        self._remove_current_form()
        try:
            context = QgsAttributeEditorContext()
            tools = self._layer_tools()
            if tools is not None:
                context.setVectorLayerTools(tools)
            form = QgsAttributeForm(layer, feature, context, self)
            try:
                form.widgetValueChanged.connect(self._on_form_widget_changed)
            except Exception:
                pass
            self._set_form_dirty(False)
            mode = form_mode('SingleEditMode')
            if mode is not None:
                try:
                    form.setMode(mode)
                except (TypeError, AttributeError):
                    pass
            if self._field_order:
                apply_field_order(form, layer, self._field_order)
            if self._hidden_fields:
                apply_to_attribute_form(form, layer, self._hidden_fields)
            scroll = make_scroll_area(form, SCROLL_AS_NEEDED)
            self._feature_form = form
            self._feature_form_container = scroll
            self._feature_form_layer_id = layer.id()
            insert_pos = self._main_layout.indexOf(self._multi_panel)
            self._main_layout.insertWidget(max(insert_pos, 0), scroll, 1)
        except Exception as e:
            self._feature_form = None
            self._feature_form_container = None
            self._feature_form_layer_id = None
            self._form_error = f"Cannot display feature form:\n{e}"

    # =========================================================================
    # DISPLAY STATE
    # =========================================================================

    def _update_display(self):
        total = len(self.feature_ids)
        idx = self.current_index
        multi = self._multi_edit_mode
        passive = not multi and self._passive_count >= 2

        self.feature_spin.blockSignals(True)
        try:
            if multi or passive:
                n = len(self._multi_panel.fids) if multi else self._passive_count
                self._show_counter_text(
                    f"M ({n})",
                    "Multi-edit: several features are selected" if multi else
                    f"{n} features selected — start editing to multi-edit them")
            elif total and idx < 0:
                self._show_counter_text("NA", "No feature is active")
            else:
                self._counter_label.setVisible(False)
                self.feature_spin.setVisible(True)
                if total:
                    self.feature_spin.setRange(1, total)
                    self.feature_spin.setValue(idx + 1)
                else:
                    self.feature_spin.setRange(0, 0)
                    self.feature_spin.setValue(0)
            self.feature_total_label.setText(f"/ {total}")
        finally:
            self.feature_spin.blockSignals(False)

        can_nav = total > 0 and not multi
        in_na = idx < 0
        self.first_btn.setEnabled(can_nav and (in_na or idx > 0))
        self.prev_btn.setEnabled(can_nav and (in_na or idx > 0))
        self.next_btn.setEnabled(can_nav and (in_na or idx < total - 1))
        self.last_btn.setEnabled(can_nav and (in_na or idx < total - 1))
        self.feature_spin.setEnabled(can_nav)

        self._update_content()
        self._update_review()
        self._update_sort_value()
        self._update_toolbar_state()

    def _show_counter_text(self, text, tooltip):
        self.feature_spin.setVisible(False)
        self._counter_label.setText(text)
        self._counter_label.setToolTip(tooltip)
        self._counter_label.setVisible(True)

    def _update_content(self):
        """Show exactly one of: multi-edit panel, feature form, placeholder."""
        if self._multi_edit_mode:
            mode = 'multi'
        elif (self._current_fid() is not None and self._feature_form is not None
              and self._form_error is None):
            mode = 'form'
        else:
            mode = 'placeholder'
            self._form_placeholder.setText(self._placeholder_text())
        self._content_mode = mode
        self._multi_panel.setVisible(mode == 'multi')
        if self._feature_form_container is not None:
            self._feature_form_container.setVisible(mode == 'form')
        self._form_placeholder.setVisible(mode == 'placeholder')
        self._measure_label.setText(self._measure_text)
        self._measure_label.setVisible(mode == 'form' and bool(self._measure_text))
        self._update_fields_indicator()

    def _placeholder_text(self):
        if self._form_error:
            return html.escape(self._form_error).replace("\n", "<br>")
        layer = self._layer
        if layer is None:
            return grey("Choose a layer above, or drag one here from the Layers panel.")
        if not self.feature_ids:
            if layer.subsetString():
                return grey("No features match the layer filter.")
            return grey("This layer has no features.")
        anchor = self._anchor_index()
        if anchor >= 0:
            return (grey("No feature selected.") + "<br><br>"
                    f"<a href='resume'>Return to feature {anchor + 1}</a>")
        if self._lock_navigation:
            return grey("No feature selected.<br>Use the arrows or Find to start.")
        return grey("No feature selected.<br>"
                    "Use the arrows, or select a feature on the map.")

    def _update_sort_value(self):
        sort_field = self.sort_field_combo.currentData()
        layer = self._layer
        fid = self._current_fid()
        title = "Sort By"
        if sort_field and layer is not None and fid is not None and not self._multi_edit_mode:
            request = QgsFeatureRequest().setFilterFid(fid)
            request.setSubsetOfAttributes([sort_field], layer.fields())
            request.setFlags(NO_GEOMETRY)
            for feat in layer.getFeatures(request):
                value = feat[sort_field]
                shown = "NULL" if is_null(value) else value
                title = f"Sort By — {sort_field}: {shown}"
        self._sort_group.setTitle(title)

    def _notify(self, text, level="info", duration=4):
        name = {'info': 'Info', 'success': 'Success', 'warning': 'Warning'}[level]
        try:
            self.iface.messageBar().pushMessage(
                "FeatureNavEd", text, message_level(name), duration)
        except Exception:
            pass

    # =========================================================================
    # SETTINGS PERSISTENCE
    # =========================================================================

    def _save_settings(self, *args):
        try:
            s = QgsSettings()
            p = self._SETTINGS_PREFIX
            s.setValue(f"{p}/auto_zoom", bool(self.auto_zoom_cb.isChecked()))
            s.setValue(f"{p}/scale_mode", self._scale_mode())
            s.setValue(f"{p}/enable_flash", bool(self.enable_flash_cb.isChecked()))
            s.setValue(f"{p}/lock_navigation", bool(self.lock_nav_cb.isChecked()))
            s.setValue(f"{p}/track_visited", bool(self.track_visited_cb.isChecked()))
            s.setValue(f"{p}/show_measurements",
                       bool(self.show_measurements_cb.isChecked()))
            s.setValue(f"{p}/scale", int(self.scale_spin.value()))
            s.setValue(f"{p}/sort_ascending", bool(self.sort_ascending))
        except Exception:
            pass

    def _load_settings(self):
        try:
            s = QgsSettings()
        except Exception:
            return

        def _b(key, default):
            v = s.value(f"{self._SETTINGS_PREFIX}/{key}", default)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.lower() in ("1", "true", "yes")
            return bool(v)

        def _i(key, default):
            try:
                return int(s.value(f"{self._SETTINGS_PREFIX}/{key}", default))
            except (TypeError, ValueError):
                return default

        for checkbox, key, default in ((self.auto_zoom_cb, "auto_zoom", True),
                                       (self.enable_flash_cb, "enable_flash", False),
                                       (self.lock_nav_cb, "lock_navigation", False),
                                       (self.track_visited_cb, "track_visited", True),
                                       (self.show_measurements_cb, "show_measurements", True)):
            checkbox.blockSignals(True)
            checkbox.setChecked(_b(key, default))
            checkbox.blockSignals(False)
        self._lock_navigation = self.lock_nav_cb.isChecked()
        self._update_nav_title()

        # 1.0.x stored a Keep/Fixed choice as the "Auto-scale" tick box.
        mode = s.value(f"{self._SETTINGS_PREFIX}/scale_mode", "")
        if mode not in ("keep", "fit", "fixed"):
            mode = "keep" if _b("auto_scale", True) else "fixed"
        self.scale_mode_combo.blockSignals(True)
        self.scale_mode_combo.setCurrentIndex(max(0, self.scale_mode_combo.findData(mode)))
        self.scale_mode_combo.blockSignals(False)

        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(_i("scale", self.scale_spin.value()))
        self.scale_spin.blockSignals(False)
        self._update_scale_controls()

        self.sort_ascending = _b("sort_ascending", True)
        self._update_sort_button()
