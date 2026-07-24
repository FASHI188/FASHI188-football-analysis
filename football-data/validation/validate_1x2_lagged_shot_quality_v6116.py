#!/usr/bin/env python3
"""Research-only cross-season test of lagged shot / shot-on-target / corner features.

All event-stat features are computed strictly from matches BEFORE the target match.
Current-match shots/corners are used only after prediction to update rolling state.
The goal is to test whether attack-process information adds 1X2 Top-1 accuracy beyond
legacy devigged closing 1X2 market probabilities.

Historical prices remain retrospective reference only; no formal probability or
CURRENT rule is modified.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import canonical_team_name, load_aliases, load_registry, parse_match_date

OUT = ROOT / "manifests" / "v6_1x2_lagged_shot_quality_v6116_status.json"
DIRECTIONS = ("home", "draw", "away")
CALENDAR_YEAR_DOMAINS = {
    "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1", "KOR_KLeague1",
    "BRA_SerieA", "ARG_Primera", "USA_MLS",
}
ODDS_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
    ("PSH", "PSD", "PSA", "Pinnacle"),
    ("B365H", "B365D", "B365A", "Bet365"),
    ("AvgH", "AvgD", "AvgA", "Average"),
)
STAT_COLS = ("HS", "AS", "HST", "AST", "HC", "AC")


def _num(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _odds(value: Any) -> float | None:
    x = _num(value)
    return x if x is not None and x > 1.0 else None


def _extract_market(raw: dict[str, str]):
    for hc, dc, ac, label in ODDS_TRIPLETS:
        h, d, a = _odds(raw.get(hc)), _odds(raw.get(dc)), _odds(raw.get(ac))
        if h is None or d is None or a is None:
            continue
        q = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
        s = sum(q.values())
        return {k: q[k] / s for k in DIRECTIONS}, label
    return None


def _actual(raw: dict[str, str]):
    try:
        h = int(float(raw.get("FTHG", "")))
        a = int(float(raw.get("FTAG", "")))
    except (TypeError, ValueError):
        return None
    return "home" if h > a else "away" if h < a else "draw"


def _target_seasons(cid: str):
    return ("2024", "2025") if cid in CALENDAR_YEAR_DOMAINS else ("2024/25", "2025/26")


def _mean(items, key):
    vals = [float(x[key]) for x in items if x.get(key) is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _profile(history, n):
    xs = list(history)[-n:]
    return {
        "sf": _mean(xs, "sf"),
        "sa": _mean(xs, "sa"),
        "sotf": _mean(xs, "sotf"),
        "sota": _mean(xs, "sota"),
        "cf": _mean(xs, "cf"),
        "ca": _mean(xs, "ca"),
    }


def _raw_matches():
    aliases = load_aliases()
    all_rows = []
    seen = set()
    provider_counts = Counter()
    stats_available = Counter()
    for item in load_registry()["competitions"]:
        cid = str(item["competition_id"])
        older, newer = _target_seasons(cid)
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw0 in csv.DictReader(handle):
                    raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in {older, newer} or not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):
                        continue
                    market = _extract_market(raw)
                    y = _actual(raw)
                    statvals = {k: _num(raw.get(k)) for k in STAT_COLS}
                    if market is None or y is None or any(statvals[k] is None for k in STAT_COLS):
                        continue
                    try:
                        dt = parse_match_date(raw["Date"], season)
                    except Exception:
                        continue
                    home = canonical_team_name(cid, raw["HomeTeam"], aliases)
                    away = canonical_team_name(cid, raw["AwayTeam"], aliases)
                    key = (cid, season, dt.date().isoformat(), home, away)
                    if key in seen:
                        continue
                    seen.add(key)
                    p, provider = market
                    all_rows.append({
                        "competition_id": cid, "season": season,
                        "bucket": "older" if season == older else "newer",
                        "date": dt.isoformat(), "home_team": home, "away_team": away,
                        "actual": y, "market": p, "provider": provider,
                        **statvals,
                    })
                    provider_counts[provider] += 1
                    stats_available[cid] += 1
    all_rows.sort(key=lambda r: (r["competition_id"], r["season"], r["date"], r["home_team"], r["away_team"]))
    return all_rows, dict(provider_counts), dict(stats_available)


def _build_lagged(raw_rows):
    by_group = defaultdict(list)
    for r in raw_rows:
        by_group[(r["competition_id"], r["season"])].append(r)
    out = []
    for (cid, season), matches in by_group.items():
        matches.sort(key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        hist = defaultdict(lambda: deque(maxlen=10))
        for r in matches:
            hh, ah = hist[r["home_team"]], hist[r["away_team"]]
            if len(hh) >= 3 and len(ah) >= 3:
                h5, a5 = _profile(hh, 5), _profile(ah, 5)
                h10, a10 = _profile(hh, 10), _profile(ah, 10)
                p = r["market"]
                order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
                feat = {
                    "market_home": p["home"], "market_draw": p["draw"], "market_away": p["away"],
                    "market_max": p[order[0]], "market_margin": p[order[0]] - p[order[1]],
                    "h_sf5": h5["sf"], "h_sa5": h5["sa"], "h_sotf5": h5["sotf"], "h_sota5": h5["sota"], "h_cf5": h5["cf"], "h_ca5": h5["ca"],
                    "a_sf5": a5["sf"], "a_sa5": a5["sa"], "a_sotf5": a5["sotf"], "a_sota5": a5["sota"], "a_cf5": a5["cf"], "a_ca5": a5["ca"],
                    "h_sf10": h10["sf"], "h_sa10": h10["sa"], "h_sotf10": h10["sotf"], "h_sota10": h10["sota"],
                    "a_sf10": a10["sf"], "a_sa10": a10["sa"], "a_sotf10": a10["sotf"], "a_sota10": a10["sota"],
                    "sot_balance5": (h5["sotf"] - h5["sota"]) - (a5["sotf"] - a5["sota"]),
                    "shot_balance5": (h5["sf"] - h5["sa"]) - (a5["sf"] - a5["sa"]),
                    "corner_balance5": (h5["cf"] - h5["ca"]) - (a5["cf"] - a5["ca"]),
                    "h_sot_rate5": h5["sotf"] / max(h5["sf"], 1e-6),
                    "a_sot_rate5": a5["sotf"] / max(a5["sf"], 1e-6),
                }
                out.append({
                    "competition_id": cid, "season": season, "bucket": r["bucket"], "date": r["date"],
                    "actual": r["actual"], "market": p, "features": feat,
                })
            # Update only AFTER prediction features are formed.
            hh.append({"sf": r["HS"], "sa": r["AS"], "sotf": r["HST"], "sota": r["AST"], "cf": r["HC"], "ca": r["AC"]})
            ah.append({"sf": r["AS"], "sa": r["HS"], "sotf": r["AST"], "sota": r["HST"], "cf": r["AC"], "ca": r["HC"]})
    out.sort(key=lambda r: (r["competition_id"], r["date"]))
    return out


def _feature_names(rows):
    names = sorted(rows[0]["features"].keys())
    comps = sorted({r["competition_id"] for r in rows})
    return names, comps


def _x(r, names, comps):
    vals = [float(r["features"][n]) for n in names]
    vals.extend(1.0 if r["competition_id"] == c else 0.0 for c in comps)
    return vals


def _split_old(rows):
    g = defaultdict(list)
    for r in rows:
        if r["bucket"] == "older": g[r["competition_id"]].append(r)
    train, val = [], []
    for cid, items in g.items():
        items.sort(key=lambda r: r["date"])
        if len(items) < 30: continue
        cut = max(1, min(len(items)-1, int(.8*len(items))))
        train += items[:cut]; val += items[cut:]
    return train, val


def _market_metrics(rows):
    hits = 0
    for r in rows:
        z = max(DIRECTIONS, key=lambda k: r["market"][k])
        hits += int(z == r["actual"])
    return {"count": len(rows), "hits": hits, "accuracy": hits/len(rows) if rows else None}


def _fit(train, names, comps, family, param):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    X = [_x(r,names,comps) for r in train]; y=[r["actual"] for r in train]
    if family == "logreg":
        model = make_pipeline(StandardScaler(), LogisticRegression(C=param, max_iter=3000, solver="lbfgs"))
    else:
        model = HistGradientBoostingClassifier(learning_rate=0.03, max_leaf_nodes=int(param), l2_regularization=10.0, max_iter=250, random_state=20260724)
    model.fit(X,y); return model


def _metrics(rows, model, names, comps):
    classes=list(model.classes_); idx={c:i for i,c in enumerate(classes)}; X=[_x(r,names,comps) for r in rows]; arr=model.predict_proba(X)
    hits=0; brier=0.0; logloss=0.0
    for r,a in zip(rows,arr):
        p={k:float(a[idx[k]]) for k in DIRECTIONS}; z=max(DIRECTIONS,key=lambda k:p[k]); hits+=int(z==r["actual"])
        brier += sum((p[k]-(1.0 if k==r["actual"] else 0.0))**2 for k in DIRECTIONS)
        logloss += -math.log(max(p[r["actual"]],1e-15))
    n=len(rows); return {"count":n,"hits":hits,"accuracy":hits/n if n else None,"brier":brier/n if n else None,"log_loss":logloss/n if n else None}


def main():
    raw, providers, stats_counts = _raw_matches(); rows=_build_lagged(raw)
    old=[r for r in rows if r["bucket"]=="older"]; new=[r for r in rows if r["bucket"]=="newer"]
    if len(old)<500 or len(new)<500: raise RuntimeError(f"insufficient lagged shot rows old={len(old)} new={len(new)} stats={stats_counts}")
    train,val=_split_old(rows); names,comps=_feature_names(old)
    leaderboard=[]
    for family, params in (("logreg",(0.01,0.03,0.1,0.3,1.0)),("hgb",(5,9,15))):
        for param in params:
            model=_fit(train,names,comps,family,param); m=_metrics(val,model,names,comps); leaderboard.append({"family":family,"param":param,**m})
    leaderboard.sort(key=lambda x:(-x["accuracy"],x["brier"],x["log_loss"]))
    best=leaderboard[0]
    final=_fit(old,names,comps,best["family"],best["param"])
    market=_market_metrics(new); candidate=_metrics(new,final,names,comps)
    bycomp={}
    for cid in sorted({r["competition_id"] for r in new}):
        sub=[r for r in new if r["competition_id"]==cid]
        if len(sub)<20: continue
        bm=_market_metrics(sub); cm=_metrics(sub,final,names,comps)
        bycomp[cid]={"count":len(sub),"market_accuracy":bm["accuracy"],"shot_model_accuracy":cm["accuracy"],"uplift_pp":(cm["accuracy"]-bm["accuracy"])*100}
    payload={
      "schema_version":"V6.11.6-lagged-shot-quality-1x2-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
      "sample":{"raw_with_stats":len(raw),"lagged_total":len(rows),"older":len(old),"newer":len(new),"older_train":len(train),"older_validation":len(val),"provider_counts":providers,"stats_rows_by_competition":stats_counts,"feature_names":names},
      "older_validation":{"market":_market_metrics(val),"leaderboard":leaderboard,"selected":{"family":best["family"],"param":best["param"]}},
      "newer_season_test":{"market":market,"selected":candidate,"selected_vs_market_uplift_pp":(candidate["accuracy"]-market["accuracy"])*100},
      "by_competition":bycomp,
      "governance":{"research_only":True,"current_match_stats_never_used_as_features":True,"newer_season_never_used_for_model_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); return 0

if __name__=="__main__": raise SystemExit(main())
