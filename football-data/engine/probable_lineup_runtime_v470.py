#!/usr/bin/env python3
"""Auditable question-time probable-lineup runtime projection for V4.7.

This layer makes the distinction between a lineup *status label* and an actually
computed/preserved XI explicit.  It never turns a predicted XI into an observed
XI and never applies numeric lineup effects to formal score probabilities.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from platform_core import PlatformError, parse_iso_datetime
from probable_lineup_challenger_v470 import starter_probabilities


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _parse_observed_lineups(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    output = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        xi = _string_list(row.get("starting_xi"))
        if len(xi) != 11:
            continue
        raw = row.get("kickoff") or row.get("kickoff_utc")
        try:
            kickoff = parse_iso_datetime(raw, f"lineup_evidence.observed_lineups[{index}].kickoff")
        except PlatformError:
            continue
        output.append({"kickoff": kickoff, "starting_xi": xi})
    return output


def _external_predicted_xi(evidence: dict[str, Any]) -> tuple[list[str], dict[str, float]]:
    xi = _string_list(evidence.get("predicted_starting_xi"))
    raw_probs = evidence.get("starter_probabilities")
    probabilities: dict[str, float] = {}
    if isinstance(raw_probs, dict):
        for player, value in raw_probs.items():
            try:
                probability = float(value)
            except (TypeError, ValueError):
                continue
            probabilities[str(player)] = min(1.0, max(0.0, probability))
    return xi, probabilities


def apply_probable_lineup_runtime(match_input: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(context)
    evidence = match_input.get("lineup_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    status = str(evidence.get("status") or "unavailable")
    cutoff = parse_iso_datetime((output.get("match_identity") or {}).get("freeze_time_utc"), "freeze_time_utc")
    sources = evidence.get("sources") if isinstance(evidence.get("sources"), list) else []

    audit: dict[str, Any] = {
        "status": "不可用",
        "evidence_status": status,
        "projection_mode": None,
        "starting_xi": [],
        "starter_probabilities": {},
        "verified_same_season_lineups_used": 0,
        "effective_lineup_matches": 0.0,
        "source_count": len(sources),
        "predicted_xi_written_as_observed": False,
        "formal_probability_effect_weight": 0,
        "probability_mutation": False,
    }

    if status == "official":
        official_xi = _string_list(evidence.get("starting_xi") or evidence.get("official_starting_xi"))
        if len(official_xi) == 11 and sources:
            audit.update({
                "status": "通过",
                "projection_mode": "official_observed_xi",
                "starting_xi": official_xi,
                "observed_xi": True,
                "reason": "verified official XI supplied before freeze",
            })
        else:
            audit.update({
                "status": "部分通过",
                "projection_mode": "official_status_without_complete_xi",
                "reason": "official lineup status exists but a complete 11-player XI was not supplied",
            })

    elif status == "probable":
        external_xi, external_probs = _external_predicted_xi(evidence)
        if len(external_xi) == 11 and sources:
            audit.update({
                "status": "部分通过",
                "projection_mode": "external_predicted_xi",
                "starting_xi": external_xi,
                "starter_probabilities": external_probs,
                "observed_xi": False,
                "reason": "source-backed predicted XI preserved as prediction, never as observed",
            })
        else:
            current_players = _string_list(evidence.get("current_players"))
            observed_lineups = _parse_observed_lineups(evidence.get("observed_lineups"))
            availability = evidence.get("availability") if isinstance(evidence.get("availability"), dict) else {}
            if len(current_players) >= 11 and observed_lineups:
                result = starter_probabilities(
                    observed_lineups,
                    cutoff,
                    current_players,
                    availability,
                    half_life_days=float(evidence.get("half_life_days", 45.0)),
                    prior_starts=float(evidence.get("prior_starts", 1.0)),
                    prior_matches=float(evidence.get("prior_matches", 2.0)),
                )
                probabilities = result["starter_probabilities"]
                ranked = sorted(probabilities.items(), key=lambda item: (-float(item[1]), item[0]))
                predicted_xi = [player for player, _ in ranked[:11]]
                usable = int(result.get("verified_same_season_lineups_used", 0)) > 0 and len(predicted_xi) == 11
                audit.update({
                    "status": "部分通过" if usable else "不可用",
                    "projection_mode": "computed_same_season_probable_xi" if usable else "computed_projection_insufficient_history",
                    "starting_xi": predicted_xi if usable else [],
                    "starter_probabilities": probabilities,
                    "verified_same_season_lineups_used": int(result.get("verified_same_season_lineups_used", 0)),
                    "effective_lineup_matches": float(result.get("effective_lineup_matches", 0.0)),
                    "observed_xi": False,
                    "reason": "computed from strictly prior observed same-season XIs and point-in-time availability" if usable else "no eligible strictly prior same-season XI history",
                })
            else:
                audit.update({
                    "status": "不可用",
                    "projection_mode": "probable_status_without_executable_inputs",
                    "reason": "probable status supplied without a complete predicted XI or executable same-season lineup history",
                })
    else:
        audit["reason"] = "lineup evidence unavailable"

    output["lineup_projection"] = {
        "status": audit["status"],
        "mode": audit["projection_mode"],
        "starting_xi": audit["starting_xi"],
        "starter_probabilities": audit["starter_probabilities"],
        "observed_xi": bool(audit.get("observed_xi", False)),
        "formal_probability_effect_weight": 0,
    }
    output["probable_lineup_v470_audit"] = audit

    states = output.setdefault("module_states", {})
    # Do not call the lineup module fully passed merely because a status label was supplied.
    if audit["status"] == "通过":
        states["lineup_and_task"] = "通过"
    elif audit["status"] == "部分通过":
        states["lineup_and_task"] = "部分通过"
    elif status in {"official", "probable"}:
        states["lineup_and_task"] = "警告"
    else:
        states["lineup_and_task"] = "不可用"

    gates = output.setdefault("gates", {})
    gates["probable_lineup_detail_available"] = bool(audit["starting_xi"])
    gates["probable_lineup_numeric_effect_enabled"] = False
    return output
