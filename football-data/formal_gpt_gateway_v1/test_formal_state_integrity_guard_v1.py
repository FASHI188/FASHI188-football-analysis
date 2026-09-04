#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import formal_state_integrity_guard_v1 as guard
import formal_state_integrity_coverage_patch_v1 as coverage_patch
import permanent_team_identity_bridge_v1 as bridge
import runtime as rt

coverage_patch.install()


class _Base:
    def __init__(self, comp: str, ids: list[str], home_eff: float, away_eff: float):
        self.teams_local = {(comp, x): object() for x in ids}
        self.teams_global = {x: object() for x in ids}
        self.seen_fixtures = set(range(100))
        self._home_eff = home_eff
        self._away_eff = away_eff

    def predict(self, fixture):
        return {
            "cold_start_bucket": "established" if (self._home_eff > 0 or self._away_eff > 0) else "zero",
            "effective_home_history": self._home_eff,
            "effective_away_history": self._away_eff,
            "effective_competition_history": 100.0,
            "prior_source": "test",
        }


class _State:
    def __init__(self, base):
        self.base = base
        self.seen = set(range(50))
        self.pending = {}


class GuardTests(unittest.TestCase):
    def _loaded(self, home_eff: float, away_eff: float, home_id: str, away_id: str, counts=(10, 10), coverage_spec=None):
        comp = "GER_Bundesliga"
        state = _State(_Base(comp, [home_id, away_id], home_eff, away_eff))
        coverage = {
            "schema_version": "football3-cross-season-xg-coverage-v1",
            "team_xg_match_counts": {comp: {home_id: counts[0], away_id: counts[1]}},
            "files": {},
        }
        if coverage_spec is not None:
            coverage["files"][comp] = coverage_spec
        return {
            "state": state,
            "source": {"xg_2025_26_integrity_coverage": coverage},
            "meta": {
                "historical_cutoff": "2026-05-25T00:00:00+00:00",
                "formal_head": rt.FORMAL_HEAD,
                "current_sha256": rt.CURRENT_SHA256,
                "model_bindings_sha256": "m" * 64,
                "source_identity_sha256": "s" * 64,
            },
            "manifest": {
                "state_sha256": "a" * 64,
                "state_bundle_sha256": "b" * 64,
            },
        }

    def _fixture(self, home_id, away_id):
        return {
            "fixture_id": "f",
            "competition_id": "GER_Bundesliga",
            "season": "2026/27",
            "kickoff": "2026-09-04T18:30:00+00:00",
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team_name": "VfB Stuttgart",
            "away_team_name": "FC Koln",
        }

    def _ident(self, relation_home="CONTINUING_TOP_FLIGHT", relation_away="CONTINUING_TOP_FLIGHT"):
        return {
            "home": {"season_relation": relation_home},
            "away": {"season_relation": relation_away},
        }

    def test_established_team_zero_history_is_anomaly(self):
        h = rt._global_team_id("Stuttgart")
        a = rt._global_team_id("FC Koln")
        loaded = self._loaded(0.0, 10.0, h, a)
        audit = guard.classify_state(
            loaded, self._fixture(h, a), self._ident(),
            {"dynamic": {"evidence": [4.0, 4.0, 4.0, 4.0]}},
            {"fallback_exact_v1": False},
        )
        self.assertEqual(audit["status"], "DATA_STATE_ANOMALY")
        self.assertIn("HOME_ESTABLISHED_HISTORY_ZERO", audit["anomaly_reasons"])

    def test_promoted_team_zero_history_can_normal_fallback(self):
        h = rt._global_team_id("Promoted Club")
        a = rt._global_team_id("Established Club")
        loaded = self._loaded(0.0, 10.0, h, a, counts=(0, 10))
        audit = guard.classify_state(
            loaded, self._fixture(h, a),
            self._ident("PROMOTED_OR_NEW_TO_FORMAL_HISTORY", "CONTINUING_TOP_FLIGHT"),
            {"dynamic": {"evidence": [0.0, 0.0, 4.0, 4.0]}},
            {"fallback_exact_v1": True},
        )
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["fallback_class"], "NORMAL_FALLBACK")

    def test_expected_xg_but_fallback_is_anomaly(self):
        h = rt._global_team_id("Stuttgart")
        a = rt._global_team_id("FC Koln")
        loaded = self._loaded(10.0, 10.0, h, a, counts=(34, 34))
        audit = guard.classify_state(
            loaded, self._fixture(h, a), self._ident(),
            {"dynamic": {"evidence": [0.5, 0.5, 0.5, 0.5]}},
            {"fallback_exact_v1": True},
        )
        self.assertEqual(audit["fallback_class"], "DATA_STATE_ANOMALY")

    def test_known_incomplete_cross_season_source_is_anomaly_not_normal_fallback(self):
        h = rt._global_team_id("Stuttgart")
        a = rt._global_team_id("FC Koln")
        spec = {
            "source_known_by_target": True,
            "coverage_status": "INCOMPLETE_NOT_INGESTED",
            "linked_rows": 304,
            "joined_rows": 304,
            "formal_rows": 306,
            "unmatched_rows": 2,
            "overlap_rows": 0,
        }
        loaded = self._loaded(10.0, 10.0, h, a, counts=(0, 0), coverage_spec=spec)
        audit = guard.classify_state(
            loaded, self._fixture(h, a), self._ident(),
            {"dynamic": {"evidence": [0.5, 0.5, 0.5, 0.5]}},
            {"fallback_exact_v1": True},
        )
        self.assertEqual(audit["status"], "DATA_STATE_ANOMALY")
        self.assertEqual(audit["fallback_class"], "DATA_STATE_ANOMALY")
        self.assertTrue(any(x.startswith("CROSS_SEASON_XG_COVERAGE_INCOMPLETE:") for x in audit["anomaly_reasons"]))

    def test_cache_binding_changes_on_any_bound_field(self):
        h = rt._global_team_id("Stuttgart")
        a = rt._global_team_id("FC Koln")
        loaded = self._loaded(10.0, 10.0, h, a)
        ident = {
            "home": {"strength_team_id": h},
            "away": {"strength_team_id": a},
        }
        cutoff = rt._parse_dt("2026-09-04T00:25:59+00:00", "cutoff")
        x = guard._binding_payload(loaded, "GER_Bundesliga", "2026/27", cutoff, ident)
        loaded2 = dict(loaded)
        loaded2["manifest"] = dict(loaded["manifest"])
        loaded2["manifest"]["state_sha256"] = "c" * 64
        y = guard._binding_payload(loaded2, "GER_Bundesliga", "2026/27", cutoff, ident)
        self.assertNotEqual(x, y)
        self.assertNotEqual(x["binding_sha256"], y["binding_sha256"])

    def test_stale_or_unbound_cache_is_rejected_for_fast(self):
        h = rt._global_team_id("Stuttgart")
        a = rt._global_team_id("FC Koln")
        loaded = self._loaded(10.0, 10.0, h, a)
        fixture = self._fixture(h, a)
        ident = {
            "home": {"strength_team_id": h},
            "away": {"strength_team_id": a},
        }
        cutoff = rt._parse_dt("2026-09-04T00:25:59+00:00", "cutoff")
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td)
            with patch.object(guard.rt, "validate_bundle", return_value=loaded), \
                 patch.object(guard.identity_bridge, "resolve_fixture", return_value=(fixture, ident)):
                pre = guard._cache_preflight(
                    state_root, Path(td), "GER_Bundesliga", "2026/27",
                    "VfB Stuttgart", "FC Koln",
                    rt._parse_dt(fixture["kickoff"], "kickoff"), cutoff,
                )
        self.assertFalse(pre["fast_eligible"])
        self.assertEqual(pre["reason"], "CACHE_BINDING_MISSING_OR_MISMATCH")

    def test_current_identity_typo_fails_closed_no_fuzzy_guess(self):
        comp = "GER_Bundesliga"
        stuttgart = rt._global_team_id("Stuttgart")
        koln = rt._global_team_id("FC Koln")
        state = _State(_Base(comp, [stuttgart, koln], 10.0, 10.0))
        repo_root = Path(__file__).resolve().parents[2]
        with self.assertRaises(rt.RuntimeGateError):
            bridge.resolve_team(repo_root, state, comp, "2026/27", "VfB Stutgart")

    def test_postponed_fixture_keeps_team_ids_but_not_fixture_time_identity(self):
        comp = "GER_Bundesliga"
        stuttgart = rt._global_team_id("Stuttgart")
        koln = rt._global_team_id("FC Koln")
        state = _State(_Base(comp, [stuttgart, koln], 10.0, 10.0))
        repo_root = Path(__file__).resolve().parents[2]
        k1 = rt._parse_dt("2026-09-04T18:30:00+00:00", "kickoff")
        k2 = rt._parse_dt("2026-09-06T18:30:00+00:00", "kickoff")
        f1, _ = bridge.resolve_fixture(repo_root, state, comp, "2026/27", "VfB Stuttgart", "FC Koln", k1)
        f2, _ = bridge.resolve_fixture(repo_root, state, comp, "2026/27", "VfB Stuttgart", "FC Koln", k2)
        self.assertEqual(f1["home_team_id"], f2["home_team_id"])
        self.assertEqual(f1["away_team_id"], f2["away_team_id"])
        self.assertNotEqual(f1["fixture_id"], f2["fixture_id"])

    def test_identity_multiple_state_ids_fail_closed(self):
        comp = "GER_Bundesliga"
        stuttgart = rt._global_team_id("Stuttgart")
        official = rt._global_team_id("VfB Stuttgart")
        state = _State(_Base(comp, [stuttgart, official], 10.0, 10.0))
        repo_root = Path(__file__).resolve().parents[2]
        with self.assertRaises(rt.RuntimeGateError):
            bridge.resolve_team(repo_root, state, comp, "2026/27", "VfB Stuttgart")


if __name__ == "__main__":
    unittest.main()
