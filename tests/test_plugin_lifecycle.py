# -*- coding: utf-8 -*-
"""Loading, unloading and reloading the plugin."""

import sys

from qgis.core import QgsProject, QgsSettings

import featurenaved
from helpers import pump


def test_unload_leaves_no_live_connections(iface, make_layer, monkeypatch):
    layer = make_layer(5)
    plugin = featurenaved.classFactory(iface)
    plugin.initGui()
    plugin.action.trigger()
    pump()
    dock = plugin.dock_widget
    dock.layer_combo.setLayer(layer)
    dock._go_next()
    pump()
    plugin.unload()
    pump(10)

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    iface.canvas.zoomScale(5000)
    layer.selectByIds([1, 2])
    QgsProject.instance().removeMapLayer(layer.id())
    pump(10)
    assert errors == []


def test_settings_survive_a_reload(iface):
    plugin = featurenaved.classFactory(iface)
    plugin.initGui()
    plugin.action.trigger()
    plugin.dock_widget.lock_nav_cb.setChecked(True)
    assert QgsSettings().value("featurenaved/lock_navigation") in (True, "true")
    plugin.unload()

    again = featurenaved.classFactory(iface)
    again.initGui()
    again.action.trigger()
    try:
        assert again.dock_widget.lock_nav_cb.isChecked()
        assert again.dock_widget._nav_group.title() == "Navigate (locked)"
    finally:
        again.unload()
