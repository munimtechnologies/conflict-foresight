#!/usr/bin/env python3
# conflict-foresight / foresight.py -- Apache-2.0
# Copyright 2026 Munim, Inc. Made by MV2 of Munim, Inc. with AI agents.
"""Did real-time public conflict forecasts see new wars coming? (PREREGISTRATION.md)

VIEWS (Uppsala University and PRIO) publishes monthly country-month forecasts of
state-based conflict deaths and keeps every past run in its public API. Those archived
runs are what decision-makers could actually see at the time. This script scores them,
run by run, on the "hard problem" of conflict forecasting: new outbreaks in countries
with no month of 25 or more state-based deaths in the previous 24 months.

  V  VIEWS score = mean predicted log(1 + deaths) over the 12 months after the origin
     (main_mean_ln in fatalities002/003, sc_cm_sb_main in fatalities001)
  H  conflict-history rule = log(1 + deaths in the last 36 months)
                             + 0.1 * log(1 + deaths in the last 120 months)
  y  1 if any month in the 12 months after the origin has >= 25 state-based deaths
     (UCDP GED 26.1 to 2025-12, UCDP Candidate Events 2026-01..2026-09)

Primary test: AUC(V) - AUC(H) over every (country, origin) pair at risk, origins
2021-12..2025-09, with a country-cluster bootstrap. Also an episode table: for each new
outbreak, where VIEWS and the history rule ranked the country 1, 3, 6 and 12 months
before. Countries are matched by Gleditsch-Ward code (GED country_id = VIEWS gwcode).
Standard library only.

    python foresight.py --data-dir data --out-dir out
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import sys
import time
import urllib.request
import zipfile
from collections import Counter, defaultdict

__version__ = "0.1.0"

VIEWS_API = "https://api.viewsforecasting.org/"
GED_URL = "https://ucdp.uu.se/downloads/ged/ged261-csv.zip"
CAND_URL = "https://ucdp.uu.se/downloads/candidateged/GEDEvent_v26_01_26_09.csv"
WB_POP_URL = ("https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL"
              "?date=2023&format=json&per_page=20000")

THRESHOLD = 25                 # state-based deaths in a country-month
FIRST_ORIGIN = "2021-12"       # first continuous fatalities001 run
LAST_RUN = "2026-09"           # newest run used (forecasts from 2026-10)
OUTCOME_LAST = "2026-09"       # last month of candidate data used
GED_FIRST = "2011-01"          # history needed for 120-month windows
GED_LAST = "2025-12"
QUIET = (("quiet24", 24), ("quiet12", 12), ("quiet60", 60))
PRIMARY_QUIET = "quiet24"
HORIZONS = (12, 6, 3)
PRIMARY_H = 12
LEADS = (1, 3, 6, 12)
HIST_SHORT = 36
HIST_LONG = 120
LONG_WEIGHT = 0.1
TOP_DIV = 10                   # top decile = ceil(n / 10) countries
BOOT_REPS = 2000
SEC_REPS = 1000
SEED = 20261117
MIN_POP = 1_000_000
EQUIV = 0.05
MIN_ORIGINS = 30
MIN_POS_COUNTRIES = 5
MAX_LN = 20.0
LN_FIELDS = ("main_mean_ln", "sc_cm_sb_main")
DICH_FIELDS = ("main_dich", "sc_cm_sb_dich_main")


# ---------------------------------------------------------------- months
def ym_index(year, month):
    return year * 12 + (month - 1)


def ym_str(i):
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def parse_ym(s):
    """'2025-01' -> month index."""
    return ym_index(int(s[0:4]), int(s[5:7]))


def parse_month(s):
    """UCDP date_start ('2025-01-03', '2025-01-03 00:00:00.000') -> month index."""
    s = (s or "").strip()
    if len(s) < 7 or s[4] != "-":
        raise ValueError("bad date %r" % s)
    return ym_index(int(s[0:4]), int(s[5:7]))


def run_name(o):
    """Origin month (data through o; forecasts from o + 1) -> VIEWS run name.
    fatalities001 to 2023-03, fatalities002 2023-04..2025-09, fatalities003 after."""
    if o <= parse_ym("2023-03"):
        fam = "fatalities001"
    elif o <= parse_ym("2025-09"):
        fam = "fatalities002"
    else:
        fam = "fatalities003"
    return "%s_%04d_%02d_t01" % (fam, o // 12, o % 12 + 1)


def origins():
    return list(range(parse_ym(FIRST_ORIGIN), parse_ym(LAST_RUN) + 1))


# ---------------------------------------------------------------- data
def download(url, path, tries=4):
    """Download once; a unique partial name lets jobs share one data volume."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    part = "%s.part-%d-%d" % (path, os.getpid(), random.SystemRandom().randrange(1 << 30))
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "conflict-foresight/%s (MV2, Munim, Inc.; mv2@munimtech.com)"
                              % __version__})
            with urllib.request.urlopen(req, timeout=300) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(part, path)
            return path
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (k + 1))
    raise RuntimeError("download failed: %s (%s)" % (url, last))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _set_csv_limit():
    lim = sys.maxsize
    while True:
        try:
            csv.field_size_limit(lim)
            return
        except OverflowError:
            lim //= 10


