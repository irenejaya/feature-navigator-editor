# -*- coding: utf-8 -*-
"""
Widget construction for the FeatureNavEd dock.

:class:`NavigatorUiMixin` only *builds* the widgets (like a pyuic ``setupUi``);
all behaviour lives in :mod:`.navigator_widget`.
"""

import os

from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QMenu,
    QSpinBox, QVBoxLayout, QWidget, QWidgetAction,
)
from qgis.core import Qgis, QgsApplication, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox

from .compat import (
    ADJUST_TO_MIN_CONTENTS, ALIGN_CENTER, APP_SHORTCUT, INSTANT_POPUP,
    LINKS_BY_MOUSE, RICH_TEXT, SUNKEN, VLINE, QShortcut,
)
from .field_organizer import FieldOrganizer
from .find_bar import FindBar
from .multi_edit import MultiEditPanel
from .utils import tool_button


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



class NavigatorUiMixin:
    """Builds the dock's widgets. Mixed into ``FeatureNavEdDockWidget``."""

    def _build_ui(self):
        main_widget = _DropWidget(self)
        self._main_layout = QVBoxLayout(main_widget)
        self._main_layout.setContentsMargins(4, 4, 4, 4)
        self._main_layout.setSpacing(4)

        self._build_toolbar()
        self._find_bar = FindBar()
        self._find_bar.setVisible(False)
        self._main_layout.addWidget(self._find_bar)

        bold_group = (
            "QGroupBox { font-weight: bold; }"
            " QGroupBox * { font-weight: normal; }"
        )

        # --- Layer ---
        layer_group = QGroupBox("Layer")
        layer_group.setStyleSheet(bold_group)
        layer_layout = QHBoxLayout()
        layer_layout.setContentsMargins(4, 4, 4, 4)
        layer_layout.setSpacing(4)
        self.layer_combo = QgsMapLayerComboBox()
        try:
            self.layer_combo.setFilters(Qgis.LayerFilter.VectorLayer)
        except AttributeError:
            self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setCurrentIndex(0)
        self.layer_combo.setShowCrs(True)
        self.layer_combo.setMinimumContentsLength(10)
        self.layer_combo.setSizeAdjustPolicy(ADJUST_TO_MIN_CONTENTS)
        layer_layout.addWidget(self.layer_combo, 1)
        self.active_layer_btn = tool_button(
            '/mActionRefresh.svg', "Use active layer / reload")
        layer_layout.addWidget(self.active_layer_btn)
        layer_group.setLayout(layer_layout)
        self._main_layout.addWidget(layer_group)

        # --- Sort ---
        self._sort_group = QGroupBox("Sort By")
        self._sort_group.setStyleSheet(bold_group)
        sort_layout = QHBoxLayout()
        sort_layout.setContentsMargins(4, 4, 4, 4)
        sort_layout.setSpacing(4)
        self.sort_field_combo = QComboBox()
        self.sort_field_combo.setToolTip("Field to sort features by")
        self.sort_field_combo.setMinimumContentsLength(8)
        self.sort_field_combo.setSizeAdjustPolicy(ADJUST_TO_MIN_CONTENTS)
        sort_layout.addWidget(self.sort_field_combo, 1)
        self.sort_order_btn = tool_button(
            '/mActionArrowUp.svg', "Ascending — click to toggle")
        self.sort_order_btn.setIconSize(self.sort_order_btn.iconSize() * 1.2)
        sort_layout.addWidget(self.sort_order_btn)
        self._sort_group.setLayout(sort_layout)
        self._main_layout.addWidget(self._sort_group)

        # --- Navigate ---
        self._nav_group = QGroupBox("Navigate")
        self._nav_group.setStyleSheet(bold_group)
        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(4, 4, 4, 4)
        nav_layout.setSpacing(4)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(2)
        self.first_btn = tool_button(
            '/mActionDoubleArrowLeft.svg', "First feature (Alt+Home)")
        self.prev_btn = tool_button(
            '/mActionArrowLeft.svg', "Previous feature (Alt+Left / Alt+P)")
        nav_row.addWidget(self.first_btn)
        nav_row.addWidget(self.prev_btn)
        nav_row.addStretch()
        self.feature_spin = QSpinBox()
        self.feature_spin.setRange(0, 0)
        self.feature_spin.setAlignment(ALIGN_CENTER)
        self.feature_spin.setToolTip("Type a feature number to jump to it")
        self.feature_spin.setKeyboardTracking(False)
        nav_row.addWidget(self.feature_spin)
        self._counter_label = QLabel("NA")
        self._counter_label.setAlignment(ALIGN_CENTER)
        self._counter_label.setVisible(False)
        self._counter_label.setMinimumWidth(self.feature_spin.sizeHint().width())
        nav_row.addWidget(self._counter_label)
        self.feature_total_label = QLabel("/ 0")
        nav_row.addWidget(self.feature_total_label)
        nav_row.addStretch()
        self.next_btn = tool_button(
            '/mActionArrowRight.svg', "Next feature (Alt+Right / Alt+N)")
        self.last_btn = tool_button(
            '/mActionDoubleArrowRight.svg', "Last feature (Alt+End)")
        self.flash_btn = tool_button(
            '/mActionHighlightFeature.svg', "Flash feature again (and re-centre)")
        self.del_btn = tool_button(
            '/mActionDeleteSelected.svg',
            "Delete current feature (layer must be in edit mode)")
        for btn in (self.next_btn, self.last_btn, self.flash_btn, self.del_btn):
            nav_row.addWidget(btn)
        nav_layout.addLayout(nav_row)

        options_row = QHBoxLayout()
        self.auto_zoom_cb = QCheckBox("Auto-zoom")
        self.auto_zoom_cb.setChecked(True)
        self.auto_zoom_cb.setToolTip("Centre on feature when navigating")
        options_row.addWidget(self.auto_zoom_cb)
        self.auto_scale_cb = QCheckBox("Auto-scale")
        self.auto_scale_cb.setChecked(True)
        self.auto_scale_cb.setToolTip(
            "Use current map canvas scale (uncheck to set a custom scale)")
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
        # Don't let the "1:1000000" width hint dictate the dock's minimum width.
        self.scale_spin.setMinimumWidth(72)
        options_row.addWidget(self.scale_spin)
        nav_layout.addLayout(options_row)
        self._nav_group.setLayout(nav_layout)
        self._main_layout.addWidget(self._nav_group)
        self._update_scale_controls(self.auto_scale_cb.isChecked())

        # --- Content: placeholder / single form (inserted later) / multi-edit
        self._form_placeholder = QLabel()
        self._form_placeholder.setAlignment(ALIGN_CENTER)
        self._form_placeholder.setWordWrap(True)
        self._form_placeholder.setTextFormat(RICH_TEXT)
        self._form_placeholder.setTextInteractionFlags(LINKS_BY_MOUSE)
        self._form_placeholder.setOpenExternalLinks(False)
        self._form_placeholder.setMinimumWidth(0)
        self._main_layout.addWidget(self._form_placeholder, 1)

        self._multi_panel = MultiEditPanel(self.iface)
        self._multi_panel.setVisible(False)
        self._main_layout.addWidget(self._multi_panel, 1)

        self._fields_hint = QLabel()
        self._fields_hint.setTextFormat(RICH_TEXT)
        self._fields_hint.setTextInteractionFlags(LINKS_BY_MOUSE)
        self._fields_hint.setOpenExternalLinks(False)
        self._fields_hint.setVisible(False)
        self._main_layout.addWidget(self._fields_hint)

        # --- Keyboard shortcuts (ApplicationShortcut: work anywhere in QGIS)
        self._shortcuts = []
        for keys, slot in (("Alt+Left", self._go_prev), ("Alt+P", self._go_prev),
                           ("Alt+Right", self._go_next), ("Alt+N", self._go_next),
                           ("Alt+Home", self._go_first), ("Alt+End", self._go_last)):
            shortcut = QShortcut(QKeySequence(keys), main_widget)
            shortcut.setContext(APP_SHORTCUT)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

        self.setWidget(main_widget)

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(2)

        # Editing controls act on the navigated layer, whichever layer is
        # active in the Layers panel.
        self.toggle_edit_btn = tool_button(
            '/mActionToggleEditing.svg',
            "Toggle editing on the navigated layer", checkable=True)
        self.save_edits_btn = tool_button(
            '/mActionSaveEdits.svg', "Save edits of the navigated layer (stay in edit mode)")
        self.undo_btn = tool_button(
            '/mActionUndo.svg', "Undo last edit on the navigated layer")
        self.redo_btn = tool_button(
            '/mActionRedo.svg', "Redo on the navigated layer")
        for btn in (self.toggle_edit_btn, self.save_edits_btn,
                    self.undo_btn, self.redo_btn):
            row.addWidget(btn)

        separator = QFrame()
        separator.setFrameShape(VLINE)
        separator.setFrameShadow(SUNKEN)
        row.addWidget(separator)

        try:
            import db_manager as _dbm
            qb_icon_path = os.path.join(os.path.dirname(_dbm.__file__), 'icons', 'sql.gif')
            qb_icon = (QIcon(qb_icon_path) if os.path.exists(qb_icon_path)
                       else QgsApplication.getThemeIcon('/mActionFilter2.svg'))
        except Exception:
            qb_icon = QgsApplication.getThemeIcon('/mActionFilter2.svg')
        self.query_builder_btn = tool_button(
            qb_icon,
            "Query Builder — filter the layer and navigate only matching "
            "features; click again to clear the filter",
            checkable=True)
        row.addWidget(self.query_builder_btn)

        self.search_btn = tool_button(
            '/search.svg', "Find feature by value (no filtering)", checkable=True)
        row.addWidget(self.search_btn)

        # --- Field organizer ---
        self.fields_btn = tool_button(
            '/mActionShowAllLayers.svg', "Choose which fields to show", checkable=True)
        self.fields_btn.setPopupMode(INSTANT_POPUP)
        self._fields_menu = QMenu(self.fields_btn)
        self._field_organizer = FieldOrganizer(self._fields_menu)
        organizer_action = QWidgetAction(self._fields_menu)
        organizer_action.setDefaultWidget(self._field_organizer)
        self._fields_menu.addAction(organizer_action)
        self.fields_btn.setMenu(self._fields_menu)
        row.addWidget(self.fields_btn)

        # --- Settings menu ---
        self.settings_btn = tool_button('/mActionOptions.svg', "Settings")
        self.settings_btn.setPopupMode(INSTANT_POPUP)
        menu = QMenu(self.settings_btn)
        container = QWidget(menu)
        menu_layout = QVBoxLayout(container)
        menu_layout.setContentsMargins(8, 6, 8, 6)
        menu_layout.setSpacing(4)
        self.lock_nav_cb = QCheckBox("Lock navigation")
        self.lock_nav_cb.setToolTip(
            "Keep the navigator on its current feature when you click or select\n"
            "other features on the map. The locked feature is outlined in orange.\n"
            "Navigation buttons, Find and Delete still work.\n"
            "Multi-edit is disabled while navigation is locked."
        )
        menu_layout.addWidget(self.lock_nav_cb)
        self.enable_flash_cb = QCheckBox("Flash on navigation")
        self.enable_flash_cb.setToolTip(
            "Briefly flash the feature on the map when the form navigates to it.\n"
            "The Flash button still works on demand."
        )
        menu_layout.addWidget(self.enable_flash_cb)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        self.settings_btn.setMenu(menu)
        row.addWidget(self.settings_btn)

        row.addStretch()
        self._main_layout.addLayout(row)
