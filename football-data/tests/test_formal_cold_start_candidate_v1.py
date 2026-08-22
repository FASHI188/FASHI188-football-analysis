from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import LOW_SCORE_CELLS, load_config, predict_from_history
from formal_cold_start_candidate_v1 import (
    GENERIC_VALIDATED_FALLBACK,
    PRIOR_SEASON_SHRINKAGE,
    STABLE_CURRENT_SEASON,
    UNINFORMED_GLOBAL_BASELINE,
    load_candidate_config,
    predict_cold_start_from_history,
)
from platform_core import MatchRow, PlatformError, sha256_json
from run_universal_prediction_candidate_v1 import predict_universal


class FormalColdStartCandidateV1Tests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.cutoff = self.start + timedelta(days=60)
        self.params = copy.deepcopy(load_config()["default_parameters"])
        self.teams = ["A", "B", "C", "D", "E", "F"]

    def history(self, count: int) -> list[MatchRow]:
        rows = []
        for index in range(count):
            if index < 2:
                home, away = "A", "B"
            else:
                home = self.teams[index % len(self.teams)]
                away = self.teams[(index + 1) % len(self.teams)]
            rows.append(
                MatchRow(
                    "TEST",
                    "2026-27",
                    "regular",
                    self.start + timedelta(days=index),
                    home,
                    away,
                    1 + (index % 2),
                    index % 2,
                    f"synthetic-{index}",
                )
            )
        return rows

    def _receipt(self, artifact: dict, status: str = "PASS") -> dict:
        receipt = {
            "schema_version": "1.0",
            "artifact_type": artifact["artifact_type"],
            "artifact_version": artifact["version"],
            "validation_status": status,
            "validated_at_utc": "2026-07-31T00:00:00+00:00",
            "artifact_sha256": sha256_json(artifact),
        }
        receipt["receipt_sha256"] = sha256_json(receipt)
        return receipt

    def prior(self, include_b: bool = True) -> tuple[dict, dict]:
        team_names = self.teams if include_b else [team for team in self.teams if team != "B"]
        artifact = {
            "schema_version": "1.0",
            "artifact_type": "PRIOR_SEASON_STRENGTH",
            "version": "synthetic-prior-v1",
            "scope": {
                "competition_id": "TEST",
                "source_season": "2025-26",
                "target_season": "2026-27",
            },
            "payload": {
                "selected_parameters": copy.deepcopy(self.params),
                "league_home_goals": 1.55,
                "league_away_goals": 1.15,
                "nb_dispersion_k": 10.0,
                "low_score_factors": {f"{h}-{a}": 1.0 for h, a in LOW_SCORE_CELLS},
                "teams": {
                    team.lower(): {
                        "home_for_rate": 1.55,
                        "home_against_rate": 1.15,
                        "away_for_rate": 1.15,
                        "away_against_rate": 1.55,
                    }
                    for team in team_names
                },
            },
        }
        return artifact, self._receipt(artifact)

    def fallback(self, known_teams: list[str] | None = None) -> tuple[dict, dict]:
        artifact = {
            "schema_version": "1.0",
            "artifact_type": "GENERIC_COMPETITION_FALLBACK",
            "version": "synthetic-generic-v1",
            "scope": {"competition_ids": ["TEST"], "seasons": ["2026-27"]},
            "payload": {
                "selected_parameters": copy.deepcopy(self.params),
                "league_home_goals": 1.5,
                "league_away_goals": 1.1,
                "nb_dispersion_k": 12.0,
                "known_teams": known_teams or self.teams,
            },
        }
        return artifact, self._receipt(artifact)

    def predict_with_prior(self, count: int) -> dict:
        artifact, receipt = self.prior()
        return predict_cold_start_from_history(
            self.history(count),
            "TEST",
            "2026-27",
            "A",
            "B",
            self.cutoff,
            stable_selected_parameters=self.params,
            prior_artifact=artifact,
            prior_receipt=receipt,
        )

    def test_0_1_2_29_30_match_routing(self):
        for count in (0, 1, 2, 29):
            with self.subTest(count=count):
                result = self.predict_with_prior(count)
                self.assertEqual(result["cold_start_candidate"]["state"], PRIOR_SEASON_SHRINKAGE)
                self.assertEqual(result["cold_start_candidate"]["formal_weight"], 0.0)
                self.assertFalse(result["cold_start_candidate"]["exact_gate"])
                self.assertEqual(result["cold_start_candidate"]["ev_decision"], "No Bet")
                self.assertAlmostEqual(
                    sum(result["probabilities"]["one_x_two"].values()), 1.0, places=10
                )
        stable = self.predict_with_prior(30)
        self.assertEqual(stable["cold_start_candidate"]["state"], STABLE_CURRENT_SEASON)
        self.assertEqual(stable["cold_start_candidate"]["prior_weight"], 0.0)

    def test_prior_weight_is_monotonic(self):
        weights = [self.predict_with_prior(count)["cold_start_candidate"]["prior_weight"] for count in (0, 1, 2, 29, 30)]
        self.assertEqual(weights, sorted(weights, reverse=True))
        self.assertEqual(weights[0], 1.0)
        self.assertEqual(weights[-1], 0.0)

    def test_29_to_30_continuity(self):
        before = self.predict_with_prior(29)["probabilities"]["one_x_two"]
        after = self.predict_with_prior(30)["probabilities"]["one_x_two"]
        maximum = max(abs(before[key] - after[key]) for key in before)
        tolerance = load_candidate_config()["continuity"]["maximum_one_x_two_absolute_delta"]
        self.assertLessEqual(maximum, tolerance)

    def test_generic_validated_fallback(self):
        artifact, receipt = self.fallback()
        result = predict_cold_start_from_history(
            [],
            "TEST",
            "2026-27",
            "A",
            "B",
            self.cutoff,
            generic_fallback_artifact=artifact,
            generic_fallback_receipt=receipt,
        )
        self.assertEqual(result["cold_start_candidate"]["state"], GENERIC_VALIDATED_FALLBACK)
        self.assertEqual(result["cold_start_candidate"]["prior_weight"], 1.0)

    def test_missing_evidence_uses_explicit_universal_baseline(self):
        result = predict_cold_start_from_history([], "TEST", "2026-27", "A", "B", self.cutoff)
        audit = result["cold_start_candidate"]
        self.assertEqual(audit["state"], UNINFORMED_GLOBAL_BASELINE)
        self.assertTrue(audit["coverage_only"])
        self.assertEqual(audit["confidence"], "VERY_LOW")
        self.assertFalse(audit["team_strength_evidence"])
        self.assertEqual(audit["formal_weight"], 0.0)
        self.assertAlmostEqual(sum(result["probabilities"]["one_x_two"].values()), 1.0, places=10)

    def test_universal_router_covers_unknown_competition(self):
        result = predict_universal({
            "competition_id": "NO_SUCH_COMPETITION",
            "season": "2026-27",
            "home_team": "Home",
            "away_team": "Away",
            "cutoff_utc": self.cutoff.isoformat(),
        })
        self.assertEqual(result["universal_router"]["route"], UNINFORMED_GLOBAL_BASELINE)
        self.assertTrue(result["universal_router"]["downgraded"])
        self.assertTrue(result["universal_router"]["coverage_guarantee"])

    def test_universal_router_does_not_downgrade_integrity_failure(self):
        from unittest.mock import patch

        integrity_error = PlatformError("formal-core artifact engine hash does not match current engine")
        with patch("run_universal_prediction_candidate_v1.predict_joint_distribution", side_effect=integrity_error):
            with self.assertRaisesRegex(PlatformError, "hash does not match"):
                predict_universal({
                    "competition_id": "TEST",
                    "season": "2026-27",
                    "home_team": "A",
                    "away_team": "B",
                    "cutoff_utc": self.cutoff.isoformat(),
                })

    def test_unvalidated_and_hash_mismatch_hard_fail_without_downgrade(self):
        prior, receipt = self.prior()
        fallback, fallback_receipt = self.fallback()
        bad_status = copy.deepcopy(receipt)
        bad_status["validation_status"] = "FAIL"
        bad_status["receipt_sha256"] = sha256_json({key: value for key, value in bad_status.items() if key != "receipt_sha256"})
        with self.assertRaisesRegex(PlatformError, "not validated"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=prior, prior_receipt=bad_status,
                generic_fallback_artifact=fallback, generic_fallback_receipt=fallback_receipt,
            )
        tampered = copy.deepcopy(prior)
        tampered["payload"]["league_home_goals"] = 9.0
        with self.assertRaisesRegex(PlatformError, "artifact hash mismatch"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=tampered, prior_receipt=receipt,
            )

    def test_receipt_hash_and_scope_mismatch_hard_fail(self):
        prior, receipt = self.prior()
        bad_receipt = copy.deepcopy(receipt)
        bad_receipt["validated_at_utc"] = "2026-07-30T00:00:00+00:00"
        with self.assertRaisesRegex(PlatformError, "receipt hash mismatch"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=prior, prior_receipt=bad_receipt,
            )
        wrong_scope = copy.deepcopy(prior)
        wrong_scope["scope"]["target_season"] = "2027-28"
        wrong_receipt = self._receipt(wrong_scope)
        with self.assertRaisesRegex(PlatformError, "scope mismatch"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=wrong_scope, prior_receipt=wrong_receipt,
            )

    def test_unknown_and_promoted_team_identity_isolation(self):
        prior, receipt = self.prior(include_b=False)
        with self.assertRaisesRegex(PlatformError, "identity absent"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=prior, prior_receipt=receipt,
            )
        fallback, fallback_receipt = self.fallback(["A", "C"])
        with self.assertRaisesRegex(PlatformError, "identity absent"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff,
                generic_fallback_artifact=fallback, generic_fallback_receipt=fallback_receipt,
            )

    def test_future_wrong_season_and_naive_cutoff_rejected(self):
        future = MatchRow(
            "TEST", "2026-27", "regular", self.cutoff + timedelta(days=1), "A", "B", 1, 0, "future"
        )
        prior, receipt = self.prior()
        with self.assertRaisesRegex(PlatformError, "not strictly before"):
            predict_cold_start_from_history(
                [future], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=prior, prior_receipt=receipt,
            )
        wrong_season = MatchRow(
            "TEST", "2025-26", "regular", self.start, "A", "B", 1, 0, "wrong-season"
        )
        with self.assertRaisesRegex(PlatformError, "season mismatch"):
            predict_cold_start_from_history(
                [wrong_season], "TEST", "2026-27", "A", "B", self.cutoff,
                prior_artifact=prior, prior_receipt=receipt,
            )
        with self.assertRaisesRegex(PlatformError, "timezone"):
            predict_cold_start_from_history(
                [], "TEST", "2026-27", "A", "B", self.cutoff.replace(tzinfo=None),
                prior_artifact=prior, prior_receipt=receipt,
            )

    def test_stable_route_matches_unchanged_formal_history_engine(self):
        history = self.history(30)
        candidate = predict_cold_start_from_history(
            history,
            "TEST",
            "2026-27",
            "A",
            "B",
            self.cutoff,
            stable_selected_parameters=self.params,
        )
        formal = predict_from_history(
            history,
            "TEST",
            "2026-27",
            "A",
            "B",
            self.cutoff,
            selected_parameters=self.params,
        )
        self.assertEqual(candidate["probabilities"], formal["probabilities"])
        self.assertEqual(candidate["team_sample"], formal["team_sample"])


if __name__ == "__main__":
    unittest.main()
