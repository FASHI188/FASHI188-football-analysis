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


SCHEMA_VERSION = "C070B_REGIME_MATCHUP_V1"
BASELINE_FEATURES = ["baseline_logit"]
C069_AUDIT_FEATURES = [
    "baseline_logit",
    "draw_persistence",
    "equalizer_hazard",
    "rebreak_hazard",
    "late_tied_aggression",
]
CANDIDATE_FEATURES = [
    "baseline_logit",
    "regime_directness_mean",
    "regime_control_logmean",
    "matchup_directness_gap",
    "matchup_control_loggap",
    "matchup_attacking_third_gap",
]
ONBALL = {"Pass", "Duel", "Shot", "Free Kick", "Others on the ball"}
LOGISTIC_C = 0.1
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 7002
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


def _tags(event: dict) -> set[int]:
    return {int(t["id"]) for t in event.get("tags", []) if "id" in t}


def _style_stats(match_row: dict, raw_events: list[dict]):
    home, away = int(match_row["home"]), int(match_row["away"])
    canonical, fallback_count = r3run._canonicalize_events(match_row, raw_events)
    filtered = []
    for idx, event in enumerate(canonical):
        minute = c69._event_minute(event)
        if minute is None:
            continue
        filtered.append((float(minute), int(event.get("id", idx)), idx, event))
    filtered.sort(key=lambda x: (x[0], x[1], x[2]))

    out = {home: defaultdict(float), away: defaultdict(float)}
    onball_teams: list[int] = []

    for _, _, _, event in filtered:
        tid_raw = event.get("teamId")
        if tid_raw is None:
            continue
        tid = int(tid_raw)
        if tid not in {home, away}:
            continue
        name = event.get("eventName")

        if name == "Pass":
            out[tid]["passes"] += 1.0
            if 1801 in _tags(event):
                out[tid]["accurate_passes"] += 1.0
            pos = event.get("positions", [])
            if len(pos) >= 2 and "x" in pos[0] and "x" in pos[-1]:
                out[tid]["geometric_passes"] += 1.0
                dx = float(pos[-1]["x"]) - float(pos[0]["x"])
                if dx >= 20.0:
                    out[tid]["direct_passes"] += 1.0

        if name in ONBALL:
            onball_teams.append(tid)
            out[tid]["onball_events"] += 1.0
            pos = event.get("positions", [])
            if pos and "x" in pos[0] and float(pos[0]["x"]) >= 66.0:
                out[tid]["att3_events"] += 1.0

    if onball_teams:
        run_team = onball_teams[0]
        run_len = 1
        for tid in onball_teams[1:]:
            if tid == run_team:
                run_len += 1
            else:
                out[run_team]["run_events"] += float(run_len)
                out[run_team]["run_count"] += 1.0
                run_team = tid
                run_len = 1
        out[run_team]["run_events"] += float(run_len)
        out[run_team]["run_count"] += 1.0

    return {team: dict(vals) for team, vals in out.items()}, canonical, int(fallback_count)


def _style_profile(acc: dict) -> tuple[float, float, float, float]:
    passes = float(acc.get("passes", 0.0))
    accuracy = (float(acc.get("accurate_passes", 0.0)) + 10.0) / (passes + 20.0)
    geometric = float(acc.get("geometric_passes", 0.0))
    direct = (float(acc.get("direct_passes", 0.0)) + 2.0) / (geometric + 4.0)
    onball = float(acc.get("onball_events", 0.0))
    att3 = (float(acc.get("att3_events", 0.0)) + 2.0) / (onball + 4.0)
    run_mean = (float(acc.get("run_events", 0.0)) + 2.0) / (
        float(acc.get("run_count", 0.0)) + 1.0
    )
    return accuracy, direct, att3, run_mean


