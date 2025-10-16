#!/usr/bin/env python3
"""
TIFF Viewer (PySide6) — RGB (2–98% global stretch) + Shapefile overlays

Features
- Open GeoTIFFs (single or multi-band)
- Combine separate single-band TIFFs into RGB (--rgbfiles R.tif G.tif B.tif)
- QGIS-like RGB display using global 2–98 percentile stretch
- Single-band view with contrast/gamma + colormap toggle (viridis <-> magma)
- Pan & zoom
- Switch bands with [ and ] (single-band)
- Overlay one or more shapefiles reprojected to raster CRS
- Z/M tolerant: ignores Z or M coords in shapefiles

Controls
  + / - : zoom in/out
  Arrow keys or WASD : pan
  C / V : increase/decrease contrast (works in RGB and single-band)
  G / H : increase/decrease gamma    (works in RGB and single-band)
  M     : toggle colormap (viridis <-> magma) — single-band only
  [ / ] : previous / next band (single-band)
  R     : reset view

Examples
  python tiff_viewer.py my.tif --band 1
  python tiff_viewer.py my_multiband.tif --rgb 4 3 2
  python tiff_viewer.py --rgbfiles B4.tif B3.tif B2.tif --shapefile coast.shp counties.shp --shp-color cyan --shp-width 1.8
"""

import sys
import os
import argparse
import numpy as np
import rasterio
from rasterio.transform import Affine
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, 
    QScrollBar, QGraphicsPathItem, QVBoxLayout, QHBoxLayout, QSlider, QLabel, 
    QWidget, QStatusBar, QPushButton, QComboBox
)
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QPainterPath
from PySide6.QtCore import Qt, QDateTime

import matplotlib.cm as cm

# Optional overlay deps
try:
    import geopandas as gpd
    from shapely.geometry import (
        LineString, MultiLineString, Polygon, MultiPolygon,
        GeometryCollection, Point, MultiPoint
    )
    HAVE_GEO = True
except Exception:
    HAVE_GEO = False

# Optional NetCDF deps (lazy-loaded when needed)
HAVE_NETCDF = False
xr = None
pd = None

# Optional cartopy deps for better map visualization (lazy-loaded when needed)
# Check if cartopy is available but don't import yet
try:
    import importlib.util
    HAVE_CARTOPY = importlib.util.find_spec("cartopy") is not None
except Exception:
    HAVE_CARTOPY = False

