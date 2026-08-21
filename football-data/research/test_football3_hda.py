from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path, PureWindowsPath

from football3_hda import (
    COMPLETE_HDA,
    DEFAULT_PROBABILITY_TOLERANCE,
    DEFAULT_TIE_TOLERANCE,
    HDA_CLASS_ORDER,
    HDA_SCHEMA_VERSION,
    HDAValidationError,
    PARTIAL_HDA_UNRESOLVED_TAIL,
    ROBUST_PARTIAL_TOP1,
    ScoreCell,
    TOP1_TIE,
    TOP1_UNRESOLVED_DUE_TO_TAIL,
    aggregate_score_matrix_to_hda,
    canonical_support_sha256,
    choose_hda_top1,
    load_score_support_registry,
    validate_score_matrix,
)
from football3_hda_scoring import draw_classification_metrics, score_hda_probabilities
from audit_football3_changed_scientific_files import scoring_module_blockers, zero_label_hda_blockers
from run_football3_hda_zero_label_audit import repo_path, scope_differences, validate_lineage

FOOTBALL3_ZERO_LABEL_TEST_SURFACE = "HDA_SYNTHETIC_ZERO_LABEL_TESTS_ONLY"

P_TOL = DEFAULT_PROBABILITY_TOLERANCE
TIE_TOL = DEFAULT_TIE_TOLERANCE
COMPLETE_ID = "d71f02ab92ecece6ca5ccd682ed2e0b5455b19dea8f0659ef1c29a7b074b694e"
PARTIAL_ID = "688b42ad005ea72c1b3b67f4fd5ff4525b77edf4a2d59cc7fac52c44cc77b7f5"
RESEARCH_DIR = Path(__file__).resolve().parent
GUARD_SOURCE = RESEARCH_DIR / "audit_football3_changed_scientific_files.py"


def git_at(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT).strip()


