# Asset provenance

All assets are local, editable, and generated from this bundle; no external artwork or private evidence is packaged.

| Asset | Local source / command | Purpose |
|---|---|---|
| `architecture.svg` | `docs/diagrams/controller-state.mmd`; `python tools/render_assets.py` invokes local `tools/assets/node_modules/.bin/mmdc` | State-machine diagram |
| `cover.svg` | Hand-authored local editable SVG in this directory | Systems Paper cover master |
| `cover.png` | `python tools/render_assets.py` rasterizes `cover.svg` via repository-local, lockfile-pinned `@resvg/resvg-js` | 1600×900 social preview |

The cover uses original typography, vector geometry, and colors. It depicts an advisory recovery loop, not an autonomous tool executor.
