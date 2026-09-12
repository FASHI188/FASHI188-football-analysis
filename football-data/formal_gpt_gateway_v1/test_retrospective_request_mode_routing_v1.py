#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import request_contract_v1 as request_contract
import historical_request_mode_guard_v1 as historical_guard
import current_v2_retrospective_replay_v1 as current_v2_replay

FOOTBALL3_GOVERNED_PRODUCTION_RUNTIME_GOVERNANCE = "football3-formal-production-runtime-governance-v1"


INCIDENT_MATCH = {
    "competition_id": "JPN_J1",
    "season": "2026/27",
    "home_team_name": "Gamba Osaka",
    "away_team_name": "FC Tokyo",
    "kickoff": "2026-09-12T10:00:00+00:00",
    "cutoff": "2026-09-12T09:00:00+00:00",
}


def canonical_request(mode: str, *, request_id: str = "retrospective-routing-regression") -> dict:
    return request_contract.validate_request(
        {
            "schema_version": request_contract.SCHEMA,
            "mode": mode,
            "request_id": request_id,
            "match": dict(INCIDENT_MATCH),
        },
        carrier_request=True,
    )


def execution_request(mode: str, *, request_id: str = "retrospective-routing-regression") -> dict:
    return request_contract.execution_request(canonical_request(mode, request_id=request_id))


