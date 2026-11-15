"""
Variable study on methodology/data/*.json. No downloads: runs in seconds.

    python3 scripts/study_variables.py --species data/obis.csv.gz                       # all sources
    python3 scripts/study_variables.py --species data/obis.csv.gz --exclude sss ssh ph o2 no3 --out results/nasa_only
    python3 scripts/study_variables.py --selftest

It answers three separate questions, in this order:

  1. TREND       Does the variable change at all? Sen's slope + Mann-Kendall per cell,
                 p-values corrected for testing ~1000 cells at once (Benjamini-Hochberg).
  2. REDUNDANCY  Which variables carry the same information? Spearman correlation,
                 hierarchical clustering at |r| >= --threshold, one representative per
                 cluster, VIF of the survivors. No species involved: done once.
  3. RELEVANCE   (--species) For each species, which surviving variables predict where it
                 is found? Presence vs target-group background, logistic model with
                 quadratic terms, trained on the early years and TESTED ON THE LATER
                 YEARS, permutation importance with a p-value per variable.

A variable is an "indicator of change" for a species when it passes 1 AND 3.
Different species keeping different variables is expected, not a problem: store the
union of the survivors and let each species weight them.

--species CSV: columns species, lat, lon, year (OBIS/GBIF names are accepted). Include
MANY species of the same group (e.g. all fishes), not only the focal ones: the records
of the other species are the background that corrects for where people look.

Needs: numpy, pandas, scipy, scikit-learn, matplotlib. Output in methodology/results/.
"""
from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from scipy.spatial.distance import squareform

ROOT = Path(__file__).resolve().parent.parent
MIN_YEARS = 8
# Which member represents a cluster of redundant variables: direct biological
# mechanism first, atmospheric context last. Edit if you disagree.
PRIORITY = ["sst_winter_min", "sst_summer_max", "sst", "mhw_months", "chl", "sss", "o2", "ph", "no3",
            "sst_range", "nflh", "ssh", "wind", "lhf", "shf", "dt_sea_air", "dq_sea_air", "evap",
            "precip", "q2m", "tqv", "aod"]


