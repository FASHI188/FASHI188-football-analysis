#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import current_v2_retrospective_replay_v1 as replay
import current_v2_retrospective_xg_coverage_v1 as coverage

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"


class _BombMapping(dict):
    def get(self, *args, **kwargs):
        raise AssertionError("post-cutoff score/xG field was read")


class _ScoreSentinel(dict):
    def get(self, key, default=None):
        if key == "score":
            raise AssertionError("score must not be read before cutoff eligibility")
        return super().get(key, default)


class _GoalSentinel(dict):
    def get(self, key, default=None):
        if key in {"homeGoal", "awayGoal"}:
            raise AssertionError("goal must not be read before cutoff eligibility")
        return super().get(key, default)


class CurrentV2RetrospectiveXGCoverageTests(unittest.TestCase):
    def _row(self, kickoff: datetime, fixture_id: str = "fixture-1"):
        return SimpleNamespace(
            competition_id="ITA_SerieA",
            kickoff=kickoff,
            home_team_name="Udinese",
            away_team_name="Lazio",
            fixture_id=fixture_id,
            home_goals=1,
            away_goals=0,
        )

    def test_install_changes_only_research_replay_history_and_xg_hooks(self):
        original_safe_history_upper = object()
        module = SimpleNamespace(
            _safe_history_upper=original_safe_history_upper,
            _research_v1_rows=object(),
            _ucl_history=object(),
            _current_xg_labels=object(),
        )
        audit = coverage.install(module)
        self.assertIs(module._current_xg_labels, coverage.research_xg_labels)
        self.assertIs(module._safe_history_upper, original_safe_history_upper)
        self.assertIs(module._research_v1_rows, coverage.score_history.research_v1_rows)
        self.assertIs(module._ucl_history, coverage.score_history.research_ucl_history)
        self.assertEqual(audit["request_mode"], replay.MODE)
        self.assertEqual(audit["missing_xg_policy"], "DELEGATE_TO_CURRENT_FORMAL_V2_EVIDENCE_ROUTE")
        self.assertEqual(audit["score_history_adapter"]["fail_closed_error"], "RETROSPECTIVE_SCORE_HISTORY_UNAVAILABLE")
        self.assertFalse(audit["fallback_forced"])
        self.assertFalse(audit["evidence_threshold_changed"])
        self.assertFalse(audit["model_or_current_or_weight_changed"])
        self.assertFalse(audit["score_history_adapter"]["model_or_current_or_weight_changed"])
        self.assertFalse(audit["score_history_adapter"]["prospective_path_changed"])
        self.assertFalse(audit["score_history_adapter"]["strict_pit_path_changed"])
        self.assertFalse(audit["score_history_adapter"]["strict_pit_claimed"])
        self.assertIn("RESEARCH_ONLY", audit["score_history_adapter"]["classification"])

    def test_target_or_post_cutoff_row_is_rejected_before_score_or_xg_access(self):
        upper = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        lower = upper - timedelta(days=7)
        historical_row = self._row(upper - timedelta(days=1))
        payload = {
            "dates": [{
                "datetime": upper.isoformat(),
                "h": _BombMapping(),
                "a": _BombMapping(),
                "xG": _BombMapping(),
                "goals": _BombMapping(),
                "isResult": True,
            }]
        }
        with mock.patch.object(coverage.live, "BIG5", {"ITA_SerieA"}), \
             mock.patch.object(coverage.live, "UNDERSTAT", {"ITA_SerieA": "Serie_A"}), \
             mock.patch.object(coverage.live, "_cross_year_starts", return_value=[2026]), \
             mock.patch.object(coverage.live, "_understat_payload", return_value=(payload, "a" * 64, "synthetic://understat")):
            labels, audit = coverage.research_xg_labels([historical_row], lower, upper, object())
        self.assertEqual(labels, {})
        self.assertEqual(audit["missing_xg_history_count"], 1)
        self.assertFalse(audit["target_score_fields_read_before_eligibility_gate"])
        self.assertTrue(audit["missing_xg_delegated_to_formal_evidence_route"])
        self.assertTrue(audit["fallback_not_forced_by_adapter"])

    def test_prior_history_xg_joins_without_forcing_route(self):
        upper = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        lower = upper - timedelta(days=7)
        kickoff = upper - timedelta(days=1)
        historical_row = self._row(kickoff)
        payload = {
            "dates": [{
                "datetime": kickoff.isoformat(),
                "h": {"title": "Udinese"},
                "a": {"title": "Lazio"},
                "xG": {"h": "1.25", "a": "0.75"},
                "goals": {"h": "1", "a": "0"},
                "isResult": True,
            }]
        }
        with mock.patch.object(coverage.live, "BIG5", {"ITA_SerieA"}), \
             mock.patch.object(coverage.live, "UNDERSTAT", {"ITA_SerieA": "Serie_A"}), \
             mock.patch.object(coverage.live, "_cross_year_starts", return_value=[2026]), \
             mock.patch.object(coverage.live, "_understat_payload", return_value=(payload, "b" * 64, "synthetic://understat")):
            labels, audit = coverage.research_xg_labels([historical_row], lower, upper, object())
        self.assertEqual(set(labels), {historical_row.fixture_id})
        self.assertEqual(audit["status"], "COMPLETE")
        self.assertEqual(audit["joined_results"], 1)
        self.assertEqual(audit["missing_xg_history_count"], 0)
        self.assertFalse(audit["strict_pit_claimed"])
        self.assertTrue(audit["missing_xg_delegated_to_formal_evidence_route"])
        self.assertTrue(audit["fallback_not_forced_by_adapter"])

    def test_source_unavailable_is_coverage_limitation_not_adapter_failure(self):
        upper = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        lower = upper - timedelta(days=7)
        historical_row = self._row(upper - timedelta(days=1))
        with mock.patch.object(coverage.live, "BIG5", {"ITA_SerieA"}), \
             mock.patch.object(coverage.live, "UNDERSTAT", {"ITA_SerieA": "Serie_A"}), \
             mock.patch.object(coverage.live, "_cross_year_starts", return_value=[2026]), \
             mock.patch.object(coverage.live, "_understat_payload", side_effect=coverage.live.AcquisitionError("synthetic unavailable")):
            labels, audit = coverage.research_xg_labels([historical_row], lower, upper, object())
        self.assertEqual(labels, {})
        self.assertEqual(audit["status"], "COMPLETE_WITH_LEGAL_XG_COVERAGE_LIMITATION")
        self.assertEqual(audit["missing_xg_history_count"], 1)
        self.assertTrue(audit["missing_xg_delegated_to_formal_evidence_route"])
        self.assertTrue(audit["fallback_not_forced_by_adapter"])

    def test_audit_hash_is_local_deterministic_canonical_json(self):
        value = ["fixture-a", "fixture-b"]
        expected_payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(coverage._audit_sha256(value), hashlib.sha256(expected_payload).hexdigest())
        source = Path(coverage.__file__).read_text(encoding="utf-8")
        self.assertNotIn("rt._canon_bytes", source)
        self.assertNotIn("rt._sha_bytes", source)

    def test_entry_wiring_order_preserves_gateway_and_receipt_layers(self):
        entry = Path(__file__).with_name("entry.py").read_text(encoding="utf-8")
        durable_pos = entry.index("formal_durable_state_governance_v1.install(gateway)")
        exact_pos = entry.index("current_v2_retrospective_exact_history_v1.install(current_v2_retrospective_replay_v1)")
        coverage_pos = entry.index("current_v2_retrospective_xg_coverage_v1.install(current_v2_retrospective_replay_v1)")
        replay_pos = entry.index("current_v2_retrospective_replay_v1.install(gateway)")
        receipt_pos = entry.index("current_v2_retrospective_receipt_contract_v1.install(gateway)")
        self.assertLess(durable_pos, exact_pos)
        self.assertLess(exact_pos, coverage_pos)
        self.assertLess(coverage_pos, replay_pos)
        self.assertLess(replay_pos, receipt_pos)

    def test_downstream_research_state_consumes_provider_rows_before_prediction(self):
        source = Path(replay.__file__).read_text(encoding="utf-8")
        build_start = source.index("def _build_research_state")
        build_end = source.index("def run", build_start)
        build = source[build_start:build_end]
        self.assertLess(build.index("_research_v1_rows"), build.index("_current_xg_labels"))
        final_replay = build.index("state, replay = rt.replay_history_state(combined, labels, target_kickoff)")
        self.assertLess(build.index("current_history = [_history_fixture(r) for r in current_rows]"), final_replay)
        run = source[source.index("def run"):]
        self.assertIn("rt._prediction_from_state(state, target)", run)


