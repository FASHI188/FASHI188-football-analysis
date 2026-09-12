#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cross_season_participation_authority_v1 as participation
import formal_durable_state_governance_v1 as durable
import formal_future_fixture_identity_bridge_v1 as identity
import runtime as rt

ROOT = Path(__file__).resolve().parents[2]


class PrematchStateIdentityContractTest(unittest.TestCase):
    def test_jpn_transition_uses_authority_compatibility_season_without_changing_request_season(self) -> None:
        self.assertEqual(identity._authority_season(ROOT, "JPN_J1", "2026/27"), "2026")
        contract = participation.domain_contract(ROOT, "JPN_J1", "2026")
        self.assertIsNotNone(contract)
        self.assertEqual(contract["formal_current_season"], "2026")
        self.assertEqual(contract["official_current_season"], "2026/27")

    def test_other_natural_year_current_alias_uses_same_mechanical_rule(self) -> None:
        self.assertEqual(identity._authority_season(ROOT, "KOR_KLeague1", "2026/27"), "2026")

    def test_historical_natural_year_request_is_not_promoted_to_current_authority(self) -> None:
        self.assertEqual(identity._authority_season(ROOT, "JPN_J1", "2025/26"), "2025/26")

    def test_autumn_spring_request_season_is_unchanged(self) -> None:
        self.assertEqual(identity._authority_season(ROOT, "GER_Bundesliga", "2026/27"), "2026/27")

    def _write_runtime_input(self, out: Path, *, runtime_cutoff: str, delta_from: str, delta_to: str) -> None:
        delta = []
        payload = {
            "cutoff": runtime_cutoff,
            "model_delta": delta,
            "delta_coverage": {
                "status": "COMPLETE",
                "verification": "VERIFIED_COMPLETE",
                "from": delta_from,
                "to": delta_to,
                "records_sha256": rt._sha_bytes(rt._canon_bytes(delta)),
            },
        }
        (out / "runtime_input.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_exact_requested_cutoff_remains_legal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            exact = "2026-09-12T12:30:00+00:00"
            requested = rt._parse_dt(exact, "requested")
            base = rt._parse_dt("2026-09-04T13:19:44+00:00", "base")
            self._write_runtime_input(out, runtime_cutoff=exact, delta_from=exact, delta_to=exact)
            _, _, _, dfrom, dto = durable._delta_from_output(out, base, requested)
            self.assertEqual(dfrom, dto)
            self.assertEqual(dto, requested)

    def test_clamped_effective_cutoff_is_legal_when_not_after_requested_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            effective = "2026-09-12T07:40:35+00:00"
            requested = rt._parse_dt("2026-09-12T12:30:00+00:00", "requested")
            base = rt._parse_dt("2026-09-04T13:19:44+00:00", "base")
            self._write_runtime_input(out, runtime_cutoff=effective, delta_from=effective, delta_to=effective)
            _, _, _, dfrom, dto = durable._delta_from_output(out, base, requested)
            self.assertEqual(dfrom, dto)
            self.assertEqual(dto, rt._parse_dt(effective, "effective"))
            self.assertLess(dto, requested)

    def test_effective_cutoff_after_requested_ceiling_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            requested = rt._parse_dt("2026-09-12T12:30:00+00:00", "requested")
            base = rt._parse_dt("2026-09-04T13:19:44+00:00", "base")
            illegal = "2026-09-12T12:31:00+00:00"
            self._write_runtime_input(out, runtime_cutoff=illegal, delta_from=illegal, delta_to=illegal)
            with self.assertRaisesRegex(rt.RuntimeGateError, "exceeds requested cutoff"):
                durable._delta_from_output(out, base, requested)

    def test_runtime_cutoff_must_exactly_bind_delta_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            requested = rt._parse_dt("2026-09-12T12:30:00+00:00", "requested")
            base = rt._parse_dt("2026-09-04T13:19:44+00:00", "base")
            self._write_runtime_input(
                out,
                runtime_cutoff="2026-09-12T07:40:36+00:00",
                delta_from="2026-09-12T07:40:35+00:00",
                delta_to="2026-09-12T07:40:35+00:00",
            )
            with self.assertRaisesRegex(rt.RuntimeGateError, "does not bind delta target cutoff"):
                durable._delta_from_output(out, base, requested)


if __name__ == "__main__":
    unittest.main(verbosity=2)
