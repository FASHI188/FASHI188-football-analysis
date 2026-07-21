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
CONSENSUS_AUDIT = ROOT / "manifests" / "prospective_market_consensus_v554_status.json"
EVIDENCE = ROOT / "evidence" / "market_matrix_prospective_outcomes"
OUT = ROOT / "manifests" / "prospective_market_matrix_validation_v548_status.json"

METRICS = [
    "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
    "score_top1", "score_top3", "total_top1", "total_top2", "total_rps", "ou_brier",
]


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
        key = (str(row.get("match_key")), str(row.get("registry_sha256")), str(row.get("profile")))
        if key in chosen:
            duplicates[str(row.get("match_key"))] += 1
            continue
        chosen[key] = row
    return list(chosen.values()), dict(duplicates)


def _constraint_residual(audit: dict[str, Any]) -> float:
    for key in ("max_constraint_residual", "market_constraint_residual"):
        if key in audit:
            return float(audit[key])
    return 1.0


def _load_consensus_audit() -> dict[str, Any]:
    if not CONSENSUS_AUDIT.exists():
        return {}
    try:
        return json.loads(CONSENSUS_AUDIT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    eligible = set(cfg["eligible_domains"])
    expected_profiles = dict(cfg["eligible_profiles"])
    consensus_audit = _load_consensus_audit()
    consensus_counts = dict(consensus_audit.get("competition_counts") or {})
    ou25_counts = dict(consensus_audit.get("ou25_eligible_competition_counts") or {})
    minimum_consensus = int(cfg["minimum_valid_consensus_snapshots_per_domain"])
    all_rows = []
    invalid_files = []
    excluded_profile_rows = []
    excluded_market_input_rows = []

    for path in sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid_files.append({"path": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"})
            continue
        cid = str(row.get("competition_id") or "")
        if row.get("status") != "SCORED_SHADOW_ROW" or cid not in eligible:
            continue
        if row.get("market_input_kind") != "INDEPENDENT_PROVIDER_CONSENSUS" or not bool(row.get("promotion_evidence_eligible")):
            excluded_market_input_rows.append({
                "path": str(path.relative_to(ROOT)), "competition_id": cid,
                "market_input_kind": row.get("market_input_kind"),
                "promotion_evidence_eligible": bool(row.get("promotion_evidence_eligible")),
            })
            continue
        expected_profile = expected_profiles.get(cid)
        if str(row.get("profile") or "") != str(expected_profile or ""):
            excluded_profile_rows.append({
                "path": str(path.relative_to(ROOT)),
                "competition_id": cid,
                "observed_profile": row.get("profile"),
                "expected_profile": expected_profile,
            })
            continue
        audit = row.get("projection_audit") or {}
        if float(audit.get("probability_sum_residual") or 1.0) > 1e-10 or _constraint_residual(audit) > 1e-10:
            invalid_files.append({"path": str(path.relative_to(ROOT)), "error": "PROJECTION_AUDIT_FAIL"})
            continue
        all_rows.append(row)

    rows, duplicates = _earliest_unique(all_rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["competition_id"], row["registry_sha256"], row["profile"])].append(row)

    reports = {}
    ready_domains = []
    boot_cfg = cfg["chronological_block_bootstrap"]
    for (cid, profile_hash, profile_name), items in grouped.items():
        formal = {m: mean(float(r["formal_metrics"][m]) for r in items) for m in METRICS}
        candidate = {m: mean(float(r["candidate_metrics"][m]) for r in items) for m in METRICS}
        delta = {m: candidate[m] - formal[m] for m in METRICS}
        bootstrap = {
            m: _bootstrap(items, m, block_size=int(boot_cfg["block_size_matches"]), draws=int(boot_cfg["draws"]), seed=int(boot_cfg["seed"]) + i)
            for i, m in enumerate(["one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_rps", "ou_brier"], 1)
        } if len(items) >= 20 else {}
        minimum_rows = int(cfg["minimum_evaluated_matches_after_formal_baseline_gate"])
        enough_rows = len(items) >= minimum_rows
        raw_consensus_count = int(consensus_counts.get(cid, 0))
        profile_consensus_count = int(ou25_counts.get(cid, 0)) if cid in {"GER_Bundesliga", "FRA_Ligue1"} else raw_consensus_count
        checks = {
            "minimum_valid_consensus_snapshots": profile_consensus_count >= minimum_consensus,
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
        readiness = all(checks.values())
        key = f"{cid}|{profile_hash}|{profile_name}"
        reports[key] = {
            "competition_id": cid,
            "profile_hash": profile_hash,
            "profile": profile_name,
            "valid_consensus_count": raw_consensus_count,
            "profile_eligible_consensus_count": profile_consensus_count,
            "evaluated_match_count": len(items),
            "formal": formal, "candidate": candidate, "candidate_minus_formal": delta,
            "bootstrap": bootstrap, "readiness_checks": checks,
            "shadow_promotion_readiness": readiness,
        }
        if readiness:
            ready_domains.append(cid)

    payload = {
        "schema_version": "V5.5.5-prospective-market-matrix-validation-aggregate-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "consensus_audit_status": consensus_audit.get("status") if consensus_audit else "MISSING",
        "consensus_competition_counts": consensus_counts,
        "consensus_ou25_counts": ou25_counts,
        "promotion_market_input_kind": "INDEPENDENT_PROVIDER_CONSENSUS",
        "evidence_file_count": len(all_rows),
        "unique_scored_match_count": len(rows),
        "duplicate_rows_excluded": sum(duplicates.values()),
        "duplicate_match_keys": duplicates,
        "invalid_files": invalid_files,
        "excluded_profile_rows": excluded_profile_rows,
        "excluded_market_input_rows": excluded_market_input_rows,
        "reports": reports,
        "shadow_promotion_ready_domains": sorted(set(ready_domains)),
        "status": "NO_OUTCOME_EVIDENCE_YET" if not rows else "PASS",
        "formal_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": "Promotion readiness requires synchronized independent-provider consensus evidence. Single-provider rows remain diagnostic and are excluded. A later CURRENT-authorized promotion receipt is mandatory before any formal model change."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