class CurrentV2RetrospectiveScoreHistoryTests(unittest.TestCase):
    def _v1row(self, comp: str, kickoff: datetime, hg: int = 1, ag: int = 0,
               home: str = "Alpha", away: str = "Beta"):
        sh = coverage.score_history
        fid = sh.rt._fixture_id(comp, "2026/27", kickoff, home, away)
        return sh.live.V1Row(
            fid, comp, "2026/27", kickoff, home, away,
            sh.rt._global_team_id(home), sh.rt._global_team_id(away),
            hg, ag, "synthetic://score", "a" * 64,
        )

    def test_openfootball_pin_is_big5_only_and_time_bounded(self):
        sh = coverage.score_history
        self.assertEqual(sh.OPENFOOTBALL_PIN, "8aa4cd0ce0410b21037f063eeb4edd981081d85d")
        self.assertEqual(set(sh.OPENFOOTBALL_FILES), {
            "ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
        })
        self.assertEqual(sh.OPENFOOTBALL_COVERAGE_END, datetime(2026, 9, 3, tzinfo=timezone.utc))
        self.assertEqual(sh.LICENSE, "CC0-1.0")
        self.assertNotIn("JPN_J1", sh.OPENFOOTBALL_FILES)
        self.assertNotIn("KOR_KLeague1", sh.OPENFOOTBALL_FILES)
        self.assertNotIn(replay.UCL, sh.OPENFOOTBALL_FILES)

    def test_second_cc0_source_has_all_big5_exact_pins_and_blobs(self):
        sh = coverage.score_history
        self.assertEqual(set(sh.OPENFOOTBALL_UPSTREAM), set(sh.OPENFOOTBALL_FILES))
        for spec in sh.OPENFOOTBALL_UPSTREAM.values():
            self.assertEqual(len(spec["commit"]), 40)
            self.assertEqual(len(spec["git_blob"]), 40)
            self.assertTrue(spec["path"])
            self.assertEqual(spec["commit_date"], "2026-09-08")

    def test_public_identity_is_strict_and_never_fuzzy(self):
        sh = coverage.score_history
        with mock.patch.object(sh.rt, "_read_aliases", return_value={
            "ENG_PremierLeague": {"Man United": "Manchester United"}
        }):
            self.assertEqual(
                sh._strict_identity(Path("."), "ENG_PremierLeague", "Man United", {"Manchester United"}),
                "Manchester United",
            )
            self.assertIsNone(
                sh._strict_identity(Path("."), "ENG_PremierLeague", "Manchester Utd", {"Manchester United"}),
            )

    def test_public_score_conflict_fails_closed(self):
        sh = coverage.score_history
        ko = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
        a = self._v1row("ENG_PremierLeague", ko, 1, 0)
        b = self._v1row("ENG_PremierLeague", ko, 2, 0)
        with self.assertRaises(sh.rt.RuntimeGateError) as ctx:
            sh._combine([("OPENFOOTBALL_PINNED_CC0", [a]), ("OPENFOOTBALL_UPSTREAM_PINNED_CC0", [b])])
        self.assertIn("RETROSPECTIVE_SCORE_HISTORY_UNAVAILABLE", str(ctx.exception))

    def test_openfootball_cutoff_gate_precedes_score_access(self):
        sh = coverage.score_history
        lower = datetime(2026, 8, 1, tzinfo=timezone.utc)
        upper = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        item = _ScoreSentinel({
            "date": "2026-09-01",
            "time": "15:00",
            "team1": "Alpha",
            "team2": "Beta",
        })
        with mock.patch.object(sh, "_fetch_json", return_value=({"matches": [item]}, "b" * 64)):
            rows, sources, unresolved = sh._openfootball_pinned_comp(
                Path("."), "ITA_SerieA", set(), lower, upper, "2026-09-09T00:00:00+00:00")
        self.assertEqual(rows, [])
        self.assertEqual(unresolved, 0)
        self.assertTrue(sources)

    def test_upstream_footballtxt_target_day_score_is_not_interpreted(self):
        sh = coverage.score_history
        payload = b"= English Premier League 2026/27\n\n  Fri Sep 4 2026\n    20:00  Alpha FC v Beta FC    NOT-A-SCORE\n"
        with mock.patch.object(sh.live, "_fetch", return_value=(payload, "c" * 64)):
            rows, source, unresolved = sh._footballtxt_rows_for_comp(
                Path("."), "ENG_PremierLeague", set(),
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc),
                "2026-09-09T00:00:00+00:00",
            )
        self.assertEqual(rows, [])
        self.assertEqual(source["scheduled_in_window"], 0)
        self.assertEqual(unresolved, 0)

    def test_upstream_footballtxt_inherits_same_group_kickoff(self):
        sh = coverage.score_history
        payload = (
            "= English Premier League 2026/27\n\n"
            "  Fri Aug 21 2026\n"
            "    20:00  Alpha FC v Beta FC    2-1 (1-0)\n"
            "           Gamma FC v Delta FC    0-0\n"
        ).encode()
        with mock.patch.object(sh.live, "_fetch", return_value=(payload, "d" * 64)):
            rows, source, unresolved = sh._footballtxt_rows_for_comp(
                Path("."), "ENG_PremierLeague", set(),
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 9, 1, tzinfo=timezone.utc),
                "2026-09-09T00:00:00+00:00",
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(source["scheduled_in_window"], 2)
        self.assertEqual(unresolved, 0)

    def test_jleague_current_target_utc_day_is_excluded_before_score_parse(self):
        sh = coverage.score_history
        html = (
            "<table><tr><td>2026/27</td><td>Ｊ１</td><td>第5節</td>"
            "<td>26/09/02(水)</td><td>19:00</td><td>東京Ｖ</td>"
            "<td>BOMB</td><td>神戸</td></tr></table>"
        ).encode()
        with mock.patch.object(sh.live, "_fetch", return_value=(html, "e" * 64)), \
             mock.patch.object(sh.jpn_official, "_parse_score", side_effect=AssertionError("score touched")):
            rows, source, unresolved = sh._jleague_rows_for_url(
                Path("."), sh.JLEAGUE_2627_URL, "2026/27", "Ｊ１", set(),
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
                "2026-09-09T00:00:00+00:00", "JLEAGUE_OFFICIAL_2026_27",
            )
        self.assertEqual(rows, [])
        self.assertEqual(source["scheduled_in_window"], 0)
        self.assertEqual(unresolved, 0)

    def test_kleague_target_utc_day_is_excluded_before_goal_access(self):
        sh = coverage.score_history
        item = _GoalSentinel({
            "gameDate": "2026-09-05", "gameTime": "1900", "gameStatus": "FE",
            "homeTeamName": "전북", "awayTeamName": "포항", "gameId": "target-day",
        })
        payload = b"{}"
        with mock.patch.object(sh.live, "_fetch", return_value=(payload, "f" * 64)), \
             mock.patch.object(sh.json, "loads", return_value={"data": {"scheduleList": [item]}}):
            rows, sources, unresolved = sh._kor_rows(
                Path("."), set(),
                datetime(2026, 9, 1, tzinfo=timezone.utc),
                datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc),
                "2026-09-09T00:00:00+00:00",
            )
        self.assertEqual(rows, [])
        self.assertTrue(sources)

    def test_research_root_never_calls_aggregate_exact_history_and_503_is_attempt_level(self):
        sh = coverage.score_history
        comps = list(sh.RESEARCH_DOMESTIC_SCOPE)
        authority = {comp: set() for comp in comps}
        backup = {
            comp: [self._v1row(comp, datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
                               home=f"{comp} Home", away=f"{comp} Away")]
            for comp in comps
        }

        def pinned(_root, comp, _auth, _lower, _upper, _observed):
            return backup[comp], [{
                "provider": "OPENFOOTBALL_PINNED_CC0", "competition_id": comp,
                "status": "OBSERVED", "rows_in_window": 1, "scheduled_in_window": 1,
            }], 0

        def upstream(_root, comp, _auth, _lower, _upper, _observed):
            return [], {
                "provider": "OPENFOOTBALL_UPSTREAM_PINNED_CC0", "competition_id": comp,
                "status": "OBSERVED", "rows_in_window": 0, "scheduled_in_window": 1,
            }, 0

        def football_data(_root, comp, _auth, _lower, _upper, _observed):
            return [], [{
                "provider": "FOOTBALL_DATA_GOVERNED", "competition_id": comp,
                "status": "UNAVAILABLE", "reason": "HTTP 503 Service Temporarily Unavailable",
            }], 0

        with mock.patch.object(sh, "_frozen_authority", return_value=(authority, {"rows": 0})), \
             mock.patch.object(sh, "_openfootball_pinned_comp", side_effect=pinned), \
             mock.patch.object(sh, "_footballtxt_rows_for_comp", side_effect=upstream), \
             mock.patch.object(sh, "_football_data_comp", side_effect=football_data), \
             mock.patch.object(sh, "_jpn_rows", return_value=(backup["JPN_J1"], [{
                 "provider": "JLEAGUE_OFFICIAL_2026_27", "competition_id": "JPN_J1",
                 "status": "OBSERVED", "rows_in_window": 1, "scheduled_in_window": 1,
             }], 0)), \
             mock.patch.object(sh, "_kor_rows", return_value=(backup["KOR_KLeague1"], [{
                 "provider": "KLEAGUE_OFFICIAL", "competition_id": "KOR_KLeague1",
                 "status": "OBSERVED", "rows_in_window": 1, "scheduled_in_window": 1,
             }], 0)), \
             mock.patch.object(sh.exact, "research_v1_rows", side_effect=AssertionError("aggregate exact must not execute")):
            rows, audit = sh.research_v1_rows(
                Path("."), datetime(2026, 5, 25, tzinfo=timezone.utc),
                datetime(2026, 9, 2, tzinfo=timezone.utc),
            )
        self.assertEqual({r.competition_id for r in rows}, set(comps))
        self.assertEqual(audit["status"], "COMPLETE")
        self.assertFalse(audit["legacy_aggregate_exact_history_called"])
        self.assertTrue(all(audit["coverage"][comp]["fixture_count"] > 0 for comp in comps))
        self.assertNotIn("RuntimeGateError: source unavailable", json.dumps(audit))

    def test_backup_coverage_absent_fails_with_specific_provider_error(self):
        sh = coverage.score_history
        comps = list(sh.RESEARCH_DOMESTIC_SCOPE)
        authority = {comp: set() for comp in comps}
        with mock.patch.object(sh, "_frozen_authority", return_value=(authority, {"rows": 0})), \
             mock.patch.object(sh, "_openfootball_pinned_comp", return_value=([], [], 0)), \
             mock.patch.object(sh, "_footballtxt_rows_for_comp", return_value=([], {
                 "provider": "OPENFOOTBALL_UPSTREAM_PINNED_CC0", "competition_id": "UNUSED", "status": "UNAVAILABLE",
             }, 0)), \
             mock.patch.object(sh, "_football_data_comp", return_value=([], [], 0)), \
             mock.patch.object(sh, "_jpn_rows", return_value=([], [], 0)), \
             mock.patch.object(sh, "_kor_rows", return_value=([], [], 0)):
            with self.assertRaises(sh.rt.RuntimeGateError) as ctx:
                sh.research_v1_rows(
                    Path("."), datetime(2026, 8, 1, tzinfo=timezone.utc),
                    datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
        self.assertIn("RETROSPECTIVE_SCORE_HISTORY_UNAVAILABLE", str(ctx.exception))
        self.assertNotEqual(str(ctx.exception), "RuntimeGateError: source unavailable")

    def test_score_history_report_discloses_research_only_and_no_eight_domain_openfootball_claim(self):
        sh = coverage.score_history
        module = SimpleNamespace(_research_v1_rows=object(), _ucl_history=object())
        audit = sh.install(module)
        self.assertIs(module._research_v1_rows, sh.research_v1_rows)
        self.assertIs(module._ucl_history, sh.research_ucl_history)
        self.assertEqual(audit["request_mode"], replay.MODE)
        self.assertFalse(audit["strict_pit_claimed"])
        self.assertIn("RESEARCH_ONLY", audit["classification"])
        self.assertTrue(audit["public_only_reconstruction_research_only"])
        self.assertFalse(audit["legacy_aggregate_exact_history_called"])
        self.assertFalse(audit["fuzzy_alias_used"])
        self.assertFalse(audit["manual_score_used"])
        self.assertFalse(audit["prospective_path_changed"])
        self.assertFalse(audit["strict_pit_path_changed"])
        self.assertFalse(audit["model_or_current_or_weight_changed"])

    def test_failure_artifact_is_always_on_before_acceptance_enforcement(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/football3-current-v2-retrospective-replay-acceptance.yml").read_text(encoding="utf-8")
        upload_accept = workflow.index("Upload replay acceptance evidence")
        upload_failure = workflow.index("Upload failure resilience diagnostics")
        enforce = workflow.index("Enforce batch and aggregate result after evidence upload")
        self.assertLess(upload_accept, enforce)
        self.assertLess(upload_failure, enforce)
        self.assertIn("if: ${{ always() }}", workflow[upload_accept:enforce])


if __name__ == "__main__":
    unittest.main()
