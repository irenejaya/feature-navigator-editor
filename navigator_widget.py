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
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox,
    QGroupBox, QSpinBox, QToolButton, QDialogButtonBox
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
from qgis.gui import QgsMapLayerComboBox, QgsMapToolIdentifyFeature

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

        # --- Layer ---
        layer_group = QGroupBox("Layer")
        layer_layout = QVBoxLayout()
        layer_layout.setContentsMargins(4, 4, 4, 4)
        layer_layout.setSpacing(2)

        layer_row = QHBoxLayout()
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
        layer_row.addWidget(self.layer_combo)

        self.active_layer_btn = QToolButton()
        self.active_layer_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionRefresh.svg')
        )
        self.active_layer_btn.setToolTip("Use active layer / reload")
        self.active_layer_btn.setAutoRaise(True)
        layer_row.addWidget(self.active_layer_btn)
        layer_layout.addLayout(layer_row)

        self.drop_hint = QLabel("Drag a layer here or select above")
        self.drop_hint.setEnabled(False)
        self.drop_hint.setAlignment(_AlignCenter)
        layer_layout.addWidget(self.drop_hint)

        layer_group.setLayout(layer_layout)
        self._main_layout.addWidget(layer_group)

        # --- Sort ---
        sort_group = QGroupBox("Sort By")
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

        sort_group.setLayout(sort_layout)
        self._main_layout.addWidget(sort_group)

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
        self.first_btn.setToolTip("First feature")
        self.first_btn.setAutoRaise(True)
        nav_row.addWidget(self.first_btn)

        self.prev_btn = QToolButton()
        self.prev_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowLeft.svg')
        )
        self.prev_btn.setToolTip("Previous feature")
        self.prev_btn.setAutoRaise(True)
        nav_row.addWidget(self.prev_btn)

        nav_row.addStretch()
        self.feature_label = QLabel("0 / 0")
        self.feature_label.setAlignment(_AlignCenter)
        nav_row.addWidget(self.feature_label)
        nav_row.addStretch()

        self.next_btn = QToolButton()
        self.next_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionArrowRight.svg')
        )
        self.next_btn.setToolTip("Next feature")
        self.next_btn.setAutoRaise(True)
        nav_row.addWidget(self.next_btn)

        self.last_btn = QToolButton()
        self.last_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionDoubleArrowRight.svg')
        )
        self.last_btn.setToolTip("Last feature")
        self.last_btn.setAutoRaise(True)
        nav_row.addWidget(self.last_btn)

        self.pick_btn = QToolButton()
        self.pick_btn.setIcon(
            QgsApplication.getThemeIcon('/mActionIdentify.svg')
        )
        self.pick_btn.setToolTip("Pick feature from map")
        self.pick_btn.setAutoRaise(True)
        self.pick_btn.setCheckable(True)
        nav_row.addWidget(self.pick_btn)

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

        self.setWidget(main_widget)

    # =========================================================================
    # SIGNALS
    # =========================================================================

    def _connect_signals(self):
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self.active_layer_btn.clicked.connect(self._use_active_layer)
        self.sort_field_combo.currentIndexChanged.connect(self._reload_features)
        self.sort_order_btn.clicked.connect(self._toggle_sort_order)
        self.first_btn.clicked.connect(self._go_first)
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)
        self.last_btn.clicked.connect(self._go_last)
        self.pick_btn.toggled.connect(self._toggle_pick_mode)
        self.auto_scale_cb.toggled.connect(self._update_scale_controls)
        self.iface.mapCanvas().scaleChanged.connect(self._on_canvas_scale_changed)

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
        self.sort_field_combo.blockSignals(True)
        self.sort_field_combo.clear()
        self.feature_ids = []
        self.current_index = -1

        if isinstance(layer, QgsVectorLayer):
            self.sort_field_combo.addItem("(Feature ID)", None)
            for field in layer.fields():
                self.sort_field_combo.addItem(field.name(), field.name())
            self.drop_hint.setVisible(False)
        else:
            self.drop_hint.setVisible(True)

        self.sort_field_combo.blockSignals(False)
        self._reload_features()

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

        from qgis.core import QgsFeatureRequest
        request = QgsFeatureRequest()
        if sort_field:
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
        self.current_index = 0 if self.feature_ids else -1
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
            self._accept_current_form()
            self.current_index = 0
            self._navigate_to_current()

    def _go_prev(self):
        if self.feature_ids and self.current_index > 0:
            self._accept_current_form()
            self.current_index -= 1
            self._navigate_to_current()

    def _go_next(self):
        if self.feature_ids and self.current_index < len(self.feature_ids) - 1:
            self._accept_current_form()
            self.current_index += 1
            self._navigate_to_current()

    def _go_last(self):
        if self.feature_ids:
            self._accept_current_form()
            self.current_index = len(self.feature_ids) - 1
            self._navigate_to_current()

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

        if total == 0:
            self.feature_label.setText("0 / 0")
            self.first_btn.setEnabled(False)
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.last_btn.setEnabled(False)
            self._remove_current_form()
            self._form_placeholder.setText("No feature selected")
            self._form_placeholder.setVisible(True)
            return

        self.feature_label.setText(f"{self.current_index + 1} / {total}")
        self.first_btn.setEnabled(self.current_index > 0)
        self.prev_btn.setEnabled(self.current_index > 0)
        self.next_btn.setEnabled(self.current_index < total - 1)
        self.last_btn.setEnabled(self.current_index < total - 1)
