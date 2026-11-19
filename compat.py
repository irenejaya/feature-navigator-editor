# -*- coding: utf-8 -*-
"""
Qt5/Qt6 and QGIS-version compatibility shims.

PyQt5 exposes enum members on the class (``Qt.AlignCenter``) while PyQt6 only
has the scoped form (``Qt.AlignmentFlag.AlignCenter``). Everything the plugin
needs is resolved once here so the rest of the code can stay version-agnostic.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QComboBox, QFrame, QMessageBox, QSizePolicy, QToolButton
from qgis.core import Qgis, QgsFeatureRequest
from qgis.gui import QgsAttributeEditorContext, QgsAttributeForm

try:
    from qgis.PyQt.QtWidgets import QShortcut as _QShortcut  # Qt5
except ImportError:  # Qt6 moved QShortcut to QtGui
    from qgis.PyQt.QtGui import QShortcut as _QShortcut
QShortcut = _QShortcut

try:
    from qgis.core import NULL
except ImportError:
    NULL = None


def qenum(owner, scope, name):
    """Return an enum member for PyQt5 (flat) and PyQt6 (scoped) alike."""
    scoped = getattr(owner, scope, None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(owner, name)


ALIGN_CENTER = qenum(Qt, 'AlignmentFlag', 'AlignCenter')
RICH_TEXT = qenum(Qt, 'TextFormat', 'RichText')
ELIDE_RIGHT = qenum(Qt, 'TextElideMode', 'ElideRight')
SCROLL_AS_NEEDED = qenum(Qt, 'ScrollBarPolicy', 'ScrollBarAsNeeded')
SCROLL_OFF = qenum(Qt, 'ScrollBarPolicy', 'ScrollBarAlwaysOff')
APP_SHORTCUT = qenum(Qt, 'ShortcutContext', 'ApplicationShortcut')
CASE_INSENSITIVE = qenum(Qt, 'CaseSensitivity', 'CaseInsensitive')
MATCH_CONTAINS = qenum(Qt, 'MatchFlag', 'MatchContains')
TOOLTIP_ROLE = qenum(Qt, 'ItemDataRole', 'ToolTipRole')
USER_ROLE = qenum(Qt, 'ItemDataRole', 'UserRole')
CHECKED = qenum(Qt, 'CheckState', 'Checked')
UNCHECKED = qenum(Qt, 'CheckState', 'Unchecked')
LINKS_BY_MOUSE = qenum(Qt, 'TextInteractionFlag', 'LinksAccessibleByMouse')
SP_EXPANDING = qenum(QSizePolicy, 'Policy', 'Expanding')
SP_PREFERRED = qenum(QSizePolicy, 'Policy', 'Preferred')
NO_FRAME = qenum(QFrame, 'Shape', 'NoFrame')
VLINE = qenum(QFrame, 'Shape', 'VLine')
SUNKEN = qenum(QFrame, 'Shadow', 'Sunken')
INSTANT_POPUP = qenum(QToolButton, 'ToolButtonPopupMode', 'InstantPopup')
MB_YES = qenum(QMessageBox, 'StandardButton', 'Yes')
MB_NO = qenum(QMessageBox, 'StandardButton', 'No')
ADJUST_TO_MIN_CONTENTS = qenum(QComboBox, 'SizeAdjustPolicy',
                               'AdjustToMinimumContentsLengthWithIcon')
ALL_DOCK_AREAS = (
    qenum(Qt, 'DockWidgetArea', 'LeftDockWidgetArea')
    | qenum(Qt, 'DockWidgetArea', 'RightDockWidgetArea')
    | qenum(Qt, 'DockWidgetArea', 'TopDockWidgetArea')
    | qenum(Qt, 'DockWidgetArea', 'BottomDockWidgetArea')
)

try:
    NO_GEOMETRY = Qgis.FeatureRequestFlag.NoGeometry
except AttributeError:
    NO_GEOMETRY = QgsFeatureRequest.NoGeometry


def message_level(name):
    """Qgis message level by name: 'Info', 'Success', 'Warning', 'Critical'."""
    try:
        return getattr(Qgis.MessageLevel, name)
    except AttributeError:
        return getattr(Qgis, name)


def form_mode(name):
    """Resolve a QgsAttributeEditorContext mode across QGIS/sip versions."""
    for src in (getattr(QgsAttributeEditorContext, 'Mode', None),
                QgsAttributeEditorContext,
                getattr(QgsAttributeForm, 'Mode', None),
                QgsAttributeForm):
        if src is not None and hasattr(src, name):
            return getattr(src, name)
    return None
