#!/usr/bin/env python3
"""Research-only adaptive latent team-strength core for football3.

This module is deliberately data-source agnostic.  It does not read repository
match rows, labels, market prices, Provider data, secrets, model artifacts or
CURRENT.  Callers must supply point-in-time observations explicitly.

The state model is a diagonal Gaussian local-level filter with separate attack
and defence states per team.  Each scalar state evolves as a random walk.  The
process variance is adaptive: sustained standardized innovations increase the
future process variance, while ordinary innovations shrink back toward the
frozen base rate.  This gives fast adaptation after structural breaks without
making every team equally volatile.

This is an engineering/research challenger only.  It does not emit formal H/D/A
probabilities and does not alter V4.6, formal weights, config, CURRENT or runtime.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SECONDS_PER_DAY = 86400.0
EPS = 1e-12


class LatentStrengthError(ValueError):
    """Raised when a caller violates the frozen research contract."""


@dataclass(frozen=True)
class AdaptiveLatentConfig:
    initial_variance: float = 1.0
    base_process_variance_per_day: float = 0.0025
    min_process_variance_per_day: float = 0.0005
    max_process_variance_per_day: float = 0.05
    observation_variance: float = 0.35
    surprise_decay: float = 0.85
    surprise_gain: float = 1.75
    max_standardized_surprise: float = 16.0
    max_abs_state: float = 8.0

    def validate(self) -> "AdaptiveLatentConfig":
        fields = {
            "initial_variance": self.initial_variance,
            "base_process_variance_per_day": self.base_process_variance_per_day,
            "min_process_variance_per_day": self.min_process_variance_per_day,
            "max_process_variance_per_day": self.max_process_variance_per_day,
            "observation_variance": self.observation_variance,
            "surprise_decay": self.surprise_decay,
            "surprise_gain": self.surprise_gain,
            "max_standardized_surprise": self.max_standardized_surprise,
            "max_abs_state": self.max_abs_state,
        }
        for name, value in fields.items():
            if not math.isfinite(float(value)):
                raise LatentStrengthError(f"{name} must be finite")
        if self.initial_variance <= 0.0:
            raise LatentStrengthError("initial_variance must be > 0")
        if self.min_process_variance_per_day <= 0.0:
            raise LatentStrengthError("min_process_variance_per_day must be > 0")
        if not (
            self.min_process_variance_per_day
            <= self.base_process_variance_per_day
            <= self.max_process_variance_per_day
        ):
            raise LatentStrengthError("process variance bounds must contain base_process_variance_per_day")
        if self.observation_variance <= 0.0:
            raise LatentStrengthError("observation_variance must be > 0")
        if not 0.0 <= self.surprise_decay < 1.0:
            raise LatentStrengthError("surprise_decay must be in [0, 1)")
        if self.surprise_gain < 0.0:
            raise LatentStrengthError("surprise_gain must be >= 0")
        if self.max_standardized_surprise < 1.0:
            raise LatentStrengthError("max_standardized_surprise must be >= 1")
        if self.max_abs_state <= 0.0:
            raise LatentStrengthError("max_abs_state must be > 0")
        return self


@dataclass
class ScalarLatentState:
    mean: float
    variance: float
    process_variance_per_day: float
    surprise_ewma: float
    last_observed_at: datetime | None


@dataclass
class TeamLatentState:
    attack: ScalarLatentState
    defence: ScalarLatentState
    observations: int = 0


def _aware_timestamp(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise LatentStrengthError(f"{field} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise LatentStrengthError(f"{field} must include timezone")
    return value


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LatentStrengthError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise LatentStrengthError(f"{field} must be finite")
    return number


def _team_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise LatentStrengthError("team must be non-empty")
    return name


class AdaptiveLatentStrengthV1:
    """Online attack/defence latent-state estimator with adaptive volatility.

    The class does not decide how observations are constructed.  A later adapter
    may use strictly point-in-time score, xG or event-derived evidence, but this
    core only receives explicit scalar attack/defence observations.
    """

    def __init__(self, config: AdaptiveLatentConfig | None = None) -> None:
        self.config = (config or AdaptiveLatentConfig()).validate()
        self._teams: dict[str, TeamLatentState] = {}

    def _new_scalar(self) -> ScalarLatentState:
        return ScalarLatentState(
            mean=0.0,
            variance=self.config.initial_variance,
            process_variance_per_day=self.config.base_process_variance_per_day,
            surprise_ewma=1.0,
            last_observed_at=None,
        )

    def _ensure_team(self, team: str) -> TeamLatentState:
        key = _team_name(team)
        if key not in self._teams:
            self._teams[key] = TeamLatentState(self._new_scalar(), self._new_scalar())
        return self._teams[key]

    def _propagated(self, state: ScalarLatentState, at: datetime) -> ScalarLatentState:
        at = _aware_timestamp(at, "at")
        if state.last_observed_at is None:
            return ScalarLatentState(
                state.mean,
                state.variance,
                state.process_variance_per_day,
                state.surprise_ewma,
                at,
            )
        if at < state.last_observed_at:
            raise LatentStrengthError("timestamps must be monotone; refusing backward state propagation")
        elapsed_days = (at - state.last_observed_at).total_seconds() / SECONDS_PER_DAY
        variance = state.variance + state.process_variance_per_day * elapsed_days
        if not math.isfinite(variance) or variance <= 0.0:
            raise LatentStrengthError("propagated variance is invalid")
        return ScalarLatentState(
            state.mean,
            variance,
            state.process_variance_per_day,
            state.surprise_ewma,
            at,
        )

    def _updated_scalar(
        self,
        state: ScalarLatentState,
        observation: float,
        at: datetime,
        observation_variance: float,
    ) -> ScalarLatentState:
        observation = _finite_number(observation, "observation")
        observation_variance = _finite_number(observation_variance, "observation_variance")
        if observation_variance <= 0.0:
            raise LatentStrengthError("observation_variance must be > 0")
        prior = self._propagated(state, at)
        innovation = observation - prior.mean
        innovation_variance = prior.variance + observation_variance
        if innovation_variance <= 0.0 or not math.isfinite(innovation_variance):
            raise LatentStrengthError("innovation variance is invalid")
        kalman_gain = prior.variance / innovation_variance
        mean = prior.mean + kalman_gain * innovation
        mean = min(self.config.max_abs_state, max(-self.config.max_abs_state, mean))
        variance = (1.0 - kalman_gain) * prior.variance
        variance = max(EPS, variance)

        standardized_surprise = min(
            self.config.max_standardized_surprise,
            innovation * innovation / innovation_variance,
        )
        surprise_ewma = (
            self.config.surprise_decay * prior.surprise_ewma
            + (1.0 - self.config.surprise_decay) * standardized_surprise
        )
        excess_surprise = max(0.0, surprise_ewma - 1.0)
        process_variance = self.config.base_process_variance_per_day * (
            1.0 + self.config.surprise_gain * excess_surprise
        )
        process_variance = min(
            self.config.max_process_variance_per_day,
            max(self.config.min_process_variance_per_day, process_variance),
        )
        return ScalarLatentState(
            mean=mean,
            variance=variance,
            process_variance_per_day=process_variance,
            surprise_ewma=surprise_ewma,
            last_observed_at=prior.last_observed_at,
        )

    def update_team(
        self,
        team: str,
        *,
        attack_observation: float,
        defence_observation: float,
        observed_at: datetime,
        attack_observation_variance: float | None = None,
        defence_observation_variance: float | None = None,
    ) -> dict[str, Any]:
        """Update one team from one explicitly supplied PIT observation pair."""
        key = _team_name(team)
        observed_at = _aware_timestamp(observed_at, "observed_at")
        attack_value = _finite_number(attack_observation, "attack_observation")
        defence_value = _finite_number(defence_observation, "defence_observation")
        attack_var = (
            self.config.observation_variance
            if attack_observation_variance is None
            else _finite_number(attack_observation_variance, "attack_observation_variance")
        )
        defence_var = (
            self.config.observation_variance
            if defence_observation_variance is None
            else _finite_number(defence_observation_variance, "defence_observation_variance")
        )
        if attack_var <= 0.0 or defence_var <= 0.0:
            raise LatentStrengthError("observation variances must be > 0")
        current = self._teams.get(key)
        if current is None:
            current = TeamLatentState(self._new_scalar(), self._new_scalar())
        attack = self._updated_scalar(current.attack, attack_value, observed_at, attack_var)
        defence = self._updated_scalar(current.defence, defence_value, observed_at, defence_var)
        self._teams[key] = TeamLatentState(attack=attack, defence=defence, observations=current.observations + 1)
        return self.snapshot(key, at=observed_at)

    def snapshot(self, team: str, *, at: datetime) -> dict[str, Any]:
        """Return a non-mutating propagated snapshot at a prediction cutoff."""
        key = _team_name(team)
        at = _aware_timestamp(at, "at")
        stored = self._teams.get(key)
        if stored is None:
            stored = TeamLatentState(self._new_scalar(), self._new_scalar(), observations=0)
        attack = self._propagated(stored.attack, at)
        defence = self._propagated(stored.defence, at)
        return {
            "team": key,
            "at": at.isoformat(),
            "observations": stored.observations,
            "attack": {
                "mean": attack.mean,
                "variance": attack.variance,
                "sd": math.sqrt(attack.variance),
                "process_variance_per_day": attack.process_variance_per_day,
                "surprise_ewma": attack.surprise_ewma,
            },
            "defence": {
                "mean": defence.mean,
                "variance": defence.variance,
                "sd": math.sqrt(defence.variance),
                "process_variance_per_day": defence.process_variance_per_day,
                "surprise_ewma": defence.surprise_ewma,
            },
        }

    def compare(self, home_team: str, away_team: str, *, at: datetime) -> dict[str, Any]:
        """Return independent latent-strength direction and uncertainty, not H/D/A probabilities."""
        home_name = _team_name(home_team)
        away_name = _team_name(away_team)
        if home_name == away_name:
            raise LatentStrengthError("home_team and away_team must be distinct")
        home = self.snapshot(home_name, at=at)
        away = self.snapshot(away_name, at=at)
        home_signal = float(home["attack"]["mean"]) - float(away["defence"]["mean"])
        away_signal = float(away["attack"]["mean"]) - float(home["defence"]["mean"])
        margin = home_signal - away_signal
        variance = (
            float(home["attack"]["variance"])
            + float(away["defence"]["variance"])
            + float(away["attack"]["variance"])
            + float(home["defence"]["variance"])
        )
        if variance <= 0.0 or not math.isfinite(variance):
            raise LatentStrengthError("comparison variance is invalid")
        sd = math.sqrt(variance)
        z = margin / sd
        return {
            "at": _aware_timestamp(at, "at").isoformat(),
            "home_team": home_name,
            "away_team": away_name,
            "home_goal_log_strength": home_signal,
            "away_goal_log_strength": away_signal,
            "latent_margin": margin,
            "latent_margin_variance": variance,
            "latent_margin_sd": sd,
            "standardized_margin": z,
            "absolute_standardized_margin": abs(z),
            "interpretation": "research_only_latent_direction_not_1x2_probability",
        }

    def export_state(self) -> dict[str, Any]:
        """Deterministic in-memory state export for audit/tests; no filesystem writes."""
        teams = {}
        for name in sorted(self._teams):
            state = self._teams[name]
            teams[name] = {
                "observations": state.observations,
                "attack": self._export_scalar(state.attack),
                "defence": self._export_scalar(state.defence),
            }
        return {
            "schema": "football3_adaptive_latent_strength_v1",
            "research_only": True,
            "formal_weight": 0.0,
            "teams": teams,
        }

    @staticmethod
    def _export_scalar(state: ScalarLatentState) -> dict[str, Any]:
        return {
            "mean": state.mean,
            "variance": state.variance,
            "process_variance_per_day": state.process_variance_per_day,
            "surprise_ewma": state.surprise_ewma,
            "last_observed_at": state.last_observed_at.isoformat() if state.last_observed_at else None,
        }
