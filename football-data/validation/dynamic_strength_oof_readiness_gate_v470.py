#!/usr/bin/env python3
"""Convert raw public-evidence coverage into a strict OOF-readiness decision.

The raw coverage audit answers whether fields exist.  This gate answers whether
there is enough chronological depth to attempt a >=8-fold dynamic-strength OOF
screen without relabeling an engineering shortage as a model failure.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manifests" / "dynamic_strength_public_evidence_v470_status.json"
OUT = ROOT / "manifests" / "dynamic_strength_oof_readiness_v470_status.json"
MIN_SEASONS = 3
MIN_LAGGED_MANAGER_MATCHES = 200
MIN_PRIOR_LINEUP_MATCHES = 200
STANDARD = {"standard", "standard_regular_league_only"}


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    reports = {}
    ready = []
    stage_adapter = []
    insufficient_history = []
    unavailable = []
    for cid, raw in source.get("reports", {}).items():
        feature_inputs = bool(raw.get("feature_inputs_observed"))
        route = str(raw.get("validation_route") or "")
        enough_history = (
            int(raw.get("season_count") or 0) >= MIN_SEASONS
            and int(raw.get("lagged_manager_feature_eligible_matches") or 0) >= MIN_LAGGED_MANAGER_MATCHES
            and int(raw.get("prior_season_lineup_feature_eligible_matches") or 0) >= MIN_PRIOR_LINEUP_MATCHES
        )
        if not feature_inputs:
            status = "PUBLIC_EVIDENCE_PARTIAL_OR_UNAVAILABLE"; unavailable.append(cid)
        elif route not in STANDARD:
            status = "STAGE_ADAPTER_REQUIRED"; stage_adapter.append(cid)
        elif not enough_history:
            status = "INSUFFICIENT_CHRONOLOGICAL_OOF_HISTORY"; insufficient_history.append(cid)
        else:
            status = "CHRONOLOGICAL_OOF_READY"; ready.append(cid)
        reports[cid] = {
            "competition_id": cid,
            "status": status,
            "validation_route": route,
            "season_count": int(raw.get("season_count") or 0),
            "lagged_manager_feature_eligible_matches": int(raw.get("lagged_manager_feature_eligible_matches") or 0),
            "prior_season_lineup_feature_eligible_matches": int(raw.get("prior_season_lineup_feature_eligible_matches") or 0),
            "feature_inputs_observed": feature_inputs,
            "chronological_oof_may_start": status == "CHRONOLOGICAL_OOF_READY",
            "formal_weight": 0,
            "probability_change": False,
        }
    out = {
        "schema_version": "V4.7.0-dynamic-strength-oof-readiness-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "thresholds": {
            "minimum_seasons": MIN_SEASONS,
            "minimum_lagged_manager_feature_matches": MIN_LAGGED_MANAGER_MATCHES,
            "minimum_prior_season_lineup_feature_matches": MIN_PRIOR_LINEUP_MATCHES,
        },
        "chronological_oof_ready": ready,
        "stage_adapter_required": stage_adapter,
        "insufficient_chronological_history": insufficient_history,
        "partial_or_unavailable": unavailable,
        "formal_weight_change": False,
        "probability_change": False,
        "reports": reports,
        "policy": "Raw field coverage alone never authorizes OOF. Engineering/data insufficiency must be reported separately from model failure."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ready": ready, "stage_adapter_required": stage_adapter, "insufficient_history": insufficient_history, "unavailable": unavailable}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
