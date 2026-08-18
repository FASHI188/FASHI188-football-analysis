from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import evaluate_c069_matched_pair_draw_state_r1 as c69
import evaluate_c069_r2_maxcard_coverage as r2
import evaluate_c069_r3_a03_expanded_dev as r3
import run_c069_r3_a03_expanded_dev as r3run


SCHEMA_VERSION = "C070C_SEMIMARKOV_GENERATOR_V1"
MARKOV_FEATURES = [
    "log_lambda_home",
    "log_lambda_away",
    "minute_frac",
    "minute_frac_sq",
    "score_diff_clip",
    "is_tied",
    "home_ahead",
    "away_ahead",
]
SEMIMARKOV_FEATURES = MARKOV_FEATURES + [
    "duration_frac",
    "duration_frac_sq",
    "duration_if_tied",
    "duration_if_home_ahead",
    "duration_if_away_ahead",
]
CLASS_NO = 0
CLASS_HOME = 1
CLASS_AWAY = 2
ALL_CLASSES = [CLASS_NO, CLASS_HOME, CLASS_AWAY]
LOGISTIC_C = 0.1
STRUCT_BOOT_REPS = 2000
STRUCT_BOOT_SEED = 7103
DRAW_BOOT_REPS = 2000
DRAW_BOOT_SEED = 7003
SIM_DIFF_MIN = -10
SIM_DIFF_MAX = 10
FOLDS = {
    "fold_1": ("2018-01-13", "2018-01-13", "2018-02-23"),
    "fold_2": ("2018-02-23", "2018-02-23", "2018-04-08"),
    "fold_3": ("2018-04-08", "2018-04-08", None),
}
EXPECTED_PAIRS = {
    "fold_1": (75, 25),
    "fold_2": (127, 23),
    "fold_3": (169, 33),
}
EXPECTED_SOURCE = {
    "source_matches": 1200,
    "regular_matches": 1192,
    "skipped_nonregular_matches": 8,
    "eligible_rows": 567,
    "eligible_draws": 230,
    "eligible_onegoal_wins": 337,
}
EXPECTED_INCUMBENT_LL = 0.6938306635015128
EXPECTED_INCUMBENT_BRIER = 0.25034155323321833
EXPECTED_INCUMBENT_PAIR_ACC = 0.4691358024691358


def _event_reg_minute(event: dict) -> float | None:
    period = event.get("matchPeriod")
    if period not in {"1H", "2H"}:
        return None
    sec = float(event.get("eventSec", 0.0) or 0.0)
    within = max(0.0, sec / 60.0)
    # Compress first-half stoppage into the final first-half regulation bin and
    # second-half stoppage into the final settlement bin. This preserves event
    # order and includes stoppage without using realized stoppage length.
    within = min(within, 44.999999)
    return within if period == "1H" else 45.0 + within


def _tags(event: dict) -> set[int]:
    return {int(t["id"]) for t in event.get("tags", []) if "id" in t}


def _scoring_team(event: dict, home: int, away: int) -> int | None:
    if event.get("eventName") == r3run.MARKER_EVENT_NAME:
        tid = int(event.get("teamId", 0) or 0)
        return tid if tid in {home, away} else None
    period = event.get("matchPeriod")
    if period not in {"1H", "2H"}:
        return None
    tid = int(event.get("teamId", 0) or 0)
    if tid not in {home, away}:
        return None
    tags = _tags(event)
    if 102 in tags:
        return away if tid == home else home
    if 101 in tags and event.get("eventName") in {"Shot", "Free Kick"}:
        return tid
    return None


def _regular_rows(comp_file: dict, matches: list[dict]) -> tuple[pd.DataFrame, int]:
    rows = []
    skipped = 0
    for raw in matches:
        row = c69._regular_match_row(raw, comp_file.get(int(raw["wyId"])))
        if row is None:
            skipped += 1
            continue
        rows.append(row)
    return (
        pd.DataFrame(rows).sort_values(["dt", "match_id"]).reset_index(drop=True),
        int(skipped),
    )


