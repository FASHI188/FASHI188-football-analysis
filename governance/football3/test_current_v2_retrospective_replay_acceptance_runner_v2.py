from __future__ import annotations

from datetime import datetime, timezone

import current_v2_retrospective_replay_acceptance_runner_v2 as runner

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"


def upper():
    return datetime(2026, 9, 10, tzinfo=timezone.utc)


def acceptance_row(comp: str, source_sha: str = "a" * 64):
    season = "2026" if comp == "KOR_KLeague1" else "2026/27"
    return {
        "competition_id": comp,
        "season": season,
        "kickoff": "2026-09-05T10:00:00+00:00",
        "home_team_name": "HOME",
        "away_team_name": "AWAY",
        "fixture_id": f"{comp}:fixture",
        "source": "https://example.invalid/source",
        "source_sha256": source_sha,
        "time_authority": "TEST",
        "score_or_result_fields_accessed_for_resolution": False,
    }


class GuardedDict(dict):
    forbidden = {"score", "homeScore", "awayScore", "result", "winner", "points", "outcome", "status"}
    def get(self, key, default=None):
        if key in self.forbidden:
            raise AssertionError(f"forbidden field accessed: {key}")
        return super().get(key, default)
    def __getitem__(self, key):
        if key in self.forbidden:
            raise AssertionError(f"forbidden field accessed: {key}")
        return super().__getitem__(key)


def test_manifest_parser_never_reads_score_result_winner_points_outcome_status(monkeypatch):
    item = GuardedDict({
        "competition": "ENG_PremierLeague",
        "fixture_identity": "fixture-1",
        "season": "2026/27",
        "kickoff": "2026-09-05T10:00:00+00:00",
        "home_team_name": "HOME",
        "away_team_name": "AWAY",
        "score": "99-98",
        "result": "H",
        "winner": "HOME",
        "points": 3,
        "outcome": "HOME_WIN",
        "status": "FT 99-98",
    })
    obj = GuardedDict({
        "source_identity": "GOVERNED_TEST",
        "source_url": "https://example.invalid/manifest",
        "observed_at": "2026-09-01T00:00:00+00:00",
        "content_sha": "a" * 64,
        "fixtures": [item],
        "status": "contains score text 99-98",
        "result": "HOME",
    })
    monkeypatch.setattr(runner.acceptance, "_metadata", lambda comp, season, kickoff, home, away, source, source_sha, time_authority: {
        **acceptance_row(comp, source_sha),
        "season": season,
        "kickoff": kickoff.astimezone(timezone.utc).isoformat(),
        "home_team_name": home,
        "away_team_name": away,
    })
    rows = runner._manifest_rows_from_object(obj, "ENG_PremierLeague", upper())
    assert len(rows) == 1
    assert rows[0]["score_or_result_fields_accessed_for_resolution"] is False


def test_provider_projection_has_exact_allowed_fields():
    rows = runner._provider_records("ENG_PremierLeague", [acceptance_row("ENG_PremierLeague")], "SOURCE", "2026-09-01T00:00:00+00:00")
    assert set(rows[0]) == set(runner.chain.PROVIDER_FIELDS)


def test_install_common_chain_covers_all_eight_domains_without_prospective_or_strict_pit_change():
    original_main = runner.acceptance._main_candidates
    original_j1 = runner.acceptance._j1_candidates
    original_k1 = runner.acceptance._k1_candidates
    original_ucl = runner.acceptance._ucl_candidate
    audit = {"source_attempts": [], "fixture_identity_fallbacks": [], "fixture_identity_provider_attempts": []}
    old_main, old_j1 = runner.install_common_fixture_identity_provider_chain(audit)
    try:
        assert old_main is original_main and old_j1 is original_j1
        assert runner.acceptance._main_candidates is not original_main
        assert runner.acceptance._j1_candidates is not original_j1
        assert runner.acceptance._k1_candidates is not original_k1
        assert runner.acceptance._ucl_candidate is not original_ucl
        assert set(audit["provider_chain_supported_domains"]) == set(runner.acceptance.DOMAINS)
        assert audit["provider_chain_prospective_path_changed"] is False
        assert audit["provider_chain_strict_pit_path_changed"] is False
        assert audit["provider_chain_model_current_weights_changed"] is False
    finally:
        runner.acceptance._main_candidates = original_main
        runner.acceptance._j1_candidates = original_j1
        runner.acceptance._k1_candidates = original_k1
        runner.acceptance._ucl_candidate = original_ucl


def test_domain_provider_order_is_generic_and_ucl_never_uses_league_csv(monkeypatch):
    original_main = runner.acceptance._main_candidates
    original_j1 = runner.acceptance._j1_candidates
    original_k1 = runner.acceptance._k1_candidates
    original_ucl = runner.acceptance._ucl_candidate
    captured = {}
    def fake_run(comp, _upper, _audit, providers):
        captured[comp] = [name for name, _priority, _loader in providers]
        return [acceptance_row(comp)]
    monkeypatch.setattr(runner, "_run_provider_chain", fake_run)
    audit = {}
    runner.install_common_fixture_identity_provider_chain(audit)
    try:
        for comp in ("ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"):
            runner.acceptance._main_candidates(comp, upper())
        runner.acceptance._j1_candidates(upper())
        runner.acceptance._k1_candidates(upper())
        runner.acceptance._ucl_candidate(upper())
    finally:
        runner.acceptance._main_candidates = original_main
        runner.acceptance._j1_candidates = original_j1
        runner.acceptance._k1_candidates = original_k1
        runner.acceptance._ucl_candidate = original_ucl
    codes = runner.live.MAIN_EUROPE
    for comp in ("ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"):
        assert captured[comp] == ["GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST", "ESPN_PUBLIC_SOCCER_API_TIER_2", f"FOOTBALL_DATA_{codes[comp]}_CSV"]
    assert captured["JPN_J1"] == ["GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST", "ESPN_PUBLIC_SOCCER_API_TIER_2", "FOOTBALL_DATA_JPN_CSV"]
    assert captured["KOR_KLeague1"] == ["GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST", "ESPN_PUBLIC_SOCCER_API_TIER_2", "OFFICIAL_KLEAGUE_SCHEDULE_API"]
    assert captured["UEFA_ChampionsLeague"] == ["GOVERNED_UCL_36_TEAM_AUTHORITY_BRIDGE"]


def test_successful_fallback_source_failure_is_recorded_without_domain_failure(monkeypatch):
    comp = "ENG_PremierLeague"
    audit = {"source_attempts": [], "fixture_identity_provider_attempts": []}
    monkeypatch.setattr(runner.acceptance, "_now", lambda: datetime(2026, 9, 9, tzinfo=timezone.utc))
    def unavailable():
        return []
    def fallback():
        return [acceptance_row(comp)]
    def primary_503():
        raise runner.live.AcquisitionError("E0.csv: HTTP Error 503: Service Temporarily Unavailable")
    rows = runner._run_provider_chain(comp, upper(), audit, [
        ("GOVERNED_MANIFEST", 1, unavailable),
        ("APPROVED_FALLBACK", 2, fallback),
        ("FOOTBALL_DATA_E0_CSV", 3, primary_503),
    ])
    assert rows
    assert audit.get("failed_domain") is None
    assert audit["fixture_identity_selected_provider"][comp] == "APPROVED_FALLBACK"
    assert any(x.get("source") == "FOOTBALL_DATA_E0_CSV" and x.get("http_status") == 503 for x in audit["source_attempts"])
