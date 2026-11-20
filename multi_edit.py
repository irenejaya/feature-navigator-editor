# -*- coding: utf-8 -*-
"""
Multi-edit panel: set shared attribute values on several features at once.

Unlike the native QGIS multi-edit form this never writes anything on its own:
values are applied only when :meth:`MultiEditPanel.apply` is called (the
*Apply* button). The native form applies pending values whenever the layer
selection changes, which is what made "Apply to selected" feel like auto-save.
"""

from functools import partial

from qgis.PyQt.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from qgis.core import QgsFeature, QgsFieldConstraints, QgsVectorLayerUtils
from qgis.gui import QgsAttributeEditorContext, QgsGui

from .compat import NULL, RICH_TEXT, SCROLL_OFF, SP_EXPANDING, qenum
from .utils import (
    ElidedLabel, fids_request, grey, is_null, make_scroll_area, values_equal,
)


class _Row:
    __slots__ = ('idx', 'name', 'field_name', 'checkbox', 'label', 'wrapper',
                 'widget', 'line_edit', 'placeholder', 'spin', 'special_text',
                 'editable', 'mixed', 'base_value', 'hidden')


class MultiEditPanel(QWidget):
    """Explicit-apply multi-edit form.

    Editing a field ticks its checkbox; un-ticking reverts it. Only ticked
    fields are written. Field names are elided and there is no horizontal
    scrollbar, so the dock can be made narrow without truncating the form.
    """

    EDITOR_MIN_WIDTH = 56

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._layer = None
        self._layer_id = None
        self._signature = None
        self._rows = []
        self._loading = False
        self._hidden_fields = set()
        self._field_order = []
        self.fids = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setTextFormat(RICH_TEXT)
        outer.addWidget(self._info)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 2, 0)
        body_layout.setSpacing(0)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(4)
        self._grid.setVerticalSpacing(3)
        self._grid.setColumnStretch(1, 0)
        self._grid.setColumnStretch(2, 1)
        body_layout.addLayout(self._grid)
        body_layout.addStretch(1)
        self._body = body
        self._scroll = make_scroll_area(body, SCROLL_OFF)
        outer.addWidget(self._scroll, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Discard the pending values")
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setToolTip(
            "Write the ticked fields to every selected feature.\n"
            "Changes go into the edit buffer — use Save Edits to keep them."
        )
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_btn)
        outer.addLayout(buttons)

        self.reset_btn.clicked.connect(self.reset)
        self._update_status()

    # ------------------------------------------------------------------ API

    def set_target(self, layer, fids, force_rebuild=False):
        """Show the panel for ``fids`` of ``layer``.

        Pending (ticked) values are kept when only the selection changes, so
        refining a selection never loses or applies what was typed.
        """
        signature = self._fields_signature(layer)
        if (force_rebuild or layer.id() != self._layer_id
                or signature != self._signature):
            self._build_rows(layer)
            self._signature = signature
        self._layer = layer
        self._layer_id = layer.id()
        self.fids = list(fids)
        self.refresh_baseline()

    def set_field_order(self, names):
        """Show rows in ``names`` order (empty = the layer's order)."""
        self._field_order = list(names)
        self._place_rows()

    def set_hidden_fields(self, names):
        """Hide rows for ``names`` (field names). A hidden field is never
        written, so a ticked row that gets hidden is reverted."""
        self._hidden_fields = set(names)
        self._apply_hidden()
        self._update_status()

    def refresh_baseline(self):
        """Recompute shared/mixed values; un-ticked rows show them again."""
        layer = self._layer
        if layer is None or not self._rows:
            self._update_status()
            return
        indices = [row.idx for row in self._rows]
        common, mixed, first = {}, set(), True
        request = fids_request(self.fids)
        request.setSubsetOfAttributes(indices)
        for feat in layer.getFeatures(request):
            attrs = feat.attributes()
            for idx in indices:
                if idx in mixed:
                    continue
                value = attrs[idx] if idx < len(attrs) else None
                if first:
                    common[idx] = value
                elif not values_equal(common.get(idx), value):
                    mixed.add(idx)
            first = False
        for row in self._rows:
            row.mixed = row.idx in mixed
            row.base_value = None if row.mixed else common.get(row.idx)
            tip = row.name
            if row.mixed:
                tip += "\nValues differ between the selected features"
            if not row.editable:
                tip += "\n" + (row.label.property('reason') or "Read-only")
            row.label.setToolTip(tip)
            if not row.checkbox.isChecked():
                self._show_baseline(row)
            self._style_row(row)
        self._update_status()

    def pending_count(self):
        return sum(1 for row in self._rows
                   if row.editable and not row.hidden and row.checkbox.isChecked())

    def reset(self):
        """Un-tick every field and show the current values again."""
        self._loading = True
        try:
            for row in self._rows:
                row.checkbox.setChecked(False)
        finally:
            self._loading = False
        for row in self._rows:
            self._show_baseline(row)
            self._style_row(row)
        self._update_status()

    def apply(self):
        """Write ticked fields to all target features as one undo step.

        Returns ``None`` when there is nothing to do, otherwise a tuple
        ``(ok, n_fields, n_features)``.
        """
        layer = self._layer
        if layer is None or not self.fids or not layer.isEditable():
            return None
        changes = {
            row.idx: self._row_value(row)
            for row in self._rows
            if row.editable and not row.hidden and row.checkbox.isChecked()
        }
        if not changes:
            return None

        request = fids_request(self.fids)
        request.setSubsetOfAttributes(list(changes))
        features = list(layer.getFeatures(request))

        n = len(self.fids)
        layer.beginEditCommand(f"FeatureNavEd: multi-edit {n} features")
        ok = True
        try:
            for feat in features:
                new_values, old_values = {}, {}
                for idx, value in changes.items():
                    current = feat.attribute(idx)
                    if not values_equal(current, value):
                        new_values[idx] = value
                        old_values[idx] = current
                if new_values and not self._change_values(
                        layer, feat.id(), new_values, old_values):
                    ok = False
        except Exception:
            ok = False
        if ok:
            layer.endEditCommand()
        else:
            layer.destroyEditCommand()
        layer.triggerRepaint()

        if ok:
            self.reset()
            self.refresh_baseline()
        return ok, len(changes), n

    def clear(self):
        """Drop all editor widgets (call before the layer goes away)."""
        self._destroy_rows()
        self._layer = None
        self._layer_id = None
        self._signature = None
        self.fids = []
        self._update_status()

    # ------------------------------------------------------------- building

    @staticmethod
    def _fields_signature(layer):
        sig = []
        fields = layer.fields()
        for idx in range(fields.count()):
            try:
                widget_type = layer.editorWidgetSetup(idx).type()
            except Exception:
                widget_type = ''
            sig.append((fields.at(idx).name(), fields.at(idx).typeName(), widget_type))
        return tuple(sig)

    @staticmethod
    def _editability(layer, idx, pk_indices):
        """Return (editable, reason) for multi-editing field ``idx``."""
        if idx in pk_indices:
            return False, "Primary key — can't be set on several features"
        if not QgsVectorLayerUtils.fieldIsEditable(layer, idx, QgsFeature(layer.fields())):
            return False, "Read-only field"
        unique = qenum(QgsFieldConstraints, 'Constraint', 'ConstraintUnique')
        if layer.fields().at(idx).constraints().constraints() & unique:
            return False, "Unique field — can't share one value"
        return True, ""

    def _build_rows(self, layer):
        self._destroy_rows()
        pk_indices = set(layer.primaryKeyAttributes())
        context = QgsAttributeEditorContext()
        tools = self.iface.vectorLayerTools()
        if tools is not None:
            context.setVectorLayerTools(tools)

        fields = layer.fields()
        for idx in range(fields.count()):
            if layer.editorWidgetSetup(idx).type() == 'Hidden':
                continue
            row = _Row()
            row.idx = idx
            row.field_name = fields.at(idx).name()
            row.name = layer.attributeDisplayName(idx)
            row.mixed = False
            row.hidden = False
            row.base_value = None
            row.editable, reason = self._editability(layer, idx, pk_indices)
            row.checkbox = QCheckBox(self._body)
            row.label = ElidedLabel(row.name, self._body)
            row.label.setProperty('reason', reason)
            row.wrapper, row.widget = self._create_editor(layer, idx, context)
            if isinstance(row.widget, QLineEdit):
                row.line_edit = row.widget
            else:
                row.line_edit = row.widget.findChild(QLineEdit)
            row.placeholder = (
                row.line_edit.placeholderText() if row.line_edit is not None else ''
            )
            spin_types = (QSpinBox, QDoubleSpinBox)
            row.spin = (row.widget if isinstance(row.widget, spin_types)
                        else row.widget.findChild(spin_types))
            row.special_text = row.spin.specialValueText() if row.spin else ''
            if row.editable:
                row.checkbox.setToolTip(
                    "Ticked fields are written to every selected feature on Apply"
                )
                row.checkbox.toggled.connect(partial(self._on_checkbox_toggled, row))
                self._connect_value_signal(row)
            else:
                row.checkbox.setEnabled(False)
                row.checkbox.setToolTip(reason)
                self._set_editor_enabled(row, False)

            self._rows.append(row)
        self._place_rows()
        self._apply_hidden()

    def _place_rows(self):
        rank = {name: i for i, name in enumerate(self._field_order)}
        rows = sorted(self._rows, key=lambda r: (0, rank[r.field_name])
                      if r.field_name in rank else (1, r.idx))
        for row in self._rows:
            for widget in (row.checkbox, row.label, row.widget):
                self._grid.removeWidget(widget)
        for line, row in enumerate(rows):
            self._grid.addWidget(row.checkbox, line, 0)
            self._grid.addWidget(row.label, line, 1)
            self._grid.addWidget(row.widget, line, 2)
        self._rows = rows

    def _create_editor(self, layer, idx, context):
        wrapper, widget = None, None
        try:
            wrapper = QgsGui.editorWidgetRegistry().create(layer, idx, None, self._body)
            if wrapper is not None:
                wrapper.setContext(context)
                widget = wrapper.widget()
        except Exception:
            wrapper = None
        if widget is None:
            wrapper = None
            widget = QLineEdit(self._body)
            widget.setClearButtonEnabled(True)
        # Keep editors shrinkable: an explicit minimum overrides the (often
        # large) minimumSizeHint of compound editor widgets.
        widget.setMinimumWidth(self.EDITOR_MIN_WIDTH)
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(SP_EXPANDING)
        widget.setSizePolicy(policy)
        return wrapper, widget

    def _connect_value_signal(self, row):
        handler = partial(self._on_value_edited, row)
        if row.wrapper is not None:
            for name in ('valuesChanged', 'valueChanged'):
                signal = getattr(row.wrapper, name, None)
                if signal is None:
                    continue
                try:
                    signal.connect(handler)
                    return
                except TypeError:       # signature doesn't fit: try the next one
                    continue
        if row.line_edit is not None:
            row.line_edit.textEdited.connect(handler)

    def _destroy_rows(self):
        rows, self._rows = self._rows, []
        for row in rows:
            # Editor wrappers and their widgets are owned by C++ (parented to
            # the body), so deleteLater() is the safe way to dispose of them.
            if row.wrapper is not None:
                try:
                    row.wrapper.deleteLater()
                except RuntimeError:
                    pass
            for widget in (row.checkbox, row.label, row.widget):
                try:
                    widget.hide()
                    self._grid.removeWidget(widget)
                    widget.deleteLater()
                except RuntimeError:
                    pass

    def _apply_hidden(self):
        for row in self._rows:
            row.hidden = row.field_name in self._hidden_fields
            if row.hidden and row.checkbox.isChecked():
                self._loading = True
                try:
                    row.checkbox.setChecked(False)
                finally:
                    self._loading = False
                self._show_baseline(row)
                self._style_row(row)
            for widget in (row.checkbox, row.label, row.widget):
                widget.setVisible(not row.hidden)

    # ------------------------------------------------------------- behaviour

    def _on_value_edited(self, row, *args):
        if self._loading or not row.editable:
            return
        if not row.checkbox.isChecked():
            row.checkbox.blockSignals(True)
            row.checkbox.setChecked(True)
            row.checkbox.blockSignals(False)
            self._style_row(row)
        self._update_status()

    def _on_checkbox_toggled(self, row, checked):
        if self._loading:
            return
        if not checked:
            self._show_baseline(row)
        self._style_row(row)
        self._update_status()

    def _show_baseline(self, row):
        self._loading = True
        try:
            if row.mixed:
                if row.wrapper is not None:
                    try:
                        row.wrapper.showIndeterminateState()
                    except Exception:
                        self._set_wrapper_value(row.wrapper, None)
                if row.spin is not None:
                    # A spin box always renders a number (or "NULL"), which
                    # would read as a shared value. Show "Mixed values" via
                    # the special-value text Qt displays at the minimum.
                    row.spin.setSpecialValueText("Mixed values")
                    row.spin.setValue(row.spin.minimum())
                elif row.line_edit is not None:
                    row.line_edit.clear()
                    row.line_edit.setPlaceholderText("Mixed values")
            else:
                if row.spin is not None:
                    row.spin.setSpecialValueText(row.special_text)
                if row.line_edit is not None:
                    row.line_edit.setPlaceholderText(row.placeholder)
                if row.wrapper is not None:
                    self._set_wrapper_value(row.wrapper, row.base_value)
                elif row.line_edit is not None:
                    row.line_edit.setText(
                        '' if is_null(row.base_value) else str(row.base_value)
                    )
        finally:
            self._loading = False

    def _style_row(self, row):
        checked = row.editable and row.checkbox.isChecked()
        font = row.label.font()
        font.setBold(checked)
        font.setItalic(row.mixed and not checked)
        row.label.setFont(font)

    def _update_status(self):
        n = len(self.fids)
        pending = self.pending_count()
        head = f"<b>Multi-edit · {n} features</b>"
        if pending:
            plural = "s" if pending != 1 else ""
            tail = f"{pending} field{plural} will change when you click Apply."
        else:
            tail = grey("Edit a field to include it. Nothing changes until you click Apply.")
        self._info.setText(f"{head}<br>{tail}")
        self.apply_btn.setToolTip(
            f"Write the ticked fields to all {n} selected features.\n"
            "Changes go into the edit buffer \u2014 use Save Edits to keep them.")
        self.apply_btn.setEnabled(pending > 0)
        self.reset_btn.setEnabled(pending > 0)

    @staticmethod
    def _set_editor_enabled(row, enabled):
        if row.wrapper is not None:
            row.wrapper.setEnabled(enabled)
        else:
            row.widget.setEnabled(enabled)

    @staticmethod
    def _set_wrapper_value(wrapper, value):
        if is_null(value):
            value = NULL
        try:
            wrapper.setValues(value, [])
        except (AttributeError, TypeError):
            wrapper.setValue(value)

    def _row_value(self, row):
        if row.wrapper is not None:
            return row.wrapper.value()
        text = row.line_edit.text() if row.line_edit is not None else ''
        if not text:
            return NULL
        field = self._layer.fields().at(row.idx)
        try:
            return field.convertCompatible(text)
        except Exception:
            return text

    @staticmethod
    def _change_values(layer, fid, new_values, old_values):
        try:
            return layer.changeAttributeValues(fid, new_values, old_values)
        except (AttributeError, TypeError):
            ok = True
            for idx, value in new_values.items():
                ok = layer.changeAttributeValue(
                    fid, idx, value, old_values.get(idx)) and ok
            return ok
