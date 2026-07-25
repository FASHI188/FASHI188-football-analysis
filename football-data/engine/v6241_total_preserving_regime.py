#!/usr/bin/env python3
"""V6.24.1 total-preserving regime-state mix.

Research only; formal_weight=0.

The V6.24.0 regime layer improved 1X2 and score probability quality on its
fixed-seed diagnostic, but slightly moved the direct total-goal marginal. This
adapter enforces the intended decomposition:

    baseline direct total P(T)
    + regime-adjusted conditional home/away allocation P(D | T)
    -> one joint score matrix

For the team playing at the relevant venue, its weighted GF/GA composition may
move, but the venue exposure and GF+GA sufficient statistic are held exactly at
the formal baseline. Therefore expected_goals() sees the same venue-total rate
and the same competition total/dispersion, while the attack/defence split can
still change the conditional allocation.
"""
from __future__ import annotations

from typing import Any

from platform_core import PlatformError

EXPERT_COUNT = 4
VALID_ROLES = {"home", "away"}


def _weighted(field: str, records: list[dict[str, Any]], weights: list[float]) -> float:
    return sum(float(w) * float(record.get(field, 0.0)) for w, record in zip(weights, records))


def mix_team_record_total_preserving(
    expert_records: list[dict[str, Any]],
    weights: list[float],
    baseline: dict[str, Any],
    blend_strength: float,
    *,
    role: str,
) -> dict[str, Any]:
    """Change venue attack/defence composition without moving the direct total track."""
    if len(expert_records) != EXPERT_COUNT or len(weights) != EXPERT_COUNT:
        raise PlatformError("V6.24.1 expert record/weight dimension mismatch")
    if abs(sum(float(w) for w in weights) - 1.0) > 1e-9:
        raise PlatformError("V6.24.1 regime weights do not sum to one")
    role = str(role).lower()
    if role not in VALID_ROLES:
        raise PlatformError(f"V6.24.1 invalid venue role: {role}")

    alpha = min(1.0, max(0.0, float(blend_strength)))
    prefix = "home" if role == "home" else "away"
    gf_field = f"{prefix}_gf"
    ga_field = f"{prefix}_ga"
    matches_field = f"{prefix}_matches"

    base_gf = float(baseline.get(gf_field, 0.0))
    base_ga = float(baseline.get(ga_field, 0.0))
    base_total = base_gf + base_ga

    regime_gf = _weighted(gf_field, expert_records, weights)
    regime_ga = _weighted(ga_field, expert_records, weights)
    blended_gf = (1.0 - alpha) * base_gf + alpha * regime_gf
    blended_ga = (1.0 - alpha) * base_ga + alpha * regime_ga
    blended_total = blended_gf + blended_ga

    out = dict(baseline)
    # Keep venue exposure fixed so the direct venue-total denominator cannot move.
    out[matches_field] = float(baseline.get(matches_field, 0.0))
    # Preserve GF+GA exactly; only redistribute its attack/defence composition.
    if base_total <= 1e-12 or blended_total <= 1e-12:
        out[gf_field] = base_gf
        out[ga_field] = base_ga
    else:
        scale = base_total / blended_total
        out[gf_field] = blended_gf * scale
        out[ga_field] = blended_ga * scale

    # Non-relevant venue fields and effective-match exposure stay at baseline.
    # This isolates the challenger to conditional allocation for this fixture.
    return out


def audit_total_preservation(baseline: dict[str, Any], mixed: dict[str, Any], *, role: str) -> dict[str, float | bool]:
    prefix = "home" if role == "home" else "away"
    gf = f"{prefix}_gf"
    ga = f"{prefix}_ga"
    matches = f"{prefix}_matches"
    base_total = float(baseline.get(gf, 0.0)) + float(baseline.get(ga, 0.0))
    mixed_total = float(mixed.get(gf, 0.0)) + float(mixed.get(ga, 0.0))
    base_matches = float(baseline.get(matches, 0.0))
    mixed_matches = float(mixed.get(matches, 0.0))
    residual = abs(base_total - mixed_total)
    exposure_residual = abs(base_matches - mixed_matches)
    return {
        "venue_total_sufficient_stat_residual": residual,
        "venue_exposure_residual": exposure_residual,
        "passed": residual <= 1e-10 and exposure_residual <= 1e-10,
    }


AUDIT = {
    "module": "V6.24.1_TOTAL_PRESERVING_REGIME",
    "formal_weight": 0,
    "runtime_enabled": False,
    "baseline_direct_total_track_preserved": True,
    "regime_changes_conditional_allocation_only": True,
    "single_joint_matrix_only": True,
}
