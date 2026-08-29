"""Exact research-governance migration of the frozen R43Y0 draw calibrator.

Pinned provenance snapshot:
  commit 7043d6f7788f05b958e2ab7ec743b982a54ec5aa
  source blob a342138bef97eb4acb0bcba015dea251a3280fdf
  frozen prediction ledger blob faafc8baf094452096cab62df56bf2fb92af10b8

R43Y0 is natively a 1X2 probability transform, not a score-matrix transform.
No matrix lifting rule is invented here. The component remains disabled until a
separate governance gate approves any matrix-level adapter and forward evidence.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

CLASSES = ("home", "draw", "away")
SOURCE_COMMIT = "7043d6f7788f05b958e2ab7ec743b982a54ec5aa"
SOURCE_BLOB_SHA = "a342138bef97eb4acb0bcba015dea251a3280fdf"
FROZEN_LEDGER_BLOB_SHA = "faafc8baf094452096cab62df56bf2fb92af10b8"
SOURCE_RUN_ID = 33178193071
SOURCE_BRANCH = "football3/r43x0-high-confidence-coverage"
SOURCE_U0_N = 53
SOURCE_DRAW_MEAN = 0.29263991077316237
SOURCE_DRAW_RATE = 0.32075471698113206


def logit(p):
    p = min(max(float(p), 1e-12), 1 - 1e-12)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


DRAW_LOGIT_INTERCEPT = logit(SOURCE_DRAW_RATE) - logit(SOURCE_DRAW_MEAN)


def calibrate(p):
    ph, pd, pa = float(p["home"]), float(p["draw"]), float(p["away"])
    qd = sigmoid(logit(pd) + DRAW_LOGIT_INTERCEPT)
    side = ph + pa
    if side <= 0:
        raise ValueError("invalid home/away mass")
    rest = 1 - qd
    q = {"home": rest * ph / side, "draw": qd, "away": rest * pa / side}
    s = sum(q.values())
    q = {k: v / s for k, v in q.items()}
    return q


class R43YDrawCalibrationComponent:
    component_id = "R43Y_draw_calibration"
    component_version = "r43gov0-m5f-y-v1"
    enabled = False
    native_input = "1x2_probabilities"
    native_output = "1x2_probabilities"
    score_matrix_lifting_migrated = False
    source_commit = SOURCE_COMMIT
    source_blob_sha = SOURCE_BLOB_SHA
    frozen_ledger_blob_sha = FROZEN_LEDGER_BLOB_SHA
    fixed_draw_logit_intercept = DRAW_LOGIT_INTERCEPT

    @staticmethod
    def apply(probabilities: Mapping[str, float]) -> dict[str, float]:
        return calibrate(probabilities)

    @classmethod
    def receipt(cls) -> dict[str, Any]:
        return {
            "component_id": cls.component_id,
            "component_version": cls.component_version,
            "enabled": cls.enabled,
            "native_input": cls.native_input,
            "native_output": cls.native_output,
            "score_matrix_lifting_migrated": cls.score_matrix_lifting_migrated,
            "source_commit": cls.source_commit,
            "source_blob_sha": cls.source_blob_sha,
            "frozen_ledger_blob_sha": cls.frozen_ledger_blob_sha,
            "source_run_id": SOURCE_RUN_ID,
            "source_branch": SOURCE_BRANCH,
            "development_source_n": SOURCE_U0_N,
            "development_mean_pred_draw": SOURCE_DRAW_MEAN,
            "development_actual_draw_rate": SOURCE_DRAW_RATE,
            "fixed_draw_logit_intercept": cls.fixed_draw_logit_intercept,
            "home_away_remaining_mass_ratio_preserved": True,
            "parameter_search": False,
            "threshold_search": False,
            "draw_override": False,
        }
