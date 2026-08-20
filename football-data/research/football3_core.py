from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

PT_CLASSES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7)
PT_CLASS_NAMES: tuple[str, ...] = ("0", "1", "2", "3", "4", "5", "6", "7+")
OU_HALF_GOAL_TO_TAIL_K: Mapping[float, int] = {0.5: 1, 1.5: 2, 2.5: 3, 3.5: 4, 4.5: 5}
MASTER_PREDICTION_CUTOFF = "T-15m"
DEFAULT_EPS = 1e-12


class Football3ContractError(RuntimeError):
    """Fail-closed contract violation."""


def _as_float_array(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if not np.all(np.isfinite(a)):
        raise Football3ContractError("non-finite numeric value")
    return a


def clip_prob(x, eps: float = DEFAULT_EPS) -> np.ndarray:
    a = _as_float_array(x)
    if not (0 < eps < 0.5):
        raise Football3ContractError("invalid eps")
    return np.clip(a, eps, 1.0 - eps)


def logit(x, eps: float = DEFAULT_EPS) -> np.ndarray:
    p = clip_prob(x, eps)
    return np.log(p / (1.0 - p))


def inv_logit(x) -> np.ndarray:
    a = _as_float_array(x)
    out = np.empty_like(a, dtype=float)
    pos = a >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-a[pos]))
    z = np.exp(a[~pos])
    out[~pos] = z / (1.0 + z)
    return out


def devig_two_way(over_odds, under_odds) -> np.ndarray:
    o = _as_float_array(over_odds)
    u = _as_float_array(under_odds)
    if np.any(o <= 1.0) or np.any(u <= 1.0):
        raise Football3ContractError("decimal odds must be > 1.0")
    io, iu = 1.0 / o, 1.0 / u
    return io / (io + iu)


def ou_tail_k(line: float) -> int:
    try:
        return OU_HALF_GOAL_TO_TAIL_K[float(line)]
    except KeyError as e:
        raise Football3ContractError(f"unsupported half-goal line: {line}") from e


def validate_nested_ou_tails(lines: Sequence[float], probs: Sequence[float], tol: float = 1e-10) -> None:
    if len(lines) != len(probs) or not lines:
        raise Football3ContractError("line/probability length mismatch")
    pairs = sorted((float(l), float(p)) for l, p in zip(lines, probs))
    expected = [ou_tail_k(l) for l, _ in pairs]
    if expected != sorted(expected):
        raise Football3ContractError("O/U tail mapping is not increasing")
    ps = [p for _, p in pairs]
    if any((not math.isfinite(p)) or p <= 0 or p >= 1 for p in ps):
        raise Football3ContractError("O/U fair probabilities must lie strictly in (0,1)")
    for a, b in zip(ps, ps[1:]):
        if b > a + tol:
            raise Football3ContractError("nested O/U tail probabilities violated")


def collapse_total_goals(total_goals: Iterable[int]) -> np.ndarray:
    y = np.asarray(list(total_goals), dtype=int)
    if y.ndim != 1 or np.any(y < 0):
        raise Football3ContractError("total-goal targets must be nonnegative integers")
    return np.minimum(y, 7)


def validate_target(y, n_classes: int = 8) -> np.ndarray:
    arr = np.asarray(y)
    if arr.ndim != 1:
        raise Football3ContractError("target must be one-dimensional")
    if arr.dtype.kind not in "iu":
        f = _as_float_array(arr)
        if not np.all(np.equal(f, np.floor(f))):
            raise Football3ContractError("target contains non-integer values")
        arr = f.astype(int)
    else:
        arr = arr.astype(int, copy=False)
    if np.any(arr < 0) or np.any(arr >= n_classes):
        raise Football3ContractError(f"target outside 0..{n_classes-1}")
    return arr


def validate_probability_matrix(p, n_classes: int = 8, atol: float = 1e-10) -> np.ndarray:
    a = _as_float_array(p)
    if a.ndim != 2 or a.shape[1] != n_classes:
        raise Football3ContractError(f"probability matrix must have shape (n,{n_classes})")
    if np.any(a < -atol) or np.any(a > 1.0 + atol):
        raise Football3ContractError("probability outside [0,1]")
    sums = a.sum(axis=1)
    if not np.allclose(sums, 1.0, atol=atol, rtol=0):
        raise Football3ContractError(f"probability rows do not sum to one; max residual={np.max(np.abs(sums-1))}")
    a = np.clip(a, 0.0, 1.0)
    a /= a.sum(axis=1, keepdims=True)
    return a


def per_match_logloss(p, y, eps: float = DEFAULT_EPS) -> np.ndarray:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    return -np.log(np.clip(probs[np.arange(len(target)), target], eps, 1.0))


def multiclass_logloss(p, y) -> float:
    return float(per_match_logloss(p, y).mean())


