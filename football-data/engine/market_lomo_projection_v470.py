#!/usr/bin/env python3
"""Target-excluded LOMO projections for V4.7 market validation.

LOMO = leave one market out.  The target market is never used as a projection
constraint:
- target 1X2 -> constraints AH + OU
- target AH  -> constraints 1X2 + OU
- target OU  -> constraints 1X2 + AH

This module is deterministic and creates no formal EV permission by itself.
"""
from __future__ import annotations

from typing import Any

from market_coordination_runtime_v470 import (
    _kl_project,
    _settlement_market,
    _three_way_no_vig,
    _two_way_no_vig,
    _valid_odds,
)
from platform_core import (
    PlatformError,
    derive_score_marginals,
    settle_home_handicap,
    settle_over_total,
)

VALID_MARKETS = {"1X2", "AH", "OU"}


def _build_subset_constraints(
    matrix: list[dict[str, Any]],
    snapshot: dict[str, Any],
    markets_used: set[str],
) -> tuple[list[list[float]], list[float], list[dict[str, Any]]]:
    one = snapshot.get("one_x_two") or {}
    ah = snapshot.get("asian_handicap") or {}
    ou = snapshot.get("total_goals") or {}

    one_fair = None
    ah_home = None
    ou_over = None
    ah_line = None
    ou_line = None

    if "1X2" in markets_used:
        one_fair = _three_way_no_vig(
            _valid_odds(one.get("home")),
            _valid_odds(one.get("draw")),
            _valid_odds(one.get("away")),
        )
    if "AH" in markets_used:
        if not isinstance(ah.get("line"), (int, float)):
            raise PlatformError("LOMO AH constraint requires explicit handicap line")
        ah_home, _ = _two_way_no_vig(_valid_odds(ah.get("home")), _valid_odds(ah.get("away")))
        ah_line = float(ah["line"])
    if "OU" in markets_used:
        if not isinstance(ou.get("line"), (int, float)):
            raise PlatformError("LOMO OU constraint requires explicit total line")
        ou_over, _ = _two_way_no_vig(_valid_odds(ou.get("over")), _valid_odds(ou.get("under")))
        ou_line = float(ou["line"])

    features: list[list[float]] = []
    constraint_rows: list[dict[str, Any]] = []
    targets: list[float] = []

    if one_fair is not None:
        constraint_rows.extend([
            {"name": "1X2_HOME", "target": one_fair["home"], "type": "marginal_probability"},
            {"name": "1X2_DRAW", "target": one_fair["draw"], "type": "marginal_probability"},
        ])
        targets.extend([one_fair["home"], one_fair["draw"]])
    if ah_home is not None:
        constraint_rows.append({
            "name": "AH_HOME_FAIR_SETTLEMENT",
            "line": ah_line,
            "target": 0.0,
            "de_vig_home_cover_share": ah_home,
            "type": "linear_fair_settlement_constraint",
        })
        targets.append(0.0)
    if ou_over is not None:
        constraint_rows.append({
            "name": "OU_OVER_FAIR_SETTLEMENT",
            "line": ou_line,
            "target": 0.0,
            "de_vig_over_share": ou_over,
            "type": "linear_fair_settlement_constraint",
        })
        targets.append(0.0)

    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        row: list[float] = []
        if one_fair is not None:
            row.extend([1.0 if home > away else 0.0, 1.0 if home == away else 0.0])
        if ah_home is not None:
            settlement = settle_home_handicap(home, away, float(ah_line))
            row.append((1.0 - ah_home) * settlement["win"] - ah_home * settlement["loss"])
        if ou_over is not None:
            settlement = settle_over_total(home, away, float(ou_line))
            row.append((1.0 - ou_over) * settlement["win"] - ou_over * settlement["loss"])
        features.append(row)

    if not targets:
        raise PlatformError("LOMO projection has no non-target market constraints")
    return features, targets, constraint_rows


def project_lomo_target(
    matrix: list[dict[str, Any]],
    snapshot: dict[str, Any],
    target_market: str,
) -> dict[str, Any]:
    target = str(target_market).upper()
    if target not in VALID_MARKETS:
        raise PlatformError(f"unknown LOMO target market: {target}")
    markets_used = VALID_MARKETS - {target}
    features, targets, constraints = _build_subset_constraints(matrix, snapshot, markets_used)
    prior = [float(cell["probability"]) for cell in matrix]
    q, solver = _kl_project(prior, features, targets)
    projected = [{**cell, "probability": probability} for cell, probability in zip(matrix, q)]
    marginals = derive_score_marginals(projected)

    target_prediction: dict[str, Any]
    if target == "1X2":
        target_prediction = {"one_x_two": marginals["1x2"]}
    elif target == "AH":
        market = snapshot.get("asian_handicap") or {}
        if not isinstance(market.get("line"), (int, float)):
            raise PlatformError("LOMO AH target requires explicit target handicap line")
        line = float(market["line"])
        target_prediction = {
            "home_handicap": {"line": line, **_settlement_market(projected, line, settle_home_handicap)}
        }
    else:
        market = snapshot.get("total_goals") or {}
        if not isinstance(market.get("line"), (int, float)):
            raise PlatformError("LOMO OU target requires explicit target total line")
        line = float(market["line"])
        target_prediction = {
            "over_total": {"line": line, **_settlement_market(projected, line, settle_over_total)}
        }

    return {
        "target_market": target,
        "markets_used_for_projection": sorted(markets_used),
        "target_excluded": target not in markets_used,
        "status": "PASS" if solver["converged"] else "FAIL",
        "projected_matrix": projected,
        "target_prediction": target_prediction,
        "audit": {
            "prior_probability_sum": sum(prior),
            "constraints": constraints,
            "objective": "minimize_KL(q||prior)_with_target_market_excluded",
            "converged": solver["converged"],
            "iterations": solver["iterations"],
            "max_constraint_residual": solver["max_constraint_residual"],
            "probability_sum": sum(q),
            "kl_q_to_prior": solver["kl_q_to_prior"],
            "target_market": target,
            "target_excluded": True,
            "markets_used_for_projection": sorted(markets_used),
        },
    }
