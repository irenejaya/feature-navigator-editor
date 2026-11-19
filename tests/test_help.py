# -*- coding: utf-8 -*-
"""Help button, help window, and the pictures staying in sync with the panel."""

import os
import subprocess
import sys

from qgis.PyQt.QtGui import QPixmap

from featurenaved.help_content import PAGES, resolve_target
from featurenaved.help_dialog import HelpDialog, picture_path, page_html
from helpers import pump

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _layout_holding(layout, widget):
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        found = _layout_holding(child, widget) if child is not None else None
        if found is not None:
            return found
    return None


def test_book_button_sits_next_to_settings(dock):
    toolbar = _layout_holding(dock._main_layout, dock.settings_btn)
    assert toolbar.indexOf(dock.help_btn) == toolbar.indexOf(dock.settings_btn) + 1
    assert not dock.help_btn.icon().isNull()


def test_button_opens_one_closeable_window(dock):
    dock.help_btn.click()
    pump()
    dialog = dock._help_dialog
    assert isinstance(dialog, HelpDialog) and dialog.isVisible()
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Overview", "Browsing", "Editing one feature", "Multi-edit", "Lock navigation"]
    dock.help_btn.click()                       # clicking again reuses it
    assert dock._help_dialog is dialog
    dialog.close()
    assert not dialog.isVisible()


def test_every_page_has_its_picture():
    for page in PAGES:
        pixmap = QPixmap(picture_path(page["id"]))
        assert not pixmap.isNull(), page["id"]
        assert pixmap.width() > 300


def test_text_lists_every_item_in_order():
    for page in PAGES:
        text = page_html(page)
        positions = [text.index(f"<b>{title}</b>") for _, _, title, _ in page["items"]]
        assert positions == sorted(positions), page["id"]


def test_plain_targets_exist_on_the_panel(dock, make_layer):
    dock.layer_combo.setLayer(make_layer(3))
    dock._go_first()
    pump()
    for page in PAGES:
        for target, _, _, _ in page["items"]:
            if ":" not in target:
                assert resolve_target(dock, target) is not None, target


def test_pictures_can_be_regenerated(tmp_path):
    """Renders every page off-screen; fails if a numbered item no longer
    matches a visible widget (e.g. a button was renamed or removed)."""
    env = dict(os.environ, FEATURENAVED_HELP_OUT=str(tmp_path))
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "make_help_images.py")],
        env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(os.listdir(tmp_path)) == sorted(f"{p['id']}.png" for p in PAGES)


def test_window_is_wide_enough_for_the_lock_page():
    """Regression: the Lock navigation picture was cut off at 900 px."""
    dialog = HelpDialog()
    try:
        lock = QPixmap(picture_path("lock"))
        picture_width = int(lock.width() / 2)
        width, _ = dialog.preferred_size()
        assert width >= picture_width + 300 + 60
        dialog.resize(width, 780)
        dialog.tabs.setCurrentIndex(4)
        dialog.show()
        pump()
        scroll = dialog.tabs.currentWidget()
        assert not scroll.horizontalScrollBar().isVisible()
        # never larger than the screen it opens on
        from qgis.PyQt.QtGui import QGuiApplication
        fresh = HelpDialog()
        available = QGuiApplication.primaryScreen().availableGeometry()
        assert fresh.width() <= available.width()
        fresh.close()
    finally:
        dialog.close()
