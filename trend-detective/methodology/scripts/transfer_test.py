"""
Is a variable a real condition for the species, or just a marker of where in the sea you are?

    python3 scripts/transfer_test.py            # the 13 NASA variables left by the redundancy step
    python3 scripts/transfer_test.py --sets     # the same test on four alternative sets of variables
    python3 scripts/transfer_test.py --selftest

Three permutation importances per species and variable, same model as study_variables.py:

  time     train on the early years, test on the later ones (the study's own test);
  space    train on the western half of the species' records, test on the eastern half, and
           the reverse. A physiological limit holds on both sides; a geographic marker learnt
           in the west says nothing in the east;
  coords   as `time`, but latitude and longitude are also given to the model. A variable that
           only stood in for position loses its importance.

DECISION RULE, fixed before looking at the result:
  habitat model  = variables that keep at least HALF of their `time` importance in BOTH tests;
  change drivers = those that also have a significant trend in at least half of the sea cells.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study_variables as sv  # noqa: E402

KEEP = ["sst_winter_min", "sst_summer_max", "mhw_months", "chl", "nflh", "lhf", "shf", "dq_sea_air",
        "dt_sea_air", "tqv", "wind", "precip", "aod"]          # what the redundancy step left (NASA only)


def importance(x, y, train, test, n_real: int, rng, n_perm: int = 60, max_rows: int = 20_000):
    """AUC on `test` of a model fitted on `train`, and the AUC drop when each of the first
    n_real columns is shuffled. None if either side has too few presences or background points."""
    from sklearn.linear_model import LogisticRegression
    if min(y[train].sum(), y[test].sum(), (1 - y[train]).sum(), (1 - y[test]).sum()) < 10:
        return None
    mu, sd = x[train].mean(0), x[train].std(0) + 1e-12
    model = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(sv._features(x[train], mu, sd), y[train])
    xt, yt = x[test], y[test]
    if len(yt) > max_rows:
        pick = np.concatenate([np.flatnonzero(yt == 1), rng.choice(np.flatnonzero(yt == 0), max_rows - int(yt.sum()), replace=False)])
        xt, yt = xt[pick], yt[pick]
    z = (xt - mu) / sd
    w, p = model.coef_[0], x.shape[1]
    eta = z @ w[:p] + (z ** 2) @ w[p:] + model.intercept_[0]
    real = sv.auc(yt, eta)
    drops = np.empty(n_real)
    for j in range(n_real):
        part = z[:, j] * w[j] + z[:, j] ** 2 * w[p + j]
        drops[j] = real - np.mean([sv.auc(yt, eta - part + part[rng.permutation(len(yt))]) for _ in range(n_perm)])
    return real, drops


def run(values, years, lat, lon, obs, keep, seed=0, max_species=40, min_presences=30):
    rng = np.random.default_rng(seed)
    cube = np.stack([values[v] for v in keep], axis=-1)
    split_year = years[int(len(years) * 0.65)]
    sampled = obs[["t", "k"]].drop_duplicates()
    size = obs.drop_duplicates(["species", "t", "k"]).species.value_counts()
    size = size[size >= min_presences]
    chosen = list(dict.fromkeys([s for s in sv.INDICATORS if s in size.index] + list(size.index[:max_species])))
    rows = []
    for sp in chosen:
        pres = obs[obs.species == sp][["t", "k"]].drop_duplicates()
        bg = sampled.merge(pres, how="left", indicator=True).query("_merge == 'left_only'")[["t", "k"]]
        d = pd.concat([pres.assign(y=1), bg.assign(y=0)])
        x = cube[d.t.values, d.k.values]
        ok = np.isfinite(x).all(axis=1)
        x, y = x[ok], d.y.values[ok]
        t, la, lo = years[d.t.values[ok]], lat[d.k.values[ok]], lon[d.k.values[ok]]
        west = lo < np.median(lo[y == 1])                      # half of the species' records on each side
        tests = {"time": (x, t < split_year, t >= split_year),
                 "west_to_east": (x, west, ~west), "east_to_west": (x, ~west, west),
                 "coords": (np.column_stack([x, la, lo]), t < split_year, t >= split_year)}
        for name, (xx, tr, te) in tests.items():
            got = importance(xx, y, tr, te, len(keep), rng)
            if got:
                rows += [{"species": sp, "test": name, "auc": got[0], "variable": v, "drop": dr} for v, dr in zip(keep, got[1])]
    return pd.DataFrame(rows)


def summarise(res: pd.DataFrame, trends: pd.DataFrame | None) -> pd.DataFrame:
    valid = res[(res.test == "time")].groupby("species").auc.first()
    res = res[res.species.isin(valid[valid > 0.55].index)]     # species whose model says something
    mean = res.pivot_table(index="variable", columns="test", values="drop", aggfunc="mean")
    out = pd.DataFrame({"time": mean["time"], "space": mean[["west_to_east", "east_to_west"]].mean(axis=1), "coords": mean["coords"]})
    out["kept_space"] = out.space / out.time
    out["kept_coords"] = out.coords / out.time
    out["habitat_model"] = (out.time > 0) & (out.kept_space >= 0.5) & (out.kept_coords >= 0.5)
    if trends is not None:
        tr = trends.set_index("variable")
        out["pct_cells_trend"] = tr.pct_cells_significant.reindex(out.index)
        out["change_driver"] = out.habitat_model & (out.pct_cells_trend >= 50)
    return out.sort_values("time", ascending=False), res


# Alternative sets: does a variable's verdict depend on which others are in the model?
SETS = {"13 NASA variables": KEEP,
        "without humidity difference and water vapour": [v for v in KEEP if v not in ("dq_sea_air", "tqv")],
        "rule-based five": ["sst_winter_min", "sst_summer_max", "mhw_months", "dq_sea_air", "tqv"],
        "final four": ["sst_winter_min", "sst_summer_max", "mhw_months", "chl"]}


def explained_by_temperature(values: dict, targets=("dq_sea_air", "tqv", "chl", "lhf")) -> pd.DataFrame:
    """R2 of each target against the coldest and hottest month (quadratic), over cell-years:
    how much of the variable is sea temperature in another form."""
    d = pd.DataFrame({v: values[v].ravel() for v in ("sst_winter_min", "sst_summer_max", *targets) if v in values}).dropna()
    a = np.column_stack([d.sst_summer_max, d.sst_winter_min, d.sst_summer_max ** 2, d.sst_winter_min ** 2, np.ones(len(d))])
    rows = []
    for t in [t for t in targets if t in d]:
        resid = d[t] - a @ np.linalg.lstsq(a, d[t], rcond=None)[0]
        rows.append({"variable": t, "r2_from_sea_temperature": 1 - resid.var() / d[t].var(),
                     "spearman_with_hottest_month": d[t].corr(d.sst_summer_max, method="spearman"),
                     "spearman_with_coldest_month": d[t].corr(d.sst_winter_min, method="spearman")})
    return pd.DataFrame(rows)


def selftest():
    rng = np.random.default_rng(2)
    nt, nc = 23, 800
    lon = np.linspace(-5, 35, nc)
    lat = rng.uniform(31, 45, nc)
    years = np.arange(2003, 2003 + nt)
    real = rng.uniform(13, 21, nc)[None, :] + rng.normal(0, 0.3, (nt, nc))          # real limit, unrelated to position
    values = {"sst_winter_min": real, "aod": 0.02 * lon[None, :] + rng.normal(0, 0.03, (nt, nc)),   # pure position
              "wind": rng.normal(7, 1, (nt, nc))}
    tt, kk = np.meshgrid(np.arange(nt), np.arange(nc), indexing="ij")
    seen = rng.random((nt, nc)) < 0.3
    # the species needs warm winters AND, for reasons no variable holds, is commoner in the west
    p = 1 / (1 + np.exp(-2.5 * (real - 17))) * np.where(lon[None, :] < 15, 1.0, 0.45)
    here = seen & (rng.random((nt, nc)) < p)
    obs = pd.concat([pd.DataFrame({"species": "Fish", "t": tt[here], "k": kk[here]}),
                     pd.DataFrame({"species": "Other", "t": tt[seen], "k": kk[seen]})])
    res = run(values, years, lat, lon, obs, list(values), max_species=2)
    s, _ = summarise(res[res.species == "Fish"], None)
    assert s.loc["sst_winter_min", "habitat_model"], s
    assert not s.loc["aod", "habitat_model"] and s.loc["aod", "time"] > 0.005, s      # looked useful, was position
    print("selftest ok")


def main():
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=root / "data")
    ap.add_argument("--species", default=str(root / "data" / "obis.csv.gz"))
    ap.add_argument("--trends", type=Path, default=root / "results" / "nasa_only" / "trends.csv")
    ap.add_argument("--out", type=Path, default=root / "results" / "nasa_only")
    ap.add_argument("--sets", action="store_true", help="run the test on the alternative sets in SETS")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    meta, years, lat, lon, values = sv.load(a.data)
    obs = sv.read_species(a.species, meta, years)
    trends = pd.read_csv(a.trends) if a.trends.exists() else None
    a.out.mkdir(parents=True, exist_ok=True)
    explained_by_temperature(values).round(4).to_csv(a.out / "explained_by_temperature.csv", index=False)
    if a.sets:
        rows = []
        for name, keep in SETS.items():
            table, used = summarise(run(values, years, lat, lon, obs, keep), trends)
            auc = used.groupby(["species", "test"]).auc.first().unstack()
            for v, r in table.iterrows():
                rows.append({"set": name, "n_variables": len(keep), "species": used.species.nunique(),
                             "median_auc_time": auc["time"].median(), "variable": v, "importance": r.time,
                             "kept_space": r.kept_space, "kept_coords": r.kept_coords, "passes": bool(r.habitat_model)})
            print(f"{name}: {used.species.nunique()} species, median AUC {auc['time'].median():.3f}, "
                  f"pass: {', '.join(table.index[table.habitat_model])}")
        pd.DataFrame(rows).round(4).to_csv(a.out / "transfer_sets.csv", index=False)
        return
    keep = [v for v in KEEP if v in values]
    res = run(values, years, lat, lon, obs, keep)
    table, used = summarise(res, trends)
    res.round(5).to_csv(a.out / "transfer_by_species.csv", index=False)
    table.round(4).to_csv(a.out / "transfer_summary.csv")
    auc = used.groupby(["species", "test"]).auc.first().unstack()
    print(f"{used.species.nunique()} species with a model above 0.55 on the later years")
    print("median AUC per test:", auc.median().round(3).to_dict())
    print(table.round(4).to_string())
    print("\nPASS BOTH TRANSFER TESTS:", ", ".join(table.index[table.habitat_model]))
    if "change_driver" in table:
        print("...AND TREND IN >= 50 % OF CELLS:", ", ".join(table.index[table.change_driver]))


if __name__ == "__main__":
    main()
