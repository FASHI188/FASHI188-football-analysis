#!/usr/bin/env python3
from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "prospective_market_matrix_validation_v548.json"
EVIDENCE = ROOT / "evidence" / "market_matrix_prospective_outcomes"
OUT = ROOT / "manifests" / "prospective_market_matrix_validation_v548_status.json"

METRICS = [
    "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
    "score_top1", "score_top3", "total_top1", "total_top2", "total_rps", "ou_brier",
]
LOWER_BETTER = {"one_x_two_brier", "one_x_two_rps", "joint_log", "total_rps", "ou_brier"}


def _blocks(rows: list[dict[str, Any]], size: int):
    ordered = sorted(rows, key=lambda r: (str(r.get("kickoff_utc")), str(r.get("match_key"))))
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def _bootstrap(rows, metric: str, *, block_size: int, draws: int, seed: int):
    blocks = _blocks(rows, block_size)
    point = mean(float(r["candidate_metrics"][metric]) - float(r["formal_metrics"][metric]) for r in rows)
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        sample = []
        for _ in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        vals.append(mean(float(r["candidate_metrics"][metric]) - float(r["formal_metrics"][metric]) for r in sample))
    vals.sort()
    return {
        "candidate_minus_formal": point,
        "ci95_lower": vals[int(0.025 * (len(vals)-1))],
        "ci95_upper": vals[int(0.975 * (len(vals)-1))],
        "blocks": len(blocks), "draws": draws,
    }


def _earliest_unique(rows: list[dict[str, Any]]):
    chosen = {}
    duplicates = defaultdict(int)
    for row in sorted(rows, key=lambda r: (str(r.get("freeze_utc")), str(r.get("evaluated_at_utc")))):
        key = (str(row.get("match_key")), str(row.get("registry_sha256")))
        if key in chosen:
            duplicates[str(row.get("match_key"))] += 1
            continue
        chosen[key] = row
    return list(chosen.values()), dict(duplicates)


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    eligible = set(cfg["eligible_domains"])
    all_rows = []
    invalid_files = []
    for path in sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid_files.append({"path": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if row.get("status") != "SCORED_SHADOW_ROW" or row.get("competition_id") not in eligible:
            continue
        audit = row.get("projection_audit") or {}
        if float(audit.get("probability_sum_residual") or 1.0) > 1e-10 or float(audit.get("max_constraint_residual") or 1.0) > 1e-10:
            invalid_files.append({"path": str(path.relative_to(ROOT)), "error": "PROJECTION_AUDIT_FAIL"})
            continue
        all_rows.append(row)

    rows, duplicates = _earliest_unique(all_rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["competition_id"], row["registry_sha256"])].append(row)

    reports = {}
    ready_domains = []
    boot_cfg = cfg["chronological_block_bootstrap"]
    for (cid, profile_hash), items in grouped.items():
        formal = {m: mean(float(r["formal_metrics"][m]) for r in items) for m in METRICS}
        candidate = {m: mean(float(r["candidate_metrics"][m]) for r in items) for m in METRICS}
        delta = {m: candidate[m] - formal[m] for m in METRICS}
        bootstrap = {
            m: _bootstrap(items, m, block_size=int(boot_cfg["block_size_matches"]), draws=int(boot_cfg["draws"]), seed=int(boot_cfg["seed"]) + i)
            for i, m in enumerate(["one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_rps", "ou_brier"], 1)
        } if len(items) >= 20 else {}
        minimum_rows = int(cfg["minimum_evaluated_matches_after_formal_baseline_gate"])
        enough_rows = len(items) >= minimum_rows
        checks = {
            "minimum_evaluated_matches": enough_rows,
            "one_x_two_brier_point_nonworse": delta["one_x_two_brier"] <= 0.0,
            "one_x_two_rps_point_nonworse": delta["one_x_two_rps"] <= 0.0,
            "ou_brier_point_nonworse": delta["ou_brier"] <= 0.0,
            "joint_log_point_nonworse": delta["joint_log"] <= 0.0,
            "score_top1_point_nonworse": delta["score_top1"] >= 0.0,
            "score_top3_point_nonworse": delta["score_top3"] >= 0.0,
            "total_top1_point_nonworse": delta["total_top1"] >= 0.0,
            "total_top2_point_nonworse": delta["total_top2"] >= 0.0,
            "total_rps_point_nonworse": delta["total_rps"] <= 0.0,
            "proper_score_strict_bootstrap": bool(bootstrap) and (
                float(bootstrap["one_x_two_brier"]["ci95_upper"]) < 0.0 or float(bootstrap["one_x_two_rps"]["ci95_upper"]) < 0.0
            ),
        }
        readiness = enough_rows and all(checks.values())
        key = f"{cid}|{profile_hash}"
        reports[key] = {
            "competition_id": cid,
            "profile_hash": profile_hash,
            "evaluated_match_count": len(items),
            "formal": formal, "candidate": candidate, "candidate_minus_formal": delta,
            "bootstrap": bootstrap, "readiness_checks": checks,
            "shadow_promotion_readiness": readiness,
        }
        if readiness:
            ready_domains.append(cid)

    payload = {
        "schema_version": "V5.4.8-prospective-market-matrix-validation-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "evidence_file_count": len(all_rows),
        "unique_scored_match_count": len(rows),
        "duplicate_rows_excluded": sum(duplicates.values()),
        "duplicate_match_keys": duplicates,
        "invalid_files": invalid_files,
        "reports": reports,
        "shadow_promotion_ready_domains": sorted(set(ready_domains)),
        "status": "NO_OUTCOME_EVIDENCE_YET" if not rows else "PASS",
        "formal_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": "Readiness is shadow evidence only. A later CURRENT-authorized promotion receipt is mandatory before any formal model change."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
