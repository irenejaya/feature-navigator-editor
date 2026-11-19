# -*- coding: utf-8 -*-
"""
FeatureNavEd - Dock Widget

Dockable panel for navigating and editing vector layer features one by one.

How the pieces fit together
---------------------------
* **Single feature** - the native ``QgsAttributeForm`` (SingleEditMode) is
  embedded in the panel. Edits go into the layer's *edit buffer* when you move
  to another feature, save, undo or stop editing - never straight to disk.
* **Multi-edit** - when 2+ features are selected on an editable layer the panel
  switches to :class:`_MultiEditPanel`, built from the layer's own editor
  widgets. Nothing is written until the user clicks *Apply*; changing the
  selection never applies pending values (the native multi-edit form does).
* **Lock navigation** - decouples the navigator from the map selection:
  clicking around the canvas no longer moves the navigator, the locked feature
  stays highlighted, and multi-edit is disabled.
* **List sync** - the navigation list follows the layer: features deleted
  (panel, Del key, attribute table), added, undone, committed or rolled back
  anywhere in QGIS are reflected without a manual reload.
"""

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:
    _xml_fromstring = None

import html
import os
from functools import partial

from qgis.PyQt.QtCore import Qt, QSize, QTimer
from qgis.PyQt.QtGui import QIcon, QKeySequence, QColor
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QCheckBox, QGroupBox, QToolButton,
    QPushButton, QLineEdit, QCompleter, QScrollArea, QFrame, QMenu,
    QWidgetAction, QMessageBox, QSizePolicy, QSpinBox, QDoubleSpinBox,
)

try:
    from qgis.PyQt.QtWidgets import QShortcut
except ImportError:  # Qt6 moved QShortcut to QtGui
    from qgis.PyQt.QtGui import QShortcut

from qgis.core import (
    Qgis, QgsApplication, QgsProject, QgsMapLayerProxyModel,
    QgsVectorLayer, QgsFeature, QgsFeatureRequest, QgsSettings,
    QgsVectorLayerUtils,
)
from qgis.gui import (
    QgsMapLayerComboBox, QgsAttributeForm, QgsAttributeEditorContext,
    QgsGui, QgsHighlight,
)

try:
    from qgis.core import NULL
except ImportError:
    NULL = None


# =============================================================================
# Qt5 / Qt6 compatibility helpers
# =============================================================================

def _qenum(owner, scope, name):
    """Return an enum member for PyQt5 (flat) and PyQt6 (scoped) alike."""
    scoped = getattr(owner, scope, None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(owner, name)


_AlignCenter = _qenum(Qt, 'AlignmentFlag', 'AlignCenter')
_RichText = _qenum(Qt, 'TextFormat', 'RichText')
_ElideRight = _qenum(Qt, 'TextElideMode', 'ElideRight')
_ScrollAsNeeded = _qenum(Qt, 'ScrollBarPolicy', 'ScrollBarAsNeeded')
_ScrollOff = _qenum(Qt, 'ScrollBarPolicy', 'ScrollBarAlwaysOff')
_AppShortcut = _qenum(Qt, 'ShortcutContext', 'ApplicationShortcut')
_CaseInsensitive = _qenum(Qt, 'CaseSensitivity', 'CaseInsensitive')
_MatchContains = _qenum(Qt, 'MatchFlag', 'MatchContains')
_ToolTipRole = _qenum(Qt, 'ItemDataRole', 'ToolTipRole')
_LinksByMouse = _qenum(Qt, 'TextInteractionFlag', 'LinksAccessibleByMouse')
_SP_Expanding = _qenum(QSizePolicy, 'Policy', 'Expanding')
_SP_Preferred = _qenum(QSizePolicy, 'Policy', 'Preferred')
_NoFrame = _qenum(QFrame, 'Shape', 'NoFrame')
_VLine = _qenum(QFrame, 'Shape', 'VLine')
_Sunken = _qenum(QFrame, 'Shadow', 'Sunken')
_InstantPopup = _qenum(QToolButton, 'ToolButtonPopupMode', 'InstantPopup')
_MB_Yes = _qenum(QMessageBox, 'StandardButton', 'Yes')
_MB_No = _qenum(QMessageBox, 'StandardButton', 'No')
_AllDockAreas = (
    _qenum(Qt, 'DockWidgetArea', 'LeftDockWidgetArea')
    | _qenum(Qt, 'DockWidgetArea', 'RightDockWidgetArea')
    | _qenum(Qt, 'DockWidgetArea', 'TopDockWidgetArea')
    | _qenum(Qt, 'DockWidgetArea', 'BottomDockWidgetArea')
)

try:
    _NO_GEOMETRY = Qgis.FeatureRequestFlag.NoGeometry
except AttributeError:
    _NO_GEOMETRY = QgsFeatureRequest.NoGeometry

_LOCK_HIGHLIGHT_COLOR = QColor(255, 128, 0)


# =============================================================================
# Small helpers
# =============================================================================

def _is_null(value):
    """True for None, QGIS NULL and null Qt values (e.g. an invalid QDate)."""
    if value is None:
        return True
    try:
        if NULL is not None and value == NULL:
            return True
    except Exception:
        pass
    is_null = getattr(value, 'isNull', None)
    if callable(is_null):
        try:
            return bool(is_null())
        except Exception:
            return False
    return False


def _values_equal(a, b):
    a_null, b_null = _is_null(a), _is_null(b)
    if a_null or b_null:
        return a_null and b_null
    try:
        return bool(a == b)
    except Exception:
        return str(a) == str(b)


def _feature_exists(layer, fid):
    """Cheap existence check that respects the layer's edit buffer."""
    request = QgsFeatureRequest().setFilterFid(fid)
    request.setNoAttributes()
    request.setFlags(_NO_GEOMETRY)
    for feat in layer.getFeatures(request):
        return feat.isValid()
    return False


def _fids_request(fids):
    """Feature request for a set of ids (QgsFeatureIds is a set or a list
    depending on the QGIS/sip version)."""
    request = QgsFeatureRequest()
    try:
        request.setFilterFids(set(fids))
    except TypeError:
        request.setFilterFids(list(fids))
    request.setFlags(_NO_GEOMETRY)
    return request


def _text_width(metrics, text):
    try:
        return metrics.horizontalAdvance(text)
    except AttributeError:
        return metrics.width(text)


def _grey(text):
    return f"<span style='color:gray'>{text}</span>"


def _form_mode(name):
    """Resolve a QgsAttributeEditorContext mode across QGIS/sip versions."""
    for src in (getattr(QgsAttributeEditorContext, 'Mode', None),
                QgsAttributeEditorContext,
                getattr(QgsAttributeForm, 'Mode', None),
                QgsAttributeForm):
        if src is not None and hasattr(src, name):
            return getattr(src, name)
    return None


def _make_scroll_area(widget, horizontal_policy):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(_NoFrame)
    scroll.setVerticalScrollBarPolicy(_ScrollAsNeeded)
    scroll.setHorizontalScrollBarPolicy(horizontal_policy)
    # Let the dock shrink below the form's preferred height/width.
    scroll.setSizePolicy(_SP_Expanding, _SP_Expanding)
    scroll.setMinimumSize(0, 0)
    scroll.setWidget(widget)
    return scroll


# =============================================================================
# Drag & drop target
# =============================================================================

class _DropWidget(QWidget):
    """Content widget that accepts layer drag-and-drop from the Layers panel."""

    def __init__(self, dock):
        super().__init__()
        self._dock = dock
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/qgis.layertreemodeldata'):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat('application/qgis.layertreemodeldata'):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        layer = self._dock._extract_layer_from_drop(event.mimeData())
        if layer:
            self._dock.layer_combo.setLayer(layer)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


# =============================================================================
# Multi-edit panel
# =============================================================================

class _ElidedLabel(QLabel):
    """Label that elides its text instead of forcing the panel wider."""

    MAX_HINT_WIDTH = 160

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setToolTip(text)
        self.setSizePolicy(_SP_Preferred, _SP_Preferred)
        self._refresh()

    def fullText(self):
        return self._full_text

    def sizeHint(self):
        width = _text_width(self.fontMetrics(), self._full_text) + 6
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
            self._full_text, _ElideRight, max(self.width() - 2, 0)
        )
        if elided != self.text():
            super().setText(elided)


class _MultiEditRow:
    __slots__ = ('idx', 'name', 'checkbox', 'label', 'wrapper', 'widget',
                 'line_edit', 'placeholder', 'spin', 'special_text',
                 'editable', 'mixed', 'base_value')


