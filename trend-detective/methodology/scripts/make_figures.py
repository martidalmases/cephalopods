"""
Figures and the machine-readable summary of the variable-selection study, drawn only from
the CSV files the other scripts write (run them first: see run_all.sh).

    python3 scripts/make_figures.py

Writes figures/fig1 ... fig8 (PNG) and results/summary.json. Needs numpy, pandas, scipy, matplotlib.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

ROOT = Path(__file__).resolve().parent.parent
DATA, ALL, NASA, FIGS = ROOT / "data", ROOT / "results" / "all_sources", ROOT / "results" / "nasa_only", ROOT / "figures"
BLUE, ORANGE, GREY, INK, RED = "#2166ac", "#d9822b", "#b8b8b8", "#333333", "#b2182b"
DIV = LinearSegmentedColormap.from_list("div", [BLUE, "#ececec", RED])

# id -> (full name, unit, source)
NAMES = {
    "sst": ("Sea surface temperature, yearly mean", "°C", "NASA MERRA-2"),
    "sst_winter_min": ("Coldest-month sea temperature", "°C", "NASA MERRA-2"),
    "sst_summer_max": ("Hottest-month sea temperature", "°C", "NASA MERRA-2"),
    "sst_range": ("Seasonal temperature range", "°C", "NASA MERRA-2"),
    "mhw_months": ("Unusually hot months", "months/yr", "NASA MERRA-2"),
    "dt_sea_air": ("Sea-air temperature difference", "°C", "NASA MERRA-2"),
    "chl": ("Chlorophyll-a (phytoplankton biomass)", "mg/m³", "NASA MODIS-Aqua"),
    "nflh": ("Phytoplankton fluorescence", "mW/cm²/µm/sr", "NASA MODIS-Aqua"),
    "aod": ("Aerosol optical depth", "", "NASA MERRA-2"),
    "wind": ("Surface wind speed", "m/s", "NASA MERRA-2"),
    "evap": ("Evaporation", "mm/day", "NASA MERRA-2"),
    "precip": ("Precipitation", "mm/day", "NASA MERRA-2"),
    "lhf": ("Latent heat flux", "W/m²", "NASA MERRA-2"),
    "shf": ("Sensible heat flux", "W/m²", "NASA MERRA-2"),
    "q2m": ("Specific humidity", "g/kg", "NASA MERRA-2"),
    "dq_sea_air": ("Sea-air humidity difference", "g/kg", "NASA MERRA-2"),
    "tqv": ("Total column water vapour", "kg/m²", "NASA MERRA-2"),
    "sss": ("Sea surface salinity", "PSU", "Copernicus Marine"),
    "ssh": ("Sea surface height", "m", "Copernicus Marine"),
    "ph": ("pH", "", "Copernicus Marine"),
    "o2": ("Dissolved oxygen", "mmol/m³", "Copernicus Marine"),
    "no3": ("Nitrate", "mmol/m³", "Copernicus Marine"),
}
INDICATORS = ["Fistularia commersonii", "Thalassoma pavo", "Sparisoma cretense", "Sphyraena viridensis",
              "Caranx crysos", "Epinephelus marginatus", "Caulerpa cylindracea",
              "Pinna nobilis", "Paramuricea clavata", "Posidonia oceanica"]
FINAL = ["sst_winter_min", "sst_summer_max", "mhw_months", "chl"]
# The team's decision and its reason for every variable. This is a JUDGEMENT informed by the
# tests, not an output of them: edit it here and the summary and the figures follow.
DECISION = {
    "sst_winter_min": ("final", "Statistics and mechanism agree; passes every test in every set."),
    "mhw_months": ("final", "Statistics and mechanism agree; passes every test in every set."),
    "sst_summer_max": ("final", "Kept on mechanism (upper thermal limit); low importance, passes in 3 of 4 sets."),
    "chl": ("final", "Kept on mechanism (food supply) and as the only direct satellite measurement; mixed evidence."),
    "dq_sea_air": ("dropped", "Sea temperature in another form: 71 % of it is explained by the coldest and hottest month."),
    "tqv": ("dropped", "Passes the tests but has no mechanism on a submerged organism."),
    "lhf": ("dropped", "Geographic marker: does not transfer across the basin, and has no trend."),
    "wind": ("dropped", "Geographic marker: makes prediction worse across the basin, and has no trend."),
    "nflh": ("dropped", "Geographic marker: all its importance disappears once position is known."),
    "aod": ("dropped", "Passes the transfer tests but does not change (9 % of cells) and has no direct mechanism."),
    "shf": ("dropped", "Passes the transfer tests; weak trend (37 % of cells) and atmospheric."),
    "precip": ("dropped", "Passes the transfer tests; weak trend (20 % of cells) and atmospheric."),
    "dt_sea_air": ("dropped", "Passes the transfer tests; weak trend (38 % of cells), low importance."),
    "sst": ("redundant", "Explained by the coldest and hottest month (VIF 32)."),
    "sst_range": ("redundant", "Exactly hottest month minus coldest month (VIF > 100 000)."),
    "q2m": ("redundant", "Correlated with the coldest month (r = +0.89)."),
    "evap": ("redundant", "Same quantity as latent heat flux (r = +1.00)."),
    "o2": ("not NASA", "Copernicus model; also redundant with sea temperature (r = -0.88)."),
    "sss": ("not NASA", "Copernicus model. Important in the all-sources study; strong west-east gradient."),
    "ssh": ("not NASA", "Copernicus model."),
    "ph": ("not NASA", "Copernicus model."),
    "no3": ("not NASA", "Copernicus model."),
}


def name(v: str) -> str:
    return NAMES.get(v, (v,))[0]


def clean(ax, grid: str | None = "x"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#bbbbbb")
    ax.tick_params(length=0, colors=INK)
    if grid:
        ax.grid(axis=grid, color="#e6e6e6", lw=0.8)
        ax.set_axisbelow(True)


def save(fig, file: str):
    fig.savefig(FIGS / file, dpi=150, bbox_inches="tight")
    plt.close(fig)


def yearly(v: str):
    meta = json.loads((DATA / "meta.json").read_text())
    doc = json.loads((DATA / f"{v}.json").read_text())
    return np.array(meta["years"]), np.array([[np.nan if c is None else c for c in row] for row in doc["values"]], float)


def fig1_trends():
    t = pd.read_csv(ALL / "trends.csv").sort_values("pct_cells_significant")
    fig, ax = plt.subplots(figsize=(10, 0.36 * len(t) + 1.6))
    y = np.arange(len(t))
    ax.barh(y, t.pct_cells_significant, height=0.6, color=BLUE)
    tr = ax.get_yaxis_transform()
    for i, r in enumerate(t.itertuples()):
        inside = r.pct_cells_significant > 15
        ax.text(r.pct_cells_significant + (-1.5 if inside else 1.5), i, f"{r.pct_cells_significant:.0f} %", va="center",
                ha="right" if inside else "left", fontsize=8, color="white" if inside else INK)
        note = "" if r.regional_p < 0.05 else "  (not significant)"
        ax.text(1.02, i, f"{r.regional_slope_per_decade:+.3g} {NAMES[r.variable][1]}/decade{note}", transform=tr,
                va="center", fontsize=8, color="#666666")
    ax.set_yticks(y, [name(v) for v in t.variable], fontsize=9)
    ax.set_xlim(0, 100)
    ax.set_xlabel("% of sea cells with a significant trend (Mann-Kendall, Benjamini-Hochberg 5 %)", color=INK)
    ax.set_title("Step 1. Which variables change in the Mediterranean, 2003-2025", loc="left", fontsize=12, color=INK)
    ax.text(1.02, len(t) - 0.2, "basin-wide trend (Sen's slope)", transform=tr, fontsize=8, color="#666666", va="bottom")
    clean(ax)
    save(fig, "fig1_trends.png")


def fig2_series():
    fig, axes = plt.subplots(2, 2, figsize=(10, 6), sharex=True)
    for ax, v in zip(axes.ravel(), FINAL):
        years, x = yearly(v)
        s = np.nanmean(x, axis=1)
        slopes = [(s[j] - s[i]) / (years[j] - years[i]) for i in range(len(s)) for j in range(i + 1, len(s))]
        b = np.median(slopes)
        ax.plot(years, s, color=BLUE, lw=2, marker="o", ms=4)
        ax.plot(years, np.median(s - b * years) + b * years, color=INK, lw=1, ls="--")
        ax.set_title(f"{name(v)}  ({10 * b:+.2g} {NAMES[v][1]}/decade)", loc="left", fontsize=10, color=INK)
        ax.set_ylabel(NAMES[v][1], color=INK, fontsize=9)
        clean(ax, "y")
    fig.suptitle("The four final variables: Mediterranean mean, year by year (dashed: Sen's slope)", x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout()
    save(fig, "fig2_final_series.png")


def fig3_correlation():
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform
    c = pd.read_csv(ALL / "correlation_space.csv", index_col=0)
    d = 1 - c.abs().values
    np.fill_diagonal(d, 0)
    order = leaves_list(linkage(squareform((d + d.T) / 2, checks=False), "average"))
    c = c.iloc[order, order]
    n = len(c)
    fig, ax = plt.subplots(figsize=(0.5 * n + 5, 0.5 * n + 2))
    im = ax.imshow(c.values, cmap=DIV, vmin=-1, vmax=1)
    for i in range(n):
        for j in range(n):
            if i != j and abs(c.values[i, j]) >= 0.5:
                ax.text(j, i, f"{c.values[i, j]:.2f}", ha="center", va="center", fontsize=6.5,
                        color="white" if abs(c.values[i, j]) > 0.7 else INK)
    ax.set_xticks(range(n), [name(v) for v in c.columns], rotation=55, ha="right", fontsize=8)
    ax.set_yticks(range(n), [name(v) for v in c.index], fontsize=8)
    ax.tick_params(length=0)
    ax.set_title("Step 2. Spearman correlation between variables over cell-years (only |r| ≥ 0.5 is written)", loc="left", fontsize=11, color=INK)
    fig.colorbar(im, ax=ax, shrink=0.5, label="correlation")
    save(fig, "fig3_correlation.png")


def fig4_importance():
    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    for ax, folder, title in [(axes[0], ALL, "All sources"), (axes[1], NASA, "NASA data only")]:
        t = pd.read_csv(folder / "species_variable_summary.csv").iloc[::-1]
        chosen = t.cumulative_share.shift(-1, fill_value=0).values < 0.7
        colours = [(BLUE if NAMES[v][2].startswith("NASA") else ORANGE) if c else GREY for v, c in zip(t.variable, chosen)]
        y = np.arange(len(t))
        ax.barh(y, 100 * t.mean_share, height=0.6, color=colours)
        for i, s in enumerate(100 * t.mean_share):
            ax.text(s + 0.3, i, f"{s:.0f} %", va="center", fontsize=8, color=INK)
        ax.set_yticks(y, [name(v) for v in t.variable], fontsize=9)
        ax.set_xlabel("mean share of a species' importance", color=INK)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        clean(ax)
    fig.legend([plt.Rectangle((0, 0), 1, 1, color=c) for c in (BLUE, ORANGE, GREY)],
               ["NASA, within the first 70 %", "Copernicus, within the first 70 %", "outside"], loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle("Step 3. Which variables the models need to predict where species occur in unseen years", x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    save(fig, "fig4_importance.png")


def fig5_species():
    r = pd.read_csv(NASA / "species_relevance.csv").dropna(subset=["variable"])
    r = r[r.species.isin(INDICATORS)]
    order = pd.read_csv(NASA / "species_variable_summary.csv").variable.tolist()
    imp = r.pivot(index="species", columns="variable", values="importance").reindex(index=[s for s in INDICATORS if s in set(r.species)], columns=order)
    sig = r.pivot(index="species", columns="variable", values="relevant").reindex_like(imp)
    auc = r.groupby("species").auc_test.first()
    fig, ax = plt.subplots(figsize=(0.85 * imp.shape[1] + 4, 0.5 * imp.shape[0] + 3))
    top = float(np.nanmax(imp.values))
    im = ax.imshow(np.clip(imp.values, 0, None), cmap="Blues", vmin=0, vmax=top, aspect="auto")
    for i in range(imp.shape[0]):
        for j in range(imp.shape[1]):
            if sig.values[i, j] == True:  # noqa: E712
                ax.text(j, i, f"{imp.values[i, j]:.2f}", ha="center", va="center", fontsize=8,
                        color="white" if imp.values[i, j] > 0.55 * top else INK)
    ax.set_xticks(range(imp.shape[1]), [name(v) for v in imp.columns], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(imp.shape[0]), [f"{s}  (AUC {auc[s]:.2f})" for s in imp.index], style="italic", fontsize=9)
    ax.tick_params(length=0)
    ax.set_title("Step 3. Candidate indicator species: AUC drop when each variable is shuffled (NASA only)\n"
                 "A number is written only where the drop is significant (Benjamini-Hochberg 5 %)", loc="left", fontsize=11, color=INK)
    fig.colorbar(im, ax=ax, shrink=0.7, label="AUC drop")
    save(fig, "fig5_indicator_species.png")


def fig6_transfer():
    t = pd.read_csv(NASA / "transfer_summary.csv", index_col=0).sort_values("time")
    y = np.arange(len(t))
    fig, axes = plt.subplots(1, 2, figsize=(12, 0.42 * len(t) + 2), sharey=True)
    for ax, col, title in [(axes[0], "kept_space", "Trained on one half of the basin, tested on the other"),
                           (axes[1], "kept_coords", "Latitude and longitude also given to the model")]:
        v = 100 * t[col].clip(lower=-20, upper=250)
        ax.barh(y, v, height=0.6, color=[BLUE if k >= 50 else RED for k in 100 * t[col]])
        for i, k in enumerate(100 * t[col]):
            ax.text(max(v.iloc[i], 0) + 4, i, f"{k:.0f} %", va="center", fontsize=8, color=INK)
        ax.axvline(50, color=INK, lw=1, ls="--")
        ax.set_xlim(-20, 285)
        ax.set_xlabel("% of the importance kept (dashed: the 50 % rule)", color=INK)
        ax.set_title(title, loc="left", fontsize=10, color=INK)
        clean(ax)
    axes[0].set_yticks(y, [name(v) for v in t.index], fontsize=9)
    fig.legend([plt.Rectangle((0, 0), 1, 1, color=c) for c in (BLUE, RED)], ["keeps at least half", "loses more than half"],
               loc="lower center", ncol=2, frameon=False, fontsize=9)
    fig.suptitle("Step 4. Real condition or geographic marker? Variables ordered by importance (top = most)", x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    save(fig, "fig6_transfer.png")


def fig7_humidity():
    e = pd.read_csv(NASA / "explained_by_temperature.csv").set_index("variable")
    _, hot = yearly("sst_summer_max")
    rng = np.random.default_rng(0)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    for ax, v in zip(axes, ("dq_sea_air", "tqv")):
        _, x = yearly(v)
        ok = np.flatnonzero(np.isfinite(hot.ravel()) & np.isfinite(x.ravel()))
        pick = rng.choice(ok, 4000, replace=False)
        ax.scatter(hot.ravel()[pick], x.ravel()[pick], s=6, color=BLUE, alpha=0.35, lw=0)
        ax.set_xlabel(f"{name('sst_summer_max')} (°C)", color=INK)
        ax.set_ylabel(f"{name(v)} ({NAMES[v][1]})", color=INK)
        ax.set_title(f"{name(v)}\n{100 * e.loc[v, 'r2_from_sea_temperature']:.0f} % explained by the coldest and hottest month",
                     loc="left", fontsize=10, color=INK)
        clean(ax, "both")
    fig.suptitle("Step 5. Is the variable sea temperature in another form? (4 000 cell-years)", x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout()
    save(fig, "fig7_humidity_vs_temperature.png")


def fig8_sets():
    s = pd.read_csv(NASA / "transfer_sets.csv")
    sets = list(dict.fromkeys(s.set))
    order = pd.read_csv(NASA / "transfer_summary.csv", index_col=0).index.tolist()
    grid = s.pivot(index="variable", columns="set", values="passes").reindex(index=order, columns=sets)
    head = s.groupby("set").agg(n=("n_variables", "first"), auc=("median_auc_time", "first")).reindex(sets)
    fig, ax = plt.subplots(figsize=(9.5, 0.42 * len(order) + 2.6))
    for i, v in enumerate(order):
        for j, k in enumerate(sets):
            val = grid.loc[v, k]
            if pd.isna(val):
                ax.text(j, i, "not in set", ha="center", va="center", fontsize=7.5, color="#aaaaaa")
            else:
                ax.add_patch(plt.Rectangle((j - 0.46, i - 0.4), 0.92, 0.8, color=BLUE if val else RED))
                ax.text(j, i, "passes" if val else "fails", ha="center", va="center", fontsize=8, color="white")
    ax.set_xlim(-0.5, len(sets) - 0.5)
    ax.set_ylim(len(order) - 0.5, -0.5)
    ax.set_xticks(range(len(sets)), [f"{k}\n({head.n[k]} variables, median AUC {head.auc[k]:.2f})" for k in sets], fontsize=8)
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(order)), [name(v) for v in order], fontsize=9)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(length=0, colors=INK)
    ax.set_title("Step 6. The verdict of the transfer rule depends on the company a variable keeps", loc="left", fontsize=12, color=INK, pad=38)
    save(fig, "fig8_sets.png")


def write_summary():
    trends = pd.read_csv(ALL / "trends.csv").set_index("variable")
    q_all = pd.read_csv(ALL / "species_variable_summary.csv").set_index("variable")
    q_nasa = pd.read_csv(NASA / "species_variable_summary.csv").set_index("variable")
    tr = pd.read_csv(NASA / "transfer_summary.csv", index_col=0)
    sets = pd.read_csv(NASA / "transfer_sets.csv")
    passed = sets.groupby("variable").passes.agg(["sum", "count"])

    def num(table, v, col):
        return None if v not in table.index or pd.isna(table.loc[v, col]) else round(float(table.loc[v, col]), 4)

    variables = []
    for v, (full, unit, source) in NAMES.items():
        variables.append({
            "id": v, "name": full, "unit": unit, "source": source, "nasa": source.startswith("NASA"),
            "decision": DECISION[v][0], "reason": DECISION[v][1],
            "trend": {"pct_cells_significant": num(trends, v, "pct_cells_significant"),
                      "basin_slope_per_decade": num(trends, v, "regional_slope_per_decade"), "basin_p": num(trends, v, "regional_p")},
            "importance_share": {"all_sources": num(q_all, v, "mean_share"), "nasa_only": num(q_nasa, v, "mean_share")},
            "transfer": {"kept_across_basin": num(tr, v, "kept_space"), "kept_with_coordinates": num(tr, v, "kept_coords"),
                         "sets_passed": None if v not in passed.index else f"{int(passed.loc[v, 'sum'])} of {int(passed.loc[v, 'count'])}"}})
    out = {"final_variables": FINAL, "period": "2003-2025", "region": "Mediterranean Sea, 0.5 degree cells",
           "note": "decision and reason are the team's judgement, informed by the tests; every number comes from results/*.csv",
           "variables": variables}
    (ROOT / "results" / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    for f in (fig1_trends, fig2_series, fig3_correlation, fig4_importance, fig5_species, fig6_transfer, fig7_humidity, fig8_sets):
        f()
    write_summary()
    print("figures ->", FIGS, "| summary ->", ROOT / "results" / "summary.json")


if __name__ == "__main__":
    main()
