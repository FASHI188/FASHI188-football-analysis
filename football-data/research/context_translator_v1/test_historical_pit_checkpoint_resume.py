from __future__ import annotations

import pathlib
import tempfile
import unittest

import historical_pit_replay as core
from historical_pit_checkpoint_resume import _usage_as_of, repair_source
from historical_pit_sharded_common import ShardError, exact_files


class CheckpointResumeTests(unittest.TestCase):
    def _snapshot(self, i: int, *, tamper: bool = False):
        cutoff = "2023-09-01T14:45:00Z"
        past = {"known_at": "2023-08-25T17:00:00Z", "match_id": f"prior-{i}", "players": []}
        future = {"known_at": "2023-09-02T17:00:00Z", "match_id": f"future-{i}", "players": []}
        vectors = {"p": {"marker": i}}
        stable_usage = {"home": [past], "away": []}
        state_sha = core.canon({"vectors": vectors, "usage": stable_usage})
        if tamper:
            state_sha = "0" * 64
        return {
            "fixture_id": f"fixture-{i:03d}",
            "cutoff_utc": cutoff,
            "understat_match_id": str(1000 + i),
            "vectors": vectors,
            "usage": {"home": [past, future], "away": []},
            "state_sha256": state_sha,
        }

    def test_reference_alias_recovery_matches_original_state_sha(self):
        snapshot = self._snapshot(1)
        usage, removed, recovered = _usage_as_of(snapshot)
        self.assertEqual(removed, 1)
        self.assertEqual(recovered, snapshot["state_sha256"])
        self.assertEqual(len(usage["home"]), 1)
        self.assertLess(core.dt(usage["home"][0]["known_at"]), core.dt(snapshot["cutoff_utc"]))

    def test_state_sha_mismatch_stops(self):
        with self.assertRaises(ShardError):
            _usage_as_of(self._snapshot(2, tamper=True))

    def test_repair_source_audits_exact_50(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "source"
            out = root / "out"
            source.mkdir()
            rows = [self._snapshot(i) for i in range(50)]
            core.writejl(source / "offline_state_snapshots.jsonl", rows)
            manifest = {
                "schema_version": "test",
                "shard": 0,
                "tag": "shard-000-049",
                "start": 0,
                "end_exclusive": 50,
                "n": 50,
                "fixture_ids": [x["fixture_id"] for x in rows],
                "labels_read": 0,
                "scorer_invoked": False,
                "raw_webpage_body_persisted": False,
                "payload": exact_files(source, ["offline_state_snapshots.jsonl"]),
            }
            core.dump(source / "source_freeze_manifest.json", manifest)
            repaired = repair_source(source, out)
            audit = core.readjl(out / "checkpoint_state_repair_audit.jsonl")
            self.assertEqual(len(audit), 50)
            self.assertTrue(all(x["state_sha_match"] for x in audit))
            self.assertEqual(repaired["checkpoint_resume"]["fixture_state_sha_checked_n"], 50)
            self.assertEqual(repaired["checkpoint_resume"]["fixture_state_sha_matched_n"], 50)
            self.assertEqual(repaired["checkpoint_resume"]["fixture_state_sha_mismatch_n"], 0)
            self.assertEqual(repaired["checkpoint_resume"]["source_network_requests"], 0)
            repaired_states = core.readjl(out / "offline_state_snapshots.jsonl")
            self.assertTrue(all(
                core.canon({"vectors": x["vectors"], "usage": x["usage"]}) == x["state_sha256"]
                for x in repaired_states
            ))


if __name__ == "__main__":
    unittest.main()
