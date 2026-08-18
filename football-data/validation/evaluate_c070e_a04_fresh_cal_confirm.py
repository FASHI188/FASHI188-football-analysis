from __future__ import annotations

import argparse
import hashlib
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

import evaluate_c069_matched_pair_draw_state_r1 as c69
import evaluate_c069_r2_maxcard_coverage as r2
import evaluate_c069_r3_a03_expanded_dev as r3
import evaluate_c070c_semimarkov_generator as c70c
import evaluate_c070d_duration_residual_integration as c70d


SCHEMA_VERSION = "C070E_A04_FRESH_CAL_CONFIRM_V1"
SEED = "A_SERIES_WYSCOUT_20260818_R1"
A04_START = 1200
A04_STOP = 1600
A04_COUNT = 400
EPS = 1e-6
MIN_RAW_PARTITION = 160
MIN_PRIOR_TRAIN_PAIRS = 30
MIN_CAL_PAIRS = 25
MIN_CONFIRM_PAIRS = 25
BOOT_REPS = 2000
BOOT_SEED = 7205


def _logit(p: np.ndarray | float) -> np.ndarray:
    x = np.clip(np.asarray(p, float), EPS, 1.0 - EPS)
    return np.log(x / (1.0 - x))


def _sigmoid(x: np.ndarray | float) -> np.ndarray:
    z = np.asarray(x, float)
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return np.clip(out, EPS, 1.0 - EPS)


def _candidate(p_inc: np.ndarray, q_markov: np.ndarray, q_semi: np.ndarray, alpha: float):
    shift = _logit(q_semi) - _logit(q_markov)
    return _sigmoid(_logit(p_inc) + float(alpha) * shift), shift


def _binary_log_loss(y: np.ndarray, p: np.ndarray) -> float:
    yy = np.asarray(y, int)
    pp = np.clip(np.asarray(p, float), 1e-12, 1.0 - 1e-12)
    return float(np.mean(-(yy * np.log(pp) + (1 - yy) * np.log(1 - pp))))


def _load_a04(path: Path):
    z = zipfile.ZipFile(path)
    pkg = json.loads(z.read("PACKAGE.json"))
    manifest = [json.loads(line) for line in z.read("MANIFEST.jsonl").decode().splitlines() if line]
    manifest = sorted(manifest, key=lambda x: int(x["rank"]))
    if pkg.get("package_id") != "A04" or int(pkg.get("match_count", -1)) != A04_COUNT:
        raise RuntimeError("A04 package identity mismatch")
    if pkg.get("seed") != SEED:
        raise RuntimeError("A04 seed mismatch")
    if int(pkg.get("source_global_rank_one_based_start", -1)) != A04_START + 1:
        raise RuntimeError("A04 global start rank mismatch")
    if int(pkg.get("source_global_rank_one_based_stop", -1)) != A04_STOP:
        raise RuntimeError("A04 global stop rank mismatch")
    if [int(x["rank"]) for x in manifest] != list(range(1, A04_COUNT + 1)):
        raise RuntimeError("A04 package rank sequence mismatch")
    if [int(x["source_global_rank_one_based"]) for x in manifest] != list(range(A04_START + 1, A04_STOP + 1)):
        raise RuntimeError("A04 source global rank sequence mismatch")
    ids = [str(x["match_id"]) for x in manifest]
    ids_sha = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    if ids_sha != pkg.get("ids_sha256"):
        raise RuntimeError("A04 ids sha mismatch")
    comp_file = {int(x["match_id"]): x["competition_file"] for x in manifest}
    matches = [json.loads(line) for line in z.read("matches.jsonl").decode().splitlines() if line]
    events = {
        int(Path(name).stem): json.loads(z.read(name))
        for name in z.namelist()
        if name.startswith("events/") and name.endswith(".json")
    }
    manifest_ids = {int(x["match_id"]) for x in manifest}
    if len(matches) != A04_COUNT or {int(x["wyId"]) for x in matches} != manifest_ids:
        raise RuntimeError("A04 match payload coverage mismatch")
    if len(events) != A04_COUNT or set(events) != manifest_ids:
        raise RuntimeError("A04 event payload coverage mismatch")
    return comp_file, matches, events, ids_sha, [int(x) for x in ids]


