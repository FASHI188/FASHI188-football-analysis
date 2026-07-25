#!/usr/bin/env python3
"""V6.24.2 research-only commensurate/regime state updater.

This replaces fixed half-life/Hedge adaptation with an explicit two-regime Bayesian
borrowing mechanism:

- stable regime: small drift variance -> strong borrowing from the prior team state;
- break regime: large drift variance -> weak borrowing after a structural change;
- the posterior stable/break probability is updated from the current PIT observation.

It is an analytic Gaussian two-component approximation to a spike-and-slab
commensurate dynamic state model. It does NOT claim to reproduce any external paper's
full sampler. No football parameter defaults are embedded: variances and prior regime
probabilities must be estimated/frozen outside this module from pre-match data only.

RESEARCH_ONLY, formal_weight=0, no workflow entrypoint and no automatic promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, log, pi
from typing import Mapping

FORMAL_WEIGHT = 0
CLASSIFICATION = "RESEARCH_ONLY_V6_24_2_COMMENSURATE_REGIME_STATE"


@dataclass(frozen=True)
class GaussianState:
    mean: float
    variance: float

    def validate(self) -> None:
        if not self.variance > 0.0:
            raise ValueError("state variance must be positive")


@dataclass(frozen=True)
class RegimeConfig:
    stable_drift_variance: float
    break_drift_variance: float
    prior_stable_probability: float

    def validate(self) -> None:
        if not self.stable_drift_variance > 0.0:
            raise ValueError("stable_drift_variance must be positive")
        if not self.break_drift_variance > self.stable_drift_variance:
            raise ValueError("break drift variance must exceed stable drift variance")
        if not 0.0 < self.prior_stable_probability < 1.0:
            raise ValueError("prior_stable_probability must be strictly between 0 and 1")


@dataclass(frozen=True)
class RegimePosterior:
    mixture_state: GaussianState
    stable_state: GaussianState
    break_state: GaussianState
    posterior_stable_probability: float
    posterior_break_probability: float


def _normal_logpdf(x: float, mean: float, variance: float) -> float:
    if variance <= 0.0:
        raise ValueError("variance must be positive")
    return -0.5 * (log(2.0 * pi * variance) + ((x - mean) ** 2) / variance)


def _component_update(
    previous: GaussianState,
    observation: GaussianState,
    drift_variance: float,
) -> tuple[GaussianState, float]:
    """One Gaussian dynamic component and its predictive log likelihood."""
    previous.validate()
    observation.validate()
    if drift_variance <= 0.0:
        raise ValueError("drift variance must be positive")

    prior_var = previous.variance + drift_variance
    obs_var = observation.variance
    precision = (1.0 / prior_var) + (1.0 / obs_var)
    post_var = 1.0 / precision
    post_mean = post_var * (
        (previous.mean / prior_var) + (observation.mean / obs_var)
    )
    pred_var = prior_var + obs_var
    pred_ll = _normal_logpdf(observation.mean, previous.mean, pred_var)
    return GaussianState(post_mean, post_var), pred_ll


def update_state(
    previous: GaussianState,
    observation: GaussianState,
    config: RegimeConfig,
) -> RegimePosterior:
    """Posterior mixture after observing one strictly pre-match state estimate.

    `observation` is not a match result; it is a PIT feature/state estimate available at
    the freeze time (for example attack strength, shot-rate state, shot-quality state,
    defensive allowance). The caller is responsible for the evidence timestamp.
    """
    config.validate()
    stable, ll_stable = _component_update(
        previous, observation, config.stable_drift_variance
    )
    brk, ll_break = _component_update(
        previous, observation, config.break_drift_variance
    )

    log_ws = log(config.prior_stable_probability) + ll_stable
    log_wb = log(1.0 - config.prior_stable_probability) + ll_break
    m = max(log_ws, log_wb)
    ws = exp(log_ws - m)
    wb = exp(log_wb - m)
    norm = ws + wb
    ps = ws / norm
    pb = wb / norm

    mix_mean = ps * stable.mean + pb * brk.mean
    # Exact variance of a two-Gaussian mixture.
    mix_var = (
        ps * (stable.variance + (stable.mean - mix_mean) ** 2)
        + pb * (brk.variance + (brk.mean - mix_mean) ** 2)
    )
    return RegimePosterior(
        mixture_state=GaussianState(mix_mean, mix_var),
        stable_state=stable,
        break_state=brk,
        posterior_stable_probability=ps,
        posterior_break_probability=pb,
    )


def update_named_states(
    previous: Mapping[str, GaussianState],
    observation: Mapping[str, GaussianState],
    configs: Mapping[str, RegimeConfig],
) -> dict[str, RegimePosterior]:
    """Update named team states independently with no hidden fallback.

    Every observed state must have an explicit previous state and explicit frozen regime
    config. Missing configuration fails closed instead of substituting a global value.
    """
    out: dict[str, RegimePosterior] = {}
    for key, obs in observation.items():
        if key not in previous:
            raise KeyError(f"missing previous state for {key}")
        if key not in configs:
            raise KeyError(f"missing regime config for {key}")
        out[key] = update_state(previous[key], obs, configs[key])
    return out
