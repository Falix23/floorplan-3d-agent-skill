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

## Replace furniture with styles you like

Use a furniture photo, including a phone photo with a room background, to replace a selected bed, chair, table, sofa or other supported item in your plan. The Agent inspects visible structure, compares legally usable similar models, adapts orientation, scale and materials, and prepares editable 2D/3D from the same mesh. You can also provide your own lawful mesh. No additional image-to-3D inference, GPU or weights are required. Matching is an approximate substitution; it cannot guarantee an asset for every image or the exact photographed product. If no suitable model is available, the original furniture stays in place.

Example prompt with a new photo and your existing plan:

> Use $floorplan-image to find the specified room/item in my plan.json and replace it based on this phone photo. Inspect the image, compare licensed candidates and explain differences. Adapt using product dimensions or a declared uniform working scale, run a dry run, then apply the replacement transaction. Deliver editable HTML, complete JSON, sources and differences, and verify moving, rotating, size/height edits, undo, export/import and reload. Keep items that cannot be matched unchanged.

After the Agent prepares local assets and a manifest, use the batch entry point:

```bash
python3 skills/floorplan-image/scripts/replace_furniture.py \
  --plan plan.json --manifest replacements.json --output outputs/replacement-case --dry-run
python3 skills/floorplan-image/scripts/replace_furniture.py \
  --plan plan.json --manifest replacements.json --output outputs/replacement-case
```

The script does not perform vision or search the web. A dry run only prints a JSON report. A successful transaction atomically creates a new, previously nonexistent directory containing before/after plans, embedded-asset HTML, a replacement report, a replayable manifest and its prepared assets. If any replacement fails, no output directory is created and the input plan is unchanged. Each manifest entry chooses `replace` or `keep`; dimension and placement changes require evidence. Mesh projection screens walls, doors and neighboring items, with pair-specific reasons for allowed overlaps. This approximate screen is not full 3D collision or ergonomic validation. Full exports with demolition or measurement state must use the existing page's asset import instead; the batch script rejects them rather than silently losing that state.

Prepare real meshes with `furniture_assets.py prepare --material-mode preserve-pbr --texture-max-size 256`. Assets are embedded in `plan.furnitureAssets`, referenced by `assetId`, and share geometry, footprint and orientation between 2D and 3D. PBR texture resizing and 2D color sampling simplify detail; hidden structure and unmarked photo dimensions remain unknown or estimated. Verify editing, undo, complete JSON export, import in a fresh session and reload in the actual HTML. If browser storage fails, retain complete JSON/HTML backups.

Keep user images, downloaded originals and generated artifacts in a private output directory, outside public commits. Record photo permissions, model licenses, authors, file hashes and adaptations separately; an asset's license does not cover the reference photo.

See the [furniture workflow](skills/floorplan-image/references/furniture-image.md), [retrieval guide](skills/floorplan-image/references/furniture-retrieval.md) and [asset schema](skills/floorplan-image/references/plan-schema.md).

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

Use a complete plan JSON for your own geometry and furniture; the root `index.html` remains an editable demo. In the page, **File → Edit plan data** edits rooms, walls and openings, while furniture controls edit position, rotation and dimensions. Save and reload the complete plan through the file menu.

```bash
python3 skills/floorplan-image/scripts/plan_tools.py validate examples/two-room.json
python3 skills/floorplan-image/scripts/plan_tools.py build examples/two-room.json --output outputs/two-room.html
```

See the [plan schema](skills/floorplan-image/references/plan-schema.md) for complete geometry and embedded furniture assets.

## Social Media

- X (Twitter): [@akokoi1](https://x.com/akokoi1)

## License

[MIT](LICENSE)
