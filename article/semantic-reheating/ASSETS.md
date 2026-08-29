# Asset provenance

All assets are local, editable, and generated from this bundle; no external artwork or private evidence is packaged.

| Asset | Local source / command | Purpose |
|---|---|---|
| `architecture.svg` | `docs/diagrams/controller-state.mmd`; `python tools/render_assets.py` invokes local `tools/assets/node_modules/.bin/mmdc` | State-machine diagram |
| `cover.svg` | Hand-authored local editable SVG in this directory | Systems Paper cover master |
| `cover.png` | `python tools/render_assets.py` rasterizes `cover.svg` via repository-local, lockfile-pinned `@resvg/resvg-js` and bundled DejaVu Sans TTF files, with system font loading disabled | 1600×900 social preview |

The cover uses original typography, vector geometry, and colors. It depicts an advisory recovery loop, not an autonomous tool executor.

CI renders the generated assets twice into temporary directories and requires within-environment byte stability without replacing the packaged preview. It also requires generated architecture exact SVG bytes and cover decoded RGBA pixels to match the packaged artifacts. The checked-in PNG and release readback are the publication bytes. Cross-environment PNG-container byte equality is not claimed because native renderer packages can encode equivalent raster pixels differently across supported runner environments.