def _build_prematch(frame: pd.DataFrame) -> pd.DataFrame:
    team = defaultdict(lambda: defaultdict(float))
    comp_goals = defaultdict(list)
    out = []
    for date, group in frame.groupby("date", sort=True):
        for _, r in group.iterrows():
            home, away, cid = int(r["home"]), int(r["away"]), int(r["cid"])
            hist = comp_goals[cid]
            lgh = float(np.mean([x[0] for x in hist])) if hist else 1.4
            lga = float(np.mean([x[1] for x in hist])) if hist else 1.1
            lm = max((lgh + lga) / 2.0, 0.05)

            def rates(tid: int):
                acc = team[tid]
                n = int(acc.get("matches", 0))
                gf = float(acc.get("gf", 0.0)) / n if n else lm
                ga = float(acc.get("ga", 0.0)) / n if n else lm
                return n, gf, ga

            hn, hgf0, hga0 = rates(home)
            an, agf0, aga0 = rates(away)
            hgf = (hgf0 * hn + c69.PRIOR_EQ_MATCHES * lm) / (hn + c69.PRIOR_EQ_MATCHES)
            hga = (hga0 * hn + c69.PRIOR_EQ_MATCHES * lm) / (hn + c69.PRIOR_EQ_MATCHES)
            agf = (agf0 * an + c69.PRIOR_EQ_MATCHES * lm) / (an + c69.PRIOR_EQ_MATCHES)
            aga = (aga0 * an + c69.PRIOR_EQ_MATCHES * lm) / (an + c69.PRIOR_EQ_MATCHES)
            lh = float(np.clip(lgh * (hgf / lm) * (aga / lm), *c69.LAMBDA_CLIP))
            la = float(np.clip(lga * (agf / lm) * (hga / lm), *c69.LAMBDA_CLIP))
            p_home, p_draw, p_away, p_one, q = c69._score_matrix(lh, la)
            q = float(np.clip(q, 1e-6, 1 - 1e-6))
            gd = int(r["hg"]) - int(r["ag"])
            target = "D" if gd == 0 else "OW" if abs(gd) == 1 else "OTHER"
            out.append(
                {
                    **r.to_dict(),
                    "hn": hn,
                    "an": an,
                    "lambda_home": lh,
                    "lambda_away": la,
                    "lambda_total": lh + la,
                    "pH": p_home,
                    "pD": p_draw,
                    "pA": p_away,
                    "p_onegoal": p_one,
                    "q_draw_cond": q,
                    "baseline_logit": math.log(q / (1.0 - q)),
                    "abs_ha_gap": abs(p_home - p_away),
                    "target": target,
                    "y": 1 if target == "D" else 0,
                }
            )
        # PIT: update only after all matches on the date were predicted.
        for _, r in group.iterrows():
            home, away, cid = int(r["home"]), int(r["away"]), int(r["cid"])
            for tid, gf, ga in (
                (home, float(r["hg"]), float(r["ag"])),
                (away, float(r["ag"]), float(r["hg"])),
            ):
                team[tid]["matches"] += 1.0
                team[tid]["gf"] += gf
                team[tid]["ga"] += ga
            comp_goals[cid].append((float(r["hg"]), float(r["ag"])))
    return pd.DataFrame(out).sort_values(["dt", "match_id"]).reset_index(drop=True)


def _minute_feature_dict(lh: float, la: float, minute: int, diff: int, duration: int) -> dict:
    diff_clip = float(np.clip(diff, -3, 3))
    tied = 1.0 if diff == 0 else 0.0
    home_ahead = 1.0 if diff > 0 else 0.0
    away_ahead = 1.0 if diff < 0 else 0.0
    mf = (float(minute) + 0.5) / 90.0
    df = min(float(duration), 90.0) / 90.0
    return {
        "log_lambda_home": math.log(max(float(lh), 1e-9)),
        "log_lambda_away": math.log(max(float(la), 1e-9)),
        "minute_frac": mf,
        "minute_frac_sq": mf * mf,
        "score_diff_clip": diff_clip,
        "is_tied": tied,
        "home_ahead": home_ahead,
        "away_ahead": away_ahead,
        "duration_frac": df,
        "duration_frac_sq": df * df,
        "duration_if_tied": df * tied,
        "duration_if_home_ahead": df * home_ahead,
        "duration_if_away_ahead": df * away_ahead,
    }


