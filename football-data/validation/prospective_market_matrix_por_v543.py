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

CFG = ROOT / "config" / "market_matrix_projection_por_v543.json"
TOL = 1e-10


def _rows(matrix: Any):
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("formal matrix must be non-empty")
    seen = set()
    for cell in matrix:
        h = int(cell["home_goals"]); a = int(cell["away_goals"]); p = float(cell["probability"])
        if h < 0 or a < 0 or not math.isfinite(p) or p < 0.0 or (h, a) in seen:
            raise ValueError("invalid or duplicate score cell")
        seen.add((h, a))
        yield h, a, p


def _group(h: int, a: int) -> str:
    return "home" if h > a else "draw" if h == a else "away"


def _devig(odds: dict[str, float]) -> dict[str, float]:
    raw = {k: 1.0 / float(v) for k, v in odds.items()}
    z = sum(raw.values())
    if z <= 0.0:
        raise ValueError("invalid 1X2 odds")
    return {k: v / z for k, v in raw.items()}


def project(prior, target):
    current = defaultdict(float)
    for h, a, p in _rows(prior):
        current[_group(h, a)] += p
    factors = {}
    for key, value in target.items():
        mass = current[key]
        if value > 0.0 and mass <= 0.0:
            raise ValueError(f"positive target without prior support: {key}")
        factors[key] = value / mass if mass > 0.0 else 0.0
    candidate = [
        {"home_goals": h, "away_goals": a, "probability": p * factors[_group(h, a)]}
        for h, a, p in _rows(prior)
    ]
    z = sum(c["probability"] for c in candidate)
    candidate = [{**c, "probability": c["probability"] / z} for c in candidate]
    achieved = defaultdict(float)
    prior_map = {(h, a): p for h, a, p in _rows(prior)}
    kl = 0.0
    for h, a, q in _rows(candidate):
        achieved[_group(h, a)] += q
        if q > 0.0:
            p = prior_map[(h, a)]
            if p <= 0.0:
                raise ValueError("candidate creates mass outside prior support")
            kl += q * math.log(q / p)
    residual = max(abs(achieved[k] - target[k]) for k in target)
    psum = sum(p for _h, _a, p in _rows(candidate))
    if residual > TOL or abs(psum - 1.0) > TOL:
        raise ValueError(f"projection audit failed residual={residual} psum={psum}")
    return candidate, {
        "method": "minimum_KL_partition_projection_1X2",
        "iterations": 1,
        "converged": True,
        "market_constraint_residual": residual,
        "probability_sum_residual": abs(psum - 1.0),
        "kl_from_formal_matrix": kl,
    }


def evaluate(snapshot: dict[str, Any], formal_matrix):
    validation = validate_snapshot(snapshot)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    result = {
        "schema_version": "V5.4.3-POR-prospective-market-matrix-shadow-evaluation-r1",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": snapshot.get("competition_id"),
        "snapshot_contract_passed": bool(validation.get("passed")),
        "snapshot_errors": validation.get("errors") or [],
        "shadow_status": "NO_SHADOW_MATRIX",
        "formal_matrix_override": False,
        "formal_probability_mutation": False,
        "formal_weight": 0,
        "candidate_matrix": None,
    }
    if not validation.get("passed"):
        result["shadow_status"] = "SNAPSHOT_INVALID_FAIL_CLOSED"
        return result
    if snapshot.get("competition_id") != cfg["competition_id"]:
        result["shadow_status"] = "DOMAIN_NOT_REGISTERED_POR_CANDIDATE"
        return result
    one = _devig({k: float(snapshot["one_x_two"][k]) for k in ("home", "draw", "away")})
    candidate, audit = project(formal_matrix, one)
    result.update({
        "shadow_status": "SHADOW_MARKET_MATRIX_READY",
        "frozen_profile": cfg["profile"],
        "de_vigged_1x2_target": one,
        "audit": audit,
        "candidate_matrix": candidate,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("formal_matrix")
    parser.add_argument("--out")
    args = parser.parse_args()
    result = evaluate(
        json.loads(Path(args.snapshot).read_text(encoding="utf-8")),
        json.loads(Path(args.formal_matrix).read_text(encoding="utf-8")),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        p = Path(args.out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["shadow_status"] in {"SHADOW_MARKET_MATRIX_READY", "DOMAIN_NOT_REGISTERED_POR_CANDIDATE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
