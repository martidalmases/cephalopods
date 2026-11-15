"""
Merge a mediterranean_monthly.npz written by standalone_fetch.py into the site's data/.

    python3 pipeline/merge_npz.py mediterranean_monthly.npz          # -> ./data
    python3 pipeline/merge_npz.py file.npz --out /tmp/data
    python3 pipeline/merge_npz.py --selftest

The yearly statistics and the variable catalogue come from fetch_real_data.py, so the
files written here are the same as if that script had downloaded them: the site and
study_variables.py see no difference. Existing variables are kept; the sea cells of the
existing data/meta.json are reused.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_real_data as med  # noqa: E402


def monthly_from_npz(z) -> dict[str, np.ndarray]:
    """npz fields -> {variable id: [year][month][lat][lon]}, using the catalogue's own expressions."""
    out = {}
    keys = list(z.files if hasattr(z, "files") else z)
    merra = {k[len("merra_"):]: z[k] for k in keys if k.startswith("merra_")}
    for var in med.VARIABLES:
        if var["id"] in keys:                       # written as such by standalone_fetch (Copernicus)
            out[var["id"]] = z[var["id"]]
        elif "merra" in var and set(med.MERRA_FIELDS[var["merra"][0]]) <= merra.keys():
            out[var["id"]] = var["merra"][1](merra)    # e.g. sst = TS - 273.15, dq_sea_air = ...
    for var in med.VARIABLES:                          # derived ones share their base's months
        if var.get("of") in out:
            out[var["id"]] = out[var["of"]]
    return out


def merge(npz: Path, out: Path) -> list[str]:
    z = np.load(npz)
    if list(z["years"]) != med.YEARS:
        sys.exit(f"{npz} covers {z['years'][0]}-{z['years'][-1]}, the site {med.YEARS[0]}-{med.YEARS[-1]}")
    if not np.allclose(z["lats"], med.LATS) or not np.allclose(z["lons"], med.LONS):
        sys.exit(f"{npz} is on another grid")
    meta_path = out / "meta.json"
    if not meta_path.exists():
        sys.exit(f"{meta_path} not found: run fetch_real_data.py once first")
    old = json.loads(meta_path.read_text())
    flat = np.array(old["ocean_index"])
    lat_cells = med.LATS[::-1][flat // med.NX]

    monthly, done = monthly_from_npz(z), []
    for var in med.VARIABLES:
        if var["id"] not in monthly:
            continue
        values = med.yearly(monthly[var["id"]], var.get("stat", "mean"))[:, ::-1].reshape(len(med.YEARS), -1)[:, flat]
        if not np.isfinite(values).any():
            print(f"{var['id']}: no value on the site's sea cells, left out")
            continue
        med.write_dataset(var, values, lat_cells, out)
        done.append(var)
        print(f"{var['id']}: written, {np.isfinite(values).sum()} of {values.size} values present")
    meta = old | {"variables": med.merge_variables(old["variables"], done), "generated": date.today().isoformat()}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"meta.json: {len(meta['variables'])} variables ({len(done)} written now) -> {out}")
    return [v["id"] for v in done]


def selftest() -> None:
    import tempfile
    nt, ny, nx = len(med.YEARS), med.NY, med.NX
    season = 6 * np.sin(2 * np.pi * (np.arange(12) - 4) / 12)[None, :, None, None]
    ones = np.ones((nt, 12, ny, nx), "float32")
    z = {"years": np.array(med.YEARS), "lats": med.LATS, "lons": med.LONS,
         "merra_TS": 293.15 * ones + season, "merra_T2M": 292.15 * ones, "merra_PS": 1e5 * ones,
         "merra_QV2M": 0.01 * ones, "merra_TQV": 20 * ones, "ph": 8.1 * ones, "sss": 38 * ones}
    m = monthly_from_npz(z)
    assert abs(m["sst"][0, 0, 0, 0] - (20.0 + season[0, 0, 0, 0])) < 1e-4      # K -> C
    assert m["sst_winter_min"] is m["sst"] and abs(m["q2m"][0, 0, 0, 0] - 10.0) < 1e-4
    assert abs(m["dq_sea_air"][0, 0, 0, 0] - (0.98 * med.qsat(z["merra_TS"][0, 0, 0, 0], 1e5) - 0.01) * 1000) < 1e-3
    assert "ssh" not in m and abs(m["ph"][0, 0, 0, 0] - 8.1) < 1e-6
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        np.savez_compressed(out / "x.npz", **z)
        (out / "meta.json").write_text(json.dumps(
            {"region": {"name": "t", "lon_west": -6.25, "lat_north": 46.25, "cell_deg": 0.5, "nx": nx, "ny": ny},
             "years": med.YEARS, "ocean_index": [100, 101, 500], "variables": [{"id": "chl", "file": "chl.json"}]}))
        ids = merge(out / "x.npz", out)
        assert "sst" in ids and "sst_winter_min" in ids and "ph" in ids and "ssh" not in ids
        doc = json.loads((out / "sst_winter_min.json").read_text())
        assert len(doc["values"]) == len(med.YEARS) and len(doc["values"][0]) == 3
        assert abs(doc["values"][0][0] - (20.0 - 6)) < 0.01                    # coldest month
        meta = json.loads((out / "meta.json").read_text())
        assert [v["id"] for v in meta["variables"]][:3] == ["sst", "dt_sea_air", "chl"] and len(meta["ocean_index"]) == 3
    print("selftest ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("npz", nargs="?", type=Path)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.npz:
        ap.error("give the .npz written by standalone_fetch.py")
    merge(a.npz, a.out)


if __name__ == "__main__":
    main()