def warn_if_large(tif_path, scale=1):
    """Warn and confirm before loading very large rasters (GeoTIFF, GDB, or HDF).
    
    Uses GDAL if available, falls back to rasterio for standard formats.
    """
    import os

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
        import numpy as np
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
        tif_path: str | None,
        scale: int = 1,
        band: int = 1,
        rgb: list[int] | None = None,
        rgbfiles: list[str] | None = None,
        shapefiles: list[str] | None = None,
        shp_color: str = "white",
        shp_width: float = 2,
        subset: int | None = None,
    ):
        super().__init__()

        self.tif_path = tif_path or ""
        self.rgb_mode = rgb is not None or rgbfiles is not None
        self.band = int(band)
        self.rgb = rgb
        self.rgbfiles = rgbfiles

        self._scale_arg = max(1, int(scale or 1))
        self._transform: Affine | None = None
        self._crs = None

        # Overlay config/state
        self._shapefiles = shapefiles or []
        self._shp_color = shp_color
        self._shp_width = float(shp_width)
        self._overlay_items: list[QGraphicsPathItem] = []

        # --- Load data ---
        if rgbfiles:
            red, green, blue = rgbfiles
            import rasterio as rio_module
            with rio_module.open(red) as r, rio_module.open(green) as g, rio_module.open(blue) as b:
                if (r.width, r.height) != (g.width, g.height) or (r.width, r.height) != (b.width, b.height):
                    raise ValueError("All RGB files must have the same dimensions.")
                arr = np.stack([
                    r.read(1, out_shape=(r.height // self._scale_arg, r.width // self._scale_arg)),
                    g.read(1, out_shape=(g.height // self._scale_arg, g.width // self._scale_arg)),
                    b.read(1, out_shape=(b.height // self._scale_arg, b.width // self._scale_arg))
                ], axis=-1).astype(np.float32)
                self._transform = r.transform
                self._crs = r.crs

            self.data = arr
            self.band_count = 3
            self.rgb = [os.path.basename(red), os.path.basename(green), os.path.basename(blue)]
            # Use common prefix for title if tif_path not passed
            self.tif_path = self.tif_path or (os.path.commonprefix([red, green, blue]) or red)

        elif tif_path:
            # --------------------- Detect NetCDF --------------------- #
            if tif_path and tif_path.lower().endswith((".nc", ".netcdf")):
                try:
                    # Lazy-load NetCDF dependencies
                    import xarray as xr
                    import pandas as pd
                    
                    # Open the NetCDF file
                    ds = xr.open_dataset(tif_path)
                    
                    # List variables, filtering out boundary variables (ending with _bnds)
                    all_vars = list(ds.data_vars)
                    data_vars = [var for var in all_vars if not var.endswith('_bnds')]
                    
                    # Auto-select the first variable if there's only one and no subset specified
                    if len(data_vars) == 1 and subset is None:
                        subset = 0
                    # Only list variables if --subset not given and multiple variables exist
                    elif subset is None:
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
                    if 'lon' in ds.coords and 'lat' in ds.coords:
                        self._has_geo_coords = True
                        self._lon_data = ds.lon.values
                        self._lat_data = ds.lat.values
                    elif 'longitude' in ds.coords and 'latitude' in ds.coords:
                        self._has_geo_coords = True
                        self._lon_data = ds.longitude.values
                        self._lat_data = ds.latitude.values
                    
                    # Handle time or other index dimension if present
                    self._has_time_dim = False
                    self._time_dim_name = None
                    time_index = 0
                    
                    # Look for a time dimension first
                    if 'time' in var_data.dims:
                        self._has_time_dim = True
                        self._time_dim_name = 'time'
                        self._time_values = ds.time.values
                        self._time_index = time_index
                        
                        # Try to format time values for better display
                        time_units = getattr(ds.time, 'units', None)
                        time_calendar = getattr(ds.time, 'calendar', 'standard')
                        
                        # Select first time step by default
                        var_data = var_data.isel(time=time_index)
                    
                    # If no time dimension but variable has multiple dimensions, 
                    # use the first non-spatial dimension as a "time" dimension
                    elif len(var_data.dims) > 2:
                        # Try to find a dimension that's not lat/lon
                        spatial_dims = ['lat', 'lon', 'latitude', 'longitude', 'y', 'x']
                        for dim in var_data.dims:
                            if dim not in spatial_dims:
                                self._has_time_dim = True
                                self._time_dim_name = dim
                                self._time_values = ds[dim].values
                                self._time_index = time_index
                                
                                # Select first index by default
                                var_data = var_data.isel({dim: time_index})
                                break
                    
                    # Convert to numpy array
                    arr = var_data.values.astype(np.float32)
                    
                    # Process array based on dimensions
                    if arr.ndim > 2:
                        # Keep only lat/lon dimensions for 3D+ arrays
                        arr = np.squeeze(arr)
                    
                    # --- Downsample large arrays for responsiveness ---
                    if arr.ndim >= 2:
                        h, w = arr.shape[:2]
                        if h * w > 4_000_000:
                            step = max(2, int((h * w / 4_000_000) ** 0.5))
                            if arr.ndim == 2:
                                arr = arr[::step, ::step]
                            else:
                                arr = arr[::step, ::step, :]
                    
                    # --- Final assignments ---
                    self.data = arr
                    
                    # Try to extract CRS from CF conventions
                    self._transform = None
                    self._crs = None
                    if 'crs' in ds.variables:
                        try:
                            import rasterio.crs
                            crs_var = ds.variables['crs']
                            if hasattr(crs_var, 'spatial_ref'):
                                self._crs = rasterio.crs.CRS.from_wkt(crs_var.spatial_ref)
                        except Exception as e:
                            print(f"Could not parse CRS: {e}")
                    
                    # Set band info
                    if arr.ndim == 3:
                        self.band_count = arr.shape[2]
                    else:
                        self.band_count = 1
                    
                    self.band_index = 0
                    self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)
                    
                    # --- If user specified --band, start there ---
                    if self.band and self.band <= self.band_count:
                        self.band_index = self.band - 1
                    
                    # Enable cartopy visualization if available
                    self._use_cartopy = HAVE_CARTOPY and self._has_geo_coords
                    
                except ImportError as e:
                    if "xarray" in str(e) or "netCDF4" in str(e):
                        raise RuntimeError(
                            f"NetCDF support requires additional dependencies.\n"
                            f"Install them with: pip install viewtif[netcdf]\n"
                            f"Original error: {str(e)}"
                        )
                    else:
                        raise RuntimeError(f"Error reading NetCDF file: {str(e)}")
                except Exception as e:
                    raise RuntimeError(f"Error reading NetCDF file: {str(e)}")
            
            # ---------------- Handle File Geodatabase (.gdb) ---------------- #
            if tif_path and tif_path.lower().endswith(".gdb") and ":" not in tif_path:
                import re, subprocess
                gdb_path = tif_path  # use full path to .gdb
                try:
                    out = subprocess.check_output(["gdalinfo", "-norat", gdb_path], text=True)
                    rasters = re.findall(r"RASTER_DATASET=(\S+)", out)
                    if not rasters:
                        print(f"[WARN] No raster datasets found in {os.path.basename(gdb_path)}.")
                        sys.exit(0)
                    else:
                        print(f"Found {len(rasters)} raster dataset{'s' if len(rasters) > 1 else ''}:")
                        for i, r in enumerate(rasters):
                            print(f"[{i}] {r}")
                        print("\nUse one of these names to open. For example, to open the first raster:")
                        print(f'viewtif "OpenFileGDB:{gdb_path}:{rasters[0]}"')
                        sys.exit(0)
                except subprocess.CalledProcessError as e:
                    print(f"[WARN] Could not inspect FileGDB: {e}")
                    sys.exit(0)
                # --- Universal size check before loading ---
            warn_if_large(tif_path, scale=self._scale_arg)
            
            if False:  # Placeholder for previous if condition
                pass
            # --------------------- Detect HDF/HDF5 --------------------- #
            elif tif_path and tif_path.lower().endswith((".hdf", ".h5", ".hdf5")):
                try:
                    # Try GDAL first (best support for HDF subdatasets)
                    from osgeo import gdal
                    gdal.UseExceptions()

                    ds = gdal.Open(tif_path)
                    subs = ds.GetSubDatasets()

                    if not subs:
                        raise ValueError("No subdatasets found in HDF/HDF5 file.")

                    print(f"Found {len(subs)} subdatasets in {os.path.basename(tif_path)}:")
                    for i, (_, desc) in enumerate(subs):
                        print(f"[{i}] {desc}")

                    # Only list subsets if --subset not given
                    if subset is None:
                        print("\nUse --subset N to open a specific subdataset.")
                        sys.exit(0)

                    # Validate subset index
                    if subset < 0 or subset >= len(subs):
                        raise ValueError(f"Invalid subset index {subset}. Valid range: 0–{len(subs)-1}")

                    sub_name, desc = subs[subset]
                    print(f"\nOpening subdataset [{subset}]: {desc}")
                    sub_ds = gdal.Open(sub_name)

                    # --- Read once ---
                    arr = sub_ds.ReadAsArray().astype(np.float32)
                    #print(f"Raw array shape from GDAL: {arr.shape} (ndim={arr.ndim})")

                    # --- Normalize shape ---
                    arr = np.squeeze(arr)
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

                    # --- Final assignments ---
                    self.data = arr
                    self._transform = None
                    self._crs = None
                    self.band_count = arr.shape[2] if arr.ndim == 3 else 1
                    self.band_index = 0
                    self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)

                    if self.band_count > 1:
                        print(f"This subdataset has {self.band_count} bands — switch with [ and ] keys.")
                    else:
                        print("This subdataset has 1 band.")

                    # --- If user specified --band, start there ---
                    if self.band and self.band <= self.band_count:
                        self.band_index = self.band - 1
                        print(f"Opening band {self.band}/{self.band_count}")
                    else:
                        self.band_index = 0

                except ImportError:
                    # GDAL not available, try rasterio as fallback for NetCDF
                    print("[INFO] GDAL not available, attempting to read HDF/NetCDF with rasterio...")
                    try:
                        import rasterio as rio
                        with rio.open(tif_path) as src:
                            print(f"[INFO] NetCDF file opened via rasterio")
                            print(f"[INFO] Data shape: {src.height} x {src.width} x {src.count} bands")
                            
                            if src.count == 0:
                                raise ValueError("No bands found in NetCDF file.")
                            
                            # Determine which band(s) to read
                            if self.band and self.band <= src.count:
                                band_indices = [self.band]
                                print(f"Opening band {self.band}/{src.count}")
                            elif rgb and all(b <= src.count for b in rgb):
                                band_indices = rgb
                                print(f"Opening bands {rgb} as RGB")
                            else:
                                band_indices = list(range(1, min(src.count + 1, 4)))  # Read up to 3 bands
                                print(f"Opening bands {band_indices}")
                            
                            # Read selected bands
                            bands = []
                            for b in band_indices:
                                band_data = src.read(b, out_shape=(src.height // self._scale_arg, src.width // self._scale_arg))
                                bands.append(band_data)
                            
                            # Stack into array
                            arr = np.stack(bands, axis=-1).astype(np.float32) if len(bands) > 1 else bands[0].astype(np.float32)
                            
                            # Handle no-data values
                            nd = src.nodata
                            if nd is not None:
                                if arr.ndim == 3:
                                    arr[arr == nd] = np.nan
                                else:
                                    arr[arr == nd] = np.nan
                            
                            # Final assignments
                            self.data = arr
                            self._transform = src.transform
                            self._crs = src.crs
                            self.band_count = arr.shape[2] if arr.ndim == 3 else 1
                            self.band_index = 0
                            self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)
                            
                            if self.band_count > 1:
                                print(f"Loaded {self.band_count} bands — switch with [ and ] keys.")
                            else:
                                print("Loaded 1 band.")
                    except Exception as e:
                        raise RuntimeError(
                            f"Failed to read HDF/NetCDF file: {e}\n"
                            "For full HDF support, install GDAL: pip install GDAL"
                        )

            # --------------------- Regular GeoTIFF --------------------- #
            else:
                if tif_path and os.path.dirname(tif_path).endswith(".gdb"):
                    tif_path = f"OpenFileGDB:{os.path.dirname(tif_path)}:{os.path.basename(tif_path)}"

                import rasterio as rio_module
                with rio_module.open(tif_path) as src:
                    self._transform = src.transform
                    self._crs = src.crs
                    if rgb is not None:
                        bands = [src.read(b, out_shape=(src.height // self._scale_arg, src.width // self._scale_arg))
                                for b in rgb]
                        arr = np.stack(bands, axis=-1).astype(np.float32)
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
                        nd = src.nodata
                        if nd is not None:
                            arr[arr == nd] = np.nan
                        self.data = arr
                        self.band_count = src.count

                        # single-band display range (fast stats or fallback)
                        try:
                            stats = src.stats(self.band)
                            if stats and stats.min is not None and stats.max is not None:
                                self.vmin, self.vmax = stats.min, stats.max
                            else:
                                raise ValueError("No stats in file")
                        except Exception:
                            self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)

        else:
            raise ValueError("Provide a TIFF path or --rgbfiles.")

        # Window title
        self.update_title()

        # State
        self.contrast = 1.0
        self.gamma = 1.0

        # Colormap (single-band)
        # For NetCDF temperature data, have three colormaps in rotation
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
        
        # Add time navigation controls at the top for NetCDF files
        if hasattr(self, '_has_time_dim') and self._has_time_dim:
            self.time_layout = QHBoxLayout()
            self.time_layout.setContentsMargins(10, 5, 10, 5)
            
            # Navigation buttons
            self.prev_button = QPushButton("<<")
            self.prev_button.setToolTip("Previous time step")
            self.prev_button.clicked.connect(self.prev_time_step)
            self.prev_button.setFixedWidth(40)
            
            # Play/Pause button
            self.play_button = QPushButton("▶")  # Play symbol
            self.play_button.setToolTip("Play/Pause time animation")
            self.play_button.clicked.connect(self.toggle_play_pause)
            self.play_button.setFixedWidth(40)
            self._is_playing = False
            self._play_timer = None
            
            self.next_button = QPushButton(">>")
            self.next_button.setToolTip("Next time step")
            self.next_button.clicked.connect(self.next_time_step)
            self.next_button.setFixedWidth(40)
            
            # Date/time label
            self.time_label = QLabel()
            self.time_label.setMinimumWidth(200)
            
            # Time slider
            self.time_slider = QSlider(Qt.Orientation.Horizontal)
            self.time_slider.setMinimum(0)
            self.time_slider.setMaximum(len(self._time_values) - 1)
            self.time_slider.setValue(self._time_index)
            self.time_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.time_slider.setTickInterval(max(1, len(self._time_values) // 10))
            self.time_slider.valueChanged.connect(self.time_slider_changed)
            
            # Date time combo box
            self.date_combo = QComboBox()
            self.populate_date_combo()
            self.date_combo.currentIndexChanged.connect(self.date_combo_changed)
            self.date_combo.setFixedWidth(200)
            
            # Add controls to layout
            self.time_layout.addWidget(self.prev_button)
            self.time_layout.addWidget(self.play_button)
            self.time_layout.addWidget(self.time_label)
            self.time_layout.addWidget(self.time_slider, 1)  # 1 = stretch factor
            self.time_layout.addWidget(self.next_button)
            self.time_layout.addWidget(QLabel("Jump to:"))
            self.time_layout.addWidget(self.date_combo)
            
            # Add time controls to main layout at the top
            self.main_layout.addLayout(self.time_layout)
            
            # Update time label
            self.update_time_label()
        
        # Scene + view
        self.scene = QGraphicsScene(self)
        self.view = RasterView(self.scene, self)
        self.main_layout.addWidget(self.view)
        
        # Status bar
        self.setStatusBar(QStatusBar())
        
        # Set central widget
        self.setCentralWidget(self.main_widget)

        self.pixmap_item = None
        self._last_rgb = None
        self.update_pixmap()

        # Overlays (if any)
        if self._shapefiles:
            self._add_shapefile_overlays()

        self.resize(1200, 800)

        if self.pixmap_item is not None:
            rect = self.pixmap_item.boundingRect()
            self.scene.setSceneRect(rect)
            self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            self.view.scale(5, 5)
            self.view.centerOn(self.pixmap_item)

    # ---------------------------- Overlays ---------------------------- #
    def _geo_to_pixel(self, x: float, y: float):
        """Map coords (raster CRS) -> image pixel coords (after downsampling)."""
        if self._transform is None:
            return None
        inv = ~self._transform  # (col, row) from (x, y)
        col, row = inv * (x, y)
        return (col / self._scale_arg, row / self._scale_arg)

    def _geom_to_qpath(self, geom) -> QPainterPath | None:
        """
        Convert shapely geom (in raster CRS) to QPainterPath in *image pixel* coords.
        Z/M tolerant: only X,Y are used. Draws Points as tiny segments.
        """
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
        if not HAVE_GEO:
            print("[WARN] geopandas/shapely not available; --shapefile ignored.")
            return
        if self._crs is None or self._transform is None:
            print("[WARN] raster lacks CRS/transform; cannot place overlays.")
            return

        pen = QPen(QColor(self._shp_color))
        pen.setWidthF(self._shp_width)
        pen.setCosmetic(True)  # constant on-screen width

        for shp_path in self._shapefiles:
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

    # ----------------------- Title / Rendering ----------------------- #
    def update_title(self):
        if self.rgbfiles:
            names = [os.path.basename(n) for n in self.rgbfiles]
            self.setWindowTitle(f"RGB ({', '.join(names)})")
        elif self.rgb_mode and self.rgb:
            self.setWindowTitle(f"RGB {self.rgb} — {os.path.basename(self.tif_path)}")
        elif hasattr(self, "band_index"):
            title = f"Band {self.band_index + 1}/{self.band_count} — {os.path.basename(self.tif_path)}"
            if hasattr(self, '_has_time_dim') and self._has_time_dim:
                time_str = self.format_time_value(self._time_values[self._time_index])
                if len(time_str) > 15:  # Truncate if too long for title
                    time_str = time_str[:15]
                
                # Use generic label for non-time dimensions
                if self._time_dim_name and self._time_dim_name != 'time':
                    title += f" - {self._time_dim_name}: {time_str} ({self._time_index + 1}/{len(self._time_values)})"
                else:
                    title += f" - {time_str}"
            self.setWindowTitle(title)
        else:
            self.setWindowTitle(f"Band {self.band}/{self.band_count} — {os.path.basename(self.tif_path)}")
            
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
            
    def update_time_label(self):
        """Update the time label with the current time value"""
        if hasattr(self, '_has_time_dim') and self._has_time_dim:
            try:
                time_value = self._time_values[self._time_index]
                time_str = self.format_time_value(time_value)
                
                # Update time label if it exists
                if hasattr(self, 'time_label'):
                    self.time_label.setText(f"Time: {time_str}")
                
                # Create a progress bar style display of time position
                total = len(self._time_values)
                position = self._time_index + 1
                bar_width = 20  # Width of the progress bar
                filled = int(bar_width * position / total)
                bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
                
                # Show time info in status bar
                step_info = f"Time step: {position}/{total} {bar} {self.format_time_value(self._time_values[self._time_index])}"
                
                # Update status bar if it exists
                if hasattr(self, 'statusBar') and callable(self.statusBar):
                    self.statusBar().showMessage(step_info)
                else:
                    print(step_info)
            except Exception as e:
                print(f"Error updating time label: {e}")
            
    def time_slider_changed(self, value):
        """Handle time slider value change"""
        if hasattr(self, '_has_time_dim') and self._has_time_dim:
            self._time_index = value
            
            # Update data for new time step
            if self._time_dim_name:
                # Use the named dimension (time or other index)
                var_data = self._nc_var_data.isel({self._time_dim_name: self._time_index})
            else:
                # Fallback to 'time' dimension
                var_data = self._nc_var_data.isel(time=self._time_index)
                
            self.data = var_data.values.astype(np.float32)
            
            # Find and select the matching date in the combo box (if it exists)
            if hasattr(self, 'date_combo'):
                try:
                    for i in range(self.date_combo.count()):
                        if self.date_combo.itemData(i) == value:
                            self.date_combo.blockSignals(True)  # Block signals to avoid recursion
                            self.date_combo.setCurrentIndex(i)
                            self.date_combo.blockSignals(False)
                            break
                except Exception as e:
                    print(f"Error updating combo box: {e}")
            
            # Update UI
            self.update_time_label()
            self.update_pixmap()
            self.update_title()
            
    def next_time_step(self):
        """Go to the next time step"""
        if hasattr(self, '_has_time_dim') and self._has_time_dim:
            try:
                next_time = (self._time_index + 1) % len(self._time_values)
                if hasattr(self, 'time_slider'):
                    self.time_slider.setValue(next_time)
                else:
                    # Direct update if slider doesn't exist
                    self._time_index = next_time
                    self.update_time_data()
            except Exception as e:
                print(f"Error going to next time step: {e}")
            
    def prev_time_step(self):
        """Go to the previous time step"""
        if hasattr(self, '_has_time_dim') and self._has_time_dim:
            try:
                prev_time = (self._time_index - 1) % len(self._time_values)
                if hasattr(self, 'time_slider'):
                    self.time_slider.setValue(prev_time)
                else:
                    # Direct update if slider doesn't exist
                    self._time_index = prev_time
                    self.update_time_data()
            except Exception as e:
                print(f"Error going to previous time step: {e}")
                
    def update_time_data(self):
        """Update data for the current time index"""
        try:
            # Update data for current time step
            if self._time_dim_name:
                # Use the named dimension (time or other index)
                var_data = self._nc_var_data.isel({self._time_dim_name: self._time_index})
            else:
                # Fallback to 'time' dimension
                var_data = self._nc_var_data.isel(time=self._time_index)
                
            self.data = var_data.values.astype(np.float32)
            
            # Update UI
            self.update_time_label()
            self.update_pixmap()
            self.update_title()
        except Exception as e:
            print(f"Error updating time data: {e}")
            
    def toggle_play_pause(self):
        """Toggle play/pause animation of time steps"""
        if self._is_playing:
            self.stop_animation()
        else:
            self.start_animation()
    
    def start_animation(self):
        """Start the time animation"""
        from PySide6.QtCore import QTimer
        
        if not hasattr(self, '_play_timer') or self._play_timer is None:
            self._play_timer = QTimer(self)
            self._play_timer.timeout.connect(self.animation_step)
        
        # Set animation speed (milliseconds between frames)
        animation_speed = 500  # 0.5 seconds between frames
        self._play_timer.start(animation_speed)
        
        self._is_playing = True
        self.play_button.setText("⏸")  # Pause symbol
        self.play_button.setToolTip("Pause animation")
    
    def stop_animation(self):
        """Stop the time animation"""
        if hasattr(self, '_play_timer') and self._play_timer is not None:
            self._play_timer.stop()
        
        self._is_playing = False
        self.play_button.setText("▶")  # Play symbol
        self.play_button.setToolTip("Play animation")
    
    def animation_step(self):
        """Advance one frame in the animation"""
        # Go to next time step
        next_time = (self._time_index + 1) % len(self._time_values)
        self.time_slider.setValue(next_time)
    
    def closeEvent(self, event):
        """Clean up resources when the window is closed"""
        # Stop animation timer if it's running
        if hasattr(self, '_is_playing') and self._is_playing:
            self.stop_animation()
        
        # Call the parent class closeEvent
        super().closeEvent(event)
            
    def populate_date_combo(self):
        """Populate the date combo box with time values"""
        if hasattr(self, '_has_time_dim') and self._has_time_dim and hasattr(self, 'date_combo'):
            try:
                self.date_combo.clear()
                
                # Add a reasonable subset of dates if there are too many
                max_items = 100  # Maximum number of items to show in dropdown
                
                if len(self._time_values) <= max_items:
                    # Add all time values
                    for i, time_value in enumerate(self._time_values):
                        time_str = self.format_time_value(time_value)
                        self.date_combo.addItem(time_str, i)
                else:
                    # Add a subset of time values
                    step = len(self._time_values) // max_items
                    
                    # Always include first and last
                    indices = list(range(0, len(self._time_values), step))
                    if (len(self._time_values) - 1) not in indices:
                        indices.append(len(self._time_values) - 1)
                    
                    for i in indices:
                        time_str = self.format_time_value(self._time_values[i])
                        self.date_combo.addItem(f"{time_str} [{i+1}/{len(self._time_values)}]", i)
            except Exception as e:
                print(f"Error populating date combo: {e}")
                    
    def date_combo_changed(self, index):
        """Handle date combo box selection change"""
        if index >= 0:
            time_index = self.date_combo.itemData(index)
            if time_index is not None:
                self.time_slider.setValue(time_index)

    def _render_rgb(self):
        if self.rgb_mode:
            arr = self.data
            finite = np.isfinite(arr)
            rgb = np.zeros_like(arr)
            if np.any(finite):
                # Global 2–98 percentile stretch across all bands (QGIS-like)
                global_min = np.nanpercentile(arr, 2)
                global_max = np.nanpercentile(arr, 98)
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
        """Render a NetCDF variable with cartopy for better geographic visualization"""
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        
        # Create a new figure with cartopy projection
        fig = plt.figure(figsize=(12, 8), dpi=100)
        ax = plt.axes(projection=ccrs.PlateCarree())
        
        # Get coordinates
        lons = self._lon_data
        lats = self._lat_data
        
        # Create contour plot
        levels = 20
        if hasattr(plt.cm, self.cmap_name):
            cmap = getattr(plt.cm, self.cmap_name)
        else:
            cmap = getattr(cm, self.cmap_name, cm.viridis)
        
        # Apply contrast and gamma adjustments
        finite = np.isfinite(data)
        norm_data = np.zeros_like(data, dtype=np.float32)
        vmin, vmax = np.nanmin(data), np.nanmax(data)
        rng = max(vmax - vmin, 1e-12)
        
        if np.any(finite):
            norm_data[finite] = (data[finite] - vmin) / rng
        
        norm_data = np.clip(norm_data * self.contrast, 0.0, 1.0)
        norm_data = np.power(norm_data, self.gamma)
        norm_data = norm_data * rng + vmin
        
        # Create the plot
        contour = ax.contourf(lons, lats, data, 
                            transform=ccrs.PlateCarree(),
                            levels=levels, cmap=cmap)
        
        # Add map features
        ax.coastlines()
        ax.add_feature(cfeature.BORDERS, linestyle=':')
        ax.gridlines(draw_labels=True)
        
        # Add title with variable name and time if available
        title = f"{self._nc_var_name}"
        if hasattr(self, '_has_time_dim') and self._has_time_dim:
            time_str = str(self._time_values[self._time_index])
            title += f"\n{time_str}"
        ax.set_title(title)
        
        # Add colorbar
        plt.colorbar(contour, ax=ax, shrink=0.6)
        
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
        
        return rgb
        
    def update_pixmap(self):
        # --- Select display data ---
        if hasattr(self, "band_index"):
            # HDF or scientific multi-band
            if self.data.ndim == 3:
                a = self.data[:, :, self.band_index]
            else:
                a = self.data
            rgb = None
        else:
            # Regular GeoTIFF (could be RGB or single-band)
            if self.rgb_mode:  # user explicitly passed --rgb or --rgbtiles
                rgb = self.data
                a = None
            else:
                a = self.data
                rgb = None
        # ----------------------------

        # --- Render image ---
        # Check if we should use cartopy for NetCDF visualization
        use_cartopy = False
        if hasattr(self, '_use_cartopy') and self._use_cartopy and HAVE_CARTOPY:
            if hasattr(self, '_has_geo_coords') and self._has_geo_coords:
                use_cartopy = True
                
        if use_cartopy:
            # Render with cartopy for better geographic visualization
            rgb = self._render_cartopy_map(a)
        elif rgb is None:
            # Standard grayscale rendering for single-band (scientific) data
            finite = np.isfinite(a)
            vmin, vmax = np.nanmin(a), np.nanmax(a)
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
        # ----------------------

        h, w, _ = rgb.shape
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

        tif_path = self.tif_path
      
        if tif_path and os.path.dirname(self.tif_path).endswith(".gdb"):
            tif_path = f"OpenFileGDB:{os.path.dirname(self.tif_path)}:{os.path.basename(self.tif_path)}"

        import rasterio as rio_module
        with rio_module.open(tif_path) as src:
            self.band = band_num
            arr = src.read(self.band).astype(np.float32)
            nd = src.nodata
            if nd is not None:
                arr[arr == nd] = np.nan
            self.data = arr
            self.vmin, self.vmax = np.nanmin(arr), np.nanmax(arr)
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

        # Contrast / Gamma now work in both modes
        elif k == Qt.Key.Key_C:
            self.contrast *= 1.1; self.update_pixmap()
        elif k == Qt.Key.Key_V:
            self.contrast /= 1.1; self.update_pixmap()
        elif k == Qt.Key.Key_G:
            self.gamma *= 1.1; self.update_pixmap()
        elif k == Qt.Key.Key_H:
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
            self.update_pixmap()

        # Band switch
        elif k == Qt.Key.Key_BracketRight:
            if hasattr(self, "band_index"):  # HDF/NetCDF mode
                self.band_index = (self.band_index + 1) % self.band_count
                self.update_pixmap()
                self.update_title()
            elif not self.rgb_mode:  # GeoTIFF single-band mode
                new_band = self.band + 1 if self.band < self.band_count else 1
                self.load_band(new_band)

        elif k == Qt.Key.Key_BracketLeft:
            if hasattr(self, "band_index"):  # HDF/NetCDF mode
                self.band_index = (self.band_index - 1) % self.band_count
                self.update_pixmap()
                self.update_title()
            elif not self.rgb_mode:  # GeoTIFF single-band mode
                new_band = self.band - 1 if self.band > 1 else self.band_count
                self.load_band(new_band)
                
        # NetCDF time/dimension navigation with Page Up/Down
        elif k == Qt.Key.Key_PageUp:
            if hasattr(self, '_has_time_dim') and self._has_time_dim:
                try:
                    # Call the next_time_step method
                    self.next_time_step()
                except Exception as e:
                    print(f"Error handling PageUp: {e}")
                
        elif k == Qt.Key.Key_PageDown:
            if hasattr(self, '_has_time_dim') and self._has_time_dim:
                try:
                    # Call the prev_time_step method
                    self.prev_time_step()
                except Exception as e:
                    print(f"Error handling PageDown: {e}")

        elif k == Qt.Key.Key_R:
            self.contrast = 1.0
            self.gamma = 1.0
            self.update_pixmap()
            self.view.resetTransform()
            self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            super().keyPressEvent(ev)


# --------------------------------- CLI ----------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="TIFF viewer with RGB (2–98%) & shapefile overlays")
    parser.add_argument("tif_path", nargs="?", help="Path to TIFF (optional if --rgbfiles is used)")
    parser.add_argument("--scale", type=int, default=1, help="Downsample factor (1=full, 10=10x smaller)")
    parser.add_argument("--band", type=int, default=1, help="Band number (ignored if --rgb/--rgbfiles used)")
    parser.add_argument("--rgb", nargs=3, type=int, help="Three band numbers for RGB, e.g. --rgb 4 3 2")
    parser.add_argument("--rgbfiles", nargs=3, help="Three single-band TIFFs for RGB, e.g. --rgbfiles B4.tif B3.tif B2.tif")
    parser.add_argument("--shapefile", nargs="+", help="One or more shapefiles to overlay")
    parser.add_argument("--shp-color", default="cyan", help="Overlay color (name or #RRGGBB). Default: cyan")
    parser.add_argument("--shp-width", type=float, default=1.5, help="Overlay line width (screen pixels). Default: 1.5")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    win = TiffViewer(
        args.tif_path,
        scale=args.scale,
        band=args.band,
        rgb=args.rgb,
        rgbfiles=args.rgbfiles,
        shapefiles=args.shapefile,
        shp_color=args.shp_color,
        shp_width=args.shp_width,
    )
    win.show()
    sys.exit(app.exec())


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
    )
    win.show()
    sys.exit(app.exec())

import click

@click.command()
@click.version_option("0.2.0", prog_name="viewtif")
@click.argument("tif_path", required=False)
@click.option("--band", default=1, show_default=True, type=int, help="Band number to display")
@click.option("--scale", default=1.0, show_default=True, type=float, help="Scale factor for display")
@click.option("--rgb", nargs=3, type=int, help="Three band numbers for RGB, e.g. --rgb 4 3 2")
@click.option("--rgbfiles", nargs=3, type=str, help="Three single-band TIFFs for RGB, e.g. --rgbfiles B4.tif B3.tif B2.tif")
@click.option("--shapefile", multiple=True, type=str, help="One or more shapefiles to overlay")
@click.option("--shp-color", default="white", show_default=True, help="Overlay color (name or #RRGGBB).")
@click.option("--shp-width", default=1.0, show_default=True, type=float, help="Overlay line width (screen pixels).")
@click.option("--subset", default=None, type=int, help="Open specific subdataset index in .hdf/.h5 file or variable in NetCDF file")
def main(tif_path, band, scale, rgb, rgbfiles, shapefile, shp_color, shp_width, subset):
    """Lightweight GeoTIFF, NetCDF, and HDF viewer."""
    # --- Warn early if shapefile requested but geopandas missing ---
    if shapefile and not HAVE_GEO:
        print(
            "[WARN] --shapefile requires geopandas and shapely.\n"
            "       Install them with: pip install viewtif[geo]\n"
            "       Proceeding without shapefile overlay."
        )

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
    )

if __name__ == "__main__":
    main()