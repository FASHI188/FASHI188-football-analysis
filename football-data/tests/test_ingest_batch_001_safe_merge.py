from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

import ingest_batch_001_safe_adapter as SAFE


class Batch001SafeMergeTests(unittest.TestCase):
    def test_merge_preserves_competitions_not_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staged = root / "staged" / "league_profiles"
            destination = root / "repo" / "league_profiles"

            existing_other = destination / "JPN_J1"
            existing_other.mkdir(parents=True)
            (existing_other / "profile.json").write_text("keep", encoding="utf-8")

            existing_batch = destination / "ENG_PremierLeague"
            existing_batch.mkdir(parents=True)
            (existing_batch / "profile.json").write_text("old", encoding="utf-8")

            new_batch = staged / "ENG_PremierLeague"
            new_batch.mkdir(parents=True)
            (new_batch / "profile.json").write_text("new", encoding="utf-8")

            SAFE.merge_competition_tree(staged, destination)

            self.assertEqual(
                (destination / "JPN_J1" / "profile.json").read_text(encoding="utf-8"),
                "keep",
            )
            self.assertEqual(
                (destination / "ENG_PremierLeague" / "profile.json").read_text(encoding="utf-8"),
                "new",
            )

    def test_empty_stage_does_not_delete_existing_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staged = root / "missing"
            destination = root / "repo" / "raw"
            existing = destination / "NOR_Eliteserien"
            existing.mkdir(parents=True)
            (existing / "sentinel.txt").write_text("keep", encoding="utf-8")

            SAFE.merge_competition_tree(staged, destination)
            self.assertTrue((existing / "sentinel.txt").is_file())


if __name__ == "__main__":
    unittest.main()