class _MultiEditPanel(QWidget):
    """Edit shared attribute values of several features at once.

    Unlike the native multi-edit form this never writes anything on its own:
    values are only applied when :meth:`apply` is called (the *Apply* button).
    Editing a field ticks its checkbox; un-ticking reverts it. The layout
    elides field names and has no horizontal scrollbar, so the dock can be
    made narrow without the form being truncated.
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
        self.fids = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setTextFormat(_RichText)
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
        self._scroll = _make_scroll_area(body, _ScrollOff)
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

    def refresh_baseline(self):
        """Recompute shared/mixed values; un-ticked rows show them again."""
        layer = self._layer
        if layer is None or not self._rows:
            self._update_status()
            return
        indices = [row.idx for row in self._rows]
        common, mixed, first = {}, set(), True
        request = _fids_request(self.fids)
        request.setSubsetOfAttributes(indices)
        for feat in layer.getFeatures(request):
            attrs = feat.attributes()
            for idx in indices:
                if idx in mixed:
                    continue
                value = attrs[idx] if idx < len(attrs) else None
                if first:
                    common[idx] = value
                elif not _values_equal(common.get(idx), value):
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
                   if row.editable and row.checkbox.isChecked())

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
            for row in self._rows if row.editable and row.checkbox.isChecked()
        }
        if not changes:
            return None

        request = _fids_request(self.fids)
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
                    if not _values_equal(current, value):
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
        try:
            if not QgsVectorLayerUtils.fieldIsEditable(
                    layer, idx, QgsFeature(layer.fields())):
                return False, "Read-only field"
        except Exception:
            try:
                if layer.editFormConfig().readOnly(idx):
                    return False, "Read-only field"
            except Exception:
                pass
        try:
            from qgis.core import QgsFieldConstraints
            unique = _qenum(QgsFieldConstraints, 'Constraint', 'ConstraintUnique')
            if layer.fields().at(idx).constraints().constraints() & unique:
                return False, "Unique field — can't share one value"
        except Exception:
            pass
        return True, ""

    def _build_rows(self, layer):
        self._destroy_rows()
        try:
            pk_indices = set(layer.primaryKeyAttributes())
        except Exception:
            pk_indices = set()
        context = QgsAttributeEditorContext()
        try:
            tools = self.iface.vectorLayerTools()
            if tools is not None:
                context.setVectorLayerTools(tools)
        except Exception:
            pass

        fields = layer.fields()
        for idx in range(fields.count()):
            try:
                if layer.editorWidgetSetup(idx).type() == 'Hidden':
                    continue
            except Exception:
                pass
            row = _MultiEditRow()
            row.idx = idx
            row.name = layer.attributeDisplayName(idx)
            row.mixed = False
            row.base_value = None
            row.editable, reason = self._editability(layer, idx, pk_indices)
            row.checkbox = QCheckBox(self._body)
            row.label = _ElidedLabel(row.name, self._body)
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

            line = len(self._rows)
            self._grid.addWidget(row.checkbox, line, 0)
            self._grid.addWidget(row.label, line, 1)
            self._grid.addWidget(row.widget, line, 2)
            self._rows.append(row)

    def _create_editor(self, layer, idx, context):
        wrapper, widget = None, None
        try:
            wrapper = QgsGui.editorWidgetRegistry().create(layer, idx, None, self._body)
            if wrapper is not None:
                try:
                    wrapper.setContext(context)
                except Exception:
                    pass
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
        policy.setHorizontalPolicy(_SP_Expanding)
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
                except Exception:
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
                        '' if _is_null(row.base_value) else str(row.base_value)
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
            tail = _grey("Edit a field to include it. Nothing changes until you click Apply.")
        self._info.setText(f"{head}<br>{tail}")
        self.apply_btn.setText(f"Apply to {n} features" if n else "Apply")
        self.apply_btn.setEnabled(pending > 0)
        self.reset_btn.setEnabled(pending > 0)

    @staticmethod
    def _set_editor_enabled(row, enabled):
        if row.wrapper is not None:
            try:
                row.wrapper.setEnabled(enabled)
                return
            except Exception:
                pass
        row.widget.setEnabled(enabled)

    @staticmethod
    def _set_wrapper_value(wrapper, value):
        if _is_null(value):
            value = NULL
        try:
            wrapper.setValues(value, [])
        except (AttributeError, TypeError):
            wrapper.setValue(value)

    def _row_value(self, row):
        if row.wrapper is not None:
            try:
                return row.wrapper.value()
            except Exception:
                pass
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


# =============================================================================
# Dock widget
# =============================================================================

class FeatureNavEdDockWidget(QDockWidget):
    """Dock widget for navigating and editing features in a vector layer."""

    _SETTINGS_PREFIX = "featurenaved"

    def __init__(self, iface, parent=None):
        super().__init__("FeatureNavEd", parent)
        self.iface = iface

        self._layer = None                 # QgsVectorLayer being navigated
        self._layer_id = None
        self._layer_connections = []       # [(bound signal, slot)]
        self.feature_ids = []              # sorted navigation list
        self.current_index = -1            # -1 = no active feature ("NA")
        self._last_fid = None              # where to resume from the NA state
        self._last_index = -1
        self.sort_ascending = True
        self._layer_positions = {}         # layer id -> last fid
        self._layer_sort_fields = {}       # layer id -> sort field name

        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None
        self._form_error = None
        self._form_saving = False          # True while we push form values

        self._multi_edit_mode = False      # 2+ selected on an editable layer
        self._passive_count = 0            # 2+ selected on a read-only layer
        self._lock_navigation = False
        self._highlight = None
        self._suppress_selection = False   # True while we select features

        # Structural layer changes (delete/add/commit/rollback) are coalesced
        # and processed once control returns to the event loop.
        self._pending_deleted = set()
        self._pending_full_reload = False
        self._structure_timer = QTimer(self)
        self._structure_timer.setSingleShot(True)
        self._structure_timer.setInterval(0)
        self._structure_timer.timeout.connect(self._process_structure_changes)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh_from_layer)

        self.setAllowedAreas(_AllDockAreas)
        self.setObjectName("FeatureNavEdDockWidget")
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._load_settings()
        self._update_display()

        # Tear the form down BEFORE the C++ layer is destroyed.
        project = QgsProject.instance()
        try:
            self._layer_removed_signal = project.layerWillBeRemoved[str]
        except (KeyError, TypeError):
            self._layer_removed_signal = project.layerWillBeRemoved
        self._layer_removed_signal.connect(self._on_layer_will_be_removed)

    # =========================================================================
    # DRAG AND DROP
    # =========================================================================

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/qgis.layertreemodeldata'):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat('application/qgis.layertreemodeldata'):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        layer = self._extract_layer_from_drop(event.mimeData())
        if layer:
            self.layer_combo.setLayer(layer)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _extract_layer_from_drop(self, mime_data):
        if _xml_fromstring is None:
            return None
        if not mime_data.hasFormat('application/qgis.layertreemodeldata'):
            return None
        data = bytes(mime_data.data('application/qgis.layertreemodeldata'))
        try:
            root = _xml_fromstring(data.decode('utf-8'))
            for elem in root.iter():
                layer_id = elem.get('id')
                if layer_id:
                    layer = QgsProject.instance().mapLayer(layer_id)
                    if isinstance(layer, QgsVectorLayer):
                        return layer
        except Exception:
            pass
        return None

    # =========================================================================
    # UI
    # =========================================================================

    @staticmethod
    def _tool_button(icon, tooltip, checkable=False):
        btn = QToolButton()
        btn.setIcon(icon if isinstance(icon, QIcon) else QgsApplication.getThemeIcon(icon))
        btn.setToolTip(tooltip)
        btn.setAutoRaise(True)
        btn.setCheckable(checkable)
        return btn

    def _build_ui(self):
        main_widget = _DropWidget(self)
        self._main_layout = QVBoxLayout(main_widget)
        self._main_layout.setContentsMargins(4, 4, 4, 4)
        self._main_layout.setSpacing(4)

        self._build_toolbar()
        self._build_search_bar()

        bold_group = (
            "QGroupBox { font-weight: bold; }"
            " QGroupBox * { font-weight: normal; }"
        )

        # --- Layer ---
        layer_group = QGroupBox("Layer")
        layer_group.setStyleSheet(bold_group)
        layer_layout = QHBoxLayout()
        layer_layout.setContentsMargins(4, 4, 4, 4)
        layer_layout.setSpacing(4)
        self.layer_combo = QgsMapLayerComboBox()
        try:
            self.layer_combo.setFilters(Qgis.LayerFilter.VectorLayer)
        except AttributeError:
            self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setCurrentIndex(0)
        self.layer_combo.setShowCrs(True)
        self.layer_combo.setMinimumContentsLength(10)
        try:
            self.layer_combo.setSizeAdjustPolicy(
                _qenum(QComboBox, 'SizeAdjustPolicy',
                       'AdjustToMinimumContentsLengthWithIcon'))
        except AttributeError:
            pass
        layer_layout.addWidget(self.layer_combo, 1)
        self.active_layer_btn = self._tool_button(
            '/mActionRefresh.svg', "Use active layer / reload")
        layer_layout.addWidget(self.active_layer_btn)
        layer_group.setLayout(layer_layout)
        self._main_layout.addWidget(layer_group)

        # --- Sort ---
        self._sort_group = QGroupBox("Sort By")
        self._sort_group.setStyleSheet(bold_group)
        sort_layout = QHBoxLayout()
        sort_layout.setContentsMargins(4, 4, 4, 4)
        sort_layout.setSpacing(4)
        self.sort_field_combo = QComboBox()
        self.sort_field_combo.setToolTip("Field to sort features by")
        self.sort_field_combo.setMinimumContentsLength(8)
        try:
            self.sort_field_combo.setSizeAdjustPolicy(
                _qenum(QComboBox, 'SizeAdjustPolicy',
                       'AdjustToMinimumContentsLengthWithIcon'))
        except AttributeError:
            pass
        sort_layout.addWidget(self.sort_field_combo, 1)
        self.sort_order_btn = self._tool_button(
            '/mActionArrowUp.svg', "Ascending — click to toggle")
        self.sort_order_btn.setIconSize(self.sort_order_btn.iconSize() * 1.2)
        sort_layout.addWidget(self.sort_order_btn)
        self._sort_group.setLayout(sort_layout)
        self._main_layout.addWidget(self._sort_group)

        # --- Navigate ---
        self._nav_group = QGroupBox("Navigate")
        self._nav_group.setStyleSheet(bold_group)
        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(4, 4, 4, 4)
        nav_layout.setSpacing(4)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(2)
        self.first_btn = self._tool_button(
            '/mActionDoubleArrowLeft.svg', "First feature (Alt+Home)")
        self.prev_btn = self._tool_button(
            '/mActionArrowLeft.svg', "Previous feature (Alt+Left / Alt+P)")
        nav_row.addWidget(self.first_btn)
        nav_row.addWidget(self.prev_btn)
        nav_row.addStretch()
        self.feature_spin = QSpinBox()
        self.feature_spin.setRange(0, 0)
        self.feature_spin.setAlignment(_AlignCenter)
        self.feature_spin.setToolTip("Type a feature number to jump to it")
        self.feature_spin.setKeyboardTracking(False)
        nav_row.addWidget(self.feature_spin)
        self._counter_label = QLabel("NA")
        self._counter_label.setAlignment(_AlignCenter)
        self._counter_label.setVisible(False)
        self._counter_label.setMinimumWidth(self.feature_spin.sizeHint().width())
        nav_row.addWidget(self._counter_label)
        self.feature_total_label = QLabel("/ 0")
        nav_row.addWidget(self.feature_total_label)
        nav_row.addStretch()
        self.next_btn = self._tool_button(
            '/mActionArrowRight.svg', "Next feature (Alt+Right / Alt+N)")
        self.last_btn = self._tool_button(
            '/mActionDoubleArrowRight.svg', "Last feature (Alt+End)")
        self.flash_btn = self._tool_button(
            '/mActionHighlightFeature.svg', "Flash feature again (and re-centre)")
        self.del_btn = self._tool_button(
            '/mActionDeleteSelected.svg',
            "Delete current feature (layer must be in edit mode)")
        for btn in (self.next_btn, self.last_btn, self.flash_btn, self.del_btn):
            nav_row.addWidget(btn)
        nav_layout.addLayout(nav_row)

        options_row = QHBoxLayout()
        self.auto_zoom_cb = QCheckBox("Auto-zoom")
        self.auto_zoom_cb.setChecked(True)
        self.auto_zoom_cb.setToolTip("Centre on feature when navigating")
        options_row.addWidget(self.auto_zoom_cb)
        self.auto_scale_cb = QCheckBox("Auto-scale")
        self.auto_scale_cb.setChecked(True)
        self.auto_scale_cb.setToolTip(
            "Use current map canvas scale (uncheck to set a custom scale)")
        options_row.addWidget(self.auto_scale_cb)
        options_row.addStretch()
        self._scale_label = QLabel("Scale:")
        options_row.addWidget(self._scale_label)
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(100, 1000000)
        self.scale_spin.setValue(1000)
        self.scale_spin.setSingleStep(500)
        self.scale_spin.setPrefix("1:")
        self.scale_spin.setToolTip("Map scale when zooming to features")
        # Don't let the "1:1000000" width hint dictate the dock's minimum width.
        self.scale_spin.setMinimumWidth(72)
        options_row.addWidget(self.scale_spin)
        nav_layout.addLayout(options_row)
        self._nav_group.setLayout(nav_layout)
        self._main_layout.addWidget(self._nav_group)
        self._update_scale_controls(self.auto_scale_cb.isChecked())

        # --- Content: placeholder / single form (inserted later) / multi-edit
        self._form_placeholder = QLabel()
        self._form_placeholder.setAlignment(_AlignCenter)
        self._form_placeholder.setWordWrap(True)
        self._form_placeholder.setTextFormat(_RichText)
        self._form_placeholder.setTextInteractionFlags(_LinksByMouse)
        self._form_placeholder.setOpenExternalLinks(False)
        self._form_placeholder.setMinimumWidth(0)
        self._main_layout.addWidget(self._form_placeholder, 1)

        self._multi_panel = _MultiEditPanel(self.iface)
        self._multi_panel.setVisible(False)
        self._main_layout.addWidget(self._multi_panel, 1)

        # --- Keyboard shortcuts (ApplicationShortcut: work anywhere in QGIS)
        self._shortcuts = []
        for keys, slot in (("Alt+Left", self._go_prev), ("Alt+P", self._go_prev),
                           ("Alt+Right", self._go_next), ("Alt+N", self._go_next),
                           ("Alt+Home", self._go_first), ("Alt+End", self._go_last)):
            shortcut = QShortcut(QKeySequence(keys), main_widget)
            shortcut.setContext(_AppShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

        self.setWidget(main_widget)

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(2)

        # Editing controls act on the navigated layer, whichever layer is
        # active in the Layers panel.
        self.toggle_edit_btn = self._tool_button(
            '/mActionToggleEditing.svg',
            "Toggle editing on the navigated layer", checkable=True)
        self.save_edits_btn = self._tool_button(
            '/mActionSaveEdits.svg', "Save edits of the navigated layer (stay in edit mode)")
        self.undo_btn = self._tool_button(
            '/mActionUndo.svg', "Undo last edit on the navigated layer")
        self.redo_btn = self._tool_button(
            '/mActionRedo.svg', "Redo on the navigated layer")
        for btn in (self.toggle_edit_btn, self.save_edits_btn,
                    self.undo_btn, self.redo_btn):
            row.addWidget(btn)

        separator = QFrame()
        separator.setFrameShape(_VLine)
        separator.setFrameShadow(_Sunken)
        row.addWidget(separator)

        try:
            import db_manager as _dbm
            qb_icon_path = os.path.join(os.path.dirname(_dbm.__file__), 'icons', 'sql.gif')
            qb_icon = (QIcon(qb_icon_path) if os.path.exists(qb_icon_path)
                       else QgsApplication.getThemeIcon('/mActionFilter2.svg'))
        except Exception:
            qb_icon = QgsApplication.getThemeIcon('/mActionFilter2.svg')
        self.query_builder_btn = self._tool_button(
            qb_icon,
            "Query Builder — filter the layer and navigate only matching "
            "features; click again to clear the filter",
            checkable=True)
        row.addWidget(self.query_builder_btn)

        self.search_btn = self._tool_button(
            '/search.svg', "Find feature by value (no filtering)", checkable=True)
        row.addWidget(self.search_btn)

        # --- Settings menu ---
        self.settings_btn = self._tool_button('/mActionOptions.svg', "Settings")
        self.settings_btn.setPopupMode(_InstantPopup)
        menu = QMenu(self.settings_btn)
        container = QWidget(menu)
        menu_layout = QVBoxLayout(container)
        menu_layout.setContentsMargins(8, 6, 8, 6)
        menu_layout.setSpacing(4)
        self.lock_nav_cb = QCheckBox("Lock navigation")
        self.lock_nav_cb.setToolTip(
            "Keep the navigator on its current feature when you click or select\n"
            "other features on the map. The locked feature is outlined in orange.\n"
            "Navigation buttons, Find and Delete still work.\n"
            "Multi-edit is disabled while navigation is locked."
        )
        menu_layout.addWidget(self.lock_nav_cb)
        self.enable_flash_cb = QCheckBox("Flash on navigation")
        self.enable_flash_cb.setToolTip(
            "Briefly flash the feature on the map when the form navigates to it.\n"
            "The Flash button still works on demand."
        )
        menu_layout.addWidget(self.enable_flash_cb)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        self.settings_btn.setMenu(menu)
        row.addWidget(self.settings_btn)

        row.addStretch()
        self._main_layout.addLayout(row)

    def _build_search_bar(self):
        self._search_bar = QWidget()
        layout = QHBoxLayout(self._search_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.search_field_combo = QComboBox()
        self.search_field_combo.setToolTip("Field to search")
        self.search_field_combo.setMinimumContentsLength(6)
        try:
            self.search_field_combo.setSizeAdjustPolicy(
                _qenum(QComboBox, 'SizeAdjustPolicy',
                       'AdjustToMinimumContentsLengthWithIcon'))
        except AttributeError:
            pass
        layout.addWidget(self.search_field_combo)
        self.search_value_edit = QLineEdit()
        self.search_value_edit.setPlaceholderText("Value...")
        self.search_value_edit.setClearButtonEnabled(True)
        self.search_value_edit.setToolTip("Value to find (case-insensitive exact match)")
        self._search_completer = QCompleter([], self.search_value_edit)
        self._search_completer.setCaseSensitivity(_CaseInsensitive)
        self._search_completer.setFilterMode(_MatchContains)
        self.search_value_edit.setCompleter(self._search_completer)
        layout.addWidget(self.search_value_edit, 1)
        self.search_prev_btn = self._tool_button('/mActionArrowUp.svg', "Previous match")
        self.search_next_btn = self._tool_button('/mActionArrowDown.svg', "Next match (Enter)")
        layout.addWidget(self.search_prev_btn)
        layout.addWidget(self.search_next_btn)
        self._search_bar.setVisible(False)
        self._main_layout.addWidget(self._search_bar)

    # =========================================================================
    # SIGNALS / CLEANUP
    # =========================================================================

    def _connect_signals(self):
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self.active_layer_btn.clicked.connect(self._use_active_layer)
        self.sort_field_combo.currentIndexChanged.connect(self._on_sort_field_changed)
        self.sort_order_btn.clicked.connect(self._toggle_sort_order)

        self.toggle_edit_btn.clicked.connect(self._toggle_editing)
        self.save_edits_btn.clicked.connect(self._save_edits)
        self.undo_btn.clicked.connect(self._undo)
        self.redo_btn.clicked.connect(self._redo)
        self.query_builder_btn.clicked.connect(self._toggle_query_builder)
        self.search_btn.toggled.connect(self._on_search_toggled)
        self.search_next_btn.clicked.connect(self._go_next_match)
        self.search_prev_btn.clicked.connect(self._go_prev_match)
        self.search_value_edit.returnPressed.connect(self._go_next_match)
        self.search_field_combo.currentIndexChanged.connect(self._populate_search_values)

        self.first_btn.clicked.connect(self._go_first)
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)
        self.last_btn.clicked.connect(self._go_last)
        self.feature_spin.valueChanged.connect(self._go_to_feature_number)
        self.flash_btn.clicked.connect(self._flash_current)
        self.del_btn.clicked.connect(self._delete_current_feature)
        self._form_placeholder.linkActivated.connect(self._on_placeholder_link)

        self.auto_scale_cb.toggled.connect(self._update_scale_controls)
        self.iface.mapCanvas().scaleChanged.connect(self._on_canvas_scale_changed)

        self.lock_nav_cb.toggled.connect(self._on_lock_navigation_toggled)
        self.auto_zoom_cb.toggled.connect(self._save_settings)
        self.auto_scale_cb.toggled.connect(self._save_settings)
        self.enable_flash_cb.toggled.connect(self._save_settings)
        self.scale_spin.valueChanged.connect(self._save_settings)

        self._multi_panel.apply_btn.clicked.connect(self._on_multi_edit_apply)

    def cleanup(self):
        """Disconnect from QGIS before the plugin is unloaded."""
        self._structure_timer.stop()
        self._refresh_timer.stop()
        self._clear_highlight()
        self._multi_edit_mode = False
        self._multi_panel.clear()
        self._accept_current_form()
        self._remove_current_form()
        self._disconnect_layer()
        self._layer = None
        self._layer_id = None
        for signal, slot in ((self._layer_removed_signal, self._on_layer_will_be_removed),
                             (self.iface.mapCanvas().scaleChanged, self._on_canvas_scale_changed)):
            try:
                signal.disconnect(slot)
            except Exception:
                pass

    # =========================================================================
    # LAYER HANDLING
    # =========================================================================

    def _use_active_layer(self):
        active = self.iface.activeLayer()
        if isinstance(active, QgsVectorLayer) and active is not self._layer:
            self.layer_combo.setLayer(active)
        else:
            self._reload_features()

    def _connect_layer(self, layer):
        slots = [
            ('selectionChanged', self._on_selection_changed),
            ('editingStarted', self._on_editing_state_changed),
            ('editingStopped', self._on_editing_state_changed),
            ('attributeValueChanged', self._on_attribute_value_changed),
            ('geometryChanged', self._on_geometry_changed),
            ('featuresDeleted', self._on_features_deleted),
            ('featureAdded', self._on_feature_added),
            ('afterRollBack', self._schedule_full_resync),
            ('afterCommitChanges', self._schedule_full_resync),
            ('subsetStringChanged', self._on_subset_changed),
            ('updatedFields', self._on_fields_updated),
            ('beforeCommitChanges', self._on_before_commit),
            ('beforeModifiedCheck', self._on_before_commit),
        ]
        for name, slot in slots:
            signal = getattr(layer, name, None)
            if signal is None:
                continue
            try:
                signal.connect(slot)
                self._layer_connections.append((signal, slot))
            except Exception:
                pass
        try:
            stack = layer.undoStack()
            for signal in (stack.canUndoChanged, stack.canRedoChanged):
                signal.connect(self._update_toolbar_state)
                self._layer_connections.append((signal, self._update_toolbar_state))
        except Exception:
            pass

    def _disconnect_layer(self):
        connections, self._layer_connections = self._layer_connections, []
        for signal, slot in connections:
            try:
                signal.disconnect(slot)
            except Exception:
                pass

    def _on_layer_changed(self, layer):
        layer = layer if isinstance(layer, QgsVectorLayer) else None
        if layer is not None and layer is self._layer:
            return
        if self._layer is not None:
            self._remember_position()
            self._leave_multi_edit()
            self._accept_current_form()
            self._disconnect_layer()

        self._structure_timer.stop()
        self._refresh_timer.stop()
        self._pending_deleted.clear()
        self._pending_full_reload = False
        self._remove_current_form()
        self._multi_panel.clear()
        self._clear_highlight()

        self._layer = layer
        self._layer_id = layer.id() if layer is not None else None
        self.feature_ids = []
        self.current_index = -1
        self._last_fid = None
        self._last_index = -1
        self._passive_count = 0
        self._form_error = None

        self._populate_field_combos(
            self._layer_sort_fields.get(self._layer_id) if layer else None)
        self.search_value_edit.clear()
        self._populate_search_values()
        if layer is not None:
            self._connect_layer(layer)
            self.feature_ids = self._query_feature_ids()
        self._restore_initial_position()

    def _remember_position(self):
        fid = self._current_fid()
        if fid is None:
            fid = self._last_fid
        if fid is not None and self._layer_id:
            self._layer_positions[self._layer_id] = fid

    def _restore_initial_position(self):
        """Pick the starting feature after a layer is loaded."""
        layer = self._layer
        if layer is None or not self.feature_ids:
            self._update_display()
            return
        # Follow an existing selection, unless navigation is locked.
        if not self._lock_navigation and layer.selectedFeatureCount() > 0:
            self._sync_with_selection()
            return
        saved = self._layer_positions.get(self._layer_id)
        idx = self._index_of(saved) if saved is not None else -1
        if idx >= 0:
            self.current_index = idx
            self._navigate_to_current()
            return
        self.current_index = -1
        self._update_display()

    def _populate_field_combos(self, sort_field=None):
        layer = self._layer
        self.sort_field_combo.blockSignals(True)
        self.search_field_combo.blockSignals(True)
        previous_search = self.search_field_combo.currentData()
        self.sort_field_combo.clear()
        self.search_field_combo.clear()
        if layer is not None:
            self.sort_field_combo.addItem("(Feature ID)", None)
            fields = layer.fields()
            for i, field in enumerate(fields):
                icon = None
                try:
                    icon = fields.iconForField(i)
                except Exception:
                    pass
                if icon is not None and not icon.isNull():
                    self.sort_field_combo.addItem(icon, field.name(), field.name())
                else:
                    self.sort_field_combo.addItem(field.name(), field.name())
                type_name = field.typeName() or ""
                self.sort_field_combo.setItemData(
                    self.sort_field_combo.count() - 1,
                    f"{field.name()} ({type_name})" if type_name else field.name(),
                    _ToolTipRole,
                )
                self.search_field_combo.addItem(field.name(), field.name())
            if sort_field:
                idx = self.sort_field_combo.findData(sort_field)
                if idx >= 0:
                    self.sort_field_combo.setCurrentIndex(idx)
            if previous_search:
                idx = self.search_field_combo.findData(previous_search)
                if idx >= 0:
                    self.search_field_combo.setCurrentIndex(idx)
        self.sort_field_combo.blockSignals(False)
        self.search_field_combo.blockSignals(False)

    def _on_layer_will_be_removed(self, layer_id):
        if not isinstance(layer_id, str):
            try:
                layer_id = layer_id.id()
            except Exception:
                return
        self._layer_positions.pop(layer_id, None)
        self._layer_sort_fields.pop(layer_id, None)
        if layer_id != self._layer_id:
            return
        # The layer is still alive here: drop everything that points at it.
        # Pending edits are not pushed — QGIS has already asked the user.
        self._structure_timer.stop()
        self._refresh_timer.stop()
        self._multi_edit_mode = False
        self._multi_panel.clear()
        self._clear_highlight()
        self._remove_current_form()
        self._disconnect_layer()
        self._layer = None
        self._layer_id = None
        self.feature_ids = []
        self.current_index = -1
        self._last_fid = None
        self._last_index = -1
        self._passive_count = 0
        self._pending_deleted.clear()
        self._pending_full_reload = False
        self._update_display()

    def _on_fields_updated(self, *args):
        layer = self._layer
        if layer is None:
            return
        sort_field = self.sort_field_combo.currentData()
        self._populate_field_combos(sort_field)
        self._populate_search_values()
        if self.sort_field_combo.currentData() != sort_field:
            self._reload_features()
        if self._multi_edit_mode:
            self._multi_panel.set_target(
                layer, layer.selectedFeatureIds(), force_rebuild=True)

    # =========================================================================
    # FEATURE LIST
    # =========================================================================

    def _current_fid(self):
        if 0 <= self.current_index < len(self.feature_ids):
            return self.feature_ids[self.current_index]
        return None

    def _index_of(self, fid):
        try:
            return self.feature_ids.index(fid)
        except ValueError:
            return -1

    def _anchor_index(self):
        """Position to resume from when no feature is active."""
        if not self.feature_ids:
            return -1
        if self._last_fid is not None:
            idx = self._index_of(self._last_fid)
            if idx >= 0:
                return idx
        if self._last_index >= 0:
            return min(self._last_index, len(self.feature_ids) - 1)
        return -1

    def _query_feature_ids(self):
        layer = self._layer
        if layer is None:
            return []
        sort_field = self.sort_field_combo.currentData()
        if sort_field and layer.fields().indexOf(sort_field) < 0:
            sort_field = None
        request = QgsFeatureRequest()
        if sort_field:
            request.setSubsetOfAttributes([sort_field], layer.fields())
        else:
            request.setNoAttributes()
        request.setFlags(_NO_GEOMETRY)

        values, nulls = [], []
        for feat in layer.getFeatures(request):
            fid = feat.id()
            if sort_field:
                value = feat[sort_field]
                if _is_null(value):
                    nulls.append(fid)
                else:
                    values.append((value, fid))
            else:
                values.append((fid, fid))
        reverse = not self.sort_ascending
        try:
            values.sort(reverse=reverse)
        except TypeError:
            values.sort(key=lambda e: (str(e[0]), e[1]), reverse=reverse)
        nulls.sort()
        # NULLs always go last, whatever the direction.
        return [fid for _, fid in values] + nulls

    def _reload_features(self, *args):
        """Rebuild the list (sort/filter change), keeping the current feature."""
        self._structure_timer.stop()
        self._pending_deleted.clear()
        self._pending_full_reload = False
        current = self._current_fid()
        self.feature_ids = self._query_feature_ids()
        idx = self._index_of(current) if current is not None else -1
        if idx >= 0:
            self.current_index = idx
        elif current is not None:
            # Filtered out: park in the NA state, ready to resume nearby.
            self._last_fid = current
            self._last_index = self.current_index
            self.current_index = -1
            self._clear_highlight()
        self._update_display()

    def _on_sort_field_changed(self, *args):
        if self._layer_id:
            self._layer_sort_fields[self._layer_id] = self.sort_field_combo.currentData()
        self._reload_features()

    def _toggle_sort_order(self):
        self.sort_ascending = not self.sort_ascending
        self._update_sort_button()
        self._save_settings()
        self._reload_features()

    def _update_sort_button(self):
        if self.sort_ascending:
            self.sort_order_btn.setIcon(QgsApplication.getThemeIcon('/mActionArrowUp.svg'))
            self.sort_order_btn.setToolTip("Ascending — click to toggle")
        else:
            self.sort_order_btn.setIcon(QgsApplication.getThemeIcon('/mActionArrowDown.svg'))
            self.sort_order_btn.setToolTip("Descending — click to toggle")

    # --- keeping the list in sync with the layer ---------------------------

    def _on_features_deleted(self, fids):
        self._pending_deleted.update(fids)
        self._structure_timer.start()

    def _on_feature_added(self, *args):
        self._pending_full_reload = True
        self._structure_timer.start()

    def _schedule_full_resync(self, *args):
        self._pending_full_reload = True
        self._structure_timer.start()

    def _flush_structure_changes(self):
        if self._structure_timer.isActive():
            self._structure_timer.stop()
            self._process_structure_changes()

    def _process_structure_changes(self):
        layer = self._layer
        deleted, self._pending_deleted = self._pending_deleted, set()
        full, self._pending_full_reload = self._pending_full_reload, False
        if layer is None:
            return

        current = self._current_fid()
        old_index = self.current_index
        if full:
            self.feature_ids = self._query_feature_ids()
        elif deleted:
            self.feature_ids = [f for f in self.feature_ids if f not in deleted]
        else:
            return

        if current is None:
            self._update_display()
        elif self._index_of(current) >= 0:
            self.current_index = self._index_of(current)
            self._update_display()
        elif current in deleted and self.feature_ids:
            # The feature on screen was deleted (panel button, Del key,
            # attribute table, ...): move on to the one that took its place.
            self.current_index = min(old_index, len(self.feature_ids) - 1)
            if self._multi_edit_mode:
                self._update_display()
            else:
                self._navigate_to_current(save_pending=False)
        else:
            # Gone for another reason (e.g. a new feature got a real id on
            # commit): don't jump to an unrelated feature.
            self._last_fid = None
            self._last_index = old_index
            self.current_index = -1
            self._clear_highlight()
            self._update_display()

        if full and self._multi_edit_mode:
            selected = layer.selectedFeatureIds()
            if len(selected) >= 2 and layer.isEditable():
                self._multi_panel.set_target(layer, selected)
            else:
                self._sync_with_selection()

    def _on_attribute_value_changed(self, fid, *args):
        if self._form_saving:
            return
        if self._multi_edit_mode:
            if fid in self._multi_panel.fids:
                self._refresh_timer.start()
        elif fid == self._current_fid():
            self._refresh_timer.start()

    def _refresh_from_layer(self):
        """Show external edits (undo, field calculator, table, ...) on screen."""
        layer = self._layer
        if layer is None:
            return
        if self._multi_edit_mode:
            self._multi_panel.refresh_baseline()
            return
        fid = self._current_fid()
        form = self._feature_form
        if fid is None or form is None or self._feature_form_layer_id != layer.id():
            return
        feat = layer.getFeature(fid)
        if not feat.isValid():
            return
        self._form_saving = True
        try:
            form.setFeature(feat)
        except Exception:
            pass
        finally:
            self._form_saving = False
        self._update_sort_value()

    def _on_geometry_changed(self, fid, *args):
        if fid == self._current_fid() and self._highlight is not None:
            self._update_highlight()

    def _on_subset_changed(self, *args):
        self._reload_features()

    # =========================================================================
    # NAVIGATION
    # =========================================================================

    def _go_to_index(self, idx):
        self._flush_structure_changes()
        if self._multi_edit_mode or not self.feature_ids:
            return
        idx = max(0, min(idx, len(self.feature_ids) - 1))
        if idx == self.current_index and self._passive_count < 2:
            return
        self.current_index = idx
        self._navigate_to_current()

    def _go_first(self):
        self._go_to_index(0)

    def _go_last(self):
        self._go_to_index(len(self.feature_ids) - 1)

    def _go_next(self):
        self._flush_structure_changes()
        if not self.feature_ids:
            return
        if self.current_index >= 0:
            if self.current_index + 1 < len(self.feature_ids):
                self._go_to_index(self.current_index + 1)
            return
        anchor = self._anchor_index()
        self._go_to_index(anchor + 1 if anchor >= 0 else 0)

    def _go_prev(self):
        self._flush_structure_changes()
        if not self.feature_ids:
            return
        if self.current_index >= 0:
            if self.current_index > 0:
                self._go_to_index(self.current_index - 1)
            return
        anchor = self._anchor_index()
        self._go_to_index(anchor - 1 if anchor > 0 else
                          (0 if anchor == 0 else len(self.feature_ids) - 1))

    def _go_to_feature_number(self, number):
        if number >= 1:
            self._go_to_index(number - 1)

    def _on_placeholder_link(self, link):
        if link == 'resume':
            anchor = self._anchor_index()
            if anchor >= 0:
                self._go_to_index(anchor)

    def _navigate_to_current(self, select=True, save_pending=True):
        """Show the feature at ``current_index``: select, zoom, form, highlight."""
        layer = self._layer
        fid = self._current_fid()
        if layer is None or fid is None:
            self._update_display()
            return
        feat = layer.getFeature(fid)
        if not feat.isValid():
            # Stale entry - drop it on the next sync.
            self._pending_deleted.add(fid)
            self._structure_timer.start()
            self._update_display()
            return

        self._last_fid = fid
        self._last_index = self.current_index
        if select:
            self._select_only(layer, fid)
            self._passive_count = 0
        if feat.hasGeometry():
            if self.auto_zoom_cb.isChecked():
                self._zoom_to_feature(feat, layer)
            if self.enable_flash_cb.isChecked():
                self._flash_geometry(feat, layer)
        self._show_feature_form(layer, feat, save_pending)
        self._update_highlight(feat)
        self._update_display()

    def _select_only(self, layer, fid):
        if list(layer.selectedFeatureIds()) == [fid]:
            return
        self._suppress_selection = True
        try:
            layer.selectByIds([fid])
        finally:
            self._suppress_selection = False

    def _zoom_to_feature(self, feature, layer):
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            return
        canvas = self.iface.mapCanvas()
        try:
            center = canvas.mapSettings().layerToMapCoordinates(
                layer, geom.centroid().asPoint())
        except Exception:
            return
        canvas.setCenter(center)
        if not self.auto_scale_cb.isChecked():
            canvas.zoomScale(self.scale_spin.value())
        canvas.refresh()

    def _flash_geometry(self, feature, layer):
        try:
            self.iface.mapCanvas().flashGeometries([feature.geometry()], layer.crs())
        except Exception:
            pass

    def _flash_current(self):
        layer = self._layer
        fid = self._current_fid()
        if layer is None or fid is None:
            return
        feat = layer.getFeature(fid)
        if not feat.isValid() or not feat.hasGeometry():
            return
        if self.auto_zoom_cb.isChecked():
            self._zoom_to_feature(feat, layer)
        self._flash_geometry(feat, layer)

    def _update_scale_controls(self, checked):
        self._scale_label.setEnabled(not checked)
        self.scale_spin.setEnabled(not checked)
        if checked:
            self._sync_scale_from_canvas()

    def _on_canvas_scale_changed(self, scale):
        if self.auto_scale_cb.isChecked():
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(round(scale))
            self.scale_spin.blockSignals(False)

    def _sync_scale_from_canvas(self):
        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(round(self.iface.mapCanvas().scale()))
        self.scale_spin.blockSignals(False)

    # =========================================================================
    # SELECTION SYNC  (and Lock navigation)
    # =========================================================================

    def _on_selection_changed(self, *args):
        if self._suppress_selection or self._lock_navigation:
            return
        self._sync_with_selection()

    def _sync_with_selection(self):
        """Follow the map selection: 1 → jump, 2+ → multi-edit, 0 → NA."""
        layer = self._layer
        if layer is None:
            return
        selected = list(layer.selectedFeatureIds())

        if len(selected) >= 2:
            if layer.isEditable():
                self._passive_count = 0
                self._enter_multi_edit_mode(layer, selected)
            else:
                self._leave_multi_edit()
                self._passive_count = len(selected)
                self._update_display()
            return

        self._passive_count = 0
        was_multi = self._multi_edit_mode
        self._leave_multi_edit()

        if not selected:
            current = self._current_fid()
            if current is not None and not _feature_exists(layer, current):
                # Deleted rather than deselected: the list sync moves on.
                self._pending_deleted.add(current)
                self._structure_timer.start()
                return
            self._enter_na_state()
            return

        target = selected[0]
        idx = self._index_of(target)
        if idx < 0:
            self._flush_structure_changes()
            idx = self._index_of(target)
        if idx < 0:
            self.feature_ids = self._query_feature_ids()
            idx = self._index_of(target)
            if idx < 0:          # e.g. hidden by the layer filter
                self._enter_na_state()
                return
        if idx == self.current_index and not was_multi:
            self._update_display()
            return
        self.current_index = idx
        self._navigate_to_current(select=False)

    def _enter_na_state(self):
        if self.current_index >= 0:
            self._accept_current_form()
            self._last_fid = self._current_fid()
            self._last_index = self.current_index
        self.current_index = -1
        self._clear_highlight()
        self._update_display()

    def _on_lock_navigation_toggled(self, checked):
        self._lock_navigation = bool(checked)
        self._save_settings()
        if self._lock_navigation:
            if self._multi_edit_mode:
                self._leave_multi_edit()
                if self._current_fid() is not None:
                    self._navigate_to_current(select=False)
            self._passive_count = 0
            self._update_highlight()
        else:
            self._clear_highlight()
        self._update_nav_title()
        self._update_display()

    def _update_nav_title(self):
        if self._lock_navigation:
            self._nav_group.setTitle("Navigate (locked)")
            self._nav_group.setToolTip(
                "Navigation is locked: map clicks and selections don't move the "
                "navigator.\nUnlock it from the Settings menu.")
        else:
            self._nav_group.setTitle("Navigate")
            self._nav_group.setToolTip("")

    # =========================================================================
    # LOCK HIGHLIGHT
    # =========================================================================

    def _update_highlight(self, feat=None):
        self._clear_highlight()
        layer = self._layer
        fid = self._current_fid()
        if (not self._lock_navigation or self._multi_edit_mode
                or layer is None or fid is None):
            return
        if feat is None or feat.id() != fid:
            feat = layer.getFeature(fid)
        if not feat.isValid() or not feat.hasGeometry():
            return
        try:
            highlight = QgsHighlight(self.iface.mapCanvas(), feat.geometry(), layer)
            highlight.setColor(_LOCK_HIGHLIGHT_COLOR)
            fill = QColor(_LOCK_HIGHLIGHT_COLOR)
            fill.setAlpha(40)
            highlight.setFillColor(fill)
            highlight.setWidth(3)
            highlight.show()
            self._highlight = highlight
        except Exception:
            self._highlight = None

    def _clear_highlight(self):
        highlight, self._highlight = self._highlight, None
        if highlight is not None:
            try:
                highlight.hide()
                self.iface.mapCanvas().scene().removeItem(highlight)
            except Exception:
                pass

    # =========================================================================
    # MULTI-EDIT  (2+ features selected on an editable layer)
    # =========================================================================

    def _enter_multi_edit_mode(self, layer, fids):
        if not self._multi_edit_mode:
            self._accept_current_form()
            self._multi_edit_mode = True
            self._clear_highlight()
        self._multi_panel.set_target(layer, fids)
        self._update_display()

    def _leave_multi_edit(self):
        """Close the multi-edit panel. Pending values are never applied."""
        if not self._multi_edit_mode:
            return
        pending = self._multi_panel.pending_count()
        self._multi_edit_mode = False
        self._multi_panel.reset()
        if pending:
            plural = "s" if pending != 1 else ""
            self._notify(
                f"Multi-edit closed — {pending} pending field change{plural} "
                "discarded (nothing was applied).")

    def _on_multi_edit_apply(self):
        if not self._multi_edit_mode or self._layer is None:
            return
        result = self._multi_panel.apply()
        if result is None:
            if self._layer is not None and not self._layer.isEditable():
                self._notify("The layer is not in edit mode.", "warning")
            return
        ok, n_fields, n_features = result
        if ok:
            plural = "s" if n_fields != 1 else ""
            self._notify(
                f"Updated {n_fields} field{plural} on {n_features} features. "
                "Use Save Edits to keep the changes.", "success")
        else:
            self._notify(
                "Some values could not be applied (check the field constraints). "
                "Nothing was changed.", "warning")

    # =========================================================================
    # EDITING TOOLBAR
    # =========================================================================

    def _layer_tools(self):
        try:
            return self.iface.vectorLayerTools()
        except Exception:
            return None

    def _toggle_editing(self, *args):
        layer = self._layer
        if layer is None:
            self._update_toolbar_state()
            return
        tools = self._layer_tools()
        if not layer.isEditable():
            if tools is not None:
                tools.startEditing(layer)
            else:
                layer.startEditing()
        else:
            self._accept_current_form()
            if tools is not None:
                tools.stopEditing(layer, True)   # asks Save / Discard / Cancel
            elif layer.isModified():
                layer.commitChanges()
            else:
                layer.rollBack()
        self._update_toolbar_state()

    def _save_edits(self):
        layer = self._layer
        if layer is None or not layer.isEditable():
            return
        self._accept_current_form()
        tools = self._layer_tools()
        if tools is not None:
            tools.saveEdits(layer)
        else:
            try:
                layer.commitChanges(False)
            except TypeError:
                layer.commitChanges()
                layer.startEditing()
        layer.triggerRepaint()
        self._update_toolbar_state()

    def _undo(self):
        layer = self._layer
        if layer is None or not layer.isEditable():
            return
        self._accept_current_form()   # so a just-typed value is what gets undone
        layer.undoStack().undo()
        layer.triggerRepaint()

    def _redo(self):
        layer = self._layer
        if layer is None or not layer.isEditable():
            return
        layer.undoStack().redo()
        layer.triggerRepaint()

    def _on_editing_state_changed(self, *args):
        self._update_toolbar_state()
        layer = self._layer
        if layer is None:
            return
        if not self._lock_navigation and (
                self._multi_edit_mode or layer.selectedFeatureCount() >= 2):
            self._sync_with_selection()
        else:
            self._update_display()

    def _on_before_commit(self, *args):
        # Make sure values still sitting in the form widgets are part of the
        # save (also when the user saves from the main QGIS toolbar).
        self._accept_current_form()

    def _update_toolbar_state(self, *args):
        layer = self._layer
        valid = layer is not None
        editable = valid and layer.isEditable()
        try:
            can_edit = valid and layer.supportsEditing()
        except Exception:
            can_edit = valid
        self.toggle_edit_btn.setEnabled(can_edit)
        self.toggle_edit_btn.blockSignals(True)
        self.toggle_edit_btn.setChecked(editable)
        self.toggle_edit_btn.blockSignals(False)
        self.save_edits_btn.setEnabled(editable)
        can_undo = can_redo = False
        if editable:
            try:
                stack = layer.undoStack()
                can_undo, can_redo = stack.canUndo(), stack.canRedo()
            except Exception:
                pass
        self.undo_btn.setEnabled(can_undo)
        self.redo_btn.setEnabled(can_redo)

        self.query_builder_btn.setEnabled(valid)
        self.query_builder_btn.blockSignals(True)
        self.query_builder_btn.setChecked(valid and bool(layer.subsetString()))
        self.query_builder_btn.blockSignals(False)
        self.search_btn.setEnabled(valid)

        has_feature = self._current_fid() is not None and not self._multi_edit_mode
        self.flash_btn.setEnabled(has_feature)
        self.del_btn.setEnabled(editable and has_feature)

    # =========================================================================
    # QUERY BUILDER
    # =========================================================================

    def _toggle_query_builder(self, *args):
        """Checked = the layer has a filter. Click to set it, click again to clear."""
        layer = self._layer
        if layer is None:
            self._update_toolbar_state()
            return
        if layer.isEditable():
            self._notify("Stop editing the layer before changing its filter.", "warning")
            self._update_toolbar_state()
            return
        if layer.subsetString():
            layer.setSubsetString('')
        else:
            try:
                from qgis.gui import QgsQueryBuilder
            except ImportError:
                self._notify("QgsQueryBuilder is not available in this QGIS version.",
                             "warning")
                self._update_toolbar_state()
                return
            dlg = QgsQueryBuilder(layer, self.iface.mainWindow())
            dlg.setSql(layer.subsetString())
            (getattr(dlg, 'exec', None) or dlg.exec_)()
        self._update_toolbar_state()

    # =========================================================================
    # FIND
    # =========================================================================

    def _on_search_toggled(self, checked):
        self._search_bar.setVisible(checked)
        if checked:
            self.search_value_edit.setFocus()
            self.search_value_edit.selectAll()

    def _populate_search_values(self, *args):
        field_name = self.search_field_combo.currentData()
        layer = self._layer
        strings = []
        if field_name and layer is not None:
            idx = layer.fields().indexOf(field_name)
            if idx >= 0:
                strings = sorted({str(v) for v in layer.uniqueValues(idx)
                                  if not _is_null(v)})
        self._search_completer.model().setStringList(strings)

    def _go_next_match(self):
        self._find_match(forward=True)

    def _go_prev_match(self):
        self._find_match(forward=False)

    def _find_match(self, forward=True):
        field_name = self.search_field_combo.currentData()
        text = self.search_value_edit.text().strip()
        layer = self._layer
        if not field_name or not text or layer is None:
            return
        self._flush_structure_changes()
        if self._multi_edit_mode or not self.feature_ids:
            return
        field_idx = layer.fields().indexOf(field_name)
        if field_idx < 0:
            return
        try:
            number = float(text)
        except ValueError:
            number = None
        wanted = text.lower()

        request = QgsFeatureRequest().setSubsetOfAttributes([field_idx])
        request.setFlags(_NO_GEOMETRY)
        matches = set()
        for feat in layer.getFeatures(request):
            value = feat.attribute(field_idx)
            if _is_null(value):
                continue
            if str(value).lower() == wanted:
                matches.add(feat.id())
            elif (number is not None and isinstance(value, (int, float))
                  and not isinstance(value, bool) and float(value) == number):
                matches.add(feat.id())
        if not matches:
            self._notify(f'No feature where {field_name} = "{text}".')
            return

        total = len(self.feature_ids)
        start = self.current_index
        if start < 0:
            start = -1 if forward else 0
        step = 1 if forward else -1
        for offset in range(1, total + 1):
            idx = (start + offset * step) % total
            if self.feature_ids[idx] in matches:
                self._go_to_index(idx)
                return

    # =========================================================================
    # DELETE
    # =========================================================================

    def _delete_current_feature(self):
        layer = self._layer
        fid = self._current_fid()
        if layer is None or fid is None or not layer.isEditable():
            return
        reply = QMessageBox.question(
            self,
            "Delete Feature",
            f"Delete feature {fid} from \"{layer.name()}\"?\n\n"
            "The change goes into the layer's edit buffer — Save Edits to "
            "keep it, or Undo to bring the feature back.",
            _MB_Yes | _MB_No,
            _MB_No,
        )
        if reply != _MB_Yes:
            return
        layer.beginEditCommand(f"FeatureNavEd: delete feature {fid}")
        if layer.deleteFeature(fid):
            layer.endEditCommand()
            layer.triggerRepaint()
            # Same path as a Del-key / attribute-table delete: the layer's
            # featuresDeleted signal moves the navigator on.
            self._pending_deleted.add(fid)
            self._flush_structure_changes()
        else:
            layer.destroyEditCommand()
            self._notify(f"Failed to delete feature {fid}.", "warning")

    # =========================================================================
    # NATIVE FEATURE FORM (single feature)
    # =========================================================================

    def _accept_current_form(self):
        """Push values still in the form widgets into the layer's edit buffer.

        Nothing is committed to disk; the layer still needs Save Edits.
        """
        form = self._feature_form
        layer = self._layer
        if form is None or layer is None or self._form_saving:
            return
        if not layer.isEditable():
            return
        try:
            fid = form.feature().id()
        except Exception:
            return
        if fid in self._pending_deleted or not _feature_exists(layer, fid):
            return   # never write into a feature that was just deleted
        self._form_saving = True
        try:
            form.save()
        except Exception:
            pass
        finally:
            self._form_saving = False

    def _remove_current_form(self):
        """Drop the cached form widget (does not save)."""
        container = self._feature_form_container or self._feature_form
        self._feature_form = None
        self._feature_form_container = None
        self._feature_form_layer_id = None
        if container is None:
            return
        try:
            self._main_layout.removeWidget(container)
            container.hide()
            container.setParent(None)
            container.deleteLater()
        except Exception:
            pass

    def _show_feature_form(self, layer, feature, save_pending=True):
        """Show ``feature`` in the embedded native attribute form.

        The form widget is reused for every feature of the same layer
        (``setFeature``), which is much faster than rebuilding wide forms.
        """
        self._form_error = None
        if self._feature_form is not None and self._feature_form_layer_id == layer.id():
            if save_pending:
                self._accept_current_form()
            try:
                self._form_saving = True
                self._feature_form.setFeature(feature)
                return
            except Exception:
                pass   # fall through to a full rebuild
            finally:
                self._form_saving = False

        if save_pending:
            self._accept_current_form()
        self._remove_current_form()
        try:
            context = QgsAttributeEditorContext()
            tools = self._layer_tools()
            if tools is not None:
                context.setVectorLayerTools(tools)
            form = QgsAttributeForm(layer, feature, context, self)
            mode = _form_mode('SingleEditMode')
            if mode is not None:
                try:
                    form.setMode(mode)
                except (TypeError, AttributeError):
                    pass
            scroll = _make_scroll_area(form, _ScrollAsNeeded)
            self._feature_form = form
            self._feature_form_container = scroll
            self._feature_form_layer_id = layer.id()
            insert_pos = self._main_layout.indexOf(self._multi_panel)
            self._main_layout.insertWidget(max(insert_pos, 0), scroll, 1)
        except Exception as e:
            self._feature_form = None
            self._feature_form_container = None
            self._feature_form_layer_id = None
            self._form_error = f"Cannot display feature form:\n{e}"

    # =========================================================================
    # DISPLAY STATE
    # =========================================================================

    def _update_display(self):
        total = len(self.feature_ids)
        idx = self.current_index
        multi = self._multi_edit_mode
        passive = not multi and self._passive_count >= 2

        self.feature_spin.blockSignals(True)
        try:
            if multi or passive:
                n = len(self._multi_panel.fids) if multi else self._passive_count
                self._show_counter_text(
                    f"M ({n})",
                    "Multi-edit: several features are selected" if multi else
                    f"{n} features selected — start editing to multi-edit them")
            elif total and idx < 0:
                self._show_counter_text("NA", "No feature is active")
            else:
                self._counter_label.setVisible(False)
                self.feature_spin.setVisible(True)
                if total:
                    self.feature_spin.setRange(1, total)
                    self.feature_spin.setValue(idx + 1)
                else:
                    self.feature_spin.setRange(0, 0)
                    self.feature_spin.setValue(0)
            self.feature_total_label.setText(f"/ {total}")
        finally:
            self.feature_spin.blockSignals(False)

        can_nav = total > 0 and not multi
        in_na = idx < 0
        self.first_btn.setEnabled(can_nav and (in_na or idx > 0))
        self.prev_btn.setEnabled(can_nav and (in_na or idx > 0))
        self.next_btn.setEnabled(can_nav and (in_na or idx < total - 1))
        self.last_btn.setEnabled(can_nav and (in_na or idx < total - 1))
        self.feature_spin.setEnabled(can_nav)

        self._update_content()
        self._update_sort_value()
        self._update_toolbar_state()

    def _show_counter_text(self, text, tooltip):
        self.feature_spin.setVisible(False)
        self._counter_label.setText(text)
        self._counter_label.setToolTip(tooltip)
        self._counter_label.setVisible(True)

    def _update_content(self):
        """Show exactly one of: multi-edit panel, feature form, placeholder."""
        if self._multi_edit_mode:
            mode = 'multi'
        elif (self._current_fid() is not None and self._feature_form is not None
              and self._form_error is None):
            mode = 'form'
        else:
            mode = 'placeholder'
            self._form_placeholder.setText(self._placeholder_text())
        self._multi_panel.setVisible(mode == 'multi')
        if self._feature_form_container is not None:
            self._feature_form_container.setVisible(mode == 'form')
        self._form_placeholder.setVisible(mode == 'placeholder')

    def _placeholder_text(self):
        if self._form_error:
            return html.escape(self._form_error).replace("\n", "<br>")
        layer = self._layer
        if layer is None:
            return _grey("Choose a layer above, or drag one here from the Layers panel.")
        if not self.feature_ids:
            if layer.subsetString():
                return _grey("No features match the layer filter.")
            return _grey("This layer has no features.")
        anchor = self._anchor_index()
        if anchor >= 0:
            return (_grey("No feature selected.") + "<br><br>"
                    f"<a href='resume'>Return to feature {anchor + 1}</a>")
        if self._lock_navigation:
            return _grey("No feature selected.<br>Use the arrows or Find to start.")
        return _grey("No feature selected.<br>"
                     "Use the arrows, or select a feature on the map.")

    def _update_sort_value(self):
        sort_field = self.sort_field_combo.currentData()
        layer = self._layer
        fid = self._current_fid()
        title = "Sort By"
        if sort_field and layer is not None and fid is not None and not self._multi_edit_mode:
            request = QgsFeatureRequest().setFilterFid(fid)
            request.setSubsetOfAttributes([sort_field], layer.fields())
            request.setFlags(_NO_GEOMETRY)
            for feat in layer.getFeatures(request):
                value = feat[sort_field]
                shown = "NULL" if _is_null(value) else value
                title = f"Sort By — {sort_field}: {shown}"
        self._sort_group.setTitle(title)

    def _notify(self, text, level="info", duration=4):
        name = {'info': 'Info', 'success': 'Success', 'warning': 'Warning'}[level]
        try:
            qgis_level = getattr(Qgis.MessageLevel, name)
        except AttributeError:
            qgis_level = getattr(Qgis, name)
        try:
            self.iface.messageBar().pushMessage("FeatureNavEd", text, qgis_level, duration)
        except Exception:
            pass

    # =========================================================================
    # SETTINGS PERSISTENCE
    # =========================================================================

    def _save_settings(self, *args):
        try:
            s = QgsSettings()
            p = self._SETTINGS_PREFIX
            s.setValue(f"{p}/auto_zoom", bool(self.auto_zoom_cb.isChecked()))
            s.setValue(f"{p}/auto_scale", bool(self.auto_scale_cb.isChecked()))
            s.setValue(f"{p}/enable_flash", bool(self.enable_flash_cb.isChecked()))
            s.setValue(f"{p}/lock_navigation", bool(self.lock_nav_cb.isChecked()))
            s.setValue(f"{p}/scale", int(self.scale_spin.value()))
            s.setValue(f"{p}/sort_ascending", bool(self.sort_ascending))
        except Exception:
            pass

    def _load_settings(self):
        try:
            s = QgsSettings()
        except Exception:
            return

        def _b(key, default):
            v = s.value(f"{self._SETTINGS_PREFIX}/{key}", default)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.lower() in ("1", "true", "yes")
            return bool(v)

        def _i(key, default):
            try:
                return int(s.value(f"{self._SETTINGS_PREFIX}/{key}", default))
            except (TypeError, ValueError):
                return default

        for checkbox, key, default in ((self.auto_zoom_cb, "auto_zoom", True),
                                       (self.auto_scale_cb, "auto_scale", True),
                                       (self.enable_flash_cb, "enable_flash", False),
                                       (self.lock_nav_cb, "lock_navigation", False)):
            checkbox.blockSignals(True)
            checkbox.setChecked(_b(key, default))
            checkbox.blockSignals(False)
        self._lock_navigation = self.lock_nav_cb.isChecked()
        self._update_nav_title()
        self._update_scale_controls(self.auto_scale_cb.isChecked())

        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(_i("scale", self.scale_spin.value()))
        self.scale_spin.blockSignals(False)

        self.sort_ascending = _b("sort_ascending", True)
        self._update_sort_button()
