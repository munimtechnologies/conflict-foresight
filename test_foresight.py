# conflict-foresight tests -- Apache-2.0
# Copyright 2026 Munim, Inc. Made by MV2 of Munim, Inc. with AI agents.
"""Offline tests for foresight.py (no network). Run: python -m unittest -v test_foresight"""
import math
import unittest

import foresight as fs


class Months(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(fs.parse_ym("2025-01"), 2025 * 12)
        self.assertEqual(fs.ym_str(fs.parse_ym("2023-04")), "2023-04")
        self.assertEqual(fs.parse_month("2025-01-20 00:00:00.000"), fs.parse_ym("2025-01"))
        with self.assertRaises(ValueError):
            fs.parse_month("")

    def test_run_names(self):
        p = fs.parse_ym
        self.assertEqual(fs.run_name(p("2021-12")), "fatalities001_2021_12_t01")
        self.assertEqual(fs.run_name(p("2023-03")), "fatalities001_2023_03_t01")
        self.assertEqual(fs.run_name(p("2023-04")), "fatalities002_2023_04_t01")
        self.assertEqual(fs.run_name(p("2025-09")), "fatalities002_2025_09_t01")
        self.assertEqual(fs.run_name(p("2025-10")), "fatalities003_2025_10_t01")
        self.assertEqual(fs.run_name(p("2026-09")), "fatalities003_2026_09_t01")
        self.assertEqual(len(fs.origins()), 58)


class Readers(unittest.TestCase):
    def test_read_views(self):
        d1 = {"data": [{"country_id": 1, "month_id": 505, "name": "Guyana", "gwcode": 110,
                        "isoab": "GUY", "year": 2022, "month": 1, "sc_cm_sb_main": 0.0066,
                        "sc_cm_sb_dich_main": 0.0026}]}
        d2 = {"data": [{"gwcode": 625, "isoab": "SDN", "name": "Sudan", "year": 2024,
                        "month": 7, "main_mean_ln": 5.9569, "main_dich": 0.9931,
                        "main_mean": 386.0},
                       {"gwcode": "x", "year": 2024, "month": 7, "main_mean_ln": 1.0}]}
        fc, names, info = fs.read_views([d1, d2])
        self.assertEqual(fc[(110, fs.ym_index(2022, 1))], (0.0066, 0.0026))
        self.assertEqual(fc[(625, fs.ym_index(2024, 7))], (5.9569, 0.9931))
        self.assertEqual(names[625], ("SDN", "Sudan"))
        self.assertEqual(info["bad_rows"], 1)
        self.assertEqual(info["first_month"], "2022-01")
        self.assertEqual(info["countries"], 2)
        self.assertEqual(info["dich_exact_zero_share"], 0.0)

    def test_count_sb(self):
        rows = [{"type_of_violence": "1", "date_start": "2025-01-03", "country_id": "625",
                 "best": "30"},
                {"type_of_violence": "2", "date_start": "2025-01-03", "country_id": "625",
                 "best": "99"},
                {"type_of_violence": "1", "date_start": "2025-01-20 00:00:00.000",
                 "country_id": "625", "best": "5"},
                {"type_of_violence": "1", "date_start": "2019-01-20", "country_id": "625",
                 "best": "7"},
                {"type_of_violence": "1", "date_start": "2025-02-01", "country_id": "",
                 "best": "7"}]
        sb, skipped = fs.count_sb(rows, fs.parse_ym("2020-01"), fs.parse_ym("2025-12"))
        self.assertEqual(sb[(625, fs.parse_ym("2025-01"))], 35)
        self.assertEqual(len(sb), 1)
        self.assertEqual(skipped["no_country_id"], 1)

    def test_wb_pop(self):
        doc = [{"page": 1}, [{"countryiso3code": "SDN", "value": 50000000},
                             {"countryiso3code": "", "value": 5},
                             {"countryiso3code": "TUV", "value": None}]]
        self.assertEqual(fs.read_wb_pop(doc), {"SDN": 50000000.0})


class Panel(unittest.TestCase):
    def setUp(self):
        self.o = fs.parse_ym("2023-01")
        self.sb = {(1, self.o - 5): 30, (1, self.o - 50): 10}

    def test_quiet_event(self):
        sb, o = self.sb, self.o
        self.assertFalse(fs.quiet(sb, 1, o, 24))
        self.assertTrue(fs.quiet(sb, 1, o, 5))
        self.assertFalse(fs.quiet(sb, 1, o, 6))
        self.assertTrue(fs.event(sb, 1, o - 6, 1))
        self.assertFalse(fs.event(sb, 1, o - 5, 12))

    def test_hist(self):
        want = math.log1p(30) + 0.1 * math.log1p(40)
        self.assertAlmostEqual(fs.hist_score(self.sb, 1, self.o), want)
        self.assertAlmostEqual(fs.hist_score(self.sb, 1, self.o, lag=1), want)
        self.assertEqual(fs.hist_score(self.sb, 2, self.o), 0.0)

    def test_views_scores(self):
        o = self.o
        fc = {(1, o + 1): (1.0, 0.5), (1, o + 2): (3.0, 0.5), (2, o + 1): (1.0, None),
              (2, o + 2): (1.0, 0.1)}
        self.assertAlmostEqual(fs.views_mean(fc, 1, o, 2), 2.0)
        self.assertAlmostEqual(fs.views_any(fc, 1, o, 2), 0.75)
        self.assertIsNone(fs.views_any(fc, 2, o, 2))
        self.assertIsNone(fs.views_mean(fc, 1, o, 3))


class Metrics(unittest.TestCase):
    def test_auc(self):
        y = [0, 0, 1, 1]
        one = [1.0] * 4
        self.assertEqual(fs.auc_g(fs.tie_groups([1, 2, 3, 4]), y, one), 1.0)
        self.assertEqual(fs.auc_g(fs.tie_groups([4, 3, 2, 1]), y, one), 0.0)
        self.assertEqual(fs.auc_g(fs.tie_groups([1, 1, 1, 1]), y, one), 0.5)
        g = fs.tie_groups([1, 2, 3])
        self.assertEqual(fs.auc_g(g, [0, 1, 0], [1.0, 1.0, 1.0]), 0.5)
        self.assertEqual(fs.auc_g(g, [0, 1, 0], [3.0, 1.0, 1.0]), 0.75)
        self.assertIsNone(fs.auc_g(g, [0, 0, 0], [1.0] * 3))

    def test_ap(self):
        g = fs.tie_groups([3, 2, 1])
        self.assertAlmostEqual(fs.ap_g(g, [1, 0, 1], [1.0] * 3), 5.0 / 6.0)
        self.assertAlmostEqual(fs.ap_g(fs.tie_groups([1, 2]), [0, 1], [1.0, 1.0]), 1.0)

    def test_rank_and_capture(self):
        vals = [5.0] * 4 + [1.0] * 16
        rank, credit = fs.rank_credit(vals, 5.0)
        self.assertEqual(rank, 2.5)
        self.assertEqual(credit, 0.5)
        self.assertEqual(fs.top_k(20), 2)
        self.assertEqual(fs.top_k(21), 3)
        self.assertEqual(fs.top_k(3), 1)
        pairs = [{"gw": i, "o": 1, "y": 1 if i == 0 else 0, "s": {"V": v}}
                 for i, v in enumerate(vals)]
        pairs += [{"gw": 100 + i, "o": 2, "y": 1 if i == 9 else 0, "s": {"V": float(i)}}
                  for i in range(10)]
        self.assertAlmostEqual(fs.top_capture(pairs, "V"), 0.75)

    def test_compare(self):
        pairs = [{"gw": g, "o": 1, "y": y, "s": {"A": a, "B": b}}
                 for g, y, a, b in [(1, 1, 3.0, 1.0), (2, 0, 1.0, 2.0), (3, 1, 2.0, 0.0),
                                    (4, 0, 0.0, 3.0)]]
        r = fs.compare(pairs, "A", "B", 50)
        self.assertEqual(r["auc_a"], 1.0)
        self.assertEqual(r["auc_b"], 0.0)
        self.assertEqual(r["d_auc"], 1.0)
        self.assertTrue(r["boot_valid"] > 0)
        self.assertEqual(r["d_auc_ci95"][1], 1.0)


def synthetic():
    """12 countries, origins 2021-12 + 0..40. Countries 1-6 each have one outbreak
    (100 deaths) at origin0 + 5k + 10; VIEWS gives them 2.0 and the others 0.1."""
    o0 = fs.parse_ym("2021-12")
    sb = {(k, o0 + 5 * k + 10): 100 for k in range(1, 7)}
    fcs = {}
    for o in range(o0, o0 + 41):
        fcs[o] = {(g, m): ((2.0, 0.5) if g <= 6 else (0.1, 0.01))
                  for g in range(1, 13) for m in range(o + 1, o + 37)}
    names = {g: ("C%02d" % g, "Country %d" % g) for g in range(1, 13)}
    return o0, sb, fcs, names


class Pipeline(unittest.TestCase):
    def test_analyze(self):
        o0, sb, fcs, names = synthetic()
        units = list(range(1, 13))
        res = fs.analyze(sb, fcs, units, units, o0 + 52, names, reps=20, sec_reps=10)
        d = res["descriptive"]["primary_sample"]
        self.assertEqual(d["origins"], 41)
        self.assertEqual(d["positives"], 72)
        self.assertEqual(len(d["positive_countries"]), 6)
        self.assertFalse(res["underpowered"])
        pr = res["primary"]
        self.assertTrue(0.5 < pr["auc_a"] < 1.0)
        self.assertTrue(pr["auc_b"] < 0.5)
        self.assertTrue(pr["d_auc"] > 0)
        self.assertIn("d_auc_ci95", pr)
        self.assertEqual(len(res["episodes"]), 6)
        for rec in res["episodes"]:
            for L in fs.LEADS:
                self.assertEqual(rec["leads"][str(L)]["status"], "ok")
        self.assertEqual(res["episode_summary"]["3"]["episodes"], 6)
        self.assertIn("verdict", res["grades"])
        self.assertTrue(res["grades"]["P2_d_auc_gt_0"])
        for key in ("quiet12|h12", "quiet60|h3", "easy|conflict24|h12",
                    "lagged_history|quiet24|h12", "dich|quiet24|h12",
                    "all_countries|quiet24|h12", "family_fatalities001|quiet24|h12"):
            self.assertIn(key, res["secondary"])
        cal = res["descriptive"]["calibration_quiet24_h12"]
        self.assertEqual(cal["observed_onset_pairs"], 72)

    def test_episodes(self):
        o0, sb, fcs, names = synthetic()
        eps = fs.find_episodes(sb, range(1, 13), o0 + 1, o0 + 52, 24)
        self.assertEqual(sorted(eps), [(k, o0 + 5 * k + 10) for k in range(1, 7)])


if __name__ == "__main__":
    unittest.main()
