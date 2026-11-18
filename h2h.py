#!/usr/bin/env python3
# conflict-foresight / h2h.py -- Apache-2.0
# Copyright 2026 Munim, Inc. Made by MV2 of Munim, Inc. with AI agents.
"""Step 2: VIEWS vs Conflict Forecast on new outbreaks, real-time vintages (PREREG-2.md).

Two public systems publish monthly country-level conflict forecasts and keep their past
releases online: VIEWS (Uppsala/PRIO, api.viewsforecasting.org, since Dec 2021) and
Conflict Forecast (Mueller, Rauh and Seimon, conflictforecast.org, since Jul 2021; its
12-month armed-conflict onset model uses news text). This script scores both on the same
"hard problem" (outbreaks in countries with no month of >= 25 state-based deaths in the
previous 24 months), using only Conflict Forecast vintages whose files were uploaded
within 75 days of the end of their month and not in a bulk upload.

  V  VIEWS: mean predicted log(1 + deaths), months o+1..o+12 (as in foresight.py)
  C  Conflict Forecast: `best_model` (news text + conflict history) at month o
  Ct Conflict Forecast: `text_model` (news text only)
  H  conflict-history rule of foresight.py
  E  ensemble: mean of the within-origin percentiles of V and C

Every score is turned into its percentile among the countries at risk at the same origin
(early Conflict Forecast vintages are scores, not probabilities), then AUCs are pooled.
Standard library only. Needs foresight.py (same repo) next to it.

    python h2h.py --data-dir data --out-dir out
"""
import argparse
import csv
import datetime
import json
import math
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from collections import defaultdict

FORESIGHT_URL = ("https://raw.githubusercontent.com/munimtechnologies/conflict-foresight/"
                 "main/foresight.py")
try:
    import foresight as fs
except ImportError:  # a compete workdir may hold only this step's files
    _here = os.path.dirname(os.path.abspath(__file__))
    try:
        urllib.request.urlretrieve(FORESIGHT_URL, os.path.join(_here, "foresight.py"))
    except OSError:
        _here = tempfile.mkdtemp(prefix="foresight-")
        urllib.request.urlretrieve(FORESIGHT_URL, os.path.join(_here, "foresight.py"))
    sys.path.insert(0, _here)
    import foresight as fs

__version__ = "0.2.0"

CF_API = ("https://api.backendless.com/C177D0DC-B3D5-818C-FF1E-1CC11BC69600/"
          "C5F2917E-C2F6-4F7D-9063-69555274134E/services/fileService/")
CF_DIRS = CF_API + "get-all-directories"
CF_LIST = CF_API + "get-file-listing?date=%s"

FIRST_ORIGIN = "2021-12"       # first VIEWS fatalities001 run
LAST_ORIGIN = "2025-09"        # last origin whose 12-month window ends by 2026-09
RT_DAYS = 75                   # upload within 75 days after the end of the label month
BATCH_HOURS = 24               # >= BATCH_MIN directories created within 24 h = bulk upload
BATCH_MIN = 3
QUIET_K = 24
H = 12
PC_RATE = 0.5                  # deaths per million per month (Conflict Forecast's line)
SEED = 20261118
BOOT_REPS = 2000
SEC_REPS = 1000
MIN_ORIGINS = 15
MIN_POS_COUNTRIES = 5
EQUIV = 0.05
SCORE_COLS = ("best_model", "prediction", "pred", "forecast", "probability")
TEXT_COLS = ("text_model",)
ISO_COLS = ("isocode", "iso3", "isoab", "iso")


# ---------------------------------------------------------------- vintages
def parse_label(name):
    """Conflict Forecast directory 'MM-YYYY' -> month index (None for 'latest' etc.)."""
    s = (name or "").strip()
    if len(s) != 7 or s[2] != "-":
        return None
    try:
        mm, yy = int(s[:2]), int(s[3:])
    except ValueError:
        return None
    if not 1 <= mm <= 12:
        return None
    return fs.ym_index(yy, mm)


