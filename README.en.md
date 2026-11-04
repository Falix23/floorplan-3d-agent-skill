# floorplan-3d-agent-skill

## Original author, copyright and license

This derivative project is based on **[wy51ai/floorplan-3d](https://github.com/wy51ai/floorplan-3d)** by **[wuyi (wy51ai)](https://github.com/wy51ai)**. The original copyright notice and complete MIT License are preserved below and in [LICENSE](LICENSE).

This fork is maintained by `Falix23`. The image-to-floorplan Skill and related enhancements are additions in this fork; they do not imply the original author's participation or endorsement.

```text
MIT License

Copyright (c) 2026 wuyi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```


[中文](README.md) | English

A pure front-end tool for interior design on a floor plan: place furniture, remove or modify walls, and take measurements on a 2D plan, then switch to a Three.js 3D scene with one click — view it from above or walk through it in first person. The whole app is a single `index.html`: no build step, just open it.

## Features

**2D Floor Plan**
- Displays the original floor plan at 1:60 / 1:100 scale, dimensions in mm
- Drag 60+ furniture and appliance items from the library on the left (bedroom, living room, dining & kitchen, bathroom, appliances, study & leisure)
- Move, rotate (hold Shift for free angle), and resize items, with automatic snapping to walls
- Measuring tool (snaps to nearby walls; hold Shift to lock horizontal / vertical)
- Remove or modify non-load-bearing walls; load-bearing walls are marked separately
- Layer toggles: dimensions, room names, furniture, grid, load-bearing walls

**3D Scene**
- Bird's-eye, oblique, and top-down views; click a room in the list to fly to it
- Walkthrough mode: WASD + mouse on desktop, virtual joystick on touch devices; click doors to open / close them
- Toggle between full-height and cut-away walls, time-of-day sunlight slider, night lighting
- Detailed furniture models: cabinet door gaps and handles, upholstered headboards, metal and ceramic materials with environment reflections, and more
- Select and drag furniture in 3D as well, kept in sync with the 2D plan in real time

**Plans & Statistics**
- Automatic calculation of room areas and net usable floor area
- Change the floor material of each room (wood, tiles, marble, terrazzo, carpet, etc.), with cost estimates based on area plus 5% wastage
- Undo / redo; plans are auto-saved in the browser's local storage
- Chinese / English UI toggle (button on the right of the top bar; defaults to Chinese and remembers your choice)
- Export to PNG, export / import plans as JSON

## Furniture photos and custom assets (development version)

The development branch adds an Agent workflow that reconstructs a single furniture photo with an open-source method, prepares an embedded GLB and a top-down 2D image from the same mesh, and replaces a specified furniture item in an existing plan. The local `image_to_furniture.py` wrapper orchestrates [TripoSR](https://github.com/VAST-AI-Research/TripoSR), recording code/model revisions, weight hashes and input provenance; an existing mesh with clear usage rights can also be used. Without an explicitly checked front axis, the wrapper stops at `needs_alignment` after reconstruction, before replacing furniture. This is a development capability, not a released feature.

Use `$floorplan-image` with a clear furniture photo, the current plan, the target item ID and any known product dimensions. The Agent must inspect the actual image and mesh, document inferred dimensions and unseen surfaces, prepare the asset, attach it to the item, then check the real HTML in 2D and 3D. A perspective photo is not a floor-plan footprint, and a generic parametric model is not a successful photo reconstruction.

Assets are stored in `plan.furnitureAssets`; furniture keeps its semantic `type` and references `assetId`, with an optional `height` in mm. The model, top-view image and provenance travel with the complete JSON/HTML. Width, depth, height, position and rotation remain editable. The first asset-preparation version samples textures into vertex colors, so fine texture detail is simplified. Separate-axis fitting can also distort proportions and must be checked visually. Large embedded assets may exceed browser storage quotas, so keep the complete JSON/HTML backup and check the actual save result.

The basic plan builder uses Python's standard library. Optional asset preparation requires `trimesh`, `Pillow` and related dependencies; image-to-3D inference runs in a separate environment. The HTML does not run reconstruction or store API keys. Keep input photos, model weights and generated files out of public commits. Method/model licenses and photo usage rights are recorded separately. See the [furniture workflow](skills/floorplan-image/references/furniture-image.md) and [asset schema](skills/floorplan-image/references/plan-schema.md).

## Quick Start

```bash
git clone <repository-url>
cd <repository-directory>
```

Then simply open `index.html` in your browser. Alternatively, start a local static server:

```bash
python3 -m http.server 8000
# Visit http://localhost:8000
```

> Three.js is loaded from the jsDelivr CDN, so an internet connection is required the first time you open the 3D scene.

## Keyboard Shortcuts

| Key | Action |
| --- | --- |
| `T` | Toggle 2D / 3D |
| `V` / `M` / `X` | Select / Measure / Modify walls |
| `R` / `Shift+R` | Rotate selected furniture 90° clockwise / counterclockwise |
| `Delete` / `Backspace` | Delete selected furniture |
| `Ctrl/⌘ + D` | Duplicate selected furniture |
| `Ctrl/⌘ + Z`, `Ctrl/⌘ + Shift + Z` | Undo, redo |
| `F` | Fit to window |
| `+` / `-` | Zoom in / out |
| `[` / `]` | Show / hide the furniture library (left) and the side panel (right) |
| `Shift + F` | Fullscreen |
| `Esc` | Cancel current action |
| Walkthrough: `WASD` / arrow keys, `Shift`, `E` | Move, walk faster, open / close doors |

## Tech Stack

- Vanilla HTML / CSS / JavaScript — no framework, no build step
- 2D floor plan rendered with SVG
- 3D scene built with [Three.js](https://threejs.org/) r160 (OrbitControls, PointerLockControls, RoundedBoxGeometry, RoomEnvironment, CSS2DRenderer)
- Data stored in `localStorage`

## Customizing the Floor Plan

The floor plan data lives in `index.html`:

- `ROOMS`: room polygons, names, and default floor materials
- `WALLS` / `WINS`: walls and window openings
- `MATS`: floor material names and unit prices
- `LIB`: furniture library (type, name, default size, color)
- `buildFurniture()`: 3D models for each furniture type

Edit this data to use your own floor plan.

## Social Media

- X (Twitter): [@akokoi1](https://x.com/akokoi1)

## License

[MIT](LICENSE)
