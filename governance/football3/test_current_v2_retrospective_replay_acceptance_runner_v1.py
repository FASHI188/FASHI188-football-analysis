#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import current_v2_retrospective_replay_acceptance_runner_v1 as runner
import live_delta_acquisition_v1 as live

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"


def _upper():
    return datetime(2026, 9, 10, tzinfo=timezone.utc)


def _row(fid: str, kickoff: str, home: str, away: str):
    return {
        "competition_id": "JPN_J1",
        "season": "2026/27",
        "kickoff": kickoff,
        "home_team_name": home,
        "away_team_name": away,
        "fixture_id": fid,
        "score_or_result_fields_accessed_for_resolution": False,
    }


def _audit():
    return {
        "source_attempts": [],
        "fixture_identity_fallbacks": [],
        "fixture_identity_result_fields_accessed": False,
        "fallback_attempted": False,
        "failed_domain": None,
        "first_authoritative_failure": None,
    }


def _manifest(path: Path):
    obj = {
        "source_identity": "GOVERNED_TEST_FIXTURE_IDENTITY",
        "source_url": "https://example.invalid/j1-identity",
        "observed_at": "2026-09-01T00:00:00+00:00",
        "content_sha": "a" * 64,
        "parser_schema_version": "test-v1",
        "fixtures": [{
            "competition": "JPN_J1",
            "fixture_identity": "j1-test-1",
            "season": "2026/27",
            "kickoff": "2026-09-05T10:00:00+00:00",
            "home_team_name": "HOME",
            "away_team_name": "AWAY",
        }],
    }
    path.write_text(json.dumps(obj), encoding="utf-8")


def _metadata_stub(comp, season, kickoff, home, away, source, source_sha, time_authority):
    return {
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.astimezone(timezone.utc).isoformat(),
        "home_team_name": home,
        "away_team_name": away,
        "fixture_id": f"{comp}:{kickoff.isoformat()}:{home}:{away}",
        "source": source,
        "source_sha256": source_sha,
        "time_authority": time_authority,
        "score_or_result_fields_accessed_for_resolution": False,
    }


def test_jpn_503_frozen_manifest_takes_over(monkeypatch, tmp_path):
    manifest = tmp_path / "j1.json"
    _manifest(manifest)
    monkeypatch.setattr(runner.acceptance, "_metadata", _metadata_stub)
    calls = {"n": 0}

    def failed_fd(_upper):
        calls["n"] += 1
        raise live.AcquisitionError("source fetch failed: JPN.csv: HTTP Error 503: Service Temporarily Unavailable")

    audit = _audit()
    rows = runner._resolve_j1_candidates(_upper(), audit, failed_fd, manifest_path=manifest)
    assert calls["n"] == 1
    assert len(rows) == 1
    assert audit["j1_selected_source"] == "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST"
    assert audit["fallback_attempted"] is False
    assert rows[0]["score_or_result_fields_accessed_for_resolution"] is False


def test_jpn_503_espn_identity_fallback(monkeypatch):
    audit = _audit()

    def failed_fd(_upper):
        raise live.AcquisitionError("source fetch failed: JPN.csv: HTTP Error 503: Service Temporarily Unavailable")

    expected = [_row("x", "2026-09-05T10:00:00+00:00", "HOME", "AWAY")]

    def espn(_upper, _audit):
        return expected

    monkeypatch.setattr(runner, "_manifest_path", lambda: None)
    rows = runner._resolve_j1_candidates(_upper(), audit, failed_fd, espn_loader=espn)
    assert rows == expected
    assert audit["fallback_attempted"] is True
    assert audit["j1_selected_source"] == "ESPN_PUBLIC_SOCCER_API_TIER_2"
    assert audit["failed_domain"] is None


def test_football_data_and_fallback_identity_agree():
    a = [_row("a", "2026-09-05T10:00:00+00:00", "HOME", "AWAY")]
    b = [_row("b", "2026-09-05T10:00:00+00:00", "HOME", "AWAY")]
    runner._assert_identity_compatible(a, b)


def test_two_source_identity_conflict_fails_closed():
    a = [_row("a", "2026-09-05T10:00:00+00:00", "HOME", "AWAY")]
    b = [_row("b", "2026-09-05T10:00:00+00:00", "HOME", "OTHER")]
    with pytest.raises(live.AcquisitionError, match="J1_FIXTURE_IDENTITY_SOURCE_CONFLICT"):
        runner._assert_identity_compatible(a, b)


def test_all_sources_unavailable_fail_closed(monkeypatch):
    monkeypatch.setattr(runner, "_manifest_path", lambda: None)
    audit = _audit()

    def failed_fd(_upper):
        raise live.AcquisitionError("source fetch failed: JPN.csv: HTTP Error 503: Service Temporarily Unavailable")

    def failed_espn(_upper, _audit):
        raise live.AcquisitionError("ESPN identity source unavailable")

    with pytest.raises(live.AcquisitionError):
        runner._resolve_j1_candidates(_upper(), audit, failed_fd, espn_loader=failed_espn)
    assert audit["failed_domain"] == "JPN_J1"
    assert audit["first_authoritative_failure"]["stage"] == "J1_ACCEPTANCE_FIXTURE_DISCOVERY"