def _minute_rows(
    prematch: pd.DataFrame,
    events: dict[int, list[dict]],
) -> tuple[pd.DataFrame, dict]:
    rows = []
    fallback_matches = []
    multi_score_bins = 0
    score_bins = 0
    for _, match in prematch.iterrows():
        match_dict = match.to_dict()
        mid = int(match["match_id"])
        home, away = int(match["home"]), int(match["away"])
        canonical, fallback_count = r3run._canonicalize_events(match_dict, events[mid])
        if fallback_count:
            fallback_matches.append(mid)
        goals_by_bin: dict[int, list[int]] = defaultdict(list)
        gh = ga = 0
        score_events = []
        for idx, event in enumerate(canonical):
            scorer = _scoring_team(event, home, away)
            if scorer is None:
                continue
            minute = _event_reg_minute(event)
            if minute is None:
                continue
            b = min(89, max(0, int(math.floor(minute))))
            eid = int(event.get("id", idx))
            score_events.append((b, minute, eid, idx, scorer))
        score_events.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        for b, _, _, _, scorer in score_events:
            goals_by_bin[b].append(scorer)
            if scorer == home:
                gh += 1
            else:
                ga += 1
        if (gh, ga) != (int(match["hg"]), int(match["ag"])):
            raise RuntimeError(
                f"minute score mismatch match={mid} canonical={gh}-{ga} official={match['hg']}-{match['ag']}"
            )

        diff = 0
        duration = 0
        for minute in range(90):
            feats = _minute_feature_dict(
                float(match["lambda_home"]),
                float(match["lambda_away"]),
                minute,
                diff,
                duration,
            )
            goals = goals_by_bin.get(minute, [])
            if len(goals) == 0:
                outcome = CLASS_NO
                include = True
            elif len(goals) == 1:
                outcome = CLASS_HOME if goals[0] == home else CLASS_AWAY
                include = True
                score_bins += 1
            else:
                outcome = -1
                include = False
                multi_score_bins += 1
                score_bins += 1
            rows.append(
                {
                    "match_id": mid,
                    "date": match["date"],
                    "dt": match["dt"],
                    "cid": int(match["cid"]),
                    "minute": minute,
                    "include_structural": include,
                    "outcome": outcome,
                    **feats,
                }
            )
            if goals:
                for scorer in goals:
                    diff += 1 if scorer == home else -1
                duration = 0
            else:
                duration += 1
    frame = pd.DataFrame(rows).sort_values(["dt", "match_id", "minute"]).reset_index(drop=True)
    diag = {
        "minute_rows_total": int(len(frame)),
        "included_structural_rows": int(frame["include_structural"].sum()),
        "multi_score_bins_excluded": int(multi_score_bins),
        "score_bins": int(score_bins),
        "multi_score_bin_rate_of_all_bins": float(multi_score_bins / len(frame)) if len(frame) else 0.0,
        "score_fallback_matches": sorted(set(fallback_matches)),
    }
    return frame, diag


def _fit_multinomial(train: pd.DataFrame, features: list[str]):
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=LOGISTIC_C,
            max_iter=5000,
            class_weight=None,
            random_state=0,
        ),
    )
    model.fit(train[features], train["outcome"].to_numpy(int))
    classes = list(model.named_steps["logisticregression"].classes_)
    if classes != ALL_CLASSES:
        raise RuntimeError(f"multinomial classes mismatch {classes}")
    return model


