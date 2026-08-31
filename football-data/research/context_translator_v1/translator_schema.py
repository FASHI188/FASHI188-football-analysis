from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


class SchemaError(RuntimeError):
    pass

HEX = set("0123456789abcdef")
STATUSES = {"IMPLEMENTED", "REJECTED_ABLATION", "BLOCKED_DATA", "CONTRACT_ONLY"}
GRADES = {"FULL_TRACKING", "FULL_EVENT", "LINEUP_STATS", "TEAM_ONLY", "HARD_FAIL"}
ROUTES = {"EXPECTED_LINEUP", "CONFIRMED_LINEUP", "LINEUP_UNKNOWN"}


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _hex64(v: Any, field: str, nullable: bool = False) -> None:
    if v is None and nullable:
        return
    if not isinstance(v, str) or len(v) != 64 or any(c not in HEX for c in v):
        raise SchemaError(f"{field} must be lowercase 64-hex")


def _finite(v: Any, field: str, nonnegative: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise SchemaError(f"{field} must be finite")
    x = float(v)
    if nonnegative and x < 0:
        raise SchemaError(f"{field} must be >=0")
    return x


def validate_translated_context(obj: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "research_status", "match_id", "cutoff", "coverage_grade", "provenance_manifest_sha256",
                "team_state", "player_state", "lineup_scenarios", "coach_tactical", "match_context", "process_hazard",
                "scenario_outputs", "uncertainty"}
    if set(obj) != required:
        raise SchemaError(f"top-level schema mismatch missing={sorted(required-set(obj))} extra={sorted(set(obj)-required)}")
    if obj["schema_version"] != "football3.context_translator.v1" or obj["research_status"] != "RESEARCH_ONLY":
        raise SchemaError("schema/research status mismatch")
    if obj["coverage_grade"] not in GRADES:
        raise SchemaError("invalid coverage grade")
    _hex64(obj["provenance_manifest_sha256"], "provenance_manifest_sha256")
    if not isinstance(obj["match_id"], str) or not obj["match_id"]:
        raise SchemaError("match_id required")
    if not isinstance(obj["lineup_scenarios"], list) or not obj["lineup_scenarios"]:
        raise SchemaError("at least one lineup scenario required")
    ps = obj["player_state"]
    if set(ps) != {"status", "players_sha256"} or ps["status"] not in STATUSES:
        raise SchemaError("invalid player_state")
    _hex64(ps["players_sha256"], "players_sha256", nullable=True)
    total_p = 0.0
    ids = set()
    for i, sc in enumerate(obj["lineup_scenarios"]):
        keys = {"scenario_id", "route", "probability", "home_player_ids", "away_player_ids", "known_at_max", "uncertainty", "scenario_sha256"}
        if set(sc) != keys or sc["route"] not in ROUTES:
            raise SchemaError(f"invalid lineup_scenarios[{i}]")
        p = _finite(sc["probability"], f"scenario[{i}].probability", True)
        if p > 1:
            raise SchemaError("scenario probability >1")
        total_p += p
        ids.add(sc["scenario_id"])
        _finite(sc["uncertainty"], "scenario uncertainty", True)
        _hex64(sc["scenario_sha256"], "scenario_sha256")
    if abs(total_p - 1.0) > 1e-9 or len(ids) != len(obj["lineup_scenarios"]):
        raise SchemaError("scenario probabilities/ids invalid")
    for name in ("coach_tactical", "match_context", "process_hazard"):
        ls = obj[name]
        keys = {"status", "log_mu_home_delta", "log_mu_away_delta", "uncertainty", "evidence_sha256"}
        if set(ls) != keys or ls["status"] not in STATUSES:
            raise SchemaError(f"invalid {name}")
        _finite(ls["log_mu_home_delta"], name)
        _finite(ls["log_mu_away_delta"], name)
        _finite(ls["uncertainty"], name, True)
        _hex64(ls["evidence_sha256"], "evidence_sha256", nullable=True)
    if not isinstance(obj["scenario_outputs"], list) or len(obj["scenario_outputs"]) != len(obj["lineup_scenarios"]):
        raise SchemaError("scenario output count mismatch")
    out_ids = set()
    for so in obj["scenario_outputs"]:
        keys = {"scenario_id", "probability", "base_mu_home", "base_mu_away", "translated_mu_home", "translated_mu_away", "score_matrix_sha256"}
        if set(so) != keys:
            raise SchemaError("scenario output schema mismatch")
        out_ids.add(so["scenario_id"])
        for f in ("base_mu_home", "base_mu_away", "translated_mu_home", "translated_mu_away"):
            if _finite(so[f], f) <= 0:
                raise SchemaError(f"{f} must be positive")
        _hex64(so["score_matrix_sha256"], "score_matrix_sha256")
    if out_ids != ids:
        raise SchemaError("scenario output identities mismatch")
    unc = obj["uncertainty"]
    if set(unc) != {"total", "components"} or not isinstance(unc["components"], dict):
        raise SchemaError("invalid uncertainty")
    _finite(unc["total"], "uncertainty.total", True)
    for k, v in unc["components"].items():
        _finite(v, f"uncertainty.components.{k}", True)
    return obj


def frozen_schema_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