class GuardedDict(dict):
    forbidden = {"score", "homeScore", "awayScore", "result", "winner", "status"}

    def get(self, key, default=None):
        if key in self.forbidden:
            raise AssertionError(f"forbidden result/status field accessed: {key}")
        return super().get(key, default)

    def __getitem__(self, key):
        if key in self.forbidden:
            raise AssertionError(f"forbidden result/status field accessed: {key}")
        return super().__getitem__(key)


def _guarded_espn_payload():
    team_home = GuardedDict({"displayName": "HOME", "score": "99", "winner": True})
    team_away = GuardedDict({"displayName": "AWAY", "score": "98", "winner": False})
    c1 = GuardedDict({"homeAway": "home", "team": team_home, "score": "99", "winner": True})
    c2 = GuardedDict({"homeAway": "away", "team": team_away, "score": "98", "winner": False})
    comp = GuardedDict({
        "competitors": [c1, c2],
        "status": GuardedDict({"type": {"completed": True}}),
        "result": "HOME",
    })
    event = GuardedDict({
        "date": "2026-09-05T10:00:00Z",
        "competitions": [comp],
        "status": GuardedDict({"type": {"completed": True}}),
        "score": "99-98",
        "result": "HOME",
        "winner": "HOME",
    })
    return GuardedDict({"events": [event], "status": "completed", "result": "HOME"})


def test_fallback_payload_may_contain_result_fields_but_parser_never_reads_them():
    rows = runner._espn_identity_rows_from_object(
        _guarded_espn_payload(), "JPN_J1", _upper(), "https://example.invalid"
    )
    assert len(rows) == 1
    assert rows[0][1:] == ("HOME", "AWAY")


def test_property_access_sentinel_covers_score_status_result_winner():
    rows = runner._espn_identity_rows_from_object(
        _guarded_espn_payload(), "JPN_J1", _upper(), "https://example.invalid"
    )
    assert rows


def test_j1_set_sort_dedupe_and_sha_are_deterministic():
    rows = [
        _row("b", "2026-09-06T10:00:00+00:00", "B", "C"),
        _row("a", "2026-09-05T10:00:00+00:00", "A", "B"),
        _row("a", "2026-09-05T10:00:00+00:00", "A", "B"),
    ]
    first = runner._stable_fixture_rows(rows)
    second = runner._stable_fixture_rows(list(reversed(rows)))
    assert [x["fixture_id"] for x in first] == ["a", "b"]
    assert first == second
    assert runner._fixture_set_sha(first) == runner._fixture_set_sha(second)


def test_other_seven_domain_routing_is_not_widened(monkeypatch):
    original_main = runner.acceptance._main_candidates
    original_j1 = runner.acceptance._j1_candidates
    original_k1 = runner.acceptance._k1_candidates
    original_ucl = runner.acceptance._ucl_candidate
    audit = _audit()
    old_main, old_j1 = runner.install_fixture_identity_fallback(audit)
    try:
        assert old_main is original_main
        assert old_j1 is original_j1
        assert runner.acceptance._k1_candidates is original_k1
        assert runner.acceptance._ucl_candidate is original_ucl
        assert "JPN_J1" not in runner.ESPN_LEAGUE
        assert runner.J1_ESPN_SLUG == "jpn.1"
    finally:
        runner.acceptance._main_candidates = old_main
        runner.acceptance._j1_candidates = old_j1


def test_prospective_and_strict_pit_flags_remain_unchanged():
    original, audit = runner.install_retry()
    try:
        assert audit["prospective_path_changed"] is False
        assert audit["strict_pit_path_changed"] is False
        assert audit["model_or_current_or_weight_changed"] is False
        assert audit["formal_authority_changed"] is False
    finally:
        live._fetch = original


def test_failure_diagnostics_are_written_without_gate_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("FOOTBALL3_CANDIDATE_EXACT_HEAD", "deadbeef")
    audit = _audit()
    audit.update({
        "status": "FAIL",
        "score_blind_guard_status": "PASS",
        "source_attempts": [{"source": "FOOTBALL_DATA_JPN_CSV", "outcome": "FAIL", "error": "HTTP Error 503"}],
        "fallback_attempted": True,
        "failed_domain": "JPN_J1",
        "completed_fixture_count": 0,
        "first_authoritative_failure": {"stage": "J1_ACCEPTANCE_FIXTURE_DISCOVERY"},
    })
    runner._write_diagnostics(tmp_path, audit, 1)
    obj = json.loads((tmp_path / "failure_diagnostics.json").read_text(encoding="utf-8"))
    assert obj["status"] == "FAIL"
    assert obj["candidate_exact_head"] == "deadbeef"
    assert obj["failed_domain"] == "JPN_J1"
    assert obj["failure_artifact_is_gate_pass"] is False
    assert obj["score_blind_guard_status"] == "PASS"


def test_transport_retries_at_least_three_before_j1_fallback(monkeypatch):
    calls = {"n": 0}

    def always_503(url, *, data=None, headers=None, timeout=60):
        calls["n"] += 1
        raise live.AcquisitionError(f"source fetch failed: {url}: HTTP Error 503: Service Temporarily Unavailable")

    monkeypatch.setattr(live, "_fetch", always_503)
    monkeypatch.setattr(runner.time, "sleep", lambda _x: None)
    captured, audit = runner.install_retry()
    try:
        with pytest.raises(live.AcquisitionError):
            live._fetch(runner.JPN_URL)
        assert calls["n"] >= 3
        assert any(x.get("url") == runner.JPN_URL and x.get("outcome") == "FAIL" for x in audit["source_attempts"])
    finally:
        live._fetch = captured
        assert captured is always_503
