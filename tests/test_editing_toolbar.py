# -*- coding: utf-8 -*-
"""Toggle editing / save / undo / redo for the navigated layer."""

from qgis.core import QgsFeatureRequest

from helpers import form_line_edit, pump, type_text


def test_toggle_editing_button(dock, make_layer):
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    assert not dock.toggle_edit_btn.isChecked() and not dock.save_edits_btn.isEnabled()
    dock.toggle_edit_btn.click()
    pump()
    assert layer.isEditable() and dock.toggle_edit_btn.isChecked()
    assert dock.save_edits_btn.isEnabled()
    dock.toggle_edit_btn.click()
    pump()
    assert not layer.isEditable() and not dock.toggle_edit_btn.isChecked()


def test_save_includes_value_still_in_the_form(dock, make_layer):
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_first()
    fid = dock._current_fid()
    type_text(form_line_edit(dock._feature_form, layer, "land"), "SAVED")
    dock._save_edits()
    pump()
    stored = next(layer.dataProvider().getFeatures(QgsFeatureRequest().setFilterFid(fid)))
    assert stored["land"] == "SAVED"
    assert layer.isEditable()


def test_saving_from_qgis_toolbar_includes_form_value(dock, make_layer):
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_first()
    fid = dock._current_fid()
    type_text(form_line_edit(dock._feature_form, layer, "land"), "MAIN")
    assert layer.commitChanges()          # as if saved from the QGIS toolbar
    assert layer.getFeature(fid)["land"] == "MAIN"


def test_undo_redo_buttons(dock, make_layer):
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_first()
    fid = dock._current_fid()
    assert not dock.undo_btn.isEnabled()
    type_text(form_line_edit(dock._feature_form, layer, "land"), "X")
    dock.undo_btn.click()                 # pushes the typed value, undoes it
    pump()
    assert layer.getFeature(fid)["land"] == "COM"
    assert dock.redo_btn.isEnabled()
    dock.redo_btn.click()
    pump()
    assert layer.getFeature(fid)["land"] == "X"
    assert form_line_edit(dock._feature_form, layer, "land").text() == "X"
