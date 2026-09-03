# Data sources and preparation notes

All data used in this project is public. Raw downloads are git-ignored (`data/raw/`); this file
records what was downloaded, when, and what the field profiling found, so the evaluation is
reproducible.

## Ontario DWSP (Drinking Water Surveillance Program)

- URL: https://data.ontario.ca/dataset/drinking-water-surveillance-program
- Licence: Open Government Licence – Ontario
- Downloaded 2026-09-03:
  - `DWSP 2023-2024 Open Data.csv` — 91,081 rows, 101 systems
  - `DWSP 2018-2022 Open Data.csv` — 245,463 rows, 138 systems
- ⚠️ **Field-profiling finding (risk register item #1, confirmed and mitigated):**
  the 2023–24 file contains only 45 chlorine records (15 free-chlorine, 6 numeric) — DWSP is a
  *scientific* survey focused on lab parameters, and recent files are thin on field chlorine.
  The **2018–2022 file is the usable chlorine source**: 9,952 chlorine records across 114 systems,
  of which 3,460 numeric free-chlorine (906 at DISTRIBUTION points). Free chlorine (mg/L):
  mean 1.12, median 1.24, p5 0.00, p95 2.30.
- Note: column naming differs between files (e.g. `DWS_NAME` vs `Drinking Water System Name`) —
  the M1 import layer must normalise both schemas.
- Use: calibrate realistic free-chlorine distributions; descriptive statistics for report.

## WNTR (US EPA)

- URL: https://usepa.github.io/WNTR/
- Installed: wntr 1.5.0 (project `.venv`, 2026-09-03)
- Smoke test: `examples/wntr_ltown_chlorine_check.py` ran a 72-hour chlorine simulation on the
  BattLeDIM L-TOWN network (785 nodes, 909 links) with 1.0 mg/L sources at reservoirs R1/R2.
  Chlorine decay observed as expected — dead-end/far junctions decay to 0.0 mg/L while
  near-source junctions hold ~1.0; transport delays of 1–13 h visible across sample nodes.
  Evidence: `docs/evidence/2026-09-03-wntr-ltown-chlorine.png` and
  `docs/evidence/2026-09-03-wntr-ltown-check.log`.
- Use: generate labelled synthetic chlorine series for the Maple Creek case and inject anomaly
  events so precision/recall/lead-time can be computed against ground truth.

## BattLeDIM 2020 (L-Town)

- URL: https://doi.org/10.5281/zenodo.4017659 (record 4017659)
- Downloaded 2026-09-03 (~95 MB): `L-TOWN.inp`, `README.txt`, `2018_Leakages.csv`,
  `2019_Leakages.csv`, `2019_SCADA_{Pressures,Demands,Levels,Flows}.csv`
- Deliberately skipped: the two 92 MB `.xlsx` SCADA workbooks (CSV equivalents downloaded instead).
- Use: labelled benchmark for comparing the anomaly-detection channel against published methods.

## Field verification note

Regulatory frequencies encoded in `rules/oreg170.yaml` must be verified line-by-line against
the current consolidated text of O. Reg. 170/03 on CanLII:
https://www.canlii.org/en/on/laws/regu/o-reg-170-03/latest/o-reg-170-03.html
Every rule row carries a `source_section` field; do not mark a rule `verified: true` until the
section has been checked against CanLII.
