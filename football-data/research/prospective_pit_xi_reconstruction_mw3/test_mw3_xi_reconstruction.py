from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "reconstruct_mw3_expected_xi.py"
CONTRACT = HERE / "MW3_XI_RECONSTRUCTION_CONTRACT.json"
FIXTURES = HERE.parent / "prospective_pit_availability_xi_shadow_mw3" / "ENG_PL_2026_27_MW3_FIXTURE_FREEZE.json"

spec = importlib.util.spec_from_file_location("mw3xi", RUNNER)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_contract_is_frozen_before_target_and_zero_label():
    c = json.loads(CONTRACT.read_text())
    f = json.loads(FIXTURES.read_text())
    assert c["status"] == "FROZEN_BEFORE_MW3_CONFIRMED_XI_REVEAL"
    earliest = min(m.parse_utc(x["kickoff_at_utc"]) for x in f["fixtures"])
    assert m.parse_utc(c["frozen_at_utc"]) < earliest
    g = c["governance"]
    for key in (
        "target_result_access", "target_score_access", "target_confirmed_xi_access",
        "target_postmatch_event_access", "market_or_odds_access", "parameter_search",
        "training_or_refit", "post_view_parameter_tuning", "2023_confirmation_set_access",
        "3504_access", "formal_model_change_allowed", "CURRENT_change_allowed",
        "production_pointer_change_allowed", "formal_weights_change_allowed",
    ):
        assert g[key] is False


def test_locked_snapshot_identity_is_exact():
    c = json.loads(CONTRACT.read_text())
    s = c["locked_snapshot_input"]
    assert c["parent_snapshot_head"] == "ff6d96d89d8ced2309c1a92aa2cb13506ca92bcf"
    assert s["run_id"] == 33830397470
    assert s["artifact_id"] == 9921466941
    assert s["artifact_digest"] == "sha256:3daa8c4a947a4c4dc86c94f1d7b0a41ae5f1a71b2b5a86fd1271f42b45968181"
    assert s["snapshot_sha256"] == "e6b609266d7e54311a764fe7668da2b651634d457bb6346daf6a483798028333"


def test_transport_constants_are_exact_and_not_fit():
    c = json.loads(CONTRACT.read_text())
    t = c["transported_mechanism"]
    assert t["decay"] == 0.78 == m.DECAY
    assert t["smoothing"] == 0.50 == m.SMOOTH
    assert t["target_probability_sum_per_team"] == 11.0 == m.TARGET_SUM
    assert t["no_new_fit"] is True
    assert c["governance"]["training_or_refit"] is False
    assert c["governance"]["parameter_search"] is False


def test_raw_probability_is_monotone_and_fixed():
    p00 = m.raw_start_probability(0, 0)
    p10 = m.raw_start_probability(1, 0)
    p01 = m.raw_start_probability(0, 1)
    p11 = m.raw_start_probability(1, 1)
    assert 0 < p00 < p10 < p01 < p11 < 1
    assert abs(p00 - (0.5 / 2.78)) < 1e-12
    assert abs(p11 - (2.28 / 2.78)) < 1e-12


def test_projection_sums_to_eleven_and_preserves_order():
    raw = [0.82] * 8 + [0.55] * 6 + [0.18] * 10
    q = m.project_sum(raw)
    assert len(q) == len(raw)
    assert abs(sum(q) - 11.0) < 1e-7
    assert q[0] > q[8] > q[14]
    assert all(0 < x < 1 for x in q)
    assert m.project_sum([0.3] * 11) == [1.0] * 11


def test_primary_and_stress_availability_rules_are_frozen():
    assert m.primary_excluded("i", None)
    assert m.primary_excluded("s", None)
    assert m.primary_excluded("u", None)
    assert m.primary_excluded("a", 0)
    assert not m.primary_excluded("d", 50)
    assert not m.primary_excluded("a", 25)
    assert m.stress_excluded("d", None)
    assert m.stress_excluded("a", 50)
    assert not m.stress_excluded("a", 75)


def test_deterministic_ranking_probability_minutes_player_id():
    rows = [
        {"player_id": 30, "p": 0.4, "locked_bootstrap_minutes": 180},
        {"player_id": 20, "p": 0.5, "locked_bootstrap_minutes": 90},
        {"player_id": 10, "p": 0.5, "locked_bootstrap_minutes": 180},
        {"player_id": 5, "p": 0.5, "locked_bootstrap_minutes": 180},
    ]
    ranked = m.rank_rows(rows, "p")
    assert [x["player_id"] for x in ranked] == [5, 10, 20, 30]


def test_fixture_cohort_and_output_contract_are_exact():
    c = json.loads(CONTRACT.read_text())
    f = json.loads(FIXTURES.read_text())
    assert len(f["fixtures"]) == 10
    assert len(set(f["expected_team_short_names"])) == 20
    out = c["output_contract"]
    assert out["team_receipts"] == 20
    assert out["fixture_receipts"] == 10
    assert out["expected_xi_size"] == 11
    assert out["variants"] == ["base", "primary", "stress"]
    assert out["include_target_confirmed_xi"] is False
    assert out["include_target_result"] is False
    assert out["include_1x2_probability"] is False


def test_source_gate_is_exact_220_starters_per_completed_round():
    c = json.loads(CONTRACT.read_text())
    s = c["prior_round_sources"]
    assert s["events"] == [1, 2]
    assert s["required_fixture_count_per_event"] == 10
    assert s["required_all_fixtures_finished"] is True
    assert s["required_starts_count_per_event"] == 220
    assert s["required_player_stat"] == "starts"


def test_receipt_schema_has_no_actual_target_or_1x2_fields():
    # Guard the intended receipt keys mechanically; actual writer must stay label-free.
    source = RUNNER.read_text()
    forbidden_literal_keys = [
        '"actual_xi"', '"confirmed_xi"', '"result"', '"score"',
        '"home_win_probability"', '"draw_probability"', '"away_win_probability"',
    ]
    for key in forbidden_literal_keys:
        assert key not in source
    assert '"contains_1x2_probability": False' in source
    assert '"target_confirmed_xi_access": False' in source
    assert '"target_result_access": False' in source