def iter_csv_rows(fh):
    """Yield dicts with lower-case keys from a text file handle."""
    _set_csv_limit()
    rdr = csv.reader(fh)
    bom = chr(0xFEFF)
    header = [h.replace(bom, "").strip().lower() for h in next(rdr)]
    for row in rdr:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        yield dict(zip(header, row))


def open_csv_maybe_zip(path):
    if path.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError("no csv in %s" % path)
        return io.TextIOWrapper(z.open(names[0]), encoding="utf-8-sig", newline="")
    return open(path, encoding="utf-8-sig", newline="")


def views_url(run, page=None):
    url = "%s%s/cm/sb?pagesize=10000" % (VIEWS_API, run)
    return url if page is None else "%s&page=%d" % (url, page)


def fetch_views(run, data_dir, max_pages=50):
    """Download every page of a run's country-month state-based forecasts."""
    docs = []
    url = views_url(run)
    page = 1
    while url and page <= max_pages:
        path = os.path.join(data_dir, "views_%s_cm_sb_p%d.json" % (run, page))
        download(url, path)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        docs.append(doc)
        url = doc.get("next_page") or ""
        page += 1
    return docs


def _pick(rec, fields):
    for f in fields:
        v = rec.get(f)
        if v is None:
            continue
        try:
            return f, float(v)
        except (TypeError, ValueError):
            continue
    return None, None


def read_views(docs):
    """VIEWS API pages -> ({(gwcode, month): (log forecast, P(>=25) or None)},
    {gwcode: (iso3, name)}, info)."""
    fc, names = {}, {}
    used = Counter()
    bad = zeros = dn = 0
    for doc in docs:
        for rec in doc.get("data") or []:
            try:
                gw = int(rec["gwcode"])
                m = ym_index(int(rec["year"]), int(rec["month"]))
            except (KeyError, TypeError, ValueError):
                bad += 1
                continue
            f, ln = _pick(rec, LN_FIELDS)
            if ln is None:
                bad += 1
                continue
            df, p = _pick(rec, DICH_FIELDS)
            used[f] += 1
            if df:
                used[df] += 1
                dn += 1
                zeros += 1 if p == 0.0 else 0
            fc[(gw, m)] = (ln, p)
            names[gw] = ((rec.get("isoab") or "").strip().upper(), rec.get("name") or "")
    months = sorted({m for _, m in fc})
    info = {"fields": dict(used), "rows": len(fc), "bad_rows": bad,
            "first_month": ym_str(months[0]) if months else None,
            "last_month": ym_str(months[-1]) if months else None,
            "countries": len(names),
            "max_ln": max((v[0] for v in fc.values()), default=None),
            "dich_exact_zero_share": (zeros / float(dn)) if dn else None}
    return fc, names, info


def count_sb(rows, first, last, out=None, skipped=None):
    """UCDP GED / candidate rows -> {(gwcode, month): state-based deaths ('best')}."""
    out = out if out is not None else defaultdict(int)
    skipped = skipped if skipped is not None else Counter()
    for r in rows:
        if (r.get("type_of_violence") or "").strip() != "1":
            continue
        try:
            m = parse_month(r.get("date_start", ""))
        except ValueError:
            skipped["bad_date"] += 1
            continue
        if m < first or m > last:
            continue
        try:
            gw = int(float(r.get("country_id") or ""))
        except ValueError:
            skipped["no_country_id"] += 1
            continue
        try:
            best = int(float(r.get("best") or 0))
        except ValueError:
            best = 0
        out[(gw, m)] += best
    return out, skipped


