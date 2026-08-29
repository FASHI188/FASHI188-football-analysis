"""Exact state-core migration of R43T0 dynamic bivariate residual state.

Source branch: football3/r43t0-dynamic-bivariate-residual-state
Source blob: f6db4f0e6c0f544c058b15a7279731f55c5f6570

R43T is stateful. Predictions sharing one kickoff group MUST all use the same
pre-update state; outcomes update the state only after every prediction in that
group is frozen. This module migrates that lifecycle exactly and is disabled from
formal inference until a later explicit governance gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any

import numpy as np


SOURCE_BLOB_SHA = "f6db4f0e6c0f544c058b15a7279731f55c5f6570"
STATE_AR = 0.90
PROCESS_VAR = 0.04
INITIAL_VAR = 0.25
OBS_NOISE_FLOOR = 0.20
STATE_APPLY_SHRINK = 0.50
MAX_STATE_ABS = 1.50


@dataclass(frozen=True)
class R43TProjection:
    lambda_home: float
    lambda_away: float
    state_total_pred: float
    state_diff_pred: float


class R43TDynamicBivariateState:
    component_id = "R43T_dynamic_bivariate_residual_state"
    component_version = "r43gov0-m5b-t-state-v1"
    enabled = False

    def __init__(self) -> None:
        self.x = np.zeros(2, dtype=float)
        self.P = np.eye(2, dtype=float) * INITIAL_VAR
        self._group_open = False
        self._x_pred: np.ndarray | None = None
        self._P_pred: np.ndarray | None = None
        self._projection_count = 0

    @staticmethod
    def observation_cov(lh: float, la: float) -> np.ndarray:
        t = max(float(lh) + float(la), 0.05)
        d = float(lh) - float(la)
        r = np.array([[t, d], [d, t]], dtype=float)
        r += np.eye(2) * OBS_NOISE_FLOOR
        return r

    @staticmethod
    def project_lambdas(lh: float, la: float, x: np.ndarray) -> tuple[float, float]:
        total = float(lh) + float(la) + STATE_APPLY_SHRINK * float(x[0])
        diff = float(lh) - float(la) + STATE_APPLY_SHRINK * float(x[1])
        total = max(0.20, total)
        diff = float(np.clip(diff, -total + 0.10, total - 0.10))
        return max(0.05, (total + diff) / 2.0), max(0.05, (total - diff) / 2.0)

    @classmethod
    def simultaneous_update(
        cls,
        x_pred: np.ndarray,
        p_pred: np.ndarray,
        group: Iterable[Mapping[str, Any]],
    ) -> tuple[np.ndarray, np.ndarray]:
        pinv = np.linalg.inv(p_pred)
        info = pinv @ x_pred
        precision = pinv.copy()
        for row in group:
            lh = float(row["lambda_home"])
            la = float(row["lambda_away"])
            z = np.array(
                [
                    (int(row["hg"]) + int(row["ag"])) - (lh + la),
                    (int(row["hg"]) - int(row["ag"])) - (lh - la),
                ],
                dtype=float,
            )
            r = cls.observation_cov(lh, la)
            rinv = np.linalg.inv(r)
            precision += rinv
            info += rinv @ z
        p_post = np.linalg.inv(precision)
        x_post = p_post @ info
        x_post = np.clip(x_post, -MAX_STATE_ABS, MAX_STATE_ABS)
        return x_post, p_post

    def begin_group(self) -> None:
        if self._group_open:
            raise RuntimeError("R43T kickoff group already open")
        self._x_pred = STATE_AR * self.x
        self._P_pred = (STATE_AR ** 2) * self.P + np.eye(2) * PROCESS_VAR
        self._projection_count = 0
        self._group_open = True

    def project(self, lambda_home: float, lambda_away: float) -> R43TProjection:
        if not self._group_open or self._x_pred is None:
            raise RuntimeError("R43T begin_group must be called before project")
        dh, da = self.project_lambdas(float(lambda_home), float(lambda_away), self._x_pred)
        self._projection_count += 1
        return R43TProjection(
            dh,
            da,
            float(self._x_pred[0]),
            float(self._x_pred[1]),
        )

    def settle_group(self, observations: Iterable[Mapping[str, Any]]) -> None:
        if not self._group_open or self._x_pred is None or self._P_pred is None:
            raise RuntimeError("R43T no open kickoff group to settle")
        rows = list(observations)
        if len(rows) != self._projection_count:
            raise RuntimeError(
                f"R43T settle count {len(rows)} does not match frozen prediction count {self._projection_count}"
            )
        self.x, self.P = self.simultaneous_update(self._x_pred, self._P_pred, rows)
        self._group_open = False
        self._x_pred = None
        self._P_pred = None
        self._projection_count = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "x": [float(v) for v in self.x],
            "P": [[float(v) for v in row] for row in self.P],
            "group_open": self._group_open,
            "projection_count": self._projection_count,
            "source_blob_sha": SOURCE_BLOB_SHA,
            "enabled": self.enabled,
        }
