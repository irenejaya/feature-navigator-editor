# -*- coding: utf-8 -*-
"""
Make the ready-made pictures of the help window (help/<page>.png).

Run from the repository root with a Python that can import qgis:

    python3 scripts/make_help_images.py

A demo copy of the panel is put in each mode with sample parcels, rendered
off-screen at 2x, and a red numbered marker is drawn on every widget listed
for that page in help_content.py (same order as the text). Re-run it whenever
the panel's buttons change. Nothing touches your own QGIS settings.
"""

import importlib.util
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

from qgis.PyQt.QtCore import QCoreApplication, QPoint, QRect, QRectF, QSettings, Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen, QPixmap  # noqa: E402
from qgis.core import QgsApplication  # noqa: E402

APP = QgsApplication([], True)
APP.initQgis()


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "featurenaved", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
    module = importlib.util.module_from_spec(spec)
    sys.modules["featurenaved"] = module
    spec.loader.exec_module(module)


_load_plugin()

from featurenaved.compat import qenum  # noqa: E402
from featurenaved.help_content import PAGES, resolve_target  # noqa: E402
from featurenaved.navigator_widget import FeatureNavEdDockWidget  # noqa: E402

_INI = qenum(QSettings, 'Format', 'IniFormat')
QCoreApplication.setOrganizationName("FeatureNavEdHelpImages")
QSettings.setDefaultFormat(_INI)
QSettings.setPath(_INI, qenum(QSettings, 'Scope', 'UserScope'), tempfile.mkdtemp())

from qgis.core import (  # noqa: E402
    QgsEditorWidgetSetup, QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer,
)
from qgis.gui import QgsGui, QgsMapCanvas, QgsMessageBar  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402

QgsGui.editorWidgetRegistry().initEditors(None)

# FEATURENAVED_HELP_OUT lets the tests render into a temporary folder.
OUT_DIR = os.environ.get("FEATURENAVED_HELP_OUT") or os.path.join(ROOT, "help")
PANEL_SIZE = (340, 600)
PAD = 16          # margin around the panel so markers can sit outside it
MARKER = QColor(220, 50, 47)
ALIGN_CENTER = qenum(Qt, 'AlignmentFlag', 'AlignCenter')
WA_DONT_SHOW = qenum(Qt, 'WidgetAttribute', 'WA_DontShowOnScreen')


def pump(times=10):
    for _ in range(times):
        QCoreApplication.processEvents()


class DemoIface:
    def __init__(self):
        self.main_window = QMainWindow()
        self.canvas = QgsMapCanvas()
        self.canvas.setAttribute(WA_DONT_SHOW, True)
        self.canvas.resize(300, 200)
        self.canvas.show()
        self.bar = QgsMessageBar()

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return self.bar

    def mainWindow(self):
        return self.main_window

    def vectorLayerTools(self):
        return None

    def activeLayer(self):
        return None


def demo_layer():
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:7855&field=PARCEL_ID:string&field=LAND_USE:string"
        "&field=AREA_HA:double&field=FLOOD_PCT:double&field=DEPTH_MAX:double",
        "Flooded parcels", "memory")
    uses = ["RESIDENTIAL", "RESIDENTIAL", "COMMERCIAL", "RESIDENTIAL", "INDUSTRIAL"]
    features = []
    for i in range(24):
        col, row = i % 6, i // 6
        x, y = 320000 + col * 60, 5810000 + row * 50
        feat = QgsFeature(layer.fields())
        feat.setAttributes([f"P-{1040 + i}", uses[i % len(uses)],
                            round(0.18 + (i * 7 % 11) * 0.02, 2),
                            float(i * 13 % 47), round((i * 3 % 9) * 0.1, 1)])
        feat.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} {y},{x + 50} {y},{x + 50} {y + 40},{x} {y + 40},{x} {y}))"))
        features.append(feat)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    land_use = layer.fields().indexOf("LAND_USE")
    layer.setEditorWidgetSetup(land_use, QgsEditorWidgetSetup("ValueMap", {"map": [
        {name: name} for name in ("RESIDENTIAL", "COMMERCIAL", "INDUSTRIAL")]}))
    symbol = layer.renderer().symbol()
    symbol.setColor(QColor(166, 206, 227))
    symbol.symbolLayer(0).setStrokeColor(QColor(90, 90, 90))
    QgsProject.instance().addMapLayer(layer)
    return layer


def widget_rect(dock, widget):
    return QRect(widget.mapTo(dock, QPoint(0, 0)), widget.size())


