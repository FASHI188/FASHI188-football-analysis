#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "manifests" / "dynamic_strength_allocation_only_oof_v470"
STATUS_PATH = ROOT / "manifests" / "dynamic_strength_allocation_only_oof_v470_status.json"
COMPETITIONS = ["ENG_PremierLeague","GER_Bundesliga","ITA_SerieA","FRA_Ligue1","ESP_LaLiga","POR_PrimeiraLiga","NED_Eredivisie","SWE_Allsvenskan","NOR_Eliteserien","BRA_SerieA"]
def main() -> int:
    reports={}; missing=[]; failures=[]; candidates=[]
    for cid in COMPETITIONS:
        p=REPORT_ROOT/f"{cid}.json"
        if not p.exists(): missing.append(cid); reports[cid]={"competition_id":cid,"status":"MISSING","formal_weight":0}; continue
        r=json.loads(p.read_text(encoding="utf-8")); reports[cid]=r
        if r.get("status")=="FAILED": failures.append(cid)
        if r.get("status")=="ALLOCATION_ONLY_DYNAMIC_STRENGTH_REVIEW_CANDIDATE": candidates.append(cid)
    out={"schema_version":"V4.7.0-dynamic-strength-allocation-only-aggregate-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS" if not missing and not failures else "PARTIAL","competition_count_requested":len(COMPETITIONS),"competition_count_built":len(COMPETITIONS)-len(missing)-len(failures),"competition_count_failed":len(failures),"competition_count_missing":len(missing),"second_stage_review_candidates":candidates,"candidate_count":len(candidates),"formal_weight_change":False,"automatic_promotion":False,"probability_change":False,"formal_rule_version_unchanged":"V4.7.0","reports":reports,"failures":failures,"missing":missing,"policy":"Allocation-only research screen. Current direct total-goals marginal is preserved. No automatic activation or CURRENT change."}
    STATUS_PATH.parent.mkdir(parents=True,exist_ok=True); STATUS_PATH.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":out["status"],"candidates":candidates,"failed":failures,"missing":missing},ensure_ascii=False,indent=2)); return 0 if out["status"]=="PASS" else 1
if __name__=="__main__": raise SystemExit(main())
