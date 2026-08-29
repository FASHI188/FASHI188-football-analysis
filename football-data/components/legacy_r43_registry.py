"""Governed registry for the existing R43Q/R/T/U/Y research components.

The registry records exact source lineage, gate state, migration state and default
enablement. Migration never implies promotion: every legacy component stays off
unless a later explicit governance gate enables it.
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
        False,
        True,
        "Same-snapshot frozen 1X2+AH+OU is legitimate prospective evidence, but R43Q architecture gate failed and the 53% target was not met. Keep as an inactive alternative baseline until exact migration/compatibility tests exist.",
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
        False,
        True,
        "Native R43R output is 1X2, not a score matrix. Architecture gate failed on only 15 scored overlap rows; do not invent a matrix lifting rule or retune it.",
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
        False,
        True,
        "R43T uses prematch markets and causal state updates but its architecture gate failed; exact source may be migrated for compatibility only, never enabled by default.",
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
        "Exact R43U diagonal operation has been migrated behind the unified component interface and compatibility-gated, but remains disabled. Its historical gate passed only on the already-consumed 53-row cohort and did not meet 53%; U0/Y0 stays sealed.",
    ),
    "R43Y": LegacyComponentSpec(
        "R43Y",
        "draw_calibration",
        None,
        None,
        None,
        "unknown_until_source_resolved",
        None,
        None,
        False,
        False,
        False,
        "No independent R43Y branch, commit, or code path was resolved in the targeted repository search. Do not fabricate an implementation; resolve provenance first.",
    ),
}


class DeclaredLegacyScoreMatrixComponent:
    """Disabled declaration compatible with UnifiedInferenceEngine's component protocol."""

    def __init__(self, key: str):
        if key not in SPECS:
            raise KeyError(key)
        spec = SPECS[key]
        self.spec = spec
        self.component_id = spec.component_id
        self.component_version = "r43gov0-m5a-declaration-v1"
        self.enabled = False

    def apply(self, matrix: list[dict[str, Any]], request: Any, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError(
            f"{self.component_id} implementation is not available through declaration-only wrapper"
        )


def unresolved_sources() -> tuple[str, ...]:
    return tuple(sorted(key for key, spec in SPECS.items() if not spec.source_resolved))


def migration_candidates() -> tuple[str, ...]:
    return tuple(sorted(key for key, spec in SPECS.items() if spec.source_resolved and spec.native_output == "score_matrix" and not spec.implementation_migrated))
