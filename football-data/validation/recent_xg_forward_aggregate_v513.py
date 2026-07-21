#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "recent_xg_forward_shadow_v513.json"
DETAIL = ROOT / "manifests" / "recent_xg_forward_shadow_v513"
OUT = ROOT / "manifests" / "recent_xg_forward_shadow_v513_status.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    reports = {}
    missing = []
    for competition_id in cfg["domains"]:
        path = DETAIL / f"{competition_id}.json"
        if not path.exists():
            missing.append(competition_id)
            continue
        reports[competition_id] = json.loads(path.read_text(encoding="utf-8"))

    passed = [cid for cid, report in reports.items() if report.get("status") == "RECENT_XG_FORWARD_SIGNAL_PASS_SHADOW_ONLY"]
    execution_failures = [cid for cid, report in reports.items() if str(report.get("status") or "").startswith("EXECUTION_FAILURE")]
    payload = {
        "schema_version": "V5.1.3-recent-xg-forward-shadow-aggregate-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "requested_domains": cfg["domains"],
        "completed_domains": list(reports),
        "missing_domains": missing,
        "execution_failure_domains": execution_failures,
        "signal_pass_domains": passed,
        "signal_pass_count": len(passed),
        "reports": {cid: {
            "status": r.get("status"),
            "selected_profile": r.get("selected_profile"),
            "forward_prediction_count": r.get("forward_prediction_count", 0),
            "pooled_metrics": r.get("pooled_metrics", {}),
            "paired_block_bootstrap": r.get("paired_block_bootstrap", {}),
            "checks": r.get("checks", {}),
            "baseline_skipped_count": r.get("baseline_skipped_count"),
            "xg_skipped_count": r.get("xg_skipped_count"),
            "chronology_violation_count": r.get("chronology_violation_count"),
            "error": r.get("error")
        } for cid, r in reports.items()},
        "status": "PASS" if len(reports) == len(cfg["domains"]) and not missing and not execution_failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "global_interpretation": "A signal pass is shadow evidence only. Current Understat retrieval is retrospective and cannot establish historical pre-match publication timestamps, so no formal V5 promotion is authorized."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
