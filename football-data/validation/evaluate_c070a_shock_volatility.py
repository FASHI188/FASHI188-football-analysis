from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import evaluate_c069_matched_pair_draw_state_r1 as c69
import evaluate_c069_r2_maxcard_coverage as r2
import evaluate_c069_r3_a03_expanded_dev as r3
import run_c069_matched_pair_draw_state_r1 as c69run
import run_c069_r3_a03_expanded_dev as r3run


SCHEMA_VERSION = "C070A_SHOCK_VOLATILITY_V1"
CANDIDATE_FEATURES = [
    "baseline_logit",
    "trail_shot_response_logrr_10",
    "lead_pressure_logrr_10",
    "tied_shot_pace",
    "tied_shot_burst_share_180",
]
BASELINE_FEATURES = ["baseline_logit"]
C069_AUDIT_FEATURES = [
    "baseline_logit",
    "draw_persistence",
    "equalizer_hazard",
    "rebreak_hazard",
    "late_tied_aggression",
]
SHOCK_WINDOW_MIN = 10.0
BURST_WINDOW_MIN = 3.0
LOGISTIC_C = 0.1
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 7001
FIXED_FOLDS = {
    "fold_1": ("2018-01-13", "2018-01-13", "2018-02-23"),
    "fold_2": ("2018-02-23", "2018-02-23", "2018-04-08"),
    "fold_3": ("2018-04-08", "2018-04-08", None),
}
EXPECTED_PAIR_COUNTS = {
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
EXPECTED_C069_POOLED = {
    "baseline_log_loss": 0.6938306635015128,
    "candidate_log_loss": 0.6962475952086132,
    "delta_log_loss": 0.002416931707100445,
    "delta_brier": 0.0011928465616311557,
    "delta_pair_accuracy": 0.03703703703703709,
}


def _is_score_event(event: dict) -> bool:
    if event.get("eventName") == r3run.MARKER_EVENT_NAME:
        return True
    return c69run._score_event(event)


def _scorer(event: dict, home: int, away: int) -> int | None:
    if not _is_score_event(event):
        return None
    return c69._scoring_team(event, home, away)


def _canonical_sorted(match_row: dict, raw_events: list[dict]):
    canonical, fallback_count = r3run._canonicalize_events(match_row, raw_events)
    filtered = []
    for idx, event in enumerate(canonical):
        minute = c69._event_minute(event)
        if minute is None:
            continue
        filtered.append((float(minute), int(event.get("id", idx)), idx, event))
    filtered.sort(key=lambda x: (x[0], x[1], x[2]))
    end_min = max(
        [90.0]
        + [x[0] for x in filtered if x[3].get("matchPeriod") == "2H"]
    )
    return canonical, filtered, float(end_min), int(fallback_count)


def _dense_state_stats(match_row: dict, raw_events: list[dict]):
    home, away = int(match_row["home"]), int(match_row["away"])
    canonical, filtered, end_min, fallback_count = _canonical_sorted(match_row, raw_events)

    out = {home: defaultdict(float), away: defaultdict(float)}
    score_h = score_a = 0
    prev_time = 0.0
    prev_tied_shot_time: float | None = None
    shocks: list[tuple[float, int, int]] = []
    shot_events: list[tuple[float, int]] = []

    for minute, _, _, event in filtered:
        t = min(float(minute), end_min)
        dur = max(0.0, t - prev_time)
        if score_h == score_a and dur > 0:
            out[home]["tied_min"] += dur
            out[away]["tied_min"] += dur

        tid_raw = event.get("teamId")
        tid = int(tid_raw) if tid_raw is not None else None
        if event.get("eventName") == "Shot" and tid in {home, away}:
            shot_events.append((t, tid))
            if score_h == score_a:
                opp = away if tid == home else home
                for team in (home, away):
                    out[team]["tied_all_shots"] += 1.0
                out[tid]["tied_own_shots"] += 1.0
                out[opp]["tied_opp_shots"] += 1.0
                if (
                    prev_tied_shot_time is not None
                    and 0.0 <= t - prev_tied_shot_time <= BURST_WINDOW_MIN
                ):
                    for team in (home, away):
                        out[team]["tied_burst_shots"] += 1.0
                prev_tied_shot_time = t

        scorer = _scorer(event, home, away)
        if scorer is not None:
            before_h, before_a = score_h, score_a
            before_diff = before_h - before_a
            if scorer == home:
                score_h += 1
            elif scorer == away:
                score_a += 1
            after_diff = score_h - score_a

            if before_diff == 0 and after_diff == 1:
                shocks.append((t, away, home))
            elif before_diff == 0 and after_diff == -1:
                shocks.append((t, home, away))

            if before_diff != 0 and after_diff == 0:
                prev_tied_shot_time = None
            elif after_diff != 0:
                prev_tied_shot_time = None

        prev_time = t

    if score_h == score_a and end_min > prev_time:
        dur = end_min - prev_time
        out[home]["tied_min"] += dur
        out[away]["tied_min"] += dur

    for shock_time, trailing, leading in shocks:
        stop = min(end_min, shock_time + SHOCK_WINDOW_MIN)
        exposure = max(0.0, stop - shock_time)
        out[trailing]["trail_shock_min"] += exposure
        out[leading]["lead_shock_min"] += exposure
        out[trailing]["trail_shocks"] += 1.0
        out[leading]["lead_shocks"] += 1.0
        for shot_time, shot_team in shot_events:
            if shot_time < shock_time or shot_time > stop:
                continue
            if shot_team == trailing:
                out[trailing]["trail_post_shots"] += 1.0
                out[leading]["lead_opp_post_shots"] += 1.0

    gh = score_h
    ga = score_a
    if (gh, ga) != (int(match_row["hg"]), int(match_row["ag"])):
        raise RuntimeError(
            f"C070 dense score mismatch match={match_row['match_id']} "
            f"events={gh}-{ga} match={match_row['hg']}-{match_row['ag']}"
        )

    return {team: dict(vals) for team, vals in out.items()}, canonical, fallback_count


def _new_profile(acc: dict) -> tuple[float, float, float, float]:
    tied_min = float(acc.get("tied_min", 0.0))
    tied_own = float(acc.get("tied_own_shots", 0.0))
    tied_opp = float(acc.get("tied_opp_shots", 0.0))
    tied_all = float(acc.get("tied_all_shots", 0.0))
    tied_burst = float(acc.get("tied_burst_shots", 0.0))

    trail_post = (float(acc.get("trail_post_shots", 0.0)) + 1.0) / (
        float(acc.get("trail_shock_min", 0.0)) + 20.0
    )
    tied_own_rate = (tied_own + 2.0) / (tied_min + 30.0)
    trail_response = math.log(max(trail_post, 1e-12) / max(tied_own_rate, 1e-12))

    lead_opp_post = (float(acc.get("lead_opp_post_shots", 0.0)) + 1.0) / (
        float(acc.get("lead_shock_min", 0.0)) + 20.0
    )
    tied_opp_rate = (tied_opp + 2.0) / (tied_min + 30.0)
    lead_pressure = math.log(max(lead_opp_post, 1e-12) / max(tied_opp_rate, 1e-12))

    tied_pace = (tied_all + 4.0) / (tied_min + 30.0) * 90.0
    burst_share = (tied_burst + 1.0) / (tied_all + 3.0)
    return trail_response, lead_pressure, tied_pace, burst_share


def _build_rows(
    comp_file: dict,
    matches: list[dict],
    events: dict[int, list[dict]],
):
    match_rows = []
    skipped_nonregular = 0
    for raw in matches:
        row = c69._regular_match_row(raw, comp_file.get(int(raw["wyId"])))
        if row is None:
            skipped_nonregular += 1
            continue
        match_rows.append(row)
    frame = pd.DataFrame(match_rows).sort_values(["dt", "match_id"]).reset_index(drop=True)

    c69._is_goal = _is_score_event

    team_acc = defaultdict(lambda: defaultdict(float))
    comp_goals = defaultdict(list)
    rows = []
    fallback_matches = []

    for date, group in frame.groupby("date", sort=True):
        for _, r in group.iterrows():
            home, away, cid = int(r["home"]), int(r["away"]), int(r["cid"])
            hist = comp_goals[cid]
            lgh = float(np.mean([x[0] for x in hist])) if hist else 1.4
            lga = float(np.mean([x[1] for x in hist])) if hist else 1.1
            lm = max((lgh + lga) / 2.0, 0.05)

            hn, hdp, heq, hrb, hla = c69._profile(team_acc[home])
            an, adp, aeq, arb, ala = c69._profile(team_acc[away])
            htr, hlp, hpace, hburst = _new_profile(team_acc[home])
            atr, alp, apace, aburst = _new_profile(team_acc[away])

            def goal_rates(team: int):
                acc = team_acc[team]
                n = int(acc.get("matches", 0))
                gf = float(acc.get("gf", 0.0)) / n if n else lm
                ga = float(acc.get("ga", 0.0)) / n if n else lm
                return n, gf, ga

            hgn, hgf0, hga0 = goal_rates(home)
            agn, agf0, aga0 = goal_rates(away)
            hgf = (hgf0 * hgn + c69.PRIOR_EQ_MATCHES * lm) / (hgn + c69.PRIOR_EQ_MATCHES)
            hga = (hga0 * hgn + c69.PRIOR_EQ_MATCHES * lm) / (hgn + c69.PRIOR_EQ_MATCHES)
            agf = (agf0 * agn + c69.PRIOR_EQ_MATCHES * lm) / (agn + c69.PRIOR_EQ_MATCHES)
            aga = (aga0 * agn + c69.PRIOR_EQ_MATCHES * lm) / (agn + c69.PRIOR_EQ_MATCHES)
            lh = float(np.clip(lgh * (hgf / lm) * (aga / lm), *c69.LAMBDA_CLIP))
            la = float(np.clip(lga * (agf / lm) * (hga / lm), *c69.LAMBDA_CLIP))
            p_home, p_draw, p_away, p_one, q0 = c69._score_matrix(lh, la)
            q0 = float(np.clip(q0, 1e-6, 1 - 1e-6))

            goal_diff = int(r["hg"]) - int(r["ag"])
            target = "D" if goal_diff == 0 else "OW" if abs(goal_diff) == 1 else "OTHER"
            rows.append(
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
                    "q_draw_cond": q0,
                    "baseline_logit": math.log(q0 / (1.0 - q0)),
                    "abs_ha_gap": abs(p_home - p_away),
                    "draw_persistence": (hdp + adp) / 2.0,
                    "equalizer_hazard": (heq + aeq) / 2.0,
                    "rebreak_hazard": (hrb + arb) / 2.0,
                    "late_tied_aggression": (hla + ala) / 2.0,
                    "trail_shot_response_logrr_10": (htr + atr) / 2.0,
                    "lead_pressure_logrr_10": (hlp + alp) / 2.0,
                    "tied_shot_pace": (hpace + apace) / 2.0,
                    "tied_shot_burst_share_180": (hburst + aburst) / 2.0,
                    "target": target,
                    "y": 1 if target == "D" else 0,
                }
            )

        for _, r in group.iterrows():
            home, away, cid = int(r["home"]), int(r["away"]), int(r["cid"])
            dense, canonical, fallback_count = _dense_state_stats(
                r.to_dict(), events[int(r["match_id"])]
            )
            if fallback_count:
                fallback_matches.append(int(r["match_id"]))
            old_state = c69._match_state_stats(r.to_dict(), canonical)
            for team, gf, ga in (
                (home, float(r["hg"]), float(r["ag"])),
                (away, float(r["ag"]), float(r["hg"])),
            ):
                acc = team_acc[team]
                acc["matches"] += 1.0
                acc["gf"] += gf
                acc["ga"] += ga
                for key, value in old_state[team].items():
                    acc[key] += float(value)
                for key, value in dense[team].items():
                    acc[key] += float(value)
            comp_goals[cid].append((float(r["hg"]), float(r["ag"])))

    return (
        pd.DataFrame(rows).sort_values(["dt", "match_id"]).reset_index(drop=True),
        int(skipped_nonregular),
        sorted(set(fallback_matches)),
    )


