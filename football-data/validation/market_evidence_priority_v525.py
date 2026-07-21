#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manifests" / "retrospective_market_all17_ceiling_v524_status.json"
OUT = ROOT / "manifests" / "market_evidence_priority_v525_status.json"


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = []
    for cid, report in (source.get("reports") or {}).items():
        status = str(report.get("status") or "")
        market = report.get("market") or {}
        selective = ((report.get("selective_accuracy_by_raw_gap") or {}).get("market") or {})
        gap25 = selective.get("gap_ge_0.25") or {}
        gap30 = selective.get("gap_ge_0.30") or {}
        accuracy_gain_pp = report.get("accuracy_gain_pp")
        strict = status == "RETROSPECTIVE_MARKET_STRONGER_PROPER_SCORES"
        unavailable = status == "MARKET_REFERENCE_UNAVAILABLE"

        # Priority is evidence-acquisition priority, not a formal model weight.
        # Strict proper-score win dominates; larger all-match accuracy gain and
        # stronger selective samples rank within that class.
        if unavailable:
            tier = "P0_MISSING_MARKET_EVIDENCE"
            score = 1000.0
        elif strict:
            tier = "P1_STRICT_MARKET_ADVANTAGE"
            score = (
                500.0
                + float(accuracy_gain_pp or 0.0) * 10.0
                + float(gap25.get("accuracy") or 0.0) * 10.0
                + float(gap30.get("accuracy") or 0.0) * 10.0
                + min(1.0, float(report.get("comparable_coverage") or 0.0)) * 5.0
            )
        else:
            tier = "P2_MARKET_POINT_GAIN_NOT_STRICT"
            score = (
                100.0
                + float(accuracy_gain_pp or 0.0) * 10.0
                + float(gap30.get("accuracy") or 0.0) * 5.0
            )

        rows.append({
            "competition_id": cid,
            "last_complete_season": report.get("season"),
            "tier": tier,
            "priority_score": score,
            "retrospective_status": status,
            "comparable_rows": report.get("comparable_row_count"),
            "comparable_coverage": report.get("comparable_coverage"),
            "formal_accuracy": (report.get("formal") or {}).get("accuracy"),
            "market_accuracy": market.get("accuracy"),
            "accuracy_gain_pp": accuracy_gain_pp,
            "market_gap25_selected": gap25.get("selected"),
            "market_gap25_accuracy": gap25.get("accuracy"),
            "market_gap30_selected": gap30.get("selected"),
            "market_gap30_accuracy": gap30.get("accuracy"),
            "formal_pit_market_eligible": False,
        })

    # Missing evidence first because it is a critical unknown; then strict wins
    # ordered by observed potential, then non-strict references.
    rows.sort(key=lambda row: (-float(row["priority_score"]), row["competition_id"]))
    for rank, row in enumerate(rows, start=1):
        row["priority_rank"] = rank

    payload = {
        "schema_version": "V5.2.5-market-evidence-priority-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_receipt": str(SOURCE.relative_to(ROOT)),
        "priorities": rows,
        "strict_market_advantage_domains": [row["competition_id"] for row in rows if row["tier"] == "P1_STRICT_MARKET_ADVANTAGE"],
        "missing_market_evidence_domains": [row["competition_id"] for row in rows if row["tier"] == "P0_MISSING_MARKET_EVIDENCE"],
        "non_strict_market_reference_domains": [row["competition_id"] for row in rows if row["tier"] == "P2_MARKET_POINT_GAIN_NOT_STRICT"],
        "research_priority_policy": (
            "P0 means obtain real prospective market evidence because the historical ceiling is unknown. "
            "P1 means retrospective market proper scores are strictly better and prospective synchronized evidence should be prioritized. "
            "P2 means point estimates improve but strict bootstrap evidence is insufficient."
        ),
        "formal_weight_change": False,
        "probability_change": False,
        "formal_pit_market_eligible": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "top_priorities": rows[:10],
        "strict_count": len(payload["strict_market_advantage_domains"]),
        "missing": payload["missing_market_evidence_domains"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