# ----------------------------------------------------------------------------- data
def load(data: Path, exclude: tuple = ()):
    """exclude: variable ids to leave out (e.g. the non-NASA ones)."""
    meta = json.loads((data / "meta.json").read_text())
    meta["variables"] = [v for v in meta["variables"] if v["id"] not in set(exclude)]
    reg, idx = meta["region"], np.array(meta["ocean_index"])
    lat = reg["lat_north"] - (idx // reg["nx"] + 0.5) * reg["cell_deg"]
    lon = reg["lon_west"] + (idx % reg["nx"] + 0.5) * reg["cell_deg"]
    values = {}
    for v in meta["variables"]:
        doc = json.loads((data / v["file"]).read_text())
        values[v["id"]] = np.array([[np.nan if x is None else x for x in row] for row in doc["values"]], float)
    return meta, np.array(meta["years"]), lat, lon, values      # values[id][year, cell]


# ----------------------------------------------------------------------------- 1. trends
def sen_mk(y: np.ndarray):
    """Sen's slope (per step) and Mann-Kendall two-sided p for y[time, cell]; NaN pairs are skipped.
    Plain Mann-Kendall (no autocorrelation correction): fine for screening variables."""
    nt = y.shape[0]
    signs, slopes = [], []
    for i in range(nt - 1):
        d = y[i + 1:] - y[i]
        signs.append(np.sign(d))
        slopes.append(d / np.arange(1, nt - i)[:, None])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        s = np.nansum(np.concatenate(signs), axis=0)
        slope = np.nanmedian(np.concatenate(slopes), axis=0)
        n = np.isfinite(y).sum(axis=0).astype(float)
        var = n * (n - 1) * (2 * n + 5) / 18
        z = np.where(s > 0, s - 1, np.where(s < 0, s + 1, 0.0)) / np.sqrt(np.where(var > 0, var, np.nan))
    z = np.where(n >= MIN_YEARS, z, np.nan)
    return slope, 2 * stats.norm.sf(np.abs(z))


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values, NaN-safe."""
    q, ok = np.full_like(p, np.nan, dtype=float), np.isfinite(p)
    if ok.any():
        q[ok] = stats.false_discovery_control(p[ok], method="bh")
    return q


def trend_table(values: dict, lat: np.ndarray) -> pd.DataFrame:
    w = np.cos(np.deg2rad(lat))
    rows = []
    for v, x in values.items():
        slope, p = sen_mk(x)
        sig = bh(p) < 0.05
        # Regional series from per-cell anomalies, so cells missing in some years do not bias it.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)          # cells with no data at all
            anom = x - np.nanmean(x, axis=0)
        ww = w * np.isfinite(anom)
        regional = np.nansum(np.nan_to_num(anom) * ww, axis=1) / ww.sum(axis=1)
        rs, rp = sen_mk(regional[:, None])
        rows.append({"variable": v, "years": int(np.isfinite(x).any(axis=1).sum()),
                     "pct_cells_significant": 100 * sig.mean(),
                     "pct_of_those_rising": 100 * (slope[sig] > 0).mean() if sig.any() else np.nan,
                     "regional_slope_per_decade": 10 * rs[0], "regional_p": rp[0]})
    return pd.DataFrame(rows).sort_values("regional_p")


# ----------------------------------------------------------------------------- 2. redundancy
def cluster(corr: pd.DataFrame, threshold: float):
    dist = 1 - corr.abs().fillna(0).values
    np.fill_diagonal(dist, 0)
    z = linkage(squareform((dist + dist.T) / 2, checks=False), "average")
    return z, fcluster(z, 1 - threshold, "distance")


def representatives(corr: pd.DataFrame, groups: np.ndarray) -> pd.DataFrame:
    rank = {v: i for i, v in enumerate(PRIORITY)}
    rows = []
    for g in np.unique(groups):
        members = sorted(corr.index[groups == g], key=lambda v: rank.get(v, 999))
        rows.append({"keep": members[0],
                     "redundant_with_it": "; ".join(f"{v} (r={corr.loc[members[0], v]:+.2f})" for v in members[1:])})
    return pd.DataFrame(rows)


def vif(df: pd.DataFrame) -> pd.Series:
    """Variance inflation factor: 1 = independent, > 5 = largely explained by the others,
    inf = an exact combination of them (sst_range is sst_summer_max minus sst_winter_min)."""
    x = df.dropna().to_numpy(float)
    x = (x - x.mean(0)) / (x.std(0) + 1e-12)
    out = {}
    for j, col in enumerate(df.columns):
        a = np.column_stack([np.delete(x, j, axis=1), np.ones(len(x))])
        beta = np.linalg.lstsq(a, x[:, j], rcond=None)[0]
        r2 = 1 - (x[:, j] - a @ beta).var()
        out[col] = np.inf if r2 > 1 - 1e-9 else 1 / max(1 - r2, 1e-12)
    return pd.Series(out)


def prune_vif(df: pd.DataFrame, keep: list[str], threshold: float = 10.0):
    """Drop variables until none is largely explained by the others. Pairwise correlation
    does not catch a variable that is a combination of two or three others; VIF does.
    Of those above the threshold the LOWEST-priority one goes, so the variables with a
    direct biological mechanism survive."""
    rank = {v: i for i, v in enumerate(PRIORITY)}
    keep, dropped = list(keep), []
    while len(keep) > 2:
        v = vif(df[keep])
        bad = v[v > threshold]
        if bad.empty:
            break
        worst = max(bad.index, key=lambda c: (rank.get(c, 999), bad[c]))
        dropped.append(f"{worst} (VIF {bad[worst]:.0f})")
        keep.remove(worst)
    return keep, dropped, vif(df[keep])


def pca_summary(df: pd.DataFrame):
    from sklearn.decomposition import PCA
    x = df.dropna()
    m = PCA().fit((x - x.mean()) / x.std())
    load = pd.DataFrame(m.components_.T, index=df.columns, columns=[f"PC{i + 1}" for i in range(df.shape[1])])
    return m.explained_variance_ratio_, load


# ----------------------------------------------------------------------------- 3. species
def read_species(path: str, meta: dict, years: np.ndarray) -> pd.DataFrame:
    """-> unique (species, t, k): year index and sea-cell index of each record."""
    obs = pd.read_csv(path)
    alias = {"species": ["species", "especie", "scientificName", "scientificname"],
             "lat": ["lat", "decimalLatitude", "decimallatitude"],
             "lon": ["lon", "decimalLongitude", "decimallongitude"],
             "year": ["year", "anio", "date_year"]}
    obs = obs.rename(columns={c: k for k, cs in alias.items() for c in cs if c in obs.columns})
    reg = meta["region"]
    row = np.floor((reg["lat_north"] - obs.lat) / reg["cell_deg"]).astype(int)
    col = np.floor((obs.lon - reg["lon_west"]) / reg["cell_deg"]).astype(int)
    inside = row.between(0, reg["ny"] - 1) & col.between(0, reg["nx"] - 1)
    cell = pd.Series(np.arange(len(meta["ocean_index"])), index=meta["ocean_index"])
    obs["k"] = (row * reg["nx"] + col).where(inside).map(cell)
    obs["t"] = pd.Index(years).get_indexer(obs.year)
    obs = obs[(obs.t >= 0) & obs.k.notna()].astype({"k": int})
    return obs[["species", "t", "k"]].drop_duplicates()


def _features(x: np.ndarray, mu, sd) -> np.ndarray:
    z = (x - mu) / sd
    return np.hstack([z, z ** 2])            # quadratic: a niche has an optimum, not just a direction


def auc(y: np.ndarray, score: np.ndarray) -> float:
    """Area under the ROC curve from the ranks (Mann-Whitney U). Faster than sklearn's,
    which is what makes thousands of permutations possible."""
    order = np.argsort(score, kind="stable")
    ranks = np.empty(len(score), float)
    ranks[order] = np.arange(1, len(score) + 1)
    n1 = float(y.sum())
    n0 = len(y) - n1
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


# Species always modelled if they have enough records, whatever --max-species says.
INDICATORS = ["Thalassoma pavo", "Sparisoma cretense", "Sphyraena viridensis", "Epinephelus marginatus",
              "Caranx crysos", "Fistularia commersonii", "Caulerpa cylindracea",
              "Pinna nobilis", "Paramuricea clavata", "Posidonia oceanica"]


def species_relevance(obs: pd.DataFrame, values: dict, keep: list[str], years: np.ndarray,
                      n_perm: int = 100, min_presences: int = 30, max_species: int = 40,
                      must: list[str] | None = None, max_rows: int = 20_000, seed: int = 0) -> pd.DataFrame:
    """For each species: which of `keep` the model needs to predict where it is found in
    years it was not trained on. Presences vs target-group background; permutation importance."""
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(seed)
    cube = np.stack([values[v] for v in keep], axis=-1)            # [year, cell, variable]
    split = years[int(len(years) * 0.65)]                          # train < split <= test
    sampled = obs[["t", "k"]].drop_duplicates()                    # where anyone recorded anything
    must = list(must if must is not None else INDICATORS)

    size = obs.drop_duplicates(["species", "t", "k"]).species.value_counts()
    size = size[size >= min_presences]
    chosen = list(dict.fromkeys([s for s in must if s in size.index] + list(size.index[:max_species])))
    rows = []
    for sp in chosen:
        pres = obs[obs.species == sp][["t", "k"]].drop_duplicates()
        bg = sampled.merge(pres, how="left", indicator=True).query("_merge == 'left_only'")[["t", "k"]]
        background = "target-group"
        if len(bg) < 2 * len(pres):                                # CSV has (almost) only this species
            t, k = rng.integers(0, cube.shape[0], 5 * len(pres)), rng.integers(0, cube.shape[1], 5 * len(pres))
            bg, background = pd.DataFrame({"t": t, "k": k}), "random (weaker: no sampling-bias correction)"
        d = pd.concat([pres.assign(y=1), bg.assign(y=0)])
        x = cube[d.t.values, d.k.values]
        ok = np.isfinite(x).all(axis=1)
        x, y, t = x[ok], d.y.values[ok], years[d.t.values[ok]]
        tr, te = t < split, t >= split
        if min(y[tr].sum(), y[te].sum()) < 10 or min((1 - y[tr]).sum(), (1 - y[te]).sum()) < 10:
            rows.append({"species": sp, "variable": None, "note": f"too few records on one side of {split}"})
            continue
        mu, sd = x[tr].mean(0), x[tr].std(0) + 1e-12
        model = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit(_features(x[tr], mu, sd), y[tr])

        xt, yt = x[te], y[te]
        if len(yt) > max_rows:                                     # keep every presence, thin the background
            pick = np.concatenate([np.flatnonzero(yt == 1),
                                   rng.choice(np.flatnonzero(yt == 0), max_rows - int(yt.sum()), replace=False)])
            xt, yt = xt[pick], yt[pick]
        # The AUC only needs the ranking, so the linear predictor is enough: no sigmoid, no refit.
        z = (xt - mu) / sd
        w, p = model.coef_[0], len(keep)
        eta = z @ w[:p] + (z ** 2) @ w[p:] + model.intercept_[0]
        real = auc(yt, eta)
        null = np.array([auc(rng.permutation(yt), eta) for _ in range(n_perm)])
        p_model = (1 + (null >= real).sum()) / (n_perm + 1)
        drops, pvals = [], []
        for j in range(p):
            part = z[:, j] * w[j] + z[:, j] ** 2 * w[p + j]        # this variable's share of eta
            perm = np.empty(n_perm)
            for r in range(n_perm):
                q = rng.permutation(len(yt))
                perm[r] = auc(yt, eta - part + part[q])            # same model, this variable shuffled
            drops.append(real - perm.mean())
            pvals.append((1 + (perm >= real).sum()) / (n_perm + 1))
        for j, (v, q) in enumerate(zip(keep, bh(np.array(pvals)))):
            lo, hi = np.percentile(x[y == 1][:, j], [5, 95])
            rows.append({"species": sp, "variable": v, "n_presences": int(y.sum()), "background": background,
                         "test_years": f">= {split}", "auc_test": real, "p_model": p_model,
                         "importance": drops[j], "q": q, "relevant": bool(q < 0.05 and drops[j] > 0),
                         "p5": lo, "p95": hi})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- figures
def _div():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("div", ["#2166ac", "#e8e8e8", "#b2182b"])


def fig_corr(corr: pd.DataFrame, z, title: str, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    order = dendrogram(z, no_plot=True)["leaves"]
    c, n = corr.iloc[order, order], len(corr)
    fig, (ax, axd) = plt.subplots(1, 2, figsize=(0.6 * n + 7, 0.55 * n + 2.5), gridspec_kw={"width_ratios": [3, 1]})
    im = ax.imshow(c.values, cmap=_div(), vmin=-1, vmax=1)
    ax.set_xticks(range(n), c.columns, rotation=60, ha="right", fontsize=9)
    ax.set_yticks(range(n), c.index, fontsize=9)
    for i in range(n):
        for j in range(n):
            if i != j and np.isfinite(c.values[i, j]):
                ax.text(j, i, f"{c.values[i, j]:.2f}", ha="center", va="center", fontsize=7,
                        color="white" if abs(c.values[i, j]) > 0.7 else "#333")
    ax.set_title(title, fontsize=11, loc="left")
    fig.colorbar(im, ax=ax, shrink=0.6, label="correlation")
    dendrogram(z, labels=corr.index.tolist(), orientation="right", ax=axd, color_threshold=0,
               above_threshold_color="#555", leaf_font_size=9)
    axd.set_xlabel("distance = 1 − |r|")
    for side in ("top", "right", "left"):
        axd.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_relevance(rel: pd.DataFrame, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    r = rel.dropna(subset=["variable"])
    imp = r.pivot(index="species", columns="variable", values="importance")
    sig = r.pivot(index="species", columns="variable", values="relevant").reindex_like(imp)
    fig, ax = plt.subplots(figsize=(1.1 * imp.shape[1] + 4, 0.45 * imp.shape[0] + 2.2))
    top = max(float(np.nanmax(imp.values)), 0.01)
    im = ax.imshow(imp.values, cmap="Blues", vmin=0, vmax=top, aspect="auto")
    for i in range(imp.shape[0]):
        for j in range(imp.shape[1]):
            if np.isfinite(imp.values[i, j]):
                ax.text(j, i, f"{imp.values[i, j]:.2f}" + ("*" if sig.values[i, j] == True else ""),  # noqa: E712
                        ha="center", va="center", fontsize=8,
                        color="white" if imp.values[i, j] > 0.6 * top else "#333")
    ax.set_xticks(range(imp.shape[1]), imp.columns, rotation=40, ha="right")
    ax.set_yticks(range(imp.shape[0]), imp.index, style="italic")
    ax.set_title("Variable importance per species: drop in test AUC when the variable is shuffled\n"
                 "(* = significant after FDR; model trained on early years, tested on later years)",
                 fontsize=10, loc="left")
    fig.colorbar(im, ax=ax, shrink=0.8, label="AUC drop")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


# ----------------------------------------------------------------------------- main
def run(meta, years, lat, lon, values, out: Path, threshold: float, species: str | None,
        vif_max: float = 10.0, n_perm: int = 100, max_species: int = 40):
    out.mkdir(parents=True, exist_ok=True)
    names = list(values)
    rep = [f"# Variable study ({meta['region']['name']}, {years[0]}–{years[-1]}, {len(lat)} sea cells)", ""]

    tr = trend_table(values, lat)
    tr.round(4).to_csv(out / "trends.csv", index=False)
    rep += ["## 1. Trends (Sen + Mann-Kendall; cells corrected with Benjamini-Hochberg)", tr.round(3).to_string(index=False), ""]

    # Environmental space: every cell-year is a point. This is what a niche model sees.
    env = pd.DataFrame({v: values[v].ravel() for v in names})
    # Year-to-year co-variation: per-cell standardised anomalies (removes the map, keeps the time signal).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ano = pd.DataFrame({v: ((values[v] - np.nanmean(values[v], 0)) / np.nanstd(values[v], 0)).ravel() for v in names})
    keep = names
    for key, df, method, title in [("space", env, "spearman", "2a. Redundancy in environmental space (cell-years)"),
                                   ("time", ano, "pearson", "2b. Redundancy in year-to-year variation (anomalies)")]:
        df = df.replace([np.inf, -np.inf], np.nan)
        flat_cols = [c for c in df if df[c].notna().sum() < 100 or df[c].nunique() < 2]
        df = df.drop(columns=flat_cols)                 # no data or no variation: nothing to correlate
        corr = df.corr(method=method)
        z, groups = cluster(corr, threshold)
        sel = representatives(corr, groups)
        var_exp, loadings = pca_summary(df)
        k90 = int(np.searchsorted(np.cumsum(var_exp), 0.9) + 1)
        corr.round(3).to_csv(out / f"correlation_{key}.csv")
        loadings.round(3).to_csv(out / f"pca_loadings_{key}.csv")
        fig_corr(corr, z, title, out / f"correlation_{key}.png")
        kept, dropped, final_vif = prune_vif(df, sel.keep.tolist(), vif_max)
        rep += [f"## {title}", *([f"Left out (no data or no variation): {', '.join(flat_cols)}."] if flat_cols else []),
                f"{df.shape[1]} variables -> {len(sel)} groups at |r| >= {threshold}. "
                f"PCA: {k90} components explain 90 % (PC1 {var_exp[0]:.0%}, PC2 {var_exp[1]:.0%}).", "",
                sel.to_string(index=False), "",
                f"Then dropped by VIF: {', '.join(dropped) if dropped else 'none'}.",
                f"**{len(kept)} variables left: {', '.join(kept)}**", "",
                f"VIF of those (1 = independent, > {vif_max:.0f} = redundant):", final_vif.round(1).to_string(), ""]
        if key == "space":
            keep = kept
    rep += [f"**Kept for the species step:** {', '.join(keep)}", ""]

    if species:
        obs = read_species(species, meta, years)
        rel = species_relevance(obs, values, keep, years, n_perm=n_perm, max_species=max_species)
        rel.round(4).to_csv(out / "species_relevance.csv", index=False)
        ok = rel.dropna(subset=["variable"]) if len(rel) else rel
        changing = set(tr[tr.regional_p < 0.05].variable)
        rep += ["## 3. Relevance per species (temporal hold-out, permutation importance)"]
        if len(ok):
            fig_relevance(ok, out / "species_relevance.png")
            summary = ok.groupby("species").apply(lambda g: pd.Series({
                "n": g.n_presences.iloc[0], "auc_test": round(g.auc_test.iloc[0], 2), "p_model": round(g.p_model.iloc[0], 3),
                "relevant": ", ".join(g[g.relevant].sort_values("importance", ascending=False).variable) or "-",
                "indicators_of_change": ", ".join(v for v in g[g.relevant].variable if v in changing) or "-"}),
                include_groups=False)
            # Significance alone keeps almost everything: with thousands of records a tiny effect
            # still comes out significant. Rank by how much of a species' importance each variable
            # carries, and cut where that runs out.
            good = ok[ok.p_model < 0.05]
            total = good.groupby("species").importance.transform(lambda x: x.clip(lower=0).sum())
            tab = good.assign(share=good.importance.clip(lower=0) / total.replace(0, np.nan)).groupby("variable").agg(
                species_relevant=("relevant", "sum"), median_importance=("importance", "median"),
                mean_share=("share", "mean")).sort_values("mean_share", ascending=False)
            tab["cumulative_share"] = tab.mean_share.cumsum()
            minimal = tab.index[:int((tab.cumulative_share < 0.7).sum()) + 1].tolist()
            changing = set(tr[tr.regional_p < 0.05].variable)
            rep += [summary.to_string(), "",
                    "relevant = shuffling it lowers the AUC on the held-out later years (q < 0.05).",
                    "indicators_of_change = relevant AND the variable has a significant regional trend.",
                    "Trust a row only if p_model < 0.05: otherwise the model does not beat chance on the later years.", "",
                    f"### Which variables carry the weight ({good.species.nunique()} species with a valid model)",
                    tab.round(4).to_string(), "",
                    f"**Minimal set (70 % of the importance): {', '.join(minimal)}**",
                    "Of those, with a significant regional trend as well: "
                    + (", ".join(v for v in minimal if v in changing) or "-"),
                    "Everything relevant anywhere (the widest set): " + (", ".join(sorted(set(ok[ok.relevant].variable))) or "-"), ""]
            tab.round(4).to_csv(out / "species_variable_summary.csv")
        skipped = rel[rel.variable.isna()] if len(rel) else rel
        rep += [f"Skipped {s}: {n}" for s, n in zip(skipped.get("species", []), skipped.get("note", []))]
        if not len(rel):
            rep += ["No species with enough records inside the grid and years."]
    (out / "report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def selftest():
    rng = np.random.default_rng(1)
    nt, nc = 23, 600
    t = np.arange(nt)[:, None]
    s, p = sen_mk(0.05 * t + rng.normal(0, 0.3, (nt, 400)))
    assert abs(np.median(s) - 0.05) < 0.005 and (p < 0.05).mean() > 0.95
    assert (sen_mk(rng.normal(0, 1, (nt, 2000)))[1] < 0.05).mean() < 0.08          # false positives near 5 %
    col = rng.normal(size=nt)
    assert abs(sen_mk(col[:, None])[0][0] - stats.theilslopes(col)[0]) < 1e-12
    # A species that needs warm water and ignores the wind must come out that way.
    base = rng.uniform(14, 22, nc)
    values = {"sst": base + 0.04 * t + rng.normal(0, 0.3, (nt, nc)), "wind": rng.normal(7, 1, (nt, nc)),
              "evap": rng.normal(3, 0.5, (nt, nc))}
    values["lhf"] = 30 * values["evap"] + rng.normal(0, 1, (nt, nc))               # redundant with evap
    years = np.arange(2003, 2003 + nt)
    tt, kk = np.meshgrid(np.arange(nt), np.arange(nc), indexing="ij")
    seen = rng.random((nt, nc)) < 0.25                                              # sampling effort
    warm = seen & (rng.random((nt, nc)) < 1 / (1 + np.exp(-3 * (values["sst"] - 20))))
    obs = pd.concat([pd.DataFrame({"species": "Warm fish", "t": tt[warm], "k": kk[warm]}),
                     pd.DataFrame({"species": "Other", "t": tt[seen], "k": kk[seen]})])
    corr = pd.DataFrame({v: x.ravel() for v, x in values.items()}).corr(method="spearman")
    keep = representatives(corr, cluster(corr, 0.8)[1]).keep.tolist()
    assert sorted(keep) == ["lhf", "sst", "wind"], keep                             # evap folded into lhf
    d = pd.DataFrame({v: x.ravel() for v, x in values.items()})
    d["tqv"] = d["sst"] * 2 - d["wind"] * 3                   # an exact combination of two others
    assert np.isinf(vif(d)["tqv"]) and np.isinf(vif(d)["wind"])
    left, gone, rest = prune_vif(d, list(d), 5.0)
    # Of the three tied at infinity the lowest-priority one goes, so sst and wind survive;
    # evap goes too, being the lower-priority twin of lhf.
    assert sorted(left) == ["lhf", "sst", "wind"] and "tqv" in gone[0] and "evap" in gone[1], (left, gone)
    assert (rest < 10).all()
    rel = species_relevance(obs, values, keep, years, n_perm=100, must=[]).query("species == 'Warm fish'").set_index("variable")
    assert rel.loc["sst", "relevant"] and not rel.loc["wind", "relevant"] and not rel.loc["lhf", "relevant"], rel
    assert rel.auc_test.iloc[0] > 0.8 and rel.p_model.iloc[0] < 0.05
    from sklearn.metrics import roc_auc_score
    yy = (rng.random(500) < 0.3).astype(int)
    ss = rng.normal(size=500) + yy
    assert abs(auc(yy, ss) - roc_auc_score(yy, ss)) < 1e-12
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "all_sources")
    ap.add_argument("--exclude", nargs="*", default=[], help="variable ids to leave out")
    ap.add_argument("--threshold", type=float, default=0.8, help="|r| above which two variables are redundant")
    ap.add_argument("--vif", type=float, default=10.0, help="VIF above which a variable is dropped")
    ap.add_argument("--species", help="CSV: species, lat, lon, year")
    ap.add_argument("--max-species", type=int, default=40, help="species modelled, most records first")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    run(*load(a.data, tuple(a.exclude)), out=a.out, threshold=a.threshold, species=a.species, vif_max=a.vif, max_species=a.max_species)


if __name__ == "__main__":
    main()
