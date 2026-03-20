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
    QLineEdit, QCompleter
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
    QgsVectorLayer, QgsCoordinateTransform
)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolIdentifyFeature, QgsExpressionLineEdit

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
        self._pick_tool = None
        self._prev_map_tool = None
        self._current_layer_id = None
        self._layer_positions = {}
        self._history = []          # list of (layer_id, fid)
        self._navigating_back = False

        self.setAllowedAreas(_AllDockAreas)
        self.setObjectName("FeatureNavEdDockWidget")
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()

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

        self._filter_bar.setVisible(False)
        self._main_layout.addWidget(self._filter_bar)

        # --- Layer ---
        layer_group = QGroupBox("Layer")
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

    def _on_layer_changed(self, layer):
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

        self._current_layer_id = layer.id() if isinstance(layer, QgsVectorLayer) else None

        self.sort_field_combo.blockSignals(True)
        self.sort_field_combo.clear()
        self.feature_ids = []
        self.current_index = -1

        if isinstance(layer, QgsVectorLayer):
            self.sort_field_combo.addItem("(Feature ID)", None)
            for field in layer.fields():
                self.sort_field_combo.addItem(field.name(), field.name())
            self.filter_expression.setLayer(layer)
            self.filter_expression.setExpression('')
            self.search_field_combo.clear()
            for field in layer.fields():
                self.search_field_combo.addItem(field.name(), field.name())
            self.search_value_edit.clear()
            self._populate_search_values()
            layer.selectionChanged.connect(self._on_selection_changed)
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
            selected = set(layer.selectedFeatureIds())
            self.feature_ids = [fid for fid in self.feature_ids if fid in selected]

        self.current_index = 0 if self.feature_ids else -1
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
        self._reload_features()

    # =========================================================================
    # NAVIGATION
    # =========================================================================

    def _go_first(self):
        if self.feature_ids:
            self._push_history()
            self._accept_current_form()
            self.current_index = 0
            self._navigate_to_current()

    def _go_prev(self):
        if self.feature_ids and self.current_index > 0:
            self._push_history()
            self._accept_current_form()
            self.current_index -= 1
            self._navigate_to_current()

    def _go_next(self):
        if self.feature_ids and self.current_index < len(self.feature_ids) - 1:
            self._push_history()
            self._accept_current_form()
            self.current_index += 1
            self._navigate_to_current()

    def _go_last(self):
        if self.feature_ids:
            self._push_history()
            self._accept_current_form()
            self.current_index = len(self.feature_ids) - 1
            self._navigate_to_current()

    def _go_to_feature_number(self, number):
        """Jump to a 1-based feature number typed in the spinbox."""
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
        """Reload features when selection changes and 'Selected only' is active."""
        if self.selected_only_cb.isChecked():
            self._reload_features()

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
        """Save any pending edits in the current form."""
        if self._feature_form is not None:
            try:
                self._feature_form.accept()
            except Exception:
                pass

    def _remove_current_form(self):
        """Remove the current feature form widget from the layout."""
        if self._feature_form is not None:
            self._main_layout.removeWidget(self._feature_form)
            try:
                self._feature_form.close()
            except Exception:
                pass
            self._feature_form.setParent(None)
            self._feature_form.deleteLater()
            self._feature_form = None

    def _show_feature_form(self, layer, feature):
        """Show the native QGIS feature form for the given feature."""
        self._remove_current_form()
        self._form_placeholder.setVisible(False)

        try:
            form = self.iface.getFeatureForm(layer, feature)
            # Hide OK/Cancel buttons so the form stays embedded
            btn_box = form.findChild(QDialogButtonBox)
            if btn_box:
                btn_box.hide()
            self._feature_form = form
            self._main_layout.addWidget(form, 1)
            form.show()
        except Exception as e:
            self._form_placeholder.setText(f"Cannot display feature form:\n{e}")
            self._form_placeholder.setVisible(True)
            self._feature_form = None

    # =========================================================================
    # DISPLAY STATE
    # =========================================================================

    def _update_display(self):
        total = len(self.feature_ids)

        self.feature_spin.blockSignals(True)
        if total == 0:
            self.feature_spin.setRange(0, 0)
            self.feature_spin.setValue(0)
            self.feature_total_label.setText("/ 0")
            self.first_btn.setEnabled(False)
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.last_btn.setEnabled(False)
            self._remove_current_form()
            self._form_placeholder.setText("No feature selected")
            self._form_placeholder.setVisible(True)
            self.feature_spin.blockSignals(False)
            self._update_sort_value()
            return

        self.feature_spin.setRange(1, total)
        self.feature_spin.setValue(self.current_index + 1)
        self.feature_total_label.setText(f"/ {total}")
        self.feature_spin.blockSignals(False)
        self.first_btn.setEnabled(self.current_index > 0)
        self.prev_btn.setEnabled(self.current_index > 0)
        self.next_btn.setEnabled(self.current_index < total - 1)
        self.last_btn.setEnabled(self.current_index < total - 1)
        self._update_sort_value()

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
