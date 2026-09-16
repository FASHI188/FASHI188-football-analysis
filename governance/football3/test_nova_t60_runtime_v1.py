#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = HERE / "nova_t60_runtime_v1.py"
spec = importlib.util.spec_from_file_location("m", P)
m = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(m)

CFG = {
    "status": "T60_RUNTIME_ENABLE_AUTHORIZED",
    "activation": {"enabled": True, "replay_coverage_start_at": None, "coverage_start_assignment": "FIRST_DEFAULT_BRANCH_RUNTIME_RUN_ONLY"},
    "timing": {"seal_offset_minutes": 60},
    "binding": {"model_head": "m1", "current_sha": "c1"},
    "safety": {"result_fields_read": 0, "retroactive_coverage_fabrication": False, "formal_v2_changed": False, "current_changed": False, "production_changed": False, "candidate_weight": 0, "matrix_delta": 0},
}


def fixture(kickoff: str = "2026-09-20T10:00:00Z") -> dict:
    return {"fixture_id": "f1", "competition": "K1", "season": "2026", "kickoff": kickoff, "home_team": "H", "away_team": "A"}


def source_dir(root: Path, observed_at: str, rows: list[dict]) -> Path:
    p = root / "src"
    p.mkdir(parents=True, exist_ok=True)
    (p / "receipt.json").write_text(json.dumps({"status": "T60_SOURCE_PRECHECK_PASS", "observed_at": observed_at, "result_fields_read": 0, "secret_required": False}), encoding="utf-8")
    (p / "future_fixture_projection.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    return p


class RuntimeTests(unittest.TestCase):
    def run_once(self, now: str, observed_at: str, rows: list[dict], prior: Path | None = None):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        cfg = root / "config.json"
        cfg.write_text(json.dumps(CFG), encoding="utf-8")
        src = source_dir(root, observed_at, rows)
        out = root / "out"
        receipt = m.execute(cfg, src, prior, out, now, "impl")
        return td, out, receipt

    def test_first_run_assigns_coverage_start_once(self):
        a, out1, r1 = self.run_once("2026-09-16T08:40:00Z", "2026-09-16T08:39:00Z", [fixture()])
        self.assertTrue(r1["coverage_start_created_this_run"])
        b, out2, r2 = self.run_once("2026-09-16T09:10:00Z", "2026-09-16T09:09:00Z", [fixture()], out1)
        self.assertFalse(r2["coverage_start_created_this_run"])
        self.assertEqual(r1["replay_coverage_start_at"], r2["replay_coverage_start_at"])
        a.cleanup(); b.cleanup()

    def test_capture_uses_only_pre_cutoff_snapshot(self):
        a, out1, _ = self.run_once("2026-09-16T08:40:00Z", "2026-09-16T08:39:00Z", [fixture()])
        b, out2, r2 = self.run_once("2026-09-20T09:05:00Z", "2026-09-20T09:04:00Z", [fixture()], out1)
        self.assertEqual(r2["new_capture_n"], 1)
        state = json.loads((out2 / "runtime_state.json").read_text(encoding="utf-8"))
        self.assertLessEqual(state["sealed_states"]["f1"]["available_at"], "2026-09-20T09:00:00Z")
        a.cleanup(); b.cleanup()

    def test_registered_after_cutoff_generates_gap(self):
        a, _, r = self.run_once("2026-09-20T09:05:00Z", "2026-09-20T09:04:00Z", [fixture()])
        self.assertEqual(r["new_gap_n"], 1)
        a.cleanup()

    def test_late_kickoff_revision_generates_gap(self):
        a, out1, _ = self.run_once("2026-09-16T08:40:00Z", "2026-09-16T08:39:00Z", [fixture()])
        b, _, r2 = self.run_once("2026-09-20T09:05:00Z", "2026-09-20T09:04:00Z", [fixture("2026-09-20T09:30:00Z")], out1)
        self.assertEqual(r2["new_gap_n"], 1)
        a.cleanup(); b.cleanup()

    def test_result_fields_are_not_consumed(self):
        safe = m.source_safe_row(dict(fixture(), score="9-9", result="x", winner="H"))
        self.assertNotIn("score", safe)
        self.assertNotIn("result", safe)
        self.assertNotIn("winner", safe)


if __name__ == "__main__":
    unittest.main()
