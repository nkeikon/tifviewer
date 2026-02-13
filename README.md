# viewtif
[![Downloads](https://static.pepy.tech/badge/viewtif)](https://pepy.tech/project/viewtif)
[![PyPI version](https://img.shields.io/pypi/v/viewtif)](https://pypi.org/project/viewtif/)
[![Python version](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://pypi.org/project/viewtif/)

A lightweight GeoTIFF viewer for quick visualization directly from the command line.  

You can visualize single-band GeoTIFFs, RGB composites, HDF, NetCDF files, and raster datasets within Esri File Geodatabases with vector overlays in a simple Qt-based window.

> **Note:** `viewtif` is meant for quick viewing and sanity checks, not for detailed or rigorous analysis. Think of it as a fast preview, not a replacement for full GIS or scientific analysis tools.

## Installation

```bash
pip install viewtif
```
> **Note:** Requires Python 3.10 or higher. On Linux, you may need python3-tk, libqt5gui5, or PySide6 dependencies.
> 
>`viewtif` requires a graphical display environment.  
> It may not run properly on headless systems (e.g., HPC compute nodes or remote servers without X11 forwarding).

### Optional dependencies

#### Vector overlay support
```bash
pip install "viewtif[geo]"
```
> **Note:** For macOS(zsh) users:
> Make sure to include the quotes, or zsh will interpret it as a pattern.

#### NetCDF support 
```bash
pip install "viewtif[netcdf]"
```
> **Note:** For enhanced geographic visualization with map projections, coastlines, and borders, install with cartopy: `pip install "viewtif[netcdf]"` (cartopy is included in the netcdf extra). If cartopy is not available, netCDF files will still display with standard rendering.

#### HDF/HDF5 & FileGDB support 
GDAL is required to open `.hdf`, `.h5`, `.hdf5`, and `.gdb` files. 
```bash
brew install gdal     # macOS
sudo apt install gdal-bin python3-gdal  # Linux
pip install GDAL
```

## Quick Start
```bash
# View a GeoTIFF
viewtif ECOSTRESS_LST.tif

# View with vector overlay
viewtif ECOSTRESS_LST.tif --shapefile Zip_Codes.shp

# View RGB composite
viewtif --rgbfiles red.tif green.tif blue.tif
```
## File Format Support
### GeoTIFF
`viewtif` supports single-band and multi-band GeoTIFF files. You can visualize individual bands or create RGB composites.

```bash
# View single band
viewtif myfile.tif

# View specific band from multi-band file
viewtif myfile.tif --band 3

# Create RGB composite from multi-band file
viewtif rgb.tif --rgb 4 3 2

# Create RGB composite from separate files
viewtif --rgbfiles B4.tif B3.tif B2.tif
```

### HDF/HDF5
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

### NetCDF
`viewtif` supports NetCDF (`.nc`) files via xarray, with optional cartopy-based geographic visualization. Use `[`  /  `]` to move forward or backward through time steps, and press M to switch between the three built-in colormaps ("RdBu_r", "viridis", "magma").

#### Multi-variable NetCDF files
If a NetCDF file contains multiple variables, viewtif will list them:
```bash
viewtif data.nc
# Found 3 variables in data.nc:
# [0] temperature
# [1] pressure  
# [2] humidity

viewtif data.nc --subset 1  # Opens pressure
```
> **Note:** `viewtif` supports 2D (lat, lon) or 3D (time, lat, lon) NetCDF data. Variables with additional dimensions (e.g., vertical levels) are not currently supported (as of v0.2.7).

#### NetCDF visualization controls
- `--vmin [value] --vmax [value]` let you fix the display range (for all file types). Without this, the range adjusts to each band/timestep's min/max values.
- `--timestep [int]` lets you jump directly to a chosen time slice.
- `--cartopy off` let you see raw NetCDF images.

```bash
viewtif data.nc
viewtif data.nc --vmin 280 --vmax 320
viewtif data.nc --timestep 100
viewtif data.nc --cartopy off
```

### File Geodatabase (.gdb)
`viewtif` can open raster datasets stored inside Esri File Geodatabases (`.gdb`). When you open a .gdb directly, `viewtif` will list available raster datasets first, then you can choose one to view.

```bash
# List available raster datasets
viewtif /path/to/geodatabase.gdb

# Open a specific raster
viewtif "OpenFileGDB:/path/to/geodatabase.gdb:RasterName"
```

> **Note:** If multiple raster datasets are present, viewtif lists them all and shows how to open each. The .gdb path and raster name must be separated by a colon (:).

### Remote Files (Cloud Storage and URLs)
`viewtif` can open GeoTIFF files directly from remote storage without downloading:
```bash
# HTTPS/HTTP URLs
viewtif https://example.com/data/file.tif

# AWS S3 (public bucket)
export AWS_NO_SIGN_REQUEST=YES
viewtif s3://bucket-name/path/to/file.tif

# RGB composites from remote files with local overlay
viewtif --rgbfiles \
  s3://bucket/red.tif \
  s3://bucket/green.tif \
  s3://bucket/blue.tif \
  --shapefile us-states.json
```
> **Note:** Remote file support uses GDAL's virtual file system drivers. For AWS S3 public buckets, set `AWS_NO_SIGN_REQUEST=YES`. For private buckets, configure credentials via `~/.aws/credentials` or environment variables. Other cloud providers (Google Cloud Storage, Azure Blob Storage) are also supported via GDAL's virtual file system paths (`/vsigs/`, `/vsiaz/`).

## Features
### Vector Overlays
Optional vector overlay (shapefile, GeoJSON, GeoPackage, etc.) for geographic context. Vector files are automatically reprojected to match the raster CRS.

```bash
# Single overlay
viewtif ECOSTRESS_LST.tif --shapefile Zip_Codes.shp

# Multiple overlays with mixed formats
viewtif ECOSTRESS_LST.tif \
  --shapefile boundaries.geojson \
  --shapefile roads.shp

# Customize appearance
viewtif ECOSTRESS_LST.tif \
  --shapefile Zip_Codes.shp --shp-color red --shp-width 2
```
### Automatic Nodata Masking
`viewtif` automatically detects and masks nodata values from file metadata, ensuring proper visualization by excluding invalid pixels. Use `--nodata` to override or specify nodata values for files with missing or incorrect metadata.
```bash
# Specify nodata value
viewtif image.tif --nodata -9999
viewtif data.hdf --subset 0 --nodata 0
```

> **Note:** The `--nodata` flag works with GeoTIFF, HDF, and .gdb files. NetCDF files use xarray's automatic fill value handling.

### Basemap
Natural Earth basemap toggle (press 'B') for geographic reference. For incompatible projections (UTM, custom CRS), shows location info (continent, coordinates) instead of basemap.

### Export to QGIS
`viewtif` can export GeoTIFF, HDF, and File Geodatabase rasters directly to QGIS for further analysis. Use the `--qgis` flag to automatically export and open the file in QGIS.

```bash
# Open a GeoTIFF in QGIS
viewtif sample.tif --qgis

# Open an HDF subdataset in QGIS
viewtif data.hdf --subset 0 --qgis

# Open a File Geodatabase raster in QGIS
viewtif "OpenFileGDB:mydata.gdb:Elevation" --qgis

# Open a remote file in QGIS
viewtif https://example.com/data/file.tif --qgis
viewtif s3://bucket/path/to/file.tif --qgis
```

> **Note:** The `--qgis` flag opens files in QGIS. Creates temporary GeoTIFF files for HDF, .gdb, and remote files (remain until reboot). Local GeoTIFF files open directly.  Flags like `--shapefile`, `--scale`, `--vmin/--vmax`, `--band`, and `--nodata` are ignored when using `--qgis`.

> **Note:** NetCDF files are not supported with `--qgis`.

### Performance
`viewtif` automatically checks raster size before loading. If a dataset is very large (>20 million pixels), it displays a warning and prompts for confirmation, since loading may freeze your system or consume excessive memory. 

To view large files safely, use the `--scale` option to downsample the data:
```bash
# Downsample by factor of 10 (loads 1/100th of the pixels)
viewtif large_file.tif --scale 10

# Downsample by factor of 5 (loads 1/25th of the pixels)
viewtif large_file.tif --scale 5
```

The `--scale N` option reduces both width and height by factor N, loading 1/N² of the total pixels. Note that size warnings are skipped for remote files (URLs, S3, cloud storage).

### Interactive Controls
Adjustable contrast, gamma, and colormap. Keyboard shortcuts are displayed in the status bar at the bottom of the window.

| Key                  | Action                                  |
| -------------------- | --------------------------------------- |
| `+` / `-` or mouse / trackpad            | Zoom in / out                           |
| Arrow keys or `WASD` | Pan                                     |
| `C` / `V`            | Increase / decrease contrast            |
| `G` / `H`            | Increase / decrease gamma               |
| `M`                  | Toggle colormap. Single-band: viridis/magma. NetCDF: RdBu_r/viridis/magma.   |
| `[` / `]`            | Previous / next band (or time step)     |
| `B`                  | Toggle basemap (Natural Earth country boundaries) |
| `R`                  | Reset view                              |

### Command-Line Options
For a complete list, run `viewtif --help`. Key options:

| Option | Description |
|--------|-------------|
| `--band N` | Select band from multi-band file |
| `--rgb R G B` | Create RGB composite from bands |
| `--rgbfiles R G B` | Create RGB from three files |
| `--subset N` | Select HDF/NetCDF subdataset (0-based) |
| `--scale N` | Downsample by factor N (loads 1/N² pixels) |
| `--vmin X --vmax Y` | Set display value range |
| `--nodata VALUE` | Mask pixels with specific nodata value |
| `--shapefile FILE` | Add vector overlay (repeatable) |
| `--shp-color COLOR` | Overlay color (default: cyan) |
| `--shp-width N` | Overlay line width (default: 1.0) |
| `--timestep N` | Jump to NetCDF time index (1-based) |
| `--cartopy on\|off` | Toggle cartopy for NetCDF (default: on) |
| `--qgis` | Export and open in QGIS |

### Limitations
- **NetCDF files:** Do not support vector overlays, basemap toggle, or QGIS export. NetCDF files use cartopy visualization which handles geographic display differently.
- **HDF files without georeferencing:** Vector overlays and basemap may not work if CRS/transform information is missing.
- **Remote files:** Large raster size warnings are skipped for remote files. Vector overlay files must be local (though the raster itself can be remote). NetCDF files cannot be opened directly from HTTP/S3 URLs due to xarray/netCDF4 library limitations. HDF and File Geodatabase support for remote files is not guaranteed.

## Example Data
- ECOSTRESS_LST.tif
- Zip_Codes.shp and associated files
- HLS_B04.tif, HLS_B03.tif, HLS_B02.tif (RGB sample)
- AG100.v003.33.-107.0001.h5 (HDF5 file)
- [tasmax_mon_ACCESS-CM2_ssp370_r1i1p1f1_gn_2021.nc](https://data.nas.nasa.gov/nex-dcp30-cmip6/monthly/ACCESS-CM2/ssp370/r1i1p1f1/tasmax/tasmax_mon_ACCESS-CM2_ssp370_r1i1p1f1_gn_2021.nc)
- [tas_Amon_KIOST-ESM_ssp585_r1i1p1f1_gr1_201501-210012.nc](https://dap.ceda.ac.uk/badc/cmip6/data/CMIP6/ScenarioMIP/KIOST/KIOST-ESM/ssp585/r1i1p1f1/Amon/tas/gr1/v20191106/tas_Amon_KIOST-ESM_ssp585_r1i1p1f1_gr1_201501-210012.nc)

## Need help?

You can ask questions about usage via the documentation-based assistant:

👉 [Ask the viewtif + viewgeom + viewinline Helper](https://chatgpt.com/g/g-698b61c42f788191b884aed1b99dfcd8-viewtif-viewgeom-viewinline-helper)

👉 For NASA staff: find 'viewtif + viewgeom + viewinline Helper' via the ChatGSFC Agent Marketplace


## Credit & License
`viewtif` was inspired by the NASA JPL Thermal Viewer — Semi-Automated Georeferencer (GeoViewer v1.12) developed by Jake Longenecker (University of Miami Rosenstiel School of Marine, Atmospheric & Earth Science) while at the NASA Jet Propulsion Laboratory, California Institute of Technology, with inspiration from JPL's ECOSTRESS geolocation batch workflow by Andrew Alamillo. The original GeoViewer was released under the MIT License (2025) and may be freely adapted with citation.

## Citation
Longenecker, Jake; Lee, Christine; Hulley, Glynn; Cawse-Nicholson, Kerry; Gleason, Art; Otis, Dan; Galdamez, Ileana; Meiseles, Jacquelyn. GeoViewer v1.12: NASA JPL Thermal Viewer—Semi-Automated Georeferencer User Guide & Reference Manual. Jet Propulsion Laboratory, California Institute of Technology, 2025. PDF.

## Contributors
- [@HarshShinde0](https://github.com/HarshShinde0) — added mouse-wheel and trackpad zoom support; added NetCDF support with [@nkeikon](https://github.com/nkeikon) 
- [@p-vdp](https://github.com/p-vdp) — added File Geodatabase (.gdb) raster support

## License
This project is released under the MIT License © 2025 Keiko Nomura.

If you find this tool useful, please consider supporting or acknowledging the author. 

## Useful links
- [YouTube demo playlist](https://www.youtube.com/playlist?list=PLP9MNCMgJIHj6FvahJ6Tembp1rCyhLtR4)
- [Demo V0.2.7 as of Feb 2026](https://www.linkedin.com/posts/keiko-nomura-0231891_dont-you-sometimes-just-want-to-see-a-tif-activity-7425056949953814529-_6Xo?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAA0INsBVIO1f6nS_NkKqFh4Na1ZpoYo2fc)
- [Demo by User](https://www.linkedin.com/posts/desmond-lartey_geospatial-analysts-heads-up-check-out-activity-7386336488050925568-G5bN?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAA0INsBVIO1f6nS_NkKqFh4Na1ZpoYo2fc)
- [Demo at the initial release](https://www.linkedin.com/posts/keiko-nomura-0231891_dont-you-sometimes-just-want-to-see-a-activity-7383409138296528896-sbRM?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAA0INsBVIO1f6nS_NkKqFh4Na1ZpoYo2fc)
- [Demo on HDF files](https://www.linkedin.com/posts/keiko-nomura-0231891_now-you-can-see-hdf-files-from-the-command-activity-7383963721561399296-F5i0?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAA0INsBVIO1f6nS_NkKqFh4Na1ZpoYo2fc)
- [Demo on NetCDF files](https://www.linkedin.com/posts/keiko-nomura-0231891_you-can-now-view-netcdf-files-nc-from-activity-7386189562072670208-3A0V?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAA0INsBVIO1f6nS_NkKqFh4Na1ZpoYo2fc)
- [Demo on Esri Geodatabases](https://www.linkedin.com/posts/keiko-nomura-0231891_did-you-know-viewtif-now-supports-esri-geodatabases-activity-7390165568688959488-pvpk?utm_source=share&utm_medium=member_desktop&rcm=ACoAAAA0INsBVIO1f6nS_NkKqFh4Na1ZpoYo2fc)
