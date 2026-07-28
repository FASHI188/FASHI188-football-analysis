#!/usr/bin/env python3
"""V6.49.8 independent structural audit for current V6.49.2 fresh matrices.

Audit-only. Recomputes matrix invariants from the immutable fresh matrix ledger and reports
current total-goal / exact-score concentration diagnostics. It never changes a probability,
pick, threshold, matrix, formal weight, or CURRENT.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "forward" / "v6_fresh_matrix_events_v6492.json"
FRESH = ROOT / "manifests" / "v6_fresh_market_forward_challengers_v6492_status.json"
OUT = ROOT / "manifests" / "v6_fresh_matrix_structural_audit_v6498_status.json"
TOL = 1e-9


def load(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"not object: {path}")
    return x


def score_key(raw: str) -> tuple[int, int]:
    a, b = str(raw).split("-", 1)
    h, aw = int(a), int(b)
    if h < 0 or aw < 0:
        raise ValueError(raw)
    return h, aw


def audit_surface(surface: dict[str, Any], label: str) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    total = surface.get("total") or {}
    result = surface.get("result") or {}
    matrix = surface.get("score_matrix") or {}
    top10 = surface.get("score_top10") or []
    tail = float(surface.get("tail15plus_probability") or 0.0)

    total_keys = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    if any(k not in total for k in total_keys):
        errors.append(f"{label}:missing_total_bucket")
    total_sum = sum(float(total.get(k) or 0.0) for k in total_keys)
    if abs(total_sum - 1.0) > TOL:
        errors.append(f"{label}:total_sum={total_sum}")

    result_sum = sum(float(result.get(k) or 0.0) for k in ("home", "draw", "away"))
    if abs(result_sum - 1.0) > TOL:
        errors.append(f"{label}:result_sum={result_sum}")

    matrix_sum = 0.0
    reconstructed = {str(i): 0.0 for i in range(7)}
    reconstructed["7+"] = 0.0
    values: list[tuple[str, float]] = []
    for key, raw_p in matrix.items():
        try:
            h, a = score_key(str(key))
            p = float(raw_p)
        except Exception:
            errors.append(f"{label}:bad_score_cell={key}")
            continue
        if not math.isfinite(p) or p < -TOL:
            errors.append(f"{label}:bad_probability={key}:{p}")
            continue
        p = max(0.0, p)
        matrix_sum += p
        t = h + a
        reconstructed[str(t) if t <= 6 else "7+"] += p
        values.append((str(key), p))
    reconstructed["7+"] += tail

    matrix_plus_tail = matrix_sum + tail
    if abs(matrix_plus_tail - 1.0) > TOL:
        errors.append(f"{label}:matrix_plus_tail={matrix_plus_tail}")
    for k in total_keys:
        if abs(reconstructed[k] - float(total.get(k) or 0.0)) > TOL:
            errors.append(f"{label}:total_reconstruction:{k}:{reconstructed[k]}:{total.get(k)}")

    expected_top = sorted(values, key=lambda kv: (-kv[1], kv[0]))[:10]
    supplied_top = [(str(x.get("score")), float(x.get("probability"))) for x in top10 if isinstance(x, dict)]
    if len(supplied_top) != min(10, len(values)):
        errors.append(f"{label}:top10_length={len(supplied_top)}")
    else:
        for i, ((ek, ep), (sk, sp)) in enumerate(zip(expected_top, supplied_top)):
            if ek != sk or abs(ep - sp) > TOL:
                errors.append(f"{label}:top10_mismatch_at_{i}:{ek}:{ep}:{sk}:{sp}")
                break

    embedded = surface.get("audit") or {}
    if abs(float(embedded.get("probability_sum_residual") or 0.0)) > TOL:
        errors.append(f"{label}:embedded_probability_residual")
    if abs(float(embedded.get("internal_total_reconstruction_residual") or 0.0)) > TOL:
        errors.append(f"{label}:embedded_total_residual")
    if int(embedded.get("parity_mapping_errors") or 0) != 0:
        errors.append(f"{label}:embedded_parity_errors")
    if abs(float(embedded.get("result_sum_residual") or 0.0)) > TOL:
        errors.append(f"{label}:embedded_result_residual")

    top1 = expected_top[0] if expected_top else (None, None)
    top2 = expected_top[1] if len(expected_top) > 1 else (None, None)
    mode_total = max(total_keys, key=lambda k: float(total.get(k) or 0.0)) if total else None
    return errors, {
        "total_sum_residual": abs(total_sum - 1.0),
        "result_sum_residual": abs(result_sum - 1.0),
        "matrix_plus_tail_residual": abs(matrix_plus_tail - 1.0),
        "max_total_reconstruction_residual": max(abs(reconstructed[k] - float(total.get(k) or 0.0)) for k in total_keys),
        "tail15plus_probability": tail,
        "total_mode": mode_total,
        "total_mode_probability": float(total.get(mode_total) or 0.0) if mode_total else None,
        "total_2_3_mass": float(total.get("2") or 0.0) + float(total.get("3") or 0.0),
        "score_top1": top1[0],
        "score_top1_probability": top1[1],
        "score_top2": top2[0],
        "score_top2_probability": top2[1],
        "score_top1_top2_gap": (top1[1] - top2[1]) if top1[1] is not None and top2[1] is not None else None,
    }


def main() -> int:
    fresh = load(FRESH)
    ledger = load(LEDGER)
    if fresh.get("status") != "PASS":
        raise RuntimeError("fresh V6.49.2 status not PASS")

    events = [e for e in (ledger.get("events") or []) if isinstance(e, dict) and e.get("event_type") == "MATRIX_PREDICTION_FROZEN"]
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    for e in events:
        p = e.get("payload") or {}
        fi = p.get("fixture_identity") or {}
        cand = p.get("candidate") or {}
        ref = p.get("reference") or {}
        ce, cm = audit_surface(cand, f"{e.get('match_id')}:candidate")
        re, rm = audit_surface(ref, f"{e.get('match_id')}:reference")
        errors.extend(ce)
        errors.extend(re)
        # The V6.47.8 challenge alters conditional margin allocation, not direct total P(T).
        ctot = cand.get("total") or {}
        rtot = ref.get("total") or {}
        total_surface_delta = max(abs(float(ctot.get(k) or 0.0) - float(rtot.get(k) or 0.0)) for k in ("0","1","2","3","4","5","6","7+"))
        if total_surface_delta > TOL:
            errors.append(f"{e.get('match_id')}:candidate_reference_total_drift={total_surface_delta}")
        rows.append({
            "match_id": e.get("match_id"),
            "competition_id": fi.get("competition_id"),
            "kickoff_at": fi.get("kickoff_at"),
            "home_team": fi.get("home_team"),
            "away_team": fi.get("away_team"),
            "candidate": cm,
            "reference": rm,
            "candidate_reference_total_max_delta": total_surface_delta,
        })

    expected = int((fresh.get("matrix_ledger_audit") or {}).get("event_count") or 0)
    if len(events) != expected:
        errors.append(f"prediction_count_mismatch:{len(events)}:{expected}")

    candidate_modes = Counter(str(r["candidate"]["total_mode"]) for r in rows)
    candidate_top1 = Counter(str(r["candidate"]["score_top1"]) for r in rows)
    top1_gaps = [float(r["candidate"]["score_top1_top2_gap"]) for r in rows if r["candidate"]["score_top1_top2_gap"] is not None]
    tails = [float(r["candidate"]["tail15plus_probability"]) for r in rows]
    mass23 = [float(r["candidate"]["total_2_3_mass"]) for r in rows]
    top1_probs = [float(r["candidate"]["score_top1_probability"]) for r in rows if r["candidate"]["score_top1_probability"] is not None]

    checks = {
        "fresh_status_pass": fresh.get("status") == "PASS",
        "prediction_count_matches_fresh_ledger": len(events) == expected,
        "all_candidate_and_reference_surfaces_reconstructed": not errors,
        "no_probability_or_matrix_mutation": True,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V6.49.8-fresh-matrix-structural-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": status,
        "prediction_count": len(events),
        "audit": {
            "checks": checks,
            "error_count": len(errors),
            "errors": errors[:100],
            "max_candidate_probability_residual": max((r["candidate"]["matrix_plus_tail_residual"] for r in rows), default=None),
            "max_candidate_total_reconstruction_residual": max((r["candidate"]["max_total_reconstruction_residual"] for r in rows), default=None),
            "max_candidate_reference_total_delta": max((r["candidate_reference_total_max_delta"] for r in rows), default=None),
        },
        "total_goal_diagnostic": {
            "candidate_mode_counts": dict(sorted(candidate_modes.items())),
            "mean_mass_on_2_or_3_goals": statistics.fmean(mass23) if mass23 else None,
            "max_mass_on_2_or_3_goals": max(mass23) if mass23 else None,
            "mean_tail15plus_probability": statistics.fmean(tails) if tails else None,
            "max_tail15plus_probability": max(tails) if tails else None,
        },
        "exact_score_diagnostic": {
            "candidate_top1_score_counts": dict(sorted(candidate_top1.items())),
            "unique_top1_score_count": len(candidate_top1),
            "one_one_top1_count": int(candidate_top1.get("1-1", 0)),
            "one_one_top1_rate": (candidate_top1.get("1-1", 0) / len(rows)) if rows else 0.0,
            "mean_top1_probability": statistics.fmean(top1_probs) if top1_probs else None,
            "mean_top1_top2_gap": statistics.fmean(top1_gaps) if top1_gaps else None,
            "min_top1_top2_gap": min(top1_gaps) if top1_gaps else None,
            "max_top1_top2_gap": max(top1_gaps) if top1_gaps else None,
        },
        "fixtures": rows,
        "governance": {
            "audit_only": True,
            "probability_mutation": False,
            "matrix_mutation": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "prediction_count": len(events),
        "audit": payload["audit"],
        "total_goal_diagnostic": payload["total_goal_diagnostic"],
        "exact_score_diagnostic": payload["exact_score_diagnostic"],
    }, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