def _expected_a04_ids(matches_zip: Path) -> list[int]:
    z = zipfile.ZipFile(matches_zip)
    ranked = []
    for name in z.namelist():
        if not name.startswith("matches_") or not name.endswith(".json"):
            continue
        for match in json.loads(z.read(name)):
            mid = int(match["wyId"])
            ranked.append((hashlib.sha256(f"{SEED}|{mid}".encode()).hexdigest(), mid))
    ranked.sort(key=lambda x: x[0])
    if len(ranked) < A04_STOP:
        raise RuntimeError(f"source universe too small {len(ranked)}")
    return [mid for _, mid in ranked[A04_START:A04_STOP]]


def _merge_four(a01: Path, a02: Path, a03: Path, a04: Path, matches_zip: Path):
    cf123, m123, e123, _, a03_sha = r3._merge_three(c69, a01, a02, a03, matches_zip)
    cf4, m4, e4, a04_sha, a04_ids = _load_a04(a04)
    if a04_ids != _expected_a04_ids(matches_zip):
        raise RuntimeError("A04 ids do not equal frozen global ranks 1201-1600")
    ids123 = {int(x["wyId"]) for x in m123}
    ids4 = {int(x["wyId"]) for x in m4}
    if ids123 & ids4:
        raise RuntimeError("A04 overlaps A01/A02/A03")
    if len(ids123 | ids4) != 1600:
        raise RuntimeError("A01+A02+A03+A04 union count mismatch")
    union_sha = hashlib.sha256(("\n".join(map(str, sorted(ids123 | ids4))) + "\n").encode()).hexdigest()
    return {**cf123, **cf4}, m123 + m4, {**e123, **e4}, union_sha, a03_sha, a04_sha, m4


def _match_date(raw: dict):
    dt = pd.to_datetime(raw.get("dateutc"), utc=True, errors="raise")
    return dt.date()


def _fresh_split(a04_matches: list[dict]) -> dict:
    rows = [(int(m["wyId"]), _match_date(m)) for m in a04_matches]
    counts = pd.Series([d for _, d in rows]).value_counts().sort_index()
    candidates = []
    cumulative = 0
    for d, n in counts.items():
        cumulative += int(n)
        confirm = A04_COUNT - cumulative
        if cumulative >= MIN_RAW_PARTITION and confirm >= MIN_RAW_PARTITION:
            candidates.append((abs(cumulative - A04_COUNT / 2), d, cumulative, confirm))
    if not candidates:
        raise RuntimeError("no legal A04 no-same-date split satisfying raw partition minimum")
    _, boundary, cal_n, conf_n = min(candidates, key=lambda x: (x[0], x[1]))
    cal_ids = {mid for mid, d in rows if d <= boundary}
    conf_ids = {mid for mid, d in rows if d > boundary}
    if cal_ids & conf_ids or len(cal_ids | conf_ids) != A04_COUNT:
        raise RuntimeError("A04 split identity failure")
    return {
        "boundary_utc_date": str(boundary),
        "calibration_raw_matches": int(cal_n),
        "confirmation_raw_matches": int(conf_n),
        "calibration_ids": cal_ids,
        "confirmation_ids": conf_ids,
    }


def _scoreability_by_date(a04_prematch: pd.DataFrame, eligible: pd.DataFrame, minute: pd.DataFrame):
    calipers = dict(c69.MATCH_CALIPERS)
    cache = {}
    for d in sorted(a04_prematch["date"].unique()):
        prior_target = eligible[eligible["date"] < d].copy()
        meta, cert = r2._optimal_pairs(prior_target, f"c070e-scoreability-{d}", calipers)
        structural = minute[(minute["date"] < d) & minute["include_structural"]].copy()
        classes = sorted(set(int(x) for x in structural["outcome"].unique())) if len(structural) else []
        cache[d] = {
            "prior_pairs": int(len(meta)),
            "prior_certificate": int(cert),
            "prior_structural_rows": int(len(structural)),
            "prior_structural_classes": classes,
            "scoreable": bool(len(meta) >= MIN_PRIOR_TRAIN_PAIRS and cert == len(meta) and classes == c70c.ALL_CLASSES),
        }
    return cache


