"""Runtime authority for the rebuilt Football3 execution path.

This file resolves an operational conflict without rewriting historical CURRENT or
the frozen M9/M10 governance receipts.  S60 is the unique stage-primary execution
baseline for unified dataset/replay/live workflows. V500 is a suspended research
component (formal_weight=0) and may never be selected as the operational baseline.

This is an execution-routing decision, not a scientific/formal promotion claim.
"""
from __future__ import annotations

from typing import Iterable

from assembly.feature_assembler import FeatureAssembler
from components.v500_dynamic_state import INVALIDATION_STATUS, V500BayesianDynamicStateComponent
from identity.team_identity import TeamIdentityResolver
from pipeline.s60_numerical_baseline import S60NumericalBaseline
from pipeline.unified_inference import UnifiedInferenceEngine
from pit.feature_store import PointInTimeFeatureStore

CANONICAL_OPERATIONAL_BASELINE = "S60"
CANONICAL_OPERATIONAL_COMPONENT_ID = "S60_stage_primary_numerical_baseline"
S60_STAGE_PRIMARY = True
S60_FORMAL_SCIENTIFIC_PROMOTION = False
V500_RUNTIME_BASELINE_ALLOWED = False
V500_STATUS = INVALIDATION_STATUS
V500_FORMAL_WEIGHT = 0
HISTORICAL_CURRENT_AUTHORITY_CHANGED = False

DISABLED_NUMERIC_FEATURES = (
    "lineup_pstart",
    "availability_status",
    "player_technical",
    "head_coach",
)


def assert_runtime_authority() -> None:
    if CANONICAL_OPERATIONAL_BASELINE != "S60":
        raise RuntimeError("operational baseline authority drift")
    if V500_RUNTIME_BASELINE_ALLOWED or V500_FORMAL_WEIGHT != 0:
        raise RuntimeError("suspended V500 may not be operational baseline")


def build_operational_s60_engine(
    identity_resolver: TeamIdentityResolver,
    pit_store: PointInTimeFeatureStore,
    baseline: S60NumericalBaseline,
    *,
    components: Iterable[object] = (),
) -> UnifiedInferenceEngine:
    assert_runtime_authority()
    if not isinstance(baseline, S60NumericalBaseline):
        raise TypeError("operational runtime requires real S60NumericalBaseline")
    assembler = FeatureAssembler()
    for family in DISABLED_NUMERIC_FEATURES:
        if assembler.policy(family).numeric_effect_enabled:
            raise RuntimeError(f"disabled feature unexpectedly enabled: {family}")
    for component in components:
        if isinstance(component, V500BayesianDynamicStateComponent) and component.enabled:
            raise RuntimeError("V500 is suspended and may not enter operational S60 runtime")
    return UnifiedInferenceEngine(identity_resolver, pit_store, assembler, baseline, tuple(components))


def authority_receipt() -> dict:
    return {
        "schema_version": "football3-runtime-authority-v1",
        "canonical_operational_baseline": CANONICAL_OPERATIONAL_BASELINE,
        "canonical_operational_component_id": CANONICAL_OPERATIONAL_COMPONENT_ID,
        "s60_stage_primary": S60_STAGE_PRIMARY,
        "s60_formal_scientific_promotion": S60_FORMAL_SCIENTIFIC_PROMOTION,
        "v500_runtime_baseline_allowed": V500_RUNTIME_BASELINE_ALLOWED,
        "v500_status": V500_STATUS,
        "v500_formal_weight": V500_FORMAL_WEIGHT,
        "historical_current_authority_changed": HISTORICAL_CURRENT_AUTHORITY_CHANGED,
        "lineup_numeric_1x2_enabled": False,
        "player_technical_numeric_1x2_enabled": False,
        "head_coach_numeric_1x2_enabled": False,
        "availability_numeric_1x2_enabled": False,
        "meaning": "execution routing only; not formal scientific promotion",
    }
