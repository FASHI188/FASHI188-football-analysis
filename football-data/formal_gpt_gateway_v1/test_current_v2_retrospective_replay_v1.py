#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import current_v2_retrospective_replay_v1 as replay
import request_contract_v1 as contract

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"


class RequestContractTests(unittest.TestCase):
    def _request(self, mode: str, comp: str) -> dict:
        return {
            "schema_version": contract.SCHEMA,
            "mode": mode,
            "request_id": "retrospective-test-1",
            "match": {
                "competition_id": comp,
                "season": "2026/27",
                "home_team_name": "Arsenal",
                "away_team_name": "Bayern Munich",
                "kickoff": "2026-09-10T19:00:00+00:00",
                "cutoff": "2026-09-10T18:00:00+00:00",
            },
        }

    def test_current_v2_mode_keeps_explicit_execution_marker(self):
        req = contract.validate_request(self._request(contract.CURRENT_V2_RETROSPECTIVE_REPLAY, "ENG_PremierLeague"), carrier_request=True)
        execution = contract.execution_request(req)
        self.assertEqual(execution["mode"], "predict")
        self.assertEqual(execution["request_mode"], contract.CURRENT_V2_RETROSPECTIVE_REPLAY)

    def test_ucl_is_allowed_only_for_current_v2_research_replay(self):
        replay_req = contract.validate_request(self._request(contract.CURRENT_V2_RETROSPECTIVE_REPLAY, replay.UCL), carrier_request=True)
        self.assertEqual(replay_req["match"]["competition_id"], replay.UCL)
        with self.assertRaises(contract.FormalRequestContractError) as ctx:
            contract.validate_request(self._request("PROSPECTIVE_FORMAL_PREDICTION", replay.UCL), carrier_request=True)
        self.assertEqual(ctx.exception.code, "FORMAL_REQUEST_COMPETITION_ID_INVALID")


