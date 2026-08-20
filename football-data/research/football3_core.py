from __future__ import annotations

import hashlib
import math
import re
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
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Football3ContractError(RuntimeError):
    """Fail-closed contract violation."""


def _as_float_array(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if not np.all(np.isfinite(a)):
        raise Football3ContractError("non-finite numeric value")
    return a


def _require_same_shape(a: np.ndarray, b: np.ndarray, what: str) -> None:
    if a.shape != b.shape:
        raise Football3ContractError(f"{what} shape mismatch: {a.shape} vs {b.shape}")


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
    _require_same_shape(o, u, "Over/Under odds")
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
    numeric_lines = [float(x) for x in lines]
    if len(set(numeric_lines)) != len(numeric_lines):
        raise Football3ContractError("duplicate O/U line")
    pairs = sorted((l, float(p)) for l, p in zip(numeric_lines, probs))
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
    raw = _as_float_array(list(total_goals))
    if raw.ndim != 1:
        raise Football3ContractError("total-goal targets must be one-dimensional")
    if np.any(raw < 0) or not np.all(raw == np.floor(raw)):
        raise Football3ContractError("total-goal targets must be nonnegative integers")
    return np.minimum(raw.astype(int), 7)


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
    if a.shape[0] == 0:
        raise Football3ContractError("probability matrix must contain at least one row")
    if np.any(a < -atol) or np.any(a > 1.0 + atol):
        raise Football3ContractError("probability outside [0,1]")
    sums = a.sum(axis=1)
    if not np.allclose(sums, 1.0, atol=atol, rtol=0):
        raise Football3ContractError(f"probability rows do not sum to one; max residual={np.max(np.abs(sums-1))}")
    a = np.clip(a, 0.0, 1.0)
    a /= a.sum(axis=1, keepdims=True)
    return a


def _validate_scoring_inputs(p, y) -> tuple[np.ndarray, np.ndarray]:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    return probs, target


def per_match_logloss(p, y, eps: float = DEFAULT_EPS) -> np.ndarray:
    probs, target = _validate_scoring_inputs(p, y)
    return -np.log(np.clip(probs[np.arange(len(target)), target], eps, 1.0))


def per_match_brier(p, y) -> np.ndarray:
    probs, target = _validate_scoring_inputs(p, y)
    onehot = np.eye(probs.shape[1], dtype=float)[target]
    return np.sum((probs - onehot) ** 2, axis=1)


def per_match_rps(p, y) -> np.ndarray:
    probs, target = _validate_scoring_inputs(p, y)
    k = probs.shape[1]
    cdf_p = np.cumsum(probs[:, :-1], axis=1)
    onehot = np.eye(k, dtype=float)[target]
    cdf_y = np.cumsum(onehot[:, :-1], axis=1)
    return np.sum((cdf_p - cdf_y) ** 2, axis=1) / (k - 1)


def multiclass_logloss(p, y) -> float:
    return float(per_match_logloss(p, y).mean())


def multiclass_brier(p, y) -> float:
    return float(per_match_brier(p, y).mean())


def normalized_rps(p, y) -> float:
    return float(per_match_rps(p, y).mean())


def _binary_ece(prob: np.ndarray, outcome: np.ndarray, n_bins: int) -> float:
    if isinstance(n_bins, bool) or not isinstance(n_bins, int) or n_bins < 2:
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
    probs, target = _validate_scoring_inputs(p, y)
    pred = np.argmax(probs, axis=1)
    conf = probs[np.arange(len(probs)), pred]
    correct = (pred == target).astype(float)
    return _binary_ece(conf, correct, n_bins)


def classwise_ece(p, y, n_bins: int = 10) -> float:
    probs, target = _validate_scoring_inputs(p, y)
    vals = [_binary_ece(probs[:, k], (target == k).astype(float), n_bins) for k in range(probs.shape[1])]
    return float(np.mean(vals))


def topk_accuracy(p, y, k: int) -> float:
    probs, target = _validate_scoring_inputs(p, y)
    if isinstance(k, bool) or not isinstance(k, int) or not (1 <= k <= probs.shape[1]):
        raise Football3ContractError("invalid top-k")
    idx = np.argpartition(-probs, kth=k - 1, axis=1)[:, :k]
    return float(np.mean(np.any(idx == target[:, None], axis=1)))


def score_bundle(p, y, *, calibration_bins: int = 10) -> dict[str, float]:
    probs, target = _validate_scoring_inputs(p, y)
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


def _validate_bootstrap_args(n_resamples: int, seed: int, ci: float) -> None:
    if isinstance(n_resamples, bool) or not isinstance(n_resamples, int) or n_resamples < 100:
        raise Football3ContractError("bootstrap resamples must be integer >=100")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise Football3ContractError("bootstrap seed must be integer")
    if not isinstance(ci, (int, float)) or isinstance(ci, bool) or not math.isfinite(float(ci)) or not (0.5 < float(ci) < 1.0):
        raise Football3ContractError("invalid bootstrap CI")


def paired_bootstrap_proper_score_deltas(
    baseline_p,
    candidate_p,
    y,
    *,
    n_resamples: int,
    seed: int,
    ci: float = 0.90,
) -> dict[str, dict[str, float]]:
    _validate_bootstrap_args(n_resamples, seed, ci)
    b_probs, target = _validate_scoring_inputs(baseline_p, y)
    c_probs, c_target = _validate_scoring_inputs(candidate_p, y)
    _require_same_shape(b_probs, c_probs, "baseline/candidate probability matrix")
    if not np.array_equal(target, c_target):
        raise Football3ContractError("baseline/candidate target mismatch")
    vectors = {
        "LogLoss": (per_match_logloss(b_probs, target), per_match_logloss(c_probs, target)),
        "Brier": (per_match_brier(b_probs, target), per_match_brier(c_probs, target)),
        "RPS": (per_match_rps(b_probs, target), per_match_rps(c_probs, target)),
    }
    n = len(target)
    if n <= 0:
        raise Football3ContractError("paired bootstrap requires at least one match")
    rng = np.random.default_rng(seed)
    means = {name: np.empty(n_resamples, dtype=float) for name in vectors}
    for i in range(n_resamples):
        ix = rng.integers(0, n, size=n)
        for name, (b, c) in vectors.items():
            means[name][i] = float(np.mean((c - b)[ix]))
    alpha = (1.0 - float(ci)) / 2.0
    out: dict[str, dict[str, float]] = {}
    for name, (b, c) in vectors.items():
        d = c - b
        lo, hi = np.quantile(means[name], [alpha, 1.0 - alpha])
        out[name] = {
            "delta": float(np.mean(d)),
            "ci_low": float(lo),
            "ci_high": float(hi),
            "p_delta_lt_0": float(np.mean(means[name] < 0.0)),
            "n": int(n),
            "n_resamples": int(n_resamples),
            "seed": int(seed),
            "paired": True,
        }
    return out


def paired_bootstrap_delta_logloss(baseline_p, candidate_p, y, *, n_resamples: int, seed: int, ci: float = 0.90) -> dict[str, float]:
    return paired_bootstrap_proper_score_deltas(
        baseline_p, candidate_p, y, n_resamples=n_resamples, seed=seed, ci=ci
    )["LogLoss"]


def required_paired_n_from_observed_delta(
    per_match_delta: Sequence[float],
    *,
    alpha: float = 0.10,
    power: float = 0.80,
    conservative_multiplier: float = 1.25,
) -> int:
    d = _as_float_array(per_match_delta)
    if d.ndim != 1 or len(d) < 30:
        raise Football3ContractError("power planning requires >=30 development paired deltas")
    if not math.isfinite(float(alpha)) or not (0 < float(alpha) < 1):
        raise Football3ContractError("power-planning alpha must be in (0,1)")
    if not math.isfinite(float(power)) or not (0.5 < float(power) < 1):
        raise Football3ContractError("power must be in (0.5,1)")
    if not math.isfinite(float(conservative_multiplier)) or float(conservative_multiplier) < 1.0:
        raise Football3ContractError("conservative multiplier must be finite and >=1")
    effect = abs(float(np.mean(d)))
    sd = float(np.std(d, ddof=1))
    if effect <= 0 or sd <= 0:
        raise Football3ContractError("nonpositive effect/variance for planning")
    z_alpha = NormalDist().inv_cdf(1.0 - float(alpha) / 2.0)
    z_power = NormalDist().inv_cdf(float(power))
    n = ((z_alpha + z_power) * sd / effect) ** 2
    return int(math.ceil(n * float(conservative_multiplier)))


def canonical_identity_string(row: Mapping[str, object]) -> str:
    fields = ("sourceCode", "id", "matchDate", "Country", "League", "Season", "homeTeam", "awayTeam")
    missing = [f for f in fields if f not in row or str(row[f]).strip() == ""]
    if missing:
        raise Football3ContractError(f"identity missing fields: {missing}")
    return "|".join(str(row[f]).strip() for f in fields)


def identity_sha256(row: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_identity_string(row).encode("utf-8")).hexdigest()


def ordered_identity_sha256(ids: Sequence[str]) -> str:
    vals = [str(x).strip() for x in ids]
    if not vals:
        raise Football3ContractError("identity hash vector must be nonempty")
    if any(not HEX64.fullmatch(x) for x in vals):
        raise Football3ContractError("identity hash vector contains non-sha256 value")
    if len(vals) != len(set(vals)):
        raise Football3ContractError("duplicate identity hash")
    raw = "\n".join(vals) + "\n"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assert_scoring_identities_match_contract(ids: Sequence[str], contract: Mapping[str, object], expected_rows: int) -> str:
    vals=[str(x).strip() for x in ids]
    if len(vals) != expected_rows:
        raise Football3ContractError(f"scoring identity row mismatch: identities={len(vals)} scoring_rows={expected_rows}")
    digest=ordered_identity_sha256(vals)
    try:
        data=contract["data_plan"]
        frozen_n=data["identity_count"]
        frozen_digest=data["ordered_identity_sha256"]
    except Exception as e:
        raise Football3ContractError(f"contract missing frozen identity binding: {e}") from e
    if isinstance(frozen_n,bool) or not isinstance(frozen_n,int) or frozen_n <= 0:
        raise Football3ContractError("contract identity_count must be positive integer")
    if expected_rows != frozen_n:
        raise Football3ContractError(f"scored rows {expected_rows} do not equal frozen identity_count {frozen_n}")
    if digest != frozen_digest:
        raise Football3ContractError("scored identity order/digest does not equal frozen identity lock")
    return digest


def assert_disjoint_identity_sets(named_sets: Mapping[str, Iterable[str]]) -> None:
    materialized = {k: set(v) for k, v in named_sets.items()}
    names = list(materialized)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            overlap = materialized[a] & materialized[b]
            if overlap:
                raise Football3ContractError(f"identity overlap {a} vs {b}: {len(overlap)}")


def assert_exact_one_to_one_join(left: pd.DataFrame, right: pd.DataFrame, *, keys: Sequence[str], expected_rows: int | None = None) -> pd.DataFrame:
    if not keys:
        raise Football3ContractError("exact join requires at least one key")
    for side, frame in (("left", left), ("right", right)):
        missing = [k for k in keys if k not in frame.columns]
        if missing:
            raise Football3ContractError(f"{side} missing join keys: {missing}")
        if frame.duplicated(list(keys)).any():
            raise Football3ContractError(f"{side} has duplicate join keys")
    out = left.merge(right, on=list(keys), how="inner", validate="one_to_one")
    wanted = len(left) if expected_rows is None else expected_rows
    if not isinstance(wanted, int) or wanted < 0:
        raise Football3ContractError("expected_rows must be nonnegative integer")
    if len(out) != wanted or len(left) != wanted:
        raise Football3ContractError(f"exact join coverage failed: left={len(left)} joined={len(out)} expected={wanted}")
    return out


def parse_aware_utc_timestamp(x) -> pd.Timestamp:
    try:
        t = pd.Timestamp(x)
    except Exception as e:
        raise Football3ContractError(f"invalid timestamp: {x!r}") from e
    if pd.isna(t):
        raise Football3ContractError("invalid timestamp")
    if t.tzinfo is None:
        raise Football3ContractError(f"timezone-naive timestamp forbidden: {x!r}")
    return t.tz_convert("UTC")


def parse_utc_or_naive_timestamp(x) -> pd.Timestamp:
    """Compatibility name; new football3 science rejects timezone-naive values."""
    return parse_aware_utc_timestamp(x)


def _strict_utc_series(values, name: str) -> pd.Series:
    raw = list(values)
    if not raw:
        raise Football3ContractError(f"empty timestamp vector: {name}")
    parsed = []
    for i, value in enumerate(raw):
        try:
            parsed.append(parse_aware_utc_timestamp(value))
        except Football3ContractError as e:
            raise Football3ContractError(f"{name} row {i}: {e}") from e
    return pd.Series(parsed, dtype="datetime64[ns, UTC]")


def assert_temporal_oos(train_dates, test_dates) -> None:
    tr = _strict_utc_series(train_dates, "train timestamp")
    te = _strict_utc_series(test_dates, "test timestamp")
    if tr.max() >= te.min():
        raise Football3ContractError(f"temporal OOS violated: train_max={tr.max()} test_min={te.min()}")


def assert_feature_pit(frame: pd.DataFrame, *, cutoff_col: str, feature_timestamp_cols: Sequence[str]) -> None:
    if cutoff_col not in frame.columns:
        raise Football3ContractError(f"missing cutoff column {cutoff_col}")
    if not feature_timestamp_cols:
        raise Football3ContractError("PIT validation requires at least one feature timestamp column")
    if len(feature_timestamp_cols) != len(set(feature_timestamp_cols)):
        raise Football3ContractError("duplicate feature timestamp column")
    cutoff = _strict_utc_series(frame[cutoff_col].tolist(), cutoff_col)
    for col in feature_timestamp_cols:
        if col not in frame.columns:
            raise Football3ContractError(f"missing feature timestamp column {col}")
        ts = _strict_utc_series(frame[col].tolist(), col)
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


def _clean_group_ids(values, name: str, n: int) -> np.ndarray:
    a = np.asarray(values, dtype=object)
    if a.ndim != 1 or len(a) != n:
        raise Football3ContractError(f"{name} must be one-dimensional and match scoring rows")
    cleaned = np.asarray([str(x).strip() if x is not None else "" for x in a], dtype=object)
    if np.any(cleaned == ""):
        raise Football3ContractError(f"{name} contains blank/missing group id")
    return cleaned


def _group_logloss_deltas(baseline_p: np.ndarray, candidate_p: np.ndarray, y: np.ndarray, groups: np.ndarray) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for g in pd.unique(groups):
        ix = groups == g
        b = multiclass_logloss(baseline_p[ix], y[ix])
        c = multiclass_logloss(candidate_p[ix], y[ix])
        out[str(g)] = {"n": int(np.sum(ix)), "baseline_LogLoss": b, "candidate_LogLoss": c, "delta_LogLoss": c - b}
    return out


def _enforce_runtime_sample_plan(n: int, contract: Mapping[str, object]) -> tuple[str, int]:
    try:
        sample=contract["sample_plan"]
    except Exception as e:
        raise Football3ContractError(f"contract missing sample_plan: {e}") from e
    confirmation=sample.get("confirmation") is True
    key="minimum_n" if confirmation else "development_minimum_n"
    minimum=sample.get(key)
    if isinstance(minimum,bool) or not isinstance(minimum,int) or minimum <= 0:
        raise Football3ContractError(f"{key} must be positive integer")
    if n < minimum:
        raise Football3ContractError(f"scoring rows {n} below frozen {key}={minimum}")
    return ("CONFIRMATION" if confirmation else "DEVELOPMENT"), minimum


def evaluate_frozen_experiment(
    baseline_p,
    candidate_p,
    y,
    *,
    identity_sha256,
    fold_ids,
    domain_ids,
    contract: Mapping[str, object],
) -> dict[str, object]:
    """Canonical V2 football3 scoring/gating path for new scientific runners."""
    b, target = _validate_scoring_inputs(baseline_p, y)
    c, c_target = _validate_scoring_inputs(candidate_p, y)
    _require_same_shape(b, c, "baseline/candidate probability matrix")
    if not np.array_equal(target, c_target):
        raise Football3ContractError("baseline/candidate target mismatch")
    n = len(target)
    scored_identity_digest=assert_scoring_identities_match_contract(identity_sha256,contract,n)
    phase, frozen_minimum_n=_enforce_runtime_sample_plan(n,contract)
    folds = _clean_group_ids(fold_ids, "fold_ids", n)
    domains = _clean_group_ids(domain_ids, "domain_ids", n)

    try:
        metrics_cfg = contract["metrics"]
        calibration_bins = int(metrics_cfg["calibration"]["bins"])
        boot_cfg = contract["bootstrap"]
        gates = contract["success_gates"]
        oos = contract["oos_design"]
    except Exception as e:
        raise Football3ContractError(f"incomplete canonical evaluation contract: {e}") from e

    baseline = score_bundle(b, target, calibration_bins=calibration_bins)
    candidate = score_bundle(c, target, calibration_bins=calibration_bins)
    bootstrap = paired_bootstrap_proper_score_deltas(
        b,
        c,
        target,
        n_resamples=boot_cfg["resamples"],
        seed=boot_cfg["seed"],
        ci=boot_cfg["ci"],
    )
    deltas = {k: float(candidate[k] - baseline[k]) for k in ("LogLoss", "Brier", "RPS", "Top1ECE", "ClasswiseECE")}

    fold_stats = _group_logloss_deltas(b, c, target, folds)
    min_fold_n = oos["minimum_test_rows_per_fold"]
    if isinstance(min_fold_n, bool) or not isinstance(min_fold_n, int) or min_fold_n <= 0:
        raise Football3ContractError("minimum_test_rows_per_fold must be positive integer")
    if any(v["n"] < min_fold_n for v in fold_stats.values()):
        raise Football3ContractError("one or more temporal folds are below frozen minimum_test_rows_per_fold")
    fold_win_fraction = float(np.mean([v["delta_LogLoss"] < 0 for v in fold_stats.values()]))

    domain_stats = _group_logloss_deltas(b, c, target, domains)
    domain_gate = gates["domain_consistency"]
    min_domain_n = domain_gate["minimum_rows_per_domain"]
    if isinstance(min_domain_n, bool) or not isinstance(min_domain_n, int) or min_domain_n <= 0:
        raise Football3ContractError("minimum_rows_per_domain must be positive integer")
    eligible_domains = {k: v for k, v in domain_stats.items() if v["n"] >= min_domain_n}
    if len(eligible_domains) < int(domain_gate["minimum_domains"]):
        raise Football3ContractError("insufficient domains meeting frozen minimum_rows_per_domain")
    domain_win_fraction = float(np.mean([v["delta_LogLoss"] < 0 for v in eligible_domains.values()]))
    max_domain_regression = float(max(v["delta_LogLoss"] for v in eligible_domains.values()))

    primary = gates["primary"]
    secondary = gates["secondary_noninferiority"]
    temporal = gates["temporal_consistency"]
    checks = {
        "primary_delta": deltas["LogLoss"] <= float(primary["delta_max"]),
        "primary_bootstrap_ci_high": bootstrap["LogLoss"]["ci_high"] <= float(primary["bootstrap_ci_high_max"]),
        "Brier_noninferiority": deltas["Brier"] <= float(secondary["Brier_delta_max"]),
        "RPS_noninferiority": deltas["RPS"] <= float(secondary["RPS_delta_max"]),
        "Top1ECE_noninferiority": deltas["Top1ECE"] <= float(secondary["Top1ECE_delta_max"]),
        "ClasswiseECE_noninferiority": deltas["ClasswiseECE"] <= float(secondary["ClasswiseECE_delta_max"]),
        "temporal_consistency": fold_win_fraction >= float(temporal["minimum_fold_win_fraction"]),
        "domain_win_fraction": domain_win_fraction >= float(domain_gate["minimum_win_fraction"]),
        "domain_max_regression": max_domain_regression <= float(domain_gate["max_domain_logloss_regression"]),
    }
    terminal = "PASS" if all(checks.values()) else "PARK"
    return {
        "terminal": terminal,
        "phase": phase,
        "n": int(n),
        "frozen_minimum_n": int(frozen_minimum_n),
        "scored_identity_sha256": scored_identity_digest,
        "baseline": baseline,
        "candidate": candidate,
        "delta": deltas,
        "bootstrap": bootstrap,
        "folds": fold_stats,
        "fold_win_fraction": fold_win_fraction,
        "domains": domain_stats,
        "eligible_domain_count": len(eligible_domains),
        "domain_win_fraction": domain_win_fraction,
        "max_domain_logloss_regression": max_domain_regression,
        "gate_checks": checks,
        "all_gates_pass": bool(all(checks.values())),
    }
