#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = HERE / "nova_t60_adapter_candidate_v1.py"
spec = importlib.util.spec_from_file_location("m", P)
m = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(m)


class AdapterCandidateTests(unittest.TestCase):
    def test_config_never_activates(self):
        c = json.loads((HERE / "nova_t60_adapter_candidate_v1.json").read_text(encoding="utf-8"))
        self.assertFalse(c["activation"]["enabled"])
        self.assertIsNone(c["activation"]["replay_coverage_start_at"])
        self.assertFalse(c["adapter_contract"]["runtime_ledger_write_allowed"])

    def test_projection_is_idempotent(self):
        reg = {}
        xs = [{"fixture_id":"x","competition":"EPL","season":"2026/27","kickoff":"2026-10-01T20:00:00Z"}]
        a = m.apply_projection(reg, xs, observed_at="2026-09-16T00:00:00Z", seal_offset_minutes=60)
        b = m.apply_projection(reg, xs, observed_at="2026-09-16T00:00:00Z", seal_offset_minutes=60)
        self.assertEqual(a, {"inserted":1,"unchanged":0,"revised":0})
        self.assertEqual(b, {"inserted":0,"unchanged":1,"revised":0})
        self.assertEqual(len(reg["x"]["kickoff_revisions"]), 1)

    def test_kickoff_change_appends_revision(self):
        reg = {}
        a = [{"fixture_id":"x","competition":"J1","season":"2026/27","kickoff":"2026-10-01T10:00:00Z"}]
        b = [{"fixture_id":"x","competition":"J1","season":"2026/27","kickoff":"2026-10-01T10:30:00Z"}]
        m.apply_projection(reg, a, observed_at="2026-09-16T00:00:00Z", seal_offset_minutes=60)
        s = m.apply_projection(reg, b, observed_at="2026-09-16T00:01:00Z", seal_offset_minutes=60)
        self.assertEqual(s["revised"], 1)
        self.assertEqual([x["revision_no"] for x in reg["x"]["kickoff_revisions"]], [1,2])

    def test_identity_drift_fails_closed(self):
        reg = {}
        m.apply_projection(reg, [{"fixture_id":"x","competition":"K1","season":"2026","kickoff":"2026-10-01T10:00:00Z"}], observed_at="2026-09-16T00:00:00Z", seal_offset_minutes=60)
        with self.assertRaises(m.AdapterError):
            m.apply_projection(reg, [{"fixture_id":"x","competition":"EPL","season":"2026","kickoff":"2026-10-01T10:00:00Z"}], observed_at="2026-09-16T00:01:00Z", seal_offset_minutes=60)

    def test_capture_plan_cannot_schedule_before_activation(self):
        reg = {}
        m.apply_projection(reg, [{"fixture_id":"x","competition":"EPL","season":"2026/27","kickoff":"2026-10-01T20:00:00Z"}], observed_at="2026-09-16T00:00:00Z", seal_offset_minutes=60)
        p = m.build_capture_plan(reg, seal_offset_minutes=60, replay_enabled=False, coverage_start=None)
        self.assertEqual(p[0]["cutoff"], "2026-10-01T19:00:00Z")
        self.assertFalse(p[0]["scheduled"])
        self.assertEqual(p[0]["status"], "PENDING_ACTIVATION")

    def test_capture_plan_rejects_non_null_coverage_start(self):
        with self.assertRaises(m.AdapterError):
            m.build_capture_plan({}, seal_offset_minutes=60, replay_enabled=False, coverage_start="2026-09-16T00:00:00Z")

    def test_execute_against_bound_artifacts(self):
        source_dir = Path("/mnt/data/t60_source_artifact")
        foundation_dir = Path("/mnt/data/t60_foundation_artifact")
        if not source_dir.exists() or not foundation_dir.exists():
            self.skipTest("bound artifacts not extracted")
        with tempfile.TemporaryDirectory() as td:
            r = m.execute(HERE / "nova_t60_adapter_candidate_v1.json", source_dir, foundation_dir, Path(td), "local-test-head")
            self.assertEqual(r["status"], "T60_ADAPTER_CANDIDATE_DRY_RUN_PASS")
            self.assertEqual(r["fixture_n"], 1892)
            self.assertEqual(r["second_apply_unchanged_n"], 1892)
            self.assertEqual(r["active_capture_scheduled_n"], 0)
            self.assertFalse(r["runtime_ledger_written"])


if __name__ == "__main__":
    unittest.main()
