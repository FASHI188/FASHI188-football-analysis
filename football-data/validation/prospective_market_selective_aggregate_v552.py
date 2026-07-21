#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist, mean

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "prospective_market_selective_challenger_v526.json"
EVIDENCE = ROOT / "evidence" / "market_selective_prospective_outcomes"
OUT = ROOT / "manifests" / "prospective_market_selective_validation_v552_status.json"


def _wilson(successes: int, n: int, confidence: float = 0.95):
    if n <= 0:
        return {"lower": None, "upper": None}
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = successes / n
    denom = 1.0 + z*z/n
    center = (p + z*z/(2*n)) / denom
    margin = z * math.sqrt((p*(1-p) + z*z/(4*n))/n) / denom
    return {"lower": max(0.0, center-margin), "upper": min(1.0, center+margin)}


def _earliest(rows):
    chosen = {}
    duplicates = defaultdict(int)
    for row in sorted(rows, key=lambda r: (str(r.get("freeze_utc")), str(r.get("evaluated_at_utc")))):
        key = (str(row.get("match_key")), str(row.get("config_sha256")))
        if key in chosen:
            duplicates[str(row.get("match_key"))] += 1
            continue
        chosen[key] = row
    return list(chosen.values()), dict(duplicates)


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    candidates = set((config.get("candidate_domains") or {}).keys())
    gate = config["prospective_validation_gate"]
    raw_rows = []
    invalid = []
    for path in sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid.append({"path": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if row.get("status") != "SCORED_SELECTIVE_ROW":
            continue
        if row.get("competition_id") not in candidates:
            continue
        if not row.get("snapshot_contract_passed") or not row.get("timing_robust_point_gate"):
            invalid.append({"path": str(path.relative_to(ROOT)), "error": "SNAPSHOT_OR_TIMING_GATE_FAIL"})
            continue
        raw_rows.append(row)

    rows, duplicates = _earliest(raw_rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["competition_id"], row["config_sha256"])].append(row)

    reports = {}
    ready = []
    for (cid, cfg_sha), items in grouped.items():
        selected = [r for r in items if bool(r.get("selected_by_shadow_gate"))]
        market_success = sum(int(r["market_direction_correct"]) for r in selected)
        formal_success = sum(int(r["formal_direction_correct"]) for r in selected)
        market_acc = market_success / len(selected) if selected else None
        formal_acc = formal_success / len(selected) if selected else None
        ci = _wilson(market_success, len(selected))
        market_brier = mean(float(r["market_brier"]) for r in selected) if selected else None
        formal_brier = mean(float(r["formal_brier"]) for r in selected) if selected else None
        market_rps = mean(float(r["market_rps"]) for r in selected) if selected else None
        formal_rps = mean(float(r["formal_rps"]) for r in selected) if selected else None
        checks = {
            "minimum_valid_pit_snapshots": len(items) >= int(gate["minimum_valid_pit_snapshots_per_domain"]),
            "minimum_selected_predictions": len(selected) >= int(gate["minimum_selected_predictions_per_domain"]),
            "minimum_selected_accuracy": market_acc is not None and market_acc >= float(gate["minimum_selected_accuracy"]),
            "minimum_accuracy_ci95_lower": ci["lower"] is not None and ci["lower"] >= float(gate["minimum_accuracy_ci95_lower"]),
            "outperform_same_snapshot_formal_direction": market_acc is not None and formal_acc is not None and market_acc > formal_acc,
            "market_brier_nonworse": market_brier is not None and formal_brier is not None and market_brier <= formal_brier,
            "market_rps_nonworse": market_rps is not None and formal_rps is not None and market_rps <= formal_rps,
        }
        readiness = all(checks.values())
        key = f"{cid}|{cfg_sha}"
        reports[key] = {
            "competition_id": cid,
            "config_sha256": cfg_sha,
            "valid_pit_snapshot_count": len(items),
            "selected_prediction_count": len(selected),
            "market_selected_accuracy": market_acc,
            "formal_direction_accuracy_on_same_selected_matches": formal_acc,
            "market_selected_wilson95": ci,
            "market_brier_on_selected": market_brier,
            "formal_brier_on_selected": formal_brier,
            "market_rps_on_selected": market_rps,
            "formal_rps_on_selected": formal_rps,
            "readiness_checks": checks,
            "shadow_promotion_readiness": readiness,
        }
        if readiness:
            ready.append(cid)

    payload = {
        "schema_version": "V5.5.2-prospective-market-selective-validation-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "candidate_domains": sorted(candidates),
        "raw_scored_row_count": len(raw_rows),
        "unique_earliest_snapshot_count": len(rows),
        "duplicate_rows_excluded": sum(duplicates.values()),
        "duplicate_match_keys": duplicates,
        "invalid_files": invalid,
        "reports": reports,
        "shadow_promotion_ready_domains": sorted(set(ready)),
        "status": "NO_OUTCOME_EVIDENCE_YET" if not rows else "PASS",
        "formal_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": "Only the earliest valid question-time row per match/config is scored for readiness. This is shadow evidence; formal promotion still requires a later CURRENT-authorized promotion receipt."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
