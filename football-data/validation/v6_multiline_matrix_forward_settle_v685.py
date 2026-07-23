#!/usr/bin/env python3
"""V6.8.5 prospective settlement/evaluation for immutable multiline-matrix freezes.

Settlement is deliberately incapable of calling the V6.8.2 projector or the formal model.
It verifies stored hashes, reads a unique processed 90-minute final score after kickoff, and
scores the two matrices that were already stored before kickoff. This closes the historical
reprojection loophole in the older V5.4.8 research scorer.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import normalize_team_token, parse_iso_datetime, read_processed_matches, score_matrix_rows, sha256_json

CFG = ROOT / "config" / "v6_multiline_matrix_forward_v685.json"
FREEZE_ROOT = ROOT / "forward" / "v6_multiline_matrix_freezes_v685"
OUTCOME_ROOT = ROOT / "evidence" / "market_matrix_forward_outcomes_v685"
STATUS = ROOT / "manifests" / "v6_multiline_matrix_forward_v685_status.json"
EPS = 1e-15
LOWER_BETTER = {"one_x_two_brier", "one_x_two_rps", "joint_log", "total_rps", "ou25_brier"}
METRICS = [
    "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
    "score_top1", "score_top3", "total_top1", "total_top2", "total_rps", "ou25_brier",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def metrics(matrix: list[dict[str, Any]], hg: int, ag: int) -> dict[str, float]:
    one = {"home": 0.0, "draw": 0.0, "away": 0.0}
    totals = [0.0] * 8
    cells = []
    p_observed = 0.0
    p_over25 = 0.0
    for h, a, p in score_matrix_rows(matrix):
        group = "home" if h > a else "draw" if h == a else "away"
        one[group] += p
        totals[min(7, h + a)] += p
        cells.append((p, h, a))
        if h == hg and a == ag:
            p_observed += p
        if h + a >= 3:
            p_over25 += p
    actual = "home" if hg > ag else "draw" if hg == ag else "away"
    pick = max(("home", "draw", "away"), key=lambda k: one[k])
    brier = sum((one[k] - (1.0 if k == actual else 0.0)) ** 2 for k in one)
    order = ["away", "draw", "home"]
    actual_idx = order.index(actual)
    cp = co = rps = 0.0
    for idx in range(2):
        cp += one[order[idx]]
        co += 1.0 if actual_idx == idx else 0.0
        rps += (cp - co) ** 2
    rps /= 2.0
    cells.sort(reverse=True)
    observed_score = (hg, ag)
    observed_total = min(7, hg + ag)
    total_rank = sorted(range(8), key=lambda i: (-totals[i], i))
    running = total_rps = 0.0
    for idx, prob in enumerate(totals[:-1]):
        running += prob
        observed_cdf = 1.0 if observed_total <= idx else 0.0
        total_rps += (running - observed_cdf) ** 2
    total_rps /= 7.0
    actual_over = 1.0 if hg + ag >= 3 else 0.0
    return {
        "one_x_two_accuracy": 1.0 if pick == actual else 0.0,
        "one_x_two_brier": brier,
        "one_x_two_rps": rps,
        "joint_log": -math.log(max(EPS, p_observed)),
        "score_top1": 1.0 if cells and (cells[0][1], cells[0][2]) == observed_score else 0.0,
        "score_top3": 1.0 if any((h, a) == observed_score for _p, h, a in cells[:3]) else 0.0,
        "total_top1": 1.0 if total_rank[0] == observed_total else 0.0,
        "total_top2": 1.0 if observed_total in total_rank[:2] else 0.0,
        "total_rps": total_rps,
        "ou25_brier": (p_over25 - actual_over) ** 2,
    }


def validate_freeze(row: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = []
    recorded = row.get("freeze_sha256")
    body = {k: v for k, v in row.items() if k != "freeze_sha256"}
    if recorded != sha256_json(body):
        errors.append("freeze_sha256_mismatch")
    formal = row.get("formal_source") or {}
    candidate = row.get("candidate") or {}
    if formal.get("formal_matrix_sha256") != sha256_json(formal.get("formal_matrix")):
        errors.append("formal_matrix_sha256_mismatch")
    if candidate.get("candidate_matrix_sha256") != sha256_json(candidate.get("candidate_matrix")):
        errors.append("candidate_matrix_sha256_mismatch")
    if row.get("status") != "FROZEN":
        errors.append("freeze_status_not_FROZEN")
    return not errors, errors


def result_for(sidecar: dict[str, Any], now: datetime, cache: dict[str, list[Any]], minimum_age: timedelta) -> tuple[int, int, str] | None:
    identity = sidecar["fixture_identity"]
    kickoff = parse_iso_datetime(identity["kickoff_utc"], "kickoff_utc")
    if now < kickoff + minimum_age:
        return None
    cid = str(identity["competition_id"])
    if cid not in cache:
        try:
            cache[cid] = read_processed_matches(cid)
        except Exception:
            cache[cid] = []
    rows = [
        m for m in cache[cid]
        if m.date.date() == kickoff.date()
        and normalize_team_token(m.home_team) == normalize_team_token(str(identity["home_team"]))
        and normalize_team_token(m.away_team) == normalize_team_token(str(identity["away_team"]))
    ]
    if len(rows) != 1:
        return None
    m = rows[0]
    return int(m.home_goals), int(m.away_goals), f"{m.source_path}|{m.date.date().isoformat()}|{m.home_team}|{m.away_team}"


def outcome_path(sidecar_path: Path) -> Path:
    return OUTCOME_ROOT / f"{sidecar_path.stem}__settled.json"


def settle(cfg: dict[str, Any]) -> dict[str, int]:
    now = utc_now()
    minimum_age = timedelta(hours=float(cfg.get("settlement_minimum_age_hours", 2)))
    cache: dict[str, list[Any]] = {}
    stats: Counter = Counter()
    OUTCOME_ROOT.mkdir(parents=True, exist_ok=True)
    for path in sorted(FREEZE_ROOT.glob("*.json")) if FREEZE_ROOT.exists() else []:
        stats["freezes_seen"] += 1
        out = outcome_path(path)
        if out.exists():
            stats["already_settled"] += 1
            continue
        try:
            sidecar = json.loads(path.read_text(encoding="utf-8"))
            valid, errors = validate_freeze(sidecar)
            if not valid:
                stats["freeze_integrity_fail"] += 1
                continue
            resolved = result_for(sidecar, now, cache, minimum_age)
            if resolved is None:
                stats["result_not_available_or_not_unique"] += 1
                continue
            hg, ag, source_record = resolved
            formal_matrix = sidecar["formal_source"]["formal_matrix"]
            candidate_matrix = sidecar["candidate"]["candidate_matrix"]
            identity = sidecar["fixture_identity"]
            payload_without_hash = {
                "schema_version": "V6.8.5-multiline-joint-matrix-forward-outcome-r1",
                "status": "SCORED_FROZEN_ROW",
                "evaluated_at_utc": now.isoformat(),
                "competition_id": identity["competition_id"],
                "season": identity.get("season"),
                "home_team": identity["home_team"],
                "away_team": identity["away_team"],
                "kickoff_utc": identity["kickoff_utc"],
                "freeze_time_utc": identity["freeze_time_utc"],
                "match_key": f"{identity['competition_id']}|{identity.get('season')}|{identity['kickoff_utc']}|{identity['home_team']}|{identity['away_team']}",
                "source_sidecar_path": str(path.relative_to(ROOT)),
                "source_sidecar_file_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                "source_sidecar_freeze_sha256": sidecar["freeze_sha256"],
                "formal_matrix_sha256": sidecar["formal_source"]["formal_matrix_sha256"],
                "candidate_matrix_sha256": sidecar["candidate"]["candidate_matrix_sha256"],
                "promotion_evidence_eligible": bool(sidecar.get("promotion_evidence_eligible")),
                "market_input_kind": "INDEPENDENT_PROVIDER_CONSENSUS_PLUS_KAMBI_MULTILINE" if sidecar.get("promotion_evidence_eligible") else "SINGLE_PROVIDER_MULTILINE_DIAGNOSTIC",
                "actual_score": {"home_goals": hg, "away_goals": ag},
                "result_source_record_id": source_record,
                "projection_audit": sidecar["candidate"].get("projection_audit"),
                "formal_metrics": metrics(formal_matrix, hg, ag),
                "candidate_metrics": metrics(candidate_matrix, hg, ag),
                "governance": {
                    "stored_formal_matrix_only": True,
                    "stored_candidate_matrix_only": True,
                    "postmatch_reprojection": False,
                    "postmatch_formal_prior_recalculation": False,
                    "formal_probability_change": False,
                },
            }
            payload = {**payload_without_hash, "outcome_sha256": sha256_json(payload_without_hash)}
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["new_results_settled"] += 1
        except Exception:
            stats["settlement_exception"] += 1
    return dict(sorted(stats.items()))


def bootstrap(rows: list[dict[str, Any]], metric: str, lower_better: bool, draws: int = 2000, block_size: int = 20) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (str(r.get("kickoff_utc")), str(r.get("match_key"))))
    blocks = [ordered[i:i + block_size] for i in range(0, len(ordered), block_size)]
    point = mean(float(r["candidate_metrics"][metric]) - float(r["formal_metrics"][metric]) for r in ordered)
    rng = random.Random(6852026 + sum(ord(c) for c in metric))
    values = []
    for _ in range(draws):
        sample = []
        for _j in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        values.append(mean(float(r["candidate_metrics"][metric]) - float(r["formal_metrics"][metric]) for r in sample))
    values.sort()
    return {
        "candidate_minus_formal": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "direction": "lower_better" if lower_better else "higher_better",
        "blocks": len(blocks), "draws": draws,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "competitions_represented": 0, "by_competition": {}, "formal": {}, "candidate": {}, "candidate_minus_formal": {}, "bootstrap": {}}
    comps = Counter(str(r["competition_id"]) for r in rows)
    formal = {m: mean(float(r["formal_metrics"][m]) for r in rows) for m in METRICS}
    candidate = {m: mean(float(r["candidate_metrics"][m]) for r in rows) for m in METRICS}
    delta = {m: candidate[m] - formal[m] for m in METRICS}
    boots = {m: bootstrap(rows, m, m in LOWER_BETTER) for m in METRICS} if len(rows) >= 120 else {}
    return {
        "count": len(rows), "competitions_represented": len(comps), "by_competition": dict(sorted(comps.items())),
        "formal": formal, "candidate": candidate, "candidate_minus_formal": delta, "bootstrap": boots,
    }


def aggregate(cfg: dict[str, Any], settlement_scan: dict[str, int]) -> dict[str, Any]:
    rows = []
    invalid = []
    for path in sorted(OUTCOME_ROOT.glob("*.json")) if OUTCOME_ROOT.exists() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            recorded = row.get("outcome_sha256")
            body = {k: v for k, v in row.items() if k != "outcome_sha256"}
            if recorded != sha256_json(body) or row.get("status") != "SCORED_FROZEN_ROW":
                invalid.append(str(path.relative_to(ROOT)))
                continue
            rows.append(row)
        except Exception:
            invalid.append(str(path.relative_to(ROOT)))
    promotion = [r for r in rows if r.get("promotion_evidence_eligible")]
    all_summary = summarize(rows)
    promotion_summary = summarize(promotion)
    gates = cfg["forward_gates"]
    minimums = (
        len(rows) >= int(gates["minimum_settled_all"])
        and len(promotion) >= int(gates["minimum_promotion_eligible_settled"])
        and int(promotion_summary["competitions_represented"]) >= int(gates["minimum_competitions"])
    )
    point_checks = {}
    for metric in gates["proper_score_nonworse_required"]:
        point_checks[metric] = bool(promotion) and float(promotion_summary["candidate_minus_formal"].get(metric, 1.0)) <= 0.0
    for metric in gates["hit_metric_nonworse_required"]:
        point_checks[metric] = bool(promotion) and float(promotion_summary["candidate_minus_formal"].get(metric, -1.0)) >= 0.0
    bootstrap_checks = {}
    if len(promotion) >= 120:
        for metric in gates["proper_score_nonworse_required"]:
            item = promotion_summary["bootstrap"][metric]
            bootstrap_checks[metric] = float(item["ci95_upper"]) <= 0.0
    strict_proper_improvement = bool(bootstrap_checks) and any(bootstrap_checks.values())
    promotion_gate = bool(minimums and all(point_checks.values()) and strict_proper_improvement and not invalid)
    if not rows:
        evaluation_status = "PENDING_NO_SETTLED_FORWARD_MATCHES"
    elif not minimums:
        evaluation_status = "PENDING_MINIMUM_SAMPLE"
    elif promotion_gate:
        evaluation_status = "FORWARD_GATE_PASS_REQUIRES_MANUAL_REVIEW"
    else:
        evaluation_status = "FORWARD_GATE_FAIL"
    return {
        "schema_version": "V6.8.5-multiline-joint-matrix-forward-evaluation-r1",
        "generated_at_utc": utc_now().isoformat(),
        "status": "PASS" if not invalid else "WARN_INVALID_OUTCOME_FILES",
        "evaluation_status": evaluation_status,
        "epoch_freeze_timestamp_utc": cfg["epoch_freeze_timestamp_utc"],
        "settlement_scan": settlement_scan,
        "settled_count": len(rows),
        "promotion_eligible_settled_count": len(promotion),
        "invalid_outcome_files": invalid,
        "all_frozen_rows": all_summary,
        "promotion_eligible_rows": promotion_summary,
        "minimum_sample_gate_met": minimums,
        "point_nonworse_checks": point_checks,
        "proper_score_bootstrap_nonworse_checks": bootstrap_checks,
        "at_least_one_strict_proper_score_improvement": strict_proper_improvement,
        "promotion_gate_passed": promotion_gate,
        "governance": cfg["governance"],
    }


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    settlement_scan = settle(cfg)
    payload = aggregate(cfg, settlement_scan)
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] in {"PASS", "WARN_INVALID_OUTCOME_FILES"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
