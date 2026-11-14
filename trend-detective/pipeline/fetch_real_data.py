"""
Download REAL monthly data for the Mediterranean and write the Trend Detective
data files (same format as make_fake_data.py, see README "Data format").

    EARTHDATA_TOKEN=... python3 pipeline/fetch_real_data.py           # writes ./data
    python3 pipeline/fetch_real_data.py --out /tmp/data --only chl    # one variable
    python3 pipeline/fetch_real_data.py --selftest

Sources (monthly means, 2003-2025):
  * NASA MERRA-2 monthly collections, subset to the Mediterranean through NASA's
    OPeNDAP service (needs an Earthdata token with "NASA GESDISC DATA ARCHIVE" approved).
  * MODIS Aqua chlorophyll and nFLH, SMOS salinity: NOAA CoastWatch ERDDAP (no login).

Everything is put on the backend's 0.5 degree grid (backend/almacen.py).
Needs: requests, numpy, scipy.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import requests
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from datos_mediterraneo import _token_earthdata  # noqa: E402  (stdlib + requests only)

log = logging.getLogger("fetch")
OUT = Path(__file__).resolve().parent.parent / "data"
YEARS = list(range(2003, 2026))
MIN_MONTHS = 10   # a yearly value needs at least this many monthly values
MIN_YEARS = 8     # a trend needs at least this many yearly values

# Grid copied from backend/almacen.py (not imported: it pulls in xarray and zarr).
PASO = 0.5
LATS = np.round(np.arange(30.0, 46.0 + PASO / 2, PASO), 2)    # 33 rows, south -> north
LONS = np.round(np.arange(-6.0, 36.5 + PASO / 2, PASO), 2)    # 86 columns
_la, _lo = np.meshgrid(LATS, LONS, indexing="ij")
MEDITERRANEO = ~((_lo < -5.5) | ((_la > 43.0) & (_lo < 0.0)) | ((_la > 40.0) & (_lo > 26.5)))
NY, NX = len(LATS), len(LONS)

# ----------------------------------------------------------------------------- variables
# "merra": (collection, expression on that collection's fields)
# "erddap": list of (server, dataset, field, stride, extra dims, first month, last month[, time stride])
CW_PFEG, CW_NOAA = "https://coastwatch.pfeg.noaa.gov/erddap", "https://coastwatch.noaa.gov/erddap"
VARIABLES = [
    dict(id="sst", name="Sea surface temperature", unit="°C", decimals=2,
         definition="How warm the very top of the sea is (skin temperature).",
         source="NASA MERRA-2 monthly (M2TMNXSLV, TS)",
         merra=("M2TMNXSLV", lambda f: f["TS"] - 273.15)),
    dict(id="dt_sea_air", name="Sea-air temperature difference", unit="°C", decimals=2,
         definition="How much warmer the sea surface is than the air 2 m above it. Positive: the sea warms the air.",
         source="NASA MERRA-2 monthly (M2TMNXSLV, TS - T2M)",
         merra=("M2TMNXSLV", lambda f: f["TS"] - f["T2M"])),
    dict(id="chl", name="Chlorophyll-a", unit="mg/m³", decimals=3,
         definition="The green pigment in microscopic algae. More chlorophyll means more algae in the water.",
         source="NASA MODIS Aqua monthly, R2022 science quality (NOAA CoastWatch ERDDAP)",
         erddap=[(CW_PFEG, "erdMH1chlamday_R2022SQ", "chlor_a", 12, [], "2003-01", "2025-12"),
                 (CW_PFEG, "erdMH1chlamday_R2022NRT", "chlorophyll", 12, [], "2022-03", "2025-12")]),
    dict(id="nflh", name="Fluorescence line height", unit="W/m²/µm/sr", decimals=4,
         definition="A faint red glow given off by algae. Useful near coasts, where chlorophyll readings are less reliable.",
         source="NASA MODIS Aqua monthly, R2022 (NOAA CoastWatch ERDDAP)",
         erddap=[(CW_PFEG, "erdMH1cflhmday_R2022SQ", "nflh", 12, [], "2003-01", "2025-12"),
                 (CW_PFEG, "erdMH1cflhmday_R2022NRT", "nflh", 12, [], "2022-03", "2025-12")]),
    dict(id="sss", name="Sea surface salinity", unit="PSU", decimals=2,
         definition="How salty the top layer of the sea is. Satellites only measure it since 2015.",
         source="NASA SMAP daily, every third day averaged to months (NOAA CoastWatch ERDDAP), from 2016",
         # SMOS (2010+) was tried: almost no valid Mediterranean pixels (radio interference), biased low.
         erddap=[(CW_NOAA, "noaacwSMAPsssDaily", "sss", 2, ["(0.0)"], "2016-01", "2025-12", 3)]),
    dict(id="aod", name="Aerosol optical depth", unit="", decimals=3,
         definition="How hazy the air is from dust, smoke and pollution. 0 is perfectly clear.",
         source="NASA MERRA-2 monthly (M2TMNXAER, TOTEXTTAU)",
         merra=("M2TMNXAER", lambda f: f["TOTEXTTAU"])),
    dict(id="wind", name="Surface wind speed", unit="m/s", decimals=2,
         definition="How fast the wind blows just above the sea.",
         source="NASA MERRA-2 monthly (M2TMNXFLX, SPEED)",
         merra=("M2TMNXFLX", lambda f: f["SPEED"])),
    dict(id="evap", name="Evaporation", unit="mm/day", decimals=3,
         definition="How much water the sea loses to the air each day, as a depth of water.",
         source="NASA MERRA-2 monthly (M2TMNXFLX, EVAP)",
         merra=("M2TMNXFLX", lambda f: f["EVAP"] * 86400)),
    dict(id="precip", name="Precipitation", unit="mm/day", decimals=3,
         definition="Rain and snow falling on the sea, as a depth of water per day.",
         source="NASA MERRA-2 monthly (M2TMNXFLX, PRECTOT)",
         merra=("M2TMNXFLX", lambda f: f["PRECTOT"] * 86400)),
    dict(id="lhf", name="Latent heat flux", unit="W/m²", decimals=1,
         definition="Heat the sea loses by evaporating water. Positive: the sea loses heat.",
         source="NASA MERRA-2 monthly (M2TMNXFLX, EFLUX)",
         merra=("M2TMNXFLX", lambda f: f["EFLUX"])),
    dict(id="shf", name="Sensible heat flux", unit="W/m²", decimals=1,
         definition="Heat the sea gives directly to the air above it. Positive: the sea loses heat.",
         source="NASA MERRA-2 monthly (M2TMNXFLX, HFLUX)",
         merra=("M2TMNXFLX", lambda f: f["HFLUX"])),
]
MERRA_FIELDS = {"M2TMNXSLV": ["TS", "T2M"], "M2TMNXAER": ["TOTEXTTAU"],
                "M2TMNXFLX": ["SPEED", "EVAP", "PRECTOT", "EFLUX", "HFLUX"]}


def _get(url: str, tries: int = 4, **kw) -> requests.Response:
    """GET with retries on timeouts and 5xx (ERDDAP and OPeNDAP both hiccup)."""
    for i in range(tries):
        try:
            r = requests.get(url, **kw)
            if r.status_code < 500:
                return r
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = str(e)
        log.warning("retry %d/%d %s: %s", i + 1, tries, url[:120], err)
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"giving up on {url[:120]}: {err}")


def empty() -> np.ndarray:
    return np.full((len(YEARS), 12, NY, NX), np.nan)   # [year][month][lat][lon]


# ----------------------------------------------------------------------------- MERRA-2
# MERRA-2 grid: lat -90..90 step 0.5 (rows 240..272 = 30..46 N, same as ours),
# lon -180..179.375 step 0.625 (columns 278..347 = -6.25..36.875 E), nearest neighbour to ours.
M_LAT, M_LON = (240, 272), (278, 347)
_mlon = -180 + 0.625 * np.arange(M_LON[0], M_LON[1] + 1)
M_COL = np.abs(_mlon[None, :] - LONS[:, None]).argmin(axis=1)


def merra_granules(collection: str) -> dict[tuple[int, int], str]:
    """OPeNDAP URL of each monthly file, from NASA's CMR catalogue."""
    r = _get("https://cmr.earthdata.nasa.gov/search/granules.json", timeout=60, params={
        "short_name": collection, "version": "5.12.4", "provider": "GES_DISC", "page_size": 2000,
        "temporal": f"{YEARS[0]}-01-01T00:00:00Z,{YEARS[-1]}-12-31T23:59:59Z"})
    out = {}
    for e in r.json()["feed"]["entry"]:
        url = next((l["href"] for l in e["links"] if l["href"].startswith("https://opendap.")), None)
        if url:
            out[(int(e["time_start"][:4]), int(e["time_start"][5:7]))] = url
    return out


