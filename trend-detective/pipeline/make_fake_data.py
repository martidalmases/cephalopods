"""
Generate SYNTHETIC data for the Trend Detective website, in the exact file
format the site reads (see DATA_FORMAT.md).

    python3 pipeline/make_fake_data.py          # writes ./data/*.json

To plug in real data, keep `write_dataset()` and `trend_stats()` and replace
`fake_field()` with code that returns the real yearly values on the same grid.

Code and synthetic data generated with AI assistance.
"""
import json
import os
import numpy as np
from scipy import stats
from scipy.ndimage import gaussian_filter, label
from global_land_mask import globe

OUT = os.path.join(os.path.dirname(__file__), "..", "data")

# ---- grid: Mediterranean, 0.25 degree, row 0 = northernmost -----------------
LON0, LAT0, D = -6.0, 46.0, 0.25          # west edge, north edge, cell size
NX, NY = 170, 64                          # -6..36.5 E, 46..30 N
YEARS = list(range(2003, 2026))
lon = LON0 + D * (np.arange(NX) + 0.5)
lat = LAT0 - D * (np.arange(NY) + 0.5)
LON, LAT = np.meshgrid(lon, lat)


def mediterranean_mask():
    """True where the cell is Mediterranean sea (Atlantic and Black Sea removed)."""
    ocean = globe.is_ocean(LAT, LON)
    ocean &= LON > -5.6                                   # cut the Atlantic at Gibraltar
    ocean &= ~((LON > 26.6) & (LAT > 40.0))               # cut Marmara / Black Sea
    lab, _ = label(ocean)
    seed = lab[np.argmin(np.abs(lat - 35.0)), np.argmin(np.abs(lon - 18.0))]
    return lab == seed                                    # keep the basin connected to 35N 18E


# ---- variables ---------------------------------------------------------------
# x, y are 0..1 across the box (x: west->east, y: south->north).
VARIABLES = [
    dict(id="sst", name="Sea surface temperature", unit="°C", decimals=2,
         definition="How warm the top layer of the sea is.",
         base=lambda x, y: 21 - 4 * y + 2 * x, slope=lambda x, y: 0.22 + 0.22 * x, noise=0.28),
    dict(id="chl", name="Chlorophyll-a", unit="mg/m³", decimals=3,
         definition="The green pigment in microscopic algae. More chlorophyll means more algae in the water.",
         base=lambda x, y: 0.07 + 0.30 * y * (1 - 0.6 * x), slope=lambda x, y: 0.020 * (y - 0.55) - 0.006 * x, noise=0.012),
    dict(id="aph", name="Phytoplankton absorption", unit="1/m", decimals=4,
         definition="How much light the algae soak up. Another way to measure how much algae there is.",
         base=lambda x, y: 0.010 + 0.022 * y, slope=lambda x, y: 0.0016 * (y - 0.55) - 0.0005 * x, noise=0.0011),
    dict(id="dfe", name="Dissolved iron", unit="nmol/L", decimals=3,
         definition="Iron dissolved in seawater. Algae need it to grow; much of it arrives as desert dust.",
         base=lambda x, y: 0.5 + 0.5 * (1 - y), slope=lambda x, y: 0.05 * np.tanh((0.5 - y) * 4), noise=0.05),
    dict(id="no3", name="Nitrate", unit="µmol/L", decimals=3,
         definition="A nutrient that feeds algae, like fertiliser on land.",
         base=lambda x, y: 0.4 + 2.2 * y * (1 - 0.5 * x), slope=lambda x, y: 0.07 * (0.45 - x), noise=0.06),
    dict(id="nflh", name="Fluorescence line height", unit="mW/cm²/µm/sr", decimals=4,
         definition="A faint red glow given off by algae. Useful near coasts, where chlorophyll readings are less reliable.",
         base=lambda x, y: 0.012 + 0.03 * y, slope=lambda x, y: 0.0022 * np.tanh((y - 0.5) * 3), noise=0.0018),
    dict(id="aod", name="Aerosol optical depth", unit="", decimals=3,
         definition="How hazy the air is from dust, smoke and pollution. Higher means less sunlight reaches the sea.",
         base=lambda x, y: 0.14 + 0.12 * (1 - y), slope=lambda x, y: 0.014 * np.tanh((x - 0.45) * 4), noise=0.012),
    dict(id="sss", name="Sea surface salinity", unit="PSU", decimals=3,
         definition="How salty the surface water is. It rises where evaporation beats rain and rivers.",
         base=lambda x, y: 36.4 + 2.6 * x, slope=lambda x, y: 0.06 * np.tanh((x - 0.4) * 4), noise=0.05),
    dict(id="wind", name="Surface wind speed", unit="m/s", decimals=2,
         definition="How fast the air moves just above the sea. Wind mixes the water and drives currents.",
         base=lambda x, y: 5.5 + 2.2 * y, slope=lambda x, y: 0.12 * np.sin((x + y) * 3.5), noise=0.16),
    dict(id="precip", name="Precipitation", unit="mm/day", decimals=3,
         definition="Rain falling on the sea. It freshens the surface water.",
         base=lambda x, y: 0.7 + 1.8 * y, slope=lambda x, y: -0.10 * (1 - y) + 0.04, noise=0.11),
]


