from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def make_lineup(match_number: int, *, label_type: str = "actual_starting_xi") -> dict:
    kickoff = datetime(2024, 8, 1, 12, tzinfo=timezone.utc) + timedelta(days=7 * match_number)
    observed = kickoff + timedelta(days=2)
    return {
        "competition_id": "TEST_LEAGUE",
        "season": "2024/25",
        "fixture_id": f"fixture-{match_number}",
        "kickoff_utc": kickoff.isoformat(),
        "team": "Test FC",
        "team_source_id": "test:1",
        "starters": [f"test:{player}" for player in range(1, 12)],
        "label_type": label_type,
        "player_id_namespace": "test",
        "source_name": "synthetic",
        "source_url": f"https://example.test/{match_number}",
        "source_observed_at_utc": observed.isoformat(),
        "ingested_at_utc": observed.isoformat(),
    }


class PlayerXIV502Tests(unittest.TestCase):
    def test_adapter_proxy_is_conservative(self) -> None:
        adapter = load_module(
            ROOT / "engine" / "ingest_transfermarkt_lineups_v502.py",
            "ingest_transfermarkt_lineups_v502",
        )
        kickoff, observed = adapter.proxy_times(datetime(2024, 1, 1).date())
        self.assertEqual(kickoff, "2024-01-01T12:00:00+00:00")
        self.assertEqual(observed, "2024-01-03T00:00:00+00:00")
        self.assertEqual(adapter.season_label(2024, "cross_year"), "2024/25")

    def test_shadow_route_uses_only_observed_timestamp_safe_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            route = load_module(
                ROOT / "validation" / "probable_lineup_route_v502.py",
                "probable_lineup_route_v502",
            )
            route.ROOT = tmp_path
            route.DATA_ROOT = tmp_path / "lineups"
            route.REPORT_ROOT = tmp_path / "reports"
            route.MIN_VALIDATION_PREDICTIONS = 1
            rows = [make_lineup(index) for index in range(4)]
            write_rows(route.DATA_ROOT / "TEST_LEAGUE" / "historical_lineups.jsonl", rows)
            report = route.validate_competition("TEST_LEAGUE", write=False)
            self.assertEqual(report["status"], "PROBABLE_LINEUP_SHADOW_VALIDATED")
            self.assertEqual(report["prediction_count"], 1)
            self.assertEqual(report["mean_top11_overlap"], 11)
            self.assertEqual(report["row_error_count"], 0)

    def test_predicted_xi_is_rejected_from_observed_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            route = load_module(
                ROOT / "validation" / "probable_lineup_route_v502.py",
                "probable_lineup_route_v502_predicted",
            )
            route.ROOT = tmp_path
            route.DATA_ROOT = tmp_path / "lineups"
            rows = [make_lineup(0, label_type="predicted_xi")]
            write_rows(route.DATA_ROOT / "TEST_LEAGUE" / "historical_lineups.jsonl", rows)
            report = route.validate_competition("TEST_LEAGUE", write=False)
            self.assertEqual(report["status"], "LINEUP_DATA_UNUSABLE")
            self.assertEqual(report["prediction_count"], 0)
            self.assertEqual(report["row_error_count"], 1)
            self.assertIn("non-observed label_type", report["row_error_examples"][0])

    def test_source_observation_after_target_freeze_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            route = load_module(
                ROOT / "validation" / "probable_lineup_route_v502.py",
                "probable_lineup_route_v502_late",
            )
            route.ROOT = tmp_path
            route.DATA_ROOT = tmp_path / "lineups"
            route.MIN_VALIDATION_PREDICTIONS = 1
            rows = [make_lineup(index) for index in range(4)]
            target_kickoff = datetime.fromisoformat(rows[3]["kickoff_utc"])
            for row in rows[:3]:
                row["source_observed_at_utc"] = (target_kickoff + timedelta(hours=1)).isoformat()
            write_rows(route.DATA_ROOT / "TEST_LEAGUE" / "historical_lineups.jsonl", rows)
            report = route.validate_competition("TEST_LEAGUE", write=False)
            self.assertEqual(report["prediction_count"], 0)
            self.assertGreaterEqual(report["prior_rows_blocked_by_source_observation_time"], 3)


if __name__ == "__main__":
    unittest.main()