def parse_ascii(text: str, name: str) -> np.ndarray:
    """Rows of one variable from an OPeNDAP .ascii response ('TS.TS[..][..], v, v' or 'TS[0][0], v, v')."""
    pat = re.compile(rf"^(\w+\.)?{name}\[")
    return np.array([[float(x) for x in line.split(",")[1:]] for line in text.splitlines() if pat.match(line)])


def merra_month(url: str, fields: list[str], token: str) -> dict[str, np.ndarray]:
    box = f"[0:0][{M_LAT[0]}:{M_LAT[1]}][{M_LON[0]}:{M_LON[1]}]"
    r = _get(url + ".ascii?" + ",".join(f + box for f in fields), timeout=120,
             headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200 or not r.text.startswith("Dataset"):
        raise RuntimeError(f"OPeNDAP HTTP {r.status_code} (token valid? 'NASA GESDISC DATA ARCHIVE' "
                           f"approved in your Earthdata profile?): {r.text[:200]}")
    out = {}
    for f in fields:
        a = parse_ascii(r.text, f)
        if a.shape != (NY, M_LON[1] - M_LON[0] + 1):
            raise RuntimeError(f"{f}: unexpected shape {a.shape}")
        a[np.abs(a) > 1e14] = np.nan            # MERRA-2 fill value 1e15
        out[f] = a[:, M_COL]
    return out


def fetch_merra(collection: str, token: str) -> dict[str, np.ndarray]:
    fields = MERRA_FIELDS[collection]
    data = {f: empty() for f in fields}
    granules = merra_granules(collection)
    log.info("%s: %d monthly files", collection, len(granules))
    for n, ((y, m), url) in enumerate(sorted(granules.items()), 1):
        try:
            month = merra_month(url, fields, token)
        except RuntimeError as e:
            if n == 1:
                raise                            # first file fails: almost surely auth, stop early
            log.warning("%s %d-%02d skipped: %s", collection, y, m, e)
            continue
        for f in fields:
            data[f][y - YEARS[0], m - 1] = month[f]
        if n % 24 == 0:
            log.info("%s: %d/%d", collection, n, len(granules))
    return data


# ----------------------------------------------------------------------------- ERDDAP
def fetch_erddap(sources: list) -> np.ndarray:
    """Monthly means per cell. Pixels are thinned with `stride` to about 0.5 deg and
    snapped to the nearest cell, as backend/recolector.py does. A later source only
    fills months/cells the earlier ones left empty (e.g. NRT after science quality)."""
    result = empty()
    for server, dataset, field, stride, extra, first, last, *tstride in sources:
        tstride = tstride[0] if tstride else 1
        total, count = np.zeros_like(result), np.zeros_like(result)
        lat_desc = True
        for year in range(max(int(first[:4]), YEARS[0]), min(int(last[:4]), YEARS[-1]) + 1):
            t0 = f"{first}-01" if year == int(first[:4]) else f"{year}-01-01"
            t1 = f"{last}-28" if year == int(last[:4]) else f"{year}-12-31"
            rows = None
            for _ in range(2):                    # latitude order differs per dataset: try both
                lat = f"(46.0):{stride}:(30.0)" if lat_desc else f"(30.0):{stride}:(46.0)"
                dims = [f"({t0}T00:00:00Z):{tstride}:({t1}T23:59:59Z)", *extra, lat, f"(-6.0):{stride}:(36.5)"]
                url = f"{server}/griddap/{dataset}.json?{field}" + "".join(f"[{d}]" for d in dims)
                r = _get(url, timeout=600)
                if r.status_code == 200:
                    rows = r.json()["table"]["rows"]
                    break
                if "no matching results" in r.text.lower():
                    rows = []
                    break
                lat_desc = not lat_desc
            if rows is None:
                raise RuntimeError(f"{dataset}: HTTP {r.status_code} {r.text[:200]}")
            for row in rows:
                t, la, lo, v = row[0], row[-3], row[-2], row[-1]
                if v is None:
                    continue
                i, j = round((la - LATS[0]) / PASO), round((lo - LONS[0]) / PASO)
                y, m = int(t[:4]), int(t[5:7])
                if 0 <= i < NY and 0 <= j < NX and y in YEARS:
                    total[y - YEARS[0], m - 1, i, j] += v
                    count[y - YEARS[0], m - 1, i, j] += 1
            log.info("%s %d: %d values", dataset, year, len(rows))
        with np.errstate(invalid="ignore"):
            result = np.where(np.isnan(result), total / count, result)   # total/count is NaN where count == 0
    return result


# ----------------------------------------------------------------------------- statistics
def _nanmean(a: np.ndarray, axis: int) -> np.ndarray:
    n = (~np.isnan(a)).sum(axis=axis)
    with np.errstate(invalid="ignore"):
        return np.where(n > 0, np.nansum(a, axis=axis) / np.maximum(n, 1), np.nan)


def yearly(monthly: np.ndarray) -> np.ndarray:
    """[year][lat][lon] mean of the months, NaN when fewer than MIN_MONTHS exist."""
    mean = _nanmean(monthly, axis=1)
    mean[(~np.isnan(monthly)).sum(axis=1) < MIN_MONTHS] = np.nan
    return mean


def trend(v: np.ndarray) -> tuple[float, float]:
    """Least-squares slope per decade and two-sided t-test p-value, ignoring NaN years."""
    ok = ~np.isnan(v)
    n = int(ok.sum())
    if n < MIN_YEARS:
        return np.nan, np.nan
    t = np.flatnonzero(ok).astype(float)
    t, y = t - t.mean(), v[ok]
    b = (t * (y - y.mean())).sum() / (t ** 2).sum()
    resid = y - y.mean() - b * t
    se = np.sqrt((resid ** 2).sum() / (n - 2) / (t ** 2).sum())
    p = 0.0 if se < 1e-12 else 2 * stats.t.sf(abs(b / se), n - 2)
    return b * 10, p


def rnd(a, d: int) -> list:
    return [None if np.isnan(x) else round(float(x), d) for x in np.asarray(a, dtype=float).ravel()]


def write_dataset(var: dict, values: np.ndarray, lat_cells: np.ndarray, out: Path) -> None:
    """values: [year][ocean cell], NaN = missing (written as null)."""
    tr = np.array([trend(values[:, k]) for k in range(values.shape[1])])
    w = np.cos(np.deg2rad(lat_cells))[None, :] * ~np.isnan(values)
    with np.errstate(invalid="ignore"):
        # ponytail: area mean over the cells present that year; coverage changes (e.g. coastal gaps) can bias it
        regional = np.nansum(np.nan_to_num(values) * w, axis=1) / w.sum(axis=1)
    rs, rp = trend(regional)
    anom = np.abs(values - _nanmean(values, axis=0))
    d = var["decimals"]
    pct = lambda a: float(np.nanpercentile(a, 98)) if np.isfinite(a).any() else 1.0
    doc = {
        "id": var["id"],
        "values": [rnd(row, d) for row in values],
        "trend": {"slope_per_decade": rnd(tr[:, 0], d + 1), "p": rnd(tr[:, 1], 4),
                  "method": "least-squares slope, two-sided t-test"},
        "regional": {"mean": rnd(regional, d + 1),
                     "slope_per_decade": None if np.isnan(rs) else round(float(rs), d + 1),
                     "p": None if np.isnan(rp) else round(float(rp), 4)},
        "scale": {"anomaly": max(round(pct(anom), d + 1), 10 ** -(d + 1)),
                  "slope": max(round(pct(np.abs(tr[:, 0])), d + 1), 10 ** -(d + 1))},
    }
    (out / f"{var['id']}.json").write_text(json.dumps(doc, separators=(",", ":")))


def selftest() -> None:
    v = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, np.nan, 8.0, 9.0, 10.0])  # exactly +1 per year
    s, p = trend(v)
    assert abs(s - 10) < 1e-9 and p == 0.0, (s, p)
    y = np.random.default_rng(0).normal(size=23)
    y[[3, 11]] = np.nan
    s, p = trend(y)
    ok = ~np.isnan(y)
    ref = stats.linregress(np.flatnonzero(ok), y[ok])
    assert abs(s - ref.slope * 10) < 1e-9 and abs(p - ref.pvalue) < 1e-9, (s, p, ref)
    assert np.isnan(trend(np.array([1.0, 2, 3] + [np.nan] * 20))[0])         # too few years
    m = empty()[:2, :, :1, :1]
    m[0, :10] = 1.0
    m[1, :9] = 1.0
    y = yearly(m)
    assert y[0, 0, 0] == 1.0 and np.isnan(y[1, 0, 0])
    text = "Dataset: x\nTS.lon, 1, 2\nTS.TS[TS.time=1][TS.lat=30], 1.5, 2.5\nT2M.T2M[T2M.time=1][T2M.lat=30], 9, 9\n"
    assert parse_ascii(text, "TS").tolist() == [[1.5, 2.5]]
    assert parse_ascii("TS[0][0], 3, 4\n", "TS").tolist() == [[3.0, 4.0]]
    assert abs(_mlon[M_COL][0] - (-6.0)) <= 0.3125 and abs(_mlon[M_COL][-1] - 36.5) <= 0.3125
    print("selftest ok")


