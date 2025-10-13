# viewtif

A lightweight GeoTIFF viewer for quick visualization directly from the command line.  

You can visualize single-band GeoTIFFs, RGB composites, and shapefile overlays in a simple Qt-based window.

---

## Installation

```bash
pip install viewtif
```

## Quick Start
```bash
# View a GeoTIFF
viewtif examples/sample_data/ECOSTRESS_LST.tif

# View with shapefile overlay
viewtif examples/sample_data/ECOSTRESS_LST.tif \
  --shapefile examples/sample_data/Zip_Codes.shp

# View an RGB composite
viewtif --rgbfiles \
  examples/sample_data/HLS_B4.tif \
  examples/sample_data/HLS_B3.tif \
  examples/sample_data/HLS_B2.tif

```

## Controls
| Key                  | Action                                  |
| -------------------- | --------------------------------------- |
| `+` / `-`            | Zoom in / out                           |
| Arrow keys or `WASD` | Pan                                     |
| `C` / `V`            | Increase / decrease contrast            |
| `G` / `H`            | Increase / decrease gamma               |
| `M`                  | Toggle colormap (`viridis` ↔ `magma`)   |
| `[` / `]`            | Previous / next band (single-band only) |
| `R`                  | Reset view                              |

## Features
- Command-line driven GeoTIFF viewer
- Supports single-band or RGB composite display.
- Optional shapefile overlay for geographic context.
- Adjustable contrast, gamma, and colormap.
- Fast preview using rasterio and PySide6.

## Example Data
- ECOSTRESS_LST.tif
- Zip_Codes.shp and associated files
- HLS_B4.tif, HLS_B3.tif, HLS_B2.tif (RGB sample)

## Credit & License
`viewtif` was inspired by the NASA JPL Thermal Viewer — Semi-Automated Georeferencer (GeoViewer v1.12) developed by Jake Longenecker (University of Miami Rosenstiel School of Marine, Atmospheric & Earth Science) while at the NASA Jet Propulsion Laboratory, California Institute of Technology, with inspiration from JPL’s ECOSTRESS geolocation batch workflow by Andrew Alamillo. The original GeoViewer was released under the MIT License (2025) and may be freely adapted with citation.

# Citation
Longenecker, Jake; Lee, Christine; Hulley, Glynn; Cawse-Nicholson, Kerry; Purkis, Sam; Gleason, Art; Otis, Dan; Galdamez, Illeana; Meiseles, Jacquelyn. GeoViewer v1.12: NASA JPL Thermal Viewer—Semi-Automated Georeferencer User Guide & Reference Manual. Jet Propulsion Laboratory, California Institute of Technology, 2025. PDF.

# License
This project is released under the MIT License.
