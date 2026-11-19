# -*- coding: utf-8 -*-
"""
Test setup: a headless QGIS application with isolated settings.

Run from the repository root (needs the QGIS Python bindings)::

    python3 -m pytest tests

On Windows use the OSGeo4W / QGIS shell so ``qgis`` is importable.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.PyQt.QtCore import QCoreApplication, QSettings  # noqa: E402
from qgis.core import QgsApplication  # noqa: E402

QGIS_APP = QgsApplication([], True)
QGIS_APP.initQgis()

ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_package():
    """Import the repository root as the ``featurenaved`` package."""
    spec = importlib.util.spec_from_file_location(
        "featurenaved", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["featurenaved"] = module
    spec.loader.exec_module(module)
    return module


_load_plugin_package()

from featurenaved.compat import qenum  # noqa: E402

# Keep QgsSettings away from the developer's real QGIS profile.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="featurenaved-tests-")
QCoreApplication.setOrganizationName("FeatureNavEdTests")
QCoreApplication.setApplicationName("tests")
_INI = qenum(QSettings, 'Format', 'IniFormat')
QSettings.setDefaultFormat(_INI)
QSettings.setPath(_INI, qenum(QSettings, 'Scope', 'UserScope'), _SETTINGS_DIR)

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem, QgsCoordinateTransformContext,
    QgsEditorWidgetSetup, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsPointXY, QgsProject, QgsSettings, QgsVectorFileWriter, QgsVectorLayer,
)
from qgis.gui import QgsGui, QgsMapCanvas, QgsMessageBar  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QMessageBox  # noqa: E402

from helpers import field_type, polygon_wkb_type, pump  # noqa: E402

QgsGui.editorWidgetRegistry().initEditors(None)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SETTINGS_DIR, ignore_errors=True)


class FakeIface:
    """The part of ``QgisInterface`` the plugin uses."""

    def __init__(self):
        self.main_window = QMainWindow()
        self.canvas = QgsMapCanvas(self.main_window)
        self.message_bar = QgsMessageBar(self.main_window)
        self.messages = []
        self.active_layer = None
        original = self.message_bar.pushMessage

        def push(title, text, *args, **kwargs):
            self.messages.append(text)
            return original(title, text, *args, **kwargs)

        self.message_bar.pushMessage = push

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return self.message_bar

    def mainWindow(self):
        return self.main_window

    def vectorLayerTools(self):
        return None     # the plugin falls back to the layer's own methods

    def activeLayer(self):
        return self.active_layer

    def addToolBar(self, name):
        return self.main_window.addToolBar(name)

    def addPluginToMenu(self, *args):
        pass

    def removePluginMenu(self, *args):
        pass

    def addDockWidget(self, area, dock):
        self.main_window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.main_window.removeDockWidget(dock)


@pytest.fixture(autouse=True)
def clean_state():
    QgsSettings().remove("featurenaved")
    yield
    QgsProject.instance().removeAllMapLayers()
    pump()


@pytest.fixture
def iface():
    return FakeIface()


@pytest.fixture
def dock(iface):
    from featurenaved.navigator_widget import FeatureNavEdDockWidget
    widget = FeatureNavEdDockWidget(iface)
    widget.resize(320, 900)
    widget.show()
    pump()
    yield widget
    widget.cleanup()
    widget.hide()
    widget.deleteLater()
    pump()


@pytest.fixture
def make_layer():
    """Memory point layer: area = (i*7) % n, land alternates COM/RES."""
    def factory(n=10, name="parcels"):
        layer = QgsVectorLayer(
            "Point?crs=EPSG:3857&field=area:double&field=land:string&field=fl:integer",
            name, "memory")
        features = []
        for i in range(n):
            feat = QgsFeature(layer.fields())
            feat.setAttributes([float((i * 7) % n), "RES" if i % 2 else "COM", -999])
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(i * 10, i * 10)))
            features.append(feat)
        layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        return layer
    return factory


@pytest.fixture
def parcels_gpkg(tmp_path):
    """GeoPackage polygon layer shaped like a real parcel dataset: ``fid``
    primary key, nullable integer, value-map text field, -999 doubles."""
    path = str(tmp_path / "parcels.gpkg")
    fields = QgsFields()
    for name, ftype in (("PARCEL_VIEW_PFI", field_type('String')),
                        ("PARCEL_MASTER_FID", field_type('Int')),
                        ("LAND_USE_TYPE", field_type('String')),
                        ("FL_1_PCT_MAX", field_type('Double')),
                        ("FL_2_PCT", field_type('Double')),
                        ("DEPTH_MAX_1_PCT", field_type('Double')),
                        ("PCL_FLOODED_AREA", field_type('Double'))):
        fields.append(QgsField(name, ftype))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    writer = QgsVectorFileWriter.create(
        path, fields, polygon_wkb_type(), QgsCoordinateReferenceSystem("EPSG:7855"),
        QgsCoordinateTransformContext(), options)
    for i in range(30):
        feat = QgsFeature(fields)
        feat.setAttributes([f"PFI{i}", None if i % 3 else i,
                            "RESIDENTIAL" if i % 4 else "COMMERCIAL",
                            -999.0, -999.0, -999.0, float(i * 13 % 30)])
        x, y = 320000 + i * 50, 5810000
        feat.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} {y},{x + 40} {y},{x + 40} {y + 40},{x} {y + 40},{x} {y}))"))
        writer.addFeature(feat)
    del writer
    layer = QgsVectorLayer(path, "MW_FL_PARCEL_FLOODED", "ogr")
    assert layer.isValid()
    land_use = layer.fields().indexOf("LAND_USE_TYPE")
    layer.setEditorWidgetSetup(land_use, QgsEditorWidgetSetup("ValueMap", {"map": [
        {"RESIDENTIAL": "RESIDENTIAL"}, {"COMMERCIAL": "COMMERCIAL"},
        {"INDUSTRIAL": "INDUSTRIAL"}]}))
    QgsProject.instance().addMapLayer(layer)
    yield layer
    QgsProject.instance().removeMapLayer(layer.id())
    pump()


@pytest.fixture
def confirm_dialogs(monkeypatch):
    """Answer Yes to the plugin's confirmation dialogs."""
    yes = qenum(QMessageBox, 'StandardButton', 'Yes')
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: yes))