class LeakageBoundaryTests(unittest.TestCase):
    def test_ucl_target_date_score_field_is_not_accessed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directory = root / "football-data" / "processed" / replay.UCL
            directory.mkdir(parents=True)
            path = directory / "2026-27.csv"
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["season", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
                writer.writeheader()
                writer.writerow({"season": "2026/27", "Date": "2026-09-06", "HomeTeam": "Arsenal", "AwayTeam": "Bayern Munich", "FTHG": "1", "FTAG": "0"})
                # Invalid score is deliberate. If the target-date label is touched,
                # the loader would fail instead of returning only the prior fixture.
                writer.writerow({"season": "2026/27", "Date": "2026-09-07", "HomeTeam": "Arsenal", "AwayTeam": "Bayern Munich", "FTHG": "DO_NOT_READ", "FTAG": "DO_NOT_READ"})
            registry = {
                "competition_id": replay.UCL,
                "team_count": 36,
                "teams": [
                    {"uefa_name": "Arsenal", "accepted_exact_names": ["Arsenal"], "association_code": "ENG", "domestic_formal_competition_id": "ENG_PremierLeague"},
                    {"uefa_name": "Bayern München", "accepted_exact_names": ["Bayern Munich", "Bayern München"], "association_code": "GER", "domestic_formal_competition_id": "GER_Bundesliga"},
                ] + [
                    {"uefa_name": f"Dummy {i}", "accepted_exact_names": [f"Dummy {i}"], "association_code": "ZZZ", "domestic_formal_competition_id": None}
                    for i in range(34)
                ],
            }
            with mock.patch.object(replay, "_ucl_registry", return_value=registry):
                rows, audit = replay._ucl_history(root, datetime(2026, 9, 7, tzinfo=timezone.utc))
            self.assertEqual(len(rows), 1)
            self.assertEqual(audit["rows"], 1)
            self.assertLess(rows[0].kickoff, datetime(2026, 9, 7, tzinfo=timezone.utc))

    def test_safe_history_boundary_excludes_entire_target_utc_date(self):
        target = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        self.assertEqual(replay._safe_history_upper(target), datetime(2026, 9, 7, tzinfo=timezone.utc))


class GatewayIsolationAndReceiptTests(unittest.TestCase):
    def test_install_delegates_every_non_research_request_unchanged(self):
        calls = []

        class G:
            @staticmethod
            def normal_request(req, state_root, out, repo_root, understat_db, confirmation_dir):
                calls.append(req)
                return {"status": "ORIGINAL"}

        adapter = replay.install(G)
        result = G.normal_request({"mode": "predict"}, Path("."), Path("."), Path("."), Path("."), Path("."))
        self.assertEqual(result, {"status": "ORIGINAL"})
        self.assertEqual(len(calls), 1)
        self.assertFalse(adapter["prospective_path_changed"])
        self.assertFalse(adapter["strict_pit_path_changed"])
        self.assertFalse(adapter["formal_scope_changed"])

    def test_research_receipt_has_required_labels_probabilities_and_sha(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            req = {
                "mode": "predict",
                "request_mode": replay.MODE,
                "match": {
                    "competition_id": "ITA_SerieA",
                    "season": "2026/27",
                    "home_team_name": "Udinese",
                    "away_team_name": "Lazio",
                    "kickoff": "2026-09-07T18:45:00+00:00",
                    "cutoff": "2026-09-07T17:45:00+00:00",
                },
            }
            fake_state = SimpleNamespace(base=SimpleNamespace(teams_local={}, teams_global={}))
            fake_prediction = {
                "row": {
                    "prediction": {
                        "p_home": 0.41,
                        "p_draw": 0.31,
                        "p_away": 0.28,
                        "score_matrix": [
                            {"home_goals": 1, "away_goals": 0, "probability": 0.41},
                            {"home_goals": 1, "away_goals": 1, "probability": 0.31},
                            {"home_goals": 0, "away_goals": 1, "probability": 0.28},
                        ],
                    },
                    "audit": {"route": "FROZEN_V1_EXACT_FALLBACK", "fallback_exact_v1": True},
                },
                "marginals": {
                    "top_scores": [
                        {"score": "1-0", "probability": 0.41},
                        {"score": "1-1", "probability": 0.31},
                        {"score": "0-1", "probability": 0.28},
                    ],
                    "total_goals": {"1": 0.69, "2": 0.31},
                    "btts": {"yes": 0.31, "no": 0.69},
                },
                "aux": {},
            }
            formal_binding = {
                "xg_weight": 0.75,
                "frozen_v1_weight": 0.25,
                "runtime_formal_head": "runtime-selected",
                "runtime_current_sha256": "runtime-selected",
            }
            reconstruction = {
                "target_fixture_excluded": True,
                "result_excluded": True,
                "post_kickoff_events_excluded": True,
                "strict_pit_claimed": False,
            }
            with mock.patch.object(replay, "_formal_binding", return_value=formal_binding), \
                 mock.patch.object(replay, "_build_research_state", return_value=(fake_state, reconstruction)), \
                 mock.patch.object(replay, "_state_integrity", return_value={"status": "PASS", "target_result_used": False, "post_kickoff_state_used": False}), \
                 mock.patch.object(replay.rt, "_prediction_from_state", return_value=fake_prediction):
                result = replay.run(req, root / "state", out, root, root / "xg.db", root / "confirm")
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["prediction_sha"])
            receipt = json.loads((out / "prediction_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["top1"], "1")
            self.assertEqual(receipt["classification"], ["RETROSPECTIVE", "RESEARCH_ONLY", "NOT_ELIGIBLE_FOR_FORMAL_WIN_RATE", "NOT_ELIGIBLE_FOR_PROSPECTIVE_OOS"])
            self.assertFalse(receipt["strict_pit_claimed"])
            self.assertTrue(receipt["result_excluded"])
            self.assertTrue(receipt["target_fixture_excluded"])
            self.assertTrue(receipt["post_kickoff_events_excluded"])
            self.assertTrue(receipt["matrix_conservation"])
            self.assertEqual(receipt["fusion_weights"], {"xg": 0.75, "v1": 0.25})
            self.assertEqual(receipt["model_route"], "FROZEN_V1_EXACT_FALLBACK")
            self.assertTrue(receipt["fallback_exact_v1"])
            self.assertEqual(len(receipt["top_scores"]), 3)


if __name__ == "__main__":
    unittest.main()
