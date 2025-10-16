# viewtif
[![Downloads](https://static.pepy.tech/badge/viewtif)](https://pepy.tech/project/viewtif)
[![PyPI version](https://img.shields.io/pypi/v/viewtif)](https://pypi.org/project/viewtif/)
[![Python versions](https://img.shields.io/pypi/pyversions/viewtif)](https://pypi.org/project/viewtif/)

A lightweight GeoTIFF viewer for quick visualization directly from the command line.  

You can visualize single-band GeoTIFFs, RGB composites, and shapefile overlays in a simple Qt-based window.

---

## Installation

```bash
pip install viewtif
```
> **Note:** On Linux, you may need python3-tk, libqt5gui5, or PySide6 dependencies.
> 
>`viewtif` requires a graphical display environment.  
> It may not run properly on headless systems (e.g., HPC compute nodes or remote servers without X11 forwarding).

### Optional features
#### Shapefile overlay support
```bash
pip install "viewtif[geo]"
```
> **Note:** For macOS(zsh) users:
> Make sure to include the quotes, or zsh will interpret it as a pattern.

#### HDF/HDF5 support
```bash
brew install gdal     # macOS
sudo apt install gdal-bin python3-gdal  # Linux
pip install GDAL
```
> **Note:** GDAL is required to open `.hdf`, .`h5`, and `.hdf5` files. If it’s missing, viewtif will display: `RuntimeError: HDF support requires GDAL.`

## Quick Start
```bash
# View a GeoTIFF
viewtif ECOSTRESS_LST.tif

# View an RGB composite
viewtif --rgbfiles \
  HLS_B04.tif \
  HLS_B03.tif \
  HLS_B02.tif

# View with shapefile overlay
viewtif ECOSTRESS_LST.tif \
  --shapefile Zip_Codes.shp
```
### Update in v1.0.6: HDF/HDF5 support
`viewtif` can open `.hdf`, `.h5`, and `.hdf5` files that contain multiple subdatasets. When opened, it lists available subdatasets and lets you view one by index. You can also specify a band to display (default is the first band) or change bands interactively with '[' and ']'.
```bash
# List subdatasets
viewtif AG100.v003.33.-107.0001.h5

# View a specific subdataset
viewtif AG100.v003.33.-107.0001.h5 --subset 1

# View a specific subdataset and band
viewtif AG100.v003.33.-107.0001.h5 --subset 1 --band 3
```
> **Note:** Some datasets (perhaps the majority of .hdf files) lack CRS information encoded, so shapefile overlays may not work. In that case, viewtif will display:
`[WARN] raster lacks CRS/transform; cannot place overlays.`

### Update in v1.0.7: File Geodatabase (.gdb) support
`viewtif` can now open raster datasets stored inside Esri File Geodatabases (`.gdb`), using the GDAL `OpenFileGDB` driver.  
When you open a .gdb directly, `viewtif`` will list available raster datasets first, then you can choose one to view.

```bash
# List available raster datasets
viewtif /path/to/geodatabase.gdb

# Open a specific raster
viewtif "OpenFileGDB:/path/to/geodatabase.gdb:RasterName"
```
> **Note:** Requires GDAL 3.7 or later with the OpenFileGDB driver enabled. If multiple raster datasets are present, viewtif lists them all and shows how to open each. The .gdb path and raster name must be separated by a colon (:).

### Update in v1.0.7: Large raster safeguard
As of v1.0.7, `viewtif` automatically checks the raster size before loading.  
If the dataset is very large (e.g., >20 million pixels), it will pause and warn that loading may freeze your system.  
You can proceed manually or rerun with the `--scale` option for a smaller, faster preview.

## Controls
| Key                  | Action                                  |
| -------------------- | --------------------------------------- |
| `+` / `-` or mouse / trackpad            | Zoom in / out                           |
| Arrow keys or `WASD` | Pan                                     |
| `C` / `V`            | Increase / decrease contrast            |
| `G` / `H`            | Increase / decrease gamma               |
| `M`                  | Toggle colormap (`viridis` ↔ `magma`)   |
| `[` / `]`            | Previous / next band (single-band only) |
| `R`                  | Reset view                              |

## Features
- Command-line driven GeoTIFF viewer.
- Supports single-band, RGB composite, and HDF/HDF5 subdatasets.
- Optional shapefile overlay for geographic context.
- Adjustable contrast, gamma, and colormap.
- Fast preview using rasterio and PySide6.

## Example Data
- ECOSTRESS_LST.tif
- Zip_Codes.shp and associated files
- HLS_B04.tif, HLS_B03.tif, HLS_B02.tif (RGB sample)
- AG100.v003.33.-107.0001.h5 (HDF5 file)

## Credit & License
`viewtif` was inspired by the NASA JPL Thermal Viewer — Semi-Automated Georeferencer (GeoViewer v1.12) developed by Jake Longenecker (University of Miami Rosenstiel School of Marine, Atmospheric & Earth Science) while at the NASA Jet Propulsion Laboratory, California Institute of Technology, with inspiration from JPL’s ECOSTRESS geolocation batch workflow by Andrew Alamillo. The original GeoViewer was released under the MIT License (2025) and may be freely adapted with citation.

## Citation
Longenecker, Jake; Lee, Christine; Hulley, Glynn; Cawse-Nicholson, Kerry; Purkis, Sam; Gleason, Art; Otis, Dan; Galdamez, Ileana; Meiseles, Jacquelyn. GeoViewer v1.12: NASA JPL Thermal Viewer—Semi-Automated Georeferencer User Guide & Reference Manual. Jet Propulsion Laboratory, California Institute of Technology, 2025. PDF.

## License
This project is released under the MIT License.

## Contributors
- [@HarshShinde0](https://github.com/HarshShinde0) — added mouse-wheel and trackpad zoom support
- [@p-vdp](https://github.com/p-vdp) — added File Geodatabase (.gdb) raster support