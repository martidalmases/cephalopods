# Trend Detective: website concept

Concept site for the NASA Space Apps 2026 challenge "Be An Earth System Trend
Detective!". It runs on **real NASA and NOAA data** for 2003–2025 (see "Real data").
The site only reads the JSON files in `data/`, so the data can be refreshed without touching
the website code.

Code, synthetic data and this document were generated with AI assistance, from
the team's layout sketch and visualisation choices.

## Run it

```
python3 -m http.server 8000      # from this folder
# open http://localhost:8000
```

Opening `index.html` directly from disk does not work: browsers block loading
the JSON files that way.

## Files

| File | Role |
|---|---|
| `index.html`, `style.css`, `app.js` | The website. Plain HTML/CSS/JS, no build step, no dependencies. |
| `data/meta.json` | Region, grid, years, list of variables. |
| `data/<id>.json` | One file per variable: yearly values, trend, significance. |
| `pipeline/fetch_real_data.py` | Downloads the real data and writes everything in `data/`. |
| `pipeline/make_fake_data.py` | Offline fallback: writes synthetic data in the same format. |

Regenerate the fake data: `pip install numpy scipy global-land-mask`, then
`python3 pipeline/make_fake_data.py`.

## What the site does

Two screens, switched at the top left: **Visualisation** and **Analysis**.

### Visualisation

- **Left panel:** region, period and the variable list. In "Stack by variable" the list becomes
  tick boxes (up to four). "What is this?" explains every selected variable.
- **Options:** Stack by time, Stack by variable, Trend map.
- **Stacks:** four layers in focus plus a grey one above and below. The mouse wheel glides the
  stack and it then snaps to four whole layers; the buttons and arrow keys move one layer.
  Each layer has its name under its left corner and a reading for the current selection above
  its right corner.
- **Opening a map:** click a layer and it turns to face you. Click a point or drag a box to select
  an area. The wheel zooms (Shift + drag moves the zoomed map). The buttons step to the next
  layer. "Back to stack" or Esc returns.
- **Trend map:** rate of change per decade for one variable, hatched where not significant.
- **Right panel:** graphs only, no numbers. One graph for one variable, or one small graph per
  stacked variable, plus a table saying Higher / Lower / No change and whether it is significant.

### Analysis

The maps slide away and the graph panel takes the space. For the current selection it shows:

- the time series of the ticked variables with rate per decade and p-value,
- a table of every variable: rate per decade, p-value, direction, significance,
- a correlation matrix of all variables (Pearson r of the yearly values), with a switch to
  remove each variable's trend first.

"No change" in the tables means the slope is smaller than 10% of that variable's colour range
(`direction()` in `app.js`); it is a display threshold, not a statistical test.

### Colours

- Time stack: every layer uses the **same** colour range, so years can be compared. The reference
  is the average of the whole sea over all years (default), so every point is coloured against the
  same number. A switch under the view changes it to each place's own average over all years.
- Variable stack and trend map: one fixed range per variable, because the units differ.

### Where the statistics are computed

- Per-cell and whole-region trends come from the Python pipeline (in the JSON files).
- Area selections are averaged (area-weighted) and fitted **in the browser** with the same method:
  least-squares slope and a two-sided t-test. See `ols()` in `app.js`.
- Correlations are computed in the browser (`pearson()` and `detrend()` in `app.js`).

## Data format

### `data/meta.json`

```json
{
  "synthetic": true,
  "region": {"name": "Mediterranean Sea", "lon_west": -6.0, "lat_north": 46.0,
             "cell_deg": 0.25, "nx": 170, "ny": 64},
  "years": [2003, 2004, "...", 2025],
  "ocean_index": [1234, 1235, "..."],
  "variables": [
    {"id": "sst", "name": "Sea surface temperature", "unit": "°C", "decimals": 2,
     "definition": "How warm the top layer of the sea is.",
     "file": "sst.json", "source": "synthetic"}
  ]
}
```

- The grid is regular in longitude and latitude. Row 0 is the **northernmost** row, column 0 the westernmost.
- `ocean_index` lists the sea cells as `row * nx + col`. Every per-cell array in the variable files
  has one entry per item of `ocean_index`, in the same order. Land cells are not stored.
- Set `"synthetic": false` to remove the warning label. Put the real dataset name in `source`.

### `data/<id>.json`

```json
{
  "id": "sst",
  "values": [[...], [...]],
  "trend": {"slope_per_decade": [...], "p": [...], "method": "least-squares slope, two-sided t-test"},
  "regional": {"mean": [...], "slope_per_decade": 0.343, "p": 0.0},
  "scale": {"anomaly": 0.775, "slope": 0.526}
}
```

| Field | Shape | Meaning |
|---|---|---|
| `values` | years x ocean cells | Yearly mean in the variable's unit |
| `trend.slope_per_decade` | ocean cells | Fitted rate of change per decade |
| `trend.p` | ocean cells | p-value of the trend test; the site treats p < 0.05 as significant |
| `regional.mean` | years | Area-weighted average over the region |
| `regional.slope_per_decade`, `regional.p` | number | Trend of that average |
| `scale.anomaly`, `scale.slope` | number | Value mapped to full colour (98th percentile of the absolute anomaly / slope) |

Missing values are `null` (in `values`, `trend`, `regional`). The site skips them in averages, fits,
correlations and charts, and draws them as "no data".

## Real data

`pipeline/fetch_real_data.py` downloads **monthly** fields for the whole Mediterranean, averages
them into yearly values on the backend's 0.5° grid (86 × 33 cells, `backend/almacen.py`), fits the
trends and writes `data/`.

```
pip install numpy scipy requests
EARTHDATA_TOKEN=... python3 pipeline/fetch_real_data.py      # about 30 min, writes ./data
python3 pipeline/fetch_real_data.py --only chl nflh --out /tmp/data
python3 pipeline/fetch_real_data.py --selftest
```

| Variables | Source | Years |
|---|---|---|
| Sea surface temperature, sea-air temperature difference | NASA MERRA-2 monthly `M2TMNXSLV` (TS, T2M) | 2003–2025 |
| Aerosol optical depth | MERRA-2 `M2TMNXAER` (TOTEXTTAU) | 2003–2025 |
| Wind speed, evaporation, precipitation, latent and sensible heat flux | MERRA-2 `M2TMNXFLX` | 2003–2025 |
| Chlorophyll-a, fluorescence line height | NASA MODIS Aqua monthly, R2022 science quality, gaps filled with the NRT version (NOAA CoastWatch ERDDAP) | 2003–2025 |

- MERRA-2 is read through NASA's OPeNDAP service, which needs a NASA Earthdata token with the
  **"NASA GESDISC DATA ARCHIVE"** application approved in the Earthdata profile. NOAA needs no login.
- A year counts only with at least 10 monthly values; a trend only with at least 8 yearly values.
  Missing values are `null` in the JSON and shown as "no data" on the site.
- Sea cells: inside the backend's Mediterranean cut-out and seen at least once by an ocean satellite.

## Known limits of the concept

- Yearly values only. Monthly data would need the seasonal cycle removed first.
- MERRA-2 is a reanalysis (a model constrained by observations), not a direct measurement.
- The MODIS fluorescence decline is partly sensor ageing, not only a change in the sea.
- Pixels are thinned to about 0.5° rather than averaged, as in the backend; coastal cells can be noisy.
- The trend test is ordinary least squares with a t-test. It does not correct for autocorrelation
  or for testing thousands of cells at once.
- Colours use one diverging blue/red scale for every variable.
