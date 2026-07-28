#!/usr/bin/env python3
"""V6.49.9 audit F05 1X2 vs F06 matrix result-marginal coherence.

Read-only diagnostic. Joins immutable V6.49.2 selector and matrix predictions by match_id.
It does not change probabilities, selections, matrices, thresholds, weights, or CURRENT.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEL = ROOT / "forward" / "v6_fresh_selector_events_v6492.json"
MAT = ROOT / "forward" / "v6_fresh_matrix_events_v6492.json"
FRESH = ROOT / "manifests" / "v6_fresh_market_forward_challengers_v6492_status.json"
OUT = ROOT / "manifests" / "v6_joint_surface_coherence_v6499_status.json"
D = ("home", "draw", "away")
TOL = 1e-9


def load(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"not object: {path}")
    return x


def argmax(p: dict[str, float]) -> str:
    return max(D, key=lambda d: float(p[d]))


def delta(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    diffs = {d: abs(float(a[d]) - float(b[d])) for d in D}
    return {"max_abs": max(diffs.values()), "l1": sum(diffs.values()), **{f"abs_{d}": diffs[d] for d in D}}


def summarize(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    max_abs = [float(r[f"{prefix}_delta"]["max_abs"]) for r in rows]
    l1 = [float(r[f"{prefix}_delta"]["l1"]) for r in rows]
    agreements = [bool(r[f"{prefix}_top1_agree"]) for r in rows]
    return {
        "count": len(rows),
        "top1_agree_count": sum(agreements),
        "top1_agree_rate": sum(agreements) / len(rows),
        "mean_max_abs_probability_delta": statistics.fmean(max_abs),
        "max_abs_probability_delta": max(max_abs),
        "mean_l1_probability_delta": statistics.fmean(l1),
        "max_l1_probability_delta": max(l1),
    }


def main() -> int:
    fresh = load(FRESH)
    sel = load(SEL)
    mat = load(MAT)
    if fresh.get("status") != "PASS":
        raise RuntimeError("fresh status not PASS")

    se = {str(e.get("match_id")): e for e in (sel.get("events") or []) if isinstance(e, dict) and e.get("event_type") == "SELECTOR_PREDICTION_FROZEN"}
    me = {str(e.get("match_id")): e for e in (mat.get("events") or []) if isinstance(e, dict) and e.get("event_type") == "MATRIX_PREDICTION_FROZEN"}
    ids = sorted(set(se) & set(me))
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    for mid in ids:
        sp = (se[mid].get("payload") or {})
        mp = (me[mid].get("payload") or {})
        fi = sp.get("fixture_identity") or {}
        pred = sp.get("prediction") or {}
        selector = sp.get("selector") or {}
        one = {d: float((pred.get("probabilities") or {})[d]) for d in D}
        cand = {d: float((((mp.get("candidate") or {}).get("result") or {})[d])) for d in D}
        ref = {d: float((((mp.get("reference") or {}).get("result") or {})[d])) for d in D}
        if abs(sum(one.values()) - 1.0) > TOL or abs(sum(cand.values()) - 1.0) > TOL or abs(sum(ref.values()) - 1.0) > TOL:
            errors.append(f"probability_sum:{mid}")
        cd = delta(one, cand)
        rd = delta(one, ref)
        rows.append({
            "match_id": mid,
            "competition_id": fi.get("competition_id"),
            "kickoff_at": fi.get("kickoff_at"),
            "home_team": fi.get("home_team"),
            "away_team": fi.get("away_team"),
            "active_selector_selected": bool(selector.get("selected") is True),
            "selector_reliability": selector.get("reliability_score"),
            "f05_1x2": one,
            "f05_top1": argmax(one),
            "f06_candidate_result": cand,
            "f06_candidate_top1": argmax(cand),
            "candidate_delta": cd,
            "candidate_top1_agree": argmax(one) == argmax(cand),
            "f06_reference_result": ref,
            "f06_reference_top1": argmax(ref),
            "reference_delta": rd,
            "reference_top1_agree": argmax(one) == argmax(ref),
        })

    selected = [r for r in rows if r["active_selector_selected"]]
    expected_sel = int((fresh.get("selector_ledger_audit") or {}).get("event_count") or 0)
    expected_mat = int((fresh.get("matrix_ledger_audit") or {}).get("event_count") or 0)
    checks = {
        "fresh_status_pass": fresh.get("status") == "PASS",
        "selector_matrix_prediction_counts_equal": expected_sel == expected_mat,
        "all_predictions_joined": len(rows) == expected_sel == expected_mat,
        "probability_sums_valid": not errors,
        "no_probability_or_matrix_mutation": True,
    }
    formal_ready_candidate = bool(rows) and all(r["candidate_delta"]["max_abs"] <= TOL for r in rows)
    payload = {
        "schema_version": "V6.49.9-joint-surface-coherence-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "prediction_count": len(rows),
        "active_selected_count": len(selected),
        "candidate_vs_f05": summarize(rows, "candidate"),
        "reference_vs_f05": summarize(rows, "reference"),
        "active_selected_candidate_vs_f05": summarize(selected, "candidate"),
        "direction_conflicts_candidate": dict(sorted(Counter(f"{r['f05_top1']}->{r['f06_candidate_top1']}" for r in rows if not r["candidate_top1_agree"]).items())),
        "formal_joint_surface_ready_without_coordination": formal_ready_candidate,
        "audit": {"checks": checks, "errors": errors},
        "fixtures": rows,
        "interpretation": (
            "A PASS status means the audit executed consistently, not that F05 and F06 are coherent. "
            "Formal joint-surface readiness requires the same final matrix to reproduce the active 1X2 marginal; "
            "material deltas are therefore a coordination gap, not a reason to average probabilities manually."
        ),
        "governance": {
            "audit_only": True,
            "probability_mutation": False,
            "matrix_mutation": False,
            "selector_mutation": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "prediction_count": len(rows),
        "candidate_vs_f05": payload["candidate_vs_f05"],
        "active_selected_candidate_vs_f05": payload["active_selected_candidate_vs_f05"],
        "direction_conflicts_candidate": payload["direction_conflicts_candidate"],
        "formal_joint_surface_ready_without_coordination": formal_ready_candidate,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
