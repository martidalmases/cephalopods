"""
STANDALONE download for the variable study. Needs nothing from the repository: copy this
one file anywhere and run it. It writes ONE file, mediterranean_monthly.npz, to send back.

    pip install copernicusmarine xarray numpy requests
    copernicusmarine login                 # once: your Copernicus Marine user and password
    python3 standalone_fetch.py

What it downloads (monthly means, 2003-2025, Mediterranean, the site's 0.5 degree grid):
  * Copernicus Marine: salinity (so), sea level (zos), pH (ph), oxygen (o2), nitrate (no3).
  * NASA MERRA-2, only if an Earthdata token is found (see below): surface temperature,
    air temperature, humidity, water vapour and pressure. These give the coldest and the
    hottest month of each year and the humidity variables. Skipped, with a message, if
    there is no token: the Copernicus part is still written.

Earthdata token (optional): either  export EARTHDATA_TOKEN=...  (from
https://urs.earthdata.nasa.gov -> Generate Token), or have run earthaccess.login(persist=True)
on this computer before.

    python3 standalone_fetch.py --no-merra      # Copernicus only
    python3 standalone_fetch.py --selftest
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

YEARS = list(range(2003, 2026))
PASO = 0.5
LATS = np.round(np.arange(30.0, 46.0 + PASO / 2, PASO), 2)     # 33 rows, south -> north
LONS = np.round(np.arange(-6.0, 36.5 + PASO / 2, PASO), 2)     # 86 columns
NY, NX = len(LATS), len(LONS)

# name in the output file -> (Copernicus dataset, field)
PHY, BGC = "cmems_mod_glo_phy_my_0.083deg_P1M-m", "cmems_mod_glo_bgc_my_0.25deg_P1M-m"
CMEMS = {"sss": (PHY, "so"), "ssh": (PHY, "zos"), "ph": (BGC, "ph"), "o2": (BGC, "o2"), "no3": (BGC, "no3")}
MERRA_COLLECTION, MERRA_FIELDS = "M2TMNXSLV", ["TS", "T2M", "QV2M", "TQV", "PS"]


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def empty() -> np.ndarray:
    return np.full((len(YEARS), 12, NY, NX), np.nan, "float32")    # [year][month][lat][lon]


def place_monthly(times: np.ndarray, a: np.ndarray, result: np.ndarray) -> int:
    """Put a[time][lat][lon] into result[year][month][lat][lon]. Returns the months placed."""
    month = np.asarray(times).astype("datetime64[M]").astype(int)
    n = 0
    for k, (y, m) in enumerate(zip(1970 + month // 12, month % 12)):
        if YEARS[0] <= y <= YEARS[-1]:
            result[y - YEARS[0], m] = a[k]
            n += 1
    return n


# ----------------------------------------------------------------------------- Copernicus Marine
def _box(da):
    """Lazy slice to the Mediterranean box. Coordinates may run either way."""
    for name, lo, hi in (("latitude", LATS[0] - 1, LATS[-1] + 1), ("longitude", LONS[0] - 1, LONS[-1] + 1)):
        if name in da.dims:
            c = np.asarray(da[name].values)
            da = da.sel({name: slice(lo, hi) if len(c) < 2 or c[0] <= c[-1] else slice(hi, lo)})
    return da


def fetch_cmems(out: dict) -> None:
    import copernicusmarine
    # Ask the server for the Mediterranean surface only. Reading the global field would need
    # gigabytes of memory, and the process gets killed.
    box = dict(minimum_longitude=float(LONS[0] - 1), maximum_longitude=float(LONS[-1] + 1),
               minimum_latitude=float(LATS[0] - 1), maximum_latitude=float(LATS[-1] + 1),
               minimum_depth=0.0, maximum_depth=1.0,
               start_datetime=f"{YEARS[0]}-01-01T00:00:00", end_datetime=f"{YEARS[-1]}-12-31T23:59:59")
    for dataset in sorted({d for d, _ in CMEMS.values()}):
        names = {k: f for k, (d, f) in CMEMS.items() if d == dataset}
        fields = sorted(set(names.values()))
        try:
            try:
                ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=fields, **box)
            except Exception as e:                       # older toolbox: subset by hand below
                say(f"{dataset}: opening without a box ({str(e)[:120]})")
                ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=fields)
        except Exception as e:
            say(f"ERROR {dataset}: could not be opened ({str(e)[:300]}). Did you run `copernicusmarine login`?")
            continue
        for name, field in names.items():
            try:
                da = ds[field]
                if "depth" in da.dims:
                    da = da.isel(depth=0)                # surface level
                da = _box(da).transpose("time", "latitude", "longitude")
                result, n = empty(), 0
                say(f"{name}: downloading {field} from {dataset} ...")
                for year in YEARS:                       # one year at a time: a few MB each
                    sub = da.sel(time=slice(f"{year}-01-01", f"{year}-12-31"))
                    if not sub.sizes["time"]:
                        continue
                    sub = sub.sel(latitude=LATS, longitude=LONS, method="nearest")
                    n += place_monthly(sub["time"].values, np.asarray(sub.values, dtype="float32"), result)
                out[name] = result
                say(f"{name}: ok, {n} months, {int(np.isfinite(result).sum())} values")
            except Exception as e:                       # one failing variable does not stop the rest
                say(f"ERROR {name}: {str(e)[:300]}")


# ----------------------------------------------------------------------------- NASA MERRA-2 (optional)
# MERRA-2 grid: lat -90..90 step 0.5 (rows 240..272 = 30..46 N, same as ours);
# lon -180..179.375 step 0.625 (columns 278..347), nearest neighbour to ours.
M_LAT, M_LON = (240, 272), (278, 347)
_mlon = -180 + 0.625 * np.arange(M_LON[0], M_LON[1] + 1)
M_COL = np.abs(_mlon[None, :] - LONS[:, None]).argmin(axis=1)


def earthdata_token() -> str | None:
    if os.environ.get("EARTHDATA_TOKEN"):
        return os.environ["EARTHDATA_TOKEN"]
    try:
        import earthaccess
        earthaccess.login(strategy="netrc")
        return earthaccess.get_edl_token()["access_token"]
    except Exception:
        return None


def _get(url: str, tries: int = 4, **kw):
    import requests
    err = ""
    for i in range(tries):
        try:
            r = requests.get(url, **kw)
            if r.status_code < 500:
                return r
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = str(e)
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"giving up on {url[:120]}: {err}")


def parse_ascii(text: str, name: str) -> np.ndarray:
    """Rows of one variable from an OPeNDAP .ascii response."""
    pat = re.compile(rf"^(\w+\.)?{name}\[")
    return np.array([[float(x) for x in line.split(",")[1:]] for line in text.splitlines() if pat.match(line)])


def merra_month(url: str, token: str) -> dict[str, np.ndarray]:
    box = f"[0:0][{M_LAT[0]}:{M_LAT[1]}][{M_LON[0]}:{M_LON[1]}]"
    r = _get(url + ".ascii?" + ",".join(f + box for f in MERRA_FIELDS), timeout=180,
             headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200 or not r.text.startswith("Dataset"):
        raise RuntimeError(f"OPeNDAP HTTP {r.status_code}: {r.text[:200]}")
    out = {}
    for f in MERRA_FIELDS:
        a = parse_ascii(r.text, f)
        if a.shape != (NY, M_LON[1] - M_LON[0] + 1):
            raise RuntimeError(f"{f}: unexpected shape {a.shape}")
        a[np.abs(a) > 1e14] = np.nan
        out[f] = a[:, M_COL]
    return out


def fetch_merra(out: dict, token: str) -> None:
    r = _get("https://cmr.earthdata.nasa.gov/search/granules.json", timeout=60, params={
        "short_name": MERRA_COLLECTION, "version": "5.12.4", "provider": "GES_DISC", "page_size": 2000,
        "temporal": f"{YEARS[0]}-01-01T00:00:00Z,{YEARS[-1]}-12-31T23:59:59Z"})
    granules = {}
    for e in r.json()["feed"]["entry"]:
        url = next((l["href"] for l in e["links"] if l["href"].startswith("https://opendap.")), None)
        if url:
            granules[(int(e["time_start"][:4]), int(e["time_start"][5:7]))] = url
    say(f"MERRA-2: {len(granules)} monthly files to ask (a few minutes)")
    data = {f: empty() for f in MERRA_FIELDS}
    first = sorted(granules)[0]
    try:
        month = merra_month(granules[first], token)          # first one alone: if the token is wrong, stop here
    except RuntimeError as e:
        say(f"MERRA-2 skipped: {e}\n         (token valid? 'NASA GESDISC DATA ARCHIVE' approved in your Earthdata profile?)")
        return
    done = {first: month}

    def one(key):
        try:
            return key, merra_month(granules[key], token)
        except RuntimeError as e:
            say(f"MERRA-2 {key[0]}-{key[1]:02d} skipped: {e}")
            return key, None

    with ThreadPoolExecutor(4) as ex:
        for n, (key, month) in enumerate(ex.map(one, [k for k in sorted(granules) if k != first]), 2):
            if month:
                done[key] = month
            if n % 48 == 0:
                say(f"MERRA-2: {n}/{len(granules)}")
    for (y, m), month in done.items():
        for f in MERRA_FIELDS:
            data[f][y - YEARS[0], m - 1] = month[f]
    for f in MERRA_FIELDS:
        out["merra_" + f] = data[f]
    say(f"MERRA-2: ok, {len(done)} months")


# ----------------------------------------------------------------------------- main
def selftest() -> None:
    r = empty()
    t = np.array(["2003-01-16", "2003-02-15", "2002-12-16", "2025-12-16"], dtype="datetime64[D]")
    assert place_monthly(t, np.arange(1.0, 5.0).reshape(4, 1, 1) * np.ones((4, NY, NX)), r) == 3
    assert r[0, 0, 0, 0] == 1.0 and r[0, 1, 5, 5] == 2.0 and r[-1, 11, 0, 0] == 4.0 and np.isnan(r[0, 2, 0, 0])
    text = "Dataset: x\nTS.lon, 1, 2\nTS.TS[TS.time=1][TS.lat=30], 1.5, 2.5\nT2M.T2M[T2M.time=1][T2M.lat=30], 9, 9\n"
    assert parse_ascii(text, "TS").tolist() == [[1.5, 2.5]]
    assert abs(_mlon[M_COL][0] - (-6.0)) <= 0.3125 and abs(_mlon[M_COL][-1] - 36.5) <= 0.3125

    biggest = [0]

    class FakeDA:                                        # the few xarray calls used, on a known field
        def __init__(self, a, coords):
            self.a, self.coords = a, coords
            self.dims = tuple(coords)
            self.sizes = {k: len(v) for k, v in coords.items()}

        @property
        def values(self):                                # only here is memory really used
            biggest[0] = max(biggest[0], self.a.size)
            assert self.a.size < 10 ** 7, f"would materialise {self.a.size} values: not lazy enough"
            return np.ascontiguousarray(self.a)

        def isel(self, **kw):
            d = list(self.coords)
            idx = tuple(kw.get(k, slice(None)) for k in d)
            return FakeDA(self.a[idx], {k: v for k, v in self.coords.items() if k not in kw})

        def sel(self, indexers=None, method=None, **kw):
            kw = {**(indexers or {}), **kw}
            a, coords = self.a, dict(self.coords)
            for k, v in kw.items():
                ax = list(coords).index(k)
                c = coords[k]
                if isinstance(v, slice):
                    lo, hi = (v.start, v.stop) if c[0] <= c[-1] else (v.stop, v.start)
                    keep = np.flatnonzero((c >= np.array(lo, c.dtype)) & (c <= np.array(hi, c.dtype)))
                else:
                    assert method == "nearest"
                    keep = np.abs(c[None, :] - np.asarray(v)[:, None]).argmin(axis=1)
                a, coords[k] = np.take(a, keep, axis=ax), c[keep]
            return FakeDA(a, coords)

        def transpose(self, *d):
            a = np.transpose(self.a, [list(self.coords).index(k) for k in d])
            return FakeDA(a, {k: self.coords[k] for k in d})

        def __getitem__(self, k):
            return FakeDA(self.coords[k], {k: self.coords[k]})

    times = np.arange("2002-06", "2026-03", dtype="datetime64[M]").astype("datetime64[D]") + np.timedelta64(15, "D")
    glat, glon = np.round(np.arange(-80, 90.25, 0.25), 2), np.round(np.arange(-180, 180, 0.25), 2)
    depth = np.array([0.5, 1.5, 5.0])

    def field(v):                                        # broadcast views: nothing is copied until .values
        base = {"so": 38.0, "zos": 0.2, "ph": 8.1, "o2": 230.0, "no3": 0.5}[v]
        a = (base + 0.01 * glat[:, None] + 0.001 * glon[None, :]).astype("float32")
        if v == "zos":
            return FakeDA(np.broadcast_to(a, (len(times),) + a.shape),
                          {"time": times, "latitude": glat, "longitude": glon})
        return FakeDA(np.broadcast_to(a, (len(times), len(depth)) + a.shape),
                      {"time": times, "depth": depth, "latitude": glat, "longitude": glon})

    fake = type(sys)("copernicusmarine")
    fake.open_dataset = lambda dataset_id, variables, **kw: {v: field(v) for v in variables}
    sys.modules["copernicusmarine"] = fake
    out = {}
    fetch_cmems(out)
    assert sorted(out) == ["no3", "o2", "ph", "ssh", "sss"] and out["ph"].shape == (len(YEARS), 12, NY, NX)
    assert np.isfinite(out["ph"]).all() and np.isnan(out["ph"][0, 0, 0, 0]) == False
    i, j = 4, 7                                          # the value of the nearest global cell, surface level
    assert abs(out["ph"][3, 5, i, j] - (8.1 + 0.01 * LATS[i] + 0.001 * LONS[j])) < 0.02
    assert abs(out["ssh"][0, 0, i, j] - (0.2 + 0.01 * LATS[i] + 0.001 * LONS[j])) < 0.02
    # Never materialise more than one Mediterranean year: this is what was killing the process.
    assert biggest[0] < 12 * 200 * 600, biggest[0]
    print("selftest ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("mediterranean_monthly.npz"))
    ap.add_argument("--no-merra", action="store_true")
    ap.add_argument("--no-copernicus", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    out: dict[str, np.ndarray] = {}
    if not a.no_copernicus:
        fetch_cmems(out)
    if not a.no_merra:
        token = earthdata_token()
        if token:
            try:
                fetch_merra(out, token)
            except Exception as e:
                say(f"MERRA-2 skipped: {str(e)[:300]}")
        else:
            say("MERRA-2 skipped: no Earthdata token (set EARTHDATA_TOKEN to include it)")
    if not out:
        sys.exit("Nothing downloaded. Nothing written.")
    np.savez_compressed(a.out, years=np.array(YEARS), lats=LATS, lons=LONS, **out)
    say(f"written {a.out} ({a.out.stat().st_size / 1e6:.1f} MB): {', '.join(sorted(out))}")
    say("Send that file back.")


if __name__ == "__main__":
    main()