def draw_marker(painter, rect, number, bounds, outline):
    if outline:
        painter.setPen(QPen(MARKER, 1.5))
        painter.setBrush(Qt.NoBrush if hasattr(Qt, 'NoBrush') else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
    radius = 8
    if rect.left() < PAD + 24 and rect.width() < 40:
        cx, cy = rect.left() - radius - 2, rect.center().y()     # beside it
    else:
        cx, cy = rect.right() - 2, rect.top() + 2                  # top-right
    cx = min(max(cx, bounds.left() + radius + 1), bounds.right() - radius - 1)
    cy = min(max(cy, bounds.top() + radius + 1), bounds.bottom() - radius - 1)
    circle = QRect(cx - radius, cy - radius, 2 * radius, 2 * radius)
    painter.setPen(QPen(QColor("white"), 1.5))
    painter.setBrush(MARKER)
    painter.drawEllipse(circle)
    painter.setPen(QColor("white"))
    painter.drawText(circle, ALIGN_CENTER, str(number))


def render(dock, page, insets=None, crop_height=None):
    """Panel picture with numbered markers, plus optional insets on the right
    (``{name: (pixmap, rect_to_mark_within_inset)}``)."""
    panel = dock.grab()
    width, height = dock.width(), crop_height or dock.height()
    inset_width = max((p.width() / p.devicePixelRatio() for p, _ in (insets or {}).values()),
                      default=0)
    total_width = PAD + width + PAD + (int(inset_width) + PAD if insets else 0)
    total_height = PAD + height + PAD
    scale = panel.devicePixelRatio()
    out = QPixmap(int(total_width * scale), int(total_height * scale))
    out.setDevicePixelRatio(scale)
    out.fill(dock.palette().color(dock.backgroundRole()))
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing if hasattr(QPainter, 'Antialiasing')
                          else QPainter.RenderHint.Antialiasing)
    painter.drawPixmap(QPoint(PAD, PAD), panel,
                       QRect(0, 0, int(width * scale), int(height * scale)))
    painter.setPen(QPen(QColor(180, 180, 180), 1))
    painter.drawRect(QRect(PAD - 1, PAD - 1, width + 1, height + 1))

    inset_rects, y = {}, PAD
    for name, (pixmap, mark) in (insets or {}).items():
        size = pixmap.size() / pixmap.devicePixelRatio()
        target = QRect(PAD + width + PAD, y, size.width(), size.height())
        painter.drawPixmap(target.topLeft(), pixmap)
        painter.setPen(QPen(QColor(150, 150, 150), 1))
        painter.drawRect(target.adjusted(0, 0, -1, -1))
        inset_rects[name] = mark.translated(target.topLeft()) if mark else target
        y += size.height() + PAD

    font = QFont()
    font.setBold(True)
    font.setPointSize(7)
    painter.setFont(font)
    bounds = QRect(0, 0, total_width, total_height)
    for number, (target, _, _, _) in enumerate(page["items"], 1):
        if target.startswith("inset:"):
            rect = inset_rects[target[6:]]
            outline = target == "inset:map"
        else:
            widget = resolve_target(dock, target)
            if not widget.isVisible():
                raise RuntimeError(f"{page['id']}: {target} is not visible")
            rect = widget_rect(dock, widget).translated(PAD, PAD)
            outline = rect.width() > 70 or rect.height() > 40
        draw_marker(painter, rect, number, bounds, outline)
    painter.end()
    path = os.path.join(OUT_DIR, f"{page['id']}.png")
    out.save(path, "PNG")
    print("wrote", os.path.relpath(path, ROOT))


def grab_settings_menu(dock):
    container = dock.lock_nav_cb.parentWidget()
    container.adjustSize()
    pixmap = container.grab()
    mark = widget_rect(container, dock.lock_nav_cb)
    mark.setWidth(dock.lock_nav_cb.sizeHint().width())
    return pixmap, mark


def grab_map(iface, layer, fid):
    canvas = iface.canvas
    canvas.setDestinationCrs(layer.crs())
    canvas.setLayers([layer])
    bbox = layer.getFeature(fid).geometry().boundingBox()
    bbox.grow(70)
    canvas.setExtent(bbox)
    finished = []
    canvas.mapCanvasRefreshed.connect(lambda: finished.append(True))
    canvas.refresh()
    deadline = time.time() + 20
    while not finished and time.time() < deadline:
        pump(1)
        time.sleep(0.01)
    pump(20)
    if not finished:
        raise RuntimeError("map did not finish drawing")
    return canvas.grab(), None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pages = {page["id"]: page for page in PAGES}
    layer = demo_layer()
    iface = DemoIface()
    iface.canvas.setDestinationCrs(layer.crs())
    iface.canvas.setLayers([layer])
    iface.canvas.setExtent(layer.extent())
    iface.canvas.zoomScale(2500)
    dock = FeatureNavEdDockWidget(iface)
    dock.setAttribute(WA_DONT_SHOW, True)
    dock.resize(*PANEL_SIZE)
    dock.show()
    dock.show_measurements_cb.setChecked(True)      # optional; shown in the pictures
    dock.layer_combo.setLayer(layer)
    dock.sort_field_combo.setCurrentIndex(dock.sort_field_combo.findData("FLOOD_PCT"))
    for index in (0, 1, 2, 3, 4, 9, 10, 4):
        dock._go_to_index(index)
    pump()

    # Overview: the toolbar, layer and sort sections.
    render(dock, pages["overview"],
           crop_height=widget_rect(dock, dock._sort_group).bottom() + 10)

    # Browsing: the whole panel.
    render(dock, pages["browsing"])

    # Editing: a changed value (so Undo, Revert and Re-sort show up).
    layer.startEditing()
    pump()
    dock._go_to_index(5)
    layer.changeAttributeValue(dock._current_fid(), layer.fields().indexOf("FLOOD_PCT"), 12.0)
    pump()
    render(dock, pages["editing"])

    # Multi-edit: three features, one field changed.
    layer.selectByIds(dock.feature_ids[6:9])
    pump()
    land_use = next(r for r in dock._multi_panel._rows if r.field_name == "LAND_USE")
    land_use.widget.setCurrentIndex(land_use.widget.findText("INDUSTRIAL"))
    pump()
    render(dock, pages["multi_edit"])

    # Lock navigation: locked on one parcel while another is selected on the map.
    dock._multi_panel.reset()
    layer.rollBack()
    pump()
    dock.lock_nav_cb.setChecked(True)
    dock._go_to_index(4)
    locked = dock._current_fid()
    neighbour = next(f for f in dock.feature_ids if f not in (locked,)
                     and abs(f - locked) == 1)
    layer.selectByIds([neighbour])
    pump()
    render(dock, pages["lock"], insets={
        "settings": grab_settings_menu(dock),
        "map": grab_map(iface, layer, locked),
    })
    dock.cleanup()


if __name__ == "__main__":
    main()
    os._exit(0)     # skip Qt teardown at exit (see tests/conftest.py)
