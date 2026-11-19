# -*- coding: utf-8 -*-
"""Moving through features, following the map selection, sorting, Find."""

from qgis.core import QgsFeature

from helpers import pump, set_sort, values


def test_loads_layer_in_na_state(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    pump()
    assert len(dock.feature_ids) == 10
    assert dock.current_index == -1
    assert dock._counter_label.text() == "NA"


def test_sort_by_field_with_nulls_last(dock, make_layer):
    layer = make_layer(6)
    layer.dataProvider().changeAttributeValues({1: {0: None}})
    dock.layer_combo.setLayer(layer)
    set_sort(dock, "area")
    areas = values(layer, dock.feature_ids, "area")
    assert areas[:-1] == sorted(areas[:-1])
    assert dock.feature_ids[-1] == 1           # the NULL goes last ...
    dock.sort_order_btn.click()
    pump()
    assert dock.feature_ids[-1] == 1           # ... in both directions


def test_navigation_selects_the_current_feature(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_next()
    assert dock.current_index == 0
    dock._go_next()
    dock._go_next()
    pump()
    assert list(layer.selectedFeatureIds()) == [dock._current_fid()]
    assert dock.feature_spin.value() == 3
    dock._go_to_feature_number(7)
    assert dock.current_index == 6


def test_clearing_selection_offers_return_link(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(2)
    here = dock._current_fid()
    layer.removeSelection()          # click on an empty part of the map
    pump()
    assert dock.current_index == -1
    assert "Return to feature 3" in dock._form_placeholder.text()
    dock._on_placeholder_link("resume")
    assert dock._current_fid() == here


def test_next_from_na_continues_after_last_position(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(2)
    layer.removeSelection()
    pump()
    dock._go_next()
    assert dock.current_index == 3


def test_map_selection_moves_navigator(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    target = dock.feature_ids[7]
    layer.selectByIds([target])
    pump()
    assert dock._current_fid() == target
    assert dock._feature_form.feature().id() == target


def test_changing_sort_keeps_current_feature(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_to_index(4)
    current = dock._current_fid()
    set_sort(dock, "area")
    assert dock._current_fid() == current
    assert dock.feature_spin.value() == dock.current_index + 1


def test_find_is_case_insensitive_and_does_not_filter(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    bar = dock._find_bar
    bar.field_combo.setCurrentIndex(bar.field_combo.findData("land"))
    bar.value_edit.setText("res")
    dock._find_match(True)
    pump()
    assert layer.getFeature(dock._current_fid())["land"] == "RES"
    assert len(dock.feature_ids) == 10


def test_find_reports_no_match(dock, iface, make_layer):
    layer = make_layer(5)
    dock.layer_combo.setLayer(layer)
    bar = dock._find_bar
    bar.field_combo.setCurrentIndex(bar.field_combo.findData("land"))
    bar.value_edit.setText("nope")
    dock._find_match(True)
    assert any("No feature where land" in m for m in iface.messages)


def test_switching_layers_restores_position_and_sort(dock, make_layer):
    first = make_layer(10, "first")
    second = make_layer(5, "second")
    dock.layer_combo.setLayer(first)
    set_sort(dock, "area")
    dock._go_to_index(3)
    position = dock._current_fid()
    dock.layer_combo.setLayer(second)
    pump()
    assert len(dock.feature_ids) == 5
    first.removeSelection()
    dock.layer_combo.setLayer(first)
    pump()
    assert dock.sort_field_combo.currentData() == "area"
    assert dock._current_fid() == position


def test_new_feature_selected_on_map_is_found(dock, make_layer):
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    feat = QgsFeature(layer.fields())
    feat.setAttributes([50.0, "NEW", 1])
    assert layer.addFeature(feat)           # sets the (temporary) feature id
    layer.selectByIds([feat.id()])          # before the list sync has run
    pump()
    assert dock._current_fid() == feat.id()
    layer.rollBack()