def fake_field(var, mask, rng):
    """Return yearly values, shape (n_years, n_ocean). REPLACE THIS for real data."""
    x = (LON - LON0) / (NX * D)
    y = 1 - (LAT0 - LAT) / (NY * D)
    wobble = gaussian_filter(rng.standard_normal((NY, NX)), 7)
    wobble /= np.abs(wobble).max()
    slope = var["slope"](x, y)
    slope = slope + 0.35 * np.abs(slope).max() * wobble          # per decade
    base = var["base"](x, y)
    t = (np.arange(len(YEARS)) - (len(YEARS) - 1) / 2) / 10.0    # decades from mid-period
    common = 0.5 * var["noise"] * np.sin(2 * np.pi * np.arange(len(YEARS)) / 4.3)
    out = np.empty((len(YEARS), int(mask.sum())))
    for k in range(len(YEARS)):
        smooth = gaussian_filter(rng.standard_normal((NY, NX)), 4) * 9
        white = rng.standard_normal((NY, NX)) * 0.5
        field = base + slope * t[k] + common[k] + var["noise"] * (smooth + white)
        out[k] = field[mask]
    return out


def trend_stats(values):
    """Least-squares slope per decade and two-sided t-test p-value, per column."""
    n = values.shape[0]
    t = np.arange(n) - (n - 1) / 2.0
    sxx = (t ** 2).sum()
    mean = values.mean(axis=0)
    b = (t[:, None] * (values - mean)).sum(axis=0) / sxx          # per year
    resid = values - (mean + np.outer(t, b))
    se = np.sqrt((resid ** 2).sum(axis=0) / (n - 2) / sxx)
    p = 2 * stats.t.sf(np.abs(b / se), n - 2)
    return b * 10, p


def rnd(a, d):
    return [round(float(v), d) for v in np.asarray(a).ravel()]


def write_dataset(var, values, mask):
    slope, p = trend_stats(values)
    w = np.cos(np.deg2rad(LAT[mask]))                              # area weights
    regional = (values * w).sum(axis=1) / w.sum()
    rs, rp = trend_stats(regional[:, None])
    anom = values - values.mean(axis=0)
    d = var["decimals"]
    doc = {
        "id": var["id"],
        "values": [rnd(row, d) for row in values],                # [year][ocean cell]
        "trend": {"slope_per_decade": rnd(slope, d + 1), "p": rnd(p, 4),
                  "method": "least-squares slope, two-sided t-test"},
        "regional": {"mean": rnd(regional, d + 1), "slope_per_decade": round(float(rs[0]), d + 1),
                     "p": round(float(rp[0]), 4)},
        "scale": {"anomaly": round(float(np.percentile(np.abs(anom), 98)), d + 1),
                  "slope": round(float(np.percentile(np.abs(slope), 98)), d + 1)},
    }
    with open(os.path.join(OUT, var["id"] + ".json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))


def main():
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(20261114)
    mask = mediterranean_mask()
    meta = {
        "synthetic": True,
        "region": {"name": "Mediterranean Sea", "lon_west": LON0, "lat_north": LAT0,
                   "cell_deg": D, "nx": NX, "ny": NY},
        "years": YEARS,
        "ocean_index": [int(i) for i in np.flatnonzero(mask.ravel())],   # row*nx+col, row 0 = north
        "variables": [{k: v[k] for k in ("id", "name", "unit", "decimals", "definition")}
                      | {"file": v["id"] + ".json", "source": "synthetic"} for v in VARIABLES],
    }
    with open(os.path.join(OUT, "meta.json"), "w") as f:
        json.dump(meta, f, separators=(",", ":"))
    for var in VARIABLES:
        write_dataset(var, fake_field(var, mask, rng), mask)
        print("wrote", var["id"])
    print("ocean cells:", int(mask.sum()))


if __name__ == "__main__":
    main()
