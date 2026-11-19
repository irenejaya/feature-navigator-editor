# -*- coding: utf-8 -*-
"""
Review tracking: remember which features have been shown in the panel.

A feature counts as *visited* once it has been displayed. Visited ids are
stored per layer as a custom property (so they are saved with the project,
not in the data) using compact ranges such as ``"1-50,52,60-75"``.
"""

from qgis.PyQt.QtCore import QSize, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPalette
from qgis.PyQt.QtWidgets import QSizePolicy, QToolTip, QWidget
from qgis.core import QgsProject

from .compat import qenum

VISITED_PROPERTY = "featurenaved/visited"

_VISITED_COLOR = QColor(46, 160, 67)
_PARTLY_COLOR = QColor(46, 160, 67, 120)
_UNVISITED_COLOR = QColor(128, 128, 128, 70)


def encode_ids(ids):
    """``{1, 2, 3, 7}`` -> ``"1-3,7"``. Temporary (negative) ids are skipped:
    unsaved features get new ids when they are saved."""
    parts = []
    start = prev = None
    for fid in sorted(i for i in ids if i >= 0):
        if start is None:
            start = prev = fid
        elif fid == prev + 1:
            prev = fid
        else:
            parts.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = fid
    if start is not None:
        parts.append(f"{start}-{prev}" if prev != start else str(start))
    return ",".join(parts)


def decode_ids(text):
    ids = set()
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                first, last = part.split("-", 1)
                ids.update(range(int(first), int(last) + 1))
            else:
                ids.add(int(part))
        except ValueError:
            continue
    return ids


class VisitedStore:
    """Visited feature ids of one layer, persisted on that layer."""

    def __init__(self):
        self._layer = None
        self.ids = set()

    def load(self, layer):
        self._layer = layer
        self.ids = set()
        if layer is not None:
            self.ids = decode_ids(layer.customProperty(VISITED_PROPERTY, ""))

    def __contains__(self, fid):
        return fid in self.ids

    def mark(self, fid):
        """Mark ``fid`` visited. Returns True if it was not visited before."""
        if fid is None or fid in self.ids:
            return False
        self.ids.add(fid)
        if fid >= 0:
            self._save()
        return True

    def clear(self):
        self.ids = set()
        self._save()

    def _save(self):
        layer = self._layer
        if layer is None:
            return
        text = encode_ids(self.ids)
        if text:
            layer.setCustomProperty(VISITED_PROPERTY, text)
        else:
            layer.removeCustomProperty(VISITED_PROPERTY)
        QgsProject.instance().setDirty(True)


class ReviewStrip(QWidget):
    """Thin bar showing every feature of the list in order: green = visited,
    grey = not yet. A dark tick marks the current position. Click to jump."""

    jumpRequested = pyqtSignal(int)     # list index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flags = []
        self._prefix = [0]
        self._current = -1
        self.setFixedHeight(10)
        self.setSizePolicy(qenum(QSizePolicy, 'Policy', 'Expanding'),
                           qenum(QSizePolicy, 'Policy', 'Fixed'))
        self.setMouseTracking(True)
        self.setToolTip("Review progress: green = visited. Click to jump.")

    def sizeHint(self):
        return QSize(120, 10)

    def minimumSizeHint(self):
        return QSize(20, 10)

    def set_state(self, flags, current):
        self._flags = list(flags)
        prefix = [0]
        total = 0
        for flag in self._flags:
            total += 1 if flag else 0
            prefix.append(total)
        self._prefix = prefix
        self._current = current
        self.update()

    def index_at(self, x):
        n = len(self._flags)
        if n == 0:
            return -1
        return max(0, min(n - 1, int(x * n / max(self.width(), 1))))

    def paintEvent(self, event):
        painter = QPainter(self)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, _UNVISITED_COLOR)
        n = len(self._flags)
        if n:
            run_start, run_color = None, None
            for x in range(width + 1):
                color = None
                if x < width:
                    first = x * n // width
                    last = max(first + 1, (x + 1) * n // width)
                    visited = self._prefix[last] - self._prefix[first]
                    if visited == last - first:
                        color = _VISITED_COLOR
                    elif visited:
                        color = _PARTLY_COLOR
                if color is not run_color:
                    if run_color is not None:
                        painter.fillRect(run_start, 0, x - run_start, height, run_color)
                    run_start, run_color = x, color
            if 0 <= self._current < n:
                x = int((self._current + 0.5) * width / n)
                text = self.palette().color(qenum(QPalette, 'ColorRole', 'WindowText'))
                painter.fillRect(max(0, x - 1), 0, 2, height, text)
        painter.end()

    def _event_x(self, event):
        try:
            return int(event.position().x())
        except AttributeError:
            return event.pos().x()

    def mousePressEvent(self, event):
        index = self.index_at(self._event_x(event))
        if index >= 0:
            self.jumpRequested.emit(index)

    def mouseMoveEvent(self, event):
        index = self.index_at(self._event_x(event))
        if index >= 0:
            state = "visited" if self._flags[index] else "not visited"
            try:
                pos = event.globalPosition().toPoint()
            except AttributeError:
                pos = event.globalPos()
            QToolTip.showText(pos, f"Feature {index + 1}: {state}", self)
        super().mouseMoveEvent(event)