def _predict_pairs_prequential(pairs: pd.DataFrame, eligible: pd.DataFrame, minute: pd.DataFrame, label: str):
    if pairs.empty:
        return np.array([]), np.array([]), np.array([]), {}
    p_inc = pd.Series(index=pairs.index, dtype=float)
    q_markov = pd.Series(index=pairs.index, dtype=float)
    q_semi = pd.Series(index=pairs.index, dtype=float)
    per_date = {}
    calipers = dict(c69.MATCH_CALIPERS)
    for d in sorted(pairs["date"].unique()):
        idx = pairs.index[pairs["date"] == d]
        prior_target = eligible[eligible["date"] < d].copy()
        meta, cert = r2._optimal_pairs(prior_target, f"{label}-train-{d}", calipers)
        if len(meta) < MIN_PRIOR_TRAIN_PAIRS or cert != len(meta):
            raise RuntimeError(f"{label} prior training coverage failed date={d} pairs={len(meta)} cert={cert}")
        train_pairs = c70c._pair_rows(prior_target, meta)
        test_rows = pairs.loc[idx].copy()
        inc = c70c._fit_incumbent(train_pairs, test_rows)

        structural = minute[(minute["date"] < d) & minute["include_structural"]].copy()
        if sorted(set(int(x) for x in structural["outcome"].unique())) != c70c.ALL_CLASSES:
            raise RuntimeError(f"{label} structural class coverage failed date={d}")
        markov = c70c._fit_multinomial(structural, c70c.MARKOV_FEATURES)
        semi = c70c._fit_multinomial(structural, c70c.SEMIMARKOV_FEATURES)
        qm = []
        qs = []
        for _, row in test_rows.iterrows():
            a, _ = c70c._simulate_q(markov, float(row["lambda_home"]), float(row["lambda_away"]), c70c.MARKOV_FEATURES)
            b, _ = c70c._simulate_q(semi, float(row["lambda_home"]), float(row["lambda_away"]), c70c.SEMIMARKOV_FEATURES)
            qm.append(a)
            qs.append(b)
        p_inc.loc[idx] = inc
        q_markov.loc[idx] = np.asarray(qm, float)
        q_semi.loc[idx] = np.asarray(qs, float)
        per_date[str(d)] = {
            "scored_rows": int(len(idx)),
            "prior_training_pairs": int(len(meta)),
            "prior_structural_rows": int(len(structural)),
        }
    return (
        p_inc.loc[pairs.index].to_numpy(float),
        q_markov.loc[pairs.index].to_numpy(float),
        q_semi.loc[pairs.index].to_numpy(float),
        per_date,
    )


def _fit_alpha(frame: pd.DataFrame, p_inc: np.ndarray, q_markov: np.ndarray, q_semi: np.ndarray):
    y = frame["y"].to_numpy(int)
    shift = _logit(q_semi) - _logit(q_markov)
    base_logit = _logit(p_inc)

    def objective(alpha: float) -> float:
        return _binary_log_loss(y, _sigmoid(base_logit + float(alpha) * shift))

    opt = minimize_scalar(objective, bounds=(0.0, 1.0), method="bounded", options={"xatol": 1e-8})
    if not opt.success:
        raise RuntimeError(f"alpha optimizer failed: {opt.message}")
    alpha = float(np.clip(opt.x, 0.0, 1.0))
    candidate = _sigmoid(base_logit + alpha * shift)
    return alpha, candidate, shift, {
        "success": bool(opt.success),
        "objective_log_loss": float(opt.fun),
        "iterations": int(getattr(opt, "nit", -1)),
        "alpha_domain": [0.0, 1.0],
        "xatol": 1e-8,
    }


