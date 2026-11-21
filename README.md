# Wible

### Click hanging curves into the Blender viewport, with live gravity and collisions.

![Blender](https://img.shields.io/badge/Blender-5.2%2B-7fd3ff?style=flat-square) ![Type](https://img.shields.io/badge/Blender-extension-ff8fa3?style=flat-square) ![Python](https://img.shields.io/badge/Python-3.13-c4a7ff?style=flat-square) ![Version](https://img.shields.io/badge/version-0.1.0-ffc36b?style=flat-square) ![License](https://img.shields.io/badge/license-GPL--3.0--or--later-6fd6a0?style=flat-square)

---

Wible places wires and cables as curves: click points on surfaces or in the air and the curve hangs between them like the real thing. Each curve stays live until you save it, so you can drag its points, tweak tension or thickness, and watch it swing and settle against nearby objects without pressing Play.

## Features

- Point placement tool: click on surfaces or in the air, with a reticle showing where the next point lands.
- Catenary gravity with Tension, Weight and Length that stay in sync, per span between points.
- Live rope physics with Stiffness and Damping, independent of the timeline.
- Drapes over and collides with nearby objects, or only the objects in a chosen collection.
- Every point gets a handle you drag with Blender's usual tools; moving, rotating or scaling the whole curve keeps its rope length.
- Right-click a curve while drawing to add a point to it.
- Depth, Resolution, Radial Resolution and Fill Caps, like Blender's own curves.
- Save as Curve (light, polygons made at render time) or Save as Mesh.
- Mesh output with UV islands for the side and both caps, smooth or flat shading, and hard or beveled cap edges.
- Trim sheet slots: pick faces on a trim plane and each curve's UVs fit a slot at your texel density, which also sets its thickness.
- Per-curve history bar: hover a step to see what changed, click to go back or forward.
- Sidebar tab plus a popup with the same UI on `Shift+Alt+X`.

## Requirements

- Blender 5.2 or newer.

## Install

1. Download `Wible-0.1.0.zip` from [Releases](../../releases).
2. In Blender, open **Edit > Preferences > Get Extensions**, click the menu at the top right and pick **Install from Disk**.
3. Select the zip. Wible shows up in the 3D Viewport sidebar (`N`) under the **Wible** tab.

> [!NOTE]
> GitHub's automatic **Source code** archives are not installable extensions. Use the `Wible-0.1.0.zip` asset.

## Usage

1. Optional: select the objects you want to attach to and keep **Only Selected** on.
2. Click **Draw** in the Wible tab and place points in the viewport.
3. Adjust the new curve in the **Shape**, **Physics**, **Geometry** and **UVs** sections, or drag its point handles.
4. Click **Save as Mesh** or **Save as Curve**. **Edit Curve** reopens a saved one.

New curves go into a **Wible** collection.

### Draw tool keys

| Key | Action |
|---|---|
| Left click | Place a point |
| Right click on a curve | Add a point to that curve |
| Right click elsewhere, `Enter` | Finish this curve and start the next |
| `Backspace`, `Ctrl+Z` | Remove the last point |
| `Esc` | Finish and leave the tool |
| Middle mouse, wheel, numpad, Alt navigation | Navigate as usual |

### Placement

| Mode | Points land |
|---|---|
| **Snap Ends** | On the surface under the pointer; over empty space, on a plane facing the view through the previous point |
| **View Plane** | Always on a plane facing the view, through the previous point (the 3D cursor for the first) |

### Performance

Defaults are set to stay responsive. Physics and collisions cost grows with **Points** and with the number of nearby objects, so for big scenes set **Drape Over** to a small collection, keep **Only Selected** on while drawing, and lower **Resolution** or **Radial Resolution** before saving many curves as meshes. Every setting's tooltip notes its cost.

### Shortcut

`Shift+Alt+X` opens the Wible popup at the mouse in the 3D Viewport. You can change it in the add-on's preferences.

## Building from source

Blender 5.2+ on your `PATH`, Python 3.11+ for the tests.

    python -m unittest discover tests
    blender --command extension validate wible
    mkdir release
    blender --command extension build --source-dir wible --output-dir release

For development, add a local extension repository in **Preferences > Get Extensions > Repositories** pointing at this folder.

## How it works

    wible/
      draw.py        point placement tool and its viewport drawing
      solver.py      catenary solver for spans between points
      sim.py         rope physics (Verlet) with collisions
      build.py       curve data, handles, colliders and rest shapes
      live.py        timer and depsgraph handler that keep curves moving
      mesh_build.py  tube mesh, caps, bevels, UVs and normals
      trim.py        trim sheet slot layout
      history.py     per-curve history steps
      ui.py          sidebar panel and popup
    tests/           solver, physics, trim and history tests (no Blender needed)
    tools/           icon generator

## Credits

**Author:** Tom Berczeller

- [Blender](https://www.blender.org) by the Blender Foundation and contributors

## License

Released under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later), © 2026 Tom Berczeller.
