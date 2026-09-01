from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from typing import Any

from candidate_c import ComponentEffect, SideDelta

GRADE_ORDER = (
    "CONFIRMED_LINEUP_PIT",
    "POSSIBLE_XI_PIT",
    "TEAM_NEWS_AVAILABILITY_PIT",
    "NO_USABLE_ROSTER_EVIDENCE",
)
UNCERTAINTY_BANDS = {
    "CONFIRMED_LINEUP_PIT": (0.10, 0.24),
    "POSSIBLE_XI_PIT": (0.35, 0.49),
    "TEAM_NEWS_AVAILABILITY_PIT": (0.65, 0.79),
    "NO_USABLE_ROSTER_EVIDENCE": (1.00, 1.00),
}


class HistoricalCandidateCContractError(RuntimeError):
    pass


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def monotonic_uncertainty(grade: str, raw_uncertainty: float) -> float:
    """Map raw uncertainty into disjoint, preregistered evidence-grade bands.

    This is an uncertainty-contract repair only. It is monotone in raw uncertainty
    within each evidence grade and strictly ordered across evidence grades.
    """
    if grade not in UNCERTAINTY_BANDS:
        raise HistoricalCandidateCContractError(f"unknown evidence grade: {grade}")
    raw = float(raw_uncertainty)
    if not math.isfinite(raw) or raw < 0.0:
        raise HistoricalCandidateCContractError("raw uncertainty must be finite and non-negative")
    lo, hi = UNCERTAINTY_BANDS[grade]
    if hi == lo:
        return lo
    scaled = raw / (1.0 + raw)
    return lo + (hi - lo) * scaled


def uncertainty_only_effect(effect: ComponentEffect, grade: str) -> ComponentEffect:
    """Replace uncertainty only; all football deltas/evidence identity remain unchanged."""
    raw = max(float(effect.home.uncertainty), float(effect.away.uncertainty))
    repaired = monotonic_uncertainty(grade, raw)
    home = replace(effect.home, uncertainty=repaired)
    away = replace(effect.away, uncertainty=repaired)
    out = replace(effect, home=home, away=away)
    if (
        out.active != effect.active
        or out.home.delta_attack != effect.home.delta_attack
        or out.home.delta_defence != effect.home.delta_defence
        or out.home.delta_tempo != effect.home.delta_tempo
        or out.away.delta_attack != effect.away.delta_attack
        or out.away.delta_defence != effect.away.delta_defence
        or out.away.delta_tempo != effect.away.delta_tempo
        or out.reason != effect.reason
        or out.affected_player_ids != effect.affected_player_ids
        or out.shrunk_player_n != effect.shrunk_player_n
        or out.reference_n_home != effect.reference_n_home
        or out.reference_n_away != effect.reference_n_away
        or out.evidence_sha256 != effect.evidence_sha256
    ):
        raise HistoricalCandidateCContractError("uncertainty repair mutated football effect")
    return out


def monotonic_contract_holds() -> bool:
    for left, right in zip(GRADE_ORDER, GRADE_ORDER[1:]):
        if UNCERTAINTY_BANDS[left][1] > UNCERTAINTY_BANDS[right][0]:
            return False
    return True


def contract() -> dict[str, Any]:
    obj = {
        "schema_version": "football3-context-translator-candidate-c-historical-uncertainty-v1",
        "status": "HISTORICAL_PIT_REPLAY_ONLY",
        "repair_scope": "UNCERTAINTY_CONTRACT_ONLY",
        "evidence_grade_order": list(GRADE_ORDER),
        "uncertainty_bands": {k: list(v) for k, v in UNCERTAINTY_BANDS.items()},
        "within_grade_mapping": "lo + (hi-lo) * raw/(1+raw)",
        "football_delta_mutation": False,
        "result_conditioning": False,
        "formal_weight": 0,
        "formal_promotion_eligible": False,
        "forbidden": [
            "label_conditioned_uncertainty",
            "outcome_multiplier",
            "direct_1x2_patch",
            "postmatch_target_feature",
        ],
    }
    obj["contract_sha256"] = _sha(obj)
    return obj


if not monotonic_contract_holds():
    raise RuntimeError("historical Candidate C uncertainty bands are not monotonic")
