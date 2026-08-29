"""Truth registry for Football3 feature-to-numeric integration governance.

A feature may be recognized or collected without being legally usable at an as-of
cutoff, without being wired into a numerical consumer, and without being formally
promotable. These states are deliberately separate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class FeatureIntegrationTruth:
    feature_family: str
    recognized_or_collected: bool
    historical_mechanism_gate_passed: bool
    pit_contract_available: bool
    numerical_consumer_exists: bool
    currently_pit_bound_to_consumer: bool
    numeric_effect_enabled: bool
    formal_promotion_allowed: bool
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


FEATURE_TRUTH: Mapping[str, FeatureIntegrationTruth] = {
    "lineup_pstart": FeatureIntegrationTruth(
        "lineup_pstart", True, True, True, False, False, False, False,
        "R43B0R1 lineup probability mechanism passed; that stage explicitly changed no 1X2 probabilities.",
    ),
    "availability_status": FeatureIntegrationTruth(
        "availability_status", True, False, False, False, False, False, False,
        "Retrospective availability/personnel labels are recognized but not approved PIT numerical inputs.",
    ),
    "player_technical": FeatureIntegrationTruth(
        "player_technical", True, False, False, True, False, False, False,
        "R42H OOS technical translation failed promotion: Top1 +1/66 while LogLoss/Brier/RPS worsened.",
    ),
    "head_coach": FeatureIntegrationTruth(
        "head_coach", True, False, False, True, False, False, False,
        "R43E2 failed on an older disjoint 20k and current-match coach changes lack a verified prematch timestamp contract.",
    ),
    "market_1x2_ah_ou": FeatureIntegrationTruth(
        "market_1x2_ah_ou", True, False, True, True, False, False, False,
        "R43Q exact source compatibility is proven, but its historical architecture gate was not passed and the unified path is not yet PIT-bound.",
    ),
}


def assert_no_false_numeric_claims() -> None:
    for truth in FEATURE_TRUTH.values():
        if truth.numeric_effect_enabled:
            assert truth.pit_contract_available
            assert truth.numerical_consumer_exists
            assert truth.currently_pit_bound_to_consumer
        if truth.formal_promotion_allowed:
            assert truth.historical_mechanism_gate_passed
            assert truth.numeric_effect_enabled


def numeric_enabled_families() -> tuple[str, ...]:
    return tuple(sorted(k for k, v in FEATURE_TRUTH.items() if v.numeric_effect_enabled))
