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


class CurrentV2RetrospectiveScoreHistoryTests(unittest.TestCase):
    def test_openfootball_pin_is_big5_only_and_time_bounded(self):
        sh = coverage.score_history
        self.assertEqual(sh.OPENFOOTBALL_PIN, "8aa4cd0ce0410b21037f063eeb4edd981081d85d")
        self.assertEqual(set(sh.OPENFOOTBALL_FILES), {
            "ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
        })
        self.assertEqual(sh.OPENFOOTBALL_COVERAGE_END, datetime(2026, 9, 3, tzinfo=timezone.utc))
        self.assertNotIn("JPN_J1", sh.OPENFOOTBALL_FILES)
        self.assertNotIn("KOR_KLeague1", sh.OPENFOOTBALL_FILES)
        self.assertNotIn(replay.UCL, sh.OPENFOOTBALL_FILES)

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
        fid = sh.rt._fixture_id("ENG_PremierLeague", "2026/27", ko, "Alpha", "Beta")
        def mk(hg):
            return sh.live.V1Row(
                fid, "ENG_PremierLeague", "2026/27", ko, "Alpha", "Beta",
                sh.rt._global_team_id("Alpha"), sh.rt._global_team_id("Beta"),
                hg, 0, "synthetic", "a" * 64,
            )
        with self.assertRaises(sh.rt.RuntimeGateError) as ctx:
            sh._combine([mk(1)], [("OPENFOOTBALL_PINNED", [mk(2)])])
        self.assertIn("RETROSPECTIVE_SCORE_HISTORY_UNAVAILABLE", str(ctx.exception))

    def test_openfootball_cutoff_gate_precedes_score_access(self):
        sh = coverage.score_history
        lower = datetime(2026, 8, 1, tzinfo=timezone.utc)
        upper = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        item = _ScoreSentinel({
            "date": "2026-09-01",
            "time": "15:00",
            "team1": "Alpha",
            "team2": "Beta",
        })
        calls = {"n": 0}
        def fake_fetch(url):
            calls["n"] += 1
            return ({"matches": [item]} if calls["n"] == 1 else {"matches": []}, "b" * 64)
        with mock.patch.object(sh, "_fetch_json", side_effect=fake_fetch):
            rows, sources, unresolved = sh._openfootball_rows(Path("."), [], lower, upper)
        self.assertEqual(rows, [])
        self.assertEqual(unresolved, 0)
        self.assertTrue(sources)

    def test_score_history_report_discloses_no_eight_domain_openfootball_claim(self):
        sh = coverage.score_history
        lower = datetime(2026, 8, 1, tzinfo=timezone.utc)
        upper = datetime(2026, 8, 2, tzinfo=timezone.utc)
        with mock.patch.object(sh.exact, "research_v1_rows", return_value=([], {"sources": []})), \
             mock.patch.object(sh, "_openfootball_rows", return_value=([], [], 0)), \
             mock.patch.object(sh, "_fixturedownload_rows", return_value=([], [], 0)):
            rows, audit = sh.research_v1_rows(Path("."), lower, upper)
        self.assertEqual(rows, [])
        self.assertEqual(audit["status"], "COMPLETE")
        self.assertEqual(audit["openfootball_pin"]["not_covered"], [replay.UCL, "JPN_J1", "KOR_KLeague1"])
        self.assertFalse(audit["fuzzy_alias_used"])
        self.assertFalse(audit["manual_score_used"])
        self.assertFalse(audit["target_score_fields_read_before_eligibility_gate"])


if __name__ == "__main__":
    unittest.main()
