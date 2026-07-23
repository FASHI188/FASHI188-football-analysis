#!/usr/bin/env python3
"""V6.8.2 multi-line market constrained joint-matrix I-projection.

Research-only successor to the hard-coded 1X2+OU2.5 shadow projector.  It keeps an explicit
score-matrix prior, constrains it to synchronized de-vigged 1X2 plus every usable ordinary
full-time half-goal O/U line in a Kambi ladder, and finds the minimum-KL distribution by
iterative proportional fitting (IPF).

Important semantics:
* market-only 0..7+ identifiability is NOT claimed when 0.5..6.5 is incomplete;
* missing tail/detail structure comes from the explicit prior and is labeled as such;
* Asian quarter lines and AH are not treated as binary probabilities here;
* convergence, probability conservation, every market residual and KL from prior are emitted;
* no formal/current/runtime probability is changed by this research implementation.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

EPS = 1e-15
TOL = 1e-10
MAX_ITER = 5000


def rows(matrix: list[dict[str, Any]]):
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("prior matrix must be a non-empty list")
    seen = set()
    for index, cell in enumerate(matrix):
        h, a, p = int(cell["home_goals"]), int(cell["away_goals"]), float(cell["probability"])
        if h < 0 or a < 0 or not math.isfinite(p) or p < 0:
            raise ValueError(f"invalid prior cell {index}")
        if (h, a) in seen:
            raise ValueError(f"duplicate prior cell {h}-{a}")
        seen.add((h, a))
        yield h, a, p


def renorm(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(p for _h, _a, p in rows(matrix))
    if total <= 0 or not math.isfinite(total):
        raise ValueError("invalid probability sum")
    return [{"home_goals": h, "away_goals": a, "probability": p / total} for h, a, p in rows(matrix)]


def devig(values: dict[str, float]) -> dict[str, float]:
    inverse = {key: 1.0 / float(price) for key, price in values.items()}
    total = sum(inverse.values())
    if total <= 0:
        raise ValueError("invalid market prices")
    return {key: value / total for key, value in inverse.items()}


def half_line(line: float) -> bool:
    return abs((float(line) - 0.5) - round(float(line) - 0.5)) <= 1e-9


def outcome_group(h: int, a: int) -> str:
    return "home" if h > a else "draw" if h == a else "away"


def total_group(line: float) -> Callable[[int, int], str]:
    threshold = math.floor(float(line))
    return lambda h, a: "under" if h + a <= threshold else "over"


def scale_partition(matrix: list[dict[str, Any]], grouper: Callable[[int, int], str], target: dict[str, float], label: str) -> list[dict[str, Any]]:
    current = defaultdict(float)
    for h, a, p in rows(matrix):
        current[grouper(h, a)] += p
    factors: dict[str, float] = {}
    for key, wanted in target.items():
        mass = float(current.get(key, 0.0))
        if wanted > 0 and mass <= 0:
            raise ValueError(f"{label}: target {key} has no prior support")
        factors[key] = float(wanted) / mass if mass > 0 else 0.0
    return renorm([{"home_goals": h, "away_goals": a, "probability": p * factors[grouper(h, a)]} for h, a, p in rows(matrix)])


def marginal(matrix: list[dict[str, Any]], grouper: Callable[[int, int], str]) -> dict[str, float]:
    out = defaultdict(float)
    for h, a, p in rows(matrix):
        out[grouper(h, a)] += p
    return dict(out)


def max_residual(current: dict[str, float], target: dict[str, float]) -> float:
    return max(abs(float(current.get(key, 0.0)) - float(value)) for key, value in target.items())


def kl(candidate: list[dict[str, Any]], prior: list[dict[str, Any]]) -> float:
    base = {(h, a): p for h, a, p in rows(prior)}
    value = 0.0
    for h, a, q in rows(candidate):
        if q <= 0:
            continue
        p = float(base.get((h, a), 0.0))
        if p <= 0:
            raise ValueError(f"candidate creates mass outside prior support at {h}-{a}")
        value += q * math.log(q / p)
    return value


def select_1x2(bundle: dict[str, Any]) -> dict[str, float]:
    offers = bundle.get("one_x_two_offers") or []
    if not offers:
        raise ValueError("bundle has no 1X2 offer")
    offer = next((row for row in offers if row.get("main_line")), offers[0])
    return devig({key: float(offer[key]) for key in ("home", "draw", "away")})


def total_targets(bundle: dict[str, Any]) -> list[tuple[float, dict[str, float]]]:
    candidates = [row for row in bundle.get("total_goal_ladder") or [] if row.get("market_kind") == "total_goals" and half_line(float(row.get("line")))]
    by_line: dict[float, dict[str, Any]] = {}
    for row in candidates:
        line = float(row["line"])
        if line not in by_line or bool(row.get("main_line")):
            by_line[line] = row
    result = []
    for line, row in sorted(by_line.items()):
        result.append((line, devig({"over": float(row["over"]), "under": float(row["under"])})))
    return result


def total_distribution(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {key: 0.0 for key in ["0", "1", "2", "3", "4", "5", "6", "7+"]}
    for h, a, p in rows(matrix):
        total = h + a
        key = str(total) if total <= 6 else "7+"
        out[key] += p
    return out


def score_diagnostics(matrix: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted([(p, h, a) for h, a, p in rows(matrix)], reverse=True)
    entropy = -sum(p * math.log(max(EPS, p)) for p, _h, _a in ranked if p > 0)
    top1 = ranked[0]
    top2 = ranked[1] if len(ranked) > 1 else (0.0, None, None)
    return {
        "top1": {"home_goals": top1[1], "away_goals": top1[2], "probability": top1[0]},
        "top2": {"home_goals": top2[1], "away_goals": top2[2], "probability": top2[0]},
        "top1_top2_gap": top1[0] - top2[0],
        "top3_cumulative": sum(item[0] for item in ranked[:3]),
        "entropy": entropy,
    }


def project(prior: list[dict[str, Any]], bundle: dict[str, Any]) -> dict[str, Any]:
    prior = renorm(prior)
    one = select_1x2(bundle)
    totals = total_targets(bundle)
    if len(totals) < 2:
        return {"status": "INSUFFICIENT_MULTILINE_TOTAL_CONTEXT", "total_half_line_count": len(totals)}
    candidate = prior
    for iteration in range(1, MAX_ITER + 1):
        candidate = scale_partition(candidate, outcome_group, one, "1x2")
        for line, target in totals:
            candidate = scale_partition(candidate, total_group(line), target, f"OU{line:g}")
        one_residual = max_residual(marginal(candidate, outcome_group), one)
        total_residuals = {str(line): max_residual(marginal(candidate, total_group(line)), target) for line, target in totals}
        worst = max([one_residual, *total_residuals.values()])
        if worst <= TOL:
            p_sum = sum(p for _h, _a, p in rows(candidate))
            return {
                "status": "MULTILINE_MARKET_MATRIX_READY",
                "method": "minimum_KL_IPF_1x2_plus_multiple_half_goal_totals",
                "objective": "minimize_KL(candidate||explicit_prior)_subject_to_market_marginals",
                "iterations": iteration,
                "converged": True,
                "de_vigged_1x2_target": one,
                "de_vigged_total_targets": {str(line): target for line, target in totals},
                "one_x_two_max_residual": one_residual,
                "total_line_max_residuals": total_residuals,
                "max_constraint_residual": worst,
                "probability_sum_residual": abs(p_sum - 1.0),
                "kl_from_prior": kl(candidate, prior),
                "total_goals_distribution_source": "EXPLICIT_PRIOR_PLUS_SYNCHRONIZED_MARKET_I_PROJECTION",
                "total_goals_distribution": total_distribution(candidate),
                "score_diagnostics": score_diagnostics(candidate),
                "candidate_matrix": candidate,
                "asian_handicap_role": "CROSSCHECK_ONLY_NOT_CONSTRAINED",
            }
    return {"status": "IPF_NONCONVERGENCE", "iterations": MAX_ITER}


def find_bundle(payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    if "bundles" not in payload:
        return payload
    for bundle in payload.get("bundles") or []:
        if str(bundle.get("event_id")) == str(event_id):
            return bundle
    raise KeyError(f"event_id {event_id} not found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ladder_json")
    parser.add_argument("formal_matrix_json")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    ladder_payload = json.loads(Path(args.ladder_json).read_text(encoding="utf-8"))
    prior = json.loads(Path(args.formal_matrix_json).read_text(encoding="utf-8"))
    bundle = find_bundle(ladder_payload, args.event_id)
    result = project(prior, bundle)
    result.update({
        "schema_version": "V6.8.2-multiline-market-matrix-projection-r1",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "event_id": bundle.get("event_id"),
        "observed_at_utc": bundle.get("observed_at_utc"),
        "research_only": True,
        "formal_probability_change": False,
        "current_rule_change": False,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("status") == "MULTILINE_MARKET_MATRIX_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
