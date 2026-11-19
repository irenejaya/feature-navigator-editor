# -*- coding: utf-8 -*-
"""Revert this feature: discard its unsaved changes only."""

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

from helpers import form_line_edit, pump, type_text, values


def editing(dock, layer, index=1):
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_to_index(index)
    pump()
    return dock._current_fid()


def test_button_only_enabled_with_unsaved_changes(dock, make_layer):
    layer = make_layer(5)
    fid = editing(dock, layer)
    land = layer.fields().indexOf("land")
    saved = values(layer, [fid], "land")[0]
    assert not dock.revert_btn.isEnabled()
    layer.changeAttributeValue(fid, land, "X")
    pump()
    assert dock.revert_btn.isEnabled()
    # Typing the saved value back by hand leaves nothing to revert, even
    # though QGIS still flags the feature as changed.
    layer.changeAttributeValue(fid, land, saved)
    pump()
    assert not dock.revert_btn.isEnabled()


def test_reverts_attributes_and_geometry_of_this_feature_only(dock, make_layer, confirm_dialogs):
    layer = make_layer(5)
    fid = editing(dock, layer)
    other = dock.feature_ids[3]
    land = layer.fields().indexOf("land")
    saved_land = values(layer, [fid], "land")
    saved_geom = layer.getFeature(fid).geometry().asWkt()
    layer.changeAttributeValue(fid, land, "CHANGED")
    layer.changeGeometry(fid, QgsGeometry.fromPointXY(QgsPointXY(999, 999)))
    layer.changeAttributeValue(other, land, "OTHER")
    pump()
    dock.revert_btn.click()
    pump()
    assert values(layer, [fid], "land") == saved_land
    assert layer.getFeature(fid).geometry().asWkt() == saved_geom
    assert values(layer, [other], "land") == ["OTHER"]          # untouched
    assert form_line_edit(dock._feature_form, layer, "land").text() == saved_land[0]
    assert not dock.revert_btn.isEnabled()

    dock._undo()                                                # one undo step
    pump()
    assert values(layer, [fid], "land") == ["CHANGED"]


def test_reverts_values_still_being_typed(dock, make_layer, confirm_dialogs):
    layer = make_layer(5)
    fid = editing(dock, layer)
    saved = values(layer, [fid], "land")[0]
    field = form_line_edit(dock._feature_form, layer, "land")
    type_text(field, "TYPED")
    assert dock.revert_btn.isEnabled()
    dock.revert_btn.click()
    pump()
    assert field.text() == saved
    dock._go_next()                         # nothing gets saved on the way out
    assert values(layer, [fid], "land") == [saved]


def test_reverting_a_new_feature_removes_it(dock, make_layer, confirm_dialogs):
    layer = make_layer(5)
    editing(dock, layer)
    feat = QgsFeature(layer.fields())
    feat.setAttributes([9.0, "NEW", 1])
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(1, 1)))
    layer.addFeature(feat)
    layer.selectByIds([feat.id()])
    pump()
    assert dock._current_fid() == feat.id()
    dock.revert_btn.click()
    pump()
    assert feat.id() not in dock.feature_ids
    assert layer.featureCount() == 5
    assert dock._current_fid() is not None          # moved on, like a delete


def test_nothing_happens_when_cancelled(dock, make_layer, monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox
    from featurenaved.compat import qenum
    layer = make_layer(5)
    fid = editing(dock, layer)
    layer.changeAttributeValue(fid, layer.fields().indexOf("land"), "KEEP")
    no = qenum(QMessageBox, 'StandardButton', 'No')
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: no))
    dock.revert_btn.click()
    assert values(layer, [fid], "land") == ["KEEP"]