def init_git_repo(root: Path, initial_files: dict[str, str]) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "lineage-test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Lineage Test"], cwd=root, check=True)
    for relative, content in initial_files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        subprocess.run(["git", "add", "--", relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return git_at(root, "rev-parse", "HEAD")


def commit_git_changes(root: Path, message: str, changes: dict[str, str | None]) -> str:
    for relative, content in changes.items():
        path = root / relative
        if content is None:
            subprocess.run(["git", "rm", "-q", "--", relative], cwd=root, check=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            subprocess.run(["git", "add", "--", relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)
    return git_at(root, "rev-parse", "HEAD")


def make_linear_lineage(root: Path) -> tuple[str, str, str, str]:
    base = init_git_repo(root, {"allowed.txt": "base\n"})
    r2 = commit_git_changes(root, "r2 failed", {"allowed.txt": "r2\n"})
    first = commit_git_changes(root, "r3 first", {"allowed.txt": "r3-a\n"})
    head = commit_git_changes(root, "r3 second", {"allowed.txt": "r3-b\n"})
    return base, r2, first, head


def total_leq(n: int) -> tuple[tuple[int, int], ...]:
    return tuple((h, total - h) for total in range(n + 1) for h in range(total + 1))


COMPLETE_CELLS = total_leq(26)
PARTIAL_CELLS = total_leq(6)


def score_cells(required, masses: dict[tuple[int, int], float]) -> list[ScoreCell]:
    return [ScoreCell(h, a, float(masses.get((h, a), 0.0))) for h, a in required]


def matrix_masses(matrix: list[list[float]]) -> dict[tuple[int, int], float]:
    return {(h, a): float(matrix[h][a]) for h in range(len(matrix)) for a in range(len(matrix[h]))}


def agg_complete(matrix: list[list[float]]):
    return aggregate_score_matrix_to_hda(
        score_cells(COMPLETE_CELLS, matrix_masses(matrix)),
        unresolved_tail=False,
        tail_probability=0.0,
        class_order=HDA_CLASS_ORDER,
        probability_tolerance=P_TOL,
        tie_tolerance=TIE_TOL,
        score_support_id=COMPLETE_ID,
        schema_version=HDA_SCHEMA_VERSION,
        required_score_cells=COMPLETE_CELLS,
    )


def agg_partial(masses: dict[tuple[int, int], float], tail: float):
    return aggregate_score_matrix_to_hda(
        score_cells(PARTIAL_CELLS, masses),
        unresolved_tail=True,
        tail_probability=tail,
        class_order=HDA_CLASS_ORDER,
        probability_tolerance=P_TOL,
        tie_tolerance=TIE_TOL,
        score_support_id=PARTIAL_ID,
        schema_version=HDA_SCHEMA_VERSION,
        required_score_cells=PARTIAL_CELLS,
    )


LOW_SYMMETRIC = [
    [0.20, 0.16, 0.08],
    [0.16, 0.12, 0.07],
    [0.08, 0.07, 0.06],
]
MID_SYMMETRIC = [
    [0.10, 0.14, 0.12],
    [0.14, 0.09, 0.105],
    [0.12, 0.105, 0.08],
]
HOME_ADVANTAGE = [
    [0.08, 0.05, 0.03],
    [0.20, 0.10, 0.04],
    [0.22, 0.18, 0.10],
]
AWAY_ADVANTAGE = [list(row) for row in zip(*HOME_ADVANTAGE)]
DRAW_AGGREGATE_NOT_SCORE_TOP1 = [
    [0.12, 0.18, 0.09],
    [0.19, 0.11, 0.06],
    [0.08, 0.06, 0.11],
]
ONE_ONE_SCORE_TOP1_HOME_HDA = [
    [0.05, 0.11, 0.09],
    [0.18, 0.22, 0.04],
    [0.16, 0.10, 0.05],
]


class HDATest(unittest.TestCase):
    def assertFailCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(HDAValidationError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def complete_call(self, cells=None, **overrides):
        kwargs = dict(
            score_cells=cells if cells is not None else score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)),
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id=COMPLETE_ID,
            schema_version=HDA_SCHEMA_VERSION,
            required_score_cells=COMPLETE_CELLS,
        )
        kwargs.update(overrides)
        score_arg = kwargs.pop("score_cells")
        return aggregate_score_matrix_to_hda(score_arg, **kwargs)

    def run_guard_case(self, changed_path: str, base_text: str, changed_text: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / changed_path
            guard = root / "football-data/research/audit_football3_changed_scientific_files.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            guard.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(GUARD_SOURCE, guard)
            target.write_text(base_text, encoding="utf-8", newline="\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "zero-label@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Zero Label Test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            target.write_text(changed_text, encoding="utf-8", newline="\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "change"], cwd=root, check=True)
            return subprocess.run(
                [sys.executable, str(guard.relative_to(root)), "--base", base, "--head", "HEAD"],
                cwd=root,
                text=True,
                capture_output=True,
            )

    # Core behavior: exact-score aggregation, no labels.
    def test_symmetric_low_goals_draw_is_natural_top1_below_half(self):
        r = agg_complete(LOW_SYMMETRIC)
        self.assertEqual(r["status"], COMPLETE_HDA)
        self.assertEqual(r["top1_category"], "DRAW")
        self.assertLess(r["draw_probability"], 0.5)

    def test_symmetric_mid_goals_home_away_tie_and_draw_lower(self):
        r = agg_complete(MID_SYMMETRIC)
        self.assertEqual(r["top1_status"], TOP1_TIE)
        self.assertIsNone(r["top1_category"])
        self.assertEqual(set(r["tied_categories"]), {"HOME", "AWAY"})
        self.assertLess(r["draw_probability"], r["known_home_mass"])

    def test_home_advantage_home_top1(self):
        self.assertEqual(agg_complete(HOME_ADVANTAGE)["top1_category"], "HOME")

    def test_away_advantage_away_top1(self):
        self.assertEqual(agg_complete(AWAY_ADVANTAGE)["top1_category"], "AWAY")

    def test_aggregate_draw_top1_without_draw_exact_score_top1(self):
        r = agg_complete(DRAW_AGGREGATE_NOT_SCORE_TOP1)
        self.assertEqual(r["top1_category"], "DRAW")
        self.assertNotEqual(r["exact_score_top1_category"], "DRAW")

    def test_one_one_exact_score_top1_but_home_hda_top1(self):
        r = agg_complete(ONE_ONE_SCORE_TOP1_HOME_HDA)
        self.assertEqual(tuple(r["max_specific_score"]), (1, 1))
        self.assertEqual(r["top1_category"], "HOME")
        self.assertFalse(r["exact_score_top1_and_hda_top1_agree"])

    def test_tail_7plus_unresolved_is_partial_not_full_hda(self):
        r = agg_partial({(0, 0): 0.20, (1, 0): 0.25, (0, 1): 0.25, (1, 1): 0.10, (2, 0): 0.05, (0, 2): 0.05}, 0.10)
        self.assertEqual(r["status"], PARTIAL_HDA_UNRESOLVED_TAIL)
        self.assertIsNone(r["home_probability"])
        self.assertIsNone(r["draw_probability"])
        self.assertIsNone(r["away_probability"])

    def test_tail_with_robust_top1(self):
        r = agg_partial({(1, 0): 0.70, (0, 1): 0.10, (0, 0): 0.10}, 0.10)
        self.assertEqual(r["top1_status"], ROBUST_PARTIAL_TOP1)
        self.assertEqual(r["top1_category"], "HOME")

    def test_tail_can_change_top1_is_unresolved(self):
        r = agg_partial({(1, 0): 0.34, (0, 1): 0.30, (0, 0): 0.26}, 0.10)
        self.assertEqual(r["top1_status"], TOP1_UNRESOLVED_DUE_TO_TAIL)
        self.assertIsNone(r["top1_category"])

    def test_floating_home_away_difference_5e_17_is_top1_tie(self):
        r = choose_hda_top1(
            {"HOME": 0.4000000000000001, "DRAW": 0.2, "AWAY": 0.4},
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )
        self.assertEqual(r["status"], TOP1_TIE)
        self.assertIsNone(r["top1_category"])

    def test_tiny_allowed_residual_is_recorded_not_normalized(self):
        masses = matrix_masses(LOW_SYMMETRIC)
        masses[(0, 0)] += 5e-13
        r = self.complete_call(score_cells(COMPLETE_CELLS, masses))
        self.assertNotEqual(r["raw_total_probability"], 1.0)
        self.assertAlmostEqual(r["probability_residual"], 5e-13, places=15)

    def test_registry_complete_hash_matches_frozen_cells(self):
        self.assertEqual(canonical_support_sha256(COMPLETE_CELLS), COMPLETE_ID)
        self.assertEqual(len(COMPLETE_CELLS), 378)

    def test_registry_partial_hash_matches_frozen_cells(self):
        self.assertEqual(canonical_support_sha256(PARTIAL_CELLS), PARTIAL_ID)
        self.assertEqual(len(PARTIAL_CELLS), 28)

    def test_label_scoring_api_removed_from_zero_label_module(self):
        module = importlib.import_module("football3_hda")
        scoring = importlib.import_module("football3_hda_scoring")
        forbidden = "score_hda_" + "probabilities"
        forbidden2 = "draw_classification_" + "metrics"
        self.assertFalse(hasattr(module, forbidden))
        self.assertFalse(hasattr(module, forbidden2))
        self.assertTrue(hasattr(scoring, forbidden))
        self.assertTrue(hasattr(scoring, forbidden2))

    def test_synthetic_hda_scoring_restores_proper_scores_and_diagnostics(self):
        rows = [
            {"HOME": 0.6, "DRAW": 0.2, "AWAY": 0.2},
            {"HOME": 0.2, "DRAW": 0.5, "AWAY": 0.3},
            {"HOME": 0.4, "DRAW": 0.2, "AWAY": 0.4},
        ]
        labels = ["HOME", "DRAW", "AWAY"]
        result = score_hda_probabilities(
            rows, labels, class_order=HDA_CLASS_ORDER, probability_tolerance=P_TOL, tie_tolerance=TIE_TOL
        )
        self.assertAlmostEqual(result["LogLoss"], (-math.log(0.6) - math.log(0.5) - math.log(0.4)) / 3.0)
        self.assertGreaterEqual(result["Brier"], 0.0)
        self.assertGreaterEqual(result["RPS"], 0.0)
        self.assertTrue(result["proper_scores_primary"])
        self.assertFalse(result["classification_metrics_can_override_proper_score_failure"])
        self.assertEqual(result["Top1TieCount"], 1)
        self.assertAlmostEqual(result["Accuracy"], 2 / 3)
        self.assertEqual(result["confusion_matrix"]["AWAY"][TOP1_TIE], 1)

    def test_synthetic_draw_diagnostics_are_diagnostic_only(self):
        rows = [
            {"HOME": 0.2, "DRAW": 0.6, "AWAY": 0.2},
            {"HOME": 0.6, "DRAW": 0.2, "AWAY": 0.2},
        ]
        labels = ["DRAW", "HOME"]
        result = draw_classification_metrics(
            rows, labels, class_order=HDA_CLASS_ORDER, probability_tolerance=P_TOL, tie_tolerance=TIE_TOL
        )
        self.assertTrue(result["classification_metrics_diagnostic_only"])
        self.assertEqual(result["DrawPrecision"], 1.0)
        self.assertEqual(result["DrawRecall"], 1.0)
        self.assertEqual(result["DrawF1"], 1.0)

    def test_synthetic_scoring_zero_truth_probability_is_infinite_logloss(self):
        rows = [{"HOME": 0.0, "DRAW": 0.4, "AWAY": 0.6}]
        result = score_hda_probabilities(
            rows, ["HOME"], class_order=HDA_CLASS_ORDER, probability_tolerance=P_TOL, tie_tolerance=TIE_TOL
        )
        self.assertTrue(math.isinf(result["LogLoss"]))

    def test_synthetic_scoring_invalid_label_fails_closed(self):
        rows = [{"HOME": 0.3, "DRAW": 0.3, "AWAY": 0.4}]
        self.assertFailCode(
            "INVALID_HDA_LABEL",
            score_hda_probabilities,
            rows, ["INVALID"],
            class_order=HDA_CLASS_ORDER, probability_tolerance=P_TOL, tie_tolerance=TIE_TOL,
        )

    def test_production_ast_contracts_accept_current_modules(self):
        self.assertEqual(zero_label_hda_blockers(RESEARCH_DIR / "football3_hda.py"), [])
        self.assertEqual(scoring_module_blockers(RESEARCH_DIR / "football3_hda_scoring.py"), [])

    # Support-contract production counterexamples required by Codex.
    def test_fabricated_schema_version_fails_closed(self):
        self.assertFailCode("INVALID_HDA_SCHEMA_VERSION", self.complete_call, schema_version="anything")

    def test_fabricated_score_support_id_fails_closed(self):
        self.assertFailCode("UNKNOWN_SCORE_SUPPORT_ID", self.complete_call, score_support_id="f" * 64)

    def test_support_id_required_cells_hash_mismatch_fails_closed(self):
        self.assertFailCode("SUPPORT_ID_REQUIRED_CELLS_HASH_MISMATCH", self.complete_call, required_score_cells=PARTIAL_CELLS)

    def test_truncated_support_sum_one_claim_complete_fails_closed(self):
        truncated = tuple(c for c in COMPLETE_CELLS if c != (26, 0))
        masses = {c: 0.0 for c in truncated}
        masses[(0, 0)] = 1.0
        self.assertFailCode(
            "SUPPORT_ID_REQUIRED_CELLS_HASH_MISMATCH",
            self.complete_call,
            score_cells(truncated, masses),
            required_score_cells=truncated,
        )

    def test_delete_high_score_cell_but_claim_complete_fails_closed(self):
        actual = [c for c in score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)) if c.score != (26, 0)]
        self.assertFailCode("MISSING_REQUIRED_SCORE_CELL", self.complete_call, actual)

    def test_t7_unresolved_but_claim_resolved_fails_closed(self):
        self.assertFailCode(
            "SUPPORT_TAIL_STATUS_MISMATCH",
            self.complete_call,
            score_cells(PARTIAL_CELLS, {(0, 0): 1.0}),
            score_support_id=PARTIAL_ID,
            required_score_cells=PARTIAL_CELLS,
            unresolved_tail=False,
        )

    def test_duplicate_required_score_cells_fails_closed(self):
        duplicated = COMPLETE_CELLS + (COMPLETE_CELLS[-1],)
        self.assertFailCode("DUPLICATE_REQUIRED_SCORE_CELL", self.complete_call, required_score_cells=duplicated)

    def test_support_registry_missing_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFailCode("SUPPORT_REGISTRY_NOT_FOUND", load_score_support_registry, Path(td) / "missing.json")

    def test_support_registry_unknown_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "registry.json"
            p.write_text(json.dumps({"registry_schema_version": "anything", "supports": []}), encoding="utf-8")
            self.assertFailCode("UNKNOWN_SUPPORT_REGISTRY_VERSION", load_score_support_registry, p)

    def test_support_registry_corrupt_hash_fails_closed(self):
        src = RESEARCH_DIR / "football3_hda_score_support_registry_v1.json"
        obj = json.loads(src.read_text(encoding="utf-8"))
        obj["supports"][0]["required_score_cells_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "registry.json"
            p.write_text(json.dumps(obj), encoding="utf-8")
            self.assertFailCode("SUPPORT_REGISTRY_HASH_MISMATCH", load_score_support_registry, p)

    # Existing production fail-closed invariants.
    def test_total_probability_point_nine_fails_closed(self):
        masses = matrix_masses(LOW_SYMMETRIC)
        masses[(0, 0)] -= 0.1
        self.assertFailCode("PROBABILITY_MASS_NOT_CONSERVED", self.complete_call, score_cells(COMPLETE_CELLS, masses))

    def test_negative_probability_fails_closed(self):
        masses = matrix_masses(LOW_SYMMETRIC); masses[(0, 0)] = -0.1; masses[(0, 1)] += 0.3
        self.assertFailCode("NEGATIVE_PROBABILITY", self.complete_call, score_cells(COMPLETE_CELLS, masses))

    def test_nan_probability_fails_closed(self):
        masses = matrix_masses(LOW_SYMMETRIC); masses[(0, 0)] = math.nan
        self.assertFailCode("NONFINITE_PROBABILITY", self.complete_call, score_cells(COMPLETE_CELLS, masses))

    def test_inf_probability_fails_closed(self):
        masses = matrix_masses(LOW_SYMMETRIC); masses[(0, 0)] = math.inf
        self.assertFailCode("NONFINITE_PROBABILITY", self.complete_call, score_cells(COMPLETE_CELLS, masses))

    def test_duplicate_score_cell_fails_closed(self):
        actual = score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)); actual.append(actual[0])
        self.assertFailCode("DUPLICATE_SCORE_CELL", self.complete_call, actual)

    def test_conflicting_duplicate_score_cell_fails_closed(self):
        actual = score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)); actual.append(ScoreCell(actual[0].home_goals, actual[0].away_goals, actual[0].probability + 0.01))
        self.assertFailCode("CONFLICTING_DUPLICATE_SCORE_CELL", self.complete_call, actual)

    def test_missing_required_diagonal_fails_closed(self):
        actual = [c for c in score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)) if c.score != (13, 13)]
        self.assertFailCode("MISSING_REQUIRED_DIAGONAL", self.complete_call, actual)

    def test_wrong_category_order_fails_closed(self):
        self.assertFailCode("INVALID_CLASS_ORDER", self.complete_call, class_order=("AWAY", "DRAW", "HOME"))

    def test_noninteger_score_fails_closed(self):
        actual = score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)); actual[0] = ScoreCell(0.0, 0, actual[0].probability)  # type: ignore[arg-type]
        self.assertFailCode("INVALID_SCORE", self.complete_call, actual)

    def test_missing_class_order_fails_closed(self):
        self.assertFailCode("MISSING_CLASS_ORDER", self.complete_call, class_order=None)

    def test_missing_tail_status_fails_closed(self):
        self.assertFailCode("MISSING_TAIL_STATUS", self.complete_call, unresolved_tail=None)

    def test_tail_mass_status_conflict_fails_closed(self):
        self.assertFailCode("TAIL_STATUS_MASS_CONFLICT", self.complete_call, tail_probability=0.01)

    def test_unfrozen_tolerance_fails_closed(self):
        self.assertFailCode("UNFROZEN_PROBABILITY_TOLERANCE", self.complete_call, probability_tolerance=1e-9)

    def test_negative_score_fails_closed(self):
        actual = score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)); actual[0] = ScoreCell(-1, 0, actual[0].probability)
        self.assertFailCode("INVALID_SCORE", self.complete_call, actual)

    def test_score_cell_outside_support_fails_closed(self):
        actual = score_cells(COMPLETE_CELLS, matrix_masses(LOW_SYMMETRIC)) + [ScoreCell(27, 0, 0.0)]
        self.assertFailCode("SCORE_CELL_OUTSIDE_SUPPORT", self.complete_call, actual)

    def test_missing_support_identity_fails_closed(self):
        self.assertFailCode("MISSING_SCORE_SUPPORT_IDENTITY", self.complete_call, score_support_id=None)

    def test_unresolved_tail_zero_mass_fails_closed(self):
        self.assertFailCode(
            "UNRESOLVED_TAIL_REQUIRES_POSITIVE_MASS",
            self.complete_call,
            score_cells(PARTIAL_CELLS, {(0, 0): 1.0}),
            score_support_id=PARTIAL_ID,
            required_score_cells=PARTIAL_CELLS,
            unresolved_tail=True,
            tail_probability=0.0,
        )

    def test_wrong_tie_tolerance_fails_closed(self):
        self.assertFailCode("UNFROZEN_TIE_TOLERANCE", self.complete_call, tie_tolerance=1e-6)

    # Guard production counterexamples required by Codex R2.
    def test_guard_rejects_evaluate_hda_rows_labels_in_zero_label_module_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        changed = base + '\ndef evaluate_hda(probability_rows, labels):\n    return {"LogLoss": 0.0}\n'
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("zero-label function set mismatch", r.stdout)
        self.assertIn("forbidden target-value parameter identifier: labels", r.stdout)

    def test_guard_rejects_calculate_metrics_rows_truth_in_zero_label_module_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        changed = base + '\ndef calculate_metrics(rows, truth):\n    return 0.0\n'
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("forbidden target-value parameter identifier: truth", r.stdout)

    def test_guard_rejects_alias_import_of_scoring_without_contract_fails_closed(self):
        base = 'def noop():\n    return 0\n'
        changed = 'from football3_hda_scoring import score_hda_probabilities as f\n\ndef run(rows, labels):\n    return f(rows, labels)\n'
        r = self.run_guard_case("football-data/research/future_hda_runner.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("HDA scoring reference must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR", r.stdout)

    def test_guard_rejects_getattr_scoring_route_without_contract_fails_closed(self):
        base = 'def noop():\n    return 0\n'
        routes = [
            'import football3_hda_scoring as scoring\n\ndef run(rows, labels):\n    f = getattr(scoring, "score_hda_probabilities")\n    return f(rows, labels)\n',
            'import importlib\n\ndef run(rows, labels):\n    module_name = "football3_hda_scoring"\n    scoring = importlib.import_module(module_name)\n    return scoring.score_hda_probabilities(rows, labels, class_order=("HOME", "DRAW", "AWAY"), probability_tolerance=1e-12, tie_tolerance=1e-12)\n',
        ]
        for changed in routes:
            with self.subTest(route=changed.splitlines()[0]):
                r = self.run_guard_case("football-data/research/future_hda_runner.py", base, changed)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("HDA scoring reference must declare", r.stdout)

    def test_guard_rejects_reexport_of_scoring_from_zero_label_module_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        changed = base.replace(
            'from __future__ import annotations\n',
            'from __future__ import annotations\nfrom football3_hda_scoring import score_hda_probabilities\n',
            1,
        )
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("imports outside AST contract", r.stdout)

    def test_guard_rejects_labels_parameter_added_to_allowed_aggregate_function_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        needle = '    required_score_cells: Iterable[tuple[int, int]] | None,\n) -> dict[str, object]:\n    """Aggregate a registered score matrix into H/D/A without labels, fitting or I/O."""'
        replacement = '    required_score_cells: Iterable[tuple[int, int]] | None,\n    labels: Sequence[str] | None = None,\n) -> dict[str, object]:\n    """Aggregate a registered score matrix into H/D/A without labels, fitting or I/O."""'
        self.assertIn(needle, base)
        changed = base.replace(needle, replacement, 1)
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("function signature mismatch for aggregate_score_matrix_to_hda", r.stdout)
        self.assertIn("forbidden target-value parameter identifier: labels", r.stdout)

    def test_guard_rejects_top_level_results_file_read_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        changed = base + '\nRESULTS = open("results.csv").read()\n'
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("forbidden I/O/network/model/scoring call in zero-label module: open", r.stdout)

    def test_guard_rejects_accuracy_score_and_log_loss_in_zero_label_module_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        needle = '    """Aggregate a registered score matrix into H/D/A without labels, fitting or I/O."""\n'
        changed = base.replace(needle, needle + '    accuracy_score([], [])\n    log_loss([], [])\n', 1)
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("forbidden scoring identifier in zero-label module: accuracy_score", r.stdout)
        self.assertIn("forbidden scoring identifier in zero-label module: log_loss", r.stdout)

    def test_scoring_ast_allowlist_rejects_import_system_without_execution_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda_scoring.py").read_text(encoding="utf-8")
        malicious = base + '\n__import__("os").system("never-execute")\n'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "football3_hda_scoring.py"
            path.write_text(malicious, encoding="utf-8", newline="\n")
            with patch("os.system", side_effect=AssertionError("AST inspection executed malicious payload")):
                blockers = scoring_module_blockers(path)
        self.assertTrue(blockers)
        self.assertTrue(any("scoring call outside explicit purity contract" in item for item in blockers), blockers)
        self.assertTrue(any("__import__" in item for item in blockers), blockers)

    def test_scoring_ast_allowlist_rejects_unresolved_subscript_callable_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda_scoring.py").read_text(encoding="utf-8")
        needle = '    rows = _validate_metric_inputs(\n'
        self.assertIn(needle, base)
        malicious = base.replace(needle, '    callbacks["metric"]()\n' + needle, 1)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "football3_hda_scoring.py"
            path.write_text(malicious, encoding="utf-8", newline="\n")
            blockers = scoring_module_blockers(path)
        self.assertTrue(any("unresolved ast.Call target in scoring module" in item for item in blockers), blockers)

    def test_scoring_ast_allowlist_rejects_unknown_class_and_decorator_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda_scoring.py").read_text(encoding="utf-8")
        variants = {
            "class": base + "\nclass RogueCallable:\n    pass\n",
            "decorator": base.replace(
                "def score_hda_probabilities(\n",
                "@staticmethod\ndef score_hda_probabilities(\n",
                1,
            ),
        }
        for kind, malicious in variants.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                path = Path(td) / "football3_hda_scoring.py"
                path.write_text(malicious, encoding="utf-8", newline="\n")
                blockers = scoring_module_blockers(path)
                self.assertTrue(blockers)
                if kind == "class":
                    self.assertTrue(any("class definitions are outside purity contract" in item for item in blockers), blockers)
                else:
                    self.assertTrue(any("decorators are outside purity contract" in item for item in blockers), blockers)

    def test_guard_rejects_import_module_alias_derived_scoring_in_exempt_core_fails_closed(self):
        base = 'def noop():\n    return 0\n'
        changed = (
            'from importlib import import_module as load_module\n\n'
            'def load_scoring():\n'
            '    module_name = "football3_hda_" + "scoring"\n'
            '    return load_module(module_name)\n'
        )
        r = self.run_guard_case("football-data/research/football3_core.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("HDA scoring reference must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR", r.stdout)

    def test_guard_rejects_exec_eval_compile_scoring_construction_fails_closed(self):
        base = 'def noop():\n    return 0\n'
        variants = {
            "exec": 'def build():\n    payload = "import football3_hda_" + "scoring"\n    exec(payload)\n',
            "eval": 'def build():\n    payload = "__import__(\\\"football3_hda_\\\" + \\\"scoring\\\")"\n    return eval(payload)\n',
            "compile": 'def build():\n    payload = "import football3_hda_" + "scoring"\n    return compile(payload, "<dynamic>", "exec")\n',
        }
        for kind, changed in variants.items():
            with self.subTest(kind=kind):
                r = self.run_guard_case("football-data/research/football3_core.py", base, changed)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("HDA scoring reference must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR", r.stdout)

    def test_guard_rejects_builtins_dynamic_execution_aliases_in_exempt_core_fails_closed(self):
        base = 'def noop():\n    return 0\n'
        variants = {
            "module_exec": (
                'import builtins as b\n\n'
                'def build():\n'
                '    payload = "import football3_hda_" + "scoring"\n'
                '    b.exec(payload)\n'
            ),
            "imported_exec_alias": (
                'from builtins import exec as run_code\n\n'
                'def build():\n'
                '    payload = "import football3_hda_" + "scoring"\n'
                '    run_code(payload)\n'
            ),
            "module_eval": (
                'import builtins\n\n'
                'def build():\n'
                '    payload = "__import__(\\\"football3_hda_\\\" + \\\"scoring\\\")"\n'
                '    return builtins.eval(payload)\n'
            ),
            "module_alias_chain": (
                'import builtins as b\n'
                'b2 = b\n'
                'run_code = b2.exec\n\n'
                'def build():\n'
                '    payload = "import football3_hda_" + "scoring"\n'
                '    run_code(payload)\n'
            ),
            "builtins_subscript": (
                'def build():\n'
                '    payload = "import football3_hda_" + "scoring"\n'
                '    __builtins__["exec"](payload)\n'
            ),
            "conditional_callable": (
                'import builtins as b\n'
                'run_code = b.exec if __debug__ else print\n\n'
                'def build():\n'
                '    payload = "import football3_hda_" + "scoring"\n'
                '    run_code(payload)\n'
            ),
        }
        for kind, changed in variants.items():
            with self.subTest(kind=kind):
                r = self.run_guard_case("football-data/research/football3_core.py", base, changed)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("HDA scoring reference must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR", r.stdout)

    def test_zero_label_ast_allowlist_rejects_subscript_callback_fails_closed(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        needle = '    if not isinstance(generator, dict) or generator.get("kind") != "total_leq":\n'
        self.assertIn(needle, base)
        changed = base.replace(needle, '    generator["callback"]()\n' + needle, 1)
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("unresolved ast.Call target in zero-label module", r.stdout)

    def test_zero_label_ast_contract_allows_comment_only_change(self):
        base = (RESEARCH_DIR / "football3_hda.py").read_text(encoding="utf-8")
        changed = base + '\n# harmless zero-label documentation-only change\n'
        r = self.run_guard_case("football-data/research/football3_hda.py", base, changed)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_windows_repo_identity_path_is_posix(self):
        self.assertEqual(
            repo_path(PureWindowsPath(r"football-data\research\football3_hda.py")),
            "football-data/research/football3_hda.py",
        )

    def test_continuous_ci_is_head_ref_based_not_fixed_base(self):
        workflow = (RESEARCH_DIR.parent.parent / ".github/workflows/football3-hda-aggregation-engineering-v1.yml")
        text = workflow.read_text(encoding="utf-8")
        pull_request_block = text.split("pull_request:", 1)[1].split("workflow_dispatch:", 1)[0]
        self.assertNotIn("branches:", pull_request_block)
        self.assertIn("football3_hda_scoring.py", pull_request_block)
        self.assertIn("startsWith(github.head_ref, 'football3/')", text)
        self.assertIn("hda-continuous-structure-linux", text)
        self.assertIn("hda-continuous-structure-windows", text)

    def test_windows_exact_job_runs_full_production_audit_entry(self):
        workflow = (RESEARCH_DIR.parent.parent / ".github/workflows/football3-hda-aggregation-engineering-v1.yml")
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("hda-pr334-exact-head-windows-full-audit", text)
        self.assertIn("Run complete production audit entry under Windows", text)
        self.assertIn("run_football3_hda_zero_label_audit.py --out-dir", text)

    def test_windows_path_normalization_does_not_hide_extra_file_fails_closed(self):
        expected = {
            "football-data/research/football3_hda.py",
            "football-data/research/football3_hda_scoring.py",
        }
        actual = [
            "football-data/research/football3_hda.py",
            "football-data/research/football3_hda_scoring.py",
            "football-data/research/extra_science.py",
        ]
        unexpected, missing = scope_differences(actual, expected)
        self.assertEqual(unexpected, ["football-data/research/extra_science.py"])
        self.assertEqual(missing, [])

    # R3 lineage semantics: R2 is an ancestor, while direct parent follows exact HEAD.
    def test_lineage_rejects_r2_that_is_not_ancestor_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = init_git_repo(root, {"allowed.txt": "base\n"})
            r2 = commit_git_changes(root, "r2 failed", {"allowed.txt": "r2\n"})
            subprocess.run(["git", "checkout", "-q", "-b", "independent", base], cwd=root, check=True)
            head = commit_git_changes(root, "independent r3", {"allowed.txt": "other\n"})
            with self.assertRaisesRegex(RuntimeError, "R2 failed ancestor is not an ancestor"):
                validate_lineage(
                    expected_exact_head=head,
                    r2_failed_ancestor=r2,
                    pr_base_head=base,
                    expected_changed_files={"allowed.txt"},
                    cwd=root,
                )

    def test_lineage_rejects_forged_direct_parent_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base, r2, _, head = make_linear_lineage(root)
            with self.assertRaisesRegex(RuntimeError, "direct parent claim mismatch"):
                validate_lineage(
                    expected_exact_head=head,
                    r2_failed_ancestor=r2,
                    pr_base_head=base,
                    expected_changed_files={"allowed.txt"},
                    cwd=root,
                    claimed_direct_parent="0" * 40,
                )

    def test_lineage_rejects_merge_commit_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = init_git_repo(root, {"a.txt": "a0\n", "b.txt": "b0\n"})
            r2 = commit_git_changes(root, "r2 failed", {"a.txt": "a-r2\n"})
            subprocess.run(["git", "checkout", "-q", "-b", "side", r2], cwd=root, check=True)
            commit_git_changes(root, "side change", {"b.txt": "b-side\n"})
            subprocess.run(["git", "checkout", "-q", "master"], cwd=root, check=True)
            commit_git_changes(root, "main change", {"a.txt": "a-main\n"})
            subprocess.run(["git", "merge", "--no-ff", "-qm", "merge side", "side"], cwd=root, check=True)
            head = git_at(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(RuntimeError, "merge commit detected in R3 lineage"):
                validate_lineage(
                    expected_exact_head=head,
                    r2_failed_ancestor=r2,
                    pr_base_head=base,
                    expected_changed_files={"a.txt", "b.txt"},
                    cwd=root,
                )

    def test_lineage_rejects_hidden_eighth_file_even_if_removed_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = init_git_repo(root, {"allowed.txt": "base\n"})
            r2 = commit_git_changes(root, "r2 failed", {"allowed.txt": "r2\n"})
            commit_git_changes(root, "r3 allowed", {"allowed.txt": "r3\n"})
            commit_git_changes(root, "smuggle eighth", {"eighth.txt": "forbidden\n"})
            head = commit_git_changes(root, "hide eighth", {"eighth.txt": None})
            self.assertEqual(git_at(root, "diff", "--name-only", f"{base}..{head}"), "allowed.txt")
            with self.assertRaisesRegex(RuntimeError, "unexpected file touched in R3 lineage commit"):
                validate_lineage(
                    expected_exact_head=head,
                    r2_failed_ancestor=r2,
                    pr_base_head=base,
                    expected_changed_files={"allowed.txt"},
                    cwd=root,
                )

    def test_lineage_accepts_normal_multi_commit_linear_r3_chain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base, r2, first, head = make_linear_lineage(root)
            receipt = validate_lineage(
                expected_exact_head=head,
                r2_failed_ancestor=r2,
                pr_base_head=base,
                expected_changed_files={"allowed.txt"},
                cwd=root,
            )
            self.assertEqual(receipt["exact_head"], head)
            self.assertEqual(receipt["direct_parent"], first)
            self.assertEqual(receipt["r2_failed_ancestor"], r2)
            self.assertEqual(receipt["lineage_commit_count"], 2)
            self.assertEqual(receipt["r2_failed_plus_r3_scope_files"], ["allowed.txt"])
            self.assertEqual(receipt["final_pr_net_changed_files"], ["allowed.txt"])

    def test_artifact_lineage_fields_are_explicit_and_not_parent_head_alias(self):
        audit_source = (RESEARCH_DIR / "run_football3_hda_zero_label_audit.py").read_text(encoding="utf-8")
        self.assertIn('"exact_head"', audit_source)
        self.assertIn('"direct_parent"', audit_source)
        self.assertIn('"r2_failed_ancestor"', audit_source)
        self.assertNotIn('"parent_head"', audit_source)


if __name__ == "__main__":
    unittest.main()
