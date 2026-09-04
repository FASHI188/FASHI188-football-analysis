#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import target_identity_replay_fix_v1 as fix
import runtime as rt


class _State:
    pass


def _fake_state(comp: str, present_ids: list[str]):
    class Base:
        pass
    s = _State()
    s.base = Base()
    s.base.teams_local = {(comp, x): object() for x in present_ids}
    s.base.teams_global = {x: object() for x in present_ids}
    return s


def test_stuttgart_official_name_resolves_to_historical_strength_id():
    comp = "GER_Bundesliga"
    historical_id = rt._global_team_id("Stuttgart")
    official_id = rt._global_team_id("VfB Stuttgart")
    assert historical_id != official_id
    state = _fake_state(comp, [historical_id])
    repo_root = Path(__file__).resolve().parents[2]
    resolved = fix._resolve_team(repo_root, state, comp, "2026/27", "VfB Stuttgart")
    assert resolved["strength_team_id"] == historical_id
    assert resolved["strength_alias"] == "Stuttgart"
    assert resolved["state_evidence"] is True
    assert resolved["resolution"] == "UNIQUE_EXISTING_STRENGTH_ID_FROM_EXPLICIT_CURRENT_SEASON_ALIASES"


def test_koln_resolution_keeps_unique_existing_strength_id():
    comp = "GER_Bundesliga"
    historical_id = rt._global_team_id("FC Koln")
    state = _fake_state(comp, [historical_id])
    repo_root = Path(__file__).resolve().parents[2]
    resolved = fix._resolve_team(repo_root, state, comp, "2026/27", "FC Koln")
    assert resolved["strength_team_id"] == historical_id
    assert resolved["state_evidence"] is True


def test_registered_club_without_history_does_not_borrow_other_strength():
    comp = "GER_Bundesliga"
    other_id = rt._global_team_id("Stuttgart")
    state = _fake_state(comp, [other_id])
    repo_root = Path(__file__).resolve().parents[2]
    resolved = fix._resolve_team(repo_root, state, comp, "2026/27", "Paderborn 07")
    assert resolved["strength_team_id"] == rt._global_team_id("Paderborn 07")
    assert resolved["state_evidence"] is False
    assert resolved["resolution"] == "REGISTERED_CURRENT_SEASON_CLUB_WITHOUT_HISTORICAL_STRENGTH_STATE"


def test_exact_replay_input_is_empty_and_sealed():
    fixture = {
        "fixture_id": "abc",
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "kickoff": "2026-09-04T18:30:00+00:00",
        "home_team_id": rt._global_team_id("Stuttgart"),
        "away_team_id": rt._global_team_id("FC Koln"),
        "home_team_name": "VfB Stuttgart",
        "away_team_name": "FC Koln",
    }
    cutoff = rt._parse_dt("2026-09-04T00:25:59+00:00", "cutoff")
    loaded = {"source": {"live_acquisition": {"source_set_sha256": "a" * 64}}}
    inp = fix._sealed_replay_input(fixture, cutoff, loaded)
    assert inp["model_delta"] == []
    assert inp["cutoff"] == cutoff.isoformat()
    assert inp["delta_coverage"]["status"] == "COMPLETE"
    assert inp["delta_coverage"]["verification"] == "VERIFIED_COMPLETE"
    assert inp["delta_coverage"]["state_preapplied_and_sealed"] is True
    assert inp["delta_coverage"]["acquisition_schema"] == "SEALED_EXACT_CUTOFF_REPLAY"