def _pair_rows(frame: pd.DataFrame, meta: list[dict]) -> pd.DataFrame:
    return r3._pair_rows(frame, meta)


def _fit_model(train: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=LOGISTIC_C,
            max_iter=5000,
            class_weight=None,
            random_state=0,
        ),
    )
    model.fit(train[features], train["y"].to_numpy(int))
    return model.predict_proba(test[features])[:, 1]


def _metric_bundle(frame: pd.DataFrame, p: np.ndarray) -> dict:
    return {
        **c69._metric(frame["y"].to_numpy(int), p),
        "pair_accuracy": c69._pair_accuracy(frame, p),
    }


def _delta(candidate: dict, baseline: dict) -> dict:
    return {
        key: float(candidate[key] - baseline[key])
        for key in ("log_loss", "brier", "auc", "accuracy", "pair_accuracy")
    }


def _bootstrap_pair_delta(
    test_rows: pd.DataFrame,
    p_baseline: np.ndarray,
    p_candidate: np.ndarray,
):
    y = test_rows["y"].to_numpy(int)
    pb = np.clip(np.asarray(p_baseline, float), 1e-12, 1 - 1e-12)
    pc = np.clip(np.asarray(p_candidate, float), 1e-12, 1 - 1e-12)
    loss_b = -(y * np.log(pb) + (1 - y) * np.log(1 - pb))
    loss_c = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
    temp = test_rows[["pair_id"]].copy()
    temp["delta"] = loss_c - loss_b
    pair_delta = temp.groupby("pair_id")["delta"].mean().to_numpy(float)
    if len(pair_delta) == 0:
        raise RuntimeError("no pair deltas for bootstrap")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sims = np.empty(BOOTSTRAP_REPS, dtype=float)
    n = len(pair_delta)
    for i in range(BOOTSTRAP_REPS):
        sims[i] = float(np.mean(pair_delta[rng.integers(0, n, size=n)]))
    return {
        "pair_count": int(n),
        "mean_delta_log_loss": float(pair_delta.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": BOOTSTRAP_REPS,
        "seed": BOOTSTRAP_SEED,
    }


def _calibration(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    logit = np.log(p / (1.0 - p)).reshape(-1, 1)
    if len(np.unique(y)) == 2:
        cal = LogisticRegression(
            C=1e6,
            max_iter=5000,
            class_weight=None,
            random_state=0,
        )
        cal.fit(logit, y)
        intercept = float(cal.intercept_[0])
        slope = float(cal.coef_[0, 0])
    else:
        intercept = None
        slope = None

    edges = np.linspace(0.0, 1.0, 6)
    bin_id = np.minimum(np.digitize(p, edges[1:-1], right=False), 4)
    ece = 0.0
    bins = []
    for b in range(5):
        mask = bin_id == b
        n = int(mask.sum())
        if n:
            mean_p = float(p[mask].mean())
            obs = float(y[mask].mean())
            ece += n / len(y) * abs(mean_p - obs)
        else:
            mean_p = None
            obs = None
        bins.append(
            {
                "bin": b + 1,
                "lower": float(edges[b]),
                "upper": float(edges[b + 1]),
                "rows": n,
                "mean_p": mean_p,
                "observed": obs,
            }
        )
    return {
        "intercept": intercept,
        "slope": slope,
        "ece_equal_width_5": float(ece),
        "bins": bins,
    }


def _feature_diffs(test_rows: pd.DataFrame, features: list[str]) -> dict:
    out = {}
    for feature in features:
        diffs = []
        for _, group in test_rows.groupby("pair_id"):
            if len(group) != 2:
                continue
            draw = float(group.loc[group["pair_role"] == "D", feature].iloc[0])
            ow = float(group.loc[group["pair_role"] == "OW", feature].iloc[0])
            diffs.append(draw - ow)
        arr = np.asarray(diffs, dtype=float)
        out[feature] = {
            "pairs": int(len(arr)),
            "mean_draw_minus_onegoal": float(arr.mean()) if len(arr) else None,
            "median_draw_minus_onegoal": float(np.median(arr)) if len(arr) else None,
            "positive_share": float((arr > 0).mean()) if len(arr) else None,
        }
    return out


def _assert_close(name: str, got: float, expected: float, atol: float = 1e-12):
    if not math.isclose(float(got), float(expected), rel_tol=0.0, abs_tol=atol):
        raise RuntimeError(f"{name} integrity mismatch got={got} expected={expected}")


def run(a01: Path, a02: Path, a03: Path, matches_zip: Path, out: Path) -> None:
    comp_file, matches, events, union_sha, a03_ids_sha = r3._merge_three(
        c69, a01, a02, a03, matches_zip
    )
    rows, skipped_nonregular, fallback_matches = _build_rows(comp_file, matches, events)
    eligible = rows[
        (rows["hn"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (rows["an"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (rows["target"].isin(["D", "OW"]))
    ].copy()
    eligible = eligible.sort_values(["dt", "match_id"]).reset_index(drop=True)

    source = {
        "packages": ["A01", "A02", "A03"],
        "source_matches": int(len(matches)),
        "union_ids_sha256_sorted": union_sha,
        "a03_ids_sha256_ordered": a03_ids_sha,
        "regular_matches": int(len(rows)),
        "skipped_nonregular_matches": int(skipped_nonregular),
        "eligible_rows": int(len(eligible)),
        "eligible_draws": int((eligible["target"] == "D").sum()),
        "eligible_onegoal_wins": int((eligible["target"] == "OW").sum()),
        "score_fallback_matches": fallback_matches,
    }
    for key, expected in EXPECTED_SOURCE.items():
        if int(source[key]) != int(expected):
            raise RuntimeError(
                f"C069-R3 source integrity mismatch {key}={source[key]} expected={expected}"
            )
    if fallback_matches != [2499781]:
        raise RuntimeError(f"score fallback identity mismatch {fallback_matches}")

    feature_coverage = {}
    coverage_failed = False
    for feature in CANDIDATE_FEATURES[1:]:
        values = eligible[feature].to_numpy(float)
        finite = bool(np.isfinite(values).all())
        std = float(np.std(values))
        unique = int(pd.Series(values).nunique())
        item = {
            "rows": int(len(values)),
            "finite_all": finite,
            "std": std,
            "unique_values": unique,
            "pass": bool(finite and std > 0.0 and unique > 1),
        }
        feature_coverage[feature] = item
        if not item["pass"]:
            coverage_failed = True

    boundary = {
        "post_view_development_only": True,
        "protected_samples_used": False,
        "new_A_packages_opened": [],
        "formal_weight": 0,
        "scientific_pass": False,
        "confirmation_pass": False,
        "formal_promotion_allowed": False,
    }
    if coverage_failed:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "STOP_DATA_BEFORE_MODEL",
            "verdict": "C070A_NEW_FEATURE_COVERAGE_INSUFFICIENT",
            "source": source,
            "feature_coverage": feature_coverage,
            "boundary": {
                **boundary,
                "model_fitting_performed": False,
                "model_scoring_performed": False,
            },
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    prepared: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    folds: dict[str, dict] = {}
    pair_failures = []
    calipers = dict(c69.MATCH_CALIPERS)

    for name, (train_end_s, test_start_s, test_end_s) in FIXED_FOLDS.items():
        train_end = pd.Timestamp(train_end_s).date()
        test_start = pd.Timestamp(test_start_s).date()
        test_end = pd.Timestamp(test_end_s).date() if test_end_s else None
        train = eligible[eligible["date"] < train_end].copy()
        test = eligible[eligible["date"] >= test_start].copy()
        if test_end is not None:
            test = test[test["date"] < test_end].copy()

        train_meta, train_cert = r2._optimal_pairs(
            train, f"{name}-c070a-train", calipers
        )
        test_meta, test_cert = r2._optimal_pairs(
            test, f"{name}-c070a-test", calipers
        )
        train_pairs = _pair_rows(train, train_meta)
        test_pairs = _pair_rows(test, test_meta)
        expected_train, expected_test = EXPECTED_PAIR_COUNTS[name]
        if (
            len(train_meta) != train_cert
            or len(test_meta) != test_cert
            or len(train_meta) != expected_train
            or len(test_meta) != expected_test
        ):
            raise RuntimeError(
                f"{name} pair integrity mismatch "
                f"got={len(train_meta)}/{len(test_meta)} "
                f"cert={train_cert}/{test_cert} "
                f"expected={expected_train}/{expected_test}"
            )
        train_pass = len(train_meta) >= c69.MIN_TRAIN_PAIRS
        test_pass = len(test_meta) >= c69.MIN_TEST_PAIRS
        if not train_pass:
            pair_failures.append(f"{name}:train")
        if not test_pass:
            pair_failures.append(f"{name}:test")

        folds[name] = {
            "train_date_max_exclusive": train_end_s,
            "test_date_min_inclusive": test_start_s,
            "test_date_max_exclusive": test_end_s,
            "train_target_rows": int(len(train)),
            "test_target_rows": int(len(test)),
            "train_pairs": int(len(train_meta)),
            "test_pairs": int(len(test_meta)),
            "train_maximum_cardinality_certificate": int(train_cert),
            "test_maximum_cardinality_certificate": int(test_cert),
            "frozen_gate": {
                "minimum_train_pairs": int(c69.MIN_TRAIN_PAIRS),
                "minimum_test_pairs": int(c69.MIN_TEST_PAIRS),
                "train_pass": bool(train_pass),
                "test_pass": bool(test_pass),
                "fold_pass": bool(train_pass and test_pass),
            },
        }
        prepared[name] = (train_pairs, test_pairs)

    if pair_failures:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "STOP_COVERAGE_BEFORE_MODEL",
            "verdict": "C070A_FROZEN_PAIR_COVERAGE_FAILED",
            "source": source,
            "feature_coverage": feature_coverage,
            "folds": folds,
            "coverage_failures": pair_failures,
            "boundary": {
                **boundary,
                "model_fitting_performed": False,
                "model_scoring_performed": False,
            },
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    pooled_rows = []
    pooled_pb = []
    pooled_pc69 = []
    pooled_pc70 = []

    for name in FIXED_FOLDS:
        train_pairs, test_pairs = prepared[name]
        pb = _fit_model(train_pairs, test_pairs, BASELINE_FEATURES)
        pc69 = _fit_model(train_pairs, test_pairs, C069_AUDIT_FEATURES)
        pc70 = _fit_model(train_pairs, test_pairs, CANDIDATE_FEATURES)

        mb = _metric_bundle(test_pairs, pb)
        mc69 = _metric_bundle(test_pairs, pc69)
        mc70 = _metric_bundle(test_pairs, pc70)
        folds[name].update(
            {
                "status": "EVALUATED",
                "baseline": mb,
                "c069_integrity_candidate": mc69,
                "c069_delta_candidate_minus_baseline": _delta(mc69, mb),
                "c070a_candidate": mc70,
                "c070a_delta_candidate_minus_baseline": _delta(mc70, mb),
                "c070a_feature_differences": _feature_diffs(
                    test_pairs, CANDIDATE_FEATURES[1:]
                ),
            }
        )
        pooled_rows.append(test_pairs)
        pooled_pb.append(pb)
        pooled_pc69.append(pc69)
        pooled_pc70.append(pc70)

    all_test = pd.concat(pooled_rows, ignore_index=True)
    pb = np.concatenate(pooled_pb)
    pc69 = np.concatenate(pooled_pc69)
    pc70 = np.concatenate(pooled_pc70)
    mb = _metric_bundle(all_test, pb)
    mc69 = _metric_bundle(all_test, pc69)
    mc70 = _metric_bundle(all_test, pc70)
    d69 = _delta(mc69, mb)
    d70 = _delta(mc70, mb)

    _assert_close("C069 baseline pooled logloss", mb["log_loss"], EXPECTED_C069_POOLED["baseline_log_loss"])
    _assert_close("C069 candidate pooled logloss", mc69["log_loss"], EXPECTED_C069_POOLED["candidate_log_loss"])
    _assert_close("C069 dLL", d69["log_loss"], EXPECTED_C069_POOLED["delta_log_loss"])
    _assert_close("C069 dBrier", d69["brier"], EXPECTED_C069_POOLED["delta_brier"])
    _assert_close("C069 dPairAccuracy", d69["pair_accuracy"], EXPECTED_C069_POOLED["delta_pair_accuracy"])

    bootstrap = _bootstrap_pair_delta(all_test, pb, pc70)
    fold_wins = sum(
        1 for name in FIXED_FOLDS
        if folds[name]["c070a_delta_candidate_minus_baseline"]["log_loss"] < 0
    )
    development_signal = bool(
        d70["log_loss"] < 0
        and bootstrap["ci90_high"] < 0
        and fold_wins >= 2
        and d70["brier"] <= 0
        and d70["pair_accuracy"] >= 0
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "POSTVIEW_C070A_DEVELOPMENT_COMPLETE",
        "verdict": (
            "C070A_SHOCK_VOLATILITY_DEVELOPMENT_SIGNAL"
            if development_signal
            else "C070A_SHOCK_VOLATILITY_STABLE_INCREMENT_NOT_ESTABLISHED"
        ),
        "source": source,
        "feature_coverage": feature_coverage,
        "contract_echo": {
            "shock_window_minutes": SHOCK_WINDOW_MIN,
            "burst_window_seconds": int(BURST_WINDOW_MIN * 60),
            "candidate_features": CANDIDATE_FEATURES,
            "baseline_features": BASELINE_FEATURES,
            "logistic_C": LOGISTIC_C,
            "bootstrap_reps": BOOTSTRAP_REPS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "fixed_fold_dates": FIXED_FOLDS,
            "calipers": calipers,
            "minimum_train_pairs_each_fold": int(c69.MIN_TRAIN_PAIRS),
            "minimum_test_pairs_each_fold": int(c69.MIN_TEST_PAIRS),
        },
        "folds": folds,
        "coverage_failures": [],
        "pooled": {
            "test_rows": int(len(all_test)),
            "test_pairs": int(all_test["pair_id"].nunique()),
            "baseline": mb,
            "c069_integrity_candidate": mc69,
            "c069_integrity_delta_candidate_minus_baseline": d69,
            "c070a_candidate": mc70,
            "c070a_delta_candidate_minus_baseline": d70,
            "fold_logloss_wins": int(fold_wins),
            "bootstrap_logloss_delta": bootstrap,
            "baseline_calibration": _calibration(all_test["y"].to_numpy(int), pb),
            "c070a_calibration": _calibration(all_test["y"].to_numpy(int), pc70),
            "c070a_feature_differences": _feature_diffs(
                all_test, CANDIDATE_FEATURES[1:]
            ),
            "development_signal": development_signal,
        },
        "boundary": {
            **boundary,
            "model_fitting_performed": True,
            "model_scoring_performed": True,
            "feature_effect_claim_allowed": True,
            "claim_scope": "post-view development only; no scientific, confirmation, or promotion claim",
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
    run(
        Path(a.a01),
        Path(a.a02),
        Path(a.a03),
        Path(a.matches_zip),
        Path(a.out),
    )


if __name__ == "__main__":
    main()
