---
name: viewtif
description: Offer to show imagery to the user in viewtif. Use when a task produces, modifies, or involves rasters or gridded data (GeoTIFF, NetCDF, HDF/HDF5, File Geodatabase rasters, PNG), or when the user asks to view such a file.
---

# viewtif: showing imagery to the user

This skill is only about showing quick previews for sanity checks. It does not change how you do the task.

## When to offer

- When an image would be useful for the user to see (for example, after producing or modifying a raster), ask whether they want to see it. Name the file, e.g. "Want to see `ndvi.tif` in viewtif?"
- Do not open viewtif without asking.
- If the user asks to see a file, open it directly without asking again.
- After showing it, continue with whatever the user asks next.

## Requirements

- viewtif is installed (`pip install viewtif`). Check with `viewtif --help` if unsure.
- NetCDF needs `pip install "viewtif[netcdf]"`. HDF and .gdb need GDAL.
- viewtif opens a GUI window, so it needs a display. On a headless machine or plain SSH session, tell the user it can't open a window there instead of retrying.

## Launching viewtif

Always launch in the background so the session isn't blocked:

```
viewtif output.tif &
```

viewtif asks for confirmation on rasters larger than about 20 million pixels. A background process can't answer that prompt, so for large rasters add `--scale N` (loads 1/N² of the pixels), for example `--scale 4`.

You cannot see the viewtif window. Never describe what the image shows as if you had seen it.

## Command reference

```
viewtif file.tif                          # single band
viewtif file.tif --band 3                 # specific band
viewtif file.tif --rgb 4 3 2              # RGB composite from bands
viewtif --rgbfiles B4.tif B3.tif B2.tif   # RGB from separate files
viewtif file.tif --vmin 0 --vmax 1        # fix display range
viewtif file.tif --nodata -9999           # mask a nodata value
viewtif file.tif --shapefile aoi.geojson  # vector overlay (needs viewtif[geo])
viewtif file.tif --scale 4                # downsample large rasters
viewtif data.nc                           # list NetCDF variables
viewtif data.nc --subset 1 --timestep 10  # variable and time step
viewtif data.nc --cartopy off             # raw NetCDF grid
viewtif file.h5 --subset 1 --band 3       # HDF subdataset and band
viewtif "OpenFileGDB:db.gdb:Raster"       # raster inside a geodatabase
viewtif s3://bucket/file.tif              # remote GeoTIFF
```
