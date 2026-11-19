# -*- coding: utf-8 -*-
"""Area and perimeter (or length / coordinates) of a feature, like the
"Derived" section of the Identify tool (project ellipsoid and units)."""

from qgis.core import Qgis, QgsDistanceArea, QgsProject, QgsWkbTypes


def _geometry_type(name):
    enum = getattr(Qgis, 'GeometryType', None)
    if enum is not None and hasattr(enum, name):
        return getattr(enum, name)
    return getattr(QgsWkbTypes, name + 'Geometry')


def describe_geometry(layer, geometry, decimals=2):
    """One-line summary: ``"Area 1,234.50 m² · Perimeter 145.20 m"`` for
    polygons, ``"Length 60.00 m"`` for lines, ``"X … · Y …"`` for single
    points. Empty string for features without geometry."""
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return ""
    project = QgsProject.instance()
    calculator = QgsDistanceArea()
    calculator.setSourceCrs(layer.crs(), project.transformContext())
    calculator.setEllipsoid(project.ellipsoid())

    items = []
    gtype = geometry.type()
    if gtype == _geometry_type('Polygon'):
        area_unit = project.areaUnits()
        area = calculator.convertAreaMeasurement(calculator.measureArea(geometry), area_unit)
        items.append("Area " + QgsDistanceArea.formatArea(area, decimals, area_unit, False))
        length_unit = project.distanceUnits()
        perimeter = calculator.convertLengthMeasurement(
            calculator.measurePerimeter(geometry), length_unit)
        items.append("Perimeter " + QgsDistanceArea.formatDistance(
            perimeter, decimals, length_unit, False))
    elif gtype == _geometry_type('Line'):
        length_unit = project.distanceUnits()
        length = calculator.convertLengthMeasurement(
            calculator.measureLength(geometry), length_unit)
        items.append("Length " + QgsDistanceArea.formatDistance(
            length, decimals, length_unit, False))
    elif gtype == _geometry_type('Point') and not geometry.isMultipart():
        point = geometry.asPoint()
        places = 6 if layer.crs().isGeographic() else decimals
        items.append(f"X {point.x():.{places}f} · Y {point.y():.{places}f}")
    return " · ".join(items)
