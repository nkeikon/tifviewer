# Examples

This folder contains small demo data to test **viewtif**.

## Quick test
After installing viewtif, run:

```bash
viewtif examples/sample_data/ECOSTRESS_LST.tif
viewtif examples/sample_data/ECOSTRESS_LST.tif --shapefile examples/Zip_Codes.shp
viewtif --rgbfiles examples/sample_data/HLS_B4.tif examples/sample_data/HLS_B3.tif examples/sample_data/HLS_B2.tif
```

Controls
| Key                           | Action                                | Mode        |
| ----------------------------- | ------------------------------------- | ----------- |
| `+` / `-`                     | Zoom in / out                         | All         |
| Arrow keys or `W` `A` `S` `D` | Pan                                   | All         |
| `C` / `V`                     | Decrease / increase contrast          | All         |
| `G` / `H`                     | Decrease / increase gamma             | All         |
| `M`                           | Toggle colormap (`viridis` ↔ `magma`) | Single-band |
| `[` / `]`                     | Previous / next band                  | Single-band |
| `R`                           | Reset view                            | All         |

