# -*- coding: utf-8 -*-
"""Field organizer: hide fields in the panel, per layer, saved in the project."""

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsField

from featurenaved.compat import CHECKED, UNCHECKED, USER_ROLE
from featurenaved.field_organizer import HIDDEN_FIELDS_PROPERTY, load_hidden_fields
from helpers import multi_row, pump, type_text, visible_form_fields


def open_organizer(dock):
    dock._fields_menu.aboutToShow.emit()     # what opening the popup does
    return dock._field_organizer


def set_shown(organizer, name, shown):
    for i in range(organizer.list_widget.count()):
        item = organizer.list_widget.item(i)
        if item.data(USER_ROLE) == name:
            item.setCheckState(CHECKED if shown else UNCHECKED)
    pump()


def loaded(dock, layer):
    dock.layer_combo.setLayer(layer)
    dock._go_first()
    pump()


def test_hiding_fields_in_the_form(dock, parcels_gpkg):
    loaded(dock, parcels_gpkg)
    organizer = open_organizer(dock)
    assert organizer.list_widget.count() == 8
    assert not dock._fields_hint.isVisible()

    set_shown(organizer, "DEPTH_MAX_1_PCT", False)
    set_shown(organizer, "PARCEL_VIEW_PFI", False)
    shown = visible_form_fields(dock)
    assert "DEPTH_MAX_1_PCT" not in shown and "PARCEL_VIEW_PFI" not in shown
    assert "FL_2_PCT" in shown
    assert "Showing 6 of 8 fields" in dock._fields_hint.text()
    assert dock._fields_hint.isVisible()
    assert dock.fields_btn.isChecked()

    dock._go_next()                           # form is reused: still hidden
    assert visible_form_fields(dock) == shown


def test_filter_with_hide_all_and_show_all(dock, parcels_gpkg):
    loaded(dock, parcels_gpkg)
    organizer = open_organizer(dock)
    QTest.keyClicks(organizer.filter_edit, "fl_")
    listed = [organizer.list_widget.item(i).text()
              for i in range(organizer.list_widget.count())
              if not organizer.list_widget.isRowHidden(i)]
    assert listed == ["FL_1_PCT_MAX", "FL_2_PCT"]

    organizer.hide_all_btn.click()            # only the listed fields
    pump()
    shown = visible_form_fields(dock)
    assert "FL_1_PCT_MAX" not in shown and "FL_2_PCT" not in shown
    assert "LAND_USE_TYPE" in shown

    organizer.filter_edit.clear()
    organizer.show_all_btn.click()
    pump()
    assert len(visible_form_fields(dock)) == 8
    assert not dock._fields_hint.isVisible()
    assert HIDDEN_FIELDS_PROPERTY not in parcels_gpkg.customPropertyKeys()


def test_multi_edit_respects_hidden_fields(dock, parcels_gpkg):
    layer = parcels_gpkg
    loaded(dock, layer)
    organizer = open_organizer(dock)
    set_shown(organizer, "PCL_FLOODED_AREA", False)
    layer.startEditing()
    layer.selectByIds(dock.feature_ids[:2])
    pump()
    assert not multi_row(dock, "PCL_FLOODED_AREA").widget.isVisible()
    assert multi_row(dock, "FL_2_PCT").widget.isVisible()
    assert dock._fields_hint.isVisible()

    row = multi_row(dock, "FL_2_PCT")
    type_text(row.line_edit, "5")
    assert dock._multi_panel.pending_count() == 1
    set_shown(open_organizer(dock), "FL_2_PCT", False)
    # a hidden field is never written "blind"
    assert not row.checkbox.isChecked()
    assert dock._multi_panel.pending_count() == 0


def test_survives_fields_being_added(dock, parcels_gpkg):
    layer = parcels_gpkg
    loaded(dock, layer)
    set_shown(open_organizer(dock), "PCL_FLOODED_AREA", False)
    layer.startEditing()
    layer.addAttribute(QgsField("NEW_NOTE", QVariant.String))
    pump()
    dock._go_first()
    pump()
    shown = visible_form_fields(dock)
    assert "PCL_FLOODED_AREA" not in shown and "NEW_NOTE" in shown
    assert "of 9 fields" in dock._fields_hint.text()
    layer.rollBack()


def test_choice_is_per_layer_and_saved_on_the_layer(dock, iface, parcels_gpkg, make_layer):
    from featurenaved.navigator_widget import FeatureNavEdDockWidget
    layer = parcels_gpkg
    other = make_layer(3, "other")
    loaded(dock, layer)
    set_shown(open_organizer(dock), "PCL_FLOODED_AREA", False)
    assert load_hidden_fields(layer) == {"PCL_FLOODED_AREA"}

    dock.layer_combo.setLayer(other)
    pump()
    assert dock._hidden_fields == set() and not dock.fields_btn.isChecked()
    loaded(dock, layer)
    assert "PCL_FLOODED_AREA" not in visible_form_fields(dock)

    second = FeatureNavEdDockWidget(iface)    # e.g. after reopening the project
    second.show()
    try:
        loaded(second, layer)
        assert "PCL_FLOODED_AREA" not in visible_form_fields(second)
    finally:
        second.cleanup()