def multiclass_brier(p, y) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    onehot = np.eye(probs.shape[1], dtype=float)[target]
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def normalized_rps(p, y) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    k = probs.shape[1]
    cdf_p = np.cumsum(probs[:, :-1], axis=1)
    onehot = np.eye(k, dtype=float)[target]
    cdf_y = np.cumsum(onehot[:, :-1], axis=1)
    return float(np.mean(np.sum((cdf_p - cdf_y) ** 2, axis=1) / (k - 1)))


def _binary_ece(prob: np.ndarray, outcome: np.ndarray, n_bins: int) -> float:
    if not isinstance(n_bins, int) or n_bins < 2:
        raise Football3ContractError("calibration n_bins must be an integer >=2")
    p = _as_float_array(prob)
    y = _as_float_array(outcome)
    if p.ndim != 1 or y.ndim != 1 or len(p) != len(y) or len(p) == 0:
        raise Football3ContractError("invalid calibration vectors")
    if np.any((p < 0) | (p > 1)) or np.any((y < 0) | (y > 1)):
        raise Football3ContractError("calibration vectors outside [0,1]")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = np.minimum(np.searchsorted(edges, p, side="right") - 1, n_bins - 1)
    bins = np.maximum(bins, 0)
    total = len(p)
    ece = 0.0
    for b in range(n_bins):
        ix = bins == b
        if not np.any(ix):
            continue
        ece += float(np.sum(ix) / total) * abs(float(np.mean(p[ix])) - float(np.mean(y[ix])))
    return float(ece)


def top1_ece(p, y, n_bins: int = 10) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    pred = np.argmax(probs, axis=1)
    conf = probs[np.arange(len(probs)), pred]
    correct = (pred == target).astype(float)
    return _binary_ece(conf, correct, n_bins)


def classwise_ece(p, y, n_bins: int = 10) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    vals = []
    for k in range(probs.shape[1]):
        vals.append(_binary_ece(probs[:, k], (target == k).astype(float), n_bins))
    return float(np.mean(vals))


def topk_accuracy(p, y, k: int) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if not (1 <= k <= probs.shape[1]):
        raise Football3ContractError("invalid top-k")
    idx = np.argpartition(-probs, kth=k - 1, axis=1)[:, :k]
    return float(np.mean(np.any(idx == target[:, None], axis=1)))


def score_bundle(p, y, *, calibration_bins: int = 10) -> dict[str, float]:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    return {
        "LogLoss": multiclass_logloss(probs, target),
        "Brier": multiclass_brier(probs, target),
        "RPS": normalized_rps(probs, target),
        "Top1ECE": top1_ece(probs, target, calibration_bins),
        "ClasswiseECE": classwise_ece(probs, target, calibration_bins),
        "Top1": topk_accuracy(probs, target, 1),
        "Top3": topk_accuracy(probs, target, min(3, probs.shape[1])),
        "probability_residual_max": float(np.max(np.abs(probs.sum(axis=1) - 1.0))),
    }


def paired_bootstrap_delta_logloss(baseline_p, candidate_p, y, *, n_resamples: int, seed: int, ci: float = 0.90) -> dict[str, float]:
    if n_resamples < 100:
        raise Football3ContractError("bootstrap resamples too small")
    if not (0.5 < ci < 1.0):
        raise Football3ContractError("invalid bootstrap CI")
    b = per_match_logloss(baseline_p, y)
    c = per_match_logloss(candidate_p, y)
    if b.shape != c.shape:
        raise Football3ContractError("paired bootstrap requires exact row pairing")
    d = c - b
    rng = np.random.default_rng(seed)
    n = len(d)
    means = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        ix = rng.integers(0, n, size=n)
        means[i] = float(np.mean(d[ix]))
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(means, [alpha, 1.0 - alpha])
    return {"delta": float(np.mean(d)), "ci_low": float(lo), "ci_high": float(hi), "p_delta_lt_0": float(np.mean(means < 0.0)), "n": int(n), "n_resamples": int(n_resamples), "seed": int(seed), "paired": True}


def required_paired_n_from_observed_delta(per_match_delta: Sequence[float], *, alpha: float = 0.10, power: float = 0.80, conservative_multiplier: float = 1.25) -> int:
    d = _as_float_array(per_match_delta)
    if d.ndim != 1 or len(d) < 30:
        raise Football3ContractError("power planning requires >=30 development paired deltas")
    effect = abs(float(np.mean(d)))
    sd = float(np.std(d, ddof=1))
    if effect <= 0 or sd <= 0:
        raise Football3ContractError("nonpositive effect/variance for planning")
    z_alpha = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    z_power = NormalDist().inv_cdf(power)
    n = ((z_alpha + z_power) * sd / effect) ** 2
    return int(math.ceil(n * conservative_multiplier))


