# -*- coding: utf-8 -*-
"""Small helpers shared by the FeatureNavEd widgets."""

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QLabel, QScrollArea, QToolButton
from qgis.core import QgsApplication, QgsFeatureRequest

from .compat import (
    ELIDE_RIGHT, NO_FRAME, NO_GEOMETRY, NULL, SCROLL_AS_NEEDED, SP_EXPANDING,
    SP_PREFERRED,
)


# --- attribute values --------------------------------------------------------

def is_null(value):
    """True for None, QGIS NULL and null Qt values (e.g. an invalid QDate)."""
    if value is None:
        return True
    try:
        if NULL is not None and value == NULL:
            return True
    except TypeError:       # some Qt types can't be compared with NULL
        pass
    is_null_method = getattr(value, 'isNull', None)
    if callable(is_null_method):
        try:
            return bool(is_null_method())
        except Exception:
            return False
    return False


def values_equal(a, b):
    a_null, b_null = is_null(a), is_null(b)
    if a_null or b_null:
        return a_null and b_null
    try:
        return bool(a == b)
    except Exception:
        return str(a) == str(b)


# --- feature requests --------------------------------------------------------

def feature_exists(layer, fid):
    """Cheap existence check that respects the layer's edit buffer."""
    request = QgsFeatureRequest().setFilterFid(fid)
    request.setNoAttributes()
    request.setFlags(NO_GEOMETRY)
    for feat in layer.getFeatures(request):
        return feat.isValid()
    return False


def fids_request(fids):
    """Geometry-less request for a set of ids (QgsFeatureIds is a set or a
    list depending on the QGIS/sip version)."""
    request = QgsFeatureRequest()
    try:
        request.setFilterFids(set(fids))
    except TypeError:
        request.setFilterFids(list(fids))
    request.setFlags(NO_GEOMETRY)
    return request


# --- widgets -----------------------------------------------------------------

def text_width(metrics, text):
    try:
        return metrics.horizontalAdvance(text)
    except AttributeError:
        return metrics.width(text)


def grey(text):
    return f"<span style='color:gray'>{text}</span>"


def tool_button(icon, tooltip, checkable=False):
    """Auto-raise tool button; ``icon`` is a QIcon or a QGIS theme icon path."""
    btn = QToolButton()
    btn.setIcon(icon if isinstance(icon, QIcon) else QgsApplication.getThemeIcon(icon))
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    btn.setCheckable(checkable)
    return btn


def make_scroll_area(widget, horizontal_policy):
    """Frameless, resizable scroll area that lets the dock shrink below the
    content's preferred size."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(NO_FRAME)
    scroll.setVerticalScrollBarPolicy(SCROLL_AS_NEEDED)
    scroll.setHorizontalScrollBarPolicy(horizontal_policy)
    scroll.setSizePolicy(SP_EXPANDING, SP_EXPANDING)
    scroll.setMinimumSize(0, 0)
    scroll.setWidget(widget)
    return scroll


class ElidedLabel(QLabel):
    """Label that elides its text instead of forcing its parent wider."""

    MAX_HINT_WIDTH = 160

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setToolTip(text)
        self.setSizePolicy(SP_PREFERRED, SP_PREFERRED)
        self._refresh()

    def fullText(self):
        return self._full_text

    def sizeHint(self):
        width = text_width(self.fontMetrics(), self._full_text) + 6
        return QSize(min(width, self.MAX_HINT_WIDTH), super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(24, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        self._refresh()  # font changes alter the elision

    def _refresh(self):
        elided = self.fontMetrics().elidedText(
            self._full_text, ELIDE_RIGHT, max(self.width() - 2, 0)
        )
        if elided != self.text():
            super().setText(elided)
