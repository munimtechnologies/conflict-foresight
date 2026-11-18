# conflict-foresight step 2 tests -- Apache-2.0
# Copyright 2026 Munim, Inc. Made by MV2 of Munim, Inc. with AI agents.
"""Offline tests for h2h.py (no network). Run: python -m unittest -v test_h2h"""
import datetime
import io
import unittest

import h2h  # imports (or, in a bare workdir, fetches) foresight.py first

fs = h2h.fs


class Vintages(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(h2h.parse_label("01-2022"), fs.parse_ym("2022-01"))
        self.assertEqual(h2h.parse_label("12-2024"), fs.parse_ym("2024-12"))
        self.assertIsNone(h2h.parse_label("latest"))
        self.assertIsNone(h2h.parse_label("13-2022"))
        self.assertEqual(h2h.label_str(fs.parse_ym("2024-06")), "06-2024")

    def test_deadline(self):
        utc = datetime.timezone.utc
        want = datetime.datetime(2024, 9, 14, tzinfo=utc).timestamp() * 1000.0
        self.assertEqual(h2h.rt_deadline_ms(fs.parse_ym("2024-06")), want)
        want = datetime.datetime(2025, 3, 17, tzinfo=utc).timestamp() * 1000.0
        self.assertEqual(h2h.rt_deadline_ms(fs.parse_ym("2024-12")), want)

    def test_provenance(self):
        utc = datetime.timezone.utc
        t = datetime.datetime(2024, 3, 6, 9, tzinfo=utc).timestamp() * 1000.0
        hour = 3600e3
        a, b, c, d = (fs.parse_ym(x) for x in ("2023-11", "2023-12", "2024-01", "2024-02"))
        dirs = {a: t, b: t + hour, c: t + 2 * hour, d: t + 19 * 24 * hour}
        batches = h2h.batch_labels(dirs)
        self.assertEqual(batches, {a, b, c})
        self.assertEqual(h2h.provenance(c, t, batches), "batch")
        self.assertEqual(h2h.provenance(d, dirs[d], batches), "realtime")
        late = h2h.rt_deadline_ms(d) + 1.0
        self.assertEqual(h2h.provenance(d, late, batches), "late")
        self.assertEqual(h2h.provenance(d, None, batches), "unknown")

    def test_pick_file(self):
        new = [{"name": "conflictforecast_ons_anyviolence_12.csv"},
               {"name": "conflictforecast_ons_armedconf_03.csv"},
               {"name": "conflictforecast_ons_armedconf_12.csv", "publicUrl": "u"},
               {"name": "conflictforecast_grid_risk.csv"}]
        self.assertEqual(h2h.pick_file(new)["publicUrl"], "u")
        old = {"data": [{"name": "conflictforecast_armedconf_3.csv"},
                        {"name": "conflictforecast_armedconf_12.csv"}]}
        self.assertEqual(h2h.pick_file(old)["name"], "conflictforecast_armedconf_12.csv")
        self.assertIsNone(h2h.pick_file([{"name": "conflictforecast_lnbest_pc_12.csv"}]))

    def test_read_cf(self):
        text = ("﻿isocode,year,month,text_model,best_model,ons_armedconf12\n"
                "AFG,2010,1,63.7,90.1,\n"
                "AFG,2021,12,60.0,88.0,\n"
                "AFG,2022,1,61.5,89.5,\n"
                "SDN,2022,1,5.0,,\n"
                "SEN,2022.0,1.0,.25,.5,0\n"
                "bad,row\n")
        lb = fs.parse_ym("2022-01")
        latest, keep, header = h2h.read_cf(io.StringIO(text), lb)
        self.assertEqual(latest, lb)
        self.assertEqual(header[0], "isocode")
        self.assertEqual(sorted(keep), [lb - 1, lb])
        self.assertEqual(keep[lb], {"AFG": (89.5, 61.5), "SEN": (0.5, 0.25)})
        self.assertEqual(h2h.choose_origin(lb, latest), lb)
        self.assertEqual(h2h.choose_origin(lb, lb - 1), lb - 1)
        self.assertIsNone(h2h.choose_origin(lb, lb + 1))
        self.assertIsNone(h2h.choose_origin(lb, None))
        with self.assertRaises(ValueError):
            h2h.read_cf(io.StringIO("iso,yr,mo\nAFG,2022,1\n"), lb)


class Panel(unittest.TestCase):
    def test_midpct(self):
        self.assertEqual(h2h.midpct([1, 2, 2, 3]), [0.25, 0.625, 0.625, 1.0])

    def test_thresholds(self):
        o = fs.parse_ym("2023-01")
        sb = {(1, o - 3): 4, (1, o + 2): 6}
        self.assertTrue(h2h.quiet_t(sb, 1, o, 24, 5))
        self.assertFalse(h2h.quiet_t(sb, 1, o, 24, 4))
        self.assertTrue(h2h.event_t(sb, 1, o, 12, 5))
        self.assertFalse(h2h.event_t(sb, 1, o, 1, 5))


def synthetic():
    """12 countries; countries 1-6 have one outbreak (100 deaths) at o0 + 5k + 10.
    VIEWS gives countries 1-6 a flat 2.0; Conflict Forecast gives 1.0 exactly when an
    outbreak falls in the next 12 months (a perfect forecaster), else 0.0."""
    o0 = fs.parse_ym("2021-12")
    onset = {k: o0 + 5 * k + 10 for k in range(1, 7)}
    sb = {(k, m): 100 for k, m in onset.items()}
    fcs, cf = {}, {}
    for o in range(o0, o0 + 41):
        fcs[o] = {(g, m): ((2.0, 0.5) if g <= 6 else (0.1, 0.01))
                  for g in range(1, 13) for m in range(o + 1, o + 37)}
        cf[o] = {}
        for g in range(1, 13):
            c = 1.0 if g in onset and o < onset[g] <= o + 12 else 0.0
            cf[o]["C%02d" % g] = (c, c / 2.0)
    names = {g: ("C%02d" % g, "Country %d" % g) for g in range(1, 13)}
    return o0, sb, fcs, cf, names


class Pipeline(unittest.TestCase):
    def test_analyze(self):
        o0, sb, fcs, cf, names = synthetic()
        units = list(range(1, 13))
        iso_of = {g: names[g][0] for g in units}
        pop_of = {g: 10e6 for g in units}
        origins_ = sorted(cf)
        res = h2h.analyze(sb, fcs, cf, origins_, origins_, units, iso_of, names, pop_of,
                          o0 + 52, reps=20, sec_reps=10)
        d = res["descriptive"]["primary_sample"]
        self.assertEqual(d["origins"], 41)
        self.assertEqual(d["positives"], 72)
        self.assertEqual(len(d["positive_countries"]), 6)
        self.assertFalse(res["underpowered"])
        pr = res["primary"]
        self.assertEqual(pr["auc_a"], 1.0)
        self.assertTrue(pr["auc_b"] < 1.0)
        self.assertTrue(pr["d_auc"] > 0)
        self.assertIn("d_auc_ci95", pr)
        self.assertEqual(pr["top_decile_capture_C"] > pr["top_decile_capture_H"], True)
        g = res["grades"]
        for k in ("Q1_auc_C_gt_V", "Q2_ci_above_0", "Q3_ci_below_0", "Q4_auc_C_gt_H",
                  "Q5_ensemble_beats_both", "Q6_text_only_below_best_model",
                  "Q7_neither_top_decile_lead3_ge_half", "Q8_same_sign_all_vintages",
                  "Q9_same_sign_per_capita_outcome", "Q10_no_loo_flip", "verdict"):
            self.assertIn(k, g)
        self.assertTrue(g["Q1_auc_C_gt_V"])
        self.assertTrue(g["Q8_same_sign_all_vintages"])
        self.assertTrue(g["Q9_same_sign_per_capita_outcome"])
        self.assertEqual(len(res["episodes"]), 6)
        for rec in res["episodes"]:
            for L in fs.LEADS:
                self.assertEqual(rec["leads"][str(L)]["status"], "ok")
        self.assertEqual(res["episode_summary"]["3"]["episodes"], 6)
        for k in ("C_vs_H", "V_vs_H", "E_vs_C", "E_vs_V", "Ct_vs_V", "Ct_vs_C",
                  "all_vintages|C_vs_V", "quiet12|C_vs_V", "quiet60|C_vs_V",
                  "per_capita_outcome|C_vs_V"):
            self.assertIn(k, res["secondary"])
        po = res["descriptive"]["per_origin_mean_auc"]
        self.assertEqual(po["C"]["mean_auc"], 1.0)

    def test_realtime_subset(self):
        o0, sb, fcs, cf, names = synthetic()
        units = list(range(1, 13))
        iso_of = {g: names[g][0] for g in units}
        rt = [o for o in sorted(cf) if o % 2 == 0]
        res = h2h.analyze(sb, fcs, cf, rt, sorted(cf), units, iso_of, names,
                          {g: 10e6 for g in units}, o0 + 52, reps=5, sec_reps=5)
        self.assertEqual(res["descriptive"]["primary_sample"]["origins"], len(rt))
        statuses = {rec["leads"][str(L)]["status"] for rec in res["episodes"]
                    for L in fs.LEADS}
        self.assertIn("no_realtime_pair", statuses)
        self.assertIn("ok", statuses)


if __name__ == "__main__":
    unittest.main()
