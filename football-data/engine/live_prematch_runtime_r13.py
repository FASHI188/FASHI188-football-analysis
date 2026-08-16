#!/usr/bin/env python3
"""Live Prematch Runtime R1.3: generic PIT-market last-resort cold-start closure.

R1.3 preserves R1.2 behavior.  If same-season, cross-season and the existing
league-specific offline promotion bridge all fail, a genuine prospective market
snapshot already persisted before kickoff may provide an operational-shadow score
matrix through the existing auditable minimum-KL projection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from football_v460_engine import load_config
from live_prematch_runtime_r1 import (
    CLASSIFICATION,
    _history_before_freeze,
    _parameter_set,
    _predict,
    _required_text,
    _season_counts,
    _season_history,
    _symmetrize_neutral,
    _validate_evidence,
)
from market_pit_coldstart_fallback_r1 import predict_market_pit_coldstart
from platform_core import PlatformError, derive_score_marginals, normalize_team_token, parse_iso_datetime, read_processed_matches, sha256_json, top_scores
from promotion_coldstart_fallback_r1 import predict_promotion_coldstart

RUNTIME_VERSION = "LIVE_PREMATCH_RUNTIME_R1_3"


def run_live_prematch(payload):
    event_cid = _required_text(payload, "event_competition_id")
    strength_cid = _required_text(payload, "strength_reference_competition_id")
    season = _required_text(payload, "season")
    event_home, event_away = _required_text(payload, "home_team"), _required_text(payload, "away_team")
    strength_home = str(payload.get("strength_home_team") or event_home).strip()
    strength_away = str(payload.get("strength_away_team") or event_away).strip()
    if normalize_team_token(strength_home) == normalize_team_token(strength_away):
        raise PlatformError("strength home/away resolve to the same team")

    kickoff = parse_iso_datetime(_required_text(payload, "kickoff_utc"), "kickoff_utc")
    freeze = parse_iso_datetime(_required_text(payload, "freeze_time_utc"), "freeze_time_utc")
    if freeze >= kickoff:
        raise PlatformError("freeze_time_utc must be strictly before kickoff_utc")
    neutral = bool(payload.get("neutral_venue", False))
    evidence = _validate_evidence(payload, freeze, kickoff)

    prior = _history_before_freeze(read_processed_matches(strength_cid), freeze)
    if not prior:
        raise PlatformError("strength reference has no completed history before freeze")
    same = _season_history(prior, season)
    params, parameter_audit = _parameter_set(strength_cid, season)

    normal_error = cross_error = promotion_error = market_error = None
    promotion_audit = market_audit = None
    selected_history = same
    route = "SAME_SEASON_NORMAL_SHADOW"
    try:
        forward = _predict(same, strength_cid, season, strength_home, strength_away, freeze, params)
        reverse = _predict(same, strength_cid, season, strength_away, strength_home, freeze, params) if neutral else None
    except Exception as exc:
        normal_error = f"{type(exc).__name__}: {exc}"
        selected_history = prior
        route = "CROSS_SEASON_COLD_START_BRIDGE"
        try:
            forward = _predict(prior, strength_cid, "ALL_PRIOR_SEASONS_SHADOW", strength_home, strength_away, freeze, params)
            reverse = _predict(prior, strength_cid, "ALL_PRIOR_SEASONS_SHADOW", strength_away, strength_home, freeze, params) if neutral else None
        except Exception as exc2:
            cross_error = f"{type(exc2).__name__}: {exc2}"
            route = "PROMOTION_OFFLINE_COLD_START_FALLBACK"
            try:
                forward = predict_promotion_coldstart(prior, strength_cid, strength_home, strength_away, freeze, params)
                reverse = predict_promotion_coldstart(prior, strength_cid, strength_away, strength_home, freeze, params) if neutral else None
                promotion_audit = {"forward": forward.get("audit"), "reverse": reverse.get("audit") if reverse else None}
            except Exception as exc3:
                promotion_error = f"{type(exc3).__name__}: {exc3}"
                route = "PIT_MARKET_COLD_START_FALLBACK"
                market_path = str(payload.get("market_snapshot_path") or "").strip()
                try:
                    forward = predict_market_pit_coldstart(
                        prior,
                        competition_id=strength_cid,
                        season=season,
                        home_team=strength_home,
                        away_team=strength_away,
                        kickoff=kickoff,
                        freeze=freeze,
                        market_snapshot_path=market_path,
                    )
                    reverse = None
                    market_audit = forward.get("audit")
                    if neutral:
                        raise PlatformError("R1.3 PIT market fallback does not support neutral venue orientation")
                except Exception as exc4:
                    market_error = f"{type(exc4).__name__}: {exc4}"
                    raise PlatformError(
                        "all live-prematch routes failed: "
                        f"normal=[{normal_error}]; cross=[{cross_error}]; promotion=[{promotion_error}]; market=[{market_error}]"
                    ) from exc4

    if neutral:
        if reverse is None:
            raise PlatformError("neutral venue requires reverse orientation")
        matrix, neutral_audit = _symmetrize_neutral(forward, reverse)
    else:
        matrix = list(forward["probabilities"]["score_matrix"])
        neutral_audit = {"method": "NOT_APPLICABLE_HOME_AWAY_ORIENTATION", "manual_home_advantage_coefficient": False}

    marg = derive_score_marginals(matrix)
    if abs(float(marg["probability_sum"]) - 1.0) > 1e-9:
        raise PlatformError("final operational-shadow matrix does not conserve probability")
    ranked = top_scores(matrix, 10)
    total_rank = sorted(marg["total_goals"].items(), key=lambda x: (-float(x[1]), x[0]))
    direction = max(marg["1x2"], key=marg["1x2"].get)
    cfg = load_config()

    market_mutates = route == "PIT_MARKET_COLD_START_FALLBACK"
    if route == "PROMOTION_OFFLINE_COLD_START_FALLBACK":
        engine_math = "promotion_coldstart_fallback_r1"
    elif market_mutates:
        engine_math = "market_pit_coldstart_fallback_r1 + market_kl_projection_v463"
    else:
        engine_math = "football_v460_engine.predict_from_history"

    return {
        "schema_version": "live-prematch-runtime-r1.3",
        "runtime_version": RUNTIME_VERSION,
        "classification": CLASSIFICATION,
        "formal_weight": 0,
        "status": "PASS",
        "event_identity": {
            "event_competition_id": event_cid,
            "strength_reference_competition_id": strength_cid,
            "season": season,
            "home_team": event_home,
            "away_team": event_away,
            "strength_home_team": strength_home,
            "strength_away_team": strength_away,
            "kickoff_utc": kickoff.isoformat(),
            "freeze_time_utc": freeze.isoformat(),
            "neutral_venue": neutral,
            "venue": payload.get("venue")
        },
        "route": {
            "selected": route,
            "same_season_history_matches": len(same),
            "selected_history_matches": len(selected_history),
            "normal_route_failure": normal_error,
            "cross_season_route_failure": cross_error,
            "promotion_route_failure": promotion_error,
            "market_route_failure": market_error,
            "cold_start_bridge_used": route != "SAME_SEASON_NORMAL_SHADOW",
            "promotion_fallback_used": route == "PROMOTION_OFFLINE_COLD_START_FALLBACK",
            "pit_market_fallback_used": market_mutates,
            "external_runtime_network_required": False,
            "date_only_same_day_rows_excluded": True
        },
        "probabilities": {
            "one_x_two": marg["1x2"],
            "total_goals": marg["total_goals"],
            "btts_yes": marg["btts_yes"],
            "score_matrix": matrix
        },
        "conclusions": {
            "result_direction": direction,
            "result_text": f"90分钟 operational-shadow：主胜{marg['1x2']['home']:.1%}、平局{marg['1x2']['draw']:.1%}、客胜{marg['1x2']['away']:.1%}。",
            "total_goals_primary": total_rank[0][0],
            "total_goals_secondary": total_rank[1][0],
            "top_score": ranked[0]["score"],
            "second_score": ranked[1]["score"] if len(ranked) > 1 else None,
            "top_scores": ranked[:5]
        },
        "audit": {
            "input_sha256": sha256_json(payload),
            "evidence": evidence,
            "parameter_source": parameter_audit,
            "neutral_venue": neutral_audit,
            "promotion_coldstart": promotion_audit,
            "pit_market_coldstart": market_audit,
            "history_season_counts": _season_counts(selected_history),
            "selected_history_latest_date": max(m.date for m in selected_history).date().isoformat(),
            "engine_math": engine_math,
            "engine_config_version": cfg.get("engine_version"),
            "availability_xi_probability_mutation": False,
            "market_probability_mutation": market_mutates,
            "event_domain_not_relabelled_as_strength_domain": event_cid != strength_cid,
            "formal_current_mutated": False
        },
        "limitations": [
            "Operational shadow only; formal CURRENT and formal weights are unchanged.",
            "PIT market fallback is permitted only with a repository-persisted prospective snapshot observed before freeze and kickoff.",
            "A single-provider old snapshot can close engineering usability but is not equivalent to near-kickoff multi-provider consensus.",
            "Availability and XI remain audit-only pending chronological OOS value validation."
        ]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = run_live_prematch(json.loads(Path(args.input).read_text(encoding="utf-8")))
    except (PlatformError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        p = Path(args.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
