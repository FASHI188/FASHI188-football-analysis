#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENGINE_DIR = ROOT / "football-data" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import nova_stage0_foundation_v1 as stage0
from platform_core import PlatformError, derive_score_marginals, score_matrix_rows, top_scores

N0_VERSION = "FOOTBALL3_NOVA_N0_V2_EQUIVALENCE_V1"
STAGE0_HEAD = "681aa6f686d6642ba251e6163820881dc6efd560"
PARENT_INTEGRATION_HEAD = "475dedfd177b02208f550fe97a89bbd1efa52125"
FORMAL_V2_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
FORMAL_POINTER_BLOB_SHA = "f07743f9c873d415fad9612f3225f64c64b10542"
CURRENT_VERSION = "V5.4.0"
CURRENT_SHA256 = "71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
MATRIX_TOLERANCE = 5e-12


class NovaN0Error(ValueError):
    pass


def _finite_probability(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NovaN0Error(f"{field}:BOOLEAN_NOT_PROBABILITY")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NovaN0Error(f"{field}:NOT_NUMERIC") from exc
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise NovaN0Error(f"{field}:OUT_OF_RANGE")
    return number


def canonical_json_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _required_identity(payload: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    keys = ("fixture_id", "competition_id", "season", "kickoff", "home_team_id", "away_team_id")
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise NovaN0Error(f"FORMAL_IDENTITY_INVALID:{key}")
        values.append(value.strip())
    if values[4] == values[5]:
        raise NovaN0Error("FORMAL_IDENTITY_HOME_AWAY_COLLISION")
    return tuple(values)  # type: ignore[return-value]


def derive_unified_outputs(score_matrix: Any) -> dict[str, Any]:
    try:
        marginals = derive_score_marginals(score_matrix)
        ranking = top_scores(score_matrix, 1)
    except PlatformError as exc:
        raise NovaN0Error(f"FORMAL_MATRIX_INVALID:{exc}") from exc
    mass = float(marginals["probability_sum"])
    if not math.isclose(mass, 1.0, rel_tol=0.0, abs_tol=MATRIX_TOLERANCE):
        raise NovaN0Error(f"FORMAL_MATRIX_MASS_DRIFT:{mass:.17g}")
    over_25 = 0.0
    btts_yes = 0.0
    for home, away, probability in score_matrix_rows(score_matrix):
        if home + away >= 3:
            over_25 += probability
        if home > 0 and away > 0:
            btts_yes += probability
    if abs(btts_yes - float(marginals["btts_yes"])) > MATRIX_TOLERANCE:
        raise NovaN0Error("BTTS_DERIVATION_DISAGREES_WITH_PLATFORM_CORE")
    if not ranking:
        raise NovaN0Error("MODEL_CENTER_UNAVAILABLE")
    return {
        "one_x_two": {
            "home": float(marginals["1x2"]["home"]),
            "draw": float(marginals["1x2"]["draw"]),
            "away": float(marginals["1x2"]["away"]),
        },
        "model_center": copy.deepcopy(ranking[0]),
        "over_2_5": float(over_25),
        "btts_yes": float(btts_yes),
        "probability_sum": mass,
    }


def verify_formal_v2_payload(formal_payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(formal_payload, Mapping):
        raise NovaN0Error("FORMAL_PAYLOAD_MUST_BE_MAPPING")
    identity = _required_identity(formal_payload)
    matrix = formal_payload.get("score_matrix")
    derived = derive_unified_outputs(matrix)
    direct = {
        "home": _finite_probability(formal_payload.get("p_home"), "p_home"),
        "draw": _finite_probability(formal_payload.get("p_draw"), "p_draw"),
        "away": _finite_probability(formal_payload.get("p_away"), "p_away"),
    }
    direct_mass = math.fsum(direct.values())
    if not math.isclose(direct_mass, 1.0, rel_tol=0.0, abs_tol=MATRIX_TOLERANCE):
        raise NovaN0Error(f"FORMAL_1X2_MASS_DRIFT:{direct_mass:.17g}")
    residuals = {key: direct[key] - derived["one_x_two"][key] for key in ("home", "draw", "away")}
    max_abs = max(abs(value) for value in residuals.values())
    if max_abs > MATRIX_TOLERANCE:
        raise NovaN0Error(f"FORMAL_MATRIX_1X2_MISMATCH:{max_abs:.17g}")
    return {
        "identity": {
            "fixture_id": identity[0],
            "competition_id": identity[1],
            "season": identity[2],
            "kickoff": identity[3],
            "home_team_id": identity[4],
            "away_team_id": identity[5],
        },
        "derived": derived,
        "one_x_two_residuals": residuals,
        "max_abs_one_x_two_residual": max_abs,
        "formal_prediction_sha256": canonical_json_sha256(formal_payload),
    }


def apply_inactive_experts_exact_passthrough(formal_payload: Any, *, adapter_key: str, experts: Iterable[stage0.ExpertOutput]) -> Any:
    passed = stage0.formal_v2_passthrough(formal_payload, adapter_key)
    if passed is not formal_payload:
        raise NovaN0Error("STAGE0_PASSTHROUGH_IDENTITY_BROKEN")
    for expert in experts:
        if not isinstance(expert, stage0.ExpertOutput):
            raise NovaN0Error("EXPERT_OUTPUT_TYPE_INVALID")
        expert.validate_stage0()
    if isinstance(formal_payload, Mapping):
        verify_formal_v2_payload(formal_payload)
    return formal_payload


def assert_frozen_v1_exact_fallback(*, frozen_v1_payload: Mapping[str, Any], formal_payload: Mapping[str, Any], audit: Mapping[str, Any]) -> dict[str, str]:
    if audit.get("route") != stage0.FORMAL_V2_FALLBACK:
        raise NovaN0Error("FALLBACK_ROUTE_NOT_FROZEN_V1_EXACT")
    if audit.get("fallback_exact_v1") is not True:
        raise NovaN0Error("FALLBACK_FLAG_NOT_TRUE")
    if formal_payload != frozen_v1_payload:
        raise NovaN0Error("FROZEN_V1_FALLBACK_NOT_VALUE_EXACT")
    left = canonical_json_sha256(frozen_v1_payload)
    right = canonical_json_sha256(formal_payload)
    if left != right:
        raise NovaN0Error("FROZEN_V1_FALLBACK_HASH_MISMATCH")
    return {"frozen_v1_sha256": left, "formal_sha256": right}


def build_n0_receipt(*, formal_payload: Mapping[str, Any], adapter_key: str, experts: Iterable[stage0.ExpertOutput], route: str) -> dict[str, Any]:
    if adapter_key not in stage0.TARGET_ADAPTERS:
        raise NovaN0Error("NOVA_ADAPTER_UNKNOWN")
    expert_rows = []
    for expert in experts:
        if not isinstance(expert, stage0.ExpertOutput):
            raise NovaN0Error("EXPERT_OUTPUT_TYPE_INVALID")
        expert_rows.append(expert.as_dict())
    verification = verify_formal_v2_payload(formal_payload)
    return {
        "schema_version": "football3-nova-n0-equivalence-receipt-v1",
        "n0_version": N0_VERSION,
        "stage0_head": STAGE0_HEAD,
        "parent_integration_head": PARENT_INTEGRATION_HEAD,
        "formal_v2_head": FORMAL_V2_HEAD,
        "formal_pointer_blob_sha": FORMAL_POINTER_BLOB_SHA,
        "current_version": CURRENT_VERSION,
        "current_sha256": CURRENT_SHA256,
        "adapter_key": adapter_key,
        "formal_competition_id": stage0.TARGET_ADAPTERS[adapter_key].formal_v2_competition_id,
        "route": route,
        "experts": expert_rows,
        "verification": verification,
        "training_performed": False,
        "new_labels_read": False,
        "model_activated": False,
        "formal_payload_mutated": False,
    }