def label_str(o):
    return "%02d-%04d" % (o % 12 + 1, o // 12)


def ms_iso(ms):
    if ms is None:
        return None
    t = datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def rt_deadline_ms(o):
    """End of label month o plus RT_DAYS days, in epoch milliseconds."""
    n = o + 1
    d = datetime.datetime(n // 12, n % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return (d + datetime.timedelta(days=RT_DAYS)).timestamp() * 1000.0


def batch_labels(dir_created):
    """{label: createdOn ms} of every directory -> set of labels in a bulk upload."""
    out = set()
    win = BATCH_HOURS * 3600e3
    for lb, t in dir_created.items():
        if sum(1 for t2 in dir_created.values() if abs(t2 - t) <= win) >= BATCH_MIN:
            out.add(lb)
    return out


def provenance(lb, file_created, batches):
    if lb in batches:
        return "batch"
    if file_created is None:
        return "unknown"
    if file_created > rt_deadline_ms(lb):
        return "late"
    return "realtime"


def as_list(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("data", "files", "items"):
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


def pick_file(listing):
    """The 12-month armed-conflict onset file of a vintage
    ('conflictforecast_armedconf_12.csv' before Oct 2024, '..._ons_armedconf_12.csv' after)."""
    hits = sorted((f for f in as_list(listing)
                   if isinstance(f, dict)
                   and (f.get("name") or "").lower().endswith("_12.csv")
                   and "armedconf" in (f.get("name") or "").lower()),
                  key=lambda f: f.get("name"))
    return hits[0] if hits else None


def _num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def read_cf(fh, label):
    """Vintage CSV (one row per country-month since 2010) -> (latest month with a score,
    {month: {iso3: (best, text)}} for months >= label - 1, header)."""
    fs._set_csv_limit()
    rdr = csv.reader(fh)
    header = [h.replace(chr(0xFEFF), "").strip().lower() for h in next(rdr)]
    idx = {h: i for i, h in enumerate(header)}

    def col(cands):
        for c in cands:
            if c in idx:
                return idx[c]
        return None
    ci, cs, ct = col(ISO_COLS), col(SCORE_COLS), col(TEXT_COLS)
    cy, cm = idx.get("year"), idx.get("month")
    if ci is None or cs is None or cy is None or cm is None:
        raise ValueError("missing columns; header: %s" % ",".join(header))
    need = max(ci, cs, cy, cm)
    latest, keep = None, defaultdict(dict)
    for row in rdr:
        if len(row) <= need:
            continue
        y, m = _num(row[cy]), _num(row[cm])
        s = _num(row[cs])
        if y is None or m is None or s is None or not 1 <= int(m) <= 12:
            continue
        mi = fs.ym_index(int(y), int(m))
        latest = mi if latest is None else max(latest, mi)
        if mi >= label - 1:
            t = _num(row[ct]) if ct is not None and ct < len(row) else None
            keep[mi][row[ci].strip().upper()] = (s, t)
    return latest, dict(keep), header


def choose_origin(label, latest):
    """Origin = latest month with scores; it must be the label month or the one before."""
    if latest is not None and label - 1 <= latest <= label:
        return latest
    return None


# ---------------------------------------------------------------- panel
def quiet_t(sb, gw, o, k, t):
    return all(sb.get((gw, m), 0) < t for m in range(o - k + 1, o + 1))


def event_t(sb, gw, o, h, t):
    return any(sb.get((gw, m), 0) >= t for m in range(o + 1, o + h + 1))


def midpct(vals):
    """Mid-rank percentile in (0, 1]; higher value -> higher percentile."""
    n = float(len(vals))
    out = []
    for v in vals:
        lt = sum(1 for x in vals if x < v)
        eq = sum(1 for x in vals if x == v)
        out.append((lt + (eq + 1) / 2.0) / n)
    return out


def build(sb, fcs, cf, units, iso_of, origins_, cover_last, thr, k=QUIET_K, h=H):
    """At-risk (country, origin) pairs scored by V, C, Ct, H (within-origin percentiles)
    and E. thr(gw) is the monthly death threshold that defines a conflict month."""
    pairs = []
    for o in sorted(origins_):
        if o + h > cover_last or o not in fcs or o not in cf:
            continue
        rows = []
        for gw in units:
            t = thr(gw)
            if not quiet_t(sb, gw, o, k, t):
                continue
            v = fs.views_mean(fcs[o], gw, o, h)
            c = cf[o].get(iso_of.get(gw, ""))
            if v is None or c is None:
                continue
            rows.append((gw, 1 if event_t(sb, gw, o, h, t) else 0,
                         {"V": v, "C": c[0], "Ct": c[1], "H": fs.hist_score(sb, gw, o)}))
        if not rows:
            continue
        pct = {}
        for key in ("V", "C", "Ct", "H"):
            vals = [r[2][key] for r in rows]
            pct[key] = ([None] * len(rows) if any(x is None for x in vals)
                        else midpct(vals))
        for i, (gw, y, raw) in enumerate(rows):
            s = {key: pct[key][i] for key in pct}
            s["E"] = (s["V"] + s["C"]) / 2.0
            pairs.append({"gw": gw, "o": o, "y": y, "s": s, "raw": raw})
    return pairs


def per_origin_auc(pairs, key):
    by_o = defaultdict(list)
    for p in pairs:
        if p["s"][key] is not None:
            by_o[p["o"]].append(p)
    vals = []
    for ps in by_o.values():
        a = fs.score_auc(ps, key)
        if a is not None:
            vals.append(a)
    return {"origins_with_both_classes": len(vals),
            "mean_auc": sum(vals) / len(vals) if vals else None}


def episodes(sb, fcs, cf, units, iso_of, names, rt_origins, cover_last, first):
    """Every new outbreak (>= 25 deaths after 24 quiet months) with V, C and H ranks among
    at-risk countries 1, 3, 6 and 12 months before, at real-time origins only."""
    cache = {}

    def rows_at(o):
        if o not in cache:
            rows = {}
            for gw in units:
                if not fs.quiet(sb, gw, o, QUIET_K):
                    continue
                v = fs.views_mean(fcs[o], gw, o, H)
                c = cf[o].get(iso_of.get(gw, ""))
                if v is None or c is None:
                    continue
                rows[gw] = {"V": v, "C": c[0], "H": fs.hist_score(sb, gw, o)}
            cache[o] = rows
        return cache[o]

    rt = set(o for o in rt_origins if o in fcs and o in cf)
    table = []
    for gw, m in fs.find_episodes(sb, units, first, cover_last, QUIET_K):
        rec = {"country": fs.label(gw, names), "gw": gw, "onset_month": fs.ym_str(m),
               "deaths_onset_month": sb.get((gw, m), 0), "leads": {}}
        for L in fs.LEADS:
            o = m - L
            if o not in rt:
                rec["leads"][str(L)] = {"status": "no_realtime_pair"}
                continue
            rows = rows_at(o)
            if gw not in rows:
                rec["leads"][str(L)] = {"status": "not_at_risk_or_no_forecast"}
                continue
            e = {"status": "ok", "origin": fs.ym_str(o), "at_risk": len(rows)}
            for key in ("V", "C", "H"):
                rank, credit = fs.rank_credit([r[key] for r in rows.values()], rows[gw][key])
                e[key + "_rank"] = rank
                e[key + "_top_decile_credit"] = credit
            e["neither_VC_top_decile"] = ((1.0 - e["V_top_decile_credit"])
                                          * (1.0 - e["C_top_decile_credit"]))
            rec["leads"][str(L)] = e
        table.append(rec)
    summary = {}
    for L in fs.LEADS:
        ok = [r["leads"][str(L)] for r in table if r["leads"][str(L)]["status"] == "ok"]
        if not ok:
            summary[str(L)] = {"episodes": 0}
            continue
        n = float(len(ok))
        summary[str(L)] = {"episodes": len(ok)}
        for key in ("V", "C", "H"):
            summary[str(L)][key + "_top_decile_share"] = sum(
                e[key + "_top_decile_credit"] for e in ok) / n
        summary[str(L)]["neither_VC_top_decile_share"] = sum(
            e["neither_VC_top_decile"] for e in ok) / n
    return table, summary


def grade(res):
    pr, sec = res["primary"], res["secondary"]
    if "d_auc" not in pr:
        return {"error": "primary comparison not available"}
    g = {}
    s0 = fs.sign(pr["d_auc"])
    ci = pr.get("d_auc_ci95")
    g["Q1_auc_C_gt_V"] = pr["d_auc"] > 0
    g["Q2_ci_above_0"] = bool(ci) and ci[0] > 0
    g["Q3_ci_below_0"] = bool(ci) and ci[1] < 0
    ch = sec.get("C_vs_H", {})
    g["Q4_auc_C_gt_H"] = "d_auc" in ch and ch["d_auc"] > 0
    ec, ev = sec.get("E_vs_C", {}), sec.get("E_vs_V", {})
    g["Q5_ensemble_beats_both"] = ("d_auc" in ec and "d_auc" in ev
                                   and ec["d_auc"] > 0 and ev["d_auc"] > 0)
    tc = sec.get("Ct_vs_C", {})
    g["Q6_text_only_below_best_model"] = "d_auc" in tc and tc["d_auc"] < 0
    ep = res["episode_summary"].get("3", {})
    g["Q7_neither_top_decile_lead3_ge_half"] = (
        ep.get("episodes", 0) > 0 and ep["neither_VC_top_decile_share"] >= 0.5)
    av = sec.get("all_vintages|C_vs_V", {})
    g["Q8_same_sign_all_vintages"] = "d_auc" in av and fs.sign(av["d_auc"]) == s0
    pc = sec.get("per_capita_outcome|C_vs_V", {})
    g["Q9_same_sign_per_capita_outcome"] = "d_auc" in pc and fs.sign(pc["d_auc"]) == s0
    g["Q10_no_loo_flip"] = not pr["leave_one_out"]["flips"]
    if res.get("underpowered"):
        v = "underpowered (descriptive only)"
    elif g["Q2_ci_above_0"] and g["Q8_same_sign_all_vintages"] and g["Q10_no_loo_flip"]:
        v = "Conflict Forecast saw new outbreaks coming better than VIEWS"
    elif g["Q3_ci_below_0"] and g["Q8_same_sign_all_vintages"] and g["Q10_no_loo_flip"]:
        v = "VIEWS saw new outbreaks coming better than Conflict Forecast"
    elif ci and -EQUIV <= ci[0] and ci[1] <= EQUIV:
        v = "no difference: equivalent within %.2f AUC" % EQUIV
    else:
        v = "inconclusive"
    g["verdict"] = v
    return g


def analyze(sb, fcs, cf, rt_origins, all_origins, units, iso_of, names, pop_of,
            cover_last, reps=BOOT_REPS, sec_reps=SEC_REPS, episode_first=None):
    res = {"primary": {}, "secondary": {}, "descriptive": {}}

    def thr25(gw):
        return fs.THRESHOLD

    def thr_pc(gw):
        return max(1.0, PC_RATE * pop_of.get(gw, 0.0) / 1e6)

    P = build(sb, fcs, cf, units, iso_of, rt_origins, cover_last, thr25)
    d = fs.describe(P, names)
    res["descriptive"]["primary_sample"] = d
    res["underpowered"] = (d["origins"] < MIN_ORIGINS
                           or len(d["positive_countries"]) < MIN_POS_COUNTRIES)
    pr = fs.compare(P, "C", "V", reps, SEED)
    for key in ("C", "V", "H", "E"):
        pr["top_decile_capture_" + key] = fs.top_capture(P, key)
    pr["leave_one_out"] = fs.loo(P, "C", "V", names)
    res["primary"] = pr

    sec = res["secondary"]
    sec["C_vs_H"] = fs.compare(P, "C", "H", sec_reps, SEED)
    sec["V_vs_H"] = fs.compare(P, "V", "H", sec_reps, SEED)
    sec["E_vs_C"] = fs.compare(P, "E", "C", sec_reps, SEED)
    sec["E_vs_V"] = fs.compare(P, "E", "V", sec_reps, SEED)
    sec["Ct_vs_V"] = fs.compare(P, "Ct", "V", sec_reps, SEED)
    sec["Ct_vs_C"] = fs.compare(P, "Ct", "C", 0)
    A = build(sb, fcs, cf, units, iso_of, all_origins, cover_last, thr25)
    sec["all_vintages|C_vs_V"] = fs.compare(A, "C", "V", sec_reps, SEED)
    for k in (12, 60):
        Q = build(sb, fcs, cf, units, iso_of, rt_origins, cover_last, thr25, k=k)
        sec["quiet%d|C_vs_V" % k] = fs.compare(Q, "C", "V", sec_reps, SEED)
    PC = build(sb, fcs, cf, units, iso_of, rt_origins, cover_last, thr_pc)
    sec["per_capita_outcome|C_vs_V"] = fs.compare(PC, "C", "V", sec_reps, SEED)
    sec["per_capita_outcome|C_vs_V"]["positive_countries"] = len(
        {p["gw"] for p in PC if p["y"]})
    res["descriptive"]["per_origin_mean_auc"] = {
        key: per_origin_auc(P, key) for key in ("C", "V", "H", "Ct", "E")}
    first = episode_first if episode_first is not None else fs.parse_ym("2022-01")
    res["episodes"], res["episode_summary"] = episodes(
        sb, fcs, cf, units, iso_of, names, rt_origins, cover_last, first)
    res["grades"] = grade(res)
    res["_pairs"] = P
    return res


# ---------------------------------------------------------------- main
def get_json(url, path):
    fs.download(url, path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_vintages(data_dir, log):
    """Download the Conflict Forecast directory list and each 12-month armed-conflict
    file for labels FIRST_ORIGIN..LAST_ORIGIN + 1. Big CSVs are read in a temporary
    folder and deleted; only the last two months of scores are cached (small JSON)."""
    cdir = os.path.join(data_dir, "conflictforecast")
    os.makedirs(cdir, exist_ok=True)
    dirs = as_list(get_json(CF_DIRS, os.path.join(cdir, "directories.json")))
    dir_created = {}
    for d in dirs:
        lb = parse_label(d.get("name"))
        if lb is not None and d.get("createdOn") is not None:
            dir_created[lb] = float(d["createdOn"])
    batches = batch_labels(dir_created)
    lo, hi = fs.parse_ym(FIRST_ORIGIN), fs.parse_ym(LAST_ORIGIN) + 1
    vint = {}
    for lb in sorted(x for x in dir_created if lo <= x <= hi):
        ls = label_str(lb)
        rec = {"label": ls, "dir_created": ms_iso(dir_created[lb])}
        log["vintages"][ls] = rec
        try:
            f = pick_file(get_json(CF_LIST % ls, os.path.join(cdir, "listing_%s.json" % ls)))
            if f is None:
                rec["error"] = "no armedconf 12-month file"
                continue
            rec["file"] = f.get("name")
            fc_ms = _num(f.get("createdOn"))
            rec["file_created"] = ms_iso(fc_ms)
            rec["provenance"] = provenance(lb, fc_ms, batches)
            cache = os.path.join(cdir, "scores_%s.json" % ls)
            if not os.path.exists(cache):
                tmp = tempfile.mkdtemp(prefix="cf-")
                try:
                    path = fs.download(f["publicUrl"], os.path.join(tmp, "v.csv"))
                    with open(path, encoding="utf-8-sig", newline="") as fh:
                        latest, keep, header = read_cf(fh, lb)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                with open(cache + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump({"latest": latest, "header": header,
                               "months": {str(m): rows for m, rows in keep.items()}}, fh)
                os.replace(cache + ".tmp", cache)
            with open(cache, encoding="utf-8") as fh:
                c = json.load(fh)
            latest = c["latest"]
            rec["latest_month"] = fs.ym_str(latest) if latest is not None else None
            rec["header"] = c["header"]
            o = choose_origin(lb, latest)
            if o is None:
                rec["error"] = "latest scored month is not the label month or the one before"
                continue
            rows = {iso: tuple(v) for iso, v in c["months"].get(str(o), {}).items()}
            rec["origin"] = fs.ym_str(o)
            rec["countries"] = len(rows)
            rec["text_model"] = any(v[1] is not None for v in rows.values())
            vint[lb] = {"origin": o, "prov": rec["provenance"], "created": fc_ms or 0.0,
                        "rows": rows}
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            rec["error"] = str(exc)[:500]
    # one vintage per origin: real-time first, then the earliest upload
    by_o = {}
    for lb, v in sorted(vint.items()):
        cur = by_o.get(v["origin"])
        key = (v["prov"] != "realtime", v["created"])
        if cur is None or key < (cur["prov"] != "realtime", cur["created"]):
            by_o[v["origin"]] = v
    return by_o


def run(data_dir, out_dir, reps=BOOT_REPS, sec_reps=SEC_REPS):
    os.makedirs(out_dir, exist_ok=True)
    j = os.path.join
    log = {"version": __version__, "foresight_version": fs.__version__,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sources": {"conflictforecast_api": CF_API, "views_api": fs.VIEWS_API,
                       "ged": fs.GED_URL, "candidate": fs.CAND_URL, "wb_pop": fs.WB_POP_URL},
           "vintages": {}, "views": {}}
    paths = {"ged": fs.download(fs.GED_URL, j(data_dir, "ged261-csv.zip")),
             "cand": fs.download(fs.CAND_URL, j(data_dir, "GEDEvent_v26_01_26_09.csv")),
             "wb_pop": fs.download(fs.WB_POP_URL, j(data_dir, "wb_pop_2023.json"))}
    log["sha256"] = {k: fs.sha256_file(p) for k, p in paths.items()}

    by_o = load_vintages(data_dir, log)
    lo, hi = fs.parse_ym(FIRST_ORIGIN), fs.parse_ym(LAST_ORIGIN)
    cf = {o: v["rows"] for o, v in by_o.items() if lo <= o <= hi}
    rt_origins = sorted(o for o in cf if by_o[o]["prov"] == "realtime")
    all_origins = sorted(cf)
    log["origins_realtime"] = [fs.ym_str(o) for o in rt_origins]
    log["origins_all"] = [fs.ym_str(o) for o in all_origins]

    fcs, names = {}, {}
    for o in all_origins:
        rn = fs.run_name(o)
        try:
            fc, nm, info = fs.read_views(fs.fetch_views(rn, data_dir))
        except (RuntimeError, ValueError) as exc:
            log["views"][rn] = {"error": str(exc)}
            continue
        log["views"][rn] = {"rows": info["rows"], "first_month": info["first_month"],
                            "max_ln": info["max_ln"]}
        if not fc or info["max_ln"] is None or info["max_ln"] > fs.MAX_LN:
            log["views"][rn]["skipped"] = "no forecasts or not on the log scale"
            continue
        if info["first_month"] != fs.ym_str(o + 1):
            log["views"][rn]["skipped"] = "first forecast month is not origin + 1"
            continue
        fcs[o] = fc
        names.update(nm)
    log["views_runs_used"] = len(fcs)

    sb, skipped = fs.count_sb(fs.iter_csv_rows(fs.open_csv_maybe_zip(paths["ged"])),
                              fs.parse_ym(fs.GED_FIRST), fs.parse_ym(fs.GED_LAST))
    ged_last = max((m for (_, m) in sb), default=None)
    if ged_last is None:
        raise SystemExit("no state-based events read from GED 26.1")
    log["ged_last_month"] = fs.ym_str(ged_last)
    cover_last = ged_last
    if ged_last >= fs.parse_ym(fs.GED_LAST):
        fs.count_sb(fs.iter_csv_rows(fs.open_csv_maybe_zip(paths["cand"])), ged_last + 1,
                    fs.parse_ym(fs.OUTCOME_LAST), sb, skipped)
        cover_last = max((m for (_, m) in sb), default=ged_last)
    log["outcome_last_month"] = fs.ym_str(cover_last)

    with open(paths["wb_pop"], encoding="utf-8") as fh:
        pop = fs.read_wb_pop(json.load(fh))
    iso_of = {g: names[g][0] for g in names}
    units = sorted(g for g in names if pop.get(iso_of[g], 0.0) >= fs.MIN_POP)
    pop_of = {g: pop.get(iso_of[g], 0.0) for g in units}
    cf_isos = set()
    for rows in cf.values():
        cf_isos.update(rows)
    log["units_pop_1m"] = len(units)
    log["views_units_not_in_conflictforecast"] = sorted(
        fs.label(g, names) for g in units if iso_of[g] not in cf_isos)
    log["conflictforecast_isos_not_in_views"] = sorted(cf_isos - set(iso_of.values()))

    res = analyze(sb, fcs, cf, rt_origins, all_origins, units, iso_of, names, pop_of,
                  cover_last, reps, sec_reps)
    pairs = res.pop("_pairs")
    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    res["meta"] = log
    with open(j(out_dir, "h2h_results.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    with open(j(out_dir, "h2h_pairs_quiet24_h12.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["gwcode", "iso3", "origin", "onset_next_12m", "V_raw", "C_raw", "Ct_raw",
                    "H_raw", "V_pct", "C_pct", "Ct_pct", "H_pct", "E"])
        for p in pairs:
            r, s = p["raw"], p["s"]
            w.writerow([p["gw"], iso_of.get(p["gw"], ""), fs.ym_str(p["o"]), p["y"]]
                       + ["" if r[k] is None else round(r[k], 6) for k in ("V", "C", "Ct", "H")]
                       + ["" if s[k] is None else round(s[k], 6)
                          for k in ("V", "C", "Ct", "H", "E")])
    with open(j(out_dir, "h2h_episodes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        head = ["country", "onset_month", "deaths_onset_month"]
        for L in fs.LEADS:
            head += ["lead%d_%s" % (L, x) for x in ("V_rank", "C_rank", "H_rank", "at_risk")]
        w.writerow(head)
        for r in res["episodes"]:
            row = [r["country"], r["onset_month"], r["deaths_onset_month"]]
            for L in fs.LEADS:
                e = r["leads"][str(L)]
                row += ([e["V_rank"], e["C_rank"], e["H_rank"], e["at_risk"]]
                        if e["status"] == "ok" else [e["status"], "", "", ""])
            w.writerow(row)

    lines = ["# conflict-foresight step 2 (h2h %s) results" % __version__, "",
             "Verdict: %s" % res["grades"].get("verdict", res["grades"].get("error")), "",
             "Real-time origins: %s" % ", ".join(log["origins_realtime"]), "",
             "## Primary: Conflict Forecast (C) vs VIEWS (V), quiet24, 12 months", ""]
    for k in ("pairs", "positives", "auc_a", "auc_b", "d_auc", "d_auc_ci95",
              "p_boot_two_sided", "ap_a", "ap_b", "d_ap", "d_ap_ci95", "top_decile_capture_C",
              "top_decile_capture_V", "top_decile_capture_H", "top_decile_capture_E"):
        lines.append("- %s: %s" % (k, json.dumps(res["primary"].get(k))))
    lines.append("- leave_one_out flips: %s" % json.dumps(res["primary"]["leave_one_out"]["flips"]))
    lines += ["", "## Grades", ""]
    for k, v in sorted(res["grades"].items()):
        lines.append("- %s: %s" % (k, v))
    lines += ["", "## Secondary", ""]
    for k in sorted(res["secondary"]):
        r = res["secondary"][k]
        lines.append("- %s: AUC %s %s vs %s %s, d %s, CI %s, pairs %s, positives %s" % (
            k, r["a"], fs._f(r.get("auc_a")), r["b"], fs._f(r.get("auc_b")),
            fs._f(r.get("d_auc")), json.dumps(r.get("d_auc_ci95")), r["pairs"], r["positives"]))
    lines += ["", "## Episode summary", ""]
    for k, v in sorted(res["episode_summary"].items(), key=lambda kv: int(kv[0])):
        lines.append("- lead %s: %s" % (k, json.dumps(v, sort_keys=True)))
    lines += ["", "## Episodes (rank among at-risk countries; 1 = highest risk)", ""]
    for r in res["episodes"]:
        parts = []
        for L in fs.LEADS:
            e = r["leads"][str(L)]
            parts.append("L%d %s" % (L, ("V %.1f/C %.1f/H %.1f of %d" % (
                e["V_rank"], e["C_rank"], e["H_rank"], e["at_risk"]))
                if e["status"] == "ok" else e["status"]))
        lines.append("- %s %s (%d deaths): %s" % (r["onset_month"], r["country"],
                                                  r["deaths_onset_month"], "; ".join(parts)))
    lines += ["", "## Descriptive", ""]
    for k, v in sorted(res["descriptive"].items()):
        lines.append("- %s: %s" % (k, json.dumps(v, sort_keys=True)))
    with open(j(out_dir, "h2h_results.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--reps", type=int, default=BOOT_REPS)
    ap.add_argument("--sec-reps", type=int, default=SEC_REPS)
    a = ap.parse_args(argv)
    run(a.data_dir, a.out_dir, a.reps, a.sec_reps)


if __name__ == "__main__":
    main()
