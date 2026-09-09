#!/usr/bin/env python3
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import current_v2_retrospective_replay_v1 as replay
import current_v2_retrospective_xg_coverage_v1 as coverage


class _BombMapping(dict):
    def get(self, *args, **kwargs):  # pragma: no cover - must never be touched
        raise AssertionError("post-cutoff score/xG field was read")


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

    def test_install_changes_only_research_replay_xg_hook(self):
        original_safe_history_upper = object()
        original_research_v1_rows = object()
        module = SimpleNamespace(
            _safe_history_upper=original_safe_history_upper,
            _research_v1_rows=original_research_v1_rows,
            _current_xg_labels=object(),
        )
        audit = coverage.install(module)
        self.assertIs(module._current_xg_labels, coverage.research_xg_labels)
        self.assertIs(module._safe_history_upper, original_safe_history_upper)
        self.assertIs(module._research_v1_rows, original_research_v1_rows)
        self.assertEqual(audit["request_mode"], replay.MODE)
        self.assertEqual(audit["missing_xg_policy"], "DELEGATE_TO_CURRENT_FORMAL_V2_EVIDENCE_ROUTE")
        self.assertFalse(audit["fallback_forced"])
        self.assertFalse(audit["evidence_threshold_changed"])
        self.assertFalse(audit["model_or_current_or_weight_changed"])

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

    def test_entry_wiring_order_preserves_gateway_and_receipt_layers(self):
        entry = Path(__file__).with_name("entry.py").read_text(encoding="utf-8")
        exact_pos = entry.index("current_v2_retrospective_exact_history_v1.install(current_v2_retrospective_replay_v1)")
        coverage_pos = entry.index("current_v2_retrospective_xg_coverage_v1.install(current_v2_retrospective_replay_v1)")
        replay_pos = entry.index("current_v2_retrospective_replay_v1.install(gateway)")
        receipt_pos = entry.index("current_v2_retrospective_receipt_contract_v1.install(gateway)")
        self.assertLess(exact_pos, coverage_pos)
        self.assertLess(coverage_pos, replay_pos)
        self.assertLess(replay_pos, receipt_pos)


if __name__ == "__main__":
    unittest.main()
