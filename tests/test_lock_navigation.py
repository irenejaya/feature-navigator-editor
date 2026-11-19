# -*- coding: utf-8 -*-
"""Lock navigation: the navigator ignores the map selection."""

from helpers import pump


def lock(dock):
    dock.lock_nav_cb.setChecked(True)
    pump()


def test_lock_ignores_map_clicks_and_outlines_feature(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(7)
    lock(dock)
    locked = dock._current_fid()
    assert dock._nav_group.title() == "Navigate (locked)"
    assert dock._highlight is not None

    layer.selectByIds([dock.feature_ids[1]])
    pump()
    assert dock._current_fid() == locked

    layer.removeSelection()
    pump()
    assert dock._current_fid() == locked
    assert dock._feature_form_container.isVisible()


def test_lock_disables_multi_edit(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(0)
    lock(dock)
    layer.startEditing()
    layer.selectByIds(dock.feature_ids[1:3])
    pump()
    assert not dock._multi_edit_mode
    assert dock._passive_count == 0


def test_locking_closes_open_multi_edit(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    layer.selectByIds(dock.feature_ids[:2])
    pump()
    assert dock._multi_edit_mode
    lock(dock)
    assert not dock._multi_edit_mode


def test_navigation_still_works_while_locked(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(2)
    lock(dock)
    start = dock._current_fid()
    dock._go_next()
    pump()
    assert dock._current_fid() != start
    assert list(layer.selectedFeatureIds()) == [dock._current_fid()]
    assert dock._highlight is not None


def test_unlock_follows_selection_again(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(2)
    lock(dock)
    dock.lock_nav_cb.setChecked(False)
    pump()
    assert dock._highlight is None
    assert dock._nav_group.title() == "Navigate"
    layer.selectByIds([dock.feature_ids[5]])
    pump()
    assert dock.current_index == 5
