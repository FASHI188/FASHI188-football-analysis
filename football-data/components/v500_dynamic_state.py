"""Real V500 Bayesian dynamic-state calculator behind the unified matrix chain.

The legacy V500 source is suspended because its old season loop allowed later
same-calendar-date fixtures to see earlier same-date settlements. This migration
keeps the source numerical primitives but changes lifecycle only: every prediction
is calculated from a copy of the pre-group state, and settlements are applied only
after the full kickoff group has been frozen.
"""
from __future__ import annotations

import copy
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from pipeline.unified_inference import FixtureRequest, canonical_matrix

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "validation" / "bayesian_dynamic_state_oof_v500.py"
SOURCE_BLOB_SHA = "9c302506c49aa1847e60cd7896fc1a80f3b6b457"
INVALIDATION_STATUS = "INVALIDATED_PENDING_SAME_DAY_SAFE_REPLAY"


def _load_source():
    name = "football3_v500_frozen_source"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen V500 numerical source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v500 = _load_source()
PROFILES = {str(item["id"]): dict(item) for item in v500.PROFILES}


class V500BayesianDynamicStateComponent:
    component_id = "V500_bayesian_dynamic_state"
    component_version = "r43gov-runtime-v500-sameday-safe-v1"
    source_blob_sha = SOURCE_BLOB_SHA
    source_status = INVALIDATION_STATUS
    research_only = True
    formal_weight = 0
    enabled = False

    def __init__(self, *, profile_id: str = "medium_balanced", enabled: bool = False,
                 prior_home_rate: float = 1.45, prior_away_rate: float = 1.20):
        if profile_id not in PROFILES:
            raise ValueError(f"unknown V500 profile_id: {profile_id}")
        self.profile_id = str(profile_id)
        self.profile = dict(PROFILES[self.profile_id])
        self.enabled = bool(enabled)
        prior_matches = float(self.profile["league_prior_matches"])
        self.states: dict[str, Any] = {}
        self._league = {
            "home_alpha": float(prior_home_rate) * prior_matches,
            "home_beta": prior_matches,
            "away_alpha": float(prior_away_rate) * prior_matches,
            "away_beta": prior_matches,
        }
        self._group_open = False
        self._last_receipt: dict[str, Any] | None = None

    def _rates(self) -> tuple[float, float]:
        return (self._league["home_alpha"] / self._league["home_beta"],
                self._league["away_alpha"] / self._league["away_beta"])

    def begin_group(self, group_key: str) -> None:
        if self._group_open:
            raise RuntimeError("V500 prediction group already open")
        self._group_open = True

    def apply(self, matrix: list[dict[str, Any]], request: FixtureRequest,
              payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled:
            return canonical_matrix(matrix)
        forbidden = {"v500_score_matrix", "v500_precomputed_matrix", "v500_probabilities"} & set(payload)
        if forbidden:
            raise ValueError(f"V500 forbids precomputed candidate numerics: {sorted(forbidden)}")
        if not self._group_open:
            raise RuntimeError("V500 enabled prediction requires begin_group lifecycle")
        home = str(payload.get("canonical_home_team_id") or "")
        away = str(payload.get("canonical_away_team_id") or "")
        if not home or not away:
            raise ValueError("V500 requires canonical team ids in component payload")
        prediction_at = payload.get("prediction_datetime")
        if isinstance(prediction_at, str):
            prediction_at = datetime.fromisoformat(prediction_at.replace("Z", "+00:00"))
        if not isinstance(prediction_at, datetime) or prediction_at.tzinfo is None:
            raise ValueError("V500 requires timezone-aware prediction_datetime")
        league_home, league_away = self._rates()
        snapshot = copy.deepcopy(self.states)
        dyn_home, dyn_away, state_audit = v500._dynamic_rates(
            snapshot, home, away, prediction_at, league_home, league_away, self.profile
        )
        candidate, tilt_audit = v500._candidate_from_baseline(
            canonical_matrix(matrix), dyn_home, dyn_away, self.profile
        )
        self._last_receipt = {
            "fixture_id": request.fixture_id,
            "source_blob_sha": self.source_blob_sha,
            "source_status": self.source_status,
            "profile_id": self.profile_id,
            "same_group_state_snapshot": True,
            "league_home_rate": league_home,
            "league_away_rate": league_away,
            "dynamic_home_rate": dyn_home,
            "dynamic_away_rate": dyn_away,
            "state_audit": state_audit,
            "tilt_audit": tilt_audit,
            "precomputed_v500_output_accepted": False,
        }
        return canonical_matrix(candidate)

    def settlement_observation(self, request: FixtureRequest, payload: Mapping[str, Any],
                               outcome, prediction_result) -> Mapping[str, Any]:
        if not self.enabled:
            return {}
        if outcome is None:
            raise RuntimeError("V500 settlement requires outcome")
        dt = payload.get("prediction_datetime")
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if not isinstance(dt, datetime) or dt.tzinfo is None:
            raise ValueError("V500 settlement requires prediction_datetime")
        return {
            "fixture_id": request.fixture_id,
            "home": str(prediction_result.canonical_home_team_id),
            "away": str(prediction_result.canonical_away_team_id),
            "date": dt,
            "home_goals": int(outcome.home_goals),
            "away_goals": int(outcome.away_goals),
        }

    def settle_group(self, observations: Iterable[Mapping[str, Any]]) -> None:
        if not self.enabled:
            self._group_open = False
            return
        if not self._group_open:
            raise RuntimeError("V500 settlement without open group")
        obs = sorted((dict(item) for item in observations), key=lambda x: str(x.get("fixture_id")))
        league_home, league_away = self._rates()
        for item in obs:
            if item:
                v500._update_states(self.states, item["home"], item["away"], item["date"],
                                    int(item["home_goals"]), int(item["away_goals"]),
                                    league_home, league_away, self.profile)
        for item in obs:
            if item:
                self._league["home_alpha"] += int(item["home_goals"])
                self._league["home_beta"] += 1.0
                self._league["away_alpha"] += int(item["away_goals"])
                self._league["away_beta"] += 1.0
        self._group_open = False

    def numerical_receipt(self) -> Mapping[str, Any] | None:
        return dict(self._last_receipt) if self._last_receipt is not None else None
