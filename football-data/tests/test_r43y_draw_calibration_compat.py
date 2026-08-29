from __future__ import annotations

import ast
import json
import math
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.r43y_draw_calibration import (
    CLASSES,
    DRAW_LOGIT_INTERCEPT,
    FROZEN_LEDGER_BLOB_SHA,
    R43YDrawCalibrationComponent,
    SOURCE_BLOB_SHA,
    SOURCE_COMMIT,
    calibrate,
)

EXPECTED_SOURCE_COMMIT = "7043d6f7788f05b958e2ab7ec743b982a54ec5aa"
EXPECTED_SOURCE_BLOB = "a342138bef97eb4acb0bcba015dea251a3280fdf"
EXPECTED_LEDGER_BLOB = "faafc8baf094452096cab62df56bf2fb92af10b8"
EXPECTED_INTERCEPT = 0.1322913820792354


def load_pinned_original():
    source_path = Path(os.environ["R43Y_SOURCE_FILE"])
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    wanted_assigns = {
        "CLASSES",
        "SOURCE_RUN_ID",
        "SOURCE_BRANCH",
        "SOURCE_U0_N",
        "SOURCE_DRAW_MEAN",
        "SOURCE_DRAW_RATE",
        "DRAW_LOGIT_INTERCEPT",
    }
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assigns:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {"logit", "sigmoid", "calibrate"}:
            selected.append(node)
    scope = {"math": math}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source_path), "exec"), scope)
    return scope


class R43YCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = load_pinned_original()
        cls.ledger = json.loads(Path(os.environ["R43Y_LEDGER_FILE"]).read_text(encoding="utf-8"))
        cls.lock_summary = json.loads(Path(os.environ["R43Y_LOCK_SUMMARY_FILE"]).read_text(encoding="utf-8"))

    def test_provenance_and_default_disabled(self):
        self.assertEqual(SOURCE_COMMIT, EXPECTED_SOURCE_COMMIT)
        self.assertEqual(SOURCE_BLOB_SHA, EXPECTED_SOURCE_BLOB)
        self.assertEqual(FROZEN_LEDGER_BLOB_SHA, EXPECTED_LEDGER_BLOB)
        self.assertFalse(R43YDrawCalibrationComponent.enabled)
        self.assertEqual(R43YDrawCalibrationComponent.native_input, "1x2_probabilities")
        self.assertEqual(R43YDrawCalibrationComponent.native_output, "1x2_probabilities")
        self.assertFalse(R43YDrawCalibrationComponent.score_matrix_lifting_migrated)

    def test_constants_exact(self):
        self.assertEqual(DRAW_LOGIT_INTERCEPT, self.original["DRAW_LOGIT_INTERCEPT"])
        self.assertEqual(DRAW_LOGIT_INTERCEPT, EXPECTED_INTERCEPT)
        self.assertEqual(tuple(CLASSES), tuple(self.original["CLASSES"]))

    def test_synthetic_calibration_exact(self):
        cases = [
            {"home": 0.50, "draw": 0.28, "away": 0.22},
            {"home": 0.4457727810939734, "draw": 0.28579217137416046, "away": 0.2684350475318662},
            {"home": 0.31, "draw": 0.38, "away": 0.31},
            {"home": 0.70, "draw": 0.20, "away": 0.10},
        ]
        for p in cases:
            self.assertEqual(calibrate(p), self.original["calibrate"](p))
            self.assertEqual(R43YDrawCalibrationComponent.apply(p), self.original["calibrate"](p))

    def test_all_frozen_41_predictions_reproduced_exactly(self):
        events = [e for e in self.ledger["events"] if e.get("event_type") == "PREDICTION_FROZEN"]
        self.assertEqual(len(events), 41)
        for event in events:
            payload = event["payload"]
            source_p = payload["source_r43u0_probabilities"]
            frozen_q = payload["r43y0_probabilities"]
            self.assertEqual(calibrate(source_p), frozen_q, msg=str(event.get("match_id")))
            self.assertEqual(self.original["calibrate"](source_p), frozen_q, msg=str(event.get("match_id")))
            self.assertFalse(payload["draw_forced"])
            self.assertFalse(payload["outcome_access_at_lock"])

    def test_frozen_structural_activation_contract(self):
        s = self.lock_summary
        self.assertEqual(s["status"], "COMPLETE")
        self.assertEqual(s["classification"], "PRISTINE_PARALLEL_FORWARD_LOCK_NO_OUTCOME_ACCESS")
        self.assertEqual(s["coverage"]["total_locked_predictions"], 41)
        self.assertEqual(s["structural_activation"]["natural_draw_top1_count"], 3)
        self.assertEqual(s["structural_activation"]["top1_changed_count"], 3)
        self.assertEqual(s["structural_activation"]["changed_to_draw_count"], 3)
        self.assertEqual(s["ledger_audit"]["status"], "PASS")
        self.assertEqual(s["development_calibration_source"]["fixed_draw_logit_intercept"], EXPECTED_INTERCEPT)
        self.assertFalse(s["governance"]["outcome_access_at_prediction_lock"])
        self.assertFalse(s["governance"]["parameter_search"])
        self.assertFalse(s["governance"]["threshold_search"])
        self.assertFalse(s["governance"]["draw_override"])


if __name__ == "__main__":
    unittest.main()
