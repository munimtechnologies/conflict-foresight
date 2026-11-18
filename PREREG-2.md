# conflict-foresight step 2: VIEWS vs Conflict Forecast on new outbreaks

Pre-registration, frozen 2026-11-18 before any forecast score or outcome was read.
MV2 of Munim, Inc. Made with AI agents. Text CC BY 4.0, code Apache-2.0 (`h2h.py`).
Step 1 (PREREGISTRATION.md) is unchanged; this step reuses its data, outcome and
history rule.

## Question

Two public early-warning systems publish monthly country-level conflict forecasts and
keep their past releases online:

- **VIEWS** (Uppsala University and PRIO): forecasts of state-based conflict deaths from
  conflict history and structural data; archive at https://api.viewsforecasting.org/
  (monthly runs since 2021-12).
- **Conflict Forecast** (Mueller, Rauh and Seimon; conflictforecast.org): the risk of an
  armed-conflict outbreak within 12 months, from millions of news articles plus conflict
  history (`best_model`) or news text alone (`text_model`); monthly vintages since 2021-07
  through its open file API (https://conflictforecast.org/downloads).

Mueller and Rauh built their system for the "hard problem": outbreaks in countries without
recent violence, where conflict history says nothing (JEEA 2022). **Which system saw the
new outbreaks of 2022-2026 coming, in real time, and did either see them at all?**

Prior art searched 2026-11-17/18: VIEWS challenges score fatalities over all countries
(Vesco et al. 2022; Hegre et al., JPR 2025). Chadefaux and Schincariol (EPJ Data Science
14:82, 2025) find lag-only models match richer ones but evaluate their own models and say
their result "may not generalize to conflict onset in previously peaceful countries".
Mueller, Rauh and Seimon (Data & Policy 2024) evaluate their own model out of sample.
A 2025 master's thesis (Zakotianskyi, VU Amsterdam) builds new models on both systems'
datasets; a GitHub project (j-j-murphy/Conflict_World_Models, now offline) used Conflict
Forecast vintages from 2024-02 as a benchmark for its own model. We found no head-to-head
of the two systems' archived real-time forecasts on new outbreaks. We may have missed
something and will correct this section if so.

**Wow if it works:** the first prospective head-to-head of the two leading public conflict
early-warning systems on the outbreaks that matter most for prevention, using only what
each system had actually published before each outbreak, and an episode list of the new
wars that neither system saw coming.

## Real-time vintages only (provenance rule)

The Conflict Forecast API reports when each monthly directory and file was created. Some
vintages were uploaded long after their month or in bulk (on our reading of the directory
list on 2026-11-18: ten directories, 04-2023 to 01-2024, created on 2024-03-06; six
2025 directories created on 2025-12-17; 12-2021, 07-2022 and 11-2022 created 5-11 months
late). Those could contain information from after their month. Rule, fixed now:

- A vintage is **real-time** if its 12-month armed-conflict file
  (`conflictforecast_armedconf_12.csv`, from Oct 2024 `conflictforecast_ons_armedconf_12.csv`)
  was created no later than 75 days after the end of its label month, AND its directory
  is not part of a **bulk upload** (3 or more directories created within 24 hours of each
  other). Otherwise it is "late" or "batch" and is used only in the all-vintages check.
- The forecast used is the file's latest month with a score (each file holds the full
  history since 2010). That month is the origin o; it must be the label month or the
  month before, otherwise the vintage is dropped. One vintage per origin (real-time first).
- VIEWS: run `fatalities00X_YYYY_MM_t01` for the same origin, as in step 1.

Expected real-time origins within 2021-12..2025-09 (whole 12-month window observed by
2026-09): about 23 (01-04/2022, 12/2022-03/2023, 02/2024-01/2025, 06, 07 and 09/2025).

## Definitions (from step 1 unless stated)

- Outcome: any month with >= 25 UCDP state-based deaths in o+1..o+12. At risk ("quiet24"):
  no such month in the 24 months up to o. Data: UCDP GED 26.1 + Candidate 26.01-26.09.
- Sample: countries in VIEWS with population >= 1 million whose ISO3 code (VIEWS `isoab`)
  appears in the Conflict Forecast vintage.
- Scores: V (VIEWS mean predicted log(1 + deaths) over o+1..o+12), C (Conflict Forecast
  `best_model` at o), Ct (`text_model`), H (step-1 history rule), each turned into its
  mid-rank percentile among the at-risk countries scored by both systems at that origin
  (Conflict Forecast vintages to early 2024 are scores, later ones probabilities).
  E = (V percentile + C percentile) / 2.
- Primary: AUC(C) - AUC(V) over all real-time at-risk pairs, country-cluster bootstrap
  (2,000 resamples of countries, seed 20261118). Also average precision, top-decile capture.
- Secondary: C vs H, V vs H, E vs C, E vs V, Ct vs V, Ct vs C; all vintages (incl. late
  and batch); quiet12 and quiet60; a per-capita outcome close to Conflict Forecast's own
  armed-conflict line (a month with deaths >= max(1, 0.5 per million inhabitants), at
  risk = no such month in 24 months); the mean of per-origin AUCs.
- Episode table: each new outbreak (first >= 25-death month after 24 quiet months) from
  2022-01, with V, C and H ranks among at-risk countries at real-time origins 1, 3, 6 and
  12 months before, and whether neither system had it in its top decile.
- Leave one country out on the primary difference.
- Stop rule: fewer than 15 real-time origins or fewer than 5 countries with a positive
  pair means descriptive only.

## Predictions (fixed now, with our probabilities)

| # | Prediction | P |
|---|---|---|
| Q1 | AUC(C) > AUC(V) on new outbreaks (primary) | .55 |
| Q2 | the 95% interval of AUC(C) - AUC(V) lies above 0 | .15 |
| Q3 | the 95% interval lies below 0 | .10 |
| Q4 | AUC(C) > AUC(H) | .65 |
| Q5 | the ensemble E beats both C and V | .50 |
| Q6 | news text alone (Ct) has lower AUC than `best_model` (C) | .70 |
| Q7 | at lead 3, at least half of new outbreaks were in neither system's top decile | .60 |
| Q8 | the all-vintages difference has the same sign as the primary | .75 |
| Q9 | the per-capita-outcome difference has the same sign as the primary | .60 |
| Q10 | no single country flips the sign of the primary difference | .45 |

`h2h.py` grades all ten automatically (`grades` in h2h_results.json).

## What we will claim

- "Conflict Forecast saw new outbreaks coming better than VIEWS" only if Q2, Q8 and Q10 hold;
  the mirror claim for VIEWS only if Q3, Q8 and Q10 hold.
- "Equivalent" if the 95% interval lies inside [-0.05, +0.05]; otherwise "inconclusive",
  with the episode table (which outbreaks each system saw, and which nobody saw) as the
  main finding.
- Every prediction is graded in a public RESULTS-2.md, including the failures.

## Known biases and limits, stated in advance

- Our outcome (>= 25 state-based deaths in a month) is VIEWS' scale, not Conflict
  Forecast's target (0.5 deaths per million, its own UCDP-based coding), which may favour
  VIEWS; Q9 checks this.
- H uses final GED 26.1 (see step 1); both systems only had earlier data.
- Bulk uploads may be honest re-uploads; we exclude them only because we cannot tell.
- About 23 origins and perhaps 5-20 outbreaks: intervals will be wide.
- Both systems change models over time; that is part of the real-time record.
