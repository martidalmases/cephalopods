# Trend Detective: website concept

Concept site for the NASA Space Apps 2026 challenge "Be An Earth System Trend
Detective!". It currently runs on **synthetic data**. The site only reads the
JSON files in `data/`, so real data can replace the fake data without touching
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
| `pipeline/make_fake_data.py` | Writes everything in `data/`. Replace its `fake_field()` with real data. |

Regenerate the fake data: `pip install numpy scipy global-land-mask`, then
`python3 pipeline/make_fake_data.py`.

## What the site does

- **Left panel:** region, period and the variable list with a plain-language definition.
  In "Stack by variable" the list becomes tick boxes: tick up to four variables to stack.
- **Visualisation options:** Stack by time, Stack by variable, Trend map.
- **Stacks:** four layers in focus, plus a grey layer above and below showing there is more.
  Mouse wheel, arrow keys or the buttons under the view move through them with a smooth slide.
  - By time: one layer per year, newest on top.
  - By variable: one layer per ticked variable, coloured by trend direction.
- **Opening a map:** click a layer and it turns to face you as a flat map. Click a point, or drag
  a box to select an area. "Back to stack" (or Esc) returns. The up/down buttons step to the
  next layer while the map stays open.
- **Trend map:** the flat map of rate of change per decade for one variable, hatched where the
  trend is not significant.
- **Right panel (round handle on its edge opens and closes it):**
  - One variable: time series, fitted trend, rate per decade and a plain-language verdict.
  - Stack by variable: one small graph per stacked variable, for the same selection.
  - A table of every variable's trend for the current selection.

### Colours

- Time stack: every layer uses the **same** colour range, so years can be compared. The reference
  is the average of the whole sea over all years (default), so every point is coloured against the
  same number. A switch under the view changes it to each place's own average over all years.
- Variable stack and trend map: one fixed range per variable, because the units differ.

### Where the statistics are computed

- Per-cell and whole-region trends come from the Python pipeline (in the JSON files).
- Area selections are averaged (area-weighted) and fitted **in the browser** with the same method:
  least-squares slope and a two-sided t-test. See `ols()` in `app.js`.

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

No missing values are supported yet inside `values`: a cell is either in `ocean_index` with a full
series or left out.

## Plugging in real data

1. In `make_fake_data.py`, replace `fake_field(var, mask, rng)` with a function returning an array
   of shape `(n_years, n_ocean_cells)` on the same grid (regrid and average months to years first).
2. Keep `trend_stats()` and `write_dataset()` as they are, or swap `trend_stats()` for a
   Mann-Kendall test and Sen slope; the site only needs a slope and a p-value.
3. Update each variable's `unit`, `decimals`, `definition` and `source`.
4. To change region or resolution, change `LON0`, `LAT0`, `D`, `NX`, `NY` and the mask function.

## Known limits of the concept

- Yearly values only. Monthly data would need the seasonal cycle removed first.
- The trend test is ordinary least squares with a t-test. It does not correct for autocorrelation
  or for testing thousands of cells at once.
- Colours use one diverging blue/red scale for every variable.
- The logo and project name are placeholders.
