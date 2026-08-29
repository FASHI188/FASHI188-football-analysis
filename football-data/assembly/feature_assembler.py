"""Feature assembly and per-match activation receipts for Football3.

This module does not alter any model. It records whether a feature was merely
recognized, was PIT-legal, entered a numerical input, and actually changed the
active component output for a specific prediction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from pit.feature_store import PITReadResult


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class FeatureFamilyPolicy:
    recognized: bool
    experiment_passed: bool
    numeric_effect_enabled: bool


@dataclass(frozen=True)
class FeatureActivation:
    feature_family: str
    recognized: bool
    pit_legal: bool
    assembled: bool
    numeric_effect: bool
    experiment_passed: bool
    numeric_effect_enabled: bool
    inactive_reason: str | None
    source_record_count: int
    source_record_hashes: tuple[str, ...]
    numerical_feature_names: tuple[str, ...]
    values_hash: str | None
    component_input_hash: str | None
    component_output_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("source_record_hashes", "numerical_feature_names"):
            out[key] = list(out[key])
        return out


@dataclass(frozen=True)
class FeatureActivationReceipt:
    fixture_id: str
    as_of: datetime
    canonical_home_team_id: str
    canonical_away_team_id: str
    activations: tuple[FeatureActivation, ...]
    final_score_matrix_hash: str | None
    final_1x2: Mapping[str, float] | None
    final_top1: str | None
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "as_of": _iso(self.as_of),
            "canonical_home_team_id": self.canonical_home_team_id,
            "canonical_away_team_id": self.canonical_away_team_id,
            "activations": [a.to_dict() for a in self.activations],
            "final_score_matrix_hash": self.final_score_matrix_hash,
            "final_1x2": dict(self.final_1x2) if self.final_1x2 is not None else None,
            "final_top1": self.final_top1,
            "receipt_hash": self.receipt_hash,
        }


DEFAULT_POLICIES: dict[str, FeatureFamilyPolicy] = {
    "lineup_pstart": FeatureFamilyPolicy(True, True, False),
    "availability_status": FeatureFamilyPolicy(True, False, False),
    "player_technical": FeatureFamilyPolicy(True, False, False),
    "head_coach": FeatureFamilyPolicy(True, False, False),
    "market_1x2_ah_ou": FeatureFamilyPolicy(True, False, False),
}


class FeatureAssembler:
    def __init__(self, policies: Mapping[str, FeatureFamilyPolicy] | None = None):
        merged = dict(DEFAULT_POLICIES)
        if policies:
            merged.update(policies)
        self._policies = merged

    @property
    def probable_lineup_numeric_effect_enabled(self) -> bool:
        policy = self._policies.get("lineup_pstart")
        return bool(policy and policy.numeric_effect_enabled)

    def policy(self, feature_family: str) -> FeatureFamilyPolicy:
        return self._policies.get(feature_family, FeatureFamilyPolicy(False, False, False))

    def assemble_family(
        self,
        feature_family: str,
        pit_result: PITReadResult | None,
        numerical_values: Mapping[str, Any] | None = None,
        numerical_feature_names: Iterable[str] = (),
        component_input_hash: str | None = None,
        component_output_hash: str | None = None,
    ) -> FeatureActivation:
        policy = self.policy(feature_family)
        recognized = bool(policy.recognized)
        pit_legal = bool(recognized and pit_result is not None and pit_result.status == "active" and pit_result.records)
        names = tuple(str(name) for name in numerical_feature_names)
        assembled = bool(pit_legal and numerical_values is not None and names)
        values_hash = _stable_hash(dict(numerical_values)) if assembled and numerical_values is not None else None
        numeric_effect = bool(
            assembled
            and policy.numeric_effect_enabled
            and component_input_hash
            and component_output_hash
            and component_input_hash != component_output_hash
        )

        reason = None
        if not recognized:
            reason = "feature_family_not_recognized"
        elif not pit_legal:
            if pit_result is None:
                reason = "no_pit_read_result"
            elif pit_result.status != "active" or not pit_result.records:
                reason = "no_pit_legal_records"
        elif not assembled:
            reason = "not_wired_into_numerical_feature_input"
        elif not policy.numeric_effect_enabled:
            reason = "numeric_effect_disabled_by_governance"
        elif not component_input_hash or not component_output_hash:
            reason = "component_hash_evidence_missing"
        elif component_input_hash == component_output_hash:
            reason = "no_component_output_delta_for_match"

        return FeatureActivation(
            feature_family=feature_family,
            recognized=recognized,
            pit_legal=pit_legal,
            assembled=assembled,
            numeric_effect=numeric_effect,
            experiment_passed=bool(policy.experiment_passed),
            numeric_effect_enabled=bool(policy.numeric_effect_enabled),
            inactive_reason=reason,
            source_record_count=len(pit_result.records) if pit_result is not None else 0,
            source_record_hashes=tuple(r.record_hash for r in pit_result.records) if pit_result is not None else (),
            numerical_feature_names=names if assembled else (),
            values_hash=values_hash,
            component_input_hash=component_input_hash if assembled else None,
            component_output_hash=component_output_hash if assembled else None,
        )

    def build_receipt(
        self,
        fixture_id: str,
        as_of: datetime,
        canonical_home_team_id: str,
        canonical_away_team_id: str,
        activations: Iterable[FeatureActivation],
        final_score_matrix_hash: str | None = None,
        final_1x2: Mapping[str, float] | None = None,
        final_top1: str | None = None,
    ) -> FeatureActivationReceipt:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        acts = tuple(activations)
        payload = {
            "fixture_id": fixture_id,
            "as_of": _iso(as_of),
            "canonical_home_team_id": canonical_home_team_id,
            "canonical_away_team_id": canonical_away_team_id,
            "activations": [a.to_dict() for a in acts],
            "final_score_matrix_hash": final_score_matrix_hash,
            "final_1x2": dict(final_1x2) if final_1x2 is not None else None,
            "final_top1": final_top1,
        }
        receipt_hash = _stable_hash(payload)
        return FeatureActivationReceipt(
            fixture_id=fixture_id,
            as_of=as_of.astimezone(timezone.utc),
            canonical_home_team_id=canonical_home_team_id,
            canonical_away_team_id=canonical_away_team_id,
            activations=acts,
            final_score_matrix_hash=final_score_matrix_hash,
            final_1x2=final_1x2,
            final_top1=final_top1,
            receipt_hash=receipt_hash,
        )