#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import formal_state_integrity_guard_v1 as guard
import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

SCHEMA = "football3-formal-state-integrity-xg-history-count-fix-v1"

_HISTORY_CACHE: dict[str, list[Any]] = {}
_INSTALLED = False


def _history(repo_root: Path):
    key = str(repo_root.resolve())
    rows = _HISTORY_CACHE.get(key)
    if rows is None:
        rows, _ = rt.load_frozen_v1_history(repo_root)
        _HISTORY_CACHE[key] = rows
    return rows


def _xg_profile(repo_root: Path, state: Any, team_id: str) -> tuple[int, dict[str, int]]:
    seen = set(getattr(state, "seen", set()))
    by_season: dict[str, int] = {}
    n = 0
    for f in _history(repo_root):
        if f.fixture_id not in seen:
            continue
        if f.home_team_id != team_id and f.away_team_id != team_id:
            continue
        n += 1
        by_season[f.season] = by_season.get(f.season, 0) + 1
    return n, dict(sorted(by_season.items()))


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {
            "schema_version": SCHEMA,
            "installed": True,
            "idempotent_reentry": True,
            "model_parameters_or_weights_changed": False,
            "formal_current_or_production_pointer_changed": False,
        }

    original_resolve_fixture = identity_bridge.resolve_fixture
    original_classify_state = guard.classify_state

    def resolve_fixture(repo_root: Path, state: Any, comp: str, season: str,
                        home: str, away: str, kickoff):
        fixture, audit = original_resolve_fixture(repo_root, state, comp, season, home, away, kickoff)
        for side, team_id in (("home", fixture["home_team_id"]), ("away", fixture["away_team_id"])):
            n, by_season = _xg_profile(repo_root, state, team_id)
            audit[side]["xg_historical_match_count"] = n
            audit[side]["xg_historical_matches_by_season"] = by_season
            audit[side]["xg_history_count_source"] = "formal_fixture_ids_intersect_frozen_xg_state_seen"
        return fixture, audit

    def classify_state(loaded: dict[str, Any], fixture: dict[str, Any], identity_audit: dict[str, Any],
                       trigger: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        out = original_classify_state(loaded, fixture, identity_audit, trigger, receipt)
        xg = out["historical_xg"]
        home = identity_audit.get("home") or {}
        away = identity_audit.get("away") or {}
        home_n = int(home.get("xg_historical_match_count", 0))
        away_n = int(away.get("xg_historical_match_count", 0))
        home_by = dict(home.get("xg_historical_matches_by_season") or {})
        away_by = dict(away.get("xg_historical_matches_by_season") or {})
        xg["home_historical_match_count"] = home_n
        xg["away_historical_match_count"] = away_n
        xg["home_historical_matches_by_season"] = home_by
        xg["away_historical_matches_by_season"] = away_by
        xg["history_count_source"] = "formal_fixture_ids_intersect_frozen_xg_state_seen"

        # The target-scoped historical repair bundle predates the generic cross-season coverage
        # schema. For receipt/reporting only, recover its represented 2025/26 match counts from
        # the sealed state itself. This does not create labels or change prediction state.
        if int(xg.get("home_linked_2025_26_matches", 0)) == 0 and home_by.get("2025/26"):
            xg["home_linked_2025_26_matches"] = int(home_by["2025/26"])
        if int(xg.get("away_linked_2025_26_matches", 0)) == 0 and away_by.get("2025/26"):
            xg["away_linked_2025_26_matches"] = int(away_by["2025/26"])
        xg["expected_legal_xg_for_both_teams"] = (
            int(xg.get("home_linked_2025_26_matches", 0)) >= guard.MIN_EXPECTED_LINKED_XG_MATCHES
            and int(xg.get("away_linked_2025_26_matches", 0)) >= guard.MIN_EXPECTED_LINKED_XG_MATCHES
        )
        return out

    identity_bridge.resolve_fixture = resolve_fixture
    guard.classify_state = classify_state
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "xg_history_count_semantics": "count formal historical fixture ids represented in frozen xg state.seen per canonical team, plus by-season counts",
        "target_scoped_replay_metadata_zero_is_not_treated_as_true_zero_when_state_seen_proves_history": True,
        "prediction_state_mutated": False,
        "new_labels_read": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
