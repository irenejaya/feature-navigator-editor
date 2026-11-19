# -*- coding: utf-8 -*-
"""Frozen order: the list only re-sorts when the user asks."""

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

from helpers import pump, set_sort, values


def sorted_by_area(dock, layer, index=3):
    dock.layer_combo.setLayer(layer)
    set_sort(dock, "area")
    layer.startEditing()
    dock._go_to_index(index)
    pump()
    return list(dock.feature_ids)


def area_idx(layer):
    return layer.fields().indexOf("area")


def test_editing_a_sort_value_moves_nothing(dock, make_layer):
    layer = make_layer(8)
    before = sorted_by_area(dock, layer)
    layer.changeAttributeValue(before[1], area_idx(layer), 50.0)
    pump()
    assert dock.feature_ids == before
    assert dock.resort_btn.isVisible()          # order is now out of date


def test_resort_button_resorts_and_hides(dock, make_layer):
    layer = make_layer(8)
    before = sorted_by_area(dock, layer)
    current = dock._current_fid()
    layer.changeAttributeValue(before[1], area_idx(layer), 50.0)
    pump()
    dock.resort_btn.click()
    pump()
    assert dock.feature_ids[-1] == before[1]    # 50 is now the largest
    assert not dock.resort_btn.isVisible()
    assert dock._current_fid() == current       # still on the same feature


def test_undoing_a_sort_edit_clears_the_hint(dock, make_layer):
    layer = make_layer(8)
    before = sorted_by_area(dock, layer)
    layer.changeAttributeValue(before[1], area_idx(layer), 50.0)
    pump()
    dock._undo()
    pump()
    assert not dock.resort_btn.isVisible()


def test_undone_delete_returns_to_exactly_the_same_place(dock, make_layer):
    """Regression: undo used to re-sort everything with current values."""
    layer = make_layer(8)
    before = sorted_by_area(dock, layer)
    layer.changeAttributeValue(before[1], area_idx(layer), 50.0)   # edited
    victim = dock._current_fid()
    layer.deleteFeature(victim)
    pump()
    assert dock.feature_ids == [f for f in before if f != victim]
    dock._undo()            # undo the delete
    pump()
    assert dock.feature_ids == before


def test_new_feature_slots_into_sorted_position(dock, make_layer):
    layer = make_layer(8)                       # areas 0..7
    before = sorted_by_area(dock, layer)
    feat = QgsFeature(layer.fields())
    feat.setAttributes([2.5, "NEW", 1])
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(1, 1)))
    layer.addFeature(feat)
    pump()
    assert dock.feature_ids == before[:3] + [feat.id()] + before[3:]


def test_saving_and_rollback_keep_the_order(dock, make_layer):
    layer = make_layer(8)
    before = sorted_by_area(dock, layer)
    layer.changeAttributeValue(before[1], area_idx(layer), 50.0)
    dock._save_edits()
    pump()
    assert dock.feature_ids == before
    assert values(layer, [before[1]], "area") == [50.0]
    layer.startEditing()
    layer.changeAttributeValue(before[2], area_idx(layer), 60.0)
    layer.rollBack()
    pump()
    assert dock.feature_ids == before


def test_changing_the_sort_field_resorts(dock, make_layer):
    layer = make_layer(8)
    before = sorted_by_area(dock, layer)
    layer.changeAttributeValue(before[1], area_idx(layer), 50.0)
    pump()
    set_sort(dock, "area")          # same field again: no change event
    set_sort(dock, None)
    assert dock.feature_ids == sorted(before)           # by feature id
    assert not dock.resort_btn.isVisible()
