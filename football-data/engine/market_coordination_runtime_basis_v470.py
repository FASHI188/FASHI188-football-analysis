#!/usr/bin/env python3
"""Non-redundant synchronized-market constraint basis for V4.7 KL coordination.

A full 1X2 surface and a zero/quarter Asian handicap surface contain overlapping
information about side strength. De-vigging each market independently can make
four exact constraints mutually inconsistent. The formal candidate therefore uses
one independent constraint from each market family:

- 1X2 draw probability;
- AH home fair-settlement equation;
- OU over fair-settlement equation.

Home/away 1X2 probabilities are retained as fit diagnostics. This keeps all three
market families in the optimization while avoiding redundant hard constraints.
"""
from __future__ import annotations

from typing import Any

import market_coordination_runtime_v470 as base
from platform_core import PlatformError, derive_score_marginals, settle_home_handicap, settle_over_total


def _market_constraints_nonredundant(
    snapshot: dict[str, Any],
    matrix: list[dict[str, Any]],
) -> tuple[list[list[float]], list[float], list[dict[str, Any]]]:
    one = snapshot.get("one_x_two") or {}
    ah = snapshot.get("asian_handicap") or {}
    ou = snapshot.get("total_goals") or {}
    if not isinstance(ah.get("line"), (int, float)) or not isinstance(ou.get("line"), (int, float)):
        raise PlatformError("complete market coordination requires explicit AH and OU lines")

    one_fair = base._three_way_no_vig(
        base._valid_odds(one.get("home")),
        base._valid_odds(one.get("draw")),
        base._valid_odds(one.get("away")),
    )
    ah_home, _ = base._two_way_no_vig(base._valid_odds(ah.get("home")), base._valid_odds(ah.get("away")))
    ou_over, _ = base._two_way_no_vig(base._valid_odds(ou.get("over")), base._valid_odds(ou.get("under")))
    ah_line = float(ah["line"])
    ou_line = float(ou["line"])

    features: list[list[float]] = []
    for cell in matrix:
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        ah_settlement = settle_home_handicap(home, away, ah_line)
        ou_settlement = settle_over_total(home, away, ou_line)
        features.append([
            1.0 if home == away else 0.0,
            (1.0 - ah_home) * ah_settlement["win"] - ah_home * ah_settlement["loss"],
            (1.0 - ou_over) * ou_settlement["win"] - ou_over * ou_settlement["loss"],
        ])

    targets = [one_fair["draw"], 0.0, 0.0]
    constraints = [
        {
            "name": "1X2_DRAW",
            "target": one_fair["draw"],
            "type": "marginal_probability",
            "basis_role": "independent_1x2_constraint",
        },
        {
            "name": "AH_HOME_FAIR_SETTLEMENT",
            "line": ah_line,
            "target": 0.0,
            "de_vig_home_cover_share": ah_home,
            "type": "linear_fair_settlement_constraint",
            "basis_role": "independent_side_strength_constraint",
        },
        {
            "name": "OU_OVER_FAIR_SETTLEMENT",
            "line": ou_line,
            "target": 0.0,
            "de_vig_over_share": ou_over,
            "type": "linear_fair_settlement_constraint",
            "basis_role": "independent_total_goals_constraint",
        },
    ]
    return features, targets, constraints


def _full_market_fit_diagnostics(matrix: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
    one = snapshot.get("one_x_two") or {}
    fair = base._three_way_no_vig(
        base._valid_odds(one.get("home")),
        base._valid_odds(one.get("draw")),
        base._valid_odds(one.get("away")),
    )
    observed = derive_score_marginals(matrix)["1x2"]
    residuals = {key: float(observed[key]) - float(fair[key]) for key in ("home", "draw", "away")}
    return {
        "target_de_vig_one_x_two": fair,
        "coordinated_one_x_two": observed,
        "one_x_two_residuals": residuals,
        "max_abs_one_x_two_residual": max(abs(value) for value in residuals.values()),
        "note": "Home/away 1X2 are diagnostics because AH supplies the independent side-strength hard constraint.",
    }


_original_apply = base.apply_market_coordination_runtime


def apply_market_coordination_runtime(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    original_builder = base._market_constraints
    base._market_constraints = _market_constraints_nonredundant
    try:
        output = _original_apply(context, calculation)
    finally:
        base._market_constraints = original_builder

    candidate = output.get("market_coordination_candidate")
    audit = output.get("optimization_audit")
    if isinstance(candidate, dict) and isinstance(audit, dict):
        projected_matrix = None
        # The base runtime intentionally does not expose the candidate matrix. Rebuild
        # diagnostics from the candidate 1X2 summary when matrix is formally applied,
        # otherwise retain target/summary residuals directly.
        target = snapshot = context.get("original_market_snapshot") or {}
        one = snapshot.get("one_x_two") or {}
        fair = base._three_way_no_vig(
            base._valid_odds(one.get("home")),
            base._valid_odds(one.get("draw")),
            base._valid_odds(one.get("away")),
        )
        observed = candidate.get("one_x_two") or {}
        if all(key in observed for key in ("home", "draw", "away")):
            residuals = {key: float(observed[key]) - float(fair[key]) for key in ("home", "draw", "away")}
            audit["market_fit_diagnostics"] = {
                "target_de_vig_one_x_two": fair,
                "coordinated_one_x_two": observed,
                "one_x_two_residuals": residuals,
                "max_abs_one_x_two_residual": max(abs(value) for value in residuals.values()),
                "note": "All three market families entered the non-redundant hard-constraint basis; non-basis 1X2 home/away residuals are reported, not hidden.",
            }
            audit["constraint_basis"] = "1X2_DRAW + AH_FAIR_SETTLEMENT + OU_FAIR_SETTLEMENT"
    return output
