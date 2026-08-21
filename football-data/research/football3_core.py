from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

PT_CLASSES = tuple(range(8))
MASTER_PREDICTION_CUTOFF = "T-15m"
DEFAULT_EPS = 1e-12
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GLOBAL_MATCH_IDENTITY_SCHEMA = "football3_global_match_identity_v1"
SOURCE_ROW_IDENTITY_SCHEMA = "football3_source_row_identity_v1"
TEMPORAL_MANIFEST_SCHEMA = "football3_temporal_fold_manifest_v1"
SEALED_MANIFEST_SCHEMA = "football3_sealed_pool_manifest_v1"
POWER_PLAN_SCHEMA = "football3_confirmation_power_plan_v2"
DELTA_DEFINITION = "candidate_loss_minus_baseline_loss"
DEFAULT_EQUIV_MAX_ABS = 1e-9
DEFAULT_EQUIV_MEAN_ABS = 1e-11
DEFAULT_MIN_FOLD_WIN_FRACTION = 0.60
DEFAULT_MIN_DOMAIN_WIN_FRACTION = 0.60
DEFAULT_MIN_CLUSTERS = 8
DEFAULT_MIN_CONFIRMATION_N = 500


class Football3ContractError(RuntimeError):
    """Fail-closed contract or scientific-safety violation."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_sha256(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def _as_float_array(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if not np.all(np.isfinite(a)):
        raise Football3ContractError("non-finite numeric value")
    return a


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
    if len(arr) == 0 or np.any(arr < 0) or np.any(arr >= n_classes):
        raise Football3ContractError(f"target outside 0..{n_classes-1} or empty")
    return arr


def collapse_total_goals(total_goals: Iterable[int]) -> np.ndarray:
    raw = _as_float_array(list(total_goals))
    if raw.ndim != 1 or len(raw) == 0 or np.any(raw < 0) or not np.all(raw == np.floor(raw)):
        raise Football3ContractError("total-goal targets must be nonempty nonnegative integers")
    return np.minimum(raw.astype(int), 7)


def validate_probability_matrix(p, n_classes: int = 8, atol: float = 1e-10) -> np.ndarray:
    a = _as_float_array(p)
    if a.ndim != 2 or a.shape[1] != n_classes or a.shape[0] == 0:
        raise Football3ContractError(f"probability matrix must have shape (n,{n_classes}) and n>0")
    if np.any(a < -atol) or np.any(a > 1.0 + atol):
        raise Football3ContractError("probability outside [0,1]")
    sums = a.sum(axis=1)
    if not np.allclose(sums, 1.0, atol=atol, rtol=0):
        raise Football3ContractError("probability rows do not sum to one")
    a = np.clip(a, 0.0, 1.0)
    a /= a.sum(axis=1, keepdims=True)
    return a


def devig_two_way(over_odds, under_odds) -> np.ndarray:
    o, u = _as_float_array(over_odds), _as_float_array(under_odds)
    if o.shape != u.shape or np.any(o <= 1.0) or np.any(u <= 1.0):
        raise Football3ContractError("invalid two-way decimal odds")
    io, iu = 1.0 / o, 1.0 / u
    return io / (io + iu)


def ou_tail_k(line: float) -> int:
    mapping = {0.5: 1, 1.5: 2, 2.5: 3, 3.5: 4, 4.5: 5}
    try:
        return mapping[float(line)]
    except KeyError as exc:
        raise Football3ContractError(f"unsupported half-goal line: {line}") from exc


def validate_nested_ou_tails(lines: Sequence[float], probs: Sequence[float], tol: float = 1e-10) -> None:
    if len(lines) != len(probs) or not lines:
        raise Football3ContractError("line/probability length mismatch")
    pairs = sorted((float(l), float(p)) for l, p in zip(lines, probs))
    if len({x[0] for x in pairs}) != len(pairs):
        raise Football3ContractError("duplicate O/U line")
    for (_, a), (_, b) in zip(pairs, pairs[1:]):
        if b > a + tol:
            raise Football3ContractError("nested O/U tail probabilities violated")


def per_match_logloss(p, y) -> np.ndarray:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    return -np.log(np.clip(probs[np.arange(len(target)), target], DEFAULT_EPS, 1.0))


def per_match_brier(p, y) -> np.ndarray:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    onehot = np.eye(probs.shape[1])[target]
    return np.sum((probs - onehot) ** 2, axis=1)


def per_match_rps(p, y) -> np.ndarray:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    onehot = np.eye(probs.shape[1])[target]
    return np.sum((np.cumsum(probs[:, :-1], axis=1) - np.cumsum(onehot[:, :-1], axis=1)) ** 2, axis=1) / (probs.shape[1] - 1)


def multiclass_logloss(p, y) -> float:
    return float(np.mean(per_match_logloss(p, y)))


def multiclass_brier(p, y) -> float:
    return float(np.mean(per_match_brier(p, y)))


def normalized_rps(p, y) -> float:
    return float(np.mean(per_match_rps(p, y)))


def topk_accuracy(p, y, k: int) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= probs.shape[1]:
        raise Football3ContractError("invalid top-k")
    idx = np.argpartition(-probs, kth=k - 1, axis=1)[:, :k]
    return float(np.mean(np.any(idx == target[:, None], axis=1)))


def _binary_ece(prob: np.ndarray, outcome: np.ndarray, bins: int) -> float:
    if not isinstance(bins, int) or isinstance(bins, bool) or bins < 2:
        raise Football3ContractError("calibration bins must be integer >=2")
    edges = np.linspace(0.0, 1.0, bins + 1)
    b = np.clip(np.searchsorted(edges, prob, side="right") - 1, 0, bins - 1)
    total = len(prob)
    out = 0.0
    for i in range(bins):
        ix = b == i
        if np.any(ix):
            out += float(np.sum(ix) / total) * abs(float(np.mean(prob[ix])) - float(np.mean(outcome[ix])))
    return out


def top1_ece(p, y, n_bins: int = 10) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    pred = np.argmax(probs, axis=1)
    conf = probs[np.arange(len(probs)), pred]
    return _binary_ece(conf, (pred == target).astype(float), n_bins)


def classwise_ece(p, y, n_bins: int = 10) -> float:
    probs = validate_probability_matrix(p)
    target = validate_target(y, probs.shape[1])
    if len(target) != len(probs):
        raise Football3ContractError("target/probability row mismatch")
    return float(np.mean([_binary_ece(probs[:, k], (target == k).astype(float), n_bins) for k in range(probs.shape[1])]))


def score_bundle(p, y, calibration_bins: int = 10) -> dict[str, float]:
    probs = validate_probability_matrix(p)
    return {
        "LogLoss": multiclass_logloss(probs, y),
        "Brier": multiclass_brier(probs, y),
        "RPS": normalized_rps(probs, y),
        "Top1ECE": top1_ece(probs, y, calibration_bins),
        "ClasswiseECE": classwise_ece(probs, y, calibration_bins),
        "Top1": topk_accuracy(probs, y, 1),
        "Top3": topk_accuracy(probs, y, min(3, probs.shape[1])),
        "probability_residual_max": float(np.max(np.abs(probs.sum(axis=1) - 1.0))),
    }


def probability_fingerprint(p) -> str:
    a = validate_probability_matrix(p)
    canonical = np.round(a.astype("<f8"), 12).tobytes(order="C")
    return sha256_bytes(canonical)


def prediction_equivalence_audit(baseline_p, candidate_p, *, max_abs_floor: float = DEFAULT_EQUIV_MAX_ABS, mean_abs_floor: float = DEFAULT_EQUIV_MEAN_ABS) -> dict[str, object]:
    b, c = validate_probability_matrix(baseline_p), validate_probability_matrix(candidate_p)
    if b.shape != c.shape:
        raise Football3ContractError("baseline/candidate probability shape mismatch")
    diff = np.abs(c - b)
    max_abs = float(np.max(diff))
    mean_abs = float(np.mean(diff))
    equivalent = max_abs <= float(max_abs_floor) or mean_abs <= float(mean_abs_floor)
    return {
        "baseline_prediction_sha256": probability_fingerprint(b),
        "candidate_prediction_sha256": probability_fingerprint(c),
        "max_abs_diff": max_abs,
        "mean_abs_diff": mean_abs,
        "max_abs_floor": float(max_abs_floor),
        "mean_abs_floor": float(mean_abs_floor),
        "materially_distinct": not equivalent,
    }


def assert_candidate_materially_distinct(baseline_p, candidate_p, **kwargs) -> dict[str, object]:
    audit = prediction_equivalence_audit(baseline_p, candidate_p, **kwargs)
    if not audit["materially_distinct"]:
        raise Football3ContractError("candidate prediction is identical or numerically equivalent to baseline")
    return audit


def paired_bootstrap_vector(delta: Sequence[float], *, n_resamples: int, seed: int, ci: float = 0.90) -> dict[str, float]:
    d = _as_float_array(delta)
    if d.ndim != 1 or len(d) == 0:
        raise Football3ContractError("paired bootstrap requires nonempty 1D delta")
    if not isinstance(n_resamples, int) or isinstance(n_resamples, bool) or n_resamples < 100:
        raise Football3ContractError("bootstrap resamples must be integer >=100")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0.5 < ci < 1.0:
        raise Football3ContractError("invalid bootstrap seed/ci")
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    for i in range(n_resamples):
        means[i] = float(np.mean(d[rng.integers(0, len(d), size=len(d))]))
    alpha = (1.0 - ci) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return {"delta": float(np.mean(d)), "ci_low": float(lo), "ci_high": float(hi), "p_delta_lt_0": float(np.mean(means < 0)), "n": int(len(d)), "n_resamples": n_resamples, "seed": seed, "paired": True, "method": "iid_match"}


def cluster_bootstrap_vector(delta: Sequence[float], cluster_ids: Sequence[object], *, n_resamples: int, seed: int, ci: float = 0.90, minimum_clusters: int = DEFAULT_MIN_CLUSTERS) -> dict[str, float]:
    d = _as_float_array(delta)
    clusters = np.asarray([str(x) for x in cluster_ids], dtype=object)
    if d.ndim != 1 or clusters.ndim != 1 or len(d) != len(clusters):
        raise Football3ContractError("cluster bootstrap row mismatch")
    unique = pd.unique(clusters)
    if len(unique) < minimum_clusters:
        raise Football3ContractError(f"cluster bootstrap requires >= {minimum_clusters} clusters")
    if not isinstance(n_resamples, int) or isinstance(n_resamples, bool) or n_resamples < 100:
        raise Football3ContractError("cluster bootstrap resamples must be integer >=100")
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    by_cluster = {u: d[clusters == u] for u in unique}
    for i in range(n_resamples):
        sampled = rng.choice(unique, size=len(unique), replace=True)
        values = np.concatenate([by_cluster[u] for u in sampled])
        means[i] = float(np.mean(values))
    alpha = (1.0 - ci) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return {"delta": float(np.mean(d)), "ci_low": float(lo), "ci_high": float(hi), "p_delta_lt_0": float(np.mean(means < 0)), "n": int(len(d)), "cluster_count": int(len(unique)), "n_resamples": n_resamples, "seed": seed, "method": "competition_season_cluster"}


def paired_bootstrap_proper_score_deltas(baseline_p, candidate_p, y, *, n_resamples: int, seed: int, ci: float = 0.90) -> dict[str, dict[str, float]]:
    return {
        "LogLoss": paired_bootstrap_vector(per_match_logloss(candidate_p, y) - per_match_logloss(baseline_p, y), n_resamples=n_resamples, seed=seed, ci=ci),
        "Brier": paired_bootstrap_vector(per_match_brier(candidate_p, y) - per_match_brier(baseline_p, y), n_resamples=n_resamples, seed=seed, ci=ci),
        "RPS": paired_bootstrap_vector(per_match_rps(candidate_p, y) - per_match_rps(baseline_p, y), n_resamples=n_resamples, seed=seed, ci=ci),
    }


def paired_bootstrap_delta_logloss(baseline_p, candidate_p, y, *, n_resamples: int, seed: int, ci: float = 0.90) -> dict[str, float]:
    return paired_bootstrap_proper_score_deltas(baseline_p, candidate_p, y, n_resamples=n_resamples, seed=seed, ci=ci)["LogLoss"]


def _cluster_design_effect(delta: np.ndarray, cluster_ids: Sequence[object]) -> float:
    clusters = np.asarray([str(x) for x in cluster_ids], dtype=object)
    unique = pd.unique(clusters)
    if len(unique) < 2:
        raise Football3ContractError("cluster-aware power planning requires >=2 clusters")
    iid_var_mean = float(np.var(delta, ddof=1) / len(delta))
    means = np.asarray([float(np.mean(delta[clusters == u])) for u in unique])
    cluster_var_mean = float(np.var(means, ddof=1) / len(unique))
    if iid_var_mean <= 0:
        raise Football3ContractError("nonpositive iid variance for power planning")
    return max(1.0, cluster_var_mean / iid_var_mean)


def build_confirmation_power_plan(per_match_delta: Sequence[float], cluster_ids: Sequence[object], *, alpha: float = 0.10, power: float = 0.80, conservative_multiplier: float = 1.25, minimum_confirmation_n: int = DEFAULT_MIN_CONFIRMATION_N, source_stage: str = "DEVELOPMENT", metric: str = "LogLoss") -> dict[str, object]:
    d = _as_float_array(per_match_delta)
    if d.ndim != 1 or len(d) < 30:
        raise Football3ContractError("power planning requires >=30 development paired deltas")
    mean_delta = float(np.mean(d))
    if mean_delta >= 0:
        raise Football3ContractError("NO_CONFIRMATION_PLAN: development candidate did not strictly improve")
    if metric != "LogLoss" or source_stage != "DEVELOPMENT":
        raise Football3ContractError("confirmation power plan must be DEVELOPMENT LogLoss")
    if not (0 < alpha < 1 and 0.5 < power < 1 and conservative_multiplier >= 1):
        raise Football3ContractError("invalid confirmation power parameters")
    if not isinstance(minimum_confirmation_n, int) or isinstance(minimum_confirmation_n, bool) or minimum_confirmation_n < 100:
        raise Football3ContractError("minimum confirmation n must be integer >=100")
    effect = -mean_delta
    sd = float(np.std(d, ddof=1))
    if sd <= 0:
        raise Football3ContractError("nonpositive variance for confirmation power plan")
    design_effect = _cluster_design_effect(d, cluster_ids)
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_power = NormalDist().inv_cdf(power)
    raw_n = ((z_alpha + z_power) * sd / effect) ** 2
    required = max(minimum_confirmation_n, int(math.ceil(raw_n * conservative_multiplier * design_effect)))
    summary = {
        "n": int(len(d)),
        "mean_delta": mean_delta,
        "paired_delta_sd": sd,
        "delta_sha256": sha256_bytes(np.asarray(d, dtype="<f8").tobytes()),
        "cluster_sha256": sha256_bytes("\n".join(map(str, cluster_ids)).encode("utf-8")),
        "cluster_count": int(len(pd.unique(np.asarray(cluster_ids, dtype=object)))),
    }
    return {
        "schema": POWER_PLAN_SCHEMA,
        "metric": metric,
        "delta_definition": DELTA_DEFINITION,
        "direction_required": "negative",
        "source_stage": source_stage,
        "input_summary": summary,
        "effect": effect,
        "alpha": alpha,
        "planned_power": power,
        "conservative_multiplier": conservative_multiplier,
        "cluster_definition": "competition-season",
        "design_effect": design_effect,
        "minimum_confirmation_n": minimum_confirmation_n,
        "required_n": required,
    }


def required_paired_n_from_observed_delta(per_match_delta: Sequence[float], *, alpha: float = 0.10, power: float = 0.80, conservative_multiplier: float = 1.25) -> int:
    d = _as_float_array(per_match_delta)
    clusters = [f"legacy-{i // 10}" for i in range(len(d))]
    return int(build_confirmation_power_plan(d, clusters, alpha=alpha, power=power, conservative_multiplier=conservative_multiplier, minimum_confirmation_n=100)["required_n"])


def validate_confirmation_power_plan(plan: Mapping[str, object], per_match_delta: Sequence[float], cluster_ids: Sequence[object]) -> None:
    recomputed = build_confirmation_power_plan(
        per_match_delta,
        cluster_ids,
        alpha=float(plan.get("alpha", math.nan)),
        power=float(plan.get("planned_power", math.nan)),
        conservative_multiplier=float(plan.get("conservative_multiplier", math.nan)),
        minimum_confirmation_n=int(plan.get("minimum_confirmation_n", 0)),
        source_stage=str(plan.get("source_stage", "")),
        metric=str(plan.get("metric", "")),
    )
    for key in ("schema", "metric", "delta_definition", "direction_required", "source_stage", "required_n"):
        if plan.get(key) != recomputed.get(key):
            raise Football3ContractError(f"confirmation power plan mismatch: {key}")
    if not math.isclose(float(plan.get("effect", math.nan)), float(recomputed["effect"]), rel_tol=1e-12, abs_tol=1e-12):
        raise Football3ContractError("confirmation power plan effect inconsistent with signed raw delta")
    if plan.get("input_summary") != recomputed.get("input_summary"):
        raise Football3ContractError("confirmation power plan input summary mismatch")


def _normalize_name(value: object) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


def parse_aware_utc_timestamp(value: object) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None:
        raise Football3ContractError(f"timezone-aware timestamp required: {value!r}")
    return t.tz_convert("UTC")


def source_row_identity(row: Mapping[str, object]) -> str:
    for key in ("sourceCode", "id"):
        if key not in row or str(row[key]).strip() == "":
            raise Football3ContractError(f"source row identity missing {key}")
    payload = {"schema": SOURCE_ROW_IDENTITY_SCHEMA, "sourceCode": str(row["sourceCode"]).strip(), "id": str(row["id"]).strip()}
    return canonical_json_sha256(payload)


@dataclass(frozen=True)
class GlobalIdentityRegistry:
    manifest_path: Path
    manifest_sha256: str
    alias_version: str
    kickoff_tolerance_seconds: int
    competition_aliases: Mapping[str, str]
    team_aliases: Mapping[str, str]
    fixtures: tuple[Mapping[str, object], ...]

    @classmethod
    def load(cls, path: str | Path, *, expected_sha256: str | None = None) -> "GlobalIdentityRegistry":
        p = Path(path)
        raw = p.read_bytes()
        digest = sha256_bytes(raw)
        if expected_sha256 and digest != expected_sha256:
            raise Football3ContractError("global identity registry SHA mismatch")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("schema") != "football3_global_fixture_registry_v1":
            raise Football3ContractError("invalid global identity registry schema")
        tol = payload.get("kickoff_tolerance_seconds")
        if not isinstance(tol, int) or isinstance(tol, bool) or not 0 <= tol <= 900:
            raise Football3ContractError("invalid kickoff tolerance")
        comp = {_normalize_name(k): str(v) for k, v in payload.get("competition_aliases", {}).items()}
        teams = {_normalize_name(k): str(v) for k, v in payload.get("team_aliases", {}).items()}
        fixtures = tuple(payload.get("fixtures", []))
        return cls(p, digest, str(payload.get("alias_version", "")), tol, comp, teams, fixtures)

    def _resolve_alias(self, value: object, mapping: Mapping[str, str], field: str) -> str:
        key = _normalize_name(value)
        if key not in mapping:
            raise Football3ContractError(f"UNRESOLVED global identity alias: {field}={value!r}")
        return mapping[key]

    def resolve(self, row: Mapping[str, object]) -> str:
        competition = self._resolve_alias(row.get("Competition", row.get("League")), self.competition_aliases, "competition")
        home = self._resolve_alias(row.get("homeTeam"), self.team_aliases, "homeTeam")
        away = self._resolve_alias(row.get("awayTeam"), self.team_aliases, "awayTeam")
        if home == away:
            raise Football3ContractError("UNRESOLVED global identity: home/away collapse")
        kickoff = parse_aware_utc_timestamp(row.get("kickoff_utc", row.get("matchDate")))
        season = str(row.get("Season", "")).strip()
        matches = []
        for fixture in self.fixtures:
            try:
                if str(fixture["competition"]) != competition or str(fixture["home_team"]) != home or str(fixture["away_team"]) != away:
                    continue
                if season and str(fixture.get("season", "")) != season:
                    continue
                fk = parse_aware_utc_timestamp(fixture["kickoff_utc"])
                if abs((kickoff - fk).total_seconds()) <= self.kickoff_tolerance_seconds:
                    matches.append((fixture, fk))
            except Exception as exc:
                raise Football3ContractError(f"invalid frozen fixture registry entry: {exc}") from exc
        if len(matches) != 1:
            raise Football3ContractError(f"UNRESOLVED global identity: fixture candidates={len(matches)}")
        fixture, fk = matches[0]
        payload = {
            "schema": GLOBAL_MATCH_IDENTITY_SCHEMA,
            "competition": competition,
            "kickoff_utc": fk.isoformat(),
            "home_team": home,
            "away_team": away,
            "season": str(fixture.get("season", season)),
            "alias_version": self.alias_version,
            "alias_manifest_sha256": self.manifest_sha256,
        }
        return canonical_json_sha256(payload)


def ordered_identity_sha256(ids: Sequence[str]) -> str:
    vals = [str(x).strip() for x in ids]
    if not vals or any(not HEX64.fullmatch(x) for x in vals) or len(vals) != len(set(vals)):
        raise Football3ContractError("invalid/duplicate global identity vector")
    return sha256_bytes(("\n".join(vals) + "\n").encode("utf-8"))


def key_set_sha256(rows: Sequence[Sequence[str]]) -> str:
    vals = ["\x1f".join(row) for row in rows]
    if len(vals) != len(set(vals)):
        raise Football3ContractError("duplicate frozen join key")
    return sha256_bytes(("\n".join(sorted(vals)) + "\n").encode("utf-8"))


def ordered_key_sha256(rows: Sequence[Sequence[str]]) -> str:
    vals = ["\x1f".join(row) for row in rows]
    if len(vals) != len(set(vals)):
        raise Football3ContractError("duplicate frozen join key")
    return sha256_bytes(("\n".join(vals) + "\n").encode("utf-8"))


def _extract_string_keys(frame: pd.DataFrame, keys: Sequence[str]) -> list[tuple[str, ...]]:
    if not keys:
        raise Football3ContractError("exact join requires keys")
    for key in keys:
        if key not in frame.columns:
            raise Football3ContractError(f"missing join key: {key}")
    if frame.duplicated(list(keys)).any():
        raise Football3ContractError("duplicate join keys")
    rows = []
    for rec in frame[list(keys)].itertuples(index=False, name=None):
        if any(type(v) is not str or v == "" for v in rec):
            raise Football3ContractError("frozen join keys must be nonempty strings; type drift rejected")
        rows.append(tuple(rec))
    return rows


def assert_exact_one_to_one_join(left: pd.DataFrame, right: pd.DataFrame, *, keys: Sequence[str], expected_rows: int | None = None) -> pd.DataFrame:
    lkeys, rkeys = _extract_string_keys(left, keys), _extract_string_keys(right, keys)
    wanted = len(lkeys) if expected_rows is None else expected_rows
    if not isinstance(wanted, int) or isinstance(wanted, bool) or wanted < 0:
        raise Football3ContractError("expected_rows must be nonnegative integer")
    if not (len(left) == len(right) == wanted):
        raise Football3ContractError(f"exact join row mismatch left={len(left)} right={len(right)} expected={wanted}")
    if set(lkeys) != set(rkeys):
        raise Football3ContractError("exact join key sets differ")
    out = left.merge(right, on=list(keys), how="inner", validate="one_to_one", sort=False)
    if len(out) != wanted:
        raise Football3ContractError("exact join coverage mismatch")
    return out


def load_label_table_after_identity_guard(left: pd.DataFrame, label_path: str | Path, manifest_path: str | Path, *, keys: Sequence[str], target_columns: Sequence[str], expected_rows: int) -> pd.DataFrame:
    lp, mp = Path(label_path), Path(manifest_path)
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    if manifest.get("schema") != "football3_label_identity_manifest_v1":
        raise Football3ContractError("invalid label identity manifest")
    if manifest.get("label_file_sha256") != file_sha256(lp):
        raise Football3ContractError("label file SHA mismatch before target decode")
    lkeys = _extract_string_keys(left, keys)
    if manifest.get("keys") != list(keys) or manifest.get("row_count") != expected_rows:
        raise Football3ContractError("label manifest contract mismatch")
    if manifest.get("key_types") != ["string"] * len(keys):
        raise Football3ContractError("label manifest key type mismatch")
    if manifest.get("ordered_keys_sha256") != ordered_key_sha256(lkeys) or manifest.get("key_set_sha256") != key_set_sha256(lkeys):
        raise Football3ContractError("label manifest identity mismatch before target decode")
    frame = pd.read_csv(lp, dtype={k: "string" for k in keys})
    for col in target_columns:
        if col not in frame.columns:
            raise Football3ContractError(f"target column missing: {col}")
    return assert_exact_one_to_one_join(left, frame[list(keys) + list(target_columns)], keys=keys, expected_rows=expected_rows)


def assert_feature_pit(frame: pd.DataFrame, *, cutoff_col: str, feature_timestamp_cols: Sequence[str]) -> None:
    if cutoff_col not in frame.columns or not feature_timestamp_cols:
        raise Football3ContractError("PIT columns missing")
    cutoff = [parse_aware_utc_timestamp(x) for x in frame[cutoff_col]]
    for col in feature_timestamp_cols:
        if col not in frame.columns:
            raise Football3ContractError(f"missing feature timestamp column: {col}")
        ts = [parse_aware_utc_timestamp(x) for x in frame[col]]
        if any(a > b for a, b in zip(ts, cutoff)):
            raise Football3ContractError(f"PIT violation in {col}")


def assert_same_prediction_cutoff(baseline_cutoff: str, candidate_cutoff: str) -> None:
    norm = lambda x: "".join(str(x).casefold().split())
    if norm(baseline_cutoff) != norm(candidate_cutoff):
        raise Football3ContractError("baseline/candidate prediction cutoffs differ")


def assert_master_prediction_cutoff(*cutoffs: str, master: str = MASTER_PREDICTION_CUTOFF) -> None:
    norm = lambda x: "".join(str(x).casefold().split())
    if any(norm(x) != norm(master) for x in cutoffs):
        raise Football3ContractError(f"football3 master prediction cutoff is {master}")


def assert_temporal_oos(train_dates, test_dates) -> None:
    tr, te = [parse_aware_utc_timestamp(x) for x in train_dates], [parse_aware_utc_timestamp(x) for x in test_dates]
    if not tr or not te or max(tr) >= min(te):
        raise Football3ContractError("temporal OOS violated")


@dataclass(frozen=True)
class TemporalFoldManifest:
    path: Path
    sha256: str
    rows: tuple[Mapping[str, object], ...]

    @classmethod
    def load(cls, path: str | Path, *, expected_sha256: str | None = None) -> "TemporalFoldManifest":
        p = Path(path)
        raw = p.read_bytes()
        digest = sha256_bytes(raw)
        if expected_sha256 and digest != expected_sha256:
            raise Football3ContractError("temporal fold manifest SHA mismatch")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("schema") != TEMPORAL_MANIFEST_SCHEMA or not isinstance(payload.get("rows"), list):
            raise Football3ContractError("invalid temporal fold manifest")
        return cls(p, digest, tuple(payload["rows"]))

    def bind_scoring_rows(self, identity_sha256: Sequence[str], fold_ids: Sequence[object], scored_dates_utc: Sequence[object]) -> None:
        ids = [str(x) for x in identity_sha256]
        folds = [str(x) for x in fold_ids]
        dates = [parse_aware_utc_timestamp(x) for x in scored_dates_utc]
        if not (len(ids) == len(folds) == len(dates) == len(self.rows)):
            raise Football3ContractError("temporal manifest/scoring row count mismatch")
        if len(ids) != len(set(ids)):
            raise Football3ContractError("scoring identity appears in multiple folds")
        seen = set()
        by_fold: dict[str, list[pd.Timestamp]] = {}
        train_max: dict[str, pd.Timestamp] = {}
        for i, (identity, fold, date, row) in enumerate(zip(ids, folds, dates, self.rows)):
            if str(row.get("identity_sha256")) != identity or str(row.get("fold_id")) != fold:
                raise Football3ContractError(f"temporal manifest identity/fold mismatch at row {i}")
            expected_date = parse_aware_utc_timestamp(row.get("test_time_utc"))
            if expected_date != date:
                raise Football3ContractError(f"temporal manifest scoring date mismatch at row {i}")
            if identity in seen:
                raise Football3ContractError("identity leakage across temporal folds")
            seen.add(identity)
            by_fold.setdefault(fold, []).append(date)
            tm = parse_aware_utc_timestamp(row.get("train_max_utc"))
            if fold in train_max and train_max[fold] != tm:
                raise Football3ContractError("inconsistent train_max within fold")
            train_max[fold] = tm
        for fold, tests in by_fold.items():
            if train_max[fold] >= min(tests):
                raise Football3ContractError(f"temporal OOS overlap in fold {fold}")


_RECEIPT_ATTESTATION = object()


@dataclass(frozen=True)
class SealedPool:
    name: str
    status: str = "SEALED"


@dataclass(frozen=True, init=False)
class SealedAccessReceipt:
    pool_id: str
    manifest_sha256: str
    file_sha256: str
    identity_sha256: str
    access_count: int
    target_column_reads: int
    rows_materialized: int
    authorized: bool
    _attestation: object

    def __init__(self, *, pool_id: str, manifest_sha256: str, file_sha256: str, identity_sha256: str, access_count: int, target_column_reads: int, rows_materialized: int, authorized: bool, _attestation: object):
        if _attestation is not _RECEIPT_ATTESTATION:
            raise Football3ContractError("sealed access receipts may only be issued by SealedPoolReader")
        object.__setattr__(self, "pool_id", pool_id)
        object.__setattr__(self, "manifest_sha256", manifest_sha256)
        object.__setattr__(self, "file_sha256", file_sha256)
        object.__setattr__(self, "identity_sha256", identity_sha256)
        object.__setattr__(self, "access_count", access_count)
        object.__setattr__(self, "target_column_reads", target_column_reads)
        object.__setattr__(self, "rows_materialized", rows_materialized)
        object.__setattr__(self, "authorized", authorized)
        object.__setattr__(self, "_attestation", _attestation)


class SealedPoolReader:
    def __init__(self, manifest_paths: Sequence[str | Path], *, authorized_pool_ids: Sequence[str] = ()):
        self._authorized = set(map(str, authorized_pool_ids))
        self._manifests: dict[str, dict[str, object]] = {}
        self._manifest_sha: dict[str, str] = {}
        self._stats: dict[str, dict[str, int]] = {}
        for path in manifest_paths:
            p = Path(path)
            raw = p.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("schema") != SEALED_MANIFEST_SCHEMA:
                raise Football3ContractError("invalid sealed pool manifest")
            pool_id = str(payload.get("pool_id", ""))
            if not pool_id or pool_id in self._manifests:
                raise Football3ContractError("invalid/duplicate sealed pool id")
            for key in ("file_sha256", "identity_sha256"):
                if not HEX64.fullmatch(str(payload.get(key, ""))):
                    raise Football3ContractError(f"invalid sealed manifest {key}")
            if payload.get("status") != "SEALED":
                raise Football3ContractError("sealed manifest status must be SEALED")
            self._manifests[pool_id] = {**payload, "manifest_path": str(p)}
            self._manifest_sha[pool_id] = sha256_bytes(raw)
            self._stats[pool_id] = {"access_count": 0, "target_column_reads": 0, "rows_materialized": 0}

    def read_csv(self, pool_id: str, *, target_columns: Sequence[str]) -> pd.DataFrame:
        if pool_id not in self._manifests:
            raise Football3ContractError("sealed pool absent from runtime manifest set")
        if pool_id not in self._authorized:
            raise Football3ContractError("sealed pool access not authorized")
        manifest = self._manifests[pool_id]
        path = Path(str(manifest["file_path"]))
        if file_sha256(path) != manifest["file_sha256"]:
            raise Football3ContractError("sealed file SHA mismatch")
        allowed_targets = set(map(str, manifest.get("target_columns", [])))
        if not set(map(str, target_columns)).issubset(allowed_targets):
            raise Football3ContractError("sealed target column not authorized by manifest")
        frame = pd.read_csv(path)
        self._stats[pool_id]["access_count"] += 1
        self._stats[pool_id]["target_column_reads"] += len(target_columns)
        self._stats[pool_id]["rows_materialized"] += len(frame)
        return frame

    def receipts(self) -> tuple[SealedAccessReceipt, ...]:
        out = []
        for pool_id, manifest in self._manifests.items():
            s = self._stats[pool_id]
            out.append(SealedAccessReceipt(
                pool_id=pool_id,
                manifest_sha256=self._manifest_sha[pool_id],
                file_sha256=str(manifest["file_sha256"]),
                identity_sha256=str(manifest["identity_sha256"]),
                access_count=s["access_count"],
                target_column_reads=s["target_column_reads"],
                rows_materialized=s["rows_materialized"],
                authorized=pool_id in self._authorized,
                _attestation=_RECEIPT_ATTESTATION,
            ))
        return tuple(out)


def validate_sealed_run_receipts(expected_pool_ids: Sequence[str], receipts: Sequence[SealedAccessReceipt]) -> None:
    expected = list(map(str, expected_pool_ids))
    if len(expected) != len(set(expected)):
        raise Football3ContractError("duplicate expected sealed pool id")
    if any(not isinstance(r, SealedAccessReceipt) or r._attestation is not _RECEIPT_ATTESTATION for r in receipts):
        raise Football3ContractError("unattested sealed receipt")
    by_id = {r.pool_id: r for r in receipts}
    if set(by_id) != set(expected):
        raise Football3ContractError("every sealed pool must have an explicit reader-issued receipt")
    for r in receipts:
        for name in ("access_count", "target_column_reads", "rows_materialized"):
            value = getattr(r, name)
            if type(value) is not int or value < 0:
                raise Football3ContractError(f"sealed receipt {name} must be nonnegative integer")
        if not r.authorized and (r.access_count or r.target_column_reads or r.rows_materialized):
            raise Football3ContractError("unauthorized sealed pool shows access")


def assert_sealed_boundaries(access_counts: Mapping[str, int], sealed: Sequence[SealedPool]) -> None:
    expected = [p.name for p in sealed]
    if set(access_counts) != set(expected):
        raise Football3ContractError("sealed access counts must explicitly include every sealed pool and no extras")
    for name in expected:
        value = access_counts[name]
        if type(value) is not int or value < 0:
            raise Football3ContractError("sealed access count must be nonnegative integer; bool/float/string rejected")
        if value != 0:
            raise Football3ContractError(f"sealed pool accessed: {name} count={value}")


def evaluate_frozen_experiment(baseline_p, candidate_p, y, *, identity_sha256: Sequence[str], fold_ids: Sequence[object], domain_ids: Sequence[object], scored_dates_utc: Sequence[object], cluster_ids: Sequence[object], temporal_manifest: TemporalFoldManifest, contract: Mapping[str, object]) -> dict[str, object]:
    b, c = validate_probability_matrix(baseline_p), validate_probability_matrix(candidate_p)
    target = validate_target(y, b.shape[1])
    if b.shape != c.shape or len(target) != len(b):
        raise Football3ContractError("scoring shape mismatch")
    n = len(target)
    ids = [str(x) for x in identity_sha256]
    if len(ids) != n or any(not HEX64.fullmatch(x) for x in ids):
        raise Football3ContractError("invalid scoring identities")
    data = contract.get("data_plan", {})
    if data.get("identity_count") != n or data.get("ordered_identity_sha256") != ordered_identity_sha256(ids):
        raise Football3ContractError("scoring identities differ from frozen contract")
    expected_tm_sha = contract.get("oos_design", {}).get("temporal_manifest_sha256")
    if expected_tm_sha != temporal_manifest.sha256:
        raise Football3ContractError("temporal manifest not bound to contract")
    temporal_manifest.bind_scoring_rows(ids, fold_ids, scored_dates_utc)
    folds = np.asarray([str(x) for x in fold_ids], dtype=object)
    domains = np.asarray([str(x) for x in domain_ids], dtype=object)
    clusters = np.asarray([str(x) for x in cluster_ids], dtype=object)
    if not (len(folds) == len(domains) == len(clusters) == n) or np.any(folds == "") or np.any(domains == "") or np.any(clusters == ""):
        raise Football3ContractError("fold/domain/cluster row mismatch")
    eq_cfg = contract.get("candidate_equivalence", {})
    prediction_audit = assert_candidate_materially_distinct(
        b, c,
        max_abs_floor=float(eq_cfg.get("max_abs_floor", DEFAULT_EQUIV_MAX_ABS)),
        mean_abs_floor=float(eq_cfg.get("mean_abs_floor", DEFAULT_EQUIV_MEAN_ABS)),
    )
    bins = int(contract.get("metrics", {}).get("calibration", {}).get("bins", 10))
    baseline = score_bundle(b, target, bins)
    candidate = score_bundle(c, target, bins)
    deltas = {k: candidate[k] - baseline[k] for k in ("LogLoss", "Brier", "RPS", "Top1ECE", "ClasswiseECE")}
    boot = contract.get("bootstrap", {})
    iid = paired_bootstrap_proper_score_deltas(b, c, target, n_resamples=int(boot.get("resamples", 1000)), seed=int(boot.get("seed", 1)), ci=float(boot.get("ci", 0.90)))
    dep = contract.get("dependency_bootstrap", {})
    minimum_clusters = int(dep.get("minimum_clusters", DEFAULT_MIN_CLUSTERS))
    cluster_boot = {
        "LogLoss": cluster_bootstrap_vector(per_match_logloss(c, target) - per_match_logloss(b, target), clusters, n_resamples=int(dep.get("resamples", boot.get("resamples", 1000))), seed=int(dep.get("seed", boot.get("seed", 1))), ci=float(dep.get("ci", boot.get("ci", 0.90))), minimum_clusters=minimum_clusters),
        "Brier": cluster_bootstrap_vector(per_match_brier(c, target) - per_match_brier(b, target), clusters, n_resamples=int(dep.get("resamples", boot.get("resamples", 1000))), seed=int(dep.get("seed", boot.get("seed", 1))), ci=float(dep.get("ci", boot.get("ci", 0.90))), minimum_clusters=minimum_clusters),
        "RPS": cluster_bootstrap_vector(per_match_rps(c, target) - per_match_rps(b, target), clusters, n_resamples=int(dep.get("resamples", boot.get("resamples", 1000))), seed=int(dep.get("seed", boot.get("seed", 1))), ci=float(dep.get("ci", boot.get("ci", 0.90))), minimum_clusters=minimum_clusters),
    }
    fold_stats = {}
    for f in pd.unique(folds):
        ix = folds == f
        fold_stats[f] = {"n": int(np.sum(ix)), "delta_LogLoss": float(multiclass_logloss(c[ix], target[ix]) - multiclass_logloss(b[ix], target[ix]))}
    domain_stats = {}
    for d in pd.unique(domains):
        ix = domains == d
        domain_stats[d] = {"n": int(np.sum(ix)), "delta_LogLoss": float(multiclass_logloss(c[ix], target[ix]) - multiclass_logloss(b[ix], target[ix]))}
    fold_win_fraction = float(np.mean([x["delta_LogLoss"] < 0 for x in fold_stats.values()]))
    domain_win_fraction = float(np.mean([x["delta_LogLoss"] < 0 for x in domain_stats.values()]))
    gates = contract.get("success_gates", {})
    temporal = gates.get("temporal_consistency", {})
    domain_gate = gates.get("domain_consistency", {})
    min_fold = float(temporal.get("minimum_fold_win_fraction", DEFAULT_MIN_FOLD_WIN_FRACTION))
    min_domain = float(domain_gate.get("minimum_win_fraction", DEFAULT_MIN_DOMAIN_WIN_FRACTION))
    checks = {
        "candidate_materially_distinct": bool(prediction_audit["materially_distinct"]),
        "primary_logloss_strict_improvement": deltas["LogLoss"] < 0.0,
        "iid_logloss_ci_high_strictly_negative": iid["LogLoss"]["ci_high"] < 0.0,
        "cluster_logloss_ci_high_strictly_negative": cluster_boot["LogLoss"]["ci_high"] < 0.0,
        "fold_win_fraction": min_fold > 0.0 and fold_win_fraction >= min_fold,
        "domain_win_fraction": min_domain > 0.0 and domain_win_fraction >= min_domain,
        "Brier_noninferiority": deltas["Brier"] <= float(gates.get("secondary_noninferiority", {}).get("Brier_delta_max", 0.0)),
        "RPS_noninferiority": deltas["RPS"] <= float(gates.get("secondary_noninferiority", {}).get("RPS_delta_max", 0.0)),
    }
    terminal = "PROMOTE" if all(checks.values()) else "PARK_NO_PROMOTION"
    return {
        "terminal": terminal,
        "all_gates_pass": all(checks.values()),
        "n": n,
        "baseline": baseline,
        "candidate": candidate,
        "delta": deltas,
        "prediction_equivalence": prediction_audit,
        "bootstrap_iid": iid,
        "bootstrap_dependency": cluster_boot,
        "folds": fold_stats,
        "domains": domain_stats,
        "fold_win_fraction": fold_win_fraction,
        "domain_win_fraction": domain_win_fraction,
        "gate_checks": checks,
        "scored_identity_sha256": ordered_identity_sha256(ids),
        "temporal_manifest_sha256": temporal_manifest.sha256,
    }
