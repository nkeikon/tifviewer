#!/usr/bin/env python3
"""
TIFF Viewer (PySide6) – view GeoTIFF, NetCDF, HDF, and File Geodatabase with vector overlays.

Features:
- Open GeoTIFFs (single or multi-band)
- Combine separate single-band TIFFs into RGB
- Apply global 2–98% stretch for RGB
- Display NetCDF/HDF subsets with consistent scaling
- Identify and display raster from File Geodatabase if any
- Overlay vector files automatically reprojected to raster CRS
- Navigate bands/time steps interactively
- Remote file support: open files directly from HTTP/HTTPS URLs, S3, Google Cloud Storage, and Azure Blob Storage.

Controls
  + / - : zoom in/out
  Arrow keys or WASD : pan
  C / V : increase/decrease contrast (works in RGB and single-band)
  G / H : increase/decrease gamma    (works in RGB and single-band)
  M     : toggle colormap. Single-band: viridis/magma. NetCDF: RdBu_r/viridis/magma.
  [ / ] : previous / next band (or time step)
  B     : toggle basemap (Natural Earth country boundaries)
  R     : reset view

Examples
  python tiff_viewer.py my.tif --band 1
  python tiff_viewer.py my_multiband.tif --rgb 4 3 2
  python tiff_viewer.py --rgbfiles B4.tif B3.tif B2.tif --shapefile coast.shp counties.shp --shp-color cyan --shp-width 1.8
"""

import sys
import os
import numpy as np
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, 
    QScrollBar, QGraphicsPathItem, QVBoxLayout, QWidget, QStatusBar
)
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QPainterPath
from PySide6.QtCore import Qt

__version__ = "0.2.7"

# Lazy-loaded heavy imports
_rasterio = None
_cm = None
_gpd = None
_shapely_geoms = None

def _get_rasterio():
    """Lazy-load rasterio (slow: ~0.5-1s)"""
    global _rasterio
    if _rasterio is None:
        import rasterio
        from rasterio.transform import Affine
        import warnings
        warnings.filterwarnings("ignore", category=UserWarning, module="rasterio")
        warnings.filterwarnings("ignore", category=FutureWarning, module="osgeo")
        _rasterio = rasterio
        # Store Affine in the module for easy access
        _rasterio.Affine = Affine
    return _rasterio

def _get_matplotlib_cm():
    """Lazy-load matplotlib colormap (slow: ~0.3-0.5s)"""
    global _cm
    if _cm is None:
        import matplotlib.cm as cm
        _cm = cm
    return _cm

def _get_geopandas():
    """Lazy-load geopandas (slow: ~1-2s)"""
    global _gpd, _shapely_geoms
    if _gpd is None:
        try:
            import geopandas as gpd
            from shapely.geometry import (
                LineString, MultiLineString, Polygon, MultiPolygon,
                GeometryCollection, Point, MultiPoint
            )
            import warnings
            warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")
            _gpd = gpd
            _shapely_geoms = {
                'LineString': LineString,
                'MultiLineString': MultiLineString,
                'Polygon': Polygon,
                'MultiPolygon': MultiPolygon,
                'GeometryCollection': GeometryCollection,
                'Point': Point,
                'MultiPoint': MultiPoint
            }
        except ImportError:
            _gpd = None
            _shapely_geoms = None
    return _gpd, _shapely_geoms

# Check availability without importing
HAVE_GEO = True  # Assume available, will be set False if import fails
try:
    import importlib.util
    HAVE_CARTOPY = importlib.util.find_spec("cartopy") is not None
except Exception:
    HAVE_CARTOPY = False

# Optional NetCDF deps (lazy-loaded when needed)
HAVE_NETCDF = False
xr = None
pd = None

