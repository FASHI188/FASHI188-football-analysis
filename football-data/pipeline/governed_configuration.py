"""Explicit Football3 governed inference configurations.

This module prevents research-capable plumbing from silently becoming the formal
model. The formal profile is frozen V500 with default feature policies and no R43
components. The R43Q profile is research-only, uses the same identity/PIT/unified
engine plumbing, and explicitly enables only the PIT-bound market feature family.
Additional R43 components require an extra opt-in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from assembly.feature_assembler import FeatureAssembler, FeatureFamilyPolicy
from components.r43_native_matrix_components import R43QMarketScoreBaseline
from identity.team_identity import TeamIdentityResolver
from pipeline.unified_inference import ScoreMatrixComponent, UnifiedInferenceEngine
from pipeline.v500_baseline_adapter import FrozenV500MatrixBaseline
from pit.feature_store import PointInTimeFeatureStore


FORMAL_DEFAULT_PROFILE = "formal_default_v500"
R43Q_RESEARCH_PROFILE = "r43q_pit_market_research_candidate"
MARKET_FEATURE_FAMILY = "market_1x2_ah_ou"


@dataclass(frozen=True)
class GovernedConfigurationReceipt:
    profile: str
    research_only: bool
    formal_default: bool
    baseline_component_id: str
    market_numeric_effect_enabled: bool
    lineup_numeric_effect_enabled: bool
    player_technical_numeric_effect_enabled: bool
    head_coach_numeric_effect_enabled: bool
    availability_numeric_effect_enabled: bool
    enabled_research_components: tuple[str, ...]


@dataclass(frozen=True)
class GovernedEngine:
    engine: UnifiedInferenceEngine
    receipt: GovernedConfigurationReceipt


def _receipt(
    profile: str,
    research_only: bool,
    formal_default: bool,
    assembler: FeatureAssembler,
    baseline_component_id: str,
    components: Iterable[ScoreMatrixComponent],
) -> GovernedConfigurationReceipt:
    enabled = tuple(
        str(component.component_id)
        for component in components
        if bool(getattr(component, "enabled", False))
    )
    return GovernedConfigurationReceipt(
        profile=profile,
        research_only=research_only,
        formal_default=formal_default,
        baseline_component_id=baseline_component_id,
        market_numeric_effect_enabled=assembler.policy(MARKET_FEATURE_FAMILY).numeric_effect_enabled,
        lineup_numeric_effect_enabled=assembler.policy("lineup_pstart").numeric_effect_enabled,
        player_technical_numeric_effect_enabled=assembler.policy("player_technical").numeric_effect_enabled,
        head_coach_numeric_effect_enabled=assembler.policy("head_coach").numeric_effect_enabled,
        availability_numeric_effect_enabled=assembler.policy("availability_status").numeric_effect_enabled,
        enabled_research_components=enabled,
    )


def build_formal_default_engine(
    identity_resolver: TeamIdentityResolver,
    pit_store: PointInTimeFeatureStore,
) -> GovernedEngine:
    """Build the only formal/default configuration: frozen V500, no research add-ons."""
    assembler = FeatureAssembler()
    baseline = FrozenV500MatrixBaseline()
    components: tuple[ScoreMatrixComponent, ...] = ()
    engine = UnifiedInferenceEngine(
        identity_resolver,
        pit_store,
        assembler,
        baseline,
        components,
    )
    receipt = _receipt(
        FORMAL_DEFAULT_PROFILE,
        research_only=False,
        formal_default=True,
        assembler=assembler,
        baseline_component_id=baseline.component_id,
        components=components,
    )
    if receipt.market_numeric_effect_enabled or receipt.enabled_research_components:
        raise RuntimeError("formal V500 profile may not enable research numerical paths")
    return GovernedEngine(engine, receipt)


def build_r43q_research_candidate_engine(
    identity_resolver: TeamIdentityResolver,
    pit_store: PointInTimeFeatureStore,
    components: Iterable[ScoreMatrixComponent] = (),
    *,
    allow_enabled_research_components: bool = False,
) -> GovernedEngine:
    """Build the PIT-bound R43Q research candidate; never a formal default.

    Market numeric effect is explicitly enabled for this profile only. All other
    feature-family defaults remain unchanged. Enabled R43 score-matrix components
    require a second explicit opt-in so creating the research baseline cannot
    silently activate T/U/R/Y behavior.
    """
    comps = tuple(components)
    enabled = tuple(c for c in comps if bool(getattr(c, "enabled", False)))
    if enabled and not allow_enabled_research_components:
        ids = [str(c.component_id) for c in enabled]
        raise RuntimeError(f"enabled research components require explicit opt-in: {ids}")

    assembler = FeatureAssembler({
        MARKET_FEATURE_FAMILY: FeatureFamilyPolicy(
            recognized=True,
            experiment_passed=False,
            numeric_effect_enabled=True,
        ),
    })
    baseline = R43QMarketScoreBaseline(pit_store)
    engine = UnifiedInferenceEngine(
        identity_resolver,
        pit_store,
        assembler,
        baseline,
        comps,
    )
    receipt = _receipt(
        R43Q_RESEARCH_PROFILE,
        research_only=True,
        formal_default=False,
        assembler=assembler,
        baseline_component_id=baseline.component_id,
        components=comps,
    )
    if not receipt.market_numeric_effect_enabled:
        raise RuntimeError("R43Q research profile must explicitly enable PIT market numerics")
    if any((
        receipt.lineup_numeric_effect_enabled,
        receipt.player_technical_numeric_effect_enabled,
        receipt.head_coach_numeric_effect_enabled,
        receipt.availability_numeric_effect_enabled,
    )):
        raise RuntimeError("R43Q research profile may not implicitly enable non-market feature families")
    return GovernedEngine(engine, receipt)
