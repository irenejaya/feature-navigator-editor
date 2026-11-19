# FeatureNavEd

A QGIS plugin for navigating and editing vector layer features one by one — like the Identify tool, but with full navigation and inline editing.

![Min QGIS Version](https://img.shields.io/badge/3.22--4.x-sketchy?label=QGIS&labelColor=589632&color=grey)

---

## Features

### Layer Selection
- Select any vector layer from the dropdown (point, line, polygon)
- Drag-and-drop a layer directly from the QGIS Layers panel
- Reload/sync button to quickly switch to the active layer

### Feature Navigation
- Step through features with **First**, **Previous**, **Next**, and **Last** buttons (or `Alt+←/→/Home/End`)
- Sort features by any attribute field, ascending or descending — the current feature stays put when you change the sort, and the sort field is remembered per layer
- **Sort field value** — displays the current feature's sort field value at the top for quick reference
- Each navigated feature is automatically **selected** and **zoomed to** (flash is optional)
- Selecting a feature on the map jumps the navigator to it; clearing the selection shows a **Return to feature N** link, and Next/Previous continue from where you were
- Feature counter shows current position (e.g. `3 / 150`)

### Lock Navigation
- Turn on **Settings → Lock navigation** to keep the navigator on its feature while you click, select or edit other things on the map
- The locked feature is outlined in orange on the canvas, so you always know which feature the panel shows
- Navigation buttons, Find and Delete keep working; multi-edit is disabled while locked

### Native Attribute Form
- Displays the native QGIS attribute form — identical to the built-in form you see when identifying features
- Supports inline editing when the layer is in edit mode — values go into the edit buffer when you move to another feature, save, undo or stop editing (also when saving from the main QGIS toolbar)

### Multi-Edit
- Select 2 or more features on a layer in edit mode to set shared values in one go
- Uses the layer's own editor widgets (value maps, spin boxes, dates…); fields that differ show **Mixed values**
- Editing a field ticks it; only ticked fields are written, and **nothing is written until you click Apply** — changing or refining the selection keeps your pending values instead of applying them
- One Apply = one undo step; primary-key, unique and read-only fields are locked
- Fits narrow panels: long field names are shortened instead of forcing a horizontal scrollbar

### Editing Toolbar
- **Toggle editing**, **Save edits**, **Undo** and **Redo** for the navigated layer — no need to make it the active layer first

### Always in Sync
- Deleting the current feature — with the panel's Delete button, the `Del` key or from the attribute table — removes it from the list and moves to the next feature
- Features added, undone, saved or rolled back anywhere in QGIS are picked up automatically

### Query Builder & Find
- **Query Builder** filters the layer so you only navigate matching features; click again to clear
- **Find** jumps to the next/previous feature with a given field value (with autocomplete), without filtering

### Auto-zoom & Scale Control
- **Auto-zoom** — centres the map on the current feature when navigating
- **Auto-scale** — keeps the current map canvas scale (live-synced); uncheck to set a custom scale
- Adjustable map scale (e.g. `1:500`, `1:5000`) when auto-scale is off

---

## Installation

### From ZIP
1. Download or clone this repository
2. In QGIS, go to **Plugins** > **Manage and Install Plugins** > **Install from ZIP**
3. Select the ZIP file or the plugin folder
4. Enable **FeatureNavEd** in the plugin manager

### Manual
Copy the `featurenaved` folder into your QGIS plugins directory:
- **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
- **Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
- **macOS:** `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

---

## Usage

1. Click the **FeatureNavEd** button in the toolbar (or **Plugins** > **FeatureNavEd**)
2. Select a vector layer from the dropdown, or drag one from the Layers panel
3. Choose a field to sort by, and toggle ascending/descending with the arrow button
4. Use the navigation buttons to step through features, or select a feature on the map to jump to it
5. Turn on **Settings → Lock navigation** when you want to click around the map without losing your place
6. To edit: click the **pencil** in the panel toolbar, change values in the form and move on — use **Save edits** to write them to the layer
7. To edit several features at once: select them (navigation unlocked), change the fields you want, then click **Apply**

---

## Project Structure

```
featurenaved/
├── __init__.py          # Plugin entry point
├── plugin.py            # Main plugin class (toolbar, menu, dock registration)
├── navigator_widget.py  # Dock widget with all UI and navigation logic
├── metadata.txt         # QGIS plugin metadata
├── icon.svg             # Plugin icon
├── LICENSE
└── README.md
```

---

## Requirements

- QGIS 3.22 or later (including QGIS 4.x)

---

## License

See [LICENSE](LICENSE) for details.
