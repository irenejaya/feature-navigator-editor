# -*- coding: utf-8 -*-
"""Help window: one tab per mode, a ready-made picture with numbered markers
and the matching numbered explanations (text from :mod:`.help_content`)."""

import html
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QGuiApplication, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from .compat import NO_FRAME, RICH_TEXT, qenum
from .help_content import PAGES

HELP_DIR = os.path.join(os.path.dirname(__file__), "help")
PICTURE_SCALE = 2.0     # pictures are made at 2x so they stay sharp on HiDPI
TEXT_WIDTH = 380        # room for the numbered explanations next to a picture

_MARKER_COLOR = "#dc322f"


def picture_path(page_id):
    return os.path.join(HELP_DIR, f"{page_id}.png")


def page_html(page):
    """Intro, numbered explanations and notes of one page as rich text."""
    parts = [f"<p>{html.escape(page['intro'])}</p>",
             "<table cellspacing='0' cellpadding='3'>"]
    for number, (_, icon, title, text) in enumerate(page["items"], 1):
        icon_html = (f"<img src=':/images/themes/default{icon}' width='16' height='16'>&nbsp;"
                     if icon else "")
        parts.append(
            "<tr>"
            "<td valign='top' width='28'>"
            "<table cellspacing='0' cellpadding='1'><tr>"
            f"<td bgcolor='{_MARKER_COLOR}' align='center' width='20'>"
            f"<font color='#ffffff'><b>{number}</b></font></td></tr></table></td>"
            f"<td valign='top'>{icon_html}<b>{html.escape(title)}</b><br>"
            f"{html.escape(text)}</td>"
            "</tr>")
    parts.append("</table>")
    if page["notes"]:
        parts.append("<p><b>Good to know</b></p><ul>")
        parts.extend(f"<li>{html.escape(note)}</li>" for note in page["notes"])
        parts.append("</ul>")
    return "".join(parts)


class HelpDialog(QDialog):
    """Closeable help pop-up. Tabs: Overview, Browsing, Editing one feature,
    Multi-edit, Lock navigation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FeatureNavEd Help")
        self._widest_picture = 0

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for page in PAGES:
            self.tabs.addTab(self._build_page(page), page["title"])
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(qenum(QDialogButtonBox, 'StandardButton', 'Close'))
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        # Wide enough for the widest picture (Lock navigation, with the
        # Settings menu and map beside the panel) plus its text, but never
        # bigger than the screen.
        width, height = self.preferred_size()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, int(available.width() * 0.95))
            height = min(height, int(available.height() * 0.9))
        self.resize(width, height)

    def preferred_size(self):
        """Window size that shows every page without sideways scrolling."""
        # page margins + picture/text spacing + scrollbar, tab frame and
        # dialog margins
        chrome = 2 * 12 + 18 + 60
        return self._widest_picture + TEXT_WIDTH + chrome, 780

    def _build_page(self, page):
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(18)
        top = qenum(Qt, 'AlignmentFlag', 'AlignTop')

        pixmap = QPixmap(picture_path(page["id"]))
        if not pixmap.isNull():
            pixmap.setDevicePixelRatio(PICTURE_SCALE)
            self._widest_picture = max(self._widest_picture,
                                       int(pixmap.width() / PICTURE_SCALE))
            picture = QLabel()
            picture.setPixmap(pixmap)
            picture.setObjectName(f"help_picture_{page['id']}")
            row.addWidget(picture, 0, top)

        text = QLabel(page_html(page))
        text.setTextFormat(RICH_TEXT)
        text.setWordWrap(True)
        text.setAlignment(top)
        text.setMinimumWidth(300)
        text.setObjectName(f"help_text_{page['id']}")
        row.addWidget(text, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(NO_FRAME)
        scroll.setWidget(body)
        return scroll
