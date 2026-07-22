#!/usr/bin/env python3
"""V6.2.9 pooled asymmetric selective gate on the corrected V6.2.5 r4 cache.

All selection uses older 850 only. The newer 850 is evaluation only.
Architecture/probabilities are already frozen in the r4 pooled cache.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "manifests" / "v6_sampled_17domain_pooled_scored_cache_v625_r4.json"
OUT = ROOT / "manifests" / "v6_sampled_pooled_asymmetric_gate_v629_status.json"
TARGET = 0.65
MIN_TOTAL = 120
MIN_DIRECTION = 20
Z90 = 1.6448536269514722


def _wilson(h: int, n: int) -> float | None:
    if n <= 0:
        return None
    p = h / n
    z = Z90
    d = 1 + z*z/n
    c = p + z*z/(2*n)
    r = z * math.sqrt((p*(1-p)+z*z/(4*n))/n)
    return (c-r)/d


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows); h = sum(int(bool(r["hit"])) for r in rows)
    by = {}
    for d in ("home", "away"):
        p = [r for r in rows if r["pick"] == d]
        ph = sum(int(bool(r["hit"])) for r in p)
        by[d] = {"count": len(p), "hits": ph, "accuracy": ph/len(p) if p else None, "wilson90_lower": _wilson(ph,len(p))}
    return {"count": n, "hits": h, "accuracy": h/n if n else None, "wilson90_lower": _wilson(h,n), "coverage_of_850": n/850.0, "directions": dict(Counter(r["pick"] for r in rows)), "by_direction": by}


def _eligible(r: dict[str, Any]) -> bool:
    return bool(r.get("eligible_prior_selective")) and r.get("pick") in ("home","away")


def _grid(rows: list[dict[str, Any]], direction: str) -> list[float]:
    return sorted({float(r["confidence"]) for r in rows if _eligible(r) and r["pick"] == direction})


def _choose(old: list[dict[str, Any]]) -> dict[str, Any] | None:
    best = None
    for ht in _grid(old,"home"):
        home=[r for r in old if _eligible(r) and r["pick"]=="home" and float(r["confidence"])>=ht]
        if len(home)<MIN_DIRECTION: continue
        for at in _grid(old,"away"):
            away=[r for r in old if _eligible(r) and r["pick"]=="away" and float(r["confidence"])>=at]
            if len(away)<MIN_DIRECTION: continue
            chosen=home+away
            if len(chosen)<MIN_TOTAL: continue
            m=_metric(chosen)
            if float(m["accuracy"]) < TARGET: continue
            cand={"home_threshold":ht,"away_threshold":at,**m}
            rank=(cand["count"],cand["wilson90_lower"] or -1,cand["accuracy"] or -1)
            br=(-1,-1,-1) if best is None else (best["count"],best["wilson90_lower"] or -1,best["accuracy"] or -1)
            if rank>br: best=cand
    return best


def _apply(rows: list[dict[str, Any]], rule: dict[str, Any], directions=("home","away")) -> list[dict[str, Any]]:
    out=[]
    for r in rows:
        if not _eligible(r) or r["pick"] not in directions: continue
        t = float(rule["home_threshold"] if r["pick"]=="home" else rule["away_threshold"])
        if float(r["confidence"])>=t: out.append(r)
    return out


def main() -> int:
    generated=datetime.now(timezone.utc).replace(microsecond=0)
    cache=json.loads(CACHE.read_text(encoding="utf-8"))
    if cache.get("schema_version")!="V6.2.5-fixed-sampled-pooled-scored-cache-r4" or cache.get("count")!=1700:
        raise SystemExit("unexpected r4 cache")
    old=[r for r in cache["rows"] if r["role"]=="older"]
    new=[r for r in cache["rows"] if r["role"]=="newer"]
    rule=_choose(old)
    if rule is None:
        payload={"schema_version":"V6.2.9-pooled-asymmetric-r1","generated_at_utc":generated.isoformat(),"status":"NO_65_CALIBRATION_RULE"}
        OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(payload)); return 0
    test=_metric(_apply(new,rule))
    survivors=[d for d in ("home","away") if (rule["by_direction"][d]["accuracy"] is not None and float(rule["by_direction"][d]["accuracy"])>=TARGET)]
    survival_test=_metric(_apply(new,rule,tuple(survivors))) if survivors else _metric([])
    payload={
      "schema_version":"V6.2.9-pooled-asymmetric-r1","generated_at_utc":generated.isoformat(),"status":"PASS",
      "design":{"cache":"V6.2.5 r4 exact pooled architecture","selection_data":"older 850 only","target":TARGET,"min_total":MIN_TOTAL,"min_per_direction":MIN_DIRECTION},
      "calibration_selected_rule":rule,
      "newer_850_asymmetric_test":{**test,"target_65_met":bool(test["count"]) and float(test["accuracy"])>=TARGET},
      "calibration_direction_survivors":survivors,
      "newer_850_survivors_only_exploratory":{**survival_test,"target_65_met":bool(survival_test["count"]) and float(survival_test["accuracy"])>=TARGET},
      "governance":{"research_fast_gate":True,"newer_850_used_for_threshold_selection":False,"newer_850_used_for_direction_survival":False,"fresh_confirmation_required":True,"formal_weight_change":False,"runtime_probability_change":False,"current_rule_change":False}
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
