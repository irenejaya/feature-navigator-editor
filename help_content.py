# -*- coding: utf-8 -*-
"""
Text of the help window.

Each page has a ready-made picture ``help/<id>.png`` with red numbered
markers; item *n* below explains marker *n*. Every item names the widget the
marker points at (``target``), which is how ``scripts/make_help_images.py``
places the markers - so pictures and text always use the same numbers.

Targets are dock attribute paths (``"undo_btn"``, ``"_multi_panel.apply_btn"``),
``"form:<field>"`` for a field in the feature form, ``"row:<field>.<part>"``
for a multi-edit row, or ``"inset:<name>"`` for an extra picture drawn next to
the panel (the Settings menu, the map).
"""

PAGES = [
    {
        "id": "overview",
        "title": "Overview",
        "intro": "The buttons along the top work on the layer shown in the panel, "
                 "whichever layer is selected in the Layers panel. Greyed-out "
                 "buttons have nothing to do yet (for example Save and Undo stay "
                 "grey until you have changed something).",
        "items": [
            ("toggle_edit_btn", "/mActionToggleEditing.svg", "Toggle editing",
             "Start or stop editing the layer. When you stop, QGIS asks whether "
             "to save or discard your changes."),
            ("save_edits_btn", "/mActionSaveEdits.svg", "Save edits",
             "Write your changes to the layer and keep editing."),
            ("undo_btn", "/mActionUndo.svg", "Undo",
             "Undo the last change on this layer, including a value you have "
             "just typed."),
            ("redo_btn", "/mActionRedo.svg", "Redo", "Redo what you just undid."),
            ("revert_btn", "/mActionRollbackEdits.svg", "Revert this feature",
             "Throw away the unsaved changes of only the feature on screen. A "
             "feature you added in this session is removed. Undo brings it back."),
            ("query_builder_btn", None, "Query Builder",
             "Filter the layer so you only step through matching features. "
             "Click again to remove the filter. Stop editing first."),
            ("search_btn", "/search.svg", "Find",
             "Jump to the next or previous feature with a given value. "
             "Nothing is filtered out."),
            ("fields_btn", "/mActionShowAllLayers.svg", "Fields",
             "Hide the fields you don't need and drag them into your own order. "
             "Saved per layer with the project."),
            ("settings_btn", "/mActionOptions.svg", "Settings",
             "Lock navigation, Flash on navigation, Track visited features, "
             "Show measurements, and Clear visited marks."),
            ("help_btn", "/mActionHelpContents.svg", "Help", "Opens this window."),
            ("layer_combo", None, "Layer",
             "The layer to step through. You can also drag a layer here from "
             "the Layers panel."),
            ("active_layer_btn", "/mActionRefresh.svg", "Use active layer / reload",
             "Switch to the layer selected in the Layers panel, or reload and "
             "re-sort this one."),
            ("sort_field_combo", None, "Sort by",
             "The field that sets the order. The heading shows the current "
             "feature's value."),
            ("sort_order_btn", "/mActionArrowUp.svg", "Sort direction",
             "Ascending or descending. Empty (NULL) values count as the "
             "smallest: first when ascending, last when descending."),
        ],
        "notes": [],
    },
    {
        "id": "browsing",
        "title": "Browsing",
        "intro": "Step through the features one by one. Each feature you move to is "
                 "selected, shown on the map and opened in the form below.",
        "items": [
            ("first_btn", "/mActionDoubleArrowLeft.svg", "First", "First feature (Alt+Home)."),
            ("prev_btn", "/mActionArrowLeft.svg", "Previous",
             "Previous feature (Alt+Left or Alt+P)."),
            ("feature_spin", None, "Position",
             "Where you are in the list. Type a number to jump there. Shows NA "
             "when no feature is open, and M (n) when several are selected."),
            ("seen_dot", None, "Seen before",
             "Green: you have opened this feature before. Grey: first visit."),
            ("next_btn", "/mActionArrowRight.svg", "Next", "Next feature (Alt+Right or Alt+N)."),
            ("last_btn", "/mActionDoubleArrowRight.svg", "Last", "Last feature (Alt+End)."),
            ("flash_btn", "/mActionHighlightFeature.svg", "Flash",
             "Flash the feature on the map, and centre it again if Auto-zoom is on."),
            ("review_strip", None, "Progress strip",
             "Every feature in list order: green = visited, grey = not yet. The "
             "dark tick is where you are. Click anywhere on it to jump there."),
            ("visited_label", None, "Visited count", "How many features you have opened so far."),
            ("next_unvisited_btn", None, "Next unvisited",
             "Go to the next feature you haven't opened yet."),
            ("auto_zoom_cb", None, "Auto-zoom", "Move the map to each feature as you go."),
            ("scale_mode_combo", None, "Scale",
             "Keep scale: just centre the feature. Fit feature: zoom so the whole "
             "feature fits. Fixed scale: use the scale typed in the box next to it."),
            ("_feature_form_container", None, "Feature form",
             "The attributes of the current feature. You can change them when "
             "the layer is in edit mode."),
            ("_measure_label", None, "Measurements (optional)",
             "Tick Show measurements in Settings to see the area and perimeter "
             "(length for lines) here in small text, in the project's units."),
        ],
        "notes": [
            "Click a feature on the map to jump to it. Clicking an empty spot "
            "leaves a “Return to feature N” link, and Next/Previous carry "
            "on from where you were.",
            "The order stays fixed while you work. It only changes when you "
            "change the sort, reload, or click Re-sort.",
            "Visited marks are saved with the project. Clear them from Settings.",
        ],
    },
    {
        "id": "editing",
        "title": "Editing one feature",
        "intro": "Turn on editing with the pencil, then change values straight in "
                 "the form. Changes stay in QGIS's edit buffer until you save.",
        "items": [
            ("toggle_edit_btn", "/mActionToggleEditing.svg", "Editing is on",
             "The pencil stays pressed while the layer is in edit mode. Click it "
             "to stop editing; QGIS asks whether to save or discard."),
            ("save_edits_btn", "/mActionSaveEdits.svg", "Save edits",
             "Write your changes to the layer. Saving from the QGIS toolbar works too "
             "and includes values still in the form."),
            ("undo_btn", "/mActionUndo.svg", "Undo", "Undo the last change on this layer."),
            ("redo_btn", "/mActionRedo.svg", "Redo", "Redo what you just undid."),
            ("revert_btn", "/mActionRollbackEdits.svg", "Revert this feature",
             "Throw away the unsaved changes of only this feature (values, shape, "
             "and anything still being typed)."),
            ("resort_btn", "/sort.svg", "Re-sort",
             "Appears when you change a value of the sort field. Nothing moves "
             "until you click it."),
            ("del_btn", "/mActionDeleteSelected.svg", "Delete",
             "Delete the feature on screen (asks first) and move to the next one. "
             "Undo brings it back."),
            ("form:FLOOD_PCT", None, "Type to change a value",
             "Your change is kept when you move to another feature. Save edits "
             "writes it to the layer."),
        ],
        "notes": [
            "Deleting with the Del key or from the attribute table works just "
            "like the Delete button: the panel moves on to the next feature.",
            "Revert only touches the feature on screen. Undo works on the whole "
            "layer, one step at a time.",
        ],
    },
    {
        "id": "multi_edit",
        "title": "Multi-edit",
        "intro": "Select 2 or more features while editing (for example Ctrl+click "
                 "on the map) to give them the same values in one go. Navigation "
                 "must not be locked.",
        "items": [
            ("_counter_label", None, "M (3)",
             "Several features are selected, so the panel is in multi-edit."),
            ("_multi_panel._info", None, "Summary",
             "How many features are selected and how many fields will change."),
            ("row:LAND_USE.checkbox", None, "Tick box",
             "Ticked fields are written to every selected feature. Editing a "
             "field ticks it; untick it to leave that field alone."),
            ("row:LAND_USE.label", None, "Field name",
             "Bold: this field will change. Italic: the selected features have "
             "different values."),
            ("row:PARCEL_ID.widget", None, "Mixed values",
             "The selected features don't share one value in this field."),
            ("_multi_panel.reset_btn", None, "Reset", "Throw away everything you typed here."),
            ("_multi_panel.apply_btn", None, "Apply",
             "Write the ticked fields to all selected features as one Undo step. "
             "Use Save edits to keep them."),
        ],
        "notes": [
            "Nothing is written until you click Apply. Changing the selection "
            "keeps what you typed.",
            "Selecting a single feature closes multi-edit and discards anything "
            "you didn't apply (a message tells you).",
            "Key fields (such as fid), read-only and unique fields are locked.",
            "When the layer is not in edit mode, the counter only shows M (n).",
        ],
    },
    {
        "id": "lock",
        "title": "Lock navigation",
        "intro": "Lock the navigator on its feature so you can click around the "
                 "map - edit shapes, check neighbours - without losing your place.",
        "items": [
            ("inset:settings", "/mActionOptions.svg", "Lock navigation",
             "In Settings (the wrench). Tick to lock, untick to unlock."),
            ("_nav_group", None, "Navigate (locked)",
             "The heading tells you navigation is locked."),
            ("inset:map", None, "Orange outline",
             "The locked feature is outlined on the map, so you can always find it."),
            ("next_btn", "/mActionArrowRight.svg", "Arrows still work",
             "Next, Previous, Find and Delete still move the navigator. The "
             "feature you move to is selected, so map tools work on it."),
        ],
        "notes": [
            "While locked, clicking or selecting other features on the map does "
            "not move the navigator.",
            "Multi-edit is off while navigation is locked.",
        ],
    },
]


def resolve_target(dock, target):
    """Widget a help item points at, or None for ``inset:`` targets."""
    if target.startswith("inset:"):
        return None
    if target.startswith("form:"):
        from qgis.gui import QgsEditorWidgetWrapper
        layer = dock._layer
        idx = layer.fields().indexOf(target[5:])
        for wrapper in dock._feature_form.findChildren(QgsEditorWidgetWrapper):
            if wrapper.fieldIdx() == idx:
                return wrapper.widget()
        raise LookupError(target)
    if target.startswith("row:"):
        field, part = target[4:].rsplit(".", 1)
        row = next(r for r in dock._multi_panel._rows if r.field_name == field)
        return getattr(row, part)
    obj = dock
    for name in target.split("."):
        obj = getattr(obj, name)
    return obj
