# -*- coding: utf-8 -*-
"""
FeatureNavEd - Dock Widget

Dockable panel for navigating and editing vector layer features one by one.
Uses QgsAttributeForm to render the native QGIS attribute form widget
embedded directly in the panel — no OK/Cancel dialog buttons.
"""

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:
    _xml_fromstring = None

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox, QShortcut,
    QGroupBox, QSpinBox, QToolButton, QDialogButtonBox,
    QLineEdit, QCompleter, QScrollArea, QFrame,
    QMenu, QWidgetAction, QMessageBox
)

# Qt5/Qt6 enum compatibility
_AlignCenter = getattr(Qt, 'AlignCenter', None) or Qt.AlignmentFlag.AlignCenter
_AllDockAreas = (
    getattr(Qt, 'LeftDockWidgetArea', None)
    or Qt.DockWidgetArea.LeftDockWidgetArea
) | (
    getattr(Qt, 'RightDockWidgetArea', None)
    or Qt.DockWidgetArea.RightDockWidgetArea
) | (
    getattr(Qt, 'TopDockWidgetArea', None)
    or Qt.DockWidgetArea.TopDockWidgetArea
) | (
    getattr(Qt, 'BottomDockWidgetArea', None)
    or Qt.DockWidgetArea.BottomDockWidgetArea
)

from qgis.core import (
    Qgis, QgsApplication, QgsProject, QgsMapLayerProxyModel,
    QgsVectorLayer, QgsCoordinateTransform, QgsFeature, QgsSettings
)
from qgis.gui import (
    QgsMapLayerComboBox, QgsMapToolIdentifyFeature, QgsExpressionLineEdit,
    QgsAttributeForm, QgsAttributeEditorContext
)

try:
    from qgis.core import NULL
except ImportError:
    NULL = None


