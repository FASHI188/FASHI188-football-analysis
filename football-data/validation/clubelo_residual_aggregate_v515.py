#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "clubelo_residual_challenger_v515.json"
DETAIL = ROOT / "manifests" / "clubelo_residual_oof_v515"
OUT = ROOT / "manifests" / "clubelo_residual_oof_v515_status.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    requested = list(cfg["domains"])
    reports = {}
    missing = []
    for cid in requested:
        path = DETAIL / f"{cid}.json"
        if not path.exists():
            missing.append(cid)
            continue
        reports[cid] = json.loads(path.read_text(encoding="utf-8"))
    passes = [cid for cid, r in reports.items() if r.get("status") == "CLUBELO_RESIDUAL_SIGNAL_PASS_SHADOW_ONLY"]
    failures = [cid for cid, r in reports.items() if str(r.get("status") or "").startswith("EXECUTION_FAILURE")]
    payload = {
        "schema_version": "V5.1.5-clubelo-residual-oof-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "requested_domains": requested,
        "completed_domains": list(reports),
        "missing_domains": missing,
        "execution_failure_domains": failures,
        "signal_pass_domains": passes,
        "signal_pass_count": len(passes),
        "reports": {cid: {
            "status": r.get("status"),
            "forward_prediction_count": r.get("forward_prediction_count"),
            "pooled_metrics": r.get("pooled_metrics"),
            "paired_block_bootstrap": r.get("paired_block_bootstrap"),
            "folds": r.get("folds"),
            "checks": r.get("checks"),
            "error": r.get("error")
        } for cid, r in reports.items()},
        "status": "PASS" if len(reports) == len(requested) and not missing and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "global_interpretation": "Any positive result is shadow evidence only. ClubElo source is PIT-capable through validity intervals, but 2025/26 has already been observed elsewhere in project research; clean promotion still requires untouched future chronological validation and the CURRENT four-target requirements."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "signal_pass_domains": passes,
        "execution_failure_domains": failures,
        "summary": {cid: {
            "status": r.get("status"),
            "accuracy_diff": ((r.get("pooled_metrics") or {}).get("one_x_two_accuracy") or {}).get("candidate_minus_baseline"),
            "brier_diff": ((r.get("pooled_metrics") or {}).get("one_x_two_brier") or {}).get("candidate_minus_baseline"),
            "rps_diff": ((r.get("pooled_metrics") or {}).get("one_x_two_rps") or {}).get("candidate_minus_baseline")
        } for cid, r in reports.items()}
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
