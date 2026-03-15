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
- Step through features with **First**, **Previous**, **Next**, and **Last** buttons
- **Pick from map** — click a feature directly on the canvas to jump to it (like the Identify tool)
- Sort features by any attribute field, ascending or descending
- Each navigated feature is automatically **selected**, **zoomed to**, and **flashed** on the map canvas
- Feature counter shows current position (e.g. `3 / 150`)

### Native Attribute Form
- Displays the native QGIS attribute form — identical to the built-in form you see when identifying features
- Scrollable for layers with many attributes
- Supports inline editing when the layer is in edit mode — changes are saved when you navigate to the next feature

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
4. Use the navigation buttons to step through features, or click the **Pick** button and click a feature on the map
5. Toggle **Auto-zoom** to centre on features, and **Auto-scale** to lock/unlock the map scale
6. To edit attributes: toggle the layer into **Edit Mode**, make changes in the form, and navigate — edits save automatically

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
