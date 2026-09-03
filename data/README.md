# Data sources and preparation notes

All data used in this project is public. Raw downloads are git-ignored (`data/raw/`); document
what was downloaded, when, and from which URL in this file so the evaluation is reproducible.

## Ontario DWSP (Drinking Water Surveillance Program)

- URL: https://data.ontario.ca/ (search "Drinking Water Surveillance Program")
- Coverage: 1998–2024, Open Government Licence – Ontario
- Use: parameter distribution calibration, descriptive statistics for the proposal/report
- Downloaded: TODO (date, file names, record counts)

## WNTR (US EPA)

- URL: https://usepa.github.io/WNTR/
- Use: simulate chlorine decay in the Maple Creek network, inject labelled anomaly events
  so precision/recall/lead-time can be computed against ground truth
- Installed: TODO (version)

## BattLeDIM 2020 (L-Town)

- URL: https://zenodo.org/ (search "BattLeDIM 2020")
- Use: labelled benchmark for comparison against published methods
- Downloaded: TODO (date, files)

## Field verification note

Regulatory frequencies encoded in `rules/oreg170.yaml` must be verified line-by-line against
the current consolidated text of O. Reg. 170/03 on CanLII:
https://www.canlii.org/en/on/laws/regu/o-reg-170-03/latest/o-reg-170-03.html
Every rule row carries a `source_section` field; do not mark a rule `verified: true` until the
section has been checked against CanLII.
