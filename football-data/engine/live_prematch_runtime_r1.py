#!/usr/bin/env python3
"""Operational-shadow single-match runtime for question-time usability closure R1.

This module deliberately does NOT mutate CURRENT or formal weights. It closes the
engineering gap between a frozen prematch question and an auditable probability
artifact when the formal single-match path cannot run because the event domain is
one-off, the target season is in cold start, or the venue is neutral.

Core policy:
* event identity is separate from the competition used as the strength reference;
* all result rows are strictly before the freeze calendar date (date-only safety);
* same-season history is preferred when it can run the existing frozen engine math;
* otherwise all prior strength-reference seasons are passed to the same frozen
  V4.6 score engine as a research-only cold-start bridge;
* neutral venues are symmetrized by averaging both orientations after mirroring;
* evidence observed after freeze fails closed;
* availability / XI / market evidence is audit context only in R1 and cannot
  numerically mutate probabilities.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from football_v460_engine import load_config, load_model_artifact, predict_from_history
from platform_core import (
    ROOT,
    PlatformError,
    derive_score_marginals,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    sha256_file,
    sha256_json,
    top_scores,
)

CLASSIFICATION = "OPERATIONAL_SHADOW_ONLY"
RUNTIME_VERSION = "LIVE_PREMATCH_RUNTIME_R1"


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise PlatformError(f"{key} must be a non-empty string")
    return value


def _validate_evidence(payload: dict[str, Any], freeze, kickoff) -> dict[str, Any]:
    items = payload.get("evidence") or []
    if not isinstance(items, list):
        raise PlatformError("evidence must be a list")
    audited: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PlatformError(f"evidence[{index}] must be an object")
        source_name = str(item.get("source_name") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        observed_raw = item.get("observed_at_utc")
        if not source_name or not source_url or not observed_raw:
            raise PlatformError(
                f"evidence[{index}] requires source_name, source_url and observed_at_utc"
            )
        observed = parse_iso_datetime(observed_raw, f"evidence[{index}].observed_at_utc")
        if observed > freeze:
            raise PlatformError(
                f"post-freeze evidence rejected: evidence[{index}] observed_at={observed.isoformat()} "
                f"freeze={freeze.isoformat()}"
            )
        if observed >= kickoff:
            raise PlatformError(f"post-kickoff evidence rejected: evidence[{index}]")
        audited.append({
            "kind": str(item.get("kind") or "context"),
            "source_name": source_name,
            "source_url": source_url,
            "observed_at_utc": observed.isoformat(),
            "payload_sha256": sha256_json(item),
            "numeric_probability_mutation": False,
        })
    return {
        "status": "PASS",
        "count": len(audited),
        "items": audited,
        "all_observed_at_or_before_freeze": True,
        "numeric_probability_mutation": False,
    }


def _history_before_freeze(matches, freeze):
    # Persisted result rows are date-only. Preserve the existing conservative
    # policy: nothing from the freeze calendar date may enter strength state.
    return [m for m in matches if m.date.date() < freeze.date()]


def _season_history(history, target_season: str):
    return [m for m in history if str(m.season) == target_season]


def _parameter_set(competition_id: str, target_season: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = load_model_artifact(competition_id)
    if not isinstance(artifact, dict):
        raise PlatformError(f"validated formal-core artifact missing for strength reference {competition_id}")
    mapping = artifact.get("point_in_time_parameters") or {}
    if isinstance(mapping, dict) and isinstance(mapping.get(target_season), dict):
        params = dict(mapping[target_season])
        source = f"POINT_IN_TIME_TARGET_SEASON:{target_season}"
        carry_forward = False
    else:
        params = dict(artifact.get("selected_parameters") or {})
        if not params:
            raise PlatformError(f"no validated parameter set available for {competition_id}")
        source = f"LATEST_VALIDATED_PARAMETER_CARRY_FORWARD_SHADOW:{artifact.get('live_target_season')}"
        carry_forward = True
    model_path = ROOT / "models" / "formal_core_v460" / competition_id / "model.json"
    return params, {
        "source": source,
        "carry_forward_shadow": carry_forward,
        "model_artifact_path": str(model_path.relative_to(ROOT)),
        "model_artifact_sha256": sha256_file(model_path),
        "live_target_season": artifact.get("live_target_season"),
    }


def _predict(history, competition_id: str, season: str, home: str, away: str, freeze, params):
    return predict_from_history(
        history,
        competition_id,
        season,
        home,
        away,
        freeze,
        selected_parameters=params,
        use_team_effects=True,
    )


def _mirror_matrix(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    return {
        (int(cell["away_goals"]), int(cell["home_goals"])): float(cell["probability"])
        for cell in matrix
    }


def _matrix_map(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    return {
        (int(cell["home_goals"]), int(cell["away_goals"])): float(cell["probability"])
        for cell in matrix
    }


def _symmetrize_neutral(forward: dict[str, Any], reverse: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    a = _matrix_map(forward["probabilities"]["score_matrix"])
    b = _mirror_matrix(reverse["probabilities"]["score_matrix"])
    keys = sorted(set(a) | set(b))
    matrix = [
        {"home_goals": h, "away_goals": g, "probability": 0.5 * a.get((h, g), 0.0) + 0.5 * b.get((h, g), 0.0)}
        for h, g in keys
    ]
    mass = sum(float(cell["probability"]) for cell in matrix)
    if mass <= 0:
        raise PlatformError("neutral symmetrization produced zero probability mass")
    for cell in matrix:
        cell["probability"] = float(cell["probability"]) / mass
    return matrix, {
        "method": "AVERAGE_FORWARD_AND_MIRRORED_REVERSE_ORIENTATIONS",
        "forward_mu_total": forward.get("team_sample", {}).get("mu_total"),
        "reverse_mu_total": reverse.get("team_sample", {}).get("mu_total"),
        "manual_home_advantage_coefficient": False,
    }


def _season_counts(history) -> dict[str, int]:
    counts = Counter(str(m.season) for m in history)
    return dict(sorted(counts.items()))


def run_live_prematch(payload: dict[str, Any]) -> dict[str, Any]:
    event_competition_id = _required_text(payload, "event_competition_id")
    strength_competition_id = _required_text(payload, "strength_reference_competition_id")
    target_season = _required_text(payload, "season")
    event_home = _required_text(payload, "home_team")
    event_away = _required_text(payload, "away_team")
    strength_home = str(payload.get("strength_home_team") or event_home).strip()
    strength_away = str(payload.get("strength_away_team") or event_away).strip()
    if normalize_team_token(strength_home) == normalize_team_token(strength_away):
        raise PlatformError("strength home/away resolve to the same team token")

    kickoff = parse_iso_datetime(_required_text(payload, "kickoff_utc"), "kickoff_utc")
    freeze = parse_iso_datetime(_required_text(payload, "freeze_time_utc"), "freeze_time_utc")
    if freeze >= kickoff:
        raise PlatformError("freeze_time_utc must be strictly before kickoff_utc")
    neutral = bool(payload.get("neutral_venue", False))
    evidence_audit = _validate_evidence(payload, freeze, kickoff)

    all_matches = read_processed_matches(strength_competition_id)
    prior_history = _history_before_freeze(all_matches, freeze)
    if not prior_history:
        raise PlatformError("strength reference has no completed history strictly before freeze date")
    same_season = _season_history(prior_history, target_season)
    params, parameter_audit = _parameter_set(strength_competition_id, target_season)

    normal_error = None
    selected_history = same_season
    route = "SAME_SEASON_NORMAL_SHADOW"
    try:
        forward = _predict(
            same_season,
            strength_competition_id,
            target_season,
            strength_home,
            strength_away,
            freeze,
            params,
        )
        reverse = (
            _predict(
                same_season,
                strength_competition_id,
                target_season,
                strength_away,
                strength_home,
                freeze,
                params,
            )
            if neutral else None
        )
    except Exception as exc:  # fail into audited research bridge, not silent fallback
        normal_error = f"{type(exc).__name__}: {exc}"
        selected_history = prior_history
        route = "CROSS_SEASON_COLD_START_BRIDGE"
        forward = _predict(
            prior_history,
            strength_competition_id,
            "ALL_PRIOR_SEASONS_SHADOW",
            strength_home,
            strength_away,
            freeze,
            params,
        )
        reverse = (
            _predict(
                prior_history,
                strength_competition_id,
                "ALL_PRIOR_SEASONS_SHADOW",
                strength_away,
                strength_home,
                freeze,
                params,
            )
            if neutral else None
        )

    if neutral:
        if reverse is None:
            raise PlatformError("neutral venue requires reverse orientation prediction")
        matrix, neutral_audit = _symmetrize_neutral(forward, reverse)
    else:
        matrix = list(forward["probabilities"]["score_matrix"])
        neutral_audit = {
            "method": "NOT_APPLICABLE_HOME_AWAY_ORIENTATION",
            "manual_home_advantage_coefficient": False,
        }

    marginals = derive_score_marginals(matrix)
    if abs(float(marginals["probability_sum"]) - 1.0) > 1e-9:
        raise PlatformError("final operational-shadow matrix does not conserve probability")
    ranking = top_scores(matrix, 10)
    total_rank = sorted(marginals["total_goals"].items(), key=lambda x: (-float(x[1]), x[0]))
    direction = max(marginals["1x2"], key=marginals["1x2"].get)

    config = load_config()
    return {
        "schema_version": "live-prematch-runtime-r1.0",
        "runtime_version": RUNTIME_VERSION,
        "classification": CLASSIFICATION,
        "formal_weight": 0,
        "status": "PASS",
        "event_identity": {
            "event_competition_id": event_competition_id,
            "strength_reference_competition_id": strength_competition_id,
            "season": target_season,
            "home_team": event_home,
            "away_team": event_away,
            "strength_home_team": strength_home,
            "strength_away_team": strength_away,
            "kickoff_utc": kickoff.isoformat(),
            "freeze_time_utc": freeze.isoformat(),
            "neutral_venue": neutral,
            "venue": payload.get("venue"),
        },
        "route": {
            "selected": route,
            "same_season_history_matches": len(same_season),
            "selected_history_matches": len(selected_history),
            "normal_route_failure": normal_error,
            "cold_start_bridge_used": route == "CROSS_SEASON_COLD_START_BRIDGE",
            "date_only_same_day_rows_excluded": True,
        },
        "probabilities": {
            "one_x_two": marginals["1x2"],
            "total_goals": marginals["total_goals"],
            "btts_yes": marginals["btts_yes"],
            "score_matrix": matrix,
        },
        "conclusions": {
            "result_direction": direction,
            "result_text": (
                f"90分钟 operational-shadow：主胜{marginals['1x2']['home']:.1%}、"
                f"平局{marginals['1x2']['draw']:.1%}、客胜{marginals['1x2']['away']:.1%}。"
            ),
            "total_goals_primary": total_rank[0][0],
            "total_goals_secondary": total_rank[1][0],
            "top_score": ranking[0]["score"],
            "second_score": ranking[1]["score"] if len(ranking) > 1 else None,
            "top_scores": ranking[:5],
        },
        "audit": {
            "input_sha256": sha256_json(payload),
            "evidence": evidence_audit,
            "parameter_source": parameter_audit,
            "neutral_venue": neutral_audit,
            "history_season_counts": _season_counts(selected_history),
            "selected_history_latest_date": max(m.date for m in selected_history).date().isoformat(),
            "engine_math": "football_v460_engine.predict_from_history",
            "engine_config_version": config.get("engine_version"),
            "availability_xi_probability_mutation": False,
            "market_probability_mutation": False,
            "event_domain_not_relabelled_as_strength_domain": event_competition_id != strength_competition_id,
            "formal_current_mutated": False,
        },
        "limitations": [
            "Research operational shadow only; formal CURRENT and formal weights are unchanged.",
            "Cold-start bridge reuses prior strength-reference seasons with the frozen V4.6 engine math; it is not a promoted cross-season scientific model.",
            "Availability, confirmed XI and market evidence are frozen/audited but have zero numeric probability effect in R1 pending OOS validation.",
            "Teams with no usable prior history in the chosen strength-reference competition may still fail closed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = run_live_prematch(payload)
    except (PlatformError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
