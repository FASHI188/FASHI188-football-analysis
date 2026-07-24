#!/usr/bin/env python3
"""V6.12.4 diagnostic-only error anatomy for the latest 100 2025/26 1X2 matches."""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import validate_1x2_late_season_stakes_fast100_v6122 as s

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"v6_1x2_error_anatomy_fast100_v6124_status.json"

def correct(r):return ("home","draw","away")[max(range(3),key=lambda k:r["p"][k])]==r["actual"]
def pack(rows):
    h=sum(correct(r) for r in rows);return {"count":len(rows),"hits":h,"accuracy":h/len(rows) if rows else None}
def group(rows,keyfn):
    d=defaultdict(list)
    for r in rows:d[str(keyfn(r))].append(r)
    return {k:pack(v) for k,v in sorted(d.items())}
def band(p):
    if p<0.45:return "<0.45"
    if p<0.50:return "0.45-0.50"
    if p<0.55:return "0.50-0.55"
    if p<0.60:return "0.55-0.60"
    if p<0.65:return "0.60-0.65"
    return ">=0.65"
def favdir(r):return ("home","draw","away")[max(range(3),key=lambda k:r["p"][k])]
def main():
    rows=s.enrich(s.load_rows());test=rows[-100:]
    payload={"schema_version":"V6.12.4-error-anatomy-fast100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","classification":"DIAGNOSTIC_RETROSPECTIVE_RESEARCH_ONLY","governance":{"test_matches":100,"no_model_fit":True,"no_threshold_selection":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},
             "sample":{"first":test[0]["date"],"last":test[-1]["date"]},"overall":pack(test),
             "by_competition":group(test,lambda r:r["competition_id"]),"by_market_pick":group(test,favdir),"by_probability_band":group(test,lambda r:band(r["maxp"])),
             "by_market_pick_and_band":group(test,lambda r:f"{favdir(r)}|{band(r['maxp'])}"),
             "stakes":{"safe_favourite":pack([r for r in test if r["fav_safe"]]),"urgent_favourite":pack([r for r in test if not r["fav_safe"] and r["fav_side"]!="draw"]),"safe_favourite_vs_urgent":pack([r for r in test if r["conflict_safe_fav_vs_urgent_opp"]])}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(payload,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
