#!/usr/bin/env python3
"""Timestamp-safe market-backed cold-start fallback for live prematch runtime R1.3.

Use case: a target club has no usable top-flight history, but the repository already
contains a genuine prospective synchronized market snapshot observed before kickoff.

This is operational-shadow only.  The routine does not modify CURRENT or formal
weights.  It builds a league score prior from completed historical matches strictly
before the freeze date, then uses the existing minimum-KL projection to match de-vigged
1X2 and O/U constraints from the prospective snapshot.  AH is retained as an
independent consistency diagnostic because AH 0 is largely redundant with 1X2 once
Draw mass is fixed.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from market_kl_projection_v463 import _fair_three_way, _fair_two_way, project_market
from platform_core import (
    ROOT,
    PlatformError,
    derive_score_marginals,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    sha256_file,
    top_scores,
)

ALLOWED_ROOT = ROOT / "evidence" / "markets_prospective"


def _safe_snapshot_path(relative_path: str) -> Path:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw:
        raise PlatformError("market_snapshot_path is required for PIT market fallback")
    path = (ROOT / raw).resolve()
    allowed = ALLOWED_ROOT.resolve()
    if allowed not in path.parents:
        raise PlatformError("market snapshot must live under evidence/markets_prospective")
    if not path.exists() or not path.is_file():
        raise PlatformError(f"market snapshot not found: {raw}")
    return path


def _validate_snapshot(snapshot: dict[str, Any], path: Path, *, competition_id: str, season: str,
                       home_team: str, away_team: str, kickoff, freeze) -> dict[str, Any]:
    observed_raw = snapshot.get("source_observed_at_utc") or snapshot.get("freeze_utc") or snapshot.get("accessed_at_utc")
    if not observed_raw:
        raise PlatformError("market snapshot lacks source observation timestamp")
    observed = parse_iso_datetime(str(observed_raw), "market.source_observed_at_utc")
    if observed > freeze:
        raise PlatformError("market snapshot observed after requested freeze")
    if observed >= kickoff:
        raise PlatformError("market snapshot observed at/after kickoff")
    semantics = snapshot.get("observation_semantics") or {}
    if semantics.get("retrospective_backfill") is True:
        raise PlatformError("retrospective market backfill is not admissible for PIT fallback")
    if str(snapshot.get("competition_id") or "") != competition_id:
        raise PlatformError("market snapshot competition mismatch")
    if str(snapshot.get("season") or "") != season:
        raise PlatformError("market snapshot season mismatch")
    if normalize_team_token(str(snapshot.get("home_team") or "")) != normalize_team_token(home_team):
        raise PlatformError("market snapshot home-team mismatch")
    if normalize_team_token(str(snapshot.get("away_team") or "")) != normalize_team_token(away_team):
        raise PlatformError("market snapshot away-team mismatch")
    snap_kickoff = parse_iso_datetime(str(snapshot.get("kickoff_utc") or ""), "market.kickoff_utc")
    if abs((snap_kickoff - kickoff).total_seconds()) > 1.0:
        raise PlatformError("market snapshot kickoff mismatch")

    one = snapshot.get("one_x_two")
    ou = snapshot.get("over_under")
    if not isinstance(one, dict) or not isinstance(ou, dict):
        raise PlatformError("market snapshot requires synchronized 1X2 and O/U")
    fair_1x2 = _fair_three_way(one["home"], one["draw"], one["away"])
    fair_over, fair_under = _fair_two_way(ou["over"], ou["under"])

    ah_diag = None
    ah = snapshot.get("asian_handicap")
    if isinstance(ah, dict) and isinstance(ah.get("line"), (int, float)):
        fair_ah_home, fair_ah_away = _fair_two_way(ah["home"], ah["away"])
        non_draw = fair_1x2["home"] + fair_1x2["away"]
        one_cond_home = fair_1x2["home"] / non_draw if non_draw > 0 else None
        ah_diag = {
            "line": float(ah["line"]),
            "fair_home": fair_ah_home,
            "fair_away": fair_ah_away,
            "one_x_two_conditional_home_given_non_draw": one_cond_home,
            "conditional_home_gap": None if one_cond_home is None else fair_ah_home - one_cond_home,
        }

    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256_file(path),
        "provider_name": snapshot.get("provider_name"),
        "provider_group": snapshot.get("provider_group"),
        "source_observed_at_utc": observed.isoformat(),
        "age_hours_at_freeze": (freeze - observed).total_seconds() / 3600.0,
        "raw_snapshot_sha256": snapshot.get("raw_snapshot_sha256"),
        "fair_1x2": fair_1x2,
        "fair_over": fair_over,
        "fair_under": fair_under,
        "ou_line": float(ou["line"]),
        "ah_consistency": ah_diag,
        "single_provider_pit_evidence": bool((snapshot.get("promotion_semantics") or {}).get("single_provider_pit_evidence")),
        "independent_provider_consensus": bool((snapshot.get("promotion_semantics") or {}).get("independent_provider_consensus")),
    }


def _season_start(value: str) -> int:
    text = str(value or "")
    try:
        return int(text[:4])
    except Exception:
        return -1


def _league_empirical_prior(history, *, recent_seasons: int = 5, smoothing: float = 0.20,
                            grid_max_goals: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seasons = sorted({str(m.season) for m in history}, key=_season_start)
    chosen = seasons[-int(recent_seasons):] if len(seasons) > recent_seasons else seasons
    rows = [m for m in history if str(m.season) in set(chosen)]
    if len(rows) < 300:
        rows = list(history)
        chosen = sorted({str(m.season) for m in rows}, key=_season_start)
    if not rows:
        raise PlatformError("league prior has no historical rows")

    counts: Counter[tuple[int, int]] = Counter()
    max_seen = 0
    for m in rows:
        h, a = int(m.home_goals), int(m.away_goals)
        if h < 0 or a < 0:
            continue
        counts[(h, a)] += 1
        max_seen = max(max_seen, h, a)
    if not counts:
        raise PlatformError("league prior has no valid completed scores")
    grid = max(int(grid_max_goals), min(12, max_seen))
    keys = [(h, a) for h in range(grid + 1) for a in range(grid + 1)]
    total = sum(counts.values()) + float(smoothing) * len(keys)
    prior = [
        {"home_goals": h, "away_goals": a, "probability": (counts.get((h, a), 0) + float(smoothing)) / total}
        for h, a in keys
    ]
    return prior, {
        "selected_seasons": chosen,
        "historical_match_count": sum(counts.values()),
        "dirichlet_cell_smoothing": float(smoothing),
        "score_grid_max_goals_each_team": grid,
    }


def predict_market_pit_coldstart(history, *, competition_id: str, season: str, home_team: str,
                                 away_team: str, kickoff, freeze, market_snapshot_path: str) -> dict[str, Any]:
    path = _safe_snapshot_path(market_snapshot_path)
    snapshot = load_json(path)
    market_audit = _validate_snapshot(
        snapshot, path,
        competition_id=competition_id,
        season=season,
        home_team=home_team,
        away_team=away_team,
        kickoff=kickoff,
        freeze=freeze,
    )
    prior_matrix, prior_audit = _league_empirical_prior(history)
    market_for_projection = {
        "one_x_two": dict(snapshot["one_x_two"]),
        "total_goals": dict(snapshot["over_under"]),
    }
    projected = project_market(prior_matrix, market_for_projection, include=("1x2", "ou"))
    if not bool(projected["audit"].get("converged")):
        raise PlatformError("PIT market KL projection did not converge")
    if float(projected["audit"].get("max_abs_constraint_residual", 1.0)) > 1e-7:
        raise PlatformError("PIT market KL projection residual exceeds gate")

    matrix = projected["matrix"]
    marg = derive_score_marginals(matrix)
    ranked = top_scores(matrix, 10)
    return {
        "competition_id": competition_id,
        "season": season,
        "history_matches": len(history),
        "probabilities": {
            "one_x_two": marg["1x2"],
            "total_goals": marg["total_goals"],
            "btts_yes": marg["btts_yes"],
            "score_matrix": matrix,
        },
        "top_scores": ranked,
        "audit": {
            "classification": "OPERATIONAL_SHADOW_PIT_MARKET_COLDSTART_R1",
            "formal_weight": 0,
            "target_result_used": False,
            "market_probability_mutation": True,
            "market_snapshot": market_audit,
            "league_prior": prior_audit,
            "projection": projected["audit"],
            "constraint_policy": "1X2_PLUS_OU; AH_RETAINED_AS_REDUNDANCY_DIAGNOSTIC",
            "external_runtime_network_required": False,
        },
    }
