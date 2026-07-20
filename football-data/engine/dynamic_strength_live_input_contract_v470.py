#!/usr/bin/env python3
"""Fail-closed live-input contract for validated V4.7 dynamic-strength candidates.

Question-time roster/manager/transfer continuity is derived only from evidence
observed at or before the freeze. Candidate coefficients are not hard-coded by
competition: they are loaded from the audited next-season frozen-selection receipt.
This layer never changes probabilities by itself.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from dynamic_strength_challenger_v470 import commensurability_score
from platform_core import ROOT, PlatformError, load_json, parse_iso_datetime

SELECTION_PATH = ROOT / "manifests" / "dynamic_strength_next_season_selection_v470_status.json"


def _require_text(value: Any, field: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise PlatformError(f"{field} must be non-empty")
    return token


def _load_frozen_candidate(competition_id: str, target_season: str) -> dict[str, Any]:
    if not SELECTION_PATH.exists():
        raise PlatformError("next-season frozen dynamic-strength selection receipt missing")
    receipt = load_json(SELECTION_PATH)
    report = (receipt.get("reports") or {}).get(competition_id)
    if not isinstance(report, dict):
        raise PlatformError("competition has no frozen dynamic-strength selection")
    if report.get("status") != "NEXT_SEASON_CANDIDATE_FROZEN_RESEARCH_ONLY":
        raise PlatformError("competition frozen dynamic-strength selection is not valid")
    if str(report.get("target_season") or "") != target_season:
        raise PlatformError("frozen dynamic-strength selection target season mismatch")
    spec = report.get("selected_candidate_spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("coefficients"), dict):
        raise PlatformError("frozen dynamic-strength candidate specification missing")
    return {
        "candidate_id": _require_text(spec.get("id"), "selected_candidate_spec.id"),
        "coefficients": spec["coefficients"],
        "max_prior_equivalent_matches": float(spec.get("max_prior_equivalent_matches", 0.0)),
        "mode": str(report.get("mode") or ""),
        "selection_season": str(report.get("selection_season") or ""),
        "selection_receipt_status": report.get("status"),
    }


def _source(source: Any, freeze: datetime, field: str) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise PlatformError(f"{field} source must be an object")
    name = _require_text(source.get("name"), f"{field}.source.name")
    url = _require_text(source.get("url"), f"{field}.source.url")
    observed = parse_iso_datetime(source.get("observed_at_utc"), f"{field}.source.observed_at_utc")
    if observed > freeze:
        raise PlatformError(f"{field} evidence observed after freeze")
    return {"name": name, "url": url, "observed_at_utc": observed.isoformat()}


def _player_ids(values: Any, field: str) -> set[str]:
    if not isinstance(values, list) or not values:
        raise PlatformError(f"{field} must be a non-empty list")
    output = {_require_text(value, field) for value in values}
    if len(output) != len(values):
        raise PlatformError(f"{field} contains duplicate player ids")
    return output


def _starter_weights(values: Any, field: str) -> dict[str, float]:
    if not isinstance(values, dict) or not values:
        raise PlatformError(f"{field} must be a non-empty object")
    output = {}
    for player, weight in values.items():
        pid = _require_text(player, field)
        try:
            number = float(weight)
        except (TypeError, ValueError) as exc:
            raise PlatformError(f"{field} has non-numeric starter weight") from exc
        if number <= 0.0:
            raise PlatformError(f"{field} starter weights must be positive")
        output[pid] = number
    return output


def _team_features(team: Any, freeze: datetime, field: str, candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(team, dict):
        raise PlatformError(f"{field} must be an object")
    team_name = _require_text(team.get("team_name"), f"{field}.team_name")
    promoted = team.get("promoted_or_relegated")
    if not isinstance(promoted, bool):
        raise PlatformError(f"{field}.promoted_or_relegated must be boolean")
    manager = _require_text(team.get("current_manager"), f"{field}.current_manager")
    prior_manager = _require_text(team.get("prior_season_terminal_manager"), f"{field}.prior_season_terminal_manager")
    manager_source = _source(team.get("manager_source"), freeze, f"{field}.manager")
    roster_source = _source(team.get("roster_source"), freeze, f"{field}.roster")
    transfer_source = _source(team.get("transfer_source"), freeze, f"{field}.transfers")
    roster = _player_ids(team.get("current_roster_player_ids"), f"{field}.current_roster_player_ids")
    weights = _starter_weights(team.get("prior_season_starter_weights"), f"{field}.prior_season_starter_weights")
    prior_end = parse_iso_datetime(team.get("prior_season_end_utc"), f"{field}.prior_season_end_utc")
    if prior_end >= freeze:
        raise PlatformError(f"{field}.prior_season_end_utc must be before freeze")

    movements: set[str] = set()
    transfer_rows = team.get("dated_transfer_events")
    if not isinstance(transfer_rows, list):
        raise PlatformError(f"{field}.dated_transfer_events must be a list")
    for index, event in enumerate(transfer_rows):
        if not isinstance(event, dict):
            raise PlatformError(f"{field}.dated_transfer_events[{index}] must be an object")
        player = _require_text(event.get("player_id"), f"{field}.dated_transfer_events[{index}].player_id")
        event_date = parse_iso_datetime(event.get("event_at_utc"), f"{field}.dated_transfer_events[{index}].event_at_utc")
        observed = parse_iso_datetime(event.get("observed_at_utc"), f"{field}.dated_transfer_events[{index}].observed_at_utc")
        if event_date <= prior_end or event_date > freeze:
            raise PlatformError(f"{field} transfer event outside prior-season-end/freeze window")
        if observed > freeze:
            raise PlatformError(f"{field} transfer evidence observed after freeze")
        direction = str(event.get("direction") or "").strip().lower()
        if direction not in {"in", "out"}:
            raise PlatformError(f"{field} transfer direction must be in/out")
        movements.add(player)

    total_weight = sum(weights.values())
    retained_weight = sum(weight for player, weight in weights.items() if player in roster)
    continuity = retained_weight / total_weight
    structural_break = min(1.0, len(movements) / max(1.0, 2.0 * len(weights)))
    coach_continuity = 1.0 if manager == prior_manager else 0.0
    if promoted:
        continuity_for_borrowing = 0.0
        coach_for_borrowing = 0.0
        structural_for_borrowing = 1.0
    else:
        continuity_for_borrowing = continuity
        coach_for_borrowing = coach_continuity
        structural_for_borrowing = structural_break
    borrowing_weight = commensurability_score(
        roster_continuity=continuity_for_borrowing,
        coach_continuity=coach_for_borrowing,
        promoted_or_relegated=promoted,
        structural_break_score=structural_for_borrowing,
        coefficients=candidate["coefficients"],
    )
    if promoted:
        borrowing_weight = 0.0
    return {
        "team_name": team_name,
        "promoted_or_relegated": promoted,
        "roster_continuity": continuity,
        "coach_continuity": coach_continuity,
        "structural_break_score": structural_break,
        "borrowing_weight_research_candidate": borrowing_weight,
        "max_prior_equivalent_matches": candidate["max_prior_equivalent_matches"],
        "prior_starter_player_count": len(weights),
        "current_roster_player_count": len(roster),
        "dated_transfer_movement_player_count": len(movements),
        "sources": {"manager": manager_source, "roster": roster_source, "transfers": transfer_source},
    }


def validate_dynamic_strength_live_input(context: dict[str, Any], evidence: Any) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise PlatformError("context must be an object")
    identity = context.get("match_identity") or {}
    competition_id = str(identity.get("competition_id") or "")
    target_season = str(identity.get("season") or "")
    freeze = parse_iso_datetime(identity.get("freeze_time_utc"), "freeze_time_utc")
    candidate = _load_frozen_candidate(competition_id, target_season)
    if not isinstance(evidence, dict):
        raise PlatformError("dynamic_strength_evidence must be an object")
    if str(evidence.get("competition_id") or "") != competition_id:
        raise PlatformError("dynamic-strength competition mismatch")
    if str(evidence.get("target_season") or "") != target_season:
        raise PlatformError("dynamic-strength target season mismatch")
    overall_observed = parse_iso_datetime(evidence.get("observed_at_utc"), "dynamic_strength_evidence.observed_at_utc")
    if overall_observed > freeze:
        raise PlatformError("dynamic-strength evidence observed after freeze")
    prior_season = _require_text(evidence.get("prior_season"), "dynamic_strength_evidence.prior_season")
    if prior_season != candidate["selection_season"]:
        raise PlatformError("dynamic-strength prior season does not match frozen selection season")
    teams = evidence.get("teams")
    if not isinstance(teams, dict):
        raise PlatformError("dynamic_strength_evidence.teams must be an object")
    home = _team_features(teams.get("home"), freeze, "dynamic_strength_evidence.teams.home", candidate)
    away = _team_features(teams.get("away"), freeze, "dynamic_strength_evidence.teams.away", candidate)
    expected_home = str(identity.get("home_team") or "").strip()
    expected_away = str(identity.get("away_team") or "").strip()
    if home["team_name"] != expected_home or away["team_name"] != expected_away:
        raise PlatformError("dynamic-strength team identity mismatch")
    return {
        "schema_version": "V4.7.0-dynamic-strength-live-input-contract-r2",
        "status": "通过",
        "competition_id": competition_id,
        "target_season": target_season,
        "prior_season": prior_season,
        "freeze_time_utc": freeze.isoformat(),
        "evidence_observed_at_utc": overall_observed.isoformat(),
        "candidate_id": candidate["candidate_id"],
        "candidate_mode": candidate["mode"],
        "selection_season": candidate["selection_season"],
        "home": home,
        "away": away,
        "formal_probability_effect_weight": 0,
        "formal_activation_eligible": False,
        "probability_mutation": False,
        "reason": "Live PIT feature contract passed and is bound to the frozen next-season candidate, but a separate competition/season promotion and hash-bound runtime activation are still required.",
    }


def apply_dynamic_strength_live_input_audit(context: dict[str, Any], evidence: Any) -> dict[str, Any]:
    output = copy.deepcopy(context)
    try:
        audit = validate_dynamic_strength_live_input(output, evidence)
    except PlatformError as exc:
        audit = {
            "schema_version": "V4.7.0-dynamic-strength-live-input-contract-r2",
            "status": "不可用",
            "formal_probability_effect_weight": 0,
            "formal_activation_eligible": False,
            "probability_mutation": False,
            "reason": str(exc),
        }
    output["dynamic_strength_live_input_audit"] = audit
    output.setdefault("module_states", {})["dynamic_strength_live_input"] = audit["status"]
    return output
