# -*- coding: utf-8 -*-
"""Scale modes (keep / fit / fixed) and the measurements line."""

from qgis.core import QgsGeometry, QgsProject, QgsSettings, QgsVectorLayer, QgsFeature

from featurenaved.measurements import describe_geometry
from helpers import pump


def set_mode(dock, mode):
    dock.scale_mode_combo.setCurrentIndex(dock.scale_mode_combo.findData(mode))
    pump()


def canvas_ready(iface, layer):
    iface.canvas.resize(800, 600)
    iface.canvas.setDestinationCrs(layer.crs())
    iface.canvas.setExtent(layer.extent())
    iface.canvas.zoomScale(500)
    pump()


def test_fit_feature_shows_the_whole_parcel(dock, iface, parcels_gpkg):
    canvas_ready(iface, parcels_gpkg)
    dock.layer_combo.setLayer(parcels_gpkg)
    set_mode(dock, "fit")
    assert not dock.scale_spin.isEnabled()
    dock._go_first()
    pump()
    bbox = parcels_gpkg.getFeature(dock._current_fid()).geometry().boundingBox()
    extent = iface.canvas.extent()
    assert extent.contains(bbox)
    # Zoomed in to the feature (the canvas aspect ratio decides which side
    # gets the extra room): the tighter side is close to the feature size.
    tight = min(extent.width() / bbox.width(), extent.height() / bbox.height())
    assert tight < 1.5


def test_keep_scale_only_centres(dock, iface, parcels_gpkg):
    canvas_ready(iface, parcels_gpkg)
    dock.layer_combo.setLayer(parcels_gpkg)
    set_mode(dock, "keep")
    scale = iface.canvas.scale()
    dock._go_first()
    pump()
    assert abs(iface.canvas.scale() - scale) < 1
    centre = parcels_gpkg.getFeature(dock._current_fid()).geometry().centroid().asPoint()
    assert iface.canvas.center().distance(centre) < 1


def test_fixed_scale_uses_the_box(dock, iface, parcels_gpkg):
    canvas_ready(iface, parcels_gpkg)
    dock.layer_combo.setLayer(parcels_gpkg)
    set_mode(dock, "fixed")
    assert dock.scale_spin.isEnabled()
    dock.scale_spin.setValue(2500)
    dock._go_first()
    pump()
    assert abs(iface.canvas.scale() - 2500) < 1


def test_old_auto_scale_setting_is_migrated(iface):
    from featurenaved.navigator_widget import FeatureNavEdDockWidget
    QgsSettings().setValue("featurenaved/auto_scale", False)    # 1.0.x: custom scale
    widget = FeatureNavEdDockWidget(iface)
    try:
        assert widget.scale_mode_combo.currentData() == "fixed"
    finally:
        widget.cleanup()


def test_measurements_for_polygons(dock, parcels_gpkg):
    dock.layer_combo.setLayer(parcels_gpkg)
    dock._go_first()
    pump()
    text = dock._measure_label.text()
    assert dock._measure_label.isVisible()
    assert "Area 1,600.00 m²" in text and "Perimeter 160.00 m" in text
    assert "5 vertices" in text
    dock.show_measurements_cb.setChecked(False)
    pump()
    assert not dock._measure_label.isVisible()


def test_measurements_for_lines_and_points(make_layer):
    line = QgsVectorLayer("LineString?crs=EPSG:7855", "lines", "memory")
    QgsProject.instance().addMapLayer(line)
    text = describe_geometry(line, QgsGeometry.fromWkt("LINESTRING(0 0, 30 40, 30 50)"))
    assert text == "Length 60.00 m · 3 vertices"
    points = make_layer(1)
    text = describe_geometry(points, QgsGeometry.fromWkt("POINT(12.5 3)"))
    assert text == "X 12.50 · Y 3.00"
    assert describe_geometry(points, QgsFeature().geometry()) == ""
