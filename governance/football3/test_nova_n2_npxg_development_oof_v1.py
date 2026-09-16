import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


labels = load("n2labels", "nova_n2_development_label_vault_v1.py")
oof = load("n2oof", "nova_n2_npxg_development_oof_v1.py")


class DevelopmentOOFTests(unittest.TestCase):
    def test_label_prefix_never_parses_isolated_next_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "projection.jsonl"
            development = [{"season_start": 2022} for _ in range(labels.EXPECTED_N)]
            path.write_text(
                "\n".join(json.dumps(row) for row in development) + "\n{BROKEN_ISOLATED",
                encoding="utf-8",
            )
            self.assertEqual(len(labels.read_development_projection(path)), labels.EXPECTED_N)

    def test_database_datetime_normalization(self):
        self.assertEqual(
            labels.normalize_db_datetime("2022-08-05 19:00:00"),
            "2022-08-05T19:00:00Z",
        )

    def test_same_kickoff_is_atomic_group(self):
        rows = [{"kickoff": "a"}, {"kickoff": "a"}, {"kickoff": "b"}]
        self.assertEqual(oof.kickoff_groups(rows), [(0, 2), (2, 3)])

    def test_window_state_uses_only_requested_prior_tail(self):
        history = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
        self.assertEqual(oof.team_state(history, "w", 2), (4.0, 5.0, 3))

    def test_perfect_metrics(self):
        result = oof.metrics(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            [0, 1, 2],
        )
        self.assertEqual(result["top1"], 1.0)
        self.assertLess(result["logloss"], 1e-12)

    def test_zero_residual_preserves_formal_probabilities(self):
        probability = oof.softmax_offset(
            [0.4, 0.3, 0.3],
            [0.0, 0.0],
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        )
        self.assertAlmostEqual(sum(probability), 1.0, 12)
        self.assertAlmostEqual(probability[0], 0.4, 12)
        self.assertAlmostEqual(probability[1], 0.3, 12)
        self.assertAlmostEqual(probability[2], 0.3, 12)


if __name__ == "__main__":
    unittest.main()
