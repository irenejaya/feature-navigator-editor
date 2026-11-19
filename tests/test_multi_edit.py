# -*- coding: utf-8 -*-
"""Multi-edit: nothing is written until Apply."""

from helpers import multi_row, pump, set_sort, type_text, values


def start_multi(dock, layer, fids):
    layer.startEditing()
    layer.selectByIds(fids)
    pump()


def test_enters_with_two_selected_on_editable_layer(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    start_multi(dock, layer, dock.feature_ids[:2])
    assert dock._multi_edit_mode
    assert dock._multi_panel.isVisible()
    assert dock._counter_label.text() == "M (2)"
    assert not dock.next_btn.isEnabled()
    assert multi_row(dock, "land").mixed       # COM vs RES


def test_selection_change_never_applies(dock, make_layer):
    """Regression: the native form wrote typed values on selection change."""
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    first_two = dock.feature_ids[:2]
    before = values(layer, first_two, "land")
    start_multi(dock, layer, first_two)
    row = multi_row(dock, "land")
    type_text(row.line_edit, "IND")
    assert row.checkbox.isChecked()

    three = first_two + [dock.feature_ids[2]]
    layer.selectByIds(three)
    pump()
    assert values(layer, first_two, "land") == before
    assert row.checkbox.isChecked() and row.line_edit.text() == "IND"
    assert dock._multi_panel.apply_btn.text() == "Apply to 3 features"


def test_apply_writes_once_and_undo_reverts(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    fids = dock.feature_ids[:3]
    before = values(layer, fids, "land")
    start_multi(dock, layer, fids)
    type_text(multi_row(dock, "land").line_edit, "IND")
    dock._multi_panel.apply_btn.click()
    pump()
    assert values(layer, fids, "land") == ["IND"] * 3
    assert dock._multi_panel.pending_count() == 0

    dock._undo()
    pump()
    assert values(layer, fids, "land") == before
    assert multi_row(dock, "land").mixed        # baseline refreshed


def test_reset_discards(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    fids = dock.feature_ids[:2]
    before = values(layer, fids, "land")
    start_multi(dock, layer, fids)
    row = multi_row(dock, "land")
    type_text(row.line_edit, "TMP")
    dock._multi_panel.reset_btn.click()
    assert not row.checkbox.isChecked()
    assert values(layer, fids, "land") == before


def test_dropping_to_one_feature_discards_and_says_so(dock, iface, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    fids = dock.feature_ids[:2]
    start_multi(dock, layer, fids)
    type_text(multi_row(dock, "land").line_edit, "LOST")
    layer.selectByIds([fids[0]])
    pump()
    assert not dock._multi_edit_mode
    assert values(layer, [fids[0]], "land") != ["LOST"]
    assert any("discarded" in m for m in iface.messages)
    assert dock._current_fid() == fids[0]


def test_passive_indicator_when_not_editing(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_first()
    layer.selectByIds(dock.feature_ids[:3])
    pump()
    assert dock._counter_label.text() == "M (3)" and not dock._multi_edit_mode

    dock.toggle_edit_btn.click()
    pump()
    assert dock._multi_edit_mode
    dock.toggle_edit_btn.click()
    pump()
    assert not dock._multi_edit_mode and dock._counter_label.text() == "M (3)"

    dock._go_first()                       # even when already on #1
    assert dock.feature_spin.isVisible()


def test_does_not_widen_or_scroll_sideways(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    dock._go_first()
    single_min = dock.minimumSizeHint().width()
    start_multi(dock, layer, dock.feature_ids[:3])
    dock.resize(260, 900)
    pump(10)
    panel = dock._multi_panel
    assert dock.minimumSizeHint().width() <= single_min
    assert panel._body.width() <= panel._scroll.viewport().width()
    assert not panel._scroll.horizontalScrollBar().isVisible()


def test_geopackage_primary_key_and_native_widgets(dock, parcels_gpkg):
    layer = parcels_gpkg
    dock.layer_combo.setLayer(layer)
    set_sort(dock, "PCL_FLOODED_AREA")
    fids = dock.feature_ids[:2]
    start_multi(dock, layer, fids)

    fid_row = multi_row(dock, "fid")
    assert not fid_row.editable and not fid_row.checkbox.isEnabled()
    land_use = multi_row(dock, "LAND_USE_TYPE")
    assert type(land_use.widget).__name__ == "QComboBox"

    type_text(multi_row(dock, "FL_2_PCT").line_edit, "0")
    land_use.widget.setCurrentIndex(land_use.widget.findText("INDUSTRIAL"))
    assert land_use.checkbox.isChecked()
    dock._multi_panel.apply_btn.click()
    pump()
    assert values(layer, fids, "FL_2_PCT") == [0.0, 0.0]
    assert values(layer, fids, "LAND_USE_TYPE") == ["INDUSTRIAL", "INDUSTRIAL"]
    assert layer.commitChanges(), layer.commitErrors()


def test_mixed_spin_box_says_mixed(dock, parcels_gpkg):
    layer = parcels_gpkg
    dock.layer_combo.setLayer(layer)
    set_sort(dock, "PCL_FLOODED_AREA")
    start_multi(dock, layer, dock.feature_ids[:2])    # PARCEL_MASTER_FID: 0 / NULL
    row = multi_row(dock, "PARCEL_MASTER_FID")
    assert row.mixed
    assert row.line_edit.text() == "Mixed values"
    row.widget.stepUp()
    pump()
    assert row.checkbox.isChecked()
    row.checkbox.setChecked(False)
    assert row.line_edit.text() == "Mixed values"
    assert dock._multi_panel.pending_count() == 0