def _build_rows(comp_file: dict, matches: list[dict], events: dict[int, list[dict]]):
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
            hacc, hdir, hatt3, hrun = _style_profile(team_acc[home])
            aacc, adir, aatt3, arun = _style_profile(team_acc[away])

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
                    "home_pass_accuracy": hacc,
                    "away_pass_accuracy": aacc,
                    "regime_directness_mean": (hdir + adir) / 2.0,
                    "regime_control_logmean": (math.log(hrun) + math.log(arun)) / 2.0,
                    "matchup_directness_gap": abs(hdir - adir),
                    "matchup_control_loggap": abs(math.log(hrun) - math.log(arun)),
                    "matchup_attacking_third_gap": abs(hatt3 - aatt3),
                    "target": target,
                    "y": 1 if target == "D" else 0,
                }
            )

        for _, r in group.iterrows():
            home, away, cid = int(r["home"]), int(r["away"]), int(r["cid"])
            style, canonical, fallback_count = _style_stats(
                r.to_dict(), events[int(r["match_id"])]
            )
            if fallback_count:
                fallback_matches.append(int(r["match_id"]))
            old = c69._match_state_stats(r.to_dict(), canonical)
            for team, gf, ga in (
                (home, float(r["hg"]), float(r["ag"])),
                (away, float(r["ag"]), float(r["hg"])),
            ):
                acc = team_acc[team]
                acc["matches"] += 1.0
                acc["gf"] += gf
                acc["ga"] += ga
                for key, value in old[team].items():
                    acc[key] += float(value)
                for key, value in style[team].items():
                    acc[key] += float(value)
            comp_goals[cid].append((float(r["hg"]), float(r["ag"])))

    return (
        pd.DataFrame(rows).sort_values(["dt", "match_id"]).reset_index(drop=True),
        int(skipped_nonregular),
        sorted(set(fallback_matches)),
    )


def _fit(train: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=LOGISTIC_C, max_iter=5000, class_weight=None, random_state=0),
    )
    model.fit(train[features], train["y"].to_numpy(int))
    return model.predict_proba(test[features])[:, 1]


def _metric(frame: pd.DataFrame, p: np.ndarray):
    return {
        **c69._metric(frame["y"].to_numpy(int), p),
        "pair_accuracy": c69._pair_accuracy(frame, p),
    }


def _delta(cand: dict, base: dict):
    return {
        k: float(cand[k] - base[k])
        for k in ("log_loss", "brier", "auc", "accuracy", "pair_accuracy")
    }