class _DropWidget(QWidget):
    """Content widget that accepts layer drag-and-drop from the Layers panel."""

    def __init__(self, dock):
        super().__init__()
        self._dock = dock
        self.setAcceptDrops(True)

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
        layer = self._dock._extract_layer_from_drop(event.mimeData())
        if layer:
            self._dock.layer_combo.setLayer(layer)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class FeatureNavEdDockWidget(QDockWidget):
    """Dock widget for navigating and editing features in a vector layer."""

    def __init__(self, iface, parent=None):
        super().__init__("FeatureNavEd", parent)
        self.iface = iface
        self.feature_ids = []
        self.current_index = -1
        self.sort_ascending = True
        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None  # cache key: only reuse form for same layer
        self._locked_selection_ids = None   # None = not locked
        self._pick_tool = None
        self._prev_map_tool = None
        self._current_layer_id = None
        self._layer_positions = {}
        self._history = []          # list of (layer_id, fid)
        self._navigating_back = False
        self._multi_edit_mode = False        # True when ≥2 features selected + layer editable
        self._passive_multi_indicator = False # True when ≥2 selected but layer NOT editable
        self._multi_edit_rows = []            # [(field_idx, QgsField, QCheckBox, QLineEdit)]

        self.setAllowedAreas(_AllDockAreas)
        self.setObjectName("FeatureNavEdDockWidget")
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._load_settings()

        # React when a layer is about to be removed from the project, so we can
        # clean up the embedded feature form BEFORE its underlying C++ layer is
        # destroyed (otherwise the widget can end up in a broken state).
        try:
            QgsProject.instance().layerWillBeRemoved.connect(
                self._on_layer_will_be_removed
            )
        except Exception:
            pass

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
    # UI
    # =========================================================================

    def _build_ui(self):
        main_widget = _DropWidget(self)
        self._main_layout = QVBoxLayout(main_widget)
        self._main_layout.setContentsMargins(4, 4, 4, 4)
        self._main_layout.setSpacing(4)

        # --- Top Toolbar ---
        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(2)

        self.pick_btn = QToolButton()
        self.pick_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionIdentify.svg')
        )
        self.pick_btn.setToolTip("Pick feature from map")
        self.pick_btn.setAutoRaise(True)
        self.pick_btn.setCheckable(True)
        toolbar_row.addWidget(self.pick_btn)

        self.filter_btn = QToolButton()
        self.filter_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionFilter2.svg')
        )
        self.filter_btn.setToolTip("Toggle filter bar")
        self.filter_btn.setAutoRaise(True)
        self.filter_btn.setCheckable(True)
        toolbar_row.addWidget(self.filter_btn)

        self.search_btn = QToolButton()
        self.search_btn.setIcon(
            QgsApplication.getThemeIcon('/search.svg')
        )
        self.search_btn.setToolTip("Find feature by expression (no filtering)")
        self.search_btn.setAutoRaise(True)
        self.search_btn.setCheckable(True)
        toolbar_row.addWidget(self.search_btn)

        self.attr_table_btn = QToolButton()
        self.attr_table_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionOpenTable.svg')
        )
        self.attr_table_btn.setToolTip("Open attribute table")
        self.attr_table_btn.setAutoRaise(True)
        toolbar_row.addWidget(self.attr_table_btn)

        self.back_btn = QToolButton()
        self.back_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionUndo.svg')
        )
        self.back_btn.setToolTip("Go back to last viewed feature")
        self.back_btn.setAutoRaise(True)
        self.back_btn.setEnabled(False)
        toolbar_row.addWidget(self.back_btn)

        # --- Advanced Options dropdown ---
        self.advanced_btn = QToolButton()
        self.advanced_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionOptions.svg')
        )
        self.advanced_btn.setToolTip("Advanced options")
        self.advanced_btn.setAutoRaise(True)
        try:
            self.advanced_btn.setPopupMode(QToolButton.InstantPopup)
        except (AttributeError, TypeError):
            self.advanced_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        adv_menu = QMenu(self.advanced_btn)
        adv_container = QWidget(adv_menu)
        adv_layout = QVBoxLayout(adv_container)
        adv_layout.setContentsMargins(8, 6, 8, 6)
        adv_layout.setSpacing(4)
        self.enable_flash_cb = QCheckBox("Flash on navigation")
        self.enable_flash_cb.setChecked(False)
        self.enable_flash_cb.setToolTip(
            "Briefly flash the feature on the map when the form navigates to it.\n"
            "Disable this to avoid the flash effect when clicking features for editing.\n"
            "The Flash toolbar button still works on-demand."
        )
        adv_layout.addWidget(self.enable_flash_cb)
        adv_action = QWidgetAction(adv_menu)
        adv_action.setDefaultWidget(adv_container)
        adv_menu.addAction(adv_action)
        self.advanced_btn.setMenu(adv_menu)
        toolbar_row.addWidget(self.advanced_btn)

        toolbar_row.addStretch()
        self._main_layout.addLayout(toolbar_row)

        # --- Search Bar (collapsible) ---
        self._search_bar = QWidget()
        search_bar_layout = QHBoxLayout(self._search_bar)
        search_bar_layout.setContentsMargins(0, 0, 0, 0)
        search_bar_layout.setSpacing(2)

        self.search_field_combo = QComboBox()
        self.search_field_combo.setToolTip("Field to search")
        search_bar_layout.addWidget(self.search_field_combo)

        self.search_value_edit = QLineEdit()
        self.search_value_edit.setPlaceholderText("Value...")
        self.search_value_edit.setClearButtonEnabled(True)
        self.search_value_edit.setToolTip("Value to find (exact match)")
        self._search_completer = QCompleter([], self.search_value_edit)
        self._search_completer.setCaseSensitivity(
            getattr(Qt, 'CaseInsensitive', None) or Qt.CaseSensitivity.CaseInsensitive
        )
        self._search_completer.setFilterMode(
            getattr(Qt, 'MatchContains', None) or Qt.MatchFlag.MatchContains
        )
        self.search_value_edit.setCompleter(self._search_completer)
        search_bar_layout.addWidget(self.search_value_edit, 1)

        self.search_go_btn = QToolButton()
        self.search_go_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionZoomToSelected.svg')
        )
        self.search_go_btn.setToolTip("Go to match (Enter)")
        self.search_go_btn.setAutoRaise(True)
        search_bar_layout.addWidget(self.search_go_btn)

        self.search_prev_btn = QToolButton()
        self.search_prev_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowUp.svg')
        )
        self.search_prev_btn.setToolTip("Previous match")
        self.search_prev_btn.setAutoRaise(True)
        search_bar_layout.addWidget(self.search_prev_btn)

        self.search_next_btn = QToolButton()
        self.search_next_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowDown.svg')
        )
        self.search_next_btn.setToolTip("Next match")
        self.search_next_btn.setAutoRaise(True)
        search_bar_layout.addWidget(self.search_next_btn)

        self._search_bar.setVisible(False)
        self._main_layout.addWidget(self._search_bar)

        # --- Filter Bar (collapsible) ---
        self._filter_bar = QWidget()
        filter_bar_layout = QHBoxLayout(self._filter_bar)
        filter_bar_layout.setContentsMargins(0, 0, 0, 0)
        filter_bar_layout.setSpacing(4)

        self.filter_expression = QgsExpressionLineEdit()
        self.filter_expression.setExpressionDialogTitle("Filter Expression")
        self.filter_expression.setToolTip("Expression to filter features")
        filter_bar_layout.addWidget(self.filter_expression, 1)

        self.selected_only_cb = QCheckBox("Selected only")
        self.selected_only_cb.setToolTip("Navigate only through currently selected features")
        filter_bar_layout.addWidget(self.selected_only_cb)

        self.lock_selection_btn = QToolButton()
        self.lock_selection_btn.setIcon(
            QgsApplication.getThemeIcon('/lockedGray.svg')
        )
        self.lock_selection_btn.setToolTip(
            "Lock the current selection as a fixed navigation set "
            "(stops auto-refreshing when the map selection changes)"
        )
        self.lock_selection_btn.setAutoRaise(True)
        self.lock_selection_btn.setCheckable(True)
        filter_bar_layout.addWidget(self.lock_selection_btn)

        self._filter_bar.setVisible(False)
        self._main_layout.addWidget(self._filter_bar)

        # --- Layer ---
        _BOLD_GROUP_STYLE = (
            "QGroupBox { font-weight: bold; }"
            " QGroupBox * { font-weight: normal; }"
        )
        layer_group = QGroupBox("Layer")
        layer_group.setStyleSheet(_BOLD_GROUP_STYLE)
        layer_layout = QHBoxLayout()
        layer_layout.setContentsMargins(4, 4, 4, 4)
        layer_layout.setSpacing(4)

        self.layer_combo = QgsMapLayerComboBox()
        try:
            self.layer_combo.setFilters(Qgis.LayerFilter.VectorLayer)
        except AttributeError:
            self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setCurrentIndex(0)  # start with empty (no layer)
        self.layer_combo.setShowCrs(True)
        self.layer_combo.setMinimumContentsLength(10)
        try:
            self.layer_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        except AttributeError:
            self.layer_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        layer_layout.addWidget(self.layer_combo)

        self.active_layer_btn = QToolButton()
        self.active_layer_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionRefresh.svg')
        )
        self.active_layer_btn.setToolTip("Use active layer / reload")
        self.active_layer_btn.setAutoRaise(True)
        layer_layout.addWidget(self.active_layer_btn)

        layer_group.setLayout(layer_layout)
        self._main_layout.addWidget(layer_group)

        # --- Sort ---
        self._sort_group = QGroupBox("Sort By")
        self._sort_group.setStyleSheet(_BOLD_GROUP_STYLE)
        sort_layout = QHBoxLayout()
        sort_layout.setContentsMargins(4, 4, 4, 4)
        sort_layout.setSpacing(4)

        self.sort_field_combo = QComboBox()
        self.sort_field_combo.setToolTip("Field to sort features by")
        sort_layout.addWidget(self.sort_field_combo, 1)

        self.sort_order_btn = QToolButton()
        self.sort_order_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowUp.svg')
        )
        self.sort_order_btn.setToolTip("Ascending — click to toggle")
        self.sort_order_btn.setAutoRaise(True)
        self.sort_order_btn.setIconSize(self.sort_order_btn.iconSize() * 1.2)
        sort_layout.addWidget(self.sort_order_btn)

        self._sort_group.setLayout(sort_layout)
        self._main_layout.addWidget(self._sort_group)

        # --- Navigate ---
        nav_group = QGroupBox("Navigate")
        nav_group.setStyleSheet(_BOLD_GROUP_STYLE)
        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(4, 4, 4, 4)
        nav_layout.setSpacing(4)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(2)

        self.first_btn = QToolButton()
        self.first_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionDoubleArrowLeft.svg')
        )
        self.first_btn.setToolTip("First feature (Alt+Home)")
        self.first_btn.setAutoRaise(True)
        nav_row.addWidget(self.first_btn)

        self.prev_btn = QToolButton()
        self.prev_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowLeft.svg')
        )
        self.prev_btn.setToolTip("Previous feature (Alt+Left)")
        self.prev_btn.setAutoRaise(True)
        nav_row.addWidget(self.prev_btn)

        nav_row.addStretch()
        self.feature_spin = QSpinBox()
        self.feature_spin.setRange(0, 0)
        self.feature_spin.setAlignment(_AlignCenter)
        self.feature_spin.setToolTip("Type a feature number to jump to it")
        self.feature_spin.setKeyboardTracking(False)
        nav_row.addWidget(self.feature_spin)
        self._multi_label = QLabel("M")
        self._multi_label.setAlignment(_AlignCenter)
        self._multi_label.setToolTip("Multi-edit active — multiple features selected")
        self._multi_label.setVisible(False)
        self._multi_label.setMinimumWidth(self.feature_spin.sizeHint().width())
        nav_row.addWidget(self._multi_label)
        self.feature_total_label = QLabel("/ 0")
        nav_row.addWidget(self.feature_total_label)
        nav_row.addStretch()

        self.next_btn = QToolButton()
        self.next_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowRight.svg')
        )
        self.next_btn.setToolTip("Next feature (Alt+Right)")
        self.next_btn.setAutoRaise(True)
        nav_row.addWidget(self.next_btn)

        self.last_btn = QToolButton()
        self.last_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionDoubleArrowRight.svg')
        )
        self.last_btn.setToolTip("Last feature (Alt+End)")
        self.last_btn.setAutoRaise(True)
        nav_row.addWidget(self.last_btn)

        self.flash_btn = QToolButton()
        self.flash_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionHighlightFeature.svg')
        )
        self.flash_btn.setToolTip("Flash feature again")
        self.flash_btn.setAutoRaise(True)
        nav_row.addWidget(self.flash_btn)

        self.del_btn = QToolButton()
        self.del_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionDeleteSelected.svg')
        )
        self.del_btn.setToolTip("Delete current feature (layer must be editable)")
        self.del_btn.setAutoRaise(True)
        self.del_btn.setEnabled(False)
        nav_row.addWidget(self.del_btn)

        nav_layout.addLayout(nav_row)

        # Options
        options_row = QHBoxLayout()
        self.auto_zoom_cb = QCheckBox("Auto-zoom")
        self.auto_zoom_cb.setChecked(True)
        self.auto_zoom_cb.setToolTip("Centre on feature when navigating")
        options_row.addWidget(self.auto_zoom_cb)

        self.auto_scale_cb = QCheckBox("Auto-scale")
        self.auto_scale_cb.setChecked(True)
        self.auto_scale_cb.setToolTip("Use current map canvas scale (uncheck to set a custom scale)")
        options_row.addWidget(self.auto_scale_cb)

        options_row.addStretch()
        self._scale_label = QLabel("Scale:")
        options_row.addWidget(self._scale_label)

        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(100, 1000000)
        self.scale_spin.setValue(1000)
        self.scale_spin.setSingleStep(500)
        self.scale_spin.setPrefix("1:")
        self.scale_spin.setToolTip("Map scale when zooming to features")
        options_row.addWidget(self.scale_spin)
        self._update_scale_controls(self.auto_scale_cb.isChecked())

        nav_layout.addLayout(options_row)
        nav_group.setLayout(nav_layout)
        self._main_layout.addWidget(nav_group)

        # --- Feature Form placeholder ---
        # The native QGIS feature form will be inserted here (stretch=1)
        self._form_placeholder = QLabel("No feature selected")
        self._form_placeholder.setEnabled(False)
        self._form_placeholder.setAlignment(_AlignCenter)
        self._main_layout.addWidget(self._form_placeholder, 1)

        # --- Multi-edit OK / Cancel (hidden until ≥2 features selected) ---
        try:
            _ok_cancel = QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        except AttributeError:
            _ok_cancel = (
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
        self._multi_edit_buttons = QDialogButtonBox(_ok_cancel)
        self._multi_edit_buttons.button(
            self._multi_edit_buttons.Ok if hasattr(self._multi_edit_buttons, 'Ok')
            else QDialogButtonBox.StandardButton.Ok
        ).setText("Apply to selected")
        self._multi_edit_buttons.setVisible(False)
        self._main_layout.addWidget(self._multi_edit_buttons)

        # --- Keyboard shortcuts ---
        _WidgetShortcut = (
            getattr(Qt, 'WidgetWithChildrenShortcut', None)
            or Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._shortcut_prev = QShortcut(QKeySequence("Alt+Left"), main_widget)
        self._shortcut_prev.setContext(_WidgetShortcut)
        self._shortcut_next = QShortcut(QKeySequence("Alt+Right"), main_widget)
        self._shortcut_next.setContext(_WidgetShortcut)
        self._shortcut_first = QShortcut(QKeySequence("Alt+Home"), main_widget)
        self._shortcut_first.setContext(_WidgetShortcut)
        self._shortcut_last = QShortcut(QKeySequence("Alt+End"), main_widget)
        self._shortcut_last.setContext(_WidgetShortcut)
        # Additional aliases / shortcuts
        self._shortcut_prev_alt = QShortcut(QKeySequence("Alt+P"), main_widget)
        self._shortcut_prev_alt.setContext(_WidgetShortcut)
        self._shortcut_next_alt = QShortcut(QKeySequence("Alt+N"), main_widget)
        self._shortcut_next_alt.setContext(_WidgetShortcut)

        self.setWidget(main_widget)

    # =========================================================================
    # SIGNALS
    # =========================================================================

    def _connect_signals(self):
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self.active_layer_btn.clicked.connect(self._use_active_layer)
        self.sort_field_combo.currentIndexChanged.connect(self._reload_features)
        self.sort_order_btn.clicked.connect(self._toggle_sort_order)
        self.filter_btn.toggled.connect(self._filter_bar.setVisible)
        self.search_btn.toggled.connect(self._search_bar.setVisible)
        self.search_go_btn.clicked.connect(self._go_next_match)
        self.search_next_btn.clicked.connect(self._go_next_match)
        self.search_prev_btn.clicked.connect(self._go_prev_match)
        self.search_value_edit.returnPressed.connect(self._go_next_match)
        self.search_field_combo.currentIndexChanged.connect(self._populate_search_values)
        self.attr_table_btn.clicked.connect(self._open_attribute_table)
        self.back_btn.clicked.connect(self._go_back)
        self.filter_expression.expressionChanged.connect(self._on_filter_changed)
        self.selected_only_cb.toggled.connect(lambda *_: self._reload_features())
        self.first_btn.clicked.connect(self._go_first)
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)
        self.last_btn.clicked.connect(self._go_last)
        self.feature_spin.valueChanged.connect(self._go_to_feature_number)
        self.pick_btn.toggled.connect(self._toggle_pick_mode)
        self.flash_btn.clicked.connect(self._flash_current)
        self.auto_scale_cb.toggled.connect(self._update_scale_controls)
        self.iface.mapCanvas().scaleChanged.connect(self._on_canvas_scale_changed)
        self._shortcut_prev.activated.connect(self._go_prev)
        self._shortcut_next.activated.connect(self._go_next)
        self._shortcut_first.activated.connect(self._go_first)
        self._shortcut_last.activated.connect(self._go_last)
        self._shortcut_prev_alt.activated.connect(self._go_prev)
        self._shortcut_next_alt.activated.connect(self._go_next)
        # New: lock selection toggle, duplicate / delete
        self.lock_selection_btn.toggled.connect(self._on_lock_selection_toggled)
        self.del_btn.clicked.connect(self._delete_current_feature)
        # Persist settings whenever the user changes a relevant option
        self.auto_zoom_cb.toggled.connect(lambda *_: self._save_settings())
        self.auto_scale_cb.toggled.connect(lambda *_: self._save_settings())
        self.enable_flash_cb.toggled.connect(lambda *_: self._save_settings())
        self.scale_spin.valueChanged.connect(lambda *_: self._save_settings())
        # Multi-edit OK / Cancel
        self._multi_edit_buttons.accepted.connect(self._on_multi_edit_ok)
        self._multi_edit_buttons.rejected.connect(self._on_multi_edit_cancel)

    # =========================================================================
    # LAYER HANDLING
    # =========================================================================

    def _use_active_layer(self):
        active = self.iface.activeLayer()
        current = self.layer_combo.currentLayer()
        if isinstance(active, QgsVectorLayer) and active != current:
            self.layer_combo.setLayer(active)
        else:
            self._reload_features()

    def _update_scale_controls(self, checked):
        """Enable/disable the scale spinbox based on auto-scale checkbox."""
        self._scale_label.setEnabled(not checked)
        self.scale_spin.setEnabled(not checked)
        if checked:
            self._sync_scale_from_canvas()

    def _on_canvas_scale_changed(self, scale):
        """Update spinbox when canvas scale changes and auto-scale is on."""
        if self.auto_scale_cb.isChecked():
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(round(scale))
            self.scale_spin.blockSignals(False)

    def _sync_scale_from_canvas(self):
        """Sync spinbox value to current canvas scale."""
        scale = round(self.iface.mapCanvas().scale())
        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(scale)
        self.scale_spin.blockSignals(False)

    def _on_layer_will_be_removed(self, layer_id):
        """Clean up state when a layer is about to be removed from the project.

        This runs BEFORE the underlying C++ layer is destroyed, so the embedded
        feature form (which holds a pointer to that layer) can be torn down
        cleanly. Without this, the form widget can stay in the layout while
        pointing at a dangling layer and the dock ends up in a broken state.
        """
        # Drop history and saved positions for the removed layer.
        self._history = [h for h in self._history if h[0] != layer_id]
        self._layer_positions.pop(layer_id, None)
        self.back_btn.setEnabled(bool(self._history))

        if layer_id != self._current_layer_id:
            return

        # Tear down the form NOW while the layer is still alive.
        if getattr(self, '_multi_edit_mode', False):
            # Discard multi-edit form without saving — no user confirmation possible here.
            self._multi_edit_mode = False
            self._multi_label.setVisible(False)
            self.feature_spin.setVisible(True)
            self._multi_edit_buttons.setVisible(False)
            self._feature_form = None  # prevent _remove_current_form from auto-saving
        self._remove_current_form()
        self._current_layer_id = None
        self.feature_ids = []
        self.current_index = -1
        self._form_placeholder.setText("No feature selected")
        self._form_placeholder.setVisible(True)
        self._update_display()

    def _on_layer_changed(self, layer):
        # Discard any active multi-edit form without saving when switching layers.
        if getattr(self, '_multi_edit_mode', False):
            self._multi_edit_mode = False
            self._multi_label.setVisible(False)
            self.feature_spin.setVisible(True)
            self._multi_edit_buttons.setVisible(False)
            self._feature_form = None  # prevent _remove_current_form from auto-saving
        # Save position for the previous layer
        if self._current_layer_id and self.feature_ids and self.current_index >= 0:
            fid = self.feature_ids[self.current_index]
            self._layer_positions[self._current_layer_id] = (self.current_index, fid)

        # Disconnect selection signal from old layer
        old_layer = (
            QgsProject.instance().mapLayer(self._current_layer_id)
            if self._current_layer_id else None
        )
        if isinstance(old_layer, QgsVectorLayer):
            try:
                old_layer.selectionChanged.disconnect(self._on_selection_changed)
            except Exception:
                pass
            try:
                old_layer.editingStarted.disconnect(self._update_edit_buttons)
            except Exception:
                pass
            try:
                old_layer.editingStopped.disconnect(self._update_edit_buttons)
            except Exception:
                pass

        # Layer changed — drop any cached form for the previous layer and
        # clear any locked selection (it belongs to that layer).
        self._remove_current_form()
        self._locked_selection_ids = None
        self.lock_selection_btn.blockSignals(True)
        self.lock_selection_btn.setChecked(False)
        self.lock_selection_btn.blockSignals(False)

        self._current_layer_id = layer.id() if isinstance(layer, QgsVectorLayer) else None

        self.sort_field_combo.blockSignals(True)
        self.sort_field_combo.clear()
        self.feature_ids = []
        self.current_index = -1

        if isinstance(layer, QgsVectorLayer):
            self.sort_field_combo.addItem("(Feature ID)", None)
            tooltip_role = getattr(Qt, 'ToolTipRole', None)
            if tooltip_role is None:
                tooltip_role = Qt.ItemDataRole.ToolTipRole
            fields = layer.fields()
            for i, field in enumerate(fields):
                type_name = field.typeName() or ""
                # Use QGIS's native field-type icon (same as attribute table /
                # native sort combos) so the dropdown matches QGIS look & feel.
                icon = None
                try:
                    icon = fields.iconForField(i)
                except Exception:
                    icon = None
                if icon is not None and not icon.isNull():
                    self.sort_field_combo.addItem(icon, field.name(), field.name())
                else:
                    self.sort_field_combo.addItem(field.name(), field.name())
                try:
                    idx = self.sort_field_combo.count() - 1
                    self.sort_field_combo.setItemData(
                        idx,
                        f"{field.name()} ({type_name})" if type_name else field.name(),
                        tooltip_role,
                    )
                except Exception:
                    pass
            self.filter_expression.setLayer(layer)
            self.filter_expression.setExpression('')
            self.search_field_combo.clear()
            for field in layer.fields():
                self.search_field_combo.addItem(field.name(), field.name())
            self.search_value_edit.clear()
            self._populate_search_values()
            layer.selectionChanged.connect(self._on_selection_changed)
            # Wire editing-state to enable/disable Duplicate/Delete buttons.
            try:
                layer.editingStarted.connect(self._update_edit_buttons)
            except Exception:
                pass
            try:
                layer.editingStopped.connect(self._update_edit_buttons)
            except Exception:
                pass
        else:
            self.filter_expression.setLayer(None)
            self.search_field_combo.clear()
            self.search_value_edit.clear()
            self._search_completer.model().setStringList([])

        self.sort_field_combo.blockSignals(False)
        self._reload_features()

        # Restore saved position for this layer
        if self._current_layer_id and self._current_layer_id in self._layer_positions:
            saved_index, saved_fid = self._layer_positions[self._current_layer_id]
            if saved_fid in self.feature_ids:
                self.current_index = self.feature_ids.index(saved_fid)
            elif 0 <= saved_index < len(self.feature_ids):
                self.current_index = saved_index
            if self.current_index >= 0:
                self._navigate_to_current()

    # =========================================================================
    # SORTING AND LOADING
    # =========================================================================

    def _reload_features(self):
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            self.feature_ids = []
            self.current_index = -1
            self._update_display()
            return

        sort_field = self.sort_field_combo.currentData()
        filter_expr = self.filter_expression.expression()

        from qgis.core import QgsFeatureRequest, QgsExpression
        request = QgsFeatureRequest()

        # Apply expression filter if valid
        if filter_expr and not QgsExpression(filter_expr).hasParserError():
            request.setFilterExpression(filter_expr)
        elif sort_field:
            request.setSubsetOfAttributes([sort_field], layer.fields())
        else:
            request.setNoAttributes()

        try:
            request.setFlags(Qgis.FeatureRequestFlag.NoGeometry)
        except AttributeError:
            request.setFlags(QgsFeatureRequest.NoGeometry)

        entries = []
        for feat in layer.getFeatures(request):
            if sort_field:
                val = feat[sort_field]
                sort_key = None if (val is None or val == NULL) else val
            else:
                sort_key = feat.id()
            entries.append((feat.id(), sort_key))

        try:
            entries.sort(
                key=lambda x: (x[1] is None, x[1]),
                reverse=not self.sort_ascending
            )
        except TypeError:
            entries.sort(
                key=lambda x: (
                    x[1] is None,
                    str(x[1]) if x[1] is not None else ''
                ),
                reverse=not self.sort_ascending
            )

        self.feature_ids = [e[0] for e in entries]

        # Filter to selected features only
        if self.selected_only_cb.isChecked():
            if self._locked_selection_ids is not None:
                selected = self._locked_selection_ids
            else:
                selected = set(layer.selectedFeatureIds())
            self.feature_ids = [fid for fid in self.feature_ids if fid in selected]

        if self.feature_ids:
            # Start at NA state when the layer has nothing currently selected
            # (e.g. on initial layer load).  Once the user selects a feature or
            # presses a nav button the NA label is replaced by the live position.
            _layer_now = self.layer_combo.currentLayer()
            _n_sel = (
                len(_layer_now.selectedFeatureIds())
                if isinstance(_layer_now, QgsVectorLayer) else 0
            )
            self.current_index = 0 if _n_sel > 0 else -1
        else:
            self.current_index = -1
        if not self._navigating_back:
            self._navigate_to_current()

    def _toggle_sort_order(self):
        self.sort_ascending = not self.sort_ascending
        if self.sort_ascending:
            self.sort_order_btn.setIcon(
                QgsApplication.getThemeIcon('/mActionArrowUp.svg')
            )
            self.sort_order_btn.setToolTip("Ascending — click to toggle")
        else:
            self.sort_order_btn.setIcon(
                QgsApplication.getThemeIcon('/mActionArrowDown.svg')
            )
            self.sort_order_btn.setToolTip("Descending — click to toggle")
        self._save_settings()
        self._reload_features()

    # =========================================================================
    # NAVIGATION
    # =========================================================================

    def _go_first(self):
        if self._multi_edit_mode:
            return
        if self.feature_ids:
            self._push_history()
            self._accept_current_form()
            self.current_index = 0
            self._navigate_to_current()

    def _go_prev(self):
        if self._multi_edit_mode:
            return
        if not self.feature_ids:
            return
        self._push_history()
        self._accept_current_form()
        if self.current_index <= 0:
            # From NA state or already at first feature → wrap to last.
            self.current_index = len(self.feature_ids) - 1
        else:
            self.current_index -= 1
        self._navigate_to_current()

    def _go_next(self):
        if self._multi_edit_mode:
            return
        if self.feature_ids and self.current_index < len(self.feature_ids) - 1:
            self._push_history()
            self._accept_current_form()
            self.current_index += 1
            self._navigate_to_current()

    def _go_last(self):
        if self._multi_edit_mode:
            return
        if self.feature_ids:
            self._push_history()
            self._accept_current_form()
            self.current_index = len(self.feature_ids) - 1
            self._navigate_to_current()

    def _go_to_feature_number(self, number):
        """Jump to a 1-based feature number typed in the spinbox."""
        if self._multi_edit_mode:
            return
        idx = number - 1
        if self.feature_ids and 0 <= idx < len(self.feature_ids) and idx != self.current_index:
            self._push_history()
            self._accept_current_form()
            self.current_index = idx
            self._navigate_to_current()

    def _on_filter_changed(self, *args):
        """Reload features when the filter expression changes."""
        expr = self.filter_expression.expression()
        if not expr:
            self._reload_features()
            return
        from qgis.core import QgsExpression
        if not QgsExpression(expr).hasParserError():
            self._reload_features()

    def _on_selection_changed(self):
        """React to layer selection changes.

        * 2+ features selected  → enter multi-edit mode (OK/Cancel form).
        * 0-1 features selected → exit multi-edit if active, then handle
          'Selected only' reload and 'Go to selected' single-feature jump.
        Guards against re-entrancy because ``_navigate_to_current()`` itself
        calls ``layer.selectByIds()``, which fires ``selectionChanged``.
        """
        if getattr(self, '_suppress_selection_jump', False):
            return

        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            return

        selected_ids = layer.selectedFeatureIds()

        # ── Multi-select: enter or refresh multi-edit mode ──────────────────
        if len(selected_ids) >= 2:
            if layer.isEditable():
                # Full multi-edit: interactive form + OK/Cancel bar.
                self._passive_multi_indicator = False
                self._enter_multi_edit_mode(layer, selected_ids)
            else:
                # Passive indicator: show M(n) in the counter without
                # entering edit mode or touching the rest of the panel.
                self._passive_multi_indicator = True
                self.feature_spin.setVisible(False)
                self._multi_label.setText(f"M ({len(selected_ids)})")
                self._multi_label.setVisible(True)
            return

        # ── Single / no selection: clear any active multi state ─────────────
        _had_passive = self._passive_multi_indicator
        self._passive_multi_indicator = False

        if self._multi_edit_mode:
            self._exit_multi_edit_mode()
        elif _had_passive:
            # Restore the normal counter (spinbox + total label).
            self._update_display()

        if self.selected_only_cb.isChecked() and self._locked_selection_ids is None:
            self._reload_features()
            return

        if not selected_ids:
            # 0 features selected → NA state (blank form, nav active).
            self.current_index = -1
            self._update_display()
            return

        # Use the first selected fid. Skip if it's already the current feature.
        target_fid = selected_ids[0]
        if (0 <= self.current_index < len(self.feature_ids)
                and self.feature_ids[self.current_index] == target_fid):
            return
        if target_fid not in self.feature_ids:
            return
        self._push_history()
        self._accept_current_form()
        self.current_index = self.feature_ids.index(target_fid)
        self._suppress_selection_jump = True
        try:
            self._navigate_to_current()
        finally:
            self._suppress_selection_jump = False

    def _push_history(self):
        """Push the current feature position onto the navigation history stack."""
        if self.current_index < 0 or not self.feature_ids or not self._current_layer_id:
            return
        fid = self.feature_ids[self.current_index]
        entry = (self._current_layer_id, fid)
        if self._history and self._history[-1] == entry:
            return
        self._history.append(entry)
        self.back_btn.setEnabled(True)

    def _go_back(self):
        """Navigate back to the previously viewed feature."""
        if not self._history:
            return

        layer_id, fid = self._history.pop()
        self.back_btn.setEnabled(bool(self._history))

        target_layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(target_layer, QgsVectorLayer):
            # Layer no longer exists — keep popping
            if self._history:
                self._go_back()
            return

        self._navigating_back = True
        if self.layer_combo.currentLayer() != target_layer:
            self.layer_combo.setLayer(target_layer)
            # _on_layer_changed → _reload_features populates feature_ids
            # but skips auto-navigate because _navigating_back is True

        if fid in self.feature_ids:
            self.current_index = self.feature_ids.index(fid)
            self._navigate_to_current()
        self._navigating_back = False

    # =========================================================================
    # FIND / GO TO
    # =========================================================================

    def _populate_search_values(self):
        """Populate the completer with unique values from the selected search field."""
        field_name = self.search_field_combo.currentData()
        layer = self.layer_combo.currentLayer()
        if not field_name or not isinstance(layer, QgsVectorLayer):
            self._search_completer.model().setStringList([])
            return
        idx = layer.fields().indexOf(field_name)
        if idx < 0:
            self._search_completer.model().setStringList([])
            return
        values = layer.uniqueValues(idx)
        strings = sorted(
            str(v) for v in values if v is not None and v != NULL
        )
        self._search_completer.model().setStringList(strings)

    def _go_next_match(self):
        """Jump to the next feature matching the search expression."""
        self._find_match(forward=True)

    def _go_prev_match(self):
        """Jump to the previous feature matching the search expression."""
        self._find_match(forward=False)

    def _find_match(self, forward=True):
        """Find and navigate to the next/previous feature matching the search field + value."""
        search_field = self.search_field_combo.currentData()
        search_value = self.search_value_edit.text().strip()
        if not search_field or not search_value:
            return
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer) or not self.feature_ids:
            return

        total = len(self.feature_ids)
        start = self.current_index
        step = 1 if forward else -1

        for offset in range(1, total + 1):
            idx = (start + offset * step) % total
            fid = self.feature_ids[idx]
            feat = layer.getFeature(fid)
            if not feat.isValid():
                continue
            val = feat[search_field]
            if val is None or val == NULL:
                continue
            if str(val) == search_value:
                self._push_history()
                self._accept_current_form()
                self.current_index = idx
                self._navigate_to_current()
                return

    # =========================================================================
    # PICK FROM MAP
    # =========================================================================

    def _toggle_pick_mode(self, active):
        """Activate or deactivate the map pick tool."""
        canvas = self.iface.mapCanvas()
        if active:
            layer = self.layer_combo.currentLayer()
            if not isinstance(layer, QgsVectorLayer):
                self.pick_btn.setChecked(False)
                return
            self._prev_map_tool = canvas.mapTool()
            self._pick_tool = QgsMapToolIdentifyFeature(canvas, layer)
            self._pick_tool.setCursor(QgsApplication.getThemeCursor(QgsApplication.Cursor.Identify))
            self._pick_tool.featureIdentified.connect(self._on_feature_picked)
            canvas.setMapTool(self._pick_tool)
            canvas.mapToolSet.connect(self._on_map_tool_changed)
        else:
            self._deactivate_pick_tool()

    def _on_feature_picked(self, feature):
        """Handle a feature clicked on the map."""
        fid = feature.id()
        if fid in self.feature_ids:
            self._push_history()
            self._accept_current_form()
            self.current_index = self.feature_ids.index(fid)
            self._navigate_to_current()

    def _on_map_tool_changed(self, new_tool):
        """Uncheck pick button when user switches to another map tool."""
        if new_tool is not self._pick_tool:
            try:
                self.iface.mapCanvas().mapToolSet.disconnect(self._on_map_tool_changed)
            except Exception:
                pass
            self.pick_btn.blockSignals(True)
            self.pick_btn.setChecked(False)
            self.pick_btn.blockSignals(False)

    def _deactivate_pick_tool(self):
        """Restore previous map tool and clean up."""
        canvas = self.iface.mapCanvas()
        try:
            canvas.mapToolSet.disconnect(self._on_map_tool_changed)
        except Exception:
            pass
        if self._pick_tool is not None:
            try:
                self._pick_tool.featureIdentified.disconnect(self._on_feature_picked)
            except Exception:
                pass
            if canvas.mapTool() is self._pick_tool:
                if self._prev_map_tool:
                    canvas.setMapTool(self._prev_map_tool)
                else:
                    canvas.unsetMapTool(self._pick_tool)
            self._pick_tool = None
        self._prev_map_tool = None

    def _flash_current(self):
        """Re-flash the current feature and optionally re-zoom to it."""
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer) or self.current_index < 0:
            return
        fid = self.feature_ids[self.current_index]
        feat = layer.getFeature(fid)
        if not feat.isValid() or not feat.hasGeometry():
            return
        if self.auto_zoom_cb.isChecked():
            self._zoom_to_feature(feat, layer)
        try:
            self.iface.mapCanvas().flashGeometries(
                [feat.geometry()], layer.crs()
            )
        except AttributeError:
            pass

    # =========================================================================
    # MULTI-EDIT  (≥2 features selected)
    # =========================================================================

    def _enter_multi_edit_mode(self, layer, selected_ids):
        """Switch the panel into multi-edit mode for the given selection.

        The embedded form is replaced with a QgsAttributeForm in MultiEditMode.
        QGIS's native multi-edit UI shows mixed-value indicators for fields
        that differ across the selection, and only applies changes to fields
        that the user explicitly modifies.  Changes are NOT committed until the
        user clicks "Apply to selected" (OK).
        """
        n = len(selected_ids)
        if self._multi_edit_mode:
            # Already in multi-edit — refresh the form if the selection changed.
            self._multi_label.setText(f"M ({n})")
            self._show_multi_edit_form(layer)
            return

        self._accept_current_form()
        self._multi_edit_mode = True

        # Grey out navigation controls.
        self.first_btn.setEnabled(False)
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.last_btn.setEnabled(False)
        self.flash_btn.setEnabled(False)
        self.del_btn.setEnabled(False)

        # Replace the spinbox counter with the "M (n)" label.
        self.feature_spin.setVisible(False)
        self._multi_label.setText(f"M ({n})")
        self._multi_label.setVisible(True)

        self._multi_edit_buttons.setVisible(True)
        self._show_multi_edit_form(layer)

    def _exit_multi_edit_mode(self):
        """Exit multi-edit mode and return to normal single-feature navigation.

        Called on Cancel, layer switch, or when selection drops below 2.
        Does NOT save — changes are discarded unless the user clicked OK first.
        """
        if not self._multi_edit_mode:
            return
        self._multi_edit_mode = False

        # Remove the multi-edit form widget directly, WITHOUT calling form.save(),
        # so that unsaved multi-edit changes are discarded on cancel/deselect.
        form = self._feature_form
        container = self._feature_form_container
        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None
        widget_to_remove = container if container is not None else form
        if widget_to_remove is not None:
            try:
                self._main_layout.removeWidget(widget_to_remove)
            except Exception:
                pass
            try:
                widget_to_remove.setParent(None)
            except Exception:
                pass
            try:
                widget_to_remove.deleteLater()
            except Exception:
                pass

        # Restore navigation UI.
        self._multi_label.setVisible(False)
        self.feature_spin.setVisible(True)
        self._multi_edit_buttons.setVisible(False)
        self._multi_edit_rows = []
        self._update_edit_buttons()

        # Re-show the single-feature form for the current position.
        if self.feature_ids and self.current_index >= 0:
            self._navigate_to_current()
        else:
            self._update_display()

    def _show_multi_edit_form(self, layer):
        """Build and embed a native QgsAttributeForm in MultiEditMode.

        Constructs the form with an *empty* QgsFeature so that no single
        feature's values are pre-loaded.  QGIS's MultiEditMode then reads
        ``layer.selectedFeatureIds()`` internally to populate each field
        widget with the common value (or a mixed-values indicator) and
        shows the chain-link icon next to each field.  The user clicks a
        chain icon to mark that field for batch editing, then clicks
        'Apply to selected'.
        """
        self._remove_current_form()
        self._multi_edit_rows = []
        self._form_placeholder.setVisible(False)
        try:
            # Multi-edit requires an editable layer — the user must start
            # editing themselves; we never auto-start it here.
            if not layer.isEditable():
                self._form_placeholder.setText(
                    "Enable layer editing to use multi-edit."
                )
                self._form_placeholder.setVisible(True)
                return

            context = QgsAttributeEditorContext()
            try:
                vlt = self.iface.vectorLayerTools()
                if vlt is not None:
                    context.setVectorLayerTools(vlt)
            except Exception:
                pass

            # Empty feature — in MultiEditMode QGIS reads the layer's
            # selectedFeatureIds() to populate widgets, not this feature.
            feat = QgsFeature(layer.fields())
            form = QgsAttributeForm(layer, feat, context, self)

            # Set MultiEditMode.  form.setMode() takes
            # QgsAttributeEditorContext::Mode, so try that enum first; fall
            # back through other spellings for version compat, then try the
            # raw integers (3 for QGIS >= 3.14 which added IdentifyMode at 2,
            # 2 for older QGIS 3.x builds where IdentifyMode didn't exist).
            _mode_candidates = []
            for _src in (QgsAttributeEditorContext,
                         getattr(QgsAttributeEditorContext, 'Mode', None),
                         QgsAttributeForm,
                         getattr(QgsAttributeForm, 'Mode', None)):
                if _src is None:
                    continue
                _v = getattr(_src, 'MultiEditMode', None)
                if _v is not None and _v not in _mode_candidates:
                    _mode_candidates.append(_v)
            _mode_candidates.extend([3, 2])  # integer fallbacks
            for _m in _mode_candidates:
                try:
                    form.setMode(_m)
                    break
                except (TypeError, AttributeError):
                    continue

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            try:
                scroll.setFrameShape(QFrame.NoFrame)
            except (AttributeError, TypeError):
                try:
                    scroll.setFrameShape(QFrame.Shape.NoFrame)
                except Exception:
                    pass
            _AsNeeded = (
                getattr(Qt, 'ScrollBarAsNeeded', None)
                or Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            scroll.setVerticalScrollBarPolicy(_AsNeeded)
            scroll.setHorizontalScrollBarPolicy(_AsNeeded)
            from qgis.PyQt.QtWidgets import QSizePolicy
            _Expanding = (
                getattr(QSizePolicy, 'Expanding', None)
                or QSizePolicy.Policy.Expanding
            )
            scroll.setSizePolicy(_Expanding, _Expanding)
            scroll.setMinimumHeight(0)
            scroll.setWidget(form)

            self._feature_form = form
            self._feature_form_container = scroll
            self._feature_form_layer_id = layer.id()
            insert_pos = self._main_layout.indexOf(self._multi_edit_buttons)
            if insert_pos >= 0:
                self._main_layout.insertWidget(insert_pos, scroll, 1)
            else:
                self._main_layout.addWidget(scroll, 1)
            scroll.show()
            form.show()
        except Exception as e:
            self._form_placeholder.setText(f'Cannot display multi-edit form:\n{e}')
            self._form_placeholder.setVisible(True)
            self._feature_form = None

    def _on_multi_edit_ok(self):
        """Apply multi-edit changes to all selected features and exit.

        Ensures the layer is in edit mode (auto-starts if needed), then
        delegates entirely to ``QgsAttributeForm.save()`` in MultiEditMode.
        That internally calls ``saveMultiEdit()``, which only writes fields
        whose chain-link icon was clicked by the user — identical to the
        native QGIS attribute-table multi-edit behaviour.
        """
        layer = self.layer_combo.currentLayer()
        form = self._feature_form
        if not isinstance(layer, QgsVectorLayer) or form is None:
            self._exit_multi_edit_mode()
            return

        # saveMultiEdit() returns False immediately if the layer is read-only.
        if not layer.isEditable():
            if not layer.startEditing():
                self.iface.messageBar().pushWarning(
                    "FeatureNavEd",
                    "Cannot start editing \u2014 changes not applied."
                )
                return

        try:
            ok = form.save()
        except Exception:
            ok = False

        if ok:
            n_f = len(layer.selectedFeatureIds())
            self.iface.messageBar().pushSuccess(
                "FeatureNavEd",
                f"Changes applied to {n_f} feature(s). "
                "Save Layer Edits to persist."
            )
            self._exit_multi_edit_mode()
        else:
            self.iface.messageBar().pushWarning(
                "FeatureNavEd",
                "Could not apply changes \u2014 the layer may not be editable."
            )

    def _on_multi_edit_cancel(self):
        """Discard all multi-edit inputs and return to single-edit mode."""
        self._exit_multi_edit_mode()

    def _navigate_to_current(self):
        layer = self.layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer) or self.current_index < 0:
            self._update_display()
            return

        fid = self.feature_ids[self.current_index]
        feat = layer.getFeature(fid)

        if not feat.isValid():
            self._update_display()
            return

        layer.selectByIds([fid])

        if self.auto_zoom_cb.isChecked() and feat.hasGeometry():
            self._zoom_to_feature(feat, layer)

        if feat.hasGeometry():
            if self.enable_flash_cb.isChecked():
                try:
                    self.iface.mapCanvas().flashGeometries(
                        [feat.geometry()], layer.crs()
                    )
                except AttributeError:
                    pass

        self._show_feature_form(layer, feat)
        self._update_display()

    def _zoom_to_feature(self, feature, layer):
        canvas = self.iface.mapCanvas()
        geom = feature.geometry()

        transform = QgsCoordinateTransform(
            layer.crs(),
            canvas.mapSettings().destinationCrs(),
            QgsProject.instance()
        )

        center = geom.centroid().asPoint()
        transformed = transform.transform(center)
        canvas.setCenter(transformed)
        if not self.auto_scale_cb.isChecked():
            canvas.zoomScale(self.scale_spin.value())
        canvas.refresh()

    # =========================================================================
    # NATIVE FEATURE FORM
    # =========================================================================

    def _accept_current_form(self):
        """Push any pending widget values into the current form's feature.

        This does NOT commit edits to disk — it only pulls the values out of
        the form's widgets into the form's working ``QgsFeature``. The layer
        still needs its standard "Save Edits" action to persist changes.
        Calling this before swapping features prevents edits made on the
        current feature from being silently discarded when we navigate away.
        """
        form = self._feature_form
        if form is None:
            return
        # In SingleEditMode, save() applies widget values to the edit buffer
        # via QgsVectorLayer.changeAttributeValue(), but the layer's edit
        # buffer is only persisted on the user's explicit "Save Edits".
        try:
            form.save()
        except Exception:
            pass

    def _remove_current_form(self):
        """Remove the current feature form widget from the layout.

        Pushes pending widget values into the edit buffer first (so edits made
        on the current feature are not silently dropped when the form is
        swapped out), and is defensive against the underlying C++ layer having
        already been destroyed.
        """
        form = self._feature_form
        container = self._feature_form_container
        # Clear the attributes first so any re-entrancy is safe.
        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None
        if form is not None:
            try:
                form.save()
            except Exception:
                pass
        widget_to_remove = container if container is not None else form
        if widget_to_remove is None:
            return
        try:
            self._main_layout.removeWidget(widget_to_remove)
        except Exception:
            pass
        try:
            widget_to_remove.close()
        except Exception:
            pass
        try:
            widget_to_remove.setParent(None)
        except Exception:
            pass
        try:
            widget_to_remove.deleteLater()
        except Exception:
            pass

    def _show_feature_form(self, layer, feature):
        """Show an embedded QgsAttributeForm for the given feature.

        Uses ``QgsAttributeForm`` directly (rather than
        ``iface.getFeatureForm()`` which returns a ``QgsAttributeDialog``
        wrapper) so that:
          * the form embeds cleanly in the dock layout with no OK/Cancel bar
          * it operates in ``SingleEditMode`` — widget changes flow into the
            layer's edit buffer when the field loses focus / save() is called,
            but are NOT auto-committed to disk
          * the user persists edits via the layer's standard "Save Edits"
            action, exactly like the native attribute form

        For repeat visits to features within the same layer, the form widget
        is reused via ``setFeature()`` instead of being rebuilt from scratch,
        which is dramatically faster for wide forms.
        """
        # Cache hit: reuse the existing form for this layer.
        if (self._feature_form is not None
                and self._feature_form_layer_id == layer.id()):
            try:
                self._feature_form.save()  # commit any in-progress edits first
            except Exception:
                pass
            try:
                self._feature_form.setFeature(feature)
                self._form_placeholder.setVisible(False)
                return
            except Exception:
                # Fall through to a full rebuild on any unexpected failure.
                pass

        self._remove_current_form()
        self._form_placeholder.setVisible(False)

        try:
            context = QgsAttributeEditorContext()
            try:
                vlt = self.iface.vectorLayerTools()
                if vlt is not None:
                    context.setVectorLayerTools(vlt)
            except Exception:
                pass

            form = QgsAttributeForm(layer, feature, context, self)
            # SingleEditMode = 0 in QgsAttributeForm.Mode. Different QGIS / sip
            # builds expose the enum value differently and some reject the
            # scoped enum on this overload, so try several spellings and fall
            # back to the integer.
            _mode_candidates = []
            try:
                _mode_candidates.append(QgsAttributeForm.SingleEditMode)
            except AttributeError:
                pass
            try:
                _mode_candidates.append(QgsAttributeForm.Mode.SingleEditMode)
            except AttributeError:
                pass
            _mode_candidates.append(0)  # integer fallback
            for _m in _mode_candidates:
                try:
                    form.setMode(_m)
                    break
                except (TypeError, AttributeError):
                    continue

            # As the user edits widgets, push values into the layer's edit
            # buffer right away — same behaviour as the standard attribute
            # form, so the 'Save Edits' action becomes enabled immediately
            # rather than only after navigating to the next feature.
            def _push_to_buffer(*_args):
                try:
                    form.save()
                except Exception:
                    pass
            try:
                form.attributeChanged.connect(_push_to_buffer)
            except Exception:
                pass

            # Wrap the form in a QScrollArea so layers with many fields can
            # scroll instead of being clipped by the dock's available height.
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            try:
                scroll.setFrameShape(QFrame.NoFrame)
            except (AttributeError, TypeError):
                try:
                    scroll.setFrameShape(QFrame.Shape.NoFrame)
                except Exception:
                    pass
            # Force scrollbars to appear when the form is taller than the
            # available viewport. Without explicit policies some styles end
            # up never drawing the vertical scrollbar, so the bottom fields
            # get clipped.
            _AsNeeded = (
                getattr(Qt, 'ScrollBarAsNeeded', None)
                or Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            scroll.setVerticalScrollBarPolicy(_AsNeeded)
            scroll.setHorizontalScrollBarPolicy(_AsNeeded)
            # Allow the scroll area to shrink below the form's preferred
            # height (otherwise its sizeHint forces the dock to grow to fit
            # all fields and the bottom of the form falls off the screen).
            from qgis.PyQt.QtWidgets import QSizePolicy
            _Expanding = (
                getattr(QSizePolicy, 'Expanding', None)
                or QSizePolicy.Policy.Expanding
            )
            scroll.setSizePolicy(_Expanding, _Expanding)
            scroll.setMinimumHeight(0)
            scroll.setWidget(form)

            self._feature_form = form
            self._feature_form_container = scroll
            self._feature_form_layer_id = layer.id()
            # Insert before the multi-edit button bar so it always sits below the form.
            insert_pos = self._main_layout.indexOf(self._multi_edit_buttons)
            if insert_pos >= 0:
                self._main_layout.insertWidget(insert_pos, scroll, 1)
            else:
                self._main_layout.addWidget(scroll, 1)
            scroll.show()
            form.show()
        except Exception as e:
            self._form_placeholder.setText(f"Cannot display feature form:\n{e}")
            self._form_placeholder.setVisible(True)
            self._feature_form = None

    # =========================================================================
    # DISPLAY STATE
    # =========================================================================

    def _update_display(self):
        # Don't modify navigation UI while multi-edit mode is active.
        if self._multi_edit_mode:
            return
        total = len(self.feature_ids)

        self.feature_spin.blockSignals(True)
        if total == 0:
            # No features at all (empty layer, or Selected-only with nothing selected).
            self._multi_label.setVisible(False)
            self.feature_spin.setVisible(True)
            self.feature_spin.setRange(0, 0)
            self.feature_spin.setValue(0)
            self.feature_total_label.setText("/ 0")
            self.first_btn.setEnabled(False)
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.last_btn.setEnabled(False)
            self.flash_btn.setEnabled(False)
            self._remove_current_form()
            self._form_placeholder.setText("No feature selected")
            self._form_placeholder.setVisible(True)
            self.feature_spin.blockSignals(False)
            self._update_sort_value()
            return

        if self.current_index < 0:
            # NA state: features exist on the layer but none is active.
            # Show 'NA / <total>' with all nav buttons enabled so the user can
            # press First/Next/Last/Prev to start browsing.
            self.feature_spin.setRange(0, 0)
            self.feature_spin.setValue(0)
            self.feature_spin.setVisible(False)
            self._multi_label.setText("NA")
            self._multi_label.setVisible(True)
            self.feature_total_label.setText(f"/ {total}")
            self.first_btn.setEnabled(True)
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.last_btn.setEnabled(True)
            self._remove_current_form()
            self._form_placeholder.setText("")
            self._form_placeholder.setVisible(True)
            self.feature_spin.blockSignals(False)
            self._update_sort_value()
            self._update_edit_buttons()
            return

        # Normal single-feature navigation.
        self._multi_label.setVisible(False)
        self.feature_spin.setVisible(True)
        self.feature_spin.setRange(1, total)
        self.feature_spin.setValue(self.current_index + 1)
        self.feature_total_label.setText(f"/ {total}")
        self.feature_spin.blockSignals(False)
        self.first_btn.setEnabled(self.current_index > 0)
        self.prev_btn.setEnabled(self.current_index > 0)
        self.next_btn.setEnabled(self.current_index < total - 1)
        self.last_btn.setEnabled(self.current_index < total - 1)
        self._update_sort_value()
        self._update_edit_buttons()

    def _update_sort_value(self):
        """Update the sort group title with the current feature's sort field value."""
        sort_field = self.sort_field_combo.currentData()
        layer = self.layer_combo.currentLayer()
        if not sort_field or not isinstance(layer, QgsVectorLayer) or self.current_index < 0:
            self._sort_group.setTitle("Sort By")
            return
        fid = self.feature_ids[self.current_index]
        feat = layer.getFeature(fid)
        if not feat.isValid():
            self._sort_group.setTitle("Sort By")
            return
        val = feat[sort_field]
        if val is None or val == NULL:
            self._sort_group.setTitle(f"Sort By \u2014 {sort_field}: NULL")
        else:
            self._sort_group.setTitle(f"Sort By \u2014 {sort_field}: {val}")

    def _open_attribute_table(self):
        """Open the attribute table for the current layer."""
        layer = self.layer_combo.currentLayer()
        if isinstance(layer, QgsVectorLayer):
            self.iface.showAttributeTable(layer)

    # =========================================================================
    # LOCK SELECTION  (filter from active selection)
    # =========================================================================

    def _on_lock_selection_toggled(self, checked):
        layer = self.layer_combo.currentLayer()
        if checked:
            if not isinstance(layer, QgsVectorLayer):
                self.lock_selection_btn.blockSignals(True)
                self.lock_selection_btn.setChecked(False)
                self.lock_selection_btn.blockSignals(False)
                return
            ids = list(layer.selectedFeatureIds())
            if not ids:
                self.iface.messageBar().pushInfo(
                    "FeatureNavEd",
                    "No features selected on the active layer."
                )
                self.lock_selection_btn.blockSignals(True)
                self.lock_selection_btn.setChecked(False)
                self.lock_selection_btn.blockSignals(False)
                return
            self._locked_selection_ids = set(ids)
            self.selected_only_cb.blockSignals(True)
            self.selected_only_cb.setChecked(True)
            self.selected_only_cb.blockSignals(False)
            self.lock_selection_btn.setIcon(
                QgsApplication.getThemeIcon('/locked.svg')
            )
            self.lock_selection_btn.setToolTip(
                f"Locked {len(ids)} feature(s). Click to unlock."
            )
        else:
            self._locked_selection_ids = None
            self.lock_selection_btn.setIcon(
                QgsApplication.getThemeIcon('/lockedGray.svg')
            )
            self.lock_selection_btn.setToolTip(
                "Lock the current selection as a fixed navigation set"
            )
        self._reload_features()

    # =========================================================================
    # DUPLICATE / DELETE
    # =========================================================================

    def _update_edit_buttons(self, *_args):
        layer = self.layer_combo.currentLayer()
        editable = isinstance(layer, QgsVectorLayer) and layer.isEditable()
        has_feature = self.current_index >= 0 and bool(self.feature_ids)
        self.flash_btn.setEnabled(has_feature)
        self.del_btn.setEnabled(editable and has_feature)

    def _delete_current_feature(self):
        layer = self.layer_combo.currentLayer()
        if (not isinstance(layer, QgsVectorLayer)
                or not layer.isEditable()
                or self.current_index < 0):
            return
        fid = self.feature_ids[self.current_index]
        reply = QMessageBox.question(
            self,
            "Delete Feature",
            f"Delete feature {fid} from \"{layer.name()}\"?\n\n"
            "The change goes into the layer's edit buffer \u2014 click 'Save Layer Edits' "
            "to persist, or 'Rollback' to undo.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not layer.deleteFeature(fid):
            self.iface.messageBar().pushWarning(
                "FeatureNavEd", f"Failed to delete feature {fid}."
            )
            return
        prev_index = self.current_index
        self._reload_features()
        if self.feature_ids:
            self.current_index = min(prev_index, len(self.feature_ids) - 1)
            self._navigate_to_current()

    # =========================================================================
    # SETTINGS PERSISTENCE
    # =========================================================================

    _SETTINGS_PREFIX = "featurenaved"

    def _save_settings(self):
        try:
            s = QgsSettings()
            s.setValue(f"{self._SETTINGS_PREFIX}/auto_zoom",
                       bool(self.auto_zoom_cb.isChecked()))
            s.setValue(f"{self._SETTINGS_PREFIX}/auto_scale",
                       bool(self.auto_scale_cb.isChecked()))
            s.setValue(f"{self._SETTINGS_PREFIX}/enable_flash",
                       bool(self.enable_flash_cb.isChecked()))
            s.setValue(f"{self._SETTINGS_PREFIX}/scale",
                       int(self.scale_spin.value()))
            s.setValue(f"{self._SETTINGS_PREFIX}/sort_ascending",
                       bool(self.sort_ascending))
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
            v = s.value(f"{self._SETTINGS_PREFIX}/{key}", default)
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        self.auto_zoom_cb.blockSignals(True)
        self.auto_zoom_cb.setChecked(_b("auto_zoom", True))
        self.auto_zoom_cb.blockSignals(False)

        self.auto_scale_cb.blockSignals(True)
        self.auto_scale_cb.setChecked(_b("auto_scale", True))
        self.auto_scale_cb.blockSignals(False)
        self._update_scale_controls(self.auto_scale_cb.isChecked())

        self.enable_flash_cb.blockSignals(True)
        self.enable_flash_cb.setChecked(_b("enable_flash", False))
        self.enable_flash_cb.blockSignals(False)

        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(_i("scale", self.scale_spin.value()))
        self.scale_spin.blockSignals(False)

        self.sort_ascending = _b("sort_ascending", True)
        if self.sort_ascending:
            self.sort_order_btn.setIcon(
                QgsApplication.getThemeIcon('/mActionArrowUp.svg')
            )
            self.sort_order_btn.setToolTip("Ascending \u2014 click to toggle")
        else:
            self.sort_order_btn.setIcon(
                QgsApplication.getThemeIcon('/mActionArrowDown.svg')
            )
            self.sort_order_btn.setToolTip("Descending \u2014 click to toggle")