def _struct_metric(frame: pd.DataFrame, p: np.ndarray) -> dict:
    y = frame["outcome"].to_numpy(int)
    p = np.clip(np.asarray(p, float), 1e-15, 1.0)
    out = {
        "rows": int(len(frame)),
        "matches": int(frame["match_id"].nunique()),
        "log_loss": float(log_loss(y, p, labels=ALL_CLASSES)),
    }
    for cls, name in [(CLASS_NO, "no_goal"), (CLASS_HOME, "home_goal"), (CLASS_AWAY, "away_goal")]:
        mask = y == cls
        out[f"{name}_rows"] = int(mask.sum())
        out[f"{name}_mean_nll"] = float(-np.log(p[mask, cls]).mean()) if mask.any() else None
    return out


def _struct_bootstrap(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray) -> dict:
    y = frame["outcome"].to_numpy(int)
    idx = np.arange(len(y))
    l0 = -np.log(np.clip(p0[idx, y], 1e-15, 1.0))
    l1 = -np.log(np.clip(p1[idx, y], 1e-15, 1.0))
    t = frame[["match_id"]].copy()
    t["delta"] = l1 - l0
    per_match = t.groupby("match_id")["delta"].mean().to_numpy(float)
    rng = np.random.default_rng(STRUCT_BOOT_SEED)
    sims = np.empty(STRUCT_BOOT_REPS, dtype=float)
    n = len(per_match)
    for i in range(STRUCT_BOOT_REPS):
        sims[i] = float(np.mean(per_match[rng.integers(0, n, size=n)]))
    return {
        "match_count": int(n),
        "mean_delta_log_loss": float(per_match.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": STRUCT_BOOT_REPS,
        "seed": STRUCT_BOOT_SEED,
    }


def _simulate_q(model, lh: float, la: float, features: list[str]) -> tuple[float, float]:
    states: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for minute in range(90):
        keys = list(states.keys())
        X = pd.DataFrame([
            _minute_feature_dict(lh, la, minute, diff, duration)
            for diff, duration in keys
        ])[features]
        probs = model.predict_proba(X)
        nxt: dict[tuple[int, int], float] = defaultdict(float)
        for i, (diff, duration) in enumerate(keys):
            mass = states[(diff, duration)]
            p_no, p_home, p_away = map(float, probs[i])
            nxt[(diff, min(duration + 1, 90))] += mass * p_no
            nxt[(min(diff + 1, SIM_DIFF_MAX), 0)] += mass * p_home
            nxt[(max(diff - 1, SIM_DIFF_MIN), 0)] += mass * p_away
        states = dict(nxt)
    p0 = float(sum(m for (d, _), m in states.items() if d == 0))
    p1 = float(sum(m for (d, _), m in states.items() if abs(d) == 1))
    denom = p0 + p1
    q = p0 / denom if denom > 1e-15 else 0.5
    cap_mass = float(sum(m for (d, _), m in states.items() if d in {SIM_DIFF_MIN, SIM_DIFF_MAX}))
    return float(np.clip(q, 1e-9, 1 - 1e-9)), cap_mass


def _fit_incumbent(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.1, max_iter=5000, class_weight=None, random_state=0),
    )
    model.fit(train[["baseline_logit"]], train["y"].to_numpy(int))
    return model.predict_proba(test[["baseline_logit"]])[:, 1]


def _draw_metric(frame: pd.DataFrame, p: np.ndarray) -> dict:
    return {
        **c69._metric(frame["y"].to_numpy(int), p),
        "pair_accuracy": c69._pair_accuracy(frame, p),
    }


def _metric_delta(candidate: dict, baseline: dict) -> dict:
    return {
        k: float(candidate[k] - baseline[k])
        for k in ("log_loss", "brier", "auc", "accuracy", "pair_accuracy")
    }