def _pair_bootstrap(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray) -> dict:
    y = frame["y"].to_numpy(int)
    p0 = np.clip(np.asarray(p0, float), 1e-12, 1 - 1e-12)
    p1 = np.clip(np.asarray(p1, float), 1e-12, 1 - 1e-12)
    l0 = -(y * np.log(p0) + (1 - y) * np.log(1 - p0))
    l1 = -(y * np.log(p1) + (1 - y) * np.log(1 - p1))
    t = frame[["pair_id"]].copy()
    t["delta"] = l1 - l0
    arr = t.groupby("pair_id")["delta"].mean().to_numpy(float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    n = len(arr)
    for i in range(BOOT_REPS):
        sims[i] = float(np.mean(arr[rng.integers(0, n, size=n)]))
    return {
        "pair_count": int(n),
        "mean_delta_log_loss": float(arr.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": BOOT_REPS,
        "seed": BOOT_SEED,
    }


def _chrono_halves(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray) -> dict:
    tmp = frame[["pair_id", "dt", "y"]].copy()
    tmp["p0"] = np.asarray(p0, float)
    tmp["p1"] = np.asarray(p1, float)
    pair_time = tmp.groupby("pair_id")["dt"].max().sort_values()
    pair_ids = list(pair_time.index)
    cut = len(pair_ids) // 2
    first = set(pair_ids[:cut])
    second = set(pair_ids[cut:])
    out = {}
    for name, ids in (("first_half", first), ("second_half", second)):
        part = tmp[tmp["pair_id"].isin(ids)]
        d = _binary_log_loss(part["y"].to_numpy(int), part["p1"].to_numpy(float)) - _binary_log_loss(
            part["y"].to_numpy(int), part["p0"].to_numpy(float)
        )
        out[name] = {"pairs": int(len(ids)), "rows": int(len(part)), "delta_log_loss": float(d)}
    return out


def _write_stop(out: Path, source: dict, status: str, verdict: str, extra: dict):
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "verdict": verdict,
        "source": source,
        **extra,
        "boundary": {
            "a04_opened": True,
            "a05_or_later_opened": False,
            "protected_samples_used": False,
            "formal_weight": 0,
            "formal_promotion_allowed": False,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def run(a01: Path, a02: Path, a03: Path, a04: Path, matches_zip: Path, out: Path) -> None:
    comp_file, matches, events, union_sha, a03_sha, a04_sha, a04_matches = _merge_four(
        a01, a02, a03, a04, matches_zip
    )
    split = _fresh_split(a04_matches)
    regular, skipped = c70c._regular_rows(comp_file, matches)
    prematch = c70c._build_prematch(regular)
    minute, minute_diag = c70c._minute_rows(prematch, events)

    eligible = prematch[
        (prematch["hn"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (prematch["an"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & prematch["target"].isin(["D", "OW"])
    ].copy().sort_values(["dt", "match_id"]).reset_index(drop=True)

    a04_ids = split["calibration_ids"] | split["confirmation_ids"]
    a04_prematch = prematch[
        prematch["match_id"].isin(a04_ids)
        & (prematch["hn"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (prematch["an"] >= c69.MIN_PRIOR_TEAM_MATCHES)
    ].copy()
    scoreability = _scoreability_by_date(a04_prematch, eligible, minute)
    scoreable_dates = {d for d, x in scoreability.items() if x["scoreable"]}
    scoreable_ids = set(a04_prematch[a04_prematch["date"].isin(scoreable_dates)]["match_id"].astype(int))

    source = {
        "packages": ["A01", "A02", "A03", "A04"],
        "source_matches": int(len(matches)),
        "union_ids_sha256_sorted": union_sha,
        "a03_ids_sha256_ordered": a03_sha,
        "a04_ids_sha256_ordered": a04_sha,
        "a04_rank_window": [1201, 1600],
        "regular_matches": int(len(regular)),
        "skipped_nonregular_matches": int(skipped),
        "eligible_rows_all_packages": int(len(eligible)),
        "minute_diagnostics": minute_diag,
        "fresh_split": {
            "boundary_utc_date": split["boundary_utc_date"],
            "calibration_raw_matches": split["calibration_raw_matches"],
            "confirmation_raw_matches": split["confirmation_raw_matches"],
        },
        "a04_prematch_rows_with_min_team_history": int(len(a04_prematch)),
        "a04_scoreable_rows_before_target_filter": int(len(scoreable_ids)),
        "scoreability_by_date": {str(k): v for k, v in scoreability.items()},
    }

    cal_target = eligible[
        eligible["match_id"].isin(split["calibration_ids"] & scoreable_ids)
    ].copy()
    cal_meta, cal_cert = r2._optimal_pairs(cal_target, "c070e-a04-calibration", dict(c69.MATCH_CALIPERS))
    if len(cal_meta) != cal_cert:
        raise RuntimeError("calibration max-cardinality certificate mismatch")
    if len(cal_meta) < MIN_CAL_PAIRS:
        _write_stop(
            out,
            source,
            "STOP_CALIBRATION_COVERAGE",
            "C070E_FRESH_CONFIRMATION_NOT_ESTABLISHED",
            {"calibration": {"target_rows": int(len(cal_target)), "matched_pairs": int(len(cal_meta)), "certificate": int(cal_cert), "minimum": MIN_CAL_PAIRS}},
        )
        return

    cal_pairs = c70c._pair_rows(cal_target, cal_meta)
    cal_inc, cal_markov, cal_semi, cal_dates = _predict_pairs_prequential(cal_pairs, eligible, minute, "c070e-cal")
    alpha, cal_candidate, cal_shift, alpha_diag = _fit_alpha(cal_pairs, cal_inc, cal_markov, cal_semi)
    cal_inc_m = c70d._metric(cal_pairs, cal_inc)
    cal_cand_m = c70d._metric(cal_pairs, cal_candidate)
    calibration = {
        "target_rows": int(len(cal_target)),
        "matched_pairs": int(len(cal_meta)),
        "certificate": int(cal_cert),
        "incumbent": cal_inc_m,
        "candidate": cal_cand_m,
        "candidate_minus_incumbent": c70d._delta(cal_cand_m, cal_inc_m),
        "alpha_hat": alpha,
        "alpha_fit": alpha_diag,
        "duration_logodds_shift": {
            "mean": float(np.mean(cal_shift)),
            "std": float(np.std(cal_shift, ddof=0)),
            "min": float(np.min(cal_shift)),
            "max": float(np.max(cal_shift)),
        },
        "prediction_by_date": cal_dates,
    }
    if alpha <= 1e-8:
        _write_stop(
            out,
            source,
            "STOP_NO_CALIBRATION_SUPPORT",
            "C070E_FRESH_CONFIRMATION_NOT_ESTABLISHED",
            {"calibration": calibration, "confirmation_scored": False},
        )
        return

    conf_target = eligible[
        eligible["match_id"].isin(split["confirmation_ids"] & scoreable_ids)
    ].copy()
    conf_meta, conf_cert = r2._optimal_pairs(conf_target, "c070e-a04-confirmation", dict(c69.MATCH_CALIPERS))
    if len(conf_meta) != conf_cert:
        raise RuntimeError("confirmation max-cardinality certificate mismatch")
    if len(conf_meta) < MIN_CONFIRM_PAIRS:
        _write_stop(
            out,
            source,
            "STOP_CONFIRMATION_COVERAGE",
            "C070E_FRESH_CONFIRMATION_NOT_ESTABLISHED",
            {
                "calibration": calibration,
                "confirmation": {"target_rows": int(len(conf_target)), "matched_pairs": int(len(conf_meta)), "certificate": int(conf_cert), "minimum": MIN_CONFIRM_PAIRS, "scored": False},
            },
        )
        return

    conf_pairs = c70c._pair_rows(conf_target, conf_meta)
    conf_inc, conf_markov, conf_semi, conf_dates = _predict_pairs_prequential(conf_pairs, eligible, minute, "c070e-confirm")
    conf_candidate, conf_shift = _candidate(conf_inc, conf_markov, conf_semi, alpha)
    mi = c70d._metric(conf_pairs, conf_inc)
    mc = c70d._metric(conf_pairs, conf_candidate)
    delta = c70d._delta(mc, mi)
    boot = _pair_bootstrap(conf_pairs, conf_inc, conf_candidate)
    halves = _chrono_halves(conf_pairs, conf_inc, conf_candidate)
    breakthrough = bool(
        alpha > 0
        and delta["log_loss"] < 0
        and boot["ci90_high"] < 0
        and delta["brier"] <= 0
        and halves["first_half"]["delta_log_loss"] <= 0
        and halves["second_half"]["delta_log_loss"] <= 0
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "FRESH_A04_CONFIRMATION_COMPLETE",
        "verdict": "C070E_FRESH_CONFIRMATION_BREAKTHROUGH" if breakthrough else "C070E_FRESH_CONFIRMATION_NOT_ESTABLISHED",
        "source": source,
        "calibration": calibration,
        "confirmation": {
            "target_rows": int(len(conf_target)),
            "matched_pairs": int(len(conf_meta)),
            "certificate": int(conf_cert),
            "alpha_frozen_from_calibration": alpha,
            "incumbent": mi,
            "candidate": mc,
            "candidate_minus_incumbent": delta,
            "pair_bootstrap": boot,
            "chronological_halves": halves,
            "duration_logodds_shift": {
                "mean": float(np.mean(conf_shift)),
                "std": float(np.std(conf_shift, ddof=0)),
                "min": float(np.min(conf_shift)),
                "max": float(np.max(conf_shift)),
            },
            "calibration_diagnostic_incumbent": c70d._calibration_diagnostic(conf_pairs["y"].to_numpy(int), conf_inc),
            "calibration_diagnostic_candidate": c70d._calibration_diagnostic(conf_pairs["y"].to_numpy(int), conf_candidate),
            "prediction_by_date": conf_dates,
        },
        "breakthrough_gate": {
            "calibration_pairs_ge_25": bool(len(cal_meta) >= MIN_CAL_PAIRS),
            "confirmation_pairs_ge_25": bool(len(conf_meta) >= MIN_CONFIRM_PAIRS),
            "alpha_gt_zero": bool(alpha > 0),
            "confirmation_delta_log_loss_lt_zero": bool(delta["log_loss"] < 0),
            "bootstrap_ci90_high_lt_zero": bool(boot["ci90_high"] < 0),
            "confirmation_delta_brier_le_zero": bool(delta["brier"] <= 0),
            "first_half_delta_log_loss_le_zero": bool(halves["first_half"]["delta_log_loss"] <= 0),
            "second_half_delta_log_loss_le_zero": bool(halves["second_half"]["delta_log_loss"] <= 0),
            "all_pass": breakthrough,
        },
        "boundary": {
            "a04_opened": True,
            "a05_or_later_opened": False,
            "protected_samples_used": False,
            "formal_weight": 0,
            "formal_promotion_allowed": False,
            "a01_a02_a03_outer_test_labels_used_for_alpha_fit": False,
            "only_learned_integration_parameter": "alpha_hat",
            "learned_intercept": False,
            "secondary_calibrator": False,
            "blend_search": False,
            "post_confirmation_rescue_allowed": False,
            "claim_scope": "fresh A04 research confirmation; formal promotion still requires separate protected gate"
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--a01", required=True)
    ap.add_argument("--a02", required=True)
    ap.add_argument("--a03", required=True)
    ap.add_argument("--a04", required=True)
    ap.add_argument("--matches-zip", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run(Path(args.a01), Path(args.a02), Path(args.a03), Path(args.a04), Path(args.matches_zip), Path(args.out))
