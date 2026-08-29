"""Governed registry for the existing R43Q/R/T/U/Y research components.

The registry records exact source lineage, historical gate state, migration state
and native output contract. Migration never implies promotion: every legacy
component stays off unless a later explicit governance gate enables it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class LegacyComponentSpec:
    component_id: str
    role: str
    source_branch: str | None
    source_path: str | None
    source_blob_sha: str | None
    native_output: str
    architecture_gate_passed: bool | None
    full_volume_53pct_met: bool | None
    enabled_by_default: bool
    implementation_migrated: bool
    source_resolved: bool
    governance_note: str


SPECS: dict[str, LegacyComponentSpec] = {
    "R43Q": LegacyComponentSpec(
        "R43Q",
        "same_timestamp_1x2_ah_ou_market_score_base",
        "football3/r43q0-sharp-market-score-base",
        "football-data/experiments/r43q0_sharp_market_score_base/run_r43q0.py",
        "299b86ed07e49af0b9ec5c7632f519e91e836158",
        "score_matrix",
        False,
        False,
        False,
        True,
        True,
        "Exact R43Q score-matrix core and draw-calibration primitives are migrated and source-compatibility gated, but historical architecture/53% gates were not passed. Keep disabled.",
    ),
    "R43R": LegacyComponentSpec(
        "R43R",
        "strong_shrink_football_residual",
        "football3/r43r0-strong-shrink-football-residual",
        "football-data/experiments/r43r0_strong_shrink_football_residual/run_r43r0.py",
        "8748e795bb92780c47af934c3187db14c254a415",
        "1x2_probabilities",
        False,
        True,
        False,
        True,
        True,
        "Exact residual transform and causal beta fit are migrated. Native R43R output remains 1X2; architecture failed on the 15-row scored overlap and the residual did not improve Top1 over market. No retuning on that overlap and no implicit matrix claim.",
    ),
    "R43T": LegacyComponentSpec(
        "R43T",
        "dynamic_bivariate_total_difference_residual_state",
        "football3/r43t0-dynamic-bivariate-residual-state",
        "football-data/experiments/r43t0_dynamic_bivariate_residual_state/run_r43t0.py",
        "f6db4f0e6c0f544c058b15a7279731f55c5f6570",
        "score_matrix",
        False,
        False,
        False,
        True,
        True,
        "Exact R43T state lifecycle, including same-kickoff pre-update freezing and post-group simultaneous settlement, is migrated and compatibility gated. Historical architecture failed; keep disabled.",
    ),
    "R43U": LegacyComponentSpec(
        "R43U",
        "fixed_diagonal_inflation",
        "football3/r43u0-fixed-diagonal-inflation",
        "football-data/experiments/r43u0_fixed_diagonal_inflation/run_r43u0.py",
        "4ad46cca4acb618068f6db2601cf96bad4109698",
        "score_matrix",
        True,
        False,
        False,
        True,
        True,
        "Single canonical exact 1.25 diagonal-inflation implementation is migrated and compatibility gated. Historical architecture passed on 53 consumed rows but 53% was not met; U1 has 41 locked and 0 settled confirmations at the evidence gate. Keep disabled and do not retune.",
    ),
    "R43Y": LegacyComponentSpec(
        "R43Y",
        "draw_calibration_in_the_large_logit_intercept",
        "football3/r43u1-pristine-forward-confirmation",
        "football-data/experiments/r43y0_draw_calibration_forward/run_r43y0.py",
        "a342138bef97eb4acb0bcba015dea251a3280fdf",
        "1x2_probabilities",
        None,
        None,
        False,
        True,
        True,
        "R43Y provenance is resolved at snapshot 7043d6f7788f05b958e2ab7ec743b982a54ec5aa. Exact fixed draw-logit intercept 0.1322913820792354 reproduces all 41 sealed predictions, including 3 natural draw Top1 changes. Native source is 1X2-only; no score-matrix lifting is claimed by the source migration.",
    ),
}


class DeclaredLegacyScoreMatrixComponent:
    """Disabled declaration retained for compatibility; it never executes source logic."""

    def __init__(self, key: str):
        if key not in SPECS:
            raise KeyError(key)
        spec = SPECS[key]
        self.spec = spec
        self.component_id = spec.component_id
        self.component_version = "r43gov0-m5g-declaration-v2"
        self.enabled = False

    def apply(self, matrix: list[dict[str, Any]], request: Any, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError(
            f"{self.component_id} implementation is not available through declaration-only wrapper"
        )


def unresolved_sources() -> tuple[str, ...]:
    return tuple(sorted(key for key, spec in SPECS.items() if not spec.source_resolved))


def migration_candidates() -> tuple[str, ...]:
    return tuple(sorted(key for key, spec in SPECS.items() if spec.source_resolved and not spec.implementation_migrated))


def native_score_matrix_components() -> tuple[str, ...]:
    return tuple(sorted(key for key, spec in SPECS.items() if spec.native_output == "score_matrix"))


def native_probability_components() -> tuple[str, ...]:
    return tuple(sorted(key for key, spec in SPECS.items() if spec.native_output == "1x2_probabilities"))