class RequestModeIdentityRegression(unittest.TestCase):
    def test_all_prediction_modes_preserve_canonical_request_mode(self) -> None:
        for mode in sorted(request_contract.FORMAL_PREDICTION_MODES):
            with self.subTest(mode=mode):
                execution = execution_request(mode, request_id=f"mode-{mode}")
                self.assertEqual(execution["mode"], "predict")
                self.assertEqual(execution["request_mode"], mode)

    def test_current_v2_replay_intercepts_before_live_chain(self) -> None:
        calls = {"live": 0, "replay": 0}

        def live_route(*args, **kwargs):
            calls["live"] += 1
            return {"status": "UNEXPECTED_LIVE"}

        gateway = types.SimpleNamespace(normal_request=live_route)
        historical_guard.install(gateway)

        def replay_route(*args, **kwargs):
            calls["replay"] += 1
            return {"status": "PASS", "route": "CURRENT_V2_RETROSPECTIVE_REPLAY"}

        with mock.patch.object(current_v2_replay, "run", side_effect=replay_route):
            current_v2_replay.install(gateway)
            req = execution_request(request_contract.CURRENT_V2_RETROSPECTIVE_REPLAY)
            result = gateway.normal_request(
                req, Path("."), Path("."), Path("."), Path("."), Path(".")
            )

        self.assertEqual(result["route"], "CURRENT_V2_RETROSPECTIVE_REPLAY")
        self.assertEqual(calls, {"live": 0, "replay": 1})

    def test_prospective_request_stays_on_existing_live_route(self) -> None:
        calls = {"live": 0}

        def live_route(*args, **kwargs):
            calls["live"] += 1
            return {"status": "PASS", "route": "EXISTING_PROSPECTIVE"}

        gateway = types.SimpleNamespace(normal_request=live_route)
        historical_guard.install(gateway)
        req = execution_request("PROSPECTIVE_FORMAL_PREDICTION")
        result = gateway.normal_request(
            req, Path("."), Path("."), Path("."), Path("."), Path(".")
        )
        self.assertEqual(result["route"], "EXISTING_PROSPECTIVE")
        self.assertEqual(calls["live"], 1)

    def test_active_at_cutoff_passes_only_with_exact_validated_sealed_state(self) -> None:
        calls = {"downstream": 0}

        def downstream(*args, **kwargs):
            calls["downstream"] += 1
            return {"status": "PASS", "route": "SEALED_EXACT_CUTOFF_REPLAY"}

        gateway = types.SimpleNamespace(normal_request=downstream)
        historical_guard.install(gateway)
        req = execution_request("ACTIVE_AT_CUTOFF_REPLAY")
        sealed = {"meta": {"historical_cutoff": INCIDENT_MATCH["cutoff"]}}
        with mock.patch.object(historical_guard.rt, "validate_bundle", return_value=sealed):
            result = gateway.normal_request(
                req, Path("state"), Path("out"), Path("."), Path("."), Path(".")
            )
        self.assertEqual(result["route"], "SEALED_EXACT_CUTOFF_REPLAY")
        self.assertEqual(calls["downstream"], 1)

    def test_active_at_cutoff_without_exact_state_fails_before_model_or_live(self) -> None:
        calls = {"downstream": 0}

        def downstream(*args, **kwargs):
            calls["downstream"] += 1
            raise AssertionError("legacy historical request reached downstream/live chain")

        gateway = types.SimpleNamespace(normal_request=downstream)
        historical_guard.install(gateway)
        req = execution_request("ACTIVE_AT_CUTOFF_REPLAY")
        nonexact = {"meta": {"historical_cutoff": "2026-09-12T08:59:59+00:00"}}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            with mock.patch.object(historical_guard.rt, "validate_bundle", return_value=nonexact):
                with self.assertRaisesRegex(
                    historical_guard.rt.RuntimeGateError,
                    "HISTORICAL_EXACT_CUTOFF_SEALED_STATE_REQUIRED",
                ):
                    gateway.normal_request(
                        req, Path(td) / "state", out, Path("."), Path("."), Path(".")
                    )
            gap = json.loads((out / "formal_gap.json").read_text(encoding="utf-8"))
            self.assertEqual(gap["status"], "HISTORICAL_COVERAGE_INSUFFICIENT")
            self.assertEqual(gap["request_mode"], "ACTIVE_AT_CUTOFF_REPLAY")
            self.assertEqual(gap["live_acquisition_calls"], 0)
            self.assertFalse(gap["model_execution_started"])
            self.assertIsNone(gap["prediction_sha"])
            self.assertIsNone(gap["receipt_sha"])
            self.assertFalse((out / "prediction_receipt.json").exists())
        self.assertEqual(calls["downstream"], 0)

    def test_deprecated_current_model_replay_never_silently_becomes_current_v2(self) -> None:
        calls = {"downstream": 0}

        def downstream(*args, **kwargs):
            calls["downstream"] += 1
            raise AssertionError("deprecated mode reached downstream/live chain")

        gateway = types.SimpleNamespace(normal_request=downstream)
        historical_guard.install(gateway)
        req = execution_request("CURRENT_MODEL_RETROSPECTIVE_REPLAY")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            with self.assertRaisesRegex(
                historical_guard.rt.RuntimeGateError,
                "CURRENT_MODEL_RETROSPECTIVE_REPLAY_DEPRECATED_USE_CURRENT_V2_RETROSPECTIVE_REPLAY",
            ):
                gateway.normal_request(
                    req, Path(td) / "state", out, Path("."), Path("."), Path(".")
                )
            gap = json.loads((out / "formal_gap.json").read_text(encoding="utf-8"))
            self.assertEqual(gap["request_mode"], "CURRENT_MODEL_RETROSPECTIVE_REPLAY")
            self.assertFalse(gap["silent_mode_rewrite_used"])
            self.assertIsNone(gap["prediction_sha"])
            self.assertIsNone(gap["receipt_sha"])
        self.assertEqual(calls["downstream"], 0)

    def test_run_34692815040_jpn_j1_incident_fails_before_live_without_exact_state(self) -> None:
        class Poison:
            def __getattribute__(self, name):
                raise AssertionError("post-kickoff/target-result payload was accessed")

            def __str__(self):
                raise AssertionError("post-kickoff/target-result payload was stringified")

        req = execution_request(
            "ACTIVE_AT_CUTOFF_REPLAY",
            request_id="football3-gateway-20260912T120400Z-gamba-osaka-fc-tokyo-replay",
        )
        req["target_result"] = Poison()
        req["score"] = Poison()
        req["events"] = Poison()

        calls = {"downstream": 0}

        def downstream(*args, **kwargs):
            calls["downstream"] += 1
            raise AssertionError("Run 34692815040 incident reached live acquisition")

        gateway = types.SimpleNamespace(normal_request=downstream)
        historical_guard.install(gateway)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            with mock.patch.object(
                historical_guard.rt,
                "validate_bundle",
                side_effect=historical_guard.rt.RuntimeGateError("NO_EXACT_SEALED_STATE"),
            ):
                with self.assertRaisesRegex(
                    historical_guard.rt.RuntimeGateError,
                    "HISTORICAL_EXACT_CUTOFF_SEALED_STATE_REQUIRED",
                ):
                    gateway.normal_request(
                        req, Path(td) / "state", out, Path("."), Path("."), Path(".")
                    )
            gap = json.loads((out / "formal_gap.json").read_text(encoding="utf-8"))
            self.assertEqual(gap["requested_cutoff"], "2026-09-12T09:00:00+00:00")
            self.assertEqual(gap["live_acquisition_calls"], 0)
            self.assertFalse(gap["result_or_post_kickoff_data_used"])
        self.assertEqual(calls["downstream"], 0)


if __name__ == "__main__":
    unittest.main()