def _draw_bootstrap(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray) -> dict:
    y = frame["y"].to_numpy(int)
    p0 = np.clip(np.asarray(p0, float), 1e-12, 1 - 1e-12)
    p1 = np.clip(np.asarray(p1, float), 1e-12, 1 - 1e-12)
    l0 = -(y*np.log(p0)+(1-y)*np.log(1-p0))
    l1 = -(y*np.log(p1)+(1-y)*np.log(1-p1))
    t = frame[["pair_id"]].copy()
    t["delta"] = l1-l0
    arr = t.groupby("pair_id")["delta"].mean().to_numpy(float)
    rng = np.random.default_rng(DRAW_BOOT_SEED)
    sims = np.empty(DRAW_BOOT_REPS, dtype=float)
    n = len(arr)
    for i in range(DRAW_BOOT_REPS):
        sims[i] = float(np.mean(arr[rng.integers(0, n, size=n)]))
    return {
        "pair_count": int(n),
        "mean_delta_log_loss": float(arr.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": DRAW_BOOT_REPS,
        "seed": DRAW_BOOT_SEED,
    }


def _pair_rows(frame: pd.DataFrame, meta: list[dict]) -> pd.DataFrame:
    return r3._pair_rows(frame, meta)


def _assert_close(name: str, got: float, expected: float, atol: float = 1e-12):
    if not math.isclose(float(got), float(expected), rel_tol=0.0, abs_tol=atol):
        raise RuntimeError(f"{name} mismatch got={got} expected={expected}")


def run(a01: Path, a02: Path, a03: Path, matches_zip: Path, out: Path) -> None:
    comp_file, matches, events, union_sha, a03_sha = r3._merge_three(c69, a01, a02, a03, matches_zip)
    regular, skipped = _regular_rows(comp_file, matches)
    prematch = _build_prematch(regular)
    minute, minute_diag = _minute_rows(prematch, events)

    eligible = prematch[
        (prematch["hn"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (prematch["an"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & prematch["target"].isin(["D", "OW"])
    ].copy().sort_values(["dt", "match_id"]).reset_index(drop=True)

    source = {
        "packages": ["A01", "A02", "A03"],
        "source_matches": int(len(matches)),
        "union_ids_sha256_sorted": union_sha,
        "a03_ids_sha256_ordered": a03_sha,
        "regular_matches": int(len(regular)),
        "skipped_nonregular_matches": int(skipped),
        "eligible_rows": int(len(eligible)),
        "eligible_draws": int((eligible["target"] == "D").sum()),
        "eligible_onegoal_wins": int((eligible["target"] == "OW").sum()),
        "minute_diagnostics": minute_diag,
    }
    for key, expected in EXPECTED_SOURCE.items():
        if int(source[key]) != int(expected):
            raise RuntimeError(f"source integrity {key}={source[key]} expected={expected}")
    if minute_diag["score_fallback_matches"] != [2499781]:
        raise RuntimeError(f"fallback identity mismatch {minute_diag['score_fallback_matches']}")

    folds = {}
    pooled_struct = []
    pooled_p_markov = []
    pooled_p_semi = []
    pooled_draw_rows = []
    pooled_incumbent = []
    pooled_draw_markov = []
    pooled_draw_semi = []
    structural_wins = 0

    calipers = dict(c69.MATCH_CALIPERS)
    for name, (train_end_s, test_start_s, test_end_s) in FOLDS.items():
        train_end = pd.Timestamp(train_end_s).date()
        test_start = pd.Timestamp(test_start_s).date()
        test_end = pd.Timestamp(test_end_s).date() if test_end_s else None

        structural_train = minute[(minute["date"] < train_end) & minute["include_structural"]].copy()
        structural_test = minute[(minute["date"] >= test_start) & minute["include_structural"]].copy()
        if test_end is not None:
            structural_test = structural_test[structural_test["date"] < test_end].copy()
        if structural_train.empty or structural_test.empty:
            raise RuntimeError(f"{name} empty structural split")

        markov = _fit_multinomial(structural_train, MARKOV_FEATURES)
        semi = _fit_multinomial(structural_train, SEMIMARKOV_FEATURES)
        p_markov = markov.predict_proba(structural_test[MARKOV_FEATURES])
        p_semi = semi.predict_proba(structural_test[SEMIMARKOV_FEATURES])
        m_markov = _struct_metric(structural_test, p_markov)
        m_semi = _struct_metric(structural_test, p_semi)
        d_struct = float(m_semi["log_loss"] - m_markov["log_loss"])
        if d_struct < 0:
            structural_wins += 1

        target_train = eligible[eligible["date"] < train_end].copy()
        target_test = eligible[eligible["date"] >= test_start].copy()
        if test_end is not None:
            target_test = target_test[target_test["date"] < test_end].copy()
        train_meta, train_cert = r2._optimal_pairs(target_train, f"{name}-c070c-train", calipers)
        test_meta, test_cert = r2._optimal_pairs(target_test, f"{name}-c070c-test", calipers)
        exp_train, exp_test = EXPECTED_PAIRS[name]
        if (len(train_meta), len(test_meta), train_cert, test_cert) != (exp_train, exp_test, exp_train, exp_test):
            raise RuntimeError(
                f"{name} pair integrity got={len(train_meta)}/{len(test_meta)} cert={train_cert}/{test_cert}"
            )
        train_pairs = _pair_rows(target_train, train_meta)
        test_pairs = _pair_rows(target_test, test_meta)
        p_inc = _fit_incumbent(train_pairs, test_pairs)

        q_markov = []
        q_semi = []
        cap_markov = []
        cap_semi = []
        for _, row in test_pairs.iterrows():
            qm, cm = _simulate_q(markov, float(row["lambda_home"]), float(row["lambda_away"]), MARKOV_FEATURES)
            qs, cs = _simulate_q(semi, float(row["lambda_home"]), float(row["lambda_away"]), SEMIMARKOV_FEATURES)
            q_markov.append(qm); q_semi.append(qs)
            cap_markov.append(cm); cap_semi.append(cs)
        q_markov = np.asarray(q_markov, float)
        q_semi = np.asarray(q_semi, float)
        dm = _draw_metric(test_pairs, q_markov)
        ds = _draw_metric(test_pairs, q_semi)
        di = _draw_metric(test_pairs, p_inc)

        folds[name] = {
            "structural": {
                "train_rows": int(len(structural_train)),
                "train_matches": int(structural_train["match_id"].nunique()),
                "test_rows": int(len(structural_test)),
                "test_matches": int(structural_test["match_id"].nunique()),
                "markov": m_markov,
                "semimarkov": m_semi,
                "delta_log_loss_semimarkov_minus_markov": d_struct,
            },
            "derived_draw": {
                "train_pairs": int(len(train_meta)),
                "test_pairs": int(len(test_meta)),
                "train_maximum_cardinality_certificate": int(train_cert),
                "test_maximum_cardinality_certificate": int(test_cert),
                "incumbent_c069_baseline": di,
                "markov_generator": dm,
                "semimarkov_generator": ds,
                "semimarkov_minus_markov": _metric_delta(ds, dm),
                "semimarkov_minus_incumbent": _metric_delta(ds, di),
                "mean_cap_mass_markov": float(np.mean(cap_markov)),
                "mean_cap_mass_semimarkov": float(np.mean(cap_semi)),
            },
        }
        pooled_struct.append(structural_test)
        pooled_p_markov.append(p_markov)
        pooled_p_semi.append(p_semi)
        pooled_draw_rows.append(test_pairs)
        pooled_incumbent.append(p_inc)
        pooled_draw_markov.append(q_markov)
        pooled_draw_semi.append(q_semi)

    all_struct = pd.concat(pooled_struct, ignore_index=True)
    p_markov = np.vstack(pooled_p_markov)
    p_semi = np.vstack(pooled_p_semi)
    m_markov = _struct_metric(all_struct, p_markov)
    m_semi = _struct_metric(all_struct, p_semi)
    struct_delta = float(m_semi["log_loss"] - m_markov["log_loss"])
    struct_boot = _struct_bootstrap(all_struct, p_markov, p_semi)

    all_draw = pd.concat(pooled_draw_rows, ignore_index=True)
    p_inc = np.concatenate(pooled_incumbent)
    q_markov = np.concatenate(pooled_draw_markov)
    q_semi = np.concatenate(pooled_draw_semi)
    mi = _draw_metric(all_draw, p_inc)
    mm = _draw_metric(all_draw, q_markov)
    ms = _draw_metric(all_draw, q_semi)
    ds_m = _metric_delta(ms, mm)
    ds_i = _metric_delta(ms, mi)
    draw_boot = _draw_bootstrap(all_draw, q_markov, q_semi)

    _assert_close("incumbent pooled LL", mi["log_loss"], EXPECTED_INCUMBENT_LL)
    _assert_close("incumbent pooled Brier", mi["brier"], EXPECTED_INCUMBENT_BRIER)
    _assert_close("incumbent pooled pair_accuracy", mi["pair_accuracy"], EXPECTED_INCUMBENT_PAIR_ACC)

    signal = bool(
        struct_delta < 0
        and struct_boot["ci90_high"] < 0
        and structural_wins >= 2
        and ds_m["log_loss"] <= 0
        and ds_m["pair_accuracy"] >= 0
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "POSTVIEW_C070C_STRUCTURAL_DEVELOPMENT_COMPLETE",
        "verdict": (
            "C070C_SEMIMARKOV_STRUCTURAL_DEVELOPMENT_SIGNAL"
            if signal else "C070C_DURATION_REPRESENTATION_STABLE_INCREMENT_NOT_ESTABLISHED"
        ),
        "source": source,
        "contract_echo": {
            "markov_features": MARKOV_FEATURES,
            "semimarkov_features": SEMIMARKOV_FEATURES,
            "logistic_C": LOGISTIC_C,
            "minute_bins": 90,
            "simulation_diff_cap": [SIM_DIFF_MIN, SIM_DIFF_MAX],
            "structural_bootstrap_reps": STRUCT_BOOT_REPS,
            "structural_bootstrap_seed": STRUCT_BOOT_SEED,
            "draw_bootstrap_reps": DRAW_BOOT_REPS,
            "draw_bootstrap_seed": DRAW_BOOT_SEED,
            "fixed_fold_dates": FOLDS,
        },
        "folds": folds,
        "pooled_structural": {
            "markov": m_markov,
            "semimarkov": m_semi,
            "delta_log_loss_semimarkov_minus_markov": struct_delta,
            "fold_logloss_wins": int(structural_wins),
            "match_bootstrap": struct_boot,
        },
        "pooled_derived_draw": {
            "test_rows": int(len(all_draw)),
            "test_pairs": int(all_draw["pair_id"].nunique()),
            "incumbent_c069_baseline": mi,
            "markov_generator": mm,
            "semimarkov_generator": ms,
            "semimarkov_minus_markov": ds_m,
            "semimarkov_minus_incumbent": ds_i,
            "pair_bootstrap_semimarkov_minus_markov": draw_boot,
        },
        "development_signal": signal,
        "boundary": {
            "post_view_development_structural": True,
            "protected_samples_used": False,
            "new_A_packages_opened": [],
            "formal_weight": 0,
            "scientific_pass": False,
            "confirmation_pass": False,
            "formal_promotion_allowed": False,
            "failed_c070a_features_used": False,
            "failed_c070b_features_used": False,
            "posthoc_probability_calibration_performed": False,
            "model_fitting_performed": True,
            "model_scoring_performed": True,
            "claim_scope": "development structural signal only; derived Draw probability is uncalibrated secondary diagnostic",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--a01", required=True)
    p.add_argument("--a02", required=True)
    p.add_argument("--a03", required=True)
    p.add_argument("--matches-zip", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    run(Path(a.a01), Path(a.a02), Path(a.a03), Path(a.matches_zip), Path(a.out))


if __name__ == "__main__":
    main()
