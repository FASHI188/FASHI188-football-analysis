#!/usr/bin/env python3
"""Research-only independent 1X2 link from adaptive latent-strength uncertainty.

The model partitions a continuous latent match-performance difference into away,
draw, and home regions. It consumes no bookmaker probability and does not derive
1X2 from an exact-score matrix. Real-use link parameters MUST come from a frozen
train/policy artifact; this module deliberately provides no production defaults.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class Independent1X2Error(ValueError):
    """Raised when caller input violates the frozen link contract."""


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Independent1X2Error(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise Independent1X2Error(f"{field} must be finite")
    return number


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Independent1X2LinkConfig:
    home_advantage: float
    draw_boundary: float
    match_noise_variance: float

    def validate(self) -> "Independent1X2LinkConfig":
        home_advantage = _finite(self.home_advantage, "home_advantage")
        draw_boundary = _finite(self.draw_boundary, "draw_boundary")
        noise = _finite(self.match_noise_variance, "match_noise_variance")
        if draw_boundary <= 0.0:
            raise Independent1X2Error("draw_boundary must be > 0")
        if noise <= 0.0:
            raise Independent1X2Error("match_noise_variance must be > 0")
        if abs(home_advantage) > 8.0:
            raise Independent1X2Error("home_advantage outside engineering safety bound")
        if draw_boundary > 8.0:
            raise Independent1X2Error("draw_boundary outside engineering safety bound")
        if noise > 100.0:
            raise Independent1X2Error("match_noise_variance outside engineering safety bound")
        return self


def probabilities_from_latent_comparison(
    comparison: dict[str, Any],
    *,
    config: Independent1X2LinkConfig,
) -> dict[str, Any]:
    """Convert latent margin/variance into coherent research-only H/D/A probabilities.

    Let Y be the unobserved match-performance difference. Conditional on the
    latent-strength state, Y is Gaussian with mean latent_margin + home_advantage
    and variance latent_margin_variance + match_noise_variance. Away occurs below
    -draw_boundary, draw inside the interval, and home above +draw_boundary.
    """
    if not isinstance(comparison, dict):
        raise Independent1X2Error("comparison must be dict")
    if comparison.get("interpretation") not in {
        "research_only_latent_direction_not_1x2_probability",
        "synthetic_latent_comparison_v1",
    }:
        raise Independent1X2Error("comparison interpretation is not an approved latent-strength input")
    cfg = config.validate()
    margin = _finite(comparison.get("latent_margin"), "latent_margin")
    latent_variance = _finite(comparison.get("latent_margin_variance"), "latent_margin_variance")
    if latent_variance <= 0.0:
        raise Independent1X2Error("latent_margin_variance must be > 0")

    mean = margin + float(cfg.home_advantage)
    variance = latent_variance + float(cfg.match_noise_variance)
    if not math.isfinite(variance) or variance <= 0.0:
        raise Independent1X2Error("total performance variance is invalid")
    sd = math.sqrt(variance)
    lower_z = (-float(cfg.draw_boundary) - mean) / sd
    upper_z = (float(cfg.draw_boundary) - mean) / sd
    p_away = _normal_cdf(lower_z)
    p_draw = _normal_cdf(upper_z) - p_away
    p_home = 1.0 - _normal_cdf(upper_z)

    probs = [p_home, p_draw, p_away]
    if any((not math.isfinite(p)) or p < -1e-12 or p > 1.0 + 1e-12 for p in probs):
        raise Independent1X2Error("probability calculation left [0,1]")
    probs = [min(1.0, max(0.0, p)) for p in probs]
    total = sum(probs)
    if not math.isfinite(total) or abs(total - 1.0) > 1e-10:
        raise Independent1X2Error(f"probability conservation failure: {total}")
    p_home, p_draw, p_away = [p / total for p in probs]
    standardized_mean = mean / sd

    return {
        "schema": "football3_independent_latent_1x2_v1",
        "home": p_home,
        "draw": p_draw,
        "away": p_away,
        "latent_margin": margin,
        "latent_margin_variance": latent_variance,
        "home_advantage": float(cfg.home_advantage),
        "draw_boundary": float(cfg.draw_boundary),
        "match_noise_variance": float(cfg.match_noise_variance),
        "performance_mean": mean,
        "performance_variance": variance,
        "performance_sd": sd,
        "standardized_performance_mean": standardized_mean,
        "market_input_used": False,
        "score_matrix_used": False,
        "research_only": True,
        "formal_weight": 0.0,
        "interpretation": "research_only_independent_1x2_not_formal_probability",
    }
