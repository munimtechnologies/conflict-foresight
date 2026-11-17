# conflict-foresight: pre-registration (frozen 2026-11-17, before any data was read)

MV2 of Munim, Inc. Made with AI agents. Text CC BY 4.0, code Apache-2.0.

## Question

Did the world's main public conflict early-warning system see new wars coming, in real
time, better than a rule anyone could compute from past deaths alone?

Since 2021, VIEWS (Uppsala University and PRIO) has published monthly forecasts of
state-based conflict deaths for every country, 1-36 months ahead. Every past run stays in
its public API (https://api.viewsforecasting.org/), so we can see exactly what was
forecast before each outbreak, with nothing fitted after the fact. Most of the skill of
conflict forecasts comes from the "conflict trap": countries at war tend to stay at war.
The part that matters most for prevention is the hard part: outbreaks in countries
with no recent war (Mueller and Rauh, "The Hard Problem of Prediction for Conflict
Prevention", JEEA 2022). We know of no published evaluation of VIEWS' archived real-time
forecasts on this hard part. Its prediction challenges score fatality counts over all
countries (Vesco et al. 2022; Hegre et al., JPR 2025), and its online evaluation tracks
fatalities against UCDP candidate data. We searched on 2026-11-17 and may have missed
something; we will correct this section if so.

**Wow if it works:** the first prospective scorecard of a public conflict early-warning
system on new outbreaks, built from its own archived forecasts (46 consecutive monthly
runs, Dec 2021 to Sep 2025). Either VIEWS measurably beats a two-number history rule on
new wars, which would be real evidence that early warning works where it matters, or it
does not, which tells funders and modellers where the effort must go.

## Data (all open, no keys)

- **Forecasts:** VIEWS country-month state-based runs, one per origin month o from
  2021-12 to 2026-09: `fatalities001_YYYY_MM_t01` (to 2023-03), `fatalities002_..._t01`
  (2023-04 to 2025-09), `fatalities003_..._t01` (from 2025-10). Run YYYY_MM uses data
  through that month; its first forecast month is o+1 (checked; runs failing this check
  are dropped). The "_00" and "_t02" runs are not used.
- **Outcomes:** UCDP GED 26.1 (to 2025-12) plus UCDP Candidate Events 26.01-26.09
  (2026-01 to 2026-09); state-based violence (type 1), `best` deaths, summed by
  `country_id` (Gleditsch-Ward code, the same code as VIEWS `gwcode`). If GED 26.1 ends
  before 2025-12, outcomes stop at its last month and candidate data is not used.
- **Sample:** VIEWS countries whose World Bank 2023 population is at least 1 million
  (secondary: all VIEWS countries).

## Definitions

- y(c, m) = 1 if country c has >= 25 state-based deaths in month m.
- **At risk (primary, "quiet24"):** no month with y = 1 in the 24 months up to and
  including the origin o. Secondary: quiet12, quiet60.
- **Outcome:** any month with y = 1 in o+1..o+12 (secondary windows: 6 and 3 months).
  Only origins whose whole window is observed are used. With data to 2026-09 the primary
  origins are 2021-12 to 2025-09 (46 runs, all fatalities001/002).
- **VIEWS score V:** the mean over the window months of the predicted log(1 + deaths)
  (`sc_cm_sb_main` in fatalities001, `main_mean_ln` later). The published probability of
  >= 25 deaths is not used as the primary score: it has a floor of 0.0026 in
  fatalities001/002 and is rounded to exactly 0 for 162 of 191 countries in
  fatalities003 (checked on run 2025_10). The secondary score Vd = 1 - prod(1 - p) uses it.
- **History rule H:** log(1 + state-based deaths in the 36 months up to o)
  + 0.1 x log(1 + deaths in the 120 months up to o). Fixed now, never tuned.
  Alternatives reported, not tested: log(1 + 120-month deaths), log(1 + 12-month deaths).

## Analysis

1. **Primary:** over all at-risk (country, origin) pairs, AUC(V) - AUC(H) (ties count
   one half). 95% interval from a country-cluster bootstrap (2,000 resamples of countries,
   seed 20261117). Also average precision and its difference, and top-decile capture: the
   share of positive pairs whose country was among the top 10% of that origin's at-risk
   countries (ties shared at random).
2. Same comparison for quiet12/quiet60 and 3-, 6- and 12-month windows (1,000 resamples);
   all VIEWS countries; H lagged one month (H through o-1, see bias below); Vd vs H;
   fatalities001 and fatalities002 origins separately.
3. **Easy set:** countries with a >= 25-death month in the last 24 months, V vs H,
   V vs log(1 + deaths in month o).
4. **Calibration:** VIEWS-implied expected number of positive at-risk pairs (sum of Vd)
   vs the observed number.
5. **Episode table:** every new outbreak (first month with y = 1 after 24 quiet months)
   from 2022-01 to the last outcome month, with the country's rank among at-risk
   countries by V and by H at origins 1, 3, 6 and 12 months before.
6. **Leave one country out:** the primary difference with each country that has a
   positive pair removed.

Stop rule: fewer than 30 origins or fewer than 5 countries with a positive pair means
the result is reported as descriptive only.

## Known bias, stated in advance

H is computed from the final GED 26.1 data. At the time VIEWS only had candidate data and
older GED versions. Revisions mostly add deaths to existing conflicts, so this favours H
a little. "VIEWS beats H" is therefore conservative; "H matches VIEWS" is weaker
evidence. The lagged-H check (2) shows how much one month of data matters.

## Predictions (fixed now, with our probabilities)

| # | Prediction | P |
|---|---|---|
| P1 | AUC(V) > 0.75 in the primary set | .75 |
| P2 | AUC(V) - AUC(H) > 0 | .55 |
| P3 | its 95% interval lies above 0 | .20 |
| P4 | its 95% interval lies below 0 | .10 |
| P5 | in the easy set, AUC(V) > AUC(H) | .70 |
| P6 | average precision of V < 0.25 in the primary set | .80 |
| P7 | fewer than half of outbreak episodes had V in the top decile 3 months before | .65 |
| P8 | VIEWS' implied number of outbreaks (sum of Vd) is below the observed number | .65 |
| P9 | the sign of the primary difference is the same for quiet12 and quiet60 | .60 |
| P10 | no single country flips the sign of the primary difference | .50 |
| P11 | lagging H by one month moves the difference by less than 0.02 | .70 |

## What we will claim

- "VIEWS adds real-time skill on new outbreaks beyond conflict history" only if P3, P9
  and P10 all hold.
- "The history rule beats VIEWS on new outbreaks" only if P4, P9 and P10 all hold.
- "Equivalent" if the 95% interval lies inside [-0.05, +0.05].
- Otherwise "inconclusive", with the episode table as the main finding.
- Every prediction is graded in a public RESULTS.md, including the ones that fail.
  `foresight.py` writes the grades automatically (`grades` in foresight_results.json).

## Limits

- One forecasting system, three model versions; the version changes are part of the
  real-time record and are reported separately, not hidden.
- New wars are rare; expect tens of positive pairs from perhaps 5-20 outbreaks, so
  intervals will be wide. Country-cluster resampling reflects this.
- The 25-death threshold and UCDP's coding (for example of gang or cartel violence)
  define what counts as an outbreak.
- 2026 outcomes are candidate data and can still be revised.
