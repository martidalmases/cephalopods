#!/usr/bin/env bash
# Rebuilds every result, figure and results/summary.json from data/. About two minutes.
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/study_variables.py --species data/obis.csv.gz --out results/all_sources > /dev/null
python3 scripts/study_variables.py --species data/obis.csv.gz --exclude sss ssh ph o2 no3 --out results/nasa_only > /dev/null
python3 scripts/transfer_test.py
python3 scripts/transfer_test.py --sets
python3 scripts/make_figures.py
