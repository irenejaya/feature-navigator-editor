# -*- coding: utf-8 -*-
"""Visited tracking: counter dot, overview strip, Next unvisited, saving."""

from featurenaved.review import VISITED_PROPERTY, decode_ids, encode_ids
from helpers import pump


def test_ids_round_trip_as_compact_ranges():
    ids = {1, 2, 3, 7, 9, 10, -4}
    assert encode_ids(ids) == "1-3,7,9-10"      # temporary ids are not stored
    assert decode_ids("1-3,7,9-10") == {1, 2, 3, 7, 9, 10}
    assert decode_ids("") == set()


def test_features_become_visited_when_shown(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    assert dock.visited_label.text() == "Visited 0 / 10"
    dock._go_next()
    dock._go_next()
    pump()
    assert dock.visited_label.text() == "Visited 2 / 10"
    assert dock.review_strip._flags[:3] == [True, True, False]
    assert dock.seen_dot.toolTip() == "First visit"
    dock._go_prev()
    pump()
    assert dock.seen_dot.toolTip() == "Seen before"


def test_clicking_the_strip_jumps(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock.review_strip.jumpRequested.emit(6)
    pump()
    assert dock.current_index == 6


def test_next_unvisited_skips_visited_and_wraps(dock, iface, make_layer):
    layer = make_layer(5)
    dock.layer_combo.setLayer(layer)
    for index in (0, 1, 3):
        dock._go_to_index(index)
    dock._go_to_index(0)
    dock._go_next_unvisited()
    assert dock.current_index == 2
    dock._go_next_unvisited()
    assert dock.current_index == 4
    assert not dock.next_unvisited_btn.isEnabled()      # all visited now
    dock._go_next_unvisited()
    assert any("All 5 features have been visited" in m for m in iface.messages)


def test_visited_marks_are_saved_on_the_layer(dock, iface, make_layer):
    from featurenaved.navigator_widget import FeatureNavEdDockWidget
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    for index in (0, 1, 2, 5):
        dock._go_to_index(index)
    fids = sorted(dock.feature_ids[i] for i in (0, 1, 2, 5))
    assert decode_ids(layer.customProperty(VISITED_PROPERTY)) == set(fids)

    second = FeatureNavEdDockWidget(iface)      # e.g. QGIS reopened
    try:
        second.layer_combo.setLayer(layer)
        pump()
        assert second.visited_label.text() == "Visited 4 / 10"
    finally:
        second.cleanup()


def test_clear_visited(dock, make_layer, confirm_dialogs):
    layer = make_layer(5)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(0)
    dock._go_to_index(1)
    dock._clear_visited()
    assert dock.visited_label.text() == "Visited 0 / 5"
    assert VISITED_PROPERTY not in layer.customPropertyKeys()


def test_tracking_can_be_switched_off(dock, make_layer):
    layer = make_layer(5)
    dock.layer_combo.setLayer(layer)
    dock.track_visited_cb.setChecked(False)
    dock._go_to_index(0)
    pump()
    assert not dock.review_strip.isVisible() and not dock.seen_dot.isVisible()
    assert dock._visited.ids == set()
