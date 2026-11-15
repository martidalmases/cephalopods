"""
STANDALONE download of species records from OBIS. One file, no login, no other script.

    pip install requests          # or ~/Desktop/.venv/bin/pip install requests
    python3 fetch_obis.py         # -> obis.csv  (Mediterranean, 2003-2025)
    python3 fetch_obis.py --taxa Teleostei "Posidonia oceanica"
    python3 fetch_obis.py --world --out obis_world.csv
    python3 fetch_obis.py --selftest

Writes species, lat, lon, year: the input of `study_variables.py --species obis.csv`.

Why whole groups and not only your species: the study compares where a species WAS
recorded against where OTHER species were recorded but not that one. Those other records
tell where and when people were looking, which is what corrects the sampling bias. A taxon
name such as "Teleostei" brings every species below it.
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger("obis")
OBIS = "https://api.obis.org/v3/occurrence"
MEDITERRANEAN = "POLYGON((-6 30,36.5 30,36.5 46,-6 46,-6 30))"
PAGE = 5000
FIRST_YEAR, LAST_YEAR = 2003, 2025
# Background groups first, then the candidate indicator species (kept even if their group is not listed).
TAXA = ["Teleostei", "Cephalopoda", "Anthozoa", "Bivalvia",
        "Thalassoma pavo", "Sparisoma cretense", "Sphyraena viridensis", "Epinephelus marginatus",
        "Caranx crysos", "Fistularia commersonii", "Caulerpa cylindracea",
        "Pinna nobilis", "Paramuricea clavata", "Posidonia oceanica"]


def _get(url: str, tries: int = 4, **kw) -> requests.Response:
    """GET with retries: OBIS times out now and then on the big groups."""
    err = ""
    for i in range(tries):
        try:
            r = requests.get(url, **kw)
            if r.status_code < 500:
                return r
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = str(e)
        log.warning("retry %d/%d: %s", i + 1, tries, err)
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"giving up: {err}")


def fetch_taxon(name: str, geometry: str | None, first_year: int, limit: int) -> list[tuple]:
    """(species, lat, lon, year) of every record of the taxon and its children, identified to species."""
    rows, after, seen = [], None, 0
    while seen < limit:
        params = {"scientificname": name, "startdate": f"{first_year}-01-01", "size": PAGE,
                  "fields": "id,species,decimalLatitude,decimalLongitude,date_year"}
        if geometry:
            params["geometry"] = geometry
        if after:
            params["after"] = after
        r = _get(OBIS, params=params, timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"OBIS HTTP {r.status_code}: {r.text[:200]}")
        batch = r.json().get("results", [])
        if not batch:
            break
        for o in batch:
            sp, la, lo, y = o.get("species"), o.get("decimalLatitude"), o.get("decimalLongitude"), o.get("date_year")
            if sp and la is not None and lo is not None and y and FIRST_YEAR <= int(y) <= LAST_YEAR:
                rows.append((sp, round(float(la), 4), round(float(lo), 4), int(y)))
        seen += len(batch)
        after = batch[-1]["id"]
        if seen % 50000 < PAGE:
            log.info("%s: %d records read", name, seen)
        if len(batch) < PAGE:
            break
    return rows


def run(taxa: list[str], out: Path, geometry: str | None, first_year: int, limit: int) -> None:
    rows: set[tuple] = set()
    for name in taxa:
        try:
            got = fetch_taxon(name, geometry, first_year, limit)
        except Exception as e:                       # one failing taxon does not stop the others
            log.error("%s FAILED: %s", name, e)
            continue
        rows.update(got)
        log.info("%s: %d records with species, position and year", name, len(got))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["species", "lat", "lon", "year"])
        w.writerows(sorted(rows))
    log.info("%d distinct records of %d species -> %s", len(rows), len({r[0] for r in rows}), out)
    log.info("Send that file back.")


def selftest() -> None:
    import tempfile
    global _get
    pages = {None: [{"id": "a", "species": "Thalassoma pavo", "decimalLatitude": 41.2, "decimalLongitude": 2.9, "date_year": 2010},
                    {"id": "b", "species": None, "decimalLatitude": 41.0, "decimalLongitude": 3.0, "date_year": 2011}] * (PAGE // 2),
             "b": [{"id": "c", "species": "Diplodus sargus", "decimalLatitude": 39.5, "decimalLongitude": 2.5, "date_year": 2015},
                   {"id": "d", "species": "Diplodus sargus", "decimalLatitude": 39.5, "decimalLongitude": 2.5},
                   {"id": "e", "species": "Old fish", "decimalLatitude": 39.0, "decimalLongitude": 2.0, "date_year": 1998}]}
    asked = []

    class Resp:
        status_code, text = 200, ""

        def __init__(self, results):
            self._r = results

        def json(self):
            return {"results": self._r}

    def fake_get(url, params=None, **kw):
        asked.append(dict(params))
        return Resp(pages[params.get("after")])

    real, _get = _get, fake_get
    try:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x" / "obis.csv"
            run(["Teleostei"], out, MEDITERRANEAN, FIRST_YEAR, 10 ** 6)
            lines = out.read_text().splitlines()
    finally:
        _get = real
    assert lines == ["species,lat,lon,year", "Diplodus sargus,39.5,2.5,2015", "Thalassoma pavo,41.2,2.9,2010"], lines
    assert len(asked) == 2 and asked[1]["after"] == "b" and asked[0]["geometry"] == MEDITERRANEAN
    print("selftest ok")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--taxa", nargs="+", default=TAXA, help="taxon or species names (children included)")
    ap.add_argument("--out", type=Path, default=Path("obis.csv"))
    ap.add_argument("--world", action="store_true", help="no Mediterranean limit (much bigger)")
    ap.add_argument("--first-year", type=int, default=FIRST_YEAR)
    ap.add_argument("--limit", type=int, default=400_000, help="records asked per taxon at most")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    run(a.taxa, a.out, None if a.world else MEDITERRANEAN, a.first_year, a.limit)


if __name__ == "__main__":
    main()