def _bootstrap(frame: pd.DataFrame, pb: np.ndarray, pc: np.ndarray):
    y = frame["y"].to_numpy(int)
    pb = np.clip(np.asarray(pb, float), 1e-12, 1 - 1e-12)
    pc = np.clip(np.asarray(pc, float), 1e-12, 1 - 1e-12)
    lb = -(y * np.log(pb) + (1-y) * np.log(1-pb))
    lc = -(y * np.log(pc) + (1-y) * np.log(1-pc))
    t = frame[["pair_id"]].copy()
    t["d"] = lc - lb
    arr = t.groupby("pair_id")["d"].mean().to_numpy(float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sims = np.empty(BOOTSTRAP_REPS)
    n = len(arr)
    for i in range(BOOTSTRAP_REPS):
        sims[i] = float(np.mean(arr[rng.integers(0, n, size=n)]))
    return {
        "pair_count": int(n),
        "mean_delta_log_loss": float(arr.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": BOOTSTRAP_REPS,
        "seed": BOOTSTRAP_SEED,
    }


def _feature_diffs(frame: pd.DataFrame):
    out = {}
    for feature in CANDIDATE_FEATURES[1:]:
        vals = []
        for _, g in frame.groupby("pair_id"):
            if len(g) != 2:
                continue
            d = float(g.loc[g["pair_role"]=="D", feature].iloc[0])
            w = float(g.loc[g["pair_role"]=="OW", feature].iloc[0])
            vals.append(d-w)
        a = np.asarray(vals, float)
        out[feature] = {
            "pairs": int(len(a)),
            "mean_draw_minus_onegoal": float(a.mean()) if len(a) else None,
            "median_draw_minus_onegoal": float(np.median(a)) if len(a) else None,
            "positive_share": float((a>0).mean()) if len(a) else None,
        }
    return out


def _assert_close(name: str, got: float, expected: float, atol: float = 1e-12):
    if not math.isclose(float(got), float(expected), rel_tol=0.0, abs_tol=atol):
        raise RuntimeError(f"{name} mismatch got={got} expected={expected}")


def run(a01: Path, a02: Path, a03: Path, matches_zip: Path, out: Path):
    comp_file, matches, events, union_sha, a03_sha = r3._merge_three(
        c69, a01, a02, a03, matches_zip
    )
    rows, skipped, fallback = _build_rows(comp_file, matches, events)
    eligible = rows[
        (rows["hn"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (rows["an"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & rows["target"].isin(["D","OW"])
    ].copy().sort_values(["dt","match_id"]).reset_index(drop=True)

    source = {
        "packages": ["A01","A02","A03"],
        "source_matches": int(len(matches)),
        "union_ids_sha256_sorted": union_sha,
        "a03_ids_sha256_ordered": a03_sha,
        "regular_matches": int(len(rows)),
        "skipped_nonregular_matches": int(skipped),
        "eligible_rows": int(len(eligible)),
        "eligible_draws": int((eligible["target"]=="D").sum()),
        "eligible_onegoal_wins": int((eligible["target"]=="OW").sum()),
        "score_fallback_matches": fallback,
    }
    for key, expected in EXPECTED_SOURCE.items():
        if int(source[key]) != int(expected):
            raise RuntimeError(f"source integrity {key}={source[key]} expected={expected}")
    if fallback != [2499781]:
        raise RuntimeError(f"fallback mismatch {fallback}")

    feature_coverage = {}
    bad = False
    for f in CANDIDATE_FEATURES[1:]:
        v = eligible[f].to_numpy(float)
        item = {
            "rows": int(len(v)),
            "finite_all": bool(np.isfinite(v).all()),
            "std": float(np.std(v)),
            "unique_values": int(pd.Series(v).nunique()),
        }
        item["pass"] = bool(item["finite_all"] and item["std"] > 0 and item["unique_values"] > 1)
        feature_coverage[f] = item
        bad |= not item["pass"]

    boundary = {
        "post_view_development_only": True,
        "protected_samples_used": False,
        "new_A_packages_opened": [],
        "formal_weight": 0,
        "scientific_pass": False,
        "confirmation_pass": False,
        "formal_promotion_allowed": False,
    }
    if bad:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "STOP_DATA_BEFORE_MODEL",
            "verdict": "C070B_NEW_FEATURE_COVERAGE_INSUFFICIENT",
            "source": source,
            "feature_coverage": feature_coverage,
            "boundary": {**boundary, "model_fitting_performed":False, "model_scoring_performed":False},
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        return

    folds = {}
    prepared = {}
    failures = []
    calipers = dict(c69.MATCH_CALIPERS)
    for name, (tr_end_s, te_start_s, te_end_s) in FIXED_FOLDS.items():
        tr_end = pd.Timestamp(tr_end_s).date()
        te_start = pd.Timestamp(te_start_s).date()
        te_end = pd.Timestamp(te_end_s).date() if te_end_s else None
        train = eligible[eligible["date"] < tr_end].copy()
        test = eligible[eligible["date"] >= te_start].copy()
        if te_end is not None:
            test = test[test["date"] < te_end].copy()
        tr_meta, tr_cert = r2._optimal_pairs(train, f"{name}-c070b-train", calipers)
        te_meta, te_cert = r2._optimal_pairs(test, f"{name}-c070b-test", calipers)
        tr_rows = r3._pair_rows(train, tr_meta)
        te_rows = r3._pair_rows(test, te_meta)
        exp_tr, exp_te = EXPECTED_PAIR_COUNTS[name]
        if (len(tr_meta),len(te_meta),tr_cert,te_cert)!=(exp_tr,exp_te,exp_tr,exp_te):
            raise RuntimeError(
                f"{name} pair integrity got={len(tr_meta)}/{len(te_meta)} cert={tr_cert}/{te_cert}"
            )
        tr_pass = len(tr_meta) >= c69.MIN_TRAIN_PAIRS
        te_pass = len(te_meta) >= c69.MIN_TEST_PAIRS
        if not tr_pass: failures.append(f"{name}:train")
        if not te_pass: failures.append(f"{name}:test")
        folds[name] = {
            "train_date_max_exclusive": tr_end_s,
            "test_date_min_inclusive": te_start_s,
            "test_date_max_exclusive": te_end_s,
            "train_pairs": int(len(tr_meta)),
            "test_pairs": int(len(te_meta)),
            "train_maximum_cardinality_certificate": int(tr_cert),
            "test_maximum_cardinality_certificate": int(te_cert),
            "frozen_gate": {
                "minimum_train_pairs": c69.MIN_TRAIN_PAIRS,
                "minimum_test_pairs": c69.MIN_TEST_PAIRS,
                "train_pass": tr_pass,
                "test_pass": te_pass,
                "fold_pass": tr_pass and te_pass,
            },
        }
        prepared[name]=(tr_rows,te_rows)

    if failures:
        result = {
            "schema_version":SCHEMA_VERSION,
            "status":"STOP_COVERAGE_BEFORE_MODEL",
            "verdict":"C070B_FROZEN_PAIR_COVERAGE_FAILED",
            "source":source,
            "feature_coverage":feature_coverage,
            "folds":folds,
            "coverage_failures":failures,
            "boundary":{**boundary,"model_fitting_performed":False,"model_scoring_performed":False},
        }
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
        return

    all_rows=[]; pbs=[]; p69s=[]; pcs=[]
    for name in FIXED_FOLDS:
        train,test=prepared[name]
        pb=_fit(train,test,BASELINE_FEATURES)
        p69=_fit(train,test,C069_AUDIT_FEATURES)
        pc=_fit(train,test,CANDIDATE_FEATURES)
        mb=_metric(test,pb); m69=_metric(test,p69); mc=_metric(test,pc)
        folds[name].update({
            "status":"EVALUATED",
            "baseline":mb,
            "c069_integrity_candidate":m69,
            "c069_delta_candidate_minus_baseline":_delta(m69,mb),
            "c070b_candidate":mc,
            "c070b_delta_candidate_minus_baseline":_delta(mc,mb),
            "c070b_feature_differences":_feature_diffs(test),
        })
        all_rows.append(test); pbs.append(pb); p69s.append(p69); pcs.append(pc)

    test=pd.concat(all_rows,ignore_index=True)
    pb=np.concatenate(pbs); p69=np.concatenate(p69s); pc=np.concatenate(pcs)
    mb=_metric(test,pb); m69=_metric(test,p69); mc=_metric(test,pc)
    d69=_delta(m69,mb); dc=_delta(mc,mb)
    _assert_close("baseline LL",mb["log_loss"],EXPECTED_C069_POOLED["baseline_log_loss"])
    _assert_close("c069 LL",m69["log_loss"],EXPECTED_C069_POOLED["candidate_log_loss"])
    _assert_close("c069 dLL",d69["log_loss"],EXPECTED_C069_POOLED["delta_log_loss"])
    _assert_close("c069 dBrier",d69["brier"],EXPECTED_C069_POOLED["delta_brier"])
    _assert_close("c069 dPairAcc",d69["pair_accuracy"],EXPECTED_C069_POOLED["delta_pair_accuracy"])

    boot=_bootstrap(test,pb,pc)
    fold_wins=sum(
        1 for name in FIXED_FOLDS
        if folds[name]["c070b_delta_candidate_minus_baseline"]["log_loss"] < 0
    )
    signal=bool(
        dc["log_loss"] < 0
        and boot["ci90_high"] < 0
        and fold_wins >= 2
        and dc["brier"] <= 0
        and dc["pair_accuracy"] >= 0
    )
    result={
        "schema_version":SCHEMA_VERSION,
        "status":"POSTVIEW_C070B_DEVELOPMENT_COMPLETE",
        "verdict":(
            "C070B_REGIME_MATCHUP_DEVELOPMENT_SIGNAL"
            if signal else "C070B_REGIME_MATCHUP_STABLE_INCREMENT_NOT_ESTABLISHED"
        ),
        "source":source,
        "feature_coverage":feature_coverage,
        "contract_echo":{
            "candidate_features":CANDIDATE_FEATURES,
            "direct_pass_threshold_x_points":20.0,
            "attacking_third_start_x":66.0,
            "logistic_C":LOGISTIC_C,
            "bootstrap_reps":BOOTSTRAP_REPS,
            "bootstrap_seed":BOOTSTRAP_SEED,
            "fixed_fold_dates":FIXED_FOLDS,
            "calipers":calipers,
        },
        "folds":folds,
        "coverage_failures":[],
        "pooled":{
            "test_rows":int(len(test)),
            "test_pairs":int(test["pair_id"].nunique()),
            "baseline":mb,
            "c069_integrity_candidate":m69,
            "c069_integrity_delta_candidate_minus_baseline":d69,
            "c070b_candidate":mc,
            "c070b_delta_candidate_minus_baseline":dc,
            "fold_logloss_wins":int(fold_wins),
            "bootstrap_logloss_delta":boot,
            "c070b_feature_differences":_feature_diffs(test),
            "development_signal":signal,
        },
        "boundary":{
            **boundary,
            "model_fitting_performed":True,
            "model_scoring_performed":True,
            "feature_effect_claim_allowed":True,
            "claim_scope":"post-view development only; no scientific, confirmation, or promotion claim",
        },
    }
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--a01",required=True)
    p.add_argument("--a02",required=True)
    p.add_argument("--a03",required=True)
    p.add_argument("--matches-zip",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    run(Path(a.a01),Path(a.a02),Path(a.a03),Path(a.matches_zip),Path(a.out))


if __name__=="__main__":
    main()