def read_wb_pop(doc):
    pop = {}
    rows = doc[1] if isinstance(doc, list) and len(doc) > 1 and doc[1] else []
    for e in rows:
        iso = (e.get("countryiso3code") or "").strip().upper()
        v = e.get("value")
        if iso and v is not None:
            pop[iso] = float(v)
    return pop


# ---------------------------------------------------------------- panel
def window_sum(sb, gw, end, n):
    return sum(sb.get((gw, m), 0) for m in range(end - n + 1, end + 1))


def quiet(sb, gw, o, k):
    """No month with >= THRESHOLD deaths in the k months up to and including o."""
    return all(sb.get((gw, m), 0) < THRESHOLD for m in range(o - k + 1, o + 1))


def event(sb, gw, o, h):
    return any(sb.get((gw, m), 0) >= THRESHOLD for m in range(o + 1, o + h + 1))


def hist_score(sb, gw, o, lag=0):
    e = o - lag
    return (math.log1p(window_sum(sb, gw, e, HIST_SHORT))
            + LONG_WEIGHT * math.log1p(window_sum(sb, gw, e, HIST_LONG)))


def views_mean(fc, gw, o, h):
    vals = [fc.get((gw, m)) for m in range(o + 1, o + h + 1)]
    if any(v is None for v in vals):
        return None
    return sum(v[0] for v in vals) / float(h)


def views_any(fc, gw, o, h):
    """1 - prod(1 - P(>=25 in month)): VIEWS' implied chance of any such month."""
    q = 1.0
    for m in range(o + 1, o + h + 1):
        v = fc.get((gw, m))
        if v is None or v[1] is None:
            return None
        q *= 1.0 - min(max(v[1], 0.0), 1.0)
    return 1.0 - q


def build_pairs(sb, fcs, units, h, cover_last, risk):
    """Every (country, origin) with the outcome window observed and risk(gw, o) true."""
    pairs = []
    for o in sorted(fcs):
        if o + h > cover_last:
            continue
        fc = fcs[o]
        for gw in units:
            if not risk(gw, o):
                continue
            v = views_mean(fc, gw, o, h)
            if v is None:
                continue
            pairs.append({"gw": gw, "o": o, "y": 1 if event(sb, gw, o, h) else 0,
                          "s": {"V": v, "Vd": views_any(fc, gw, o, h),
                                "H": hist_score(sb, gw, o),
                                "H_lag1": hist_score(sb, gw, o, lag=1),
                                "H120": math.log1p(window_sum(sb, gw, o, HIST_LONG)),
                                "H12": math.log1p(window_sum(sb, gw, o, 12)),
                                "P0": math.log1p(sb.get((gw, o), 0))}})
    return pairs


