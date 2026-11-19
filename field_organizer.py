# -*- coding: utf-8 -*-
"""
Field organizer: choose which fields FeatureNavEd shows.

Hiding a field here is a *view* setting of this panel only. The layer's own
attribute-form configuration is untouched, so the attribute table, identify
results and other forms still show every field. The choice is stored per
layer as a custom property, which means it is saved with the project.
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)
from qgis.core import QgsProject
from qgis.gui import QgsEditorWidgetWrapper

from .compat import CHECKED, UNCHECKED, USER_ROLE

HIDDEN_FIELDS_PROPERTY = "featurenaved/hidden_fields"

# Marks widgets *we* hid, so un-hiding never reveals widgets that QGIS keeps
# hidden on purpose (e.g. constraint icons of unconstrained fields).
_HIDDEN_BY_US = "featurenaved_hidden"


# =============================================================================
# Persistence
# =============================================================================

def load_hidden_fields(layer):
    """Names of the fields hidden in FeatureNavEd for ``layer``."""
    if layer is None:
        return set()
    value = layer.customProperty(HIDDEN_FIELDS_PROPERTY, [])
    if not value:
        return set()
    if isinstance(value, str):      # a one-item list can come back as a str
        return {value}
    return {str(v) for v in value}


def save_hidden_fields(layer, names):
    names = sorted(names)
    if names == sorted(load_hidden_fields(layer)):
        return
    if names:
        layer.setCustomProperty(HIDDEN_FIELDS_PROPERTY, names)
    else:
        layer.removeCustomProperty(HIDDEN_FIELDS_PROPERTY)
    QgsProject.instance().setDirty(True)


def organizable_fields(layer):
    """``[(name, display_name, icon)]`` for the fields the forms can show."""
    entries = []
    if layer is None:
        return entries
    fields = layer.fields()
    for idx in range(fields.count()):
        try:
            if layer.editorWidgetSetup(idx).type() == 'Hidden':
                continue    # never shown in a form anyway
        except Exception:
            pass
        try:
            icon = fields.iconForField(idx)
        except Exception:
            icon = None
        entries.append((fields.at(idx).name(), layer.attributeDisplayName(idx), icon))
    return entries


# =============================================================================
# Native attribute form
# =============================================================================

def apply_to_attribute_form(form, layer, hidden_names):
    """Show/hide fields of a native ``QgsAttributeForm``.

    Works with auto-generated and drag-and-drop layouts: for every editor
    widget it hides the field's container, its label (found through the
    label's buddy) and, in auto-generated layouts, its constraint icon.
    Safe to call repeatedly; with no hidden fields it only restores what it
    hid before.
    """
    fields = layer.fields()
    hidden = {fields.indexOf(name) for name in hidden_names}
    hidden.discard(-1)
    labels = form.findChildren(QLabel)
    for wrapper in form.findChildren(QgsEditorWidgetWrapper):
        try:
            editor = wrapper.widget()
            idx = wrapper.fieldIdx()
        except RuntimeError:
            continue
        if editor is None:
            continue
        container = _field_container(editor, form)
        widgets = [container]
        for label in labels:
            buddy = label.buddy()
            if buddy is not None and (buddy is editor or buddy is container
                                      or container.isAncestorOf(buddy)):
                widgets.append(label)
        widgets.extend(_constraint_icons(container))
        _set_visible(widgets, idx not in hidden)


def _field_container(editor, form):
    """The QgsAttributeFormEditorWidget wrapping ``editor`` (or the editor)."""
    parent = editor.parentWidget()
    while parent is not None and parent is not form:
        if parent.inherits("QgsAttributeFormEditorWidget"):
            return parent
        parent = parent.parentWidget()
    return editor


def _constraint_icons(container):
    """Constraint icon next to (or under, label-on-top) a field in the
    auto-generated layout's grid."""
    parent = container.parentWidget()
    layout = parent.layout() if parent is not None else None
    if not isinstance(layout, QGridLayout):
        return []
    index = layout.indexOf(container)
    if index < 0:
        return []
    row, col, row_span, _ = layout.getItemPosition(index)
    icons = []
    for r, c in ((row, col + 1), (row + row_span, col)):
        item = layout.itemAtPosition(r, c)
        widget = item.widget() if item is not None else None
        if widget is not None and widget.inherits("QSvgWidget"):
            icons.append(widget)
    return icons


def _set_visible(widgets, visible):
    for widget in widgets:
        if visible:
            if widget.property(_HIDDEN_BY_US):
                widget.setProperty(_HIDDEN_BY_US, False)
                widget.show()
        elif not widget.isHidden():
            widget.setProperty(_HIDDEN_BY_US, True)
            widget.hide()


# =============================================================================
# Organizer popup
# =============================================================================

class FieldOrganizer(QWidget):
    """Checkable field list with a filter box and Show all / Hide all.

    Changes apply immediately; :attr:`changed` is emitted after each one.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.setMinimumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        title = QLabel("<b>Fields shown in the panel</b>")
        layout.addWidget(title)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter fields…")
        self.filter_edit.setClearButtonEnabled(True)
        layout.addWidget(self.filter_edit)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.show_all_btn = QPushButton("Show all")
        self.hide_all_btn = QPushButton("Hide all")
        for btn in (self.show_all_btn, self.hide_all_btn):
            btn.setToolTip("Applies to the fields listed below (respects the filter)")
            buttons.addWidget(btn)
        buttons.addStretch(1)
        self.count_label = QLabel()
        buttons.addWidget(self.count_label)
        layout.addLayout(buttons)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(160)
        self.list_widget.setMaximumHeight(360)
        layout.addWidget(self.list_widget)

        hint = QLabel("Only affects this panel. Saved with the project.")
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.filter_edit.textChanged.connect(self._apply_filter)
        self.show_all_btn.clicked.connect(lambda *_: self._set_listed(True))
        self.hide_all_btn.clicked.connect(lambda *_: self._set_listed(False))
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self._update_count()

    def set_fields(self, entries, hidden_names):
        """Fill the list from ``organizable_fields()`` output."""
        self._loading = True
        try:
            self.list_widget.clear()
            for name, display, icon in entries:
                text = display if display == name else f"{display}  ({name})"
                item = QListWidgetItem(text)
                if icon is not None and not icon.isNull():
                    item.setIcon(icon)
                item.setData(USER_ROLE, name)
                item.setToolTip(name)
                item.setCheckState(UNCHECKED if name in hidden_names else CHECKED)
                self.list_widget.addItem(item)
        finally:
            self._loading = False
        self._apply_filter(self.filter_edit.text())
        self._update_count()

    def hidden_fields(self):
        return {self._item(i).data(USER_ROLE)
                for i in range(self.list_widget.count())
                if self._item(i).checkState() != CHECKED}

    def _item(self, row):
        return self.list_widget.item(row)

    def _apply_filter(self, text):
        needle = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self._item(i)
            self.list_widget.setRowHidden(i, bool(needle) and needle not in item.text().lower())

    def _set_listed(self, visible):
        state = CHECKED if visible else UNCHECKED
        self._loading = True
        try:
            for i in range(self.list_widget.count()):
                if not self.list_widget.isRowHidden(i):
                    self._item(i).setCheckState(state)
        finally:
            self._loading = False
        self._update_count()
        self.changed.emit()

    def _on_item_changed(self, *args):
        if self._loading:
            return
        self._update_count()
        self.changed.emit()

    def _update_count(self):
        total = self.list_widget.count()
        shown = total - len(self.hidden_fields())
        self.count_label.setText(f"{shown} / {total} shown")
