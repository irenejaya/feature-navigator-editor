# -*- coding: utf-8 -*-
"""Find bar: jump to features with a given field value (no filtering)."""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QComboBox, QCompleter, QHBoxLayout, QLineEdit, QWidget
from qgis.core import QgsFeatureRequest

from .compat import ADJUST_TO_MIN_CONTENTS, CASE_INSENSITIVE, MATCH_CONTAINS, NO_GEOMETRY
from .utils import is_null, tool_button


class FindBar(QWidget):
    """Field + value search. Emits :attr:`findRequested` (True = forward)."""

    findRequested = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.field_combo = QComboBox()
        self.field_combo.setToolTip("Field to search")
        self.field_combo.setMinimumContentsLength(6)
        self.field_combo.setSizeAdjustPolicy(ADJUST_TO_MIN_CONTENTS)
        layout.addWidget(self.field_combo)

        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("Value...")
        self.value_edit.setClearButtonEnabled(True)
        self.value_edit.setToolTip("Value to find (case-insensitive exact match)")
        self._completer = QCompleter([], self.value_edit)
        self._completer.setCaseSensitivity(CASE_INSENSITIVE)
        self._completer.setFilterMode(MATCH_CONTAINS)
        self.value_edit.setCompleter(self._completer)
        layout.addWidget(self.value_edit, 1)

        self.prev_btn = tool_button('/mActionArrowUp.svg', "Previous match")
        self.next_btn = tool_button('/mActionArrowDown.svg', "Next match (Enter)")
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.next_btn)

        self.prev_btn.clicked.connect(lambda *_: self.findRequested.emit(False))
        self.next_btn.clicked.connect(lambda *_: self.findRequested.emit(True))
        self.value_edit.returnPressed.connect(lambda: self.findRequested.emit(True))
        self.field_combo.currentIndexChanged.connect(self._populate_values)

    def set_layer(self, layer):
        self._layer = layer
        self.value_edit.clear()
        self.refresh_fields()

    def refresh_fields(self):
        """Re-read the layer's fields, keeping the chosen field if possible."""
        previous = self.field_combo.currentData()
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        if self._layer is not None:
            for field in self._layer.fields():
                self.field_combo.addItem(field.name(), field.name())
            idx = self.field_combo.findData(previous) if previous else -1
            if idx >= 0:
                self.field_combo.setCurrentIndex(idx)
        self.field_combo.blockSignals(False)
        self._populate_values()

    def focus_input(self):
        self.value_edit.setFocus()
        self.value_edit.selectAll()

    def query(self):
        return self.field_combo.currentData(), self.value_edit.text().strip()

    def matching_ids(self, layer):
        """Ids of features matching the query, or ``None`` without a query."""
        field_name, text = self.query()
        if not field_name or not text or layer is None:
            return None
        field_idx = layer.fields().indexOf(field_name)
        if field_idx < 0:
            return None
        try:
            number = float(text)
        except ValueError:
            number = None
        wanted = text.lower()
        request = QgsFeatureRequest().setSubsetOfAttributes([field_idx])
        request.setFlags(NO_GEOMETRY)
        matches = set()
        for feat in layer.getFeatures(request):
            value = feat.attribute(field_idx)
            if is_null(value):
                continue
            if str(value).lower() == wanted:
                matches.add(feat.id())
            elif (number is not None and isinstance(value, (int, float))
                  and not isinstance(value, bool) and float(value) == number):
                matches.add(feat.id())
        return matches

    def _populate_values(self, *args):
        field_name = self.field_combo.currentData()
        strings = []
        if field_name and self._layer is not None:
            idx = self._layer.fields().indexOf(field_name)
            if idx >= 0:
                strings = sorted({str(v) for v in self._layer.uniqueValues(idx)
                                  if not is_null(v)})
        self._completer.model().setStringList(strings)
