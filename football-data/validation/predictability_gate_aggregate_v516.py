#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "platform_registry.json"
DETAIL = ROOT / "manifests" / "predictability_gate_v516"
OUT = ROOT / "manifests" / "predictability_gate_v516_status.json"


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    requested = [item["competition_id"] for item in registry["competitions"]]
    reports = {}
    missing = []
    for cid in requested:
        path = DETAIL / f"{cid}.json"
        if not path.exists():
            missing.append(cid)
            continue
        reports[cid] = json.loads(path.read_text(encoding="utf-8"))
    candidates = [cid for cid, r in reports.items() if r.get("status") == "PREDICTABILITY_RESEARCH_CANDIDATE"]
    improved_vs_gap = [
        cid for cid, r in reports.items()
        if isinstance(r.get("multi_signal_minus_gap_only_accuracy"), (int, float))
        and float(r["multi_signal_minus_gap_only_accuracy"]) > 0
    ]
    execution_failures = [cid for cid, r in reports.items() if str(r.get("status") or "").startswith("EXECUTION_FAILURE")]
    ranking = sorted(
        [
            {
                "competition_id": cid,
                "status": r.get("status"),
                "pooled_accuracy": r.get("pooled_accuracy"),
                "pooled_selected_count": r.get("pooled_selected_count"),
                "pooled_coverage": r.get("pooled_coverage"),
                "wilson_lower": (r.get("pooled_ci95_wilson") or {}).get("lower"),
                "gap_only_pooled_accuracy": r.get("gap_only_pooled_accuracy"),
                "multi_signal_minus_gap_only_accuracy": r.get("multi_signal_minus_gap_only_accuracy"),
                "forward_accuracy_min": r.get("forward_accuracy_min"),
                "forward_accuracy_std": r.get("forward_accuracy_std"),
            }
            for cid, r in reports.items() if r.get("pooled_accuracy") is not None
        ],
        key=lambda x: (-(float(x["pooled_accuracy"]) if x["pooled_accuracy"] is not None else -1), -(int(x["pooled_selected_count"] or 0)))
    )
    payload = {
        "schema_version": "V5.1.6-predictability-gate-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "requested_domains": requested,
        "completed_domains": list(reports),
        "missing_domains": missing,
        "execution_failure_domains": execution_failures,
        "research_candidate_domains": candidates,
        "multi_signal_accuracy_improved_vs_gap_only_domains": improved_vs_gap,
        "ranking": ranking,
        "reports": reports,
        "status": "PASS" if len(reports) == len(requested) and not missing and not execution_failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "governance": "This layer only abstains/selects research directions. It does not alter the unified score matrix or existing LaLiga runtime gate."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "research_candidate_domains": candidates,
        "improved_vs_gap": improved_vs_gap,
        "execution_failure_domains": execution_failures,
        "top_ranking": ranking[:10]
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
