#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import validate as validate_snapshot

REGISTRY = ROOT / "config" / "market_matrix_projection_final_registry_v531.json"
EPS = 1e-15
TOL = 1e-10
MAX_ITER = 5000


def _matrix_rows(matrix: Any):
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("formal matrix must be a non-empty list")
    seen = set()
    for index, cell in enumerate(matrix):
        if not isinstance(cell, dict):
            raise ValueError(f"matrix cell {index} must be an object")
        h = int(cell["home_goals"])
        a = int(cell["away_goals"])
        p = float(cell["probability"])
        if h < 0 or a < 0 or not math.isfinite(p) or p < 0.0:
            raise ValueError(f"invalid score cell at {index}")
        if (h, a) in seen:
            raise ValueError(f"duplicate score cell {h}-{a}")
        seen.add((h, a))
        yield h, a, p


def _renormalize(matrix):
    total = sum(float(cell["probability"]) for cell in matrix)
    if total <= 0.0 or not math.isfinite(total):
        raise ValueError("invalid matrix probability sum")
    return [
        {"home_goals": int(c["home_goals"]), "away_goals": int(c["away_goals"]), "probability": float(c["probability"]) / total}
        for c in matrix
    ]


def _outcome_group(h: int, a: int) -> str:
    return "home" if h > a else "draw" if h == a else "away"


def _ou25_group(h: int, a: int) -> str:
    return "over" if h + a >= 3 else "under"


def _devig(values: dict[str, float]) -> dict[str, float]:
    raw = {key: 1.0 / float(value) for key, value in values.items()}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def _scale(matrix, grouper, target, label: str):
    current = defaultdict(float)
    for h, a, p in _matrix_rows(matrix):
        current[grouper(h, a)] += p
    factors = {}
    for key, target_value in target.items():
        mass = float(current.get(key, 0.0))
        if float(target_value) > 0.0 and mass <= 0.0:
            raise ValueError(f"{label} positive target without prior support: {key}")
        factors[key] = float(target_value) / mass if mass > 0.0 else 0.0
    return _renormalize([
        {"home_goals": h, "away_goals": a, "probability": p * factors[grouper(h, a)]}
        for h, a, p in _matrix_rows(matrix)
    ])


def _residual(matrix, one, ou):
    current_one = defaultdict(float)
    current_ou = defaultdict(float)
    for h, a, p in _matrix_rows(matrix):
        current_one[_outcome_group(h, a)] += p
        current_ou[_ou25_group(h, a)] += p
    one_r = max(abs(float(current_one[k]) - float(one[k])) for k in one)
    ou_r = max(abs(float(current_ou[k]) - float(ou[k])) for k in ou)
    return one_r, ou_r, max(one_r, ou_r)


def _kl(candidate, prior):
    prior_map = {(h, a): p for h, a, p in _matrix_rows(prior)}
    value = 0.0
    for h, a, q in _matrix_rows(candidate):
        if q <= 0.0:
            continue
        p = float(prior_map.get((h, a), 0.0))
        if p <= 0.0:
            raise ValueError(f"candidate creates mass outside prior support at {h}-{a}")
        value += q * math.log(q / p)
    return value


def project(prior, one, ou):
    candidate = _renormalize(prior)
    max_residual = math.inf
    one_residual = ou_residual = math.inf
    for iteration in range(1, MAX_ITER + 1):
        candidate = _scale(candidate, _outcome_group, one, "1x2")
        candidate = _scale(candidate, _ou25_group, ou, "ou25")
        one_residual, ou_residual, max_residual = _residual(candidate, one, ou)
        if max_residual <= TOL:
            probability_sum = sum(p for _, _, p in _matrix_rows(candidate))
            return candidate, {
                "method": "minimum_KL_IPF_1x2_plus_ou25",
                "iterations": iteration,
                "converged": True,
                "one_x_two_max_residual": one_residual,
                "ou25_max_residual": ou_residual,
                "max_constraint_residual": max_residual,
                "probability_sum_residual": abs(probability_sum - 1.0),
                "kl_from_formal_matrix": _kl(candidate, prior),
            }
    raise ValueError(f"IPF did not converge after {MAX_ITER}; max_residual={max_residual}")


def evaluate(snapshot: dict[str, Any], formal_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot_validation = validate_snapshot(snapshot)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    competition_id = str(snapshot.get("competition_id") or "")
    candidate_cfg = (registry.get("primary_prospective_architecture_candidates") or {}).get(competition_id)
    result = {
        "schema_version": "V5.3.1-prospective-market-matrix-shadow-evaluation-r1",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": competition_id,
        "snapshot_contract_passed": bool(snapshot_validation.get("passed")),
        "snapshot_errors": snapshot_validation.get("errors") or [],
        "registered_primary_candidate": bool(candidate_cfg),
        "shadow_status": "NO_SHADOW_MATRIX",
        "formal_matrix_override": False,
        "formal_probability_mutation": False,
        "formal_weight": 0,
        "candidate_matrix": None,
    }
    if not snapshot_validation.get("passed"):
        result["shadow_status"] = "SNAPSHOT_INVALID_FAIL_CLOSED"
        return result
    if not candidate_cfg:
        result["shadow_status"] = "DOMAIN_NOT_REGISTERED_PRIMARY_MATRIX_CANDIDATE"
        return result

    ou_surface = snapshot.get("over_under") or {}
    if abs(float(ou_surface.get("line")) - 2.5) > 1e-9:
        result["shadow_status"] = "OU25_REFERENCE_REQUIRED_FOR_FROZEN_PROFILE"
        result["observed_ou_line"] = ou_surface.get("line")
        return result

    one = _devig({key: float(snapshot["one_x_two"][key]) for key in ("home", "draw", "away")})
    ou = _devig({key: float(ou_surface[key]) for key in ("over", "under")})
    candidate, audit = project(formal_matrix, one, ou)
    result.update({
        "shadow_status": "SHADOW_MARKET_MATRIX_READY",
        "frozen_profile": candidate_cfg.get("profile"),
        "de_vigged_1x2_target": one,
        "de_vigged_ou25_target": ou,
        "audit": audit,
        "candidate_matrix": candidate,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", help="V5.2.3 market snapshot JSON")
    parser.add_argument("formal_matrix", help="Current formal score-matrix JSON list")
    parser.add_argument("--out", help="Optional output shadow JSON")
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    formal_matrix = json.loads(Path(args.formal_matrix).read_text(encoding="utf-8"))
    result = evaluate(snapshot, formal_matrix)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["shadow_status"] in {
        "SHADOW_MARKET_MATRIX_READY",
        "DOMAIN_NOT_REGISTERED_PRIMARY_MATRIX_CANDIDATE",
        "OU25_REFERENCE_REQUIRED_FOR_FROZEN_PROFILE",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
