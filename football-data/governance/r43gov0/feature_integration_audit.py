"""M7 feature-family integration truth table.

This module separates four questions that were historically easy to conflate:
(1) do we recognize the data, (2) is there usable historical/PIT evidence,
(3) is a numerical consumer wired to PIT-derived values now, and (4) is the
family eligible for formal prediction.  Only (3) permits the phrase "the model
used this data numerically" for the current governed pipeline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class FeatureIntegrationStatus:
    feature_family: str
    recognized: bool
    pit_family_registered: bool
    mechanism_evidence: str
    mechanism_gate_passed: bool | None
    prematch_timestamp_evidence: str
    numerical_consumer_exists: bool
    pit_bound_numeric_wiring: bool
    verified_numerical_integration: bool
    formal_numeric_eligible: bool
    evidence_ref: str
    evidence_blob_sha: str | None
    note: str

    def __post_init__(self) -> None:
        if self.verified_numerical_integration and not self.pit_bound_numeric_wiring:
            raise ValueError("verified numerical integration requires PIT-bound numeric wiring")
        if self.formal_numeric_eligible and not self.verified_numerical_integration:
            raise ValueError("formal numerical eligibility requires verified numerical integration")

    def to_dict(self) -> dict:
        return asdict(self)


STATUSES: Mapping[str, FeatureIntegrationStatus] = {
    "lineup_pstart": FeatureIntegrationStatus(
        feature_family="lineup_pstart",
        recognized=True,
        pit_family_registered=True,
        mechanism_evidence="R43B0R1 strict chronological P(start) mechanism passed lineup-quality gate only",
        mechanism_gate_passed=True,
        prematch_timestamp_evidence="prior completed-match history only; target current-match lineup excluded from features",
        numerical_consumer_exists=False,
        pit_bound_numeric_wiring=False,
        verified_numerical_integration=False,
        formal_numeric_eligible=False,
        evidence_ref="football3/r43b0r1-probabilistic-lineup-eligible-split@9ae6aaae49c61cf00ae1c3808fc0a9db125302e0",
        evidence_blob_sha="c94de3e9427baef81aad6240fea7858edc169be8",
        note="Gate promoted the start-probability mechanism toward availability integration; it explicitly changed no 1X2 probabilities and is not fresh forward confirmation.",
    ),
    "availability_status": FeatureIntegrationStatus(
        feature_family="availability_status",
        recognized=True,
        pit_family_registered=True,
        mechanism_evidence="no governed 1X2 integration gate",
        mechanism_gate_passed=False,
        prematch_timestamp_evidence="current/retrospective availability labels are not yet admitted as historical numerical inputs",
        numerical_consumer_exists=False,
        pit_bound_numeric_wiring=False,
        verified_numerical_integration=False,
        formal_numeric_eligible=False,
        evidence_ref="R43B0R1 governance exclusions",
        evidence_blob_sha="c94de3e9427baef81aad6240fea7858edc169be8",
        note="R43B0R1 explicitly excluded current injury status and retrospective availability status from features.",
    ),
    "player_technical": FeatureIntegrationStatus(
        feature_family="player_technical",
        recognized=True,
        pit_family_registered=True,
        mechanism_evidence="R42H strict-chronology technical translation OOS audit failed promotion gate",
        mechanism_gate_passed=False,
        prematch_timestamp_evidence="technical values frozen pre-opener but provider collection timestamps are not independently bound per row",
        numerical_consumer_exists=False,
        pit_bound_numeric_wiring=False,
        verified_numerical_integration=False,
        formal_numeric_eligible=False,
        evidence_ref="football3/r42h-player-technical-translation-oos@68d419dbf371f29bd2c8b5e26b94af04da4b3026",
        evidence_blob_sha="b4a5a38d81d037eae068cd4cdc59121304b5b09f",
        note="On 66 consumed OOS matches Top1 gained one hit, but LogLoss/Brier/RPS all worsened; gate says DO_NOT_PROMOTE_TECHNICAL_TRANSLATION_V1.",
    ),
    "head_coach": FeatureIntegrationStatus(
        feature_family="head_coach",
        recognized=False,
        pit_family_registered=False,
        mechanism_evidence="R43E2 third disjoint 20k hierarchical draw-coach test failed promotion gate",
        mechanism_gate_passed=False,
        prematch_timestamp_evidence="no verified prematch timestamp for current-match coach changes",
        numerical_consumer_exists=False,
        pit_bound_numeric_wiring=False,
        verified_numerical_integration=False,
        formal_numeric_eligible=False,
        evidence_ref="football3/r43e2-hierarchical-draw-coach-older20k@d1d161c0afb3070ef4dce1bc32c81a5e2e2d8e91",
        evidence_blob_sha="f7e4c8c42d5327fb7b34320ba5a36a1ecc1e856a",
        note="Historical candidate failed; the evidence also states current-match coach changes remain unknown until a completed fixture because no verified prematch coach timestamp exists.",
    ),
    "market_1x2_ah_ou": FeatureIntegrationStatus(
        feature_family="market_1x2_ah_ou",
        recognized=True,
        pit_family_registered=True,
        mechanism_evidence="R43Q exact market-score mechanics migrated and source-compatible; historical architecture gate did not pass",
        mechanism_gate_passed=False,
        prematch_timestamp_evidence="same-timestamp 1X2/AH/OU required by source contract, but current R43Q candidate receives caller baseline_payload rather than PIT-bound values",
        numerical_consumer_exists=True,
        pit_bound_numeric_wiring=False,
        verified_numerical_integration=False,
        formal_numeric_eligible=False,
        evidence_ref="R43GOV0 M5C R43Q compatibility",
        evidence_blob_sha="299b86ed07e49af0b9ec5c7632f519e91e836158",
        note="R43Q numerically consumes market odds in research when explicitly selected, but M7 cannot claim the governed feature family is numerically integrated until those odds are injected from legal PIT records.",
    ),
}


def verified_numerical_families() -> tuple[str, ...]:
    return tuple(sorted(k for k, v in STATUSES.items() if v.verified_numerical_integration))


def formal_numeric_families() -> tuple[str, ...]:
    return tuple(sorted(k for k, v in STATUSES.items() if v.formal_numeric_eligible))


def audit_receipt() -> dict:
    return {
        "schema_version": "football3-r43gov0-m7-feature-integration-audit-v1",
        "families": {k: v.to_dict() for k, v in sorted(STATUSES.items())},
        "verified_numerical_families": list(verified_numerical_families()),
        "formal_numeric_families": list(formal_numeric_families()),
        "truth_rule": "recognized_or_collected_data_must_not_be_called_model_used_without_verified_numerical_integration",
    }