# ----------------------------------------------------------------------------- main
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--only", nargs="+", help="variable ids to fetch (default: all)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    chosen = [v for v in VARIABLES if not args.only or v["id"] in args.only]
    token = _token_earthdata(None)
    monthly, merra = {}, {}
    for var in chosen:
        try:
            if "merra" in var:
                coll, expr = var["merra"]
                if coll not in merra:
                    if not token:
                        raise RuntimeError("EARTHDATA_TOKEN not set")
                    merra[coll] = fetch_merra(coll, token)
                monthly[var["id"]] = expr(merra[coll])
            else:
                monthly[var["id"]] = fetch_erddap(var["erddap"])
        except Exception as e:                    # one failing source does not stop the others
            log.error("%s FAILED, left out: %s", var["id"], e)

    # Sea cells: inside the Mediterranean cut and seen by an ocean satellite.
    seen = [np.isfinite(monthly[k]).any(axis=(0, 1)) for k in ("chl", "nflh", "sss") if k in monthly]
    if not seen:
        sys.exit("No satellite ocean data (chl, nflh, sss): cannot tell sea from land. Nothing written.")
    sea_n = (MEDITERRANEO & np.logical_or.reduce(seen))[::-1]      # site grid: row 0 = north
    flat = np.flatnonzero(sea_n.ravel())
    lat_cells = LATS[::-1][flat // NX]

    args.out.mkdir(parents=True, exist_ok=True)
    done = []
    for var in chosen:
        if var["id"] not in monthly:
            continue
        values = yearly(monthly[var["id"]])[:, ::-1].reshape(len(YEARS), -1)[:, flat]
        write_dataset(var, values, lat_cells, args.out)
        done.append(var)
        log.info("%s written: %d of %d values present", var["id"], np.isfinite(values).sum(), values.size)

    meta = {"synthetic": False,
            "region": {"name": "Mediterranean Sea", "lon_west": float(LONS[0] - PASO / 2),
                       "lat_north": float(LATS[-1] + PASO / 2), "cell_deg": PASO, "nx": NX, "ny": NY},
            "years": YEARS, "ocean_index": flat.tolist(),
            "variables": [{k: v[k] for k in ("id", "name", "unit", "decimals", "definition", "source")}
                          | {"file": v["id"] + ".json"} for v in done],
            "generated": date.today().isoformat()}
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log.info("meta.json: %d variables, %d sea cells -> %s", len(done), len(flat), args.out)


if __name__ == "__main__":
    main()
