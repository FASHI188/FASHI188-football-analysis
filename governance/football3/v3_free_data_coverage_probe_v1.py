from __future__ import annotations
import json
from pathlib import Path
from typing import Any

REQUIRED_CATEGORIES=[
"data_source_missing","timestamp_or_pit_insufficient","license_or_sustainability_risk",
"league_or_season_coverage_insufficient","identity_binding_insufficient","historical_depth_insufficient",
"source_stability_insufficient","free_quota_insufficient","quality_missing_conflict_ungoverned",
]

def load_registry(path: str|Path) -> dict[str,Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def candidate_receipt(registry: dict[str,Any], candidate_id: str) -> dict[str,Any]:
    row=registry["candidate_coverage_matrix"][candidate_id]
    missing=[k for k in REQUIRED_CATEGORIES if bool(row[k])]
    return {
      "schema_version":"football3-v3-free-data-candidate-coverage-receipt-v1",
      "candidate_id":candidate_id,
      "coverage_status":row["status"],
      "first_authoritative_gap":row["first_authoritative_gap"],
      "unresolved_categories":missing,
      "unresolved_category_count":len(missing),
      "data_ready":False if candidate_id=="C7" or missing else True,
      "training_allowed":False,
      "target_label_access_allowed":False,
    }

def full_probe(registry: dict[str,Any]) -> dict[str,Any]:
    receipts={c:candidate_receipt(registry,c) for c in sorted(registry["candidate_coverage_matrix"])}
    ready=[c for c,r in receipts.items() if r["data_ready"]]
    return {
      "schema_version":"football3-v3-free-data-coverage-probe-receipt-v1",
      "status":"FREE_DATA_SHARED_INFRA_READY_NO_CANDIDATE_DATA_READY" if not ready else "ZERO_LABEL_REAUDIT_REQUIRED_BEFORE_DATA_READY_AMENDMENT",
      "candidate_receipts":receipts,
      "data_ready_candidates":ready,
      "active_experts":[],
      "selected_experts":[],
      "new_target_labels_read":False,
      "training_performed":False,
      "tuning_performed":False,
      "formal_v2_changed":False,
      "current_changed":False,
      "production_changed":False,
    }

def main() -> int:
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--registry",default="governance/football3/v3_free_source_registry_v1.json")
    a=ap.parse_args()
    print(json.dumps(full_probe(load_registry(a.registry)),sort_keys=True))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
