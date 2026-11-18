# conflict-foresight

**Did real-time public conflict forecasts see new wars coming?**

An open, pre-registered test by MV2 of Munim, Inc. (made with AI agents). It scores the
archived monthly forecasts of the public conflict early-warning systems on the hardest and
most useful task: outbreaks of state-based violence in countries with no recent war. It
uses only what each system published before each outbreak.

- **Step 1:** VIEWS (Uppsala University and PRIO) against a two-number rule computed from
  past deaths alone. Design and 11 predictions: [PREREGISTRATION.md](PREREGISTRATION.md)
  (frozen 2026-11-17). Code: [foresight.py](foresight.py), tests:
  [test_foresight.py](test_foresight.py).
- **Step 2:** VIEWS against Conflict Forecast (Mueller, Rauh and Seimon; conflictforecast.org),
  whose news-text model was built for exactly this problem, using only Conflict Forecast
  vintages uploaded within 75 days of their month and not in a bulk upload. Design and 10
  predictions: [PREREG-2.md](PREREG-2.md) (frozen 2026-11-18). Code: [h2h.py](h2h.py),
  tests: [test_h2h.py](test_h2h.py).
- Data: VIEWS API (https://api.viewsforecasting.org/), Conflict Forecast file API
  (https://conflictforecast.org/downloads), UCDP GED 26.1 and UCDP Candidate Events
  26.01-26.09 (https://ucdp.uu.se/downloads/), World Bank population. All open.

```
python -m unittest -v test_foresight test_h2h
python foresight.py --data-dir data --out-dir out
python h2h.py --data-dir data --out-dir out
```

Outputs: `foresight_results.json` / `h2h_results.json` (all numbers and automatic grades
of every prediction), Markdown summaries, every scored country-origin pair, and episode
tables (every new outbreak, with where each system ranked it 1, 3, 6 and 12 months before).

## Status

- 2026-11-17: step 1 pre-registered; first run queued. No result yet.
- 2026-11-18: step 2 pre-registered; run queued. No result yet.

## Credits and licence

VIEWS forecasts: VIEWS team, Uppsala University and PRIO. Conflict Forecast: Hannes Mueller,
Christopher Rauh and Ben Seimon (please cite Mueller and Rauh, JEEA 2022, and Mueller,
Rauh and Seimon, Data & Policy 2024). UCDP data: Uppsala Conflict Data Program (Sundberg
and Melander 2013; Davies et al.). This project is not affiliated with any of them. Code
Apache-2.0; text and result tables CC BY 4.0. Contact: MV2, Munim, Inc., mv2@munimtech.com
