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
import production_base_binding_v1 as production_binding
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
    @staticmethod
    def _delegating_gateway(calls):
        class G:
            @staticmethod
            def normal_request(req, state_root, out, repo_root, understat_db, confirmation_dir):
                calls.append(req)
                return {"status": "ORIGINAL", "request": req}
        return G

    def test_install_delegates_prospective_request_unchanged(self):
        calls = []
        G = self._delegating_gateway(calls)
        adapter = replay.install(G)
        request = {
            "mode": "predict",
            "request_mode": "PROSPECTIVE_FORMAL_PREDICTION",
            "sentinel": "prospective-original-path",
        }
        result = G.normal_request(request, Path("."), Path("."), Path("."), Path("."), Path("."))
        self.assertEqual(result, {"status": "ORIGINAL", "request": request})
        self.assertEqual(calls, [request])
        self.assertFalse(adapter["prospective_path_changed"])
        self.assertFalse(adapter["formal_scope_changed"])

    def test_install_delegates_strict_pit_request_unchanged(self):
        calls = []
        G = self._delegating_gateway(calls)
        adapter = replay.install(G)
        request = {
            "mode": "predict",
            "request_mode": "ACTIVE_AT_CUTOFF_REPLAY",
            "strict_pit_claimed": True,
            "sentinel": "strict-pit-original-path",
        }
        result = G.normal_request(request, Path("."), Path("."), Path("."), Path("."), Path("."))
        self.assertEqual(result, {"status": "ORIGINAL", "request": request})
        self.assertEqual(calls, [request])
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
            # Non-production sentinel values: the contract under test is mechanical
            # propagation from the runtime formal binding, not any particular weight.
            formal_binding = {
                "xg_weight": 0.61,
                "frozen_v1_weight": 0.39,
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
            self.assertEqual(receipt["fusion_weights"], {
                "xg": formal_binding["xg_weight"],
                "v1": formal_binding["frozen_v1_weight"],
            })
            self.assertEqual(receipt["model_route"], "FROZEN_V1_EXACT_FALLBACK")
            self.assertTrue(receipt["fallback_exact_v1"])
            self.assertEqual(len(receipt["top_scores"]), 3)


class ProductionReceiptProvenanceCompatibilityTests(unittest.TestCase):
    FORMAL_HEAD = "a" * 40
    CURRENT_SHA = "b" * 64

    def _assert_alias_invalid(self, alias: str, value: object, expected_code: str) -> None:
        receipt = {"formal_head": self.FORMAL_HEAD, "current_sha256": self.CURRENT_SHA}
        summary = {}
        if alias == "receipt.formal_head":
            receipt["formal_head"] = value
        elif alias == "receipt.formal_model_head":
            receipt["formal_model_head"] = value
        elif alias == "receipt.formal_binding.runtime_formal_head":
            receipt["formal_binding"] = {"runtime_formal_head": value}
        elif alias == "summary.formal_head":
            summary["formal_head"] = value
        elif alias == "receipt.current_sha256":
            receipt["current_sha256"] = value
        elif alias == "receipt.actual_current_sha":
            receipt["actual_current_sha"] = value
        elif alias == "receipt.formal_binding.runtime_current_sha256":
            receipt.setdefault("formal_binding", {})["runtime_current_sha256"] = value
        elif alias == "summary.formal_current_sha256":
            summary["formal_current_sha256"] = value
        else:
            self.fail(f"unknown provenance alias: {alias}")
        with self.assertRaisesRegex(production_binding.ProductionBaseBindingError, expected_code):
            production_binding.resolve_receipt_provenance(receipt, summary)

    def test_legacy_receipt_fields_remain_supported(self):
        actual = production_binding.resolve_receipt_provenance(
            {"formal_head": self.FORMAL_HEAD, "current_sha256": self.CURRENT_SHA},
            {"formal_head": self.FORMAL_HEAD, "formal_current_sha256": self.CURRENT_SHA},
        )
        self.assertEqual(actual, (self.FORMAL_HEAD, self.CURRENT_SHA))

    def test_retrospective_receipt_fields_are_supported(self):
        receipt = {
            "formal_model_head": self.FORMAL_HEAD,
            "actual_current_sha": self.CURRENT_SHA,
            "formal_binding": {
                "runtime_formal_head": self.FORMAL_HEAD,
                "runtime_current_sha256": self.CURRENT_SHA,
            },
        }
        actual = production_binding.resolve_receipt_provenance(
            receipt,
            {"formal_head": self.FORMAL_HEAD, "formal_current_sha256": self.CURRENT_SHA},
        )
        self.assertEqual(actual, (self.FORMAL_HEAD, self.CURRENT_SHA))

    def test_every_formal_head_alias_rejects_wrong_length_nonhex_and_uppercase(self):
        aliases = (
            "receipt.formal_head",
            "receipt.formal_model_head",
            "receipt.formal_binding.runtime_formal_head",
            "summary.formal_head",
        )
        malformed = ("a" * 39, "g" * 40, "A" * 40)
        for alias in aliases:
            for value in malformed:
                with self.subTest(alias=alias, value=value):
                    self._assert_alias_invalid(alias, value, "PRODUCTION_FORMAL_HEAD_INVALID")

    def test_every_current_sha_alias_rejects_wrong_length_nonhex_and_uppercase(self):
        aliases = (
            "receipt.current_sha256",
            "receipt.actual_current_sha",
            "receipt.formal_binding.runtime_current_sha256",
            "summary.formal_current_sha256",
        )
        malformed = ("b" * 63, "g" * 64, "B" * 64)
        for alias in aliases:
            for value in malformed:
                with self.subTest(alias=alias, value=value):
                    self._assert_alias_invalid(alias, value, "PRODUCTION_CURRENT_SHA_INVALID")

    def test_formal_head_aliases_reject_empty_and_non_string_values(self):
        for alias in ("receipt.formal_head", "receipt.formal_model_head", "receipt.formal_binding.runtime_formal_head", "summary.formal_head"):
            for value in ("", None, 40):
                with self.subTest(alias=alias, value=value):
                    self._assert_alias_invalid(alias, value, "PRODUCTION_FORMAL_HEAD_INVALID")

    def test_current_sha_aliases_reject_empty_and_non_string_values(self):
        for alias in ("receipt.current_sha256", "receipt.actual_current_sha", "receipt.formal_binding.runtime_current_sha256", "summary.formal_current_sha256"):
            for value in ("", None, 64):
                with self.subTest(alias=alias, value=value):
                    self._assert_alias_invalid(alias, value, "PRODUCTION_CURRENT_SHA_INVALID")

    def test_nested_formal_binding_malformed_fields_fail_closed(self):
        self._assert_alias_invalid("receipt.formal_binding.runtime_formal_head", "not-a-git-sha", "PRODUCTION_FORMAL_HEAD_INVALID")
        self._assert_alias_invalid("receipt.formal_binding.runtime_current_sha256", "not-a-current-sha", "PRODUCTION_CURRENT_SHA_INVALID")

    def test_summary_corroborating_malformed_fields_fail_closed(self):
        self._assert_alias_invalid("summary.formal_head", "C" * 40, "PRODUCTION_FORMAL_HEAD_INVALID")
        self._assert_alias_invalid("summary.formal_current_sha256", "D" * 64, "PRODUCTION_CURRENT_SHA_INVALID")

    def test_conflicting_receipt_aliases_fail_closed(self):
        receipt = {
            "formal_head": self.FORMAL_HEAD,
            "formal_model_head": "c" * 40,
            "current_sha256": self.CURRENT_SHA,
        }
        with self.assertRaisesRegex(production_binding.ProductionBaseBindingError, "PRODUCTION_FORMAL_HEAD_CONFLICT"):
            production_binding.resolve_receipt_provenance(receipt, {})

    def test_conflicting_current_aliases_fail_closed(self):
        receipt = {
            "formal_head": self.FORMAL_HEAD,
            "current_sha256": self.CURRENT_SHA,
            "actual_current_sha": "c" * 64,
        }
        with self.assertRaisesRegex(production_binding.ProductionBaseBindingError, "PRODUCTION_CURRENT_SHA_CONFLICT"):
            production_binding.resolve_receipt_provenance(receipt, {})

    def test_missing_receipt_provenance_fails_closed(self):
        with self.assertRaisesRegex(production_binding.ProductionBaseBindingError, "PRODUCTION_FORMAL_HEAD_MISSING"):
            production_binding.resolve_receipt_provenance({}, {})
        with self.assertRaisesRegex(production_binding.ProductionBaseBindingError, "PRODUCTION_CURRENT_SHA_MISSING"):
            production_binding.resolve_receipt_provenance({"formal_head": self.FORMAL_HEAD}, {})

    def test_incident_retrospective_provenance_combination_remains_supported(self):
        receipt = {
            "formal_model_head": self.FORMAL_HEAD,
            "actual_current_sha": self.CURRENT_SHA,
            "formal_binding": {
                "runtime_formal_head": self.FORMAL_HEAD,
                "runtime_current_sha256": self.CURRENT_SHA,
            },
        }
        summary = {"formal_head": self.FORMAL_HEAD, "formal_current_sha256": self.CURRENT_SHA}
        self.assertEqual(production_binding.resolve_receipt_provenance(receipt, summary), (self.FORMAL_HEAD, self.CURRENT_SHA))

    def test_finalize_writes_retrospective_provenance_to_execution_receipt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "output"
            output.mkdir()
            exact_head = "d" * 40
            binding_path = root / "binding.json"
            binding_path.write_text(json.dumps({
                "resolved_live_base_sha": exact_head,
                "checkout_head_sha": exact_head,
                "request_carrier_ref": None,
                "initial_live_ref_query_count": 0,
            }), encoding="utf-8")
            prediction_sha = "e" * 64
            (output / "summary.json").write_text(json.dumps({
                "status": "PASS",
                "prediction_sha": prediction_sha,
                "formal_head": self.FORMAL_HEAD,
                "formal_current_sha256": self.CURRENT_SHA,
            }), encoding="utf-8")
            (output / "prediction_receipt.json").write_text(json.dumps({
                "prediction_sha": prediction_sha,
                "formal_model_head": self.FORMAL_HEAD,
                "actual_current_sha": self.CURRENT_SHA,
                "formal_binding": {
                    "runtime_formal_head": self.FORMAL_HEAD,
                    "runtime_current_sha256": self.CURRENT_SHA,
                },
                "state_integrity_guard": {"status": "PASS"},
                "fusion_weights": {"xg": 0.75, "v1": 0.25},
                "model_route": "FROZEN_V1_EXACT_FALLBACK",
                "fallback_exact_v1": True,
            }), encoding="utf-8")
            (output / "state_recovery.json").write_text(json.dumps({
                "candidates": [],
                "selection_sha256": "f" * 64,
            }), encoding="utf-8")
            stats_path = root / "stats.json"
            stats_path.write_text(json.dumps({
                "run_list_pages": 0,
                "rate_limit_remaining_start": 100,
                "rate_limit_remaining_end": 99,
                "network_request_count": 1,
                "api_budget": 10,
            }), encoding="utf-8")
            production_binding.finalize(SimpleNamespace(
                binding=str(binding_path),
                repo="unused",
                token="unused",
                out_dir=str(output),
                stats=str(stats_path),
                prediction_required="true",
                final_timestamp="2026-09-10T00:00:00Z",
            ))
            execution = json.loads((output / "production_execution_binding_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(execution["formal_head"], self.FORMAL_HEAD)
            self.assertEqual(execution["current_sha256"], self.CURRENT_SHA)


if __name__ == "__main__":
    unittest.main()