def warn_if_large(tif_path, scale=1):
    """Warn and confirm before loading very large rasters (GeoTIFF, GDB, or HDF).    
    Uses GDAL if available, falls back to rasterio for standard formats.
    """
    # Skip size check for URLs, S3, and remote paths (can't reliably check remote file size)
    if tif_path and tif_path.startswith(("http://", "https://", "s3://", "/vsi")):
        return
    
    rasterio = _get_rasterio()
    import os
    width = height = None
    size_mb = None

    if tif_path and os.path.dirname(tif_path).endswith(".gdb"):
        tif_path = f"OpenFileGDB:{os.path.dirname(tif_path)}:{os.path.basename(tif_path)}"

    try:
        width, height = None, None
        
        # Try GDAL first (supports more formats including GDB, HDF)
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            info = gdal.Info(tif_path, format="json")
            width, height = info.get("size", [0, 0])
        except ImportError:
            # GDAL not available, try rasterio for standard formats
            try:
                with rasterio.open(tif_path) as src:
                    width = src.width
                    height = src.height
            except Exception:
                # If rasterio also fails, skip the check
                print(f"[INFO] Could not determine raster dimensions for size check.")
                return
        
        if width and height:
            total_pixels = (width * height) / (scale ** 2)  # account for downsampling
            size_mb = None
            if os.path.exists(tif_path):
                size_mb = os.path.getsize(tif_path) / (1024 ** 2)

            # Only warn if the *effective* pixels remain large
            if total_pixels > 20_000_000 and scale <= 5:
                print(
                    f"[WARN] Large raster detected ({width}×{height}, ~{total_pixels/1e6:.1f}M effective pixels"
                    + (f", ~{size_mb:.1f} MB" if size_mb else "")
                    + "). Loading may freeze. Consider rerunning with --scale (e.g. --scale 10)."
                )
                ans = input("Proceed anyway? [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("Cancelled.")
                    sys.exit(0)

    except Exception as e:
        print(f"[INFO] Could not pre-check raster size: {e}")

# -------------------------- QGraphicsView tweaks -------------------------- #
class RasterView(QGraphicsView):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._wheel_zoom_step = 1.2

    def wheelEvent(self, event):
        """Zoom in/out centered at the cursor position.

        Uses a multiplicative scale per 15° wheel step.
        """
        delta = event.angleDelta().y()
        if delta == 0:
            # Trackpads may report pixelDelta; fall back to it if angleDelta is 0
            pixel_delta = event.pixelDelta().y()
            delta = pixel_delta

        if delta == 0:
            event.ignore()
            return

        steps = delta / 120.0  # 120 units per 15° step
        if steps > 0:
            factor = self._wheel_zoom_step ** steps
        else:
            factor = (1.0 / self._wheel_zoom_step) ** (-steps)

        self.scale(factor, factor)
        event.accept()


# ------------------------------- Main Window ------------------------------ #
class TiffViewer(QMainWindow):
    def __init__(
        self,
        tif_path,
        scale=1,
        band=1,
        rgb=None,
        rgbfiles=None,
        shapefiles=None,
        shp_color="cyan",
        shp_width=2,
        subset=None,
        vmin=None,
        vmax=None,
        cartopy="on",
        timestep=None,
        nodata=None, 
    ):
        super().__init__()

        self.tif_path = tif_path or ""
        self.rgb_mode = rgb is not None or rgbfiles is not None
        self.band = int(band)
        self.rgb = rgb
        self.rgbfiles = rgbfiles
        self._user_vmin = vmin
        self._user_vmax = vmax
        self.cartopy_mode = cartopy.lower()
        self._nodata = nodata

        if not tif_path and not rgbfiles:
           print("Usage: viewtif <file.tif>")
           sys.exit(1)

        # Check if file exists (skip for URLs)
        if tif_path and not tif_path.startswith(("http://", "https://", "s3://", "/vsi")):
            # Extract actual file path from GDAL format strings
            check_path = tif_path
            if tif_path.startswith("OpenFileGDB:"):
                # OpenFileGDB:path.gdb:layer -> path.gdb
                parts = tif_path.split(":")
                if len(parts) >= 2:
                    check_path = parts[1]
            elif tif_path.startswith(("HDF4_EOS:", "HDF5:")):
                # HDF format strings - extract file path
                parts = tif_path.split(":")
                if len(parts) >= 2:
                    check_path = parts[1]
            
            if not os.path.exists(check_path):
                print(f"[ERROR] File not found: {check_path}")
                sys.exit(1)

        # Load rasterio early since we'll need it
        rasterio = _get_rasterio()
        Affine = rasterio.Affine

        self._scale_arg = max(1, int(scale or 1))
        self._transform = None
        self._crs = None

        # Overlay config/state
        self._shapefiles = shapefiles or []
        self._shp_color = shp_color
        self._shp_width = float(shp_width)
        self._overlay_items: list[QGraphicsPathItem] = []
        
        # Basemap state
        self.base_gdf = None
        self.basemap_items: list[QGraphicsPathItem] = []

        # --- Load data ---
        if rgbfiles:
            # Check if all RGB files exist (skip for remote paths)
            for f in rgbfiles:
                if not f.startswith(("http://", "https://", "s3://", "/vsi")) and not os.path.exists(f):
                    print(f"[ERROR] File not found: {f}")
                    sys.exit(1)
            
            red, green, blue = rgbfiles
            with rasterio.open(red) as r, rasterio.open(green) as g, rasterio.open(blue) as b:
                if (r.width, r.height) != (g.width, g.height) or (r.width, r.height) != (b.width, b.height):
                    raise ValueError("All RGB files must have the same dimensions.")
                arr = np.stack([
                    r.read(1, out_shape=(r.height // self._scale_arg, r.width // self._scale_arg)),
                    g.read(1, out_shape=(g.height // self._scale_arg, g.width // self._scale_arg)),
                    b.read(1, out_shape=(b.height // self._scale_arg, b.width // self._scale_arg))
                ], axis=-1).astype(np.float32)

                # Apply nodata mask if specified
                if self._nodata is not None:
                    arr = np.where(arr == self._nodata, np.nan, arr)

                self._transform = r.transform
                self._crs = r.crs

            self.data = arr
            self.band_count = 3
            # Extract filenames from paths (works for both local and remote)
            self.rgb = [f.split('/')[-1] for f in [red, green, blue]]
            self.tif_path = self.tif_path or red

        elif tif_path:

            # ---------------- Handle File Geodatabase (.gdb) ---------------- #
            if tif_path.lower().endswith(".gdb") and ":" not in tif_path:
                
                import re, subprocess
                gdb_path = tif_path

                try:
                    out = subprocess.check_output(
                        ["gdalinfo", "-norat", gdb_path],
                        text=True
                    )
                    rasters = re.findall(r"RASTER_DATASET=(\S+)", out)

                    if not rasters:
                        print(f"[WARN] No raster datasets found in {os.path.basename(gdb_path)}.")
                        sys.exit(0)

                    print(f"Found {len(rasters)} raster dataset{'s' if len(rasters) > 1 else ''}:")
                    for i, r in enumerate(rasters):
                        print(f"[{i}] {r}")

                    print("\nUse one of these names to open. For example, to open the first raster:")
                    print(f'viewtif "OpenFileGDB:{gdb_path}:{rasters[0]}"')
                    sys.exit(0)

                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    print("[ERROR] This file requires full GDAL support.")
                    sys.exit(1)

            # Warn for large files
            warn_if_large(tif_path, scale=self._scale_arg)

            # ---------------------------------------------------------------
            # Detect NetCDF
            # ---------------------------------------------------------------
            if tif_path.lower().endswith((".nc", ".netcdf")):
                    try:
                        import xarray as xr
                        import warnings
                        warnings.filterwarnings("ignore", category=xr.SerializationWarning)
                    except ModuleNotFoundError:
                        print("NetCDF support requires extra dependencies.")
                        print("Install them with: pip install viewtif[netcdf]")
                        sys.exit(0)

                    # Open the NetCDF file
                    ds = xr.open_dataset(tif_path)
                    
                    # List variables, filtering out boundary variables (ending with _bnds)
                    all_vars = list(ds.data_vars)
                    data_vars = [var for var in all_vars if not var.endswith('_bnds')]
                    
                    # Auto-select the first variable if there's only one and no subset specified
                    if len(data_vars) == 1 and subset is None:
                        subset = 0
                    # List variables if --subset not given and multiple variables exist
                    elif subset is None:
                        print(f"Found {len(data_vars)} variables in {os.path.basename(tif_path)}:")
                        for i, var in enumerate(data_vars):
                            print(f"[{i}] {var}")
                        print("\nUse --subset N to open a specific variable.")
                        sys.exit(0)
                    
                    # Validate subset index
                    if subset < 0 or subset >= len(data_vars):
                        raise ValueError(f"Invalid variable index {subset}. Valid range: 0–{len(data_vars)-1}")
                    
                    # Get the selected variable from filtered data_vars
                    var_name = data_vars[subset]
                    var_data = ds[var_name]
                    
                    # Store original dataset and variable information for better visualization
                    self._nc_dataset = ds
                    self._nc_var_name = var_name
                    self._nc_var_data = var_data
                    
                    # Get coordinate info if available
                    self._has_geo_coords = False
                    if "lon" in ds.coords and "lat" in ds.coords:
                        self._has_geo_coords = True
                        self._lon_data = ds.lon.values
                        self._lat_data = ds.lat.values
                    elif "longitude" in ds.coords and "latitude" in ds.coords:
                        self._has_geo_coords = True
                        self._lon_data = ds.longitude.values
                        self._lat_data = ds.latitude.values
                    
                    # Handle time or other index dimension if present
                    self._has_time_dim = False
                    self._time_dim_name = None
                    
                    # Look for a time dimension first
                    if 'time' in var_data.dims:
                        self._has_time_dim = True
                        self._time_dim_name = "time"
                        self._time_values = ds["time"].values
                        self._time_index = 0
                        print(f"NetCDF time dimension detected: {len(self._time_values)} steps")
                        self.band_count = var_data.sizes["time"]
                        self.band_index = 0
                        var_data = var_data.isel(time=0)

                    elif len(var_data.dims) > 2:
                        # Try to find a dimension that's not lat/lon
                        spatial_dims = ['lat', 'lon', 'latitude', 'longitude', 'y', 'x']
                        for dim in var_data.dims:
                            if dim not in spatial_dims:
                                self._has_time_dim = True
                                self._time_dim_name = dim
                                self._time_values = ds[dim].values
                                self._time_index = 0
                                var_data = var_data.isel({dim: 0})
                                break

                    arr = var_data.values.astype(np.float32)
                    arr = np.squeeze(arr)

                    # Check if variable has unsupported dimensions (e.g., vertical levels)
                    spatial_dims = ['lat', 'lon', 'latitude', 'longitude', 'y', 'x']
                    time_dims = ['time']

                    # Count spatial and time dimensions
                    spatial_count = sum(1 for d in var_data.dims if d in spatial_dims)
                    time_count = sum(1 for d in var_data.dims if d in time_dims)
                    total_dims = len(var_data.dims)

                    # Valid: 2 spatial dims, or 1 time + 2 spatial dims
                    is_valid = (total_dims == 2 and spatial_count == 2) or \
                               (total_dims == 3 and time_count == 1 and spatial_count == 2)

                    if not is_valid:
                        print(f"[ERROR] Variable has unsupported dimensions: {list(var_data.dims)}")
                        print(f"[INFO] viewtif only supports 2D (lat, lon) or 3D (time, lat, lon) NetCDF data")
                        sys.exit(1)

                    # --------------------------------------------------------
                    # Apply timestep jump after base array is created
                    # --------------------------------------------------------
                    if timestep is not None and self._has_time_dim:
                        ts = max(1, min(timestep, self.band_count))
                        self.band_index = ts - 1
                        print(f"[INFO] Jumping to timestep {ts}/{self.band_count}")

                        # Replace arr with the correct slice
                        frame = self._nc_var_data.isel({self._time_dim_name: self.band_index})
                        arr = np.squeeze(frame.values.astype(np.float32))

                    if arr.ndim >= 2:
                        h, w = arr.shape[:2]
                        if h * w > 4_000_000:
                            step = max(2, int((h * w / 4_000_000) ** 0.5))
                            arr = arr[::step, ::step]

                    self.data = arr
                    
                    # Try to extract CRS from CF conventions
                    self._transform = None
                    self._crs = None

                    if "crs" in ds.variables:
                        try:
                            crs_var = ds.variables["crs"]
                            if hasattr(crs_var, "spatial_ref"):
                                self._crs = rasterio.crs.CRS.from_wkt(crs_var.spatial_ref)
                        except Exception as e:
                            print(f"Could not parse CRS: {e}")

                    # Preserve time dimension if detected earlier
                    if not self._has_time_dim:
                        self.band_count = 1
                        self.band_index = 0

                    self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)

                    if self._user_vmin is not None:
                        self.vmin = self._user_vmin
                    if self._user_vmax is not None:
                        self.vmax = self._user_vmax

                    self._use_cartopy = HAVE_CARTOPY and self._has_geo_coords

            # ---------------------------------------------------------------
            # Detect HDF or HDF5
            # ---------------------------------------------------------------
            elif tif_path.lower().endswith((".hdf", ".h5", ".hdf5")):
                try:
                    from osgeo import gdal
                    # gdal.UseExceptions()

                    ds = gdal.Open(tif_path)
                    subs = ds.GetSubDatasets()

                    if not subs:
                        raise ValueError("No subdatasets found in HDF file.")

                    # Only list subsets if --subset not given
                    if subset is None:
                        print(f"Found {len(subs)} subdatasets in {os.path.basename(tif_path)}:")
                        for i, (_, desc) in enumerate(subs):
                            print(f"[{i}] {desc}")
                        print("\nUse --subset N to open a specific subdataset.")
                        sys.exit(0)

                    # Validate subset index
                    if subset < 0 or subset >= len(subs):
                        raise ValueError(f"Invalid subset index {subset}.")

                    sub_name, desc = subs[subset]
                    print(f"\nOpening subdataset [{subset}]: {desc}")
                    sub_ds = gdal.Open(sub_name)

                    arr = sub_ds.ReadAsArray().astype(np.float32)
                    arr = np.squeeze(arr)

                    # -------------------------------
                    # Apply nodata masking (HDF)
                    # -------------------------------
                    if self._nodata is not None:
                        arr[arr == self._nodata] = np.nan

                    # Try dataset-provided nodata as well
                    try:
                        band = sub_ds.GetRasterBand(1)
                        ds_nodata = band.GetNoDataValue()
                        if ds_nodata is not None:
                            arr[arr == ds_nodata] = np.nan
                    except Exception:
                        pass

                    if arr.ndim == 3:
                        # Convert from (bands, rows, cols) → (rows, cols, bands)
                        arr = np.transpose(arr, (1, 2, 0))
                        #print(f"Transposed to {arr.shape} (rows, cols, bands)")
                    elif arr.ndim == 2:
                        print("Single-band dataset.")
                    else:
                        raise ValueError(f"Unexpected array shape {arr.shape}")

                    # --- Downsample large arrays for responsiveness ---
                    h, w = arr.shape[:2]
                    if h * w > 4_000_000:
                        step = max(2, int((h * w / 4_000_000) ** 0.5))
                        arr = arr[::step, ::step] if arr.ndim == 2 else arr[::step, ::step, :]

                    self.data = arr
                    self._transform = None
                    self._crs = None
                    self.band_count = arr.shape[2] if arr.ndim == 3 else 1
                    self.band_index = 0
                    self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)
                    if getattr(self, "_scale_arg", 1) > 1:
                        print(f"[INFO] Value range (scaled): {self.vmin:.3f} -> {self.vmax:.3f}")
                    else:
                        print(f"[INFO] Value range: {self.vmin:.3f} -> {self.vmax:.3f}")

                except ImportError as e:
                    if "osgeo" in str(e):
                        print("[ERROR] This file requires full GDAL support.")
                        # print("Install GDAL with:")
                        # print("  conda install -c conda-forge gdal")
                        sys.exit(1)
                    else:
                        print(f"Error reading HDF file: {e}")
                        sys.exit(1)

                except Exception as e:
                    print(f"Error reading HDF file: {e}")
                    sys.exit(1)

            # ---------------------------------------------------------------
            # Regular TIFF
            # ---------------------------------------------------------------
            else:
                with rasterio.open(tif_path) as src:
                    self._transform = src.transform
                    self._crs = src.crs

                    if rgb is not None:
                        bands = [
                            src.read(b, out_shape=(src.height // self._scale_arg, src.width // self._scale_arg))
                            for b in rgb
                        ]
                        
                        arr = np.stack(bands, axis=-1).astype(np.float32)
                        
                        # Apply user-specified nodata first
                        if self._nodata is not None:
                            arr[arr == self._nodata] = np.nan
                        
                        # Then apply file's nodata if present
                        nd = src.nodata
                        if nd is not None:
                            arr[arr == nd] = np.nan
                        
                        self.data = arr
                        self.band_count = 3
                    else:
                        arr = src.read(
                            self.band,
                            out_shape=(src.height // self._scale_arg, src.width // self._scale_arg)
                        ).astype(np.float32)

                        # Apply user-specified nodata first
                        if self._nodata is not None:
                            arr[arr == self._nodata] = np.nan

                        # Then apply file's nodata if present
                        nd = src.nodata
                        if nd is not None:
                            arr[arr == nd] = np.nan

                        self.data = arr

                        self.band_count = src.count

                        if self.band_count == 1:
                            print("[INFO] This TIFF has 1 band.")
                        else:
                            print(
                                f"[INFO] This TIFF has {self.band_count} bands. "
                                "Use [ and ] to switch bands, or use --rgb R G B."
                            )

                        try:
                            stats = src.stats(self.band)
                            if stats and stats.min is not None and stats.max is not None:
                                self.vmin, self.vmax = stats.min, stats.max
                            else:
                                raise ValueError("No stats in file")
                        except Exception:
                            # Always calculate from masked array for consistency
                            self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)
                            if getattr(self, "_scale_arg", 1) > 1:
                                print(f"[INFO] Value range (scaled): {self.vmin:.3f} -> {self.vmax:.3f}")
                            else:
                                print(f"[INFO] Value range: {self.vmin:.3f} -> {self.vmax:.3f}")

        # Window title
        self.update_title()

        # State
        self.contrast = 1.0
        self.gamma = 1.0

        # Colormap (single-band)
        if tif_path and tif_path.lower().endswith(('.nc', '.netcdf')):
            self.cmap_names = ["RdBu_r", "viridis", "magma"]  # three colormaps for NetCDF
            self.cmap_index = 0  # start with RdBu_r
            self.cmap_name = self.cmap_names[self.cmap_index]
        else:
            self.cmap_name = "viridis"
            self.alt_cmap_name = "magma"  # toggle with M in single-band

        self.zoom_step = 1.2
        self.pan_step = 80

        # Create main widget and layout
        self.main_widget = QWidget()
        self.main_layout = QVBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # Scene + view
        self.scene = QGraphicsScene(self)
        self.view = RasterView(self.scene, self)
        self.main_layout.addWidget(self.view)
        
        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Keys: +/- zoom | C/V contrast | G/H gamma | M colormap | [/] bands or timestep | B basemap | R reset")
        
        # Set central widget
        self.setCentralWidget(self.main_widget)

        self.pixmap_item = None
        self._last_rgb = None

        # --- Initial render ---
        self._suppress_scale_print = True # Need for NetCDF
        self.update_pixmap()
        self._suppress_scale_print = False # Need for NetCDF

        # Overlays (if any)
        if self._shapefiles:
            self._add_shapefile_overlays()

        self.resize(1200, 800)

        if self.pixmap_item is not None:
            rect = self.pixmap_item.boundingRect()
            self.scene.setSceneRect(rect)

            # Fit first
            self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatioByExpanding)

            # ----------------------------
            # NetCDF needs a different scaling (appears smaller)
            # ----------------------------
            if hasattr(self, "_nc_var_name"):
                # NetCDF view adjustment
                self.view.scale(11.0, 11.0)
            else:
                # Default behavior for TIFF/HDF imagery
                self.view.scale(7.0, 7.0)

            self.view.centerOn(self.pixmap_item)
            
        # Previous version below
        # # --- Initial render ---
        # self.update_pixmap()
        # self.resize(1200, 800) 
        # if self.pixmap_item is not None:
        #     rect = self.pixmap_item.boundingRect()
        #     self.scene.setSceneRect(rect)
        #     self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        #     self.view.scale(5, 5)
        #     self.view.centerOn(self.pixmap_item)

    # ---------------------------- Overlays ---------------------------- #
    def _geo_to_pixel(self, x, y):
        """Map coords (raster CRS) -> image pixel coords (after downsampling)."""
        if self._transform is None:
            return None
        inv = ~self._transform  # (col, row) from (x, y)
        col, row = inv * (x, y)
        return (col / self._scale_arg, row / self._scale_arg)

    def _geom_to_qpath(self, geom):
        """
        Convert shapely geom (in raster CRS) to QPainterPath in *image pixel* coords.
        Z/M tolerant: only X,Y are used. Draws Points as tiny segments.
        """
        _, shapely_geoms = _get_geopandas()
        if shapely_geoms is None:
            return None
        
        LineString = shapely_geoms['LineString']
        MultiLineString = shapely_geoms['MultiLineString']
        Polygon = shapely_geoms['Polygon']
        MultiPolygon = shapely_geoms['MultiPolygon']
        GeometryCollection = shapely_geoms['GeometryCollection']
        Point = shapely_geoms['Point']
        MultiPoint = shapely_geoms['MultiPoint']
        
        def _coords_to_path(coords, path: QPainterPath):
            first = True
            for c in coords:
                if c is None:
                    continue
                # tolerate 2D or 3D tuples (ignore Z/M)
                x = c[0]
                y = c[1] if len(c) > 1 else None
                if y is None:
                    continue
                px = self._geo_to_pixel(x, y)
                if px is None:
                    continue
                if first:
                    path.moveTo(px[0], px[1])
                    first = False
                else:
                    path.lineTo(px[0], px[1])

        path = QPainterPath()

        if isinstance(geom, LineString):
            _coords_to_path(list(geom.coords), path)
            return path

        if isinstance(geom, MultiLineString):
            for ls in geom.geoms:
                _coords_to_path(list(ls.coords), path)
            return path

        if isinstance(geom, Polygon):
            _coords_to_path(list(geom.exterior.coords), path)
            for ring in geom.interiors:
                _coords_to_path(list(ring.coords), path)
            return path

        if isinstance(geom, MultiPolygon):
            for poly in geom.geoms:
                _coords_to_path(list(poly.exterior.coords), path)
                for ring in poly.interiors:
                    _coords_to_path(list(ring.coords), path)
            return path

        if isinstance(geom, Point):
            px = self._geo_to_pixel(geom.x, geom.y)
            if px is None:
                return None
            path.moveTo(px[0], px[1])
            path.lineTo(px[0] + 0.01, px[1] + 0.01)  # tiny mark; cosmetic pen keeps visible
            return path

        if isinstance(geom, MultiPoint):
            for p in geom.geoms:
                sub = self._geom_to_qpath(p)
                if sub:
                    path.addPath(sub)
            return path

        if isinstance(geom, GeometryCollection):
            for g in geom.geoms:
                sub = self._geom_to_qpath(g)
                if sub:
                    path.addPath(sub)
            return path

        return None

    def _add_shapefile_overlays(self):
        gpd, _ = _get_geopandas() 
        if gpd is None:
            global HAVE_GEO
            HAVE_GEO = False
            print("[WARN] --shapefile requires geopandas and shapely.")
            print("       Install them with: pip install viewtif[geo]")
            print("       Proceeding without shapefile overlay.")
            return
        if self._crs is None or self._transform is None:
            print("[WARN] raster lacks CRS/transform; cannot place overlays.")
            return

        pen = QPen(QColor(self._shp_color))
        pen.setWidthF(self._shp_width)
        pen.setCosmetic(True)  # constant on-screen width

        for shp_path in self._shapefiles:
            if not os.path.exists(shp_path):
                print(f"[WARN] File not found: {shp_path}")
                continue
            try:
                gdf = gpd.read_file(shp_path)

                if gdf.empty:
                    continue

                if gdf.crs is None:
                    print(f"[WARN] {os.path.basename(shp_path)} has no CRS; assuming raster CRS.")
                    gdf = gdf.set_crs(self._crs)
                else:
                    gdf = gdf.to_crs(self._crs)

                for geom in gdf.geometry:
                    if geom is None or geom.is_empty:
                        continue
                    qpath = self._geom_to_qpath(geom)
                    if qpath is None or qpath.isEmpty():
                        continue
                    item = QGraphicsPathItem(qpath)
                    item.setPen(pen)
                    item.setZValue(10.0)
                    self.scene.addItem(item)
                    self._overlay_items.append(item)

            except Exception as e:
                print(f"[WARN] Failed to draw overlay {os.path.basename(shp_path)}: {e}")

    # ---------------------------- Basemap ---------------------------- #
    def _load_basemap(self):
        """Load Natural Earth basemap with timeout to avoid blocking."""
        gpd, _ = _get_geopandas()
        if gpd is None:
            print("[WARN] geopandas not available; cannot load basemap.")
            return
        
        # Basemap not supported for NetCDF files
        if hasattr(self, "_nc_var_name"):
            print("[INFO] Basemap not supported for NetCDF files (cartopy used).")
            return
        
        if self._crs is None:
            print("[WARN] Raster lacks CRS; cannot load basemap.")
            return
        
        # Get CRS info
        crs_string = str(self._crs).upper()
        
        # Try to get EPSG code
        crs_code = None
        try:
            crs_code = self._crs.to_epsg()
        except Exception:
            pass
        
        if crs_code is None:
            import re
            epsg_match = re.search(r'EPSG:(\d+)', crs_string)
            if epsg_match:
                crs_code = int(epsg_match.group(1))
        
        # Block UTM zones (known to cause artifacts)
        if crs_code and (32600 <= crs_code <= 32660 or 32700 <= crs_code <= 32760):
            self._show_disabled_message(crs_code)
            self.base_gdf = None
            return
        
        # Check if suitable for basemap
        is_geographic = False
        try:
            is_geographic = self._crs.is_geographic
        except Exception:
            is_geographic = 'GEOGCS' in crs_string or 'GEOG' in crs_string
        
        # Good projected CRS
        good_crs = [4326, 3857, 3395, 4269, 4267]
        is_approved = crs_code in good_crs if crs_code else False
        
        # Equal-area projections (work well with basemap)
        equal_area_keywords = ['ALBERS', 'EQUAL_AREA', 'LAMBERT_AZIMUTHAL_EQUAL_AREA']
        is_equal_area = any(kw in crs_string for kw in equal_area_keywords)
        
        # Allow if: geographic OR approved OR equal-area
        if not (is_geographic or is_approved or is_equal_area):
            self._show_disabled_message(crs_code)
            self.base_gdf = None
            return
        
        # Load basemap
        import requests
        from io import BytesIO
        
        url = "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
        print("[INFO] Loading basemap (timeout 3s)...")
        
        try:
            resp = requests.get(url, timeout=3)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            print("[WARN] Basemap download timed out (slow connection).")
            self.base_gdf = None
            return
        except requests.exceptions.ConnectionError:
            print("[WARN] Basemap not loaded (no internet connection).")
            self.base_gdf = None
            return
        except Exception as e:
            print(f"[WARN] Basemap download failed: {e}")
            self.base_gdf = None
            return
        
        try:
            zip_bytes = BytesIO(resp.content)
            gdf = gpd.read_file(zip_bytes)
            
            # Reproject to raster CRS
            if gdf.crs != self._crs:
                gdf = gdf.to_crs(self._crs)
            
            self.base_gdf = gdf
            # print("[INFO] Basemap loaded successfully")
            
        except Exception as e:
            print(f"[WARN] Basemap processing failed: {e}")
            self.base_gdf = None
            return
    
    def _show_disabled_message(self, crs_code):
        """Show location info when basemap is disabled."""
        rasterio = _get_rasterio()
        try:
            if self._transform is not None:
                h, w = self.data.shape[:2] if self.data.ndim == 2 else self.data.shape[:2]
                from rasterio.warp import transform_bounds
                west, south, east, north = transform_bounds(
                    self._crs, 'EPSG:4326',
                    self._transform.c,
                    self._transform.f + self._transform.e * h,
                    self._transform.c + self._transform.a * w,
                    self._transform.f
                )
                center_lon = (west + east) / 2
                center_lat = (south + north) / 2
                
                # Get continent info
                continent_info = ""
                country_info = ""
                try:
                    import requests
                    from io import BytesIO
                    gpd, shapely_geoms = _get_geopandas()
                    if gpd is None or shapely_geoms is None:
                        raise ImportError("geopandas/shapely not available")
                    Point = shapely_geoms['Point']
                    
                    url = "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
                    resp = requests.get(url, timeout=3)
                    resp.raise_for_status()
                    
                    zip_bytes = BytesIO(resp.content)
                    gdf = gpd.read_file(zip_bytes)
                    center_point = Point(center_lon, center_lat)
                    
                    if 'CONTINENT' in gdf.columns:
                        containing = gdf[gdf.contains(center_point)]
                        if not containing.empty:
                            continent_info = containing.iloc[0]['CONTINENT']
                            country_info = containing.iloc[0].get('NAME', 'unknown')
                        else:
                            import warnings
                            warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")
                            gdf['dist'] = gdf.distance(center_point)
                            nearest = gdf.loc[gdf['dist'].idxmin()]
                            continent_info = nearest['CONTINENT']
                            country_info = f"near {nearest.get('NAME', 'unknown')}"
                except Exception:
                    pass
                
                if continent_info and country_info:
                    print(f"[INFO] Location: {continent_info}, {country_info} ({center_lat:.4f}°, {center_lon:.4f}°)")
                else:
                    print(f"[INFO] Location: {center_lat:.4f}°, {center_lon:.4f}°")
                print(f"[INFO] Basemap disabled for this projection (CRS: {crs_code or 'unknown'})")
                print("[INFO] Add your own boundaries with --shapefile <vector_file>")
        except Exception:
            print(f"[INFO] Basemap disabled for this projection (CRS: {crs_code or 'unknown'})")
            print("[INFO] Add your own boundaries with --shapefile <vector_file>")

    def _draw_basemap(self):
        """Draw basemap using the loaded Natural Earth data."""
        if self.base_gdf is None:
            return
        
        # Determine pen color based on theme
        palette = QApplication.palette()
        bg = palette.window().color()
        brightness = (bg.red() * 299 + bg.green() * 587 + bg.blue() * 114) / 1000
        pen = QPen(QColor(255, 255, 255) if brightness < 128 else QColor(80, 80, 80))
        pen.setWidthF(0.5)
        pen.setCosmetic(True)
        
        # Clear existing basemap items
        for it in self.basemap_items:
            self.scene.removeItem(it)
        self.basemap_items.clear()
        
        # Draw each geometry using pixel transformation
        for geom in self.base_gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            
            # Fix invalid geometries after reprojection
            if not geom.is_valid:
                try:
                    geom = geom.buffer(0)
                except Exception:
                    continue
            
            qpath = self._geom_to_qpath(geom)
            if qpath is None or qpath.isEmpty():
                continue
            
            item = QGraphicsPathItem(qpath)
            item.setPen(pen)
            item.setZValue(-100)  # Draw behind raster
            self.scene.addItem(item)
            self.basemap_items.append(item)

    # ----------------------- Title / Rendering ----------------------- #
    def update_title(self):
        """Add band before the title."""
        import os
        file_name = os.path.basename(self.tif_path)

        if hasattr(self, "_has_time_dim") and self._has_time_dim:
            # nc_name = getattr(self, "_nc_var_name", "")
            
            title = f"Time step {self.band_index + 1}/{self.band_count} — {file_name}"
            

        elif hasattr(self, "band_index"):
            title = f"Band {self.band_index + 1}/{self.band_count} — {file_name}"

        elif self.rgb_mode:
           
            # Case 1: --rgbfiles → filenames
            if self.rgbfiles:
                files = [os.path.basename(p) for p in self.rgbfiles]
                title = f"RGB ({files[0]}, {files[1]}, {files[2]})"

            # Case 2: --rgb → band numbers
            elif self.rgb:
                r, g, b = self.rgb
                title = f"RGB ({r}, {g}, {b}) — {file_name}"

            else:
                title = f"RGB — {file_name}"

        elif not self.rgb_mode:
            # TIFF uses self.band
            title = f"Band {self.band}/{self.band_count} — {file_name}"

        else:
            title = {file_name}

        print(f"Title: {title}")
        self.setWindowTitle(title)

    def _normalize_lat_lon(self, frame):
        """Flip frame only if data and lat orientation disagree."""
        import numpy as np

        if not hasattr(self, "_lat_data"):
            return frame

        lats = self._lat_data

        # 1D latitude case
        if np.ndim(lats) == 1:
            lat_ascending = lats[0] < lats[-1]

            # If first pixel row corresponds to northernmost lat → do nothing
            # If first pixel row corresponds to southernmost lat → flip to make north at top
            # We'll assume data[0, :] corresponds to lats[0]
            if lat_ascending:
                # print("[DEBUG] Flipping latitude orientation (lat ascending, data starts south)")
                frame = np.flipud(frame)
#             else:
#                 print("[DEBUG] No flip (lat descending, already north-up)")
            return frame

        # 2D latitude grid (rare case)
        elif np.ndim(lats) == 2:
            first_col = lats[:, 0]
            lat_ascending = first_col[0] < first_col[-1]
            if lat_ascending:
                # print("[DEBUG] Flipping latitude orientation (2D grid ascending)")
                frame = np.flipud(frame)
#             else:
#                 print("[DEBUG] No flip (2D grid already north-up)")
            return frame

        return frame

    def _apply_scale_if_needed(self, frame):
        """Downsample frame and lat/lon consistently if --scale > 1."""
        if not hasattr(self, "_scale_arg") or self._scale_arg <= 1:
            return frame

        step = int(self._scale_arg)
        if not hasattr(self, "_suppress_scale_print"):
            print(f"Applying scale factor {self._scale_arg} to current frame")

        # Downsample the frame
        frame = frame[::step, ::step]

        # Also downsample lat/lon for this viewer instance if not already
        if hasattr(self, "_lat_data") and np.ndim(self._lat_data) == 1 and len(self._lat_data) > frame.shape[0]:
            self._lat_data = self._lat_data[::step]
        if hasattr(self, "_lon_data") and np.ndim(self._lon_data) == 1 and len(self._lon_data) > frame.shape[1]:
            self._lon_data = self._lon_data[::step]

        return frame

    def get_current_frame(self):
        """Return the current time/band frame as a NumPy array (2D)."""
        frame = None

        if hasattr(self, '_time_dim_name') and hasattr(self, '_nc_var_data'):
            # Select frame using band_index
            try:
                frame = self._nc_var_data.isel({self._time_dim_name: self.band_index})
            except Exception:
                # Already numpy or index error fallback
                frame = self._nc_var_data

        elif isinstance(self.data, np.ndarray):
            frame = self.data

        # Normalize lat orientation if needed
        frame = self._normalize_lat_lon(frame)
        frame = self._apply_scale_if_needed(frame)
        # Convert to numpy if it's still an xarray
        if hasattr(frame, "values"):
            frame = frame.values

        return frame.astype(np.float32)
        
    def format_time_value(self, time_value):
        """Format a time value into a user-friendly string"""
        # Default is the string representation
        time_str = str(time_value)
        
        try:
            # Handle numpy datetime64
            if hasattr(time_value, 'dtype') and np.issubdtype(time_value.dtype, np.datetime64):
                # Lazy-load pandas for timestamp conversion
                import pandas as pd
                # Convert to Python datetime if possible
                dt = pd.Timestamp(time_value).to_pydatetime()
                time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            # Handle native Python datetime
            elif hasattr(time_value, 'strftime'):
                time_str = time_value.strftime('%Y-%m-%d %H:%M:%S')
            # Handle cftime datetime-like objects used in some NetCDF files
            elif hasattr(time_value, 'isoformat'):
                time_str = time_value.isoformat().replace('T', ' ')
        except Exception:
            # Fall back to string representation
            pass
            
        return time_str

    def _render_rgb(self):
        import warnings
        warnings.filterwarnings("ignore", message="invalid value encountered in cast")

        cm = _get_matplotlib_cm()
        
        if self.rgb_mode:
            arr = self.data
            finite = np.isfinite(arr)
            rgb = np.zeros_like(arr)
            if np.any(finite):
                # Global 2–98 percentile stretch across all bands (QGIS-like)
                global_min, global_max = np.nanpercentile(arr, (2, 98))
                rng = max(global_max - global_min, 1e-12)
                norm = np.clip((arr - global_min) / rng, 0, 1)
                rgb = np.clip(norm * self.contrast, 0, 1)
                rgb = np.power(rgb, self.gamma)
            return (rgb * 255).astype(np.uint8)
        else:
            a = self.data
            finite = np.isfinite(a)
            norm = np.zeros_like(a, dtype=np.float32)
            rng = max(self.vmax - self.vmin, 1e-12)
            if np.any(finite):
                norm[finite] = (a[finite] - self.vmin) / rng
            norm = np.clip(norm * self.contrast, 0.0, 1.0)
            norm = np.power(norm, self.gamma)
            # viridis <-> magma toggle
            cmap = getattr(cm, self.cmap_name, cm.viridis)
            rgb = (cmap(norm)[..., :3] * 255).astype(np.uint8)
            return rgb

    def _render_cartopy_map(self, data):
        """ Use cartopy for better visualization"""
        import warnings
        warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")
        warnings.filterwarnings("ignore", message="invalid value encountered in create_collection")
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")

        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        
        cm = _get_matplotlib_cm()
        
        # Create a new figure with cartopy projection
        fig = plt.figure(figsize=(12, 8), dpi=100)
        ax = plt.axes(projection=ccrs.PlateCarree())
        
        # Get coordinates
        lons = self._lon_data
        lats = self._lat_data
        
        # Create contour plot
        if hasattr(plt.cm, self.cmap_name):
            cmap = getattr(plt.cm, self.cmap_name)
        else:
            cmap = getattr(cm, self.cmap_name, cm.viridis)
        
        # Apply contrast and gamma adjustments
        finite = np.isfinite(data)
        norm_data = np.zeros_like(data, dtype=np.float32)
        
        # Check if we have any valid data
        if not np.any(finite):
            vmin, vmax = 0, 1  # Use dummy values for all-NaN data
        else:
            vmin, vmax = np.nanmin(data), np.nanmax(data)
        
        rng = max(vmax - vmin, 1e-12)
        
        if np.any(finite):
            norm_data[finite] = (data[finite] - vmin) / rng
        
        norm_data = np.clip(norm_data * self.contrast, 0.0, 1.0)
        norm_data = np.power(norm_data, self.gamma)
        norm_data = norm_data * rng + vmin
        
        # Downsample coordinates to match downsampled data shape
        data_height, data_width = data.shape[:2]
        lat_samples = len(lats)
        lon_samples = len(lons)

        lat_step = max(1, lat_samples // data_height)
        lon_step = max(1, lon_samples // data_width)

         # Downsample coordinate arrays to match data
        lats_downsampled = lats[::lat_step][:data_height]
        lons_downsampled = lons[::lon_step][:data_width]

        # --- Synchronize latitude orientation with normalized data ---
        if np.ndim(lats) == 1 and lats[0] < lats[-1]:
            # print("[DEBUG] Lat ascending → flip lats_downsampled to match flipped data")
            lats_downsampled = lats_downsampled[::-1]
        elif np.ndim(lats) == 2:
            first_col = lats[:, 0]
            if first_col[0] < first_col[-1]:
                # print("[DEBUG] 2D lat grid ascending → flip lats_downsampled vertically")
                lats_downsampled = np.flipud(lats_downsampled)

        # ---- Fix longitude and sort correctly ----
        lons_ds = lons_downsampled.copy()

        # Convert 0–360 → -180–180 only once
        if lons_ds.max() > 180:
            lons_ds = ((lons_ds + 180) % 360) - 180

        # Sort and reorder data
        sort_idx = np.argsort(lons_ds)
        lons_ds = lons_ds[sort_idx]
        data = data[:, sort_idx]

        extent = (
            float(lons_ds[0]),
            float(lons_ds[-1]),
            float(lats_downsampled[-1]),
            float(lats_downsampled[0])
        )

        vmin = self.vmin if self._user_vmin is not None else np.nanmin(data)
        vmax = self.vmax if self._user_vmax is not None else np.nanmax(data)
 
 # Changed from pcolormesh to imshow to prevent artefacts when used with cartopy
        img = ax.imshow(
            data,
            extent=extent,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            interpolation="nearest",
            vmin=vmin,
            vmax=vmax
        )

        # Add map features
        ax.coastlines(resolution="50m", linewidth=0.5)
        ax.add_feature(cfeature.BORDERS, linestyle=":", linewidth=0.5)
        ax.add_feature(cfeature.STATES, linestyle="-", linewidth=0.3, alpha=0.5)
        ax.gridlines(draw_labels=True, alpha=0.3)

        # --- Add dynamic title ---
        title = os.path.basename(self.tif_path)
        if hasattr(self, "_has_time_dim") and self._has_time_dim:
            # Use current band_index as proxy for time_index
            try:
                current_time = self._time_values[self.band_index]
                time_str = self.format_time_value(current_time) if hasattr(self, "format_time_value") else str(current_time)
                ax.set_title(f"{title}\n{time_str}", fontsize=10)
            except Exception as e:
                ax.set_title(f"{title}\n(time step {self.band_index + 1})", fontsize=10)
        else:
            ax.set_title(title, fontsize=10)

        # Add colorbar
        plt.colorbar(img, ax=ax, shrink=0.6)
        plt.tight_layout()

        
        # Convert matplotlib figure to image
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        width, height = fig.canvas.get_width_height()
        rgba = np.frombuffer(canvas.buffer_rgba(), dtype=np.uint8).reshape(height, width, 4)
        
        # Extract RGB and ensure it's C-contiguous for QImage
        rgb = np.ascontiguousarray(rgba[:, :, :3])
        
        # Close figure to prevent memory leak
        plt.close(fig)
        del fig
        
        return rgb
    
    def update_pixmap(self):
    # ------------------------------------------------------------------
    # Select respective data (a = single-band 2D, rgb = RGB array)
    # ------------------------------------------------------------------

        rgb = None  # ensure defined

        # Case 1: RGB override (GeoTIFF or RGB-files)
        if self.rgb_mode:
            rgb = self.data
            a = None

        # Case 2: Scientific multi-band (NetCDF/HDF)
        elif hasattr(self, "band_index"):
            # Always get consistent per-frame 2D data
            a = self.get_current_frame()

        # Case 3: Regular GeoTIFF single-band
        else:
            rgb = None
            a = self.data

        # --- Render image ---
        # Cartopy is only relevant for NetCDF
        use_cartopy = False

        if hasattr(self, "_nc_var_name"):
            use_cartopy = (
                self.cartopy_mode == "on"
                and HAVE_CARTOPY
                and getattr(self, "_use_cartopy", False)
                and getattr(self, "_has_geo_coords", False)
            )

            # Inform user when cartopy was requested but cannot be used
            if self.cartopy_mode == "on" and not use_cartopy:
                if not HAVE_CARTOPY:
                    print("[INFO] Cartopy not installed — using standard scientific rendering.")
                elif not getattr(self, "_use_cartopy", False):
                    print("[INFO] This file lacks geospatial coordinates — cartopy disabled.")
                elif not getattr(self, "_has_geo_coords", False):
                    print("[INFO] No lat/lon coordinates found — cartopy disabled.")

        if use_cartopy:
            rgb = self._render_cartopy_map(a)
        elif rgb is None:
            # Standard grayscale rendering for single-band data
            cm = _get_matplotlib_cm()
            finite = np.isfinite(a)

            # Check if we have any valid data
            if not np.any(finite):
                vmin = vmax = 0
                rng = 1e-12
                norm = np.zeros_like(a, dtype=np.float32)
            else:
                # Respect user-specified limits or calculate from valid pixels only
                if self._user_vmin is not None:
                    vmin = self._user_vmin
                else:
                    valid_pixels = a[finite]
                    vmin = np.percentile(valid_pixels, 2)  # 2nd percentile
                
                if self._user_vmax is not None:
                    vmax = self._user_vmax
                else:
                    valid_pixels = a[finite]
                    vmax = np.percentile(valid_pixels, 98)  # 98th percentile
                
                rng = max(vmax - vmin, 1e-12)

                norm = np.zeros_like(a, dtype=np.float32)
                if np.any(finite):
                    norm[finite] = (a[finite] - vmin) / rng
                norm = np.clip(norm, 0, 1)
                norm = np.power(norm * self.contrast, self.gamma)
            
            cmap = getattr(cm, self.cmap_name, cm.viridis)
            rgb = (cmap(norm)[..., :3] * 255).astype(np.uint8)
        else:
            # True RGB mode (unchanged)
            rgb = self._render_rgb()


        h, w = rgb.shape[:2]  # for both 2D and 3D
        self._last_rgb = rgb

        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        
        if self.pixmap_item is None:
            
            self.pixmap_item = QGraphicsPixmapItem(pix)
            self.pixmap_item.setZValue(0.0)
            self.scene.addItem(self.pixmap_item)
        else:
            self.pixmap_item.setPixmap(pix)
    # ----------------------- Single-band switching ------------------- #
    def load_band(self, band_num: int):
        if self.rgb_mode:
            return

        rasterio = _get_rasterio()
        tif_path = self.tif_path
      
        if tif_path and os.path.dirname(self.tif_path).endswith(".gdb"):
            tif_path = f"OpenFileGDB:{os.path.dirname(self.tif_path)}:{os.path.basename(self.tif_path)}"

        with rasterio.open(tif_path) as src:
            self.band = band_num
            arr = src.read(self.band).astype(np.float32)

            # Apply user-specified nodata first
            if self._nodata is not None:
                arr[arr == self._nodata] = np.nan
            
            # Then apply file's nodata if present
            nd = src.nodata
            if nd is not None:
                arr[arr == nd] = np.nan
            self.data = arr

            self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)
            print(f"[INFO] Value range: {self.vmin:.3f} -> {self.vmax:.3f}")
        self.update_pixmap()
        self.update_title()

    # ------------------------------ Keys ----------------------------- #
    def keyPressEvent(self, ev):
        k = ev.key()
        hsb: QScrollBar = self.view.horizontalScrollBar()
        vsb: QScrollBar = self.view.verticalScrollBar()

        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Z):
            self.view.scale(self.zoom_step, self.zoom_step)
        elif k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore, Qt.Key.Key_X):
            inv = 1.0 / self.zoom_step
            self.view.scale(inv, inv)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_A):
            hsb.setValue(hsb.value() - self.pan_step)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_D):
            hsb.setValue(hsb.value() + self.pan_step)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_W):
            vsb.setValue(vsb.value() - self.pan_step)
        elif k in (Qt.Key.Key_Down, Qt.Key.Key_S):
            vsb.setValue(vsb.value() + self.pan_step)

        # Contrast / Gamma
        elif k == Qt.Key.Key_C:
            if hasattr(self, "_nc_var_name") and self.cartopy_mode == "on" and getattr(self, "_use_cartopy", False):
                print("[INFO] Contrast adjustment disabled with cartopy rendering")
                print("[INFO] Use --vmin/--vmax flags, or reopen with --cartopy off")
            else:
                self.contrast *= 1.1; self.update_pixmap()
        elif k == Qt.Key.Key_V:
            if hasattr(self, "_nc_var_name") and self.cartopy_mode == "on" and getattr(self, "_use_cartopy", False):
                print("[INFO] Contrast adjustment disabled with cartopy rendering")
                print("[INFO] Use --vmin/--vmax flags, or reopen with --cartopy off")
            else:
                self.contrast /= 1.1; self.update_pixmap()
        elif k == Qt.Key.Key_G:
            if hasattr(self, "_nc_var_name") and self.cartopy_mode == "on" and getattr(self, "_use_cartopy", False):
                print("[INFO] Gamma adjustment disabled with cartopy rendering")
                print("[INFO] Use --vmin/--vmax flags, or reopen with --cartopy off")
            else:
                self.gamma *= 1.1; self.update_pixmap()
        elif k == Qt.Key.Key_H:
            if hasattr(self, "_nc_var_name") and self.cartopy_mode == "on" and getattr(self, "_use_cartopy", False):
                print("[INFO] Gamma adjustment disabled with cartopy rendering")
                print("[INFO] Use --vmin/--vmax flags, or reopen with --cartopy off")
            else:
                self.gamma /= 1.1; self.update_pixmap()

        # Colormap toggle (single-band only)
        elif not self.rgb_mode and k == Qt.Key.Key_M:
            # For NetCDF files, cycle through three colormaps
            if hasattr(self, 'cmap_names'):
                self.cmap_index = (self.cmap_index + 1) % len(self.cmap_names)
                self.cmap_name = self.cmap_names[self.cmap_index]
                print(f"Colormap: {self.cmap_name}")
            # For other files, toggle between two colormaps
            else:
                self.cmap_name, self.alt_cmap_name = self.alt_cmap_name, self.cmap_name
                print(f"Colormap: {self.cmap_name}")
            self.update_pixmap()

        # Band switch
        elif k == Qt.Key.Key_BracketRight:
            if hasattr(self, "band_index"):  # HDF/NetCDF mode
                self.band_index = (self.band_index + 1) % self.band_count
                self.data = self.get_current_frame()
                
                # Recalculate and print value range for new band
                if self._user_vmin is None and self._user_vmax is None:
                    self.vmin, self.vmax = np.nanmin(self.data), np.nanmax(self.data)
                    print(f"[INFO] Value range: {self.vmin:.3f} -> {self.vmax:.3f}")
                
                self.update_pixmap()
                self.update_title()

            elif not self.rgb_mode:  # GeoTIFF single-band mode
                new_band = self.band + 1 if self.band < self.band_count else 1
                self.load_band(new_band)

        elif k == Qt.Key.Key_BracketLeft:
            if hasattr(self, "band_index"):  # HDF/NetCDF mode
                self.band_index = (self.band_index - 1) % self.band_count
                self.data = self.get_current_frame()
                
                # Recalculate and print value range for new band
                if self._user_vmin is None and self._user_vmax is None:
                    self.vmin, self.vmax = np.nanmin(self.data), np.nanmax(self.data)
                    print(f"[INFO] Value range: {self.vmin:.3f} -> {self.vmax:.3f}")
                
                self.update_pixmap()
                self.update_title()

            elif not self.rgb_mode:  # GeoTIFF single-band mode
                new_band = self.band - 1 if self.band > 1 else self.band_count
                self.load_band(new_band)
                
        # Basemap toggle
        elif k == Qt.Key.Key_B:
            if self.basemap_items:
                # Basemap currently visible
                for it in self.basemap_items:
                    self.scene.removeItem(it)
                self.basemap_items.clear()
                print("[INFO] Basemap removed")
            else:
                # Basemap not visible - load and display it
                if self.base_gdf is None:
                    self._load_basemap()
                
                if self.base_gdf is not None:
                    self._draw_basemap()
                    print("[INFO] Basemap displayed")
                # else:
                #     print("[INFO] Basemap not available")

        elif k == Qt.Key.Key_R:
            self.contrast = 1.0
            self.gamma = 1.0
            self.update_pixmap()
            self.view.resetTransform()
            self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            super().keyPressEvent(ev)


# --------------------------------- CLI ----------------------------------- #
def run_viewer(
    tif_path,
    scale=None,
    band=None,
    rgb=None,
    rgbfiles=None,
    shapefile=None,
    shp_color=None,
    shp_width=None,
    subset=None,    
    vmin=None,
    vmax=None,
    cartopy="on",
    timestep=None,
    nodata=None,
):

    """Launch the TiffViewer app"""
    app = QApplication(sys.argv)
    win = TiffViewer(
        tif_path,
        scale=scale,
        band=band,
        rgb=rgb,
        rgbfiles=rgbfiles,
        shapefiles=shapefile,
        shp_color=shp_color,
        shp_width=shp_width,
        subset=subset,
        vmin=vmin,
        vmax=vmax,
        cartopy=cartopy,
        timestep=timestep,
        nodata=nodata,
    )
    win.show()
    sys.exit(app.exec())

import click

@click.command()
@click.version_option(__version__, prog_name="viewtif")
@click.argument("tif_path", required=False)
@click.option("--band", default=1, show_default=True, type=int, help="Band number to display")
@click.option("--scale", default=1, show_default=True, type=int, help="Downsample by factor N (e.g., --scale 5 loads 1/25 of pixels)")
@click.option("--rgb", nargs=3, type=int, help="Three band numbers for RGB, e.g. --rgb 4 3 2")
@click.option("--rgbfiles", nargs=3, type=str, help="Three single-band TIFFs for RGB, e.g. --rgbfiles B4.tif B3.tif B2.tif")
@click.option("--shapefile", multiple=True, type=str, help="Vector overlay file(s) (shapefile, GeoJSON, etc.)")
@click.option("--shp-color", default="cyan", show_default=True, help="Vector overlay color (name or #RRGGBB).")
@click.option("--shp-width", default=1.0, show_default=True, type=float, help="Vector overlay line width (screen pixels).")
@click.option("--subset", default=None, type=int, help="Open specific subdataset index in .hdf/.h5 file or variable in NetCDF file")
@click.option("--vmin", type=float, default=None, help="Manual minimum display value")
@click.option("--vmax", type=float, default=None, help="Manual maximum display value")
@click.option(
    "--timestep",
    type=int,
    default=None,
    help="For NetCDF files, jump directly to a specific time index (1-based)."
)
@click.option(
    "--cartopy",
    type=click.Choice(["on", "off"], case_sensitive=False),
    default="on",
    show_default=True,
    help="Use cartopy for NetCDF geospatial rendering."
)
@click.option(
    "--qgis",
    is_flag=True,
    help="Open in QGIS directly (skips viewer)"
)
@click.option("--nodata", type=float, default=None, help="Nodata value to mask (e.g., -9999)")

def main(tif_path, band, scale, rgb, rgbfiles, shapefile, shp_color, shp_width, subset, vmin, vmax, cartopy, timestep, qgis, nodata):    
    """Lightweight GeoTIFF, NetCDF, and HDF viewer."""
    # --- Warn early if shapefile requested but geopandas missing ---
    if shapefile and not HAVE_GEO:
        print(
            "[WARN] --shapefile requires geopandas and shapely.\n"
            "       Install them with: pip install viewtif[geo]\n"
            "       Proceeding without shapefile overlay."
        )
    # Check if vector files exist before launching viewer
    if shapefile:
        for shp_path in shapefile:
            if not os.path.exists(shp_path):
                print(f"[ERROR] Vector file not found: {shp_path}")
                sys.exit(1)

# --- Handle --qgis: check QGIS availability first, then export ---
    if qgis:
        import uuid
        import tempfile
        
        # Load rasterio early for QGIS export
        rasterio = _get_rasterio()
        Affine = rasterio.Affine

        if not tif_path:
            print("[ERROR] --qgis requires a file path")
            sys.exit(1)
        
        # Check if QGIS is available BEFORE exporting
        qgis_path = None
        
        if sys.platform == "darwin":
            candidates = [
                "/Applications/QGIS.app",
                "/Applications/QGIS-LTR.app",
            ]
            for app in candidates:
                if os.path.exists(app):
                    qgis_path = app
                    break
        
        elif sys.platform.startswith("win"):
            candidates = [
                r"C:\Program Files\QGIS 3.34.0\bin\qgis-bin.exe",
                r"C:\Program Files\QGIS 3.32.0\bin\qgis-bin.exe",
                r"C:\OSGeo4W64\bin\qgis-bin.exe",
            ]
            for exe in candidates:
                if os.path.exists(exe):
                    qgis_path = exe
                    break
            
            # Try system PATH
            if not qgis_path:
                import shutil
                if shutil.which("qgis"):
                    qgis_path = "qgis"
        
        else:  # Linux
            import shutil
            if shutil.which("qgis"):
                qgis_path = "qgis"
            else:
                linux_candidates = [
                    "/usr/bin/qgis",
                    "/usr/local/bin/qgis",
                    "/snap/bin/qgis",
                ]
                for exe in linux_candidates:
                    if os.path.exists(exe):
                        qgis_path = exe
                        break
        
        # If QGIS not found, exit early
        if not qgis_path:
            print("[ERROR] QGIS not found on your system")
            print("[INFO] Install QGIS or specify the path manually")
            sys.exit(1)
        
        # Warn if --shapefile was provided (it will be ignored)
        ignored_flags = []
        if shapefile:
            ignored_flags.append("--shapefile")
        if scale and scale != 1:
            ignored_flags.append("--scale")
        if vmin is not None or vmax is not None:
            ignored_flags.append("--vmin/--vmax")
        if band and band != 1:
            ignored_flags.append("--band")
        
        if ignored_flags:
            print(f"[INFO] {', '.join(ignored_flags)} ignored when using --qgis")
        
        # QGIS found - proceed with export
        # Handle GDAL format strings (e.g., "OpenFileGDB:path.gdb:layer")
        if ":" in tif_path and tif_path.startswith(("OpenFileGDB:", "HDF4_EOS:", "HDF5:")):
            parts = tif_path.split(":")
            if len(parts) >= 2:
                file_part = parts[1]
                ext = os.path.splitext(file_part.lower())[1]
            else:
                ext = ""
        else:
            ext = os.path.splitext(tif_path.lower())[1]
        
        # Skip local file check for remote paths
        is_remote = tif_path.startswith(("http://", "https://", "s3://", "/vsi"))
        
        # Check if NetCDF - not supported for --qgis
        if ext in (".nc", ".netcdf"):
            print("[ERROR] --qgis is not supported for NetCDF files")
            sys.exit(1)
        
        tmp_file_path = None
        random_part = uuid.uuid4().hex[:6]
        
        try:
            # For regular GeoTIFFs, check if remote or local
            if ext in (".tif", ".tiff"):
                if is_remote:
                    # Remote GeoTIFFs need to be downloaded first
                    print(f"[INFO] Downloading remote GeoTIFF for QGIS...")
                    base = tif_path.split('/')[-1].replace('.tif', '').replace('.tiff', '')
                    tmp_file_path = os.path.join(tempfile.gettempdir(), f"{base}_{random_part}.tif")
                    
                    # Download using rasterio
                    with rasterio.open(tif_path) as src:
                        data = src.read()

                        # --- FORCE clean display-friendly GeoTIFF ---
                        profile = {
                            "driver": "GTiff",
                            "height": src.height,
                            "width": src.width,
                            "count": src.count,
                            "dtype": data.dtype,
                            "crs": src.crs,
                            "transform": src.transform,
                            "compress": "LZW",          # safe default
                            "interleave": "PIXEL",
                        }

                        with rasterio.open(tmp_file_path, "w", **profile) as dst:
                            dst.write(data)

                    print(f"[INFO] Download complete")
                else:
                    # Local GeoTIFF - use directly
                    tmp_file_path = tif_path
            
            # For File Geodatabase (.gdb), export to temporary GeoTIFF
            elif ext == ".gdb":
                try:
                    from osgeo import gdal
                except ImportError:
                    print("[ERROR] This file requires full GDAL support.")
                    sys.exit(1)
                
                if not tif_path.startswith("OpenFileGDB:"):
                    print("[ERROR] File Geodatabase requires layer specification for --qgis")
                    print("[INFO] You provided: " + tif_path)
                    print("[INFO] First run without --qgis to see available raster layers:")
                    print(f'[INFO]   viewtif {tif_path}')
                    print("[INFO] Then use the GDAL format with layer name:")
                    print(f'[INFO]   viewtif "OpenFileGDB:{tif_path}:LAYERNAME" --qgis')
                    sys.exit(1)
                
                parts = tif_path.split(":")
                if len(parts) < 3 or not parts[2].strip():
                    print("[ERROR] Layer name is missing in the path")
                    print("[INFO] You provided: " + tif_path)
                    print("[INFO] Correct format: OpenFileGDB:path/to/file.gdb:LAYERNAME")
                    print("[INFO] Example: viewtif \"OpenFileGDB:Wetlands.gdb:Wetlands\" --qgis")
                    sys.exit(1)
                
                layer_name = parts[2]

                print(f"[INFO] Exporting {layer_name} to temporary GeoTIFF...")
                
                try:
                    ds = gdal.Open(tif_path)
                    if ds is None:
                        print(f"[ERROR] Could not open layer '{layer_name}' in geodatabase")
                        print("[INFO] Possible reasons:")
                        print("        - Layer name is incorrect")
                        print("        - Layer is not a raster (vector layers not supported with --qgis)")
                        print("        - GDAL cannot access the file")
                        print(f"[INFO] Run without --qgis to see available raster layers:")
                        gdb_path = parts[1]
                        print(f"[INFO]   viewtif {gdb_path}")
                        sys.exit(1)
                    
                    arr = ds.ReadAsArray().astype(np.float32)
                    arr = np.squeeze(arr)
                    
                    base = os.path.splitext(os.path.basename(parts[1]))[0]
                    tmp_file_path = os.path.join(tempfile.gettempdir(), f"{base}_{layer_name}_{random_part}.tif")
                    
                    print(f"[INFO] Writing {arr.shape[0]}×{arr.shape[1]} raster...")

                    geotransform = ds.GetGeoTransform()
                    projection = ds.GetProjection()
                    
                    with rasterio.open(
                        tmp_file_path, 'w',
                        driver='GTiff',
                        height=arr.shape[0],
                        width=arr.shape[1],
                        count=1,
                        dtype=arr.dtype,
                        compress='lzw',
                        transform=Affine.from_gdal(*geotransform) if geotransform else None,
                        crs=projection if projection else None
                    ) as dst:
                        dst.write(arr, 1)
                    
                    print(f"[INFO] Export complete")

                except Exception as e:
                    print(f"[ERROR] Failed to export .gdb raster: {e}")
                    sys.exit(1)

            # For HDF, export to temporary GeoTIFF
            elif ext in (".hdf", ".h5", ".hdf5"):
                if subset is None:
                    print("[ERROR] HDF file requires --subset N")
                    print("[INFO] First run without --qgis to see available subdatasets")
                    sys.exit(1)
                
                try:
                    from osgeo import gdal
                except ImportError:
                    print("[ERROR] This file requires full GDAL support.")
                    sys.exit(1)
                
                ds = gdal.Open(tif_path)
                subs = ds.GetSubDatasets()
                
                if subset < 0 or subset >= len(subs):
                    print(f"[ERROR] Invalid subset index {subset}. Valid range: 0–{len(subs)-1}")
                    sys.exit(1)
                
                base = os.path.splitext(os.path.basename(tif_path))[0]
                tmp_file_path = os.path.join(tempfile.gettempdir(), f"{base}_subset{subset}_{random_part}.tif")

                print(f"[INFO] Exporting HDF subdataset to temporary GeoTIFF...")
                
                sub_name, _ = subs[subset]
                sub_ds = gdal.Open(sub_name)
                
                if sub_ds is None:
                    print(f"[ERROR] Could not open HDF subdataset {subset}")
                    sys.exit(1)
                
                arr = sub_ds.ReadAsArray().astype(np.float32)
                arr = np.squeeze(arr)

                print(f"[INFO] Writing {arr.shape[0]}×{arr.shape[1]} raster...")

                # Try to get geotransform and projection
                geotransform = sub_ds.GetGeoTransform()
                projection = sub_ds.GetProjection()
                
                # Build kwargs for rasterio
                write_kwargs = {
                    'driver': 'GTiff',
                    'height': arr.shape[0],
                    'width': arr.shape[1],
                    'count': 1,
                    'dtype': arr.dtype,
                    'compress': 'lzw'
                }
                
                # Only add transform/crs if they exist AND are valid
                if geotransform and geotransform != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
                    write_kwargs['transform'] = Affine.from_gdal(*geotransform)
                
                if projection and projection.strip():
                    write_kwargs['crs'] = projection
                
                # Warn if missing georeferencing
                if 'crs' not in write_kwargs:
                    print("[WARN] HDF subdataset has no CRS - exported image will lack georeferencing")
                
                with rasterio.open(tmp_file_path, 'w', **write_kwargs) as dst:
                    dst.write(arr, 1)

                print(f"[INFO] Export complete")
            
            else:
                print(f"[ERROR] --qgis only supports GeoTIFF (.tif), HDF (.hdf, .h5, .hdf5), and File Geodatabase (.gdb)")
                print(f"[INFO] File extension '{ext}' is not supported")
                sys.exit(1)
            
            # Check if QGIS is already running
            qgis_running = False
            if sys.platform == "darwin":
                import subprocess
                result = subprocess.run(['pgrep', '-f', 'QGIS'], capture_output=True)
                qgis_running = result.returncode == 0
            
            # Launch QGIS (works whether already running or not)
            if sys.platform == "darwin":
                os.system(f'open -a "{qgis_path}" "{tmp_file_path}"')
            elif sys.platform.startswith("win"):
                os.system(f'start "" "{qgis_path}" "{tmp_file_path}"')
            else:
                os.system(f'"{qgis_path}" "{tmp_file_path}" &')
            
            # Info message
            if ext in (".tif", ".tiff"):
                print(f"[INFO] Opened in QGIS")
            else:
                print(f"[INFO] Opened in QGIS")
                # print(f"[INFO] Temp file: {tmp_file_path}")
                # print(f"[INFO] (Will be cleaned on system reboot)")
        
        except Exception as e:
            print(f"[ERROR] Failed to export for QGIS: {e}")
            sys.exit(1)
        
        return

    run_viewer(
        tif_path,
        scale=scale,
        band=band,
        rgb=rgb,
        rgbfiles=rgbfiles,
        shapefile=shapefile,
        shp_color=shp_color,
        shp_width=shp_width,
        subset=subset,
        vmin=vmin,
        vmax=vmax,
        cartopy=cartopy,
        timestep=timestep,
        nodata=nodata,
    )

if __name__ == "__main__":
    main()