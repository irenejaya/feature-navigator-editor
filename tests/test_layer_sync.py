# -*- coding: utf-8 -*-
"""The navigation list follows deletes, adds, undo, commit, rollback, filters."""

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject

from helpers import pump


def editing_dock(dock, layer, index=4):
    dock.layer_combo.setLayer(layer)
    layer.startEditing()
    dock._go_to_index(index)
    pump()


def test_del_key_delete_moves_to_next(dock, make_layer):
    """Regression: Del-key deletes left the feature in the list."""
    layer = make_layer(10)
    editing_dock(dock, layer)
    victim, expected_next = dock._current_fid(), dock.feature_ids[5]
    layer.beginEditCommand("Features deleted")      # what QGIS's Del key does
    layer.deleteSelectedFeatures()
    layer.endEditCommand()
    pump()
    assert victim not in dock.feature_ids and len(dock.feature_ids) == 9
    assert dock._current_fid() == expected_next and dock.current_index == 4
    assert list(layer.selectedFeatureIds()) == [expected_next]
    assert dock._feature_form.feature().id() == expected_next


def test_attribute_table_delete_moves_to_next(dock, make_layer):
    layer = make_layer(10)
    editing_dock(dock, layer)
    victim = dock._current_fid()
    layer.deleteFeature(victim)
    pump()
    assert victim not in dock.feature_ids
    assert dock.current_index == 4 and dock._current_fid() is not None


def test_deleting_another_feature_keeps_current(dock, make_layer):
    layer = make_layer(10)
    editing_dock(dock, layer)
    current, other = dock._current_fid(), dock.feature_ids[0]
    layer.deleteFeature(other)
    pump()
    assert dock._current_fid() == current and other not in dock.feature_ids


def test_panel_delete_matches_del_key_and_undo_restores(dock, make_layer, confirm_dialogs):
    layer = make_layer(10)
    editing_dock(dock, layer)
    victim, index = dock._current_fid(), dock.current_index
    dock.del_btn.click()
    pump()
    assert victim not in dock.feature_ids and dock.current_index == index
    dock._undo()
    pump()
    assert victim in dock.feature_ids and len(dock.feature_ids) == 10


def test_deleting_the_last_feature_moves_to_new_last(dock, make_layer):
    layer = make_layer(10)
    editing_dock(dock, layer)
    dock._go_last()
    layer.deleteFeature(dock._current_fid())
    pump()
    assert dock.current_index == len(dock.feature_ids) - 1 == 8


def test_added_features_and_rollback_resync(dock, make_layer):
    layer = make_layer(5)
    editing_dock(dock, layer, 0)
    feat = QgsFeature(layer.fields())
    feat.setAttributes([99.0, "NEW", 1])
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(5, 5)))
    layer.addFeature(feat)
    pump()
    assert len(dock.feature_ids) == 6
    layer.rollBack()
    pump()
    assert len(dock.feature_ids) == 5


def test_saving_a_new_feature_keeps_it_on_screen(dock, make_layer):
    """On commit the temporary id is reported as deleted; the navigator must
    follow the feature to its permanent id instead of moving on."""
    layer = make_layer(5)
    editing_dock(dock, layer, 0)
    feat = QgsFeature(layer.fields())
    feat.setAttributes([99.0, "NEW", 1])
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(7, 7)))
    layer.addFeature(feat)
    temporary = feat.id()
    layer.selectByIds([temporary])        # navigator on the new feature
    pump()
    assert dock._current_fid() == temporary
    dock._save_edits()
    pump()
    assert len(dock.feature_ids) == layer.featureCount() == 6
    committed = dock._current_fid()
    assert committed is not None and committed != temporary
    assert layer.getFeature(committed)["land"] == "NEW"
    assert dock._feature_form.feature().id() == committed


def test_layer_filter_reloads_and_query_button_clears(dock, make_layer):
    layer = make_layer(10)
    dock.layer_combo.setLayer(layer)
    layer.setSubsetString('"area" < 5')
    pump()
    assert len(dock.feature_ids) == 5
    assert dock.query_builder_btn.isChecked()
    dock.query_builder_btn.click()
    pump()
    assert layer.subsetString() == "" and not dock.query_builder_btn.isChecked()
    assert len(dock.feature_ids) == 10


def test_removing_the_layer_clears_everything(dock, make_layer):
    layer = make_layer(5)
    dock.layer_combo.setLayer(layer)
    dock.lock_nav_cb.setChecked(True)
    dock._go_first()
    pump()
    QgsProject.instance().removeMapLayer(layer.id())
    pump()
    assert dock._layer is None and dock.feature_ids == []
    assert dock._highlight is None and dock._feature_form is None
