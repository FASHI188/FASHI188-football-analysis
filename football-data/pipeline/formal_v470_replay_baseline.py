"""Real V4.7 formal replay calculator behind UnifiedInferenceEngine.

This adapter is only for full-volume migration acceptance on the historical 18,464
OOF cohort. It calls the same frozen V4.7 numerical functions that produced that
cohort; it never accepts a precomputed matrix. It is not the operational runtime
baseline (S60 remains the unique operational baseline).
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, load_json, read_processed_matches
from pipeline.unified_inference import FixtureRequest, canonical_matrix


class FormalV470ReplayBaseline:
    component_id = "V470_formal_replay_numerical_baseline"
    component_version = "r43gov-runtime-v470-full-diff-v1"
    operational_runtime_baseline = False
    replay_acceptance_only = True

    def __init__(self):
        self._matches: dict[str, Any] = {}
        self._reports: dict[str, Mapping[str, Any]] = {}
        self._last_receipt: dict[str, Any] | None = None

    def _loaded(self, competition_id: str):
        if competition_id not in self._matches:
            self._matches[competition_id] = read_processed_matches(competition_id)
            self._reports[competition_id] = load_json(REPORT_ROOT / f"{competition_id}.json")
        return self._matches[competition_id], self._reports[competition_id]

    def predict(
        self,
        request: FixtureRequest,
        canonical_home_team_id: str,
        canonical_away_team_id: str,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        forbidden = {"score_matrix", "precomputed_matrix", "probabilities"} & set(payload)
        if forbidden:
            raise ValueError(f"V470 replay baseline forbids precomputed numerics: {sorted(forbidden)}")
        competition_id = str(payload.get("competition_id") or "").strip()
        season = str(payload.get("season") or "").strip()
        target_datetime = payload.get("target_datetime")
        if not competition_id or not season or target_datetime is None:
            raise ValueError("V470 replay requires competition_id, season and target_datetime")
        matches, report = self._loaded(competition_id)
        fold = _fold_for_season(report, season)
        params = fold.get("selected_parameters")
        if not isinstance(params, dict):
            raise PlatformError(f"invalid selected parameters {competition_id} {season}")
        matrix = _predict_from_loaded_matches(
            matches,
            canonical_home_team_id,
            canonical_away_team_id,
            target_datetime,
            season,
            params,
        )
        temperature, calibration_mode = _target_season_temperature(competition_id, season)
        if abs(float(temperature) - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, float(temperature))
        out = canonical_matrix(matrix)
        self._last_receipt = {
            "fixture_id": request.fixture_id,
            "competition_id": competition_id,
            "season": season,
            "target_datetime": str(target_datetime),
            "oof_temperature": float(temperature),
            "oof_calibration_mode": calibration_mode,
            "precomputed_matrix_accepted": False,
            "operational_runtime_baseline": False,
        }
        return out

    def numerical_receipt(self) -> Mapping[str, Any] | None:
        return dict(self._last_receipt) if self._last_receipt is not None else None