# ---------------------------------------------------------------- metrics
def tie_groups(scores):
    """Indices grouped by equal score, in ascending score order."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    groups, cur, last = [], [], None
    for i in order:
        if cur and scores[i] != last:
            groups.append(cur)
            cur = []
        cur.append(i)
        last = scores[i]
    if cur:
        groups.append(cur)
    return groups


def auc_g(groups, y, w):
    """Weighted ROC AUC (ties count one half). None without both classes."""
    P = N = acc = neg_below = 0.0
    for g in groups:
        pg = sum(w[i] for i in g if y[i])
        ng = sum(w[i] for i in g if not y[i])
        acc += pg * neg_below + 0.5 * pg * ng
        neg_below += ng
        P += pg
        N += ng
    if P <= 0 or N <= 0:
        return None
    return acc / (P * N)


def ap_g(groups, y, w):
    """Weighted average precision; tied scores enter together."""
    P = sum(w[i] for i in range(len(y)) if y[i])
    if P <= 0:
        return None
    tp = fp = s = 0.0
    for g in reversed(groups):
        pg = sum(w[i] for i in g if y[i])
        ng = sum(w[i] for i in g if not y[i])
        tp += pg
        fp += ng
        if pg > 0:
            s += pg * tp / (tp + fp)
    return s / P


def _pct(v, q):
    s = sorted(v)
    return s[int(q * (len(s) - 1))]


def sign(x):
    return 0 if x == 0 else (1 if x > 0 else -1)


def score_auc(pairs, key):
    use = [p for p in pairs if p["s"][key] is not None]
    y = [p["y"] for p in use]
    return auc_g(tie_groups([p["s"][key] for p in use]), y, [1.0] * len(use))


def compare(pairs, a, b, reps, seed=SEED):
    """AUC and average precision of scores a and b on the same pairs; country-cluster
    bootstrap of the differences (a - b) when reps > 0."""
    use = [p for p in pairs if p["s"][a] is not None and p["s"][b] is not None]
    y = [p["y"] for p in use]
    ga = tie_groups([p["s"][a] for p in use])
    gb = tie_groups([p["s"][b] for p in use])
    ones = [1.0] * len(use)
    out = {"a": a, "b": b, "pairs": len(use), "positives": sum(y),
           "auc_a": auc_g(ga, y, ones), "auc_b": auc_g(gb, y, ones),
           "ap_a": ap_g(ga, y, ones), "ap_b": ap_g(gb, y, ones)}
    if out["auc_a"] is None or out["auc_b"] is None:
        out["error"] = "no positives or no negatives"
        return out
    out["d_auc"] = out["auc_a"] - out["auc_b"]
    out["d_ap"] = out["ap_a"] - out["ap_b"]
    if reps <= 0:
        return out
    countries = sorted({p["gw"] for p in use})
    rng = random.Random(seed)
    da, dp = [], []
    for _ in range(reps):
        mult = Counter(rng.choice(countries) for _ in countries)
        w = [float(mult.get(p["gw"], 0)) for p in use]
        a1, b1 = auc_g(ga, y, w), auc_g(gb, y, w)
        if a1 is None or b1 is None:
            continue
        da.append(a1 - b1)
        dp.append(ap_g(ga, y, w) - ap_g(gb, y, w))
    out["boot_reps"] = reps
    out["boot_valid"] = len(da)
    if da:
        out["d_auc_ci95"] = [_pct(da, 0.025), _pct(da, 0.975)]
        out["d_ap_ci95"] = [_pct(dp, 0.025), _pct(dp, 0.975)]
        le = sum(1 for d in da if d <= 0) / float(len(da))
        ge = sum(1 for d in da if d >= 0) / float(len(da))
        out["p_boot_two_sided"] = min(1.0, 2.0 * min(le, ge))
    return out


def top_k(n):
    return max(1, -(-n // TOP_DIV))


def rank_credit(vals, s):
    """Mid-rank of s among vals (1 = highest) and its expected top-decile membership
    under random tie-breaking."""
    gt = sum(1 for t in vals if t > s)
    eq = sum(1 for t in vals if t == s)
    k = top_k(len(vals))
    return gt + (eq + 1) / 2.0, min(1.0, max(0.0, (k - gt) / float(eq)))


def top_capture(pairs, key):
    """Share of positive pairs whose country was in the top decile of that origin's
    at-risk countries."""
    by_o = defaultdict(list)
    for p in pairs:
        if p["s"][key] is not None:
            by_o[p["o"]].append(p)
    credit, pos = 0.0, 0
    for o, ps in by_o.items():
        vals = [p["s"][key] for p in ps]
        for p in ps:
            if p["y"]:
                pos += 1
                credit += rank_credit(vals, p["s"][key])[1]
    return credit / pos if pos else None


# ---------------------------------------------------------------- analysis
def label(gw, names):
    iso, nm = names.get(gw, ("", ""))
    return "%s %s (%d)" % (iso or "?", nm or "?", gw)


def describe(pairs, names):
    pos = Counter(p["gw"] for p in pairs if p["y"])
    os_ = sorted({p["o"] for p in pairs})
    return {"pairs": len(pairs), "positives": sum(pos.values()), "origins": len(os_),
            "first_origin": ym_str(os_[0]) if os_ else None,
            "last_origin": ym_str(os_[-1]) if os_ else None,
            "countries": len({p["gw"] for p in pairs}),
            "positive_countries": {label(g, names): n for g, n in sorted(pos.items())}}


def loo(pairs, a, b, names):
    def d(ps):
        x, z = score_auc(ps, a), score_auc(ps, b)
        return None if x is None or z is None else x - z
    full = d(pairs)
    out = {"full": full, "by_country": {}, "flips": []}
    for gw in sorted({p["gw"] for p in pairs if p["y"]}):
        dq = d([p for p in pairs if p["gw"] != gw])
        out["by_country"][label(gw, names)] = dq
        if dq is not None and full is not None and sign(dq) != sign(full):
            out["flips"].append(label(gw, names))
    vals = [v for v in out["by_country"].values() if v is not None]
    out["min"] = min(vals) if vals else None
    out["max"] = max(vals) if vals else None
    return out


def find_episodes(sb, units, first, last, k):
    """(gw, m): month m has >= THRESHOLD deaths after k quiet months."""
    eps = []
    for gw in units:
        for m in range(first, last + 1):
            if sb.get((gw, m), 0) >= THRESHOLD and quiet(sb, gw, m - 1, k):
                eps.append((gw, m))
    return eps


def episode_table(sb, fcs, units, cover_last, names, k, first):
    cache = {}

    def at_risk(o):
        if o not in cache:
            rows = {}
            for gw in units:
                if not quiet(sb, gw, o, k):
                    continue
                v = views_mean(fcs[o], gw, o, PRIMARY_H)
                if v is None:
                    continue
                rows[gw] = {"V": v, "H": hist_score(sb, gw, o)}
            cache[o] = rows
        return cache[o]

    table = []
    for gw, m in find_episodes(sb, units, first, cover_last, k):
        rec = {"country": label(gw, names), "gw": gw, "onset_month": ym_str(m),
               "deaths_onset_month": sb.get((gw, m), 0),
               "deaths_first_3_months": sum(sb.get((gw, x), 0)
                                            for x in range(m, min(m + 2, cover_last) + 1)),
               "leads": {}}
        for L in LEADS:
            o = m - L
            if o not in fcs:
                rec["leads"][str(L)] = {"status": "no_run"}
                continue
            rows = at_risk(o)
            if gw not in rows:
                rec["leads"][str(L)] = {"status": "not_at_risk_or_no_forecast"}
                continue
            e = {"status": "ok", "origin": ym_str(o), "at_risk": len(rows)}
            for key in ("V", "H"):
                vals = [r[key] for r in rows.values()]
                rank, credit = rank_credit(vals, rows[gw][key])
                e[key + "_rank"] = rank
                e[key + "_top_decile_credit"] = credit
                e[key + "_score"] = rows[gw][key]
            rec["leads"][str(L)] = e
        table.append(rec)
    summary = {}
    for L in LEADS:
        ok = [r["leads"][str(L)] for r in table if r["leads"][str(L)]["status"] == "ok"]
        if not ok:
            summary[str(L)] = {"episodes": 0}
            continue
        summary[str(L)] = {
            "episodes": len(ok),
            "V_top_decile_share": sum(e["V_top_decile_credit"] for e in ok) / len(ok),
            "H_top_decile_share": sum(e["H_top_decile_credit"] for e in ok) / len(ok),
            "V_median_rank_pct": _pct([e["V_rank"] / e["at_risk"] for e in ok], 0.5),
            "H_median_rank_pct": _pct([e["H_rank"] / e["at_risk"] for e in ok], 0.5)}
    return table, summary


def grade(res):
    g = {}
    pr = res["primary"]
    sec = res["secondary"]
    if "d_auc" not in pr:
        return {"error": "primary comparison not available"}
    g["P1_auc_V_gt_0.75"] = pr["auc_a"] > 0.75
    g["P2_d_auc_gt_0"] = pr["d_auc"] > 0
    ci = pr.get("d_auc_ci95")
    g["P3_ci_above_0"] = bool(ci) and ci[0] > 0
    g["P4_ci_below_0"] = bool(ci) and ci[1] < 0
    e = sec.get("easy|conflict24|h12", {})
    g["P5_easy_auc_V_gt_H"] = ("d_auc" in e) and e["d_auc"] > 0
    g["P6_ap_V_lt_0.25"] = pr["ap_a"] < 0.25
    ep = res["episode_summary"].get("3", {})
    g["P7_lt_half_episodes_V_top_decile_lead3"] = (
        ep.get("episodes", 0) > 0 and ep["V_top_decile_share"] < 0.5)
    cal = res["descriptive"].get("calibration_quiet24_h12", {})
    g["P8_views_underpredicts_onsets"] = (
        cal.get("pairs", 0) > 0 and cal["expected_onset_pairs_from_views"]
        < cal["observed_onset_pairs"])
    s0 = sign(pr["d_auc"])
    q12 = sec.get("quiet12|h12", {})
    q60 = sec.get("quiet60|h12", {})
    g["P9_same_sign_quiet12_quiet60"] = ("d_auc" in q12 and "d_auc" in q60
                                         and sign(q12["d_auc"]) == s0
                                         and sign(q60["d_auc"]) == s0)
    g["P10_no_loo_flip"] = not pr["leave_one_out"]["flips"]
    lag = sec.get("lagged_history|quiet24|h12", {})
    g["P11_lagged_history_moves_d_auc_lt_0.02"] = (
        "d_auc" in lag and abs(lag["d_auc"] - pr["d_auc"]) < 0.02)
    if res.get("underpowered"):
        verdict = "underpowered (descriptive only)"
    elif g["P3_ci_above_0"] and g["P9_same_sign_quiet12_quiet60"] and g["P10_no_loo_flip"]:
        verdict = "VIEWS adds real-time skill on new outbreaks beyond conflict history"
    elif g["P4_ci_below_0"] and g["P9_same_sign_quiet12_quiet60"] and g["P10_no_loo_flip"]:
        verdict = "the conflict-history rule beats VIEWS on new outbreaks"
    elif ci and -EQUIV <= ci[0] and ci[1] <= EQUIV:
        verdict = "no difference: equivalent within %.2f AUC" % EQUIV
    else:
        verdict = "inconclusive"
    g["verdict"] = verdict
    return g


def analyze(sb, fcs, units, units_all, cover_last, names=None, reps=BOOT_REPS,
            sec_reps=SEC_REPS, episode_first=None):
    """sb: {(gw, month): state-based deaths}; fcs: {origin: read_views fc}."""
    names = names or {}
    res = {"primary": {}, "secondary": {}, "descriptive": {}}
    kq = dict(QUIET)[PRIMARY_QUIET]

    def risk(k):
        return lambda gw, o: quiet(sb, gw, o, k)

    P = build_pairs(sb, fcs, units, PRIMARY_H, cover_last, risk(kq))
    d = describe(P, names)
    res["descriptive"]["primary_sample"] = d
    res["underpowered"] = (d["origins"] < MIN_ORIGINS
                           or len(d["positive_countries"]) < MIN_POS_COUNTRIES)
    prim = compare(P, "V", "H", reps)
    prim["top_decile_capture_V"] = top_capture(P, "V")
    prim["top_decile_capture_H"] = top_capture(P, "H")
    prim["leave_one_out"] = loo(P, "V", "H", names)
    res["primary"] = prim

    sec = res["secondary"]
    for qname, k in QUIET:
        for h in HORIZONS:
            if qname == PRIMARY_QUIET and h == PRIMARY_H:
                continue
            Q = build_pairs(sb, fcs, units, h, cover_last, risk(k))
            r = compare(Q, "V", "H", sec_reps)
            r["top_decile_capture_V"] = top_capture(Q, "V")
            r["top_decile_capture_H"] = top_capture(Q, "H")
            r["positive_countries"] = len({p["gw"] for p in Q if p["y"]})
            sec["%s|h%d" % (qname, h)] = r
    A = build_pairs(sb, fcs, units_all, PRIMARY_H, cover_last, risk(kq))
    sec["all_countries|quiet24|h12"] = compare(A, "V", "H", sec_reps)
    sec["lagged_history|quiet24|h12"] = compare(P, "V", "H_lag1", sec_reps)
    sec["dich|quiet24|h12"] = compare(P, "Vd", "H", sec_reps)
    for alt in ("H120", "H12"):
        sec["V_vs_%s|quiet24|h12" % alt] = compare(P, "V", alt, 0)
    E = build_pairs(sb, fcs, units, PRIMARY_H, cover_last,
                    lambda gw, o: not quiet(sb, gw, o, kq))
    sec["easy|conflict24|h12"] = compare(E, "V", "H", sec_reps)
    sec["easy_persistence|conflict24|h12"] = compare(E, "V", "P0", 0)
    Z = build_pairs(sb, fcs, units, PRIMARY_H, cover_last, lambda gw, o: True)
    sec["all_pairs|h12"] = compare(Z, "V", "H", 0)
    fams = {"fatalities001": (parse_ym("2000-01"), parse_ym("2023-03")),
            "fatalities002": (parse_ym("2023-04"), parse_ym("2025-09"))}
    for fam, (lo, hi) in fams.items():
        sec["family_%s|quiet24|h12" % fam] = compare(
            [p for p in P if lo <= p["o"] <= hi], "V", "H", 0)

    vd = [p for p in P if p["s"]["Vd"] is not None]
    res["descriptive"]["calibration_quiet24_h12"] = {
        "pairs": len(vd),
        "expected_onset_pairs_from_views": sum(p["s"]["Vd"] for p in vd),
        "observed_onset_pairs": sum(p["y"] for p in vd)}
    first = episode_first if episode_first is not None else parse_ym("2022-01")
    res["episodes"], res["episode_summary"] = episode_table(
        sb, fcs, units, cover_last, names, kq, first)
    res["grades"] = grade(res)
    res["_pairs"] = P
    return res


# ---------------------------------------------------------------- main
def run(data_dir, out_dir, reps=BOOT_REPS, sec_reps=SEC_REPS):
    os.makedirs(out_dir, exist_ok=True)
    j = os.path.join
    log = {"version": __version__,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sources": {"ged": GED_URL, "candidate": CAND_URL, "wb_pop": WB_POP_URL,
                       "views_api": VIEWS_API}}
    paths = {"ged": download(GED_URL, j(data_dir, "ged261-csv.zip")),
             "cand": download(CAND_URL, j(data_dir, "GEDEvent_v26_01_26_09.csv")),
             "wb_pop": download(WB_POP_URL, j(data_dir, "wb_pop_2023.json"))}
    log["sha256"] = {k: sha256_file(p) for k, p in paths.items()}

    fcs, names, log["views"] = {}, {}, {}
    for o in origins():
        rn = run_name(o)
        try:
            fc, nm, info = read_views(fetch_views(rn, data_dir))
        except (RuntimeError, ValueError) as exc:
            log["views"][rn] = {"error": str(exc)}
            continue
        info["origin"] = ym_str(o)
        log["views"][rn] = info
        if not fc or info["max_ln"] is None or info["max_ln"] > MAX_LN:
            info["skipped"] = "no forecasts or not on the log scale"
            continue
        if info["first_month"] != ym_str(o + 1):
            info["skipped"] = "first forecast month is not origin + 1"
            continue
        fcs[o] = fc
        names.update(nm)
    log["runs_used"] = len(fcs)

    sb, skipped = count_sb(iter_csv_rows(open_csv_maybe_zip(paths["ged"])),
                           parse_ym(GED_FIRST), parse_ym(GED_LAST))
    ged_months = sorted({m for (_, m) in sb})
    ged_last = ged_months[-1] if ged_months else None
    log["ged_last_month"] = ym_str(ged_last) if ged_last is not None else None
    if ged_last is None:
        raise SystemExit("no state-based events read from GED 26.1")
    if ged_last < parse_ym(GED_LAST):
        cover_last = ged_last
        log["candidate_used"] = False
    else:
        n0 = len(sb)
        count_sb(iter_csv_rows(open_csv_maybe_zip(paths["cand"])), ged_last + 1,
                 parse_ym(OUTCOME_LAST), sb, skipped)
        cand_months = sorted({m for (_, m) in sb if m > ged_last})
        cover_last = cand_months[-1] if cand_months else ged_last
        log["candidate_used"] = len(sb) > n0
    log["outcome_last_month"] = ym_str(cover_last)
    log["ged_skipped"] = dict(skipped)

    with open(paths["wb_pop"], encoding="utf-8") as fh:
        pop = read_wb_pop(json.load(fh))
    units_all = sorted(names)
    units = [g for g in units_all if pop.get(names[g][0], 0.0) >= MIN_POP]
    log["units_all"] = len(units_all)
    log["units_pop_1m"] = len(units)
    log["views_without_wb_pop"] = [label(g, names) for g in units_all if names[g][0] not in pop]
    conflict_gw = {g for (g, m), v in sb.items() if v >= THRESHOLD and m >= parse_ym("2021-01")}
    log["ged_conflict_codes_not_in_views"] = sorted(conflict_gw - set(units_all))

    res = analyze(sb, fcs, units, units_all, cover_last, names, reps, sec_reps)
    pairs = res.pop("_pairs")
    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    res["meta"] = log
    with open(j(out_dir, "foresight_results.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    with open(j(out_dir, "pairs_quiet24_h12.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        keys = ["V", "Vd", "H", "H_lag1", "H120", "H12", "P0"]
        w.writerow(["gwcode", "iso3", "origin", "onset_next_12m"] + keys)
        for p in pairs:
            w.writerow([p["gw"], names.get(p["gw"], ("", ""))[0], ym_str(p["o"]), p["y"]]
                       + ["" if p["s"][k] is None else round(p["s"][k], 6) for k in keys])
    with open(j(out_dir, "episodes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        head = ["country", "onset_month", "deaths_onset_month", "deaths_first_3_months"]
        for L in LEADS:
            head += ["lead%d_V_rank" % L, "lead%d_H_rank" % L, "lead%d_at_risk" % L]
        w.writerow(head)
        for r in res["episodes"]:
            row = [r["country"], r["onset_month"], r["deaths_onset_month"],
                   r["deaths_first_3_months"]]
            for L in LEADS:
                e = r["leads"][str(L)]
                row += ([e["V_rank"], e["H_rank"], e["at_risk"]] if e["status"] == "ok"
                        else [e["status"], "", ""])
            w.writerow(row)

    lines = ["# conflict-foresight %s results" % __version__, "",
             "Verdict: %s" % res["grades"].get("verdict", res["grades"].get("error")), "",
             "## Primary (quiet24, 12-month window)", ""]
    for k in ("pairs", "positives", "auc_a", "auc_b", "d_auc", "d_auc_ci95",
              "p_boot_two_sided", "ap_a", "ap_b", "d_ap", "d_ap_ci95",
              "top_decile_capture_V", "top_decile_capture_H"):
        lines.append("- %s: %s" % (k, json.dumps(res["primary"].get(k))))
    lines.append("- leave_one_out flips: %s" % json.dumps(res["primary"]["leave_one_out"]["flips"]))
    lines += ["", "## Grades", ""]
    for k, v in sorted(res["grades"].items()):
        lines.append("- %s: %s" % (k, v))
    lines += ["", "## Secondary", ""]
    for k in sorted(res["secondary"]):
        r = res["secondary"][k]
        lines.append("- %s: AUC %s %s vs %s %s, d %s, CI %s, pairs %s, positives %s" % (
            k, r["a"], _f(r.get("auc_a")), r["b"], _f(r.get("auc_b")), _f(r.get("d_auc")),
            json.dumps(r.get("d_auc_ci95")), r["pairs"], r["positives"]))
    lines += ["", "## Episode summary", ""]
    for k, v in sorted(res["episode_summary"].items(), key=lambda kv: int(kv[0])):
        lines.append("- lead %s: %s" % (k, json.dumps(v, sort_keys=True)))
    lines += ["", "## Episodes", ""]
    for r in res["episodes"]:
        parts = []
        for L in LEADS:
            e = r["leads"][str(L)]
            parts.append("L%d %s" % (L, ("V %.1f/H %.1f of %d" % (e["V_rank"], e["H_rank"],
                                                               e["at_risk"]))
                                     if e["status"] == "ok" else e["status"]))
        lines.append("- %s %s (%d deaths): %s" % (r["onset_month"], r["country"],
                                                  r["deaths_onset_month"], "; ".join(parts)))
    lines += ["", "## Descriptive", ""]
    for k, v in sorted(res["descriptive"].items()):
        lines.append("- %s: %s" % (k, json.dumps(v, sort_keys=True)))
    with open(j(out_dir, "foresight_results.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return res


def _f(x):
    return "None" if x is None else "%.4f" % x


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
