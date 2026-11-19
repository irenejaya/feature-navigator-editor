# -*- coding: utf-8 -*-
"""Helpers shared by the tests."""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QLabel, QLineEdit
from qgis.gui import QgsEditorWidgetWrapper


def pump(times=5):
    """Let queued signals and deferred timers run."""
    for _ in range(times):
        QCoreApplication.processEvents()


def type_text(line_edit, text):
    """Replace the content of ``line_edit`` as if the user typed ``text``."""
    line_edit.setFocus()
    line_edit.selectAll()
    QTest.keyClicks(line_edit, text)
    pump()


def form_line_edit(form, layer, field_name):
    """Line edit of ``field_name`` inside a native QgsAttributeForm."""
    idx = layer.fields().indexOf(field_name)
    for wrapper in form.findChildren(QgsEditorWidgetWrapper):
        if wrapper.fieldIdx() == idx:
            widget = wrapper.widget()
            return widget if isinstance(widget, QLineEdit) else widget.findChild(QLineEdit)
    raise LookupError(field_name)


def visible_form_fields(dock):
    """Field labels currently visible in the single-feature form."""
    form = dock._feature_form
    return sorted(label.text() for label in form.findChildren(QLabel)
                  if label.isVisible() and label.buddy() is not None)


def values(layer, fids, field):
    return [layer.getFeature(fid)[field] for fid in fids]


def multi_row(dock, field_name):
    return next(r for r in dock._multi_panel._rows if r.field_name == field_name)


def set_sort(dock, field_name):
    dock.sort_field_combo.setCurrentIndex(dock.sort_field_combo.findData(field_name))
    pump()
