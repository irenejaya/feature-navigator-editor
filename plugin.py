# -*- coding: utf-8 -*-
"""
FeatureNavEd - Main Plugin Class

Registers the dock widget and creates toolbar/menu entries.
"""

import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication

# Qt5/Qt6 enum compatibility
_RightDockArea = getattr(Qt, 'RightDockWidgetArea', None) or Qt.DockWidgetArea.RightDockWidgetArea
_Vertical = getattr(Qt, 'Vertical', None) or Qt.Orientation.Vertical


class FeatureNavEdPlugin:
    """Main plugin class for FeatureNavEd."""

    def __init__(self, iface):
        self.iface = iface
        self.dock_widget = None
        self.action = None
        self.toolbar = None
        self.menu_name = "FeatureNavEd"

    def initGui(self):
        """Initialize the GUI - called when plugin is loaded."""
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.svg')
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = QgsApplication.getThemeIcon('/mActionPanToSelected.svg')

        # Create a dedicated toolbar (like hydrology_model_prep)
        self.toolbar = self.iface.addToolBar(self.menu_name)
        self.toolbar.setObjectName("FeatureNavEdToolbar")

        self.action = QAction(icon, "FeatureNavEd", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("Toggle the FeatureNavEd panel")
        self.action.triggered.connect(self._toggle_dock)

        self.toolbar.addAction(self.action)
        self.iface.addPluginToMenu(self.menu_name, self.action)

    def _toggle_dock(self, checked):
        """Show or hide the dock widget."""
        if self.dock_widget is None:
            from .navigator_widget import FeatureNavEdDockWidget
            self.dock_widget = FeatureNavEdDockWidget(self.iface)
            self.iface.addDockWidget(_RightDockArea, self.dock_widget)
            self.dock_widget.visibilityChanged.connect(self._on_visibility_changed)

            # Resize to fill full height of the dock area on first open
            main_window = self.iface.mainWindow()
            self.dock_widget.resize(
                self.dock_widget.width(),
                main_window.height()
            )
            try:
                main_window.resizeDocks(
                    [self.dock_widget],
                    [main_window.height()],
                    _Vertical
                )
            except AttributeError:
                pass

        self.dock_widget.setVisible(checked)

    def _on_visibility_changed(self, visible):
        """Sync the action check state with dock visibility."""
        self.action.setChecked(visible)

    def unload(self):
        """Unload the plugin - called when plugin is unloaded."""
        if self.dock_widget:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None

        if self.action:
            self.iface.removePluginMenu(self.menu_name, self.action)

        if self.toolbar:
            del self.toolbar
