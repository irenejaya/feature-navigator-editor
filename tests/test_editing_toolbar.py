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
    assert not dock.save_edits_btn.isEnabled()      # nothing to save yet
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


def test_save_greys_out_when_nothing_is_left_to_save(dock, make_layer):
    """Regression: Save stayed enabled after saving (plugin or QGIS toolbar)."""
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_first()
    fid = dock._current_fid()
    land = layer.fields().indexOf("land")
    assert not dock.save_edits_btn.isEnabled()

    layer.changeAttributeValue(fid, land, "X")
    pump()
    assert dock.save_edits_btn.isEnabled()
    dock.save_edits_btn.click()                      # the plugin's Save
    pump()
    assert not dock.save_edits_btn.isEnabled()

    layer.changeAttributeValue(fid, land, "Y")
    pump()
    assert dock.save_edits_btn.isEnabled()
    assert layer.commitChanges(False)                # QGIS's own Save Layer Edits
    pump()
    assert not dock.save_edits_btn.isEnabled()

    layer.changeAttributeValue(fid, land, "Z")
    pump()
    dock.undo_btn.click()                            # undone: nothing left to save
    pump()
    assert not dock.save_edits_btn.isEnabled()


def test_typing_in_the_form_enables_save(dock, make_layer):
    layer = make_layer(3)
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_first()
    assert not dock.save_edits_btn.isEnabled()
    type_text(form_line_edit(dock._feature_form, layer, "land"), "TYPED")
    assert dock.save_edits_btn.isEnabled()           # still only in the form
    assert not layer.editBuffer().isModified()       # and not pushed into the layer
    dock.save_edits_btn.click()
    pump()
    assert layer.getFeature(dock._current_fid())["land"] == "TYPED"
    assert not dock.save_edits_btn.isEnabled()