def canonical_identity_string(row: Mapping[str, object]) -> str:
    fields = ("sourceCode", "id", "matchDate", "Country", "League", "Season", "homeTeam", "awayTeam")
    missing = [f for f in fields if f not in row or str(row[f]).strip() == ""]
    if missing:
        raise Football3ContractError(f"identity missing fields: {missing}")
    return "|".join(str(row[f]).strip() for f in fields)


def identity_sha256(row: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_identity_string(row).encode("utf-8")).hexdigest()


def ordered_identity_sha256(ids: Sequence[str]) -> str:
    if len(ids) != len(set(ids)):
        raise Football3ContractError("duplicate identity hash")
    raw = "\n".join(str(x) for x in ids) + ("\n" if ids else "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assert_disjoint_identity_sets(named_sets: Mapping[str, Iterable[str]]) -> None:
    materialized = {k: set(v) for k, v in named_sets.items()}
    names = list(materialized)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            overlap = materialized[a] & materialized[b]
            if overlap:
                raise Football3ContractError(f"identity overlap {a} vs {b}: {len(overlap)}")


def assert_exact_one_to_one_join(left: pd.DataFrame, right: pd.DataFrame, *, keys: Sequence[str], expected_rows: int | None = None) -> pd.DataFrame:
    for side, frame in (("left", left), ("right", right)):
        missing = [k for k in keys if k not in frame.columns]
        if missing:
            raise Football3ContractError(f"{side} missing join keys: {missing}")
        if frame.duplicated(list(keys)).any():
            raise Football3ContractError(f"{side} has duplicate join keys")
    out = left.merge(right, on=list(keys), how="inner", validate="one_to_one")
    wanted = len(left) if expected_rows is None else expected_rows
    if len(out) != wanted or len(left) != wanted:
        raise Football3ContractError(f"exact join coverage failed: left={len(left)} joined={len(out)} expected={wanted}")
    return out


def parse_utc_or_naive_timestamp(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    if pd.isna(t):
        raise Football3ContractError("invalid timestamp")
    return t


def assert_temporal_oos(train_dates, test_dates) -> None:
    tr = pd.to_datetime(pd.Series(train_dates), errors="raise", utc=True)
    te = pd.to_datetime(pd.Series(test_dates), errors="raise", utc=True)
    if tr.empty or te.empty:
        raise Football3ContractError("empty train/test dates")
    if tr.isna().any() or te.isna().any():
        raise Football3ContractError("missing train/test timestamp")
    if tr.max() >= te.min():
        raise Football3ContractError(f"temporal OOS violated: train_max={tr.max()} test_min={te.min()}")


def assert_feature_pit(frame: pd.DataFrame, *, cutoff_col: str, feature_timestamp_cols: Sequence[str]) -> None:
    if cutoff_col not in frame.columns:
        raise Football3ContractError(f"missing cutoff column {cutoff_col}")
    cutoff = pd.to_datetime(frame[cutoff_col], errors="coerce", utc=True)
    if cutoff.isna().any():
        raise Football3ContractError(f"missing/invalid cutoff timestamp: {int(cutoff.isna().sum())} rows")
    for col in feature_timestamp_cols:
        if col not in frame.columns:
            raise Football3ContractError(f"missing feature timestamp column {col}")
        ts = pd.to_datetime(frame[col], errors="coerce", utc=True)
        if ts.isna().any():
            raise Football3ContractError(f"missing/invalid feature timestamp in {col}: {int(ts.isna().sum())} rows")
        bad = ts > cutoff
        if bad.any():
            raise Football3ContractError(f"PIT violation in {col}: {int(bad.sum())} rows")


def _norm_cutoff(s: str) -> str:
    return "".join(str(s).lower().split())


def assert_same_prediction_cutoff(baseline_cutoff: str, candidate_cutoff: str) -> None:
    if _norm_cutoff(baseline_cutoff) != _norm_cutoff(candidate_cutoff):
        raise Football3ContractError(f"baseline/candidate prediction cutoffs differ: {baseline_cutoff!r} vs {candidate_cutoff!r}")


def assert_master_prediction_cutoff(*cutoffs: str, master: str = MASTER_PREDICTION_CUTOFF) -> None:
    expected = _norm_cutoff(master)
    bad = [c for c in cutoffs if _norm_cutoff(c) != expected]
    if bad:
        raise Football3ContractError(f"football3 master prediction cutoff is {master}; got {bad}")


@dataclass(frozen=True)
class SealedPool:
    name: str
    status: str = "SEALED"


def assert_sealed_boundaries(access_counts: Mapping[str, int], sealed: Sequence[SealedPool]) -> None:
    for p in sealed:
        count = int(access_counts.get(p.name, 0))
        if count != 0:
            raise Football3ContractError(f"sealed pool accessed: {p.name} count={count}")
