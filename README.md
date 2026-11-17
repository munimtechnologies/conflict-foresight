# conflict-foresight

**Did real-time public conflict forecasts see new wars coming?**

An open, pre-registered test by MV2 of Munim, Inc. (made with AI agents). It scores the
archived monthly forecasts of VIEWS, the public conflict early-warning system run by
Uppsala University and PRIO, on the hardest and most useful task: outbreaks of
state-based violence in countries with no recent war. It compares VIEWS with a two-number
rule computed from past deaths alone, using only what was published before each outbreak.

- Design, definitions and 11 predictions with probabilities: [PREREGISTRATION.md](PREREGISTRATION.md)
  (frozen 2026-11-17, before any data was read)
- Code: [foresight.py](foresight.py) (Python standard library only), tests:
  [test_foresight.py](test_foresight.py)
- Data: VIEWS API (https://api.viewsforecasting.org/), UCDP GED 26.1 and UCDP Candidate
  Events 26.01-26.09 (https://ucdp.uu.se/downloads/), World Bank population. All open.

```
python -m unittest -v test_foresight
python foresight.py --data-dir data --out-dir out
```

Outputs: `foresight_results.json` (all numbers, automatic grades of every prediction),
`foresight_results.md`, `pairs_quiet24_h12.csv` (every scored country-origin pair) and
`episodes.csv` (every new outbreak, with where VIEWS and the history rule ranked it
1, 3, 6 and 12 months before).

## Status

- 2026-11-17: pre-registered; first run queued. No result yet.

## Credits and licence

VIEWS forecasts: ViEWS/VIEWS team, Uppsala University and PRIO. UCDP data: Uppsala
Conflict Data Program (Sundberg and Melander 2013; Davies et al.). This project is not
affiliated with either. Code Apache-2.0; text and result tables CC BY 4.0.
Contact: MV2, Munim, Inc., mv2@munimtech.com
