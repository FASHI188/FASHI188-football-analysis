#!/usr/bin/env python3
"""V6.12.5 fixed cross-season anatomy of high-confidence late-season favourites."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import validate_1x2_safe_favourite_crossseason100_v6123 as x

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"v6_1x2_high_confidence_crossseason100_v6125_status.json"

def correct(r):return ("home","draw","away")[max(range(3),key=lambda k:r["p"][k])]==r["actual"]
def fav(r):return ("home","draw","away")[max(range(3),key=lambda k:r["p"][k])]
def pack(rows):
    h=sum(correct(r) for r in rows);return {"count":len(rows),"hits":h,"accuracy":h/len(rows) if rows else None}
def report(season):
    rows=x.enrich(x.load(season));t=rows[-100:]
    return {
      "all":pack(t),
      "p_0.58_0.60":pack([r for r in t if 0.58<=r["maxp"]<0.60]),
      "p_0.60_0.65":pack([r for r in t if 0.60<=r["maxp"]<0.65]),
      "p_ge_0.65":pack([r for r in t if r["maxp"]>=0.65]),
      "home_p_ge_0.65":pack([r for r in t if r["maxp"]>=0.65 and fav(r)=="home"]),
      "away_p_ge_0.65":pack([r for r in t if r["maxp"]>=0.65 and fav(r)=="away"]),
      "home_p_ge_0.65_safe":pack([r for r in t if r["maxp"]>=0.65 and fav(r)=="home" and r["fav_safe"]]),
      "home_p_ge_0.65_not_safe":pack([r for r in t if r["maxp"]>=0.65 and fav(r)=="home" and not r["fav_safe"]]),
      "p_ge_0.58_cap_below_0.65":pack([r for r in t if 0.58<=r["maxp"]<0.65]),
      "p_ge_0.58_all":pack([r for r in t if r["maxp"]>=0.58]),
    }
def main():
    reps={s:report(s) for s in ("2024/25","2025/26")}
    payload={"schema_version":"V6.12.5-high-confidence-crossseason100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","classification":"DIAGNOSTIC_RETROSPECTIVE_RESEARCH_ONLY","governance":{"fixed_bands_no_selection":True,"two_disjoint_season_end_100_blocks":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},"reports":reps}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(reps,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
