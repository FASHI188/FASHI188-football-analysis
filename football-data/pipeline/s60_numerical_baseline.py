"""Real S60 numerical baseline for the unified Football3 inference path.

Per-fixture precomputed probabilities/matrices are forbidden. S60 replays the
frozen R9b strict-prior state, fits the fixed 24,123-row classifier head locally,
and calculates every prediction inside UnifiedInferenceEngine.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

import numpy as np

from components.outcome_mass_matrix_transport import lift_1x2_target
from pipeline.unified_inference import FixtureRequest, canonical_matrix

ROOT = Path(__file__).resolve().parents[1]
R9_SOURCE = ROOT / "experiments" / "top1_r9b_xg_hf" / "run_experiment_r9b.py"
R9_SOURCE_BLOB_SHA = "986e3bd10bd3f94b1ce7983964ea5dbc548ee50a"
HISTORY_ROWS = 60000
CLASSIFIER_TRAIN_ROWS = 24123
CLASSIFIER_C = 0.5
CLASSIFIER_RANDOM_STATE = 0
MAX_GOALS = 12


def _load_r9():
    name = "football3_s60_r9_source"
    spec = importlib.util.spec_from_file_location(name, R9_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen R9b numerical source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r9 = _load_r9()

FORBIDDEN_FIXTURE_PAYLOAD_KEYS = {
    "score_matrix", "score_matrix_hash", "source_model_blob_sha",
    "precomputed_probabilities", "probabilities", "p_home", "p_draw", "p_away",
    "top1", "actual_result", "home_goals_90", "away_goals_90", "home_goals",
    "away_goals", "home_xg", "away_xg",
}


@dataclass(frozen=True)
class S60FitReceipt:
    source_blob_sha: str
    history_rows: int
    classifier_train_rows: int
    first_history_date: str
    last_history_date: str
    first_classifier_date: str
    last_classifier_date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_blob_sha": self.source_blob_sha,
            "history_rows": self.history_rows,
            "classifier_train_rows": self.classifier_train_rows,
            "first_history_date": self.first_history_date,
            "last_history_date": self.last_history_date,
            "first_classifier_date": self.first_classifier_date,
            "last_classifier_date": self.last_classifier_date,
        }


def _clean_history(row: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "date", "game_id", "competition_id", "home_team", "away_team",
        "home_goals", "away_goals", "home_xg", "away_xg",
    }
    missing = required - set(row)
    if missing:
        raise ValueError(f"S60 history row missing fields: {sorted(missing)}")
    out = {
        "date": str(row["date"]),
        "game_id": str(row["game_id"]),
        "competition_id": str(row["competition_id"]),
        "home_team": str(row["home_team"]),
        "away_team": str(row["away_team"]),
        "home_goals": int(row["home_goals"]),
        "away_goals": int(row["away_goals"]),
        "home_xg": float(row["home_xg"]),
        "away_xg": float(row["away_xg"]),
    }
    if row.get("xg_known_at") is not None:
        out["xg_known_at"] = str(row["xg_known_at"])
    return out


def replay_history_date_safe(rows: Iterable[Mapping[str, Any]]):
    clean = sorted((_clean_history(row) for row in rows), key=lambda x: (x["date"], x["game_id"]))
    if len({x["game_id"] for x in clean}) != len(clean):
        raise ValueError("duplicate S60 history game_id")
    state = r9.S()
    predicted: list[dict[str, Any]] = []
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clean:
        by_date[row["date"]].append(row)
    for day in sorted(by_date):
        pending = []
        for row in sorted(by_date[day], key=lambda x: x["game_id"]):
            raw = state.pred(row)
            predicted.append({"date": day, "game_id": row["game_id"], "y": r9.actual(row), "raw": raw})
            pending.append((row, raw))
        for row, raw in pending:
            state.update(row, raw)
    return predicted, state


def _fit_classifier(train_rows: list[dict[str, Any]]):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if not train_rows:
        raise ValueError("S60 classifier training rows are empty")
    y = [int(row["y"]) for row in train_rows]
    if len(set(y)) < 3:
        raise ValueError("S60 classifier training requires all three outcomes")
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=CLASSIFIER_C, max_iter=3000, random_state=CLASSIFIER_RANDOM_STATE),
    )
    model.fit([r9.feat_k1(row["raw"]) for row in train_rows], y)
    return model


def _classifier_probabilities(model, raw: Mapping[str, Any]) -> dict[str, float]:
    predicted = model.predict_proba([r9.feat_k1(raw)])[0]
    values = np.zeros(3, dtype=float)
    for label, probability in zip(model[-1].classes_, predicted):
        values[int(label)] = float(probability)
    values = np.clip(values, 1e-12, None)
    values /= values.sum()
    return {"home": float(values[0]), "draw": float(values[1]), "away": float(values[2])}


def _poisson_matrix(mu_home: float, mu_away: float) -> list[dict[str, Any]]:
    mh, ma = float(mu_home), float(mu_away)
    if not math.isfinite(mh) or not math.isfinite(ma) or mh <= 0 or ma <= 0:
        raise ValueError("invalid S60 Poisson means")
    hp, ap = [math.exp(-mh)], [math.exp(-ma)]
    for k in range(1, MAX_GOALS + 1):
        hp.append(hp[-1] * mh / k)
        ap.append(ap[-1] * ma / k)
    cells = [
        {"home_goals": h, "away_goals": a, "probability": hp[h] * ap[a]}
        for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)
    ]
    total = sum(float(c["probability"]) for c in cells)
    return canonical_matrix([{**c, "probability": float(c["probability"]) / total} for c in cells])


class S60NumericalBaseline:
    component_id = "S60_stage_primary_numerical_baseline"
    component_version = "r43gov-runtime-s60-v1"
    native_output = "1x2_probabilities"
    score_matrix_adapter = "poisson_raw_means_then_outcome_mass_transport"
    source_blob_sha = R9_SOURCE_BLOB_SHA
    formal_scientific_promotion = False

    def __init__(self, state, model, fit_receipt: S60FitReceipt):
        self.state = state
        self.model = model
        self.fit_receipt = fit_receipt
        self._last_receipt: dict[str, Any] | None = None

    @classmethod
    def fit_from_history(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        expected_history_rows: int | None = HISTORY_ROWS,
        classifier_train_rows: int = CLASSIFIER_TRAIN_ROWS,
    ) -> "S60NumericalBaseline":
        clean = sorted((_clean_history(row) for row in rows), key=lambda x: (x["date"], x["game_id"]))
        if expected_history_rows is not None and len(clean) != int(expected_history_rows):
            raise ValueError(f"S60 history row count mismatch: {len(clean)} != {expected_history_rows}")
        predicted, state = replay_history_date_safe(clean)
        n = int(classifier_train_rows)
        if n <= 0 or len(predicted) < n:
            raise ValueError("insufficient S60 classifier training history")
        train = predicted[-n:]
        model = _fit_classifier(train)
        receipt = S60FitReceipt(
            R9_SOURCE_BLOB_SHA, len(clean), n, clean[0]["date"], clean[-1]["date"],
            train[0]["date"], train[-1]["date"],
        )
        return cls(state, model, receipt)

    def predict(
        self,
        request: FixtureRequest,
        canonical_home_team_id: str,
        canonical_away_team_id: str,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        forbidden = sorted(FORBIDDEN_FIXTURE_PAYLOAD_KEYS & set(payload))
        if forbidden:
            raise ValueError(f"S60 forbids precomputed/target payload fields: {forbidden}")
        competition_id = str(payload.get("competition_id") or "").strip()
        target_date = str(payload.get("target_date") or "").strip()
        if not competition_id or not target_date:
            raise ValueError("S60 requires competition_id and target_date")
        row = {
            "date": target_date,
            "game_id": str(request.fixture_id),
            "competition_id": competition_id,
            "home_team": str(canonical_home_team_id),
            "away_team": str(canonical_away_team_id),
        }
        raw = self.state.pred(row)
        target = _classifier_probabilities(self.model, raw)
        matrix = lift_1x2_target(_poisson_matrix(raw["mu_home"], raw["mu_away"]), target)
        self._last_receipt = {
            "fixture_id": request.fixture_id,
            "competition_id": competition_id,
            "target_date": target_date,
            "source_blob_sha": self.source_blob_sha,
            "fit": self.fit_receipt.to_dict(),
            "raw_mu_home": float(raw["mu_home"]),
            "raw_mu_away": float(raw["mu_away"]),
            "raw_xg_mu_home": float(raw["xg_mu_home"]),
            "raw_xg_mu_away": float(raw["xg_mu_away"]),
            "home_history": int(raw["home_history"]),
            "away_history": int(raw["away_history"]),
            "competition_history": int(raw["comp_history"]),
            "classifier_1x2": target,
            "score_matrix_adapter": self.score_matrix_adapter,
            "per_fixture_precomputed_numerics_accepted": False,
        }
        return matrix

    def numerical_receipt(self) -> Mapping[str, Any] | None:
        return dict(self._last_receipt) if self._last_receipt is not None else None
