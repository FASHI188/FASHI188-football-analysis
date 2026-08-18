from __future__ import annotations

import argparse
import hashlib
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

A01_IDS_SHA = "12ccdea126c4b92c2ea82ce4fbcbea54c8525885423371883f71339c1204adcf"
A02_IDS_SHA = "a91e6274a25c096ff3a70309feab4df245f705001bfc38b8689df4873346b752"

PRIOR_EQ_MATCHES = 4.0
LAMBDA_CLIP = (0.2, 3.5)
MIN_PRIOR_TEAM_MATCHES = 4

MATCH_CALIPERS = {
    "q_draw_cond": 0.05,
    "abs_ha_gap": 0.08,
    "lambda_total": 0.45,
}
STATE_FEATURES = [
    "draw_persistence",
    "equalizer_hazard",
    "rebreak_hazard",
    "late_tied_aggression",
]
BASELINE_FEATURES = ["baseline_logit"]
CANDIDATE_FEATURES = BASELINE_FEATURES + STATE_FEATURES

LOGISTIC_C = 0.1
BOOTSTRAP_SEED = 6901
BOOTSTRAP_REPS = 2000
MIN_TRAIN_PAIRS = 30
MIN_TEST_PAIRS = 15


def _tag_ids(event: dict) -> set[int]:
    return {int(t["id"]) for t in event.get("tags", []) if "id" in t}


def _event_minute(event: dict) -> float | None:
    period = event.get("matchPeriod")
    if period not in {"1H", "2H"}:
        return None
    sec = float(event.get("eventSec", 0.0))
    return sec / 60.0 + (45.0 if period == "2H" else 0.0)


def _is_goal(event: dict) -> bool:
    tags = _tag_ids(event)
    return 101 in tags or 102 in tags


def _scoring_team(event: dict, home: int, away: int) -> int | None:
    tid = event.get("teamId")
    if tid is None:
        return None
    tid = int(tid)
    if tid not in {home, away}:
        return None
    tags = _tag_ids(event)
    if 102 in tags:
        return away if tid == home else home
    if 101 in tags:
        return tid
    return None


def _load_package(path: Path, expected_id: str, expected_ids_sha: str):
    z = zipfile.ZipFile(path)
    pkg = json.loads(z.read("PACKAGE.json"))
    manifest = [
        json.loads(line)
        for line in z.read("MANIFEST.jsonl").decode().splitlines()
        if line
    ]
    manifest = sorted(manifest, key=lambda x: x["rank"])
    ids = [str(x["match_id"]) for x in manifest]
    ids_sha = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    if pkg["package_id"] != expected_id or int(pkg["match_count"]) != 400:
        raise RuntimeError(f"{expected_id} package identity mismatch")
    if ids_sha != expected_ids_sha:
        raise RuntimeError(f"{expected_id} ids sha mismatch: {ids_sha}")
    comp_file = {int(x["match_id"]): x["competition_file"] for x in manifest}
    matches = [
        json.loads(line)
        for line in z.read("matches.jsonl").decode().splitlines()
        if line
    ]
    events = {
        int(Path(name).stem): json.loads(z.read(name))
        for name in z.namelist()
        if name.startswith("events/") and name.endswith(".json")
    }
    if len(events) != 400:
        raise RuntimeError(f"{expected_id} event coverage={len(events)}")
    return comp_file, matches, events


def _merge_sources(a01: Path, a02: Path):
    cf1, m1, e1 = _load_package(a01, "A01", A01_IDS_SHA)
    cf2, m2, e2 = _load_package(a02, "A02", A02_IDS_SHA)
    ids1 = {int(x["wyId"]) for x in m1}
    ids2 = {int(x["wyId"]) for x in m2}
    if ids1 & ids2:
        raise RuntimeError("A01/A02 overlap")
    cf = {**cf1, **cf2}
    events = {**e1, **e2}
    matches = m1 + m2
    union_sha = hashlib.sha256(
        ("\n".join(map(str, sorted(ids1 | ids2))) + "\n").encode()
    ).hexdigest()
    return cf, matches, events, union_sha


def _score_matrix(lh: float, la: float, kmax: int = 16):
    hp = np.array([math.exp(-lh) * lh**k / math.factorial(k) for k in range(kmax)])
    ap = np.array([math.exp(-la) * la**k / math.factorial(k) for k in range(kmax)])
    matrix = np.outer(hp, ap)
    matrix /= matrix.sum()
    p_home = float(np.tril(matrix, -1).sum())
    p_draw = float(np.trace(matrix))
    p_away = float(np.triu(matrix, 1).sum())
    p_one = float(
        sum(
            matrix[i, j]
            for i in range(kmax)
            for j in range(kmax)
            if abs(i - j) == 1
        )
    )
    q_draw = p_draw / (p_draw + p_one)
    return p_home, p_draw, p_away, p_one, q_draw


def _regular_match_row(match: dict, comp_file: str | None):
    if str(match.get("duration", "Regular")) != "Regular":
        return None
    teams = list(match["teamsData"].values())
    home = next(team for team in teams if team["side"] == "home")
    away = next(team for team in teams if team["side"] == "away")
    dt = pd.to_datetime(match["dateutc"], utc=True)
    return {
        "match_id": int(match["wyId"]),
        "dt": dt,
        "date": dt.date(),
        "cid": int(match["competitionId"]),
        "competition_file": comp_file,
        "home": int(home["teamId"]),
        "away": int(away["teamId"]),
        "hg": int(home["score"]),
        "ag": int(away["score"]),
    }


def _match_state_stats(match_row: dict, raw_events: list[dict]):
    home, away = int(match_row["home"]), int(match_row["away"])
    filtered = []
    for idx, event in enumerate(raw_events):
        minute = _event_minute(event)
        if minute is None:
            continue
        filtered.append((minute, int(event.get("id", idx)), idx, event))
    filtered.sort(key=lambda x: (x[0], x[1], x[2]))
    end_min = max([90.0] + [x[0] for x in filtered if x[3].get("matchPeriod") == "2H"])

    goals = []
    for minute, eid, idx, event in filtered:
        if not _is_goal(event):
            continue
        scorer = _scoring_team(event, home, away)
        if scorer is None:
            raise RuntimeError(f"unresolvable goal team match={match_row['match_id']}")
        goals.append((minute, eid, idx, scorer))

    gh = sum(1 for *_, scorer in goals if scorer == home)
    ga = sum(1 for *_, scorer in goals if scorer == away)
    if (gh, ga) != (int(match_row["hg"]), int(match_row["ag"])):
        raise RuntimeError(
            f"90m goal reconstruction mismatch match={match_row['match_id']} "
            f"events={gh}-{ga} match={match_row['hg']}-{match_row['ag']}"
        )

    out = {home: defaultdict(float), away: defaultdict(float)}

    for checkpoint in (60.0, 70.0, 80.0):
        sh = sa = 0
        for minute, _, _, scorer in goals:
            if minute > checkpoint:
                break
            if scorer == home:
                sh += 1
            else:
                sa += 1
        if sh == sa:
            for team in (home, away):
                out[team]["dp_opp"] += 1.0
                if int(match_row["hg"]) == int(match_row["ag"]):
                    out[team]["dp_success"] += 1.0

    sh = sa = 0
    prev = 0.0
    post_eq_tied = False

    def add_interval(start: float, end: float):
        dur = max(0.0, end - start)
        if dur <= 0:
            return
        if sh - sa == -1:
            out[home]["trail1_min"] += dur
        if sa - sh == -1:
            out[away]["trail1_min"] += dur
        if post_eq_tied and sh == sa:
            out[home]["posteq_tied_min"] += dur
            out[away]["posteq_tied_min"] += dur
        if sh == sa:
            late_start = max(start, 60.0)
            if end > late_start:
                late_dur = end - late_start
                out[home]["late_tied_min"] += late_dur
                out[away]["late_tied_min"] += late_dur

    for minute, _, _, scorer in goals:
        t = min(float(minute), end_min)
        add_interval(prev, t)
        before_h, before_a = sh, sa
        if post_eq_tied and before_h == before_a:
            out[home]["rebreak"] += 1.0
            out[away]["rebreak"] += 1.0
        if scorer == home:
            sh += 1
        else:
            sa += 1
        if scorer == home and before_h - before_a == -1 and sh == sa:
            out[home]["equalizer"] += 1.0
            post_eq_tied = True
        elif scorer == away and before_a - before_h == -1 and sh == sa:
            out[away]["equalizer"] += 1.0
            post_eq_tied = True
        elif sh != sa:
            post_eq_tied = False
        prev = t
    add_interval(prev, end_min)

    sh = sa = 0
    for minute, _, _, event in filtered:
        if minute >= 60.0 and sh == sa and event.get("eventName") == "Shot":
            tid = event.get("teamId")
            if tid is not None and int(tid) in {home, away}:
                out[int(tid)]["late_tied_shots"] += 1.0
        if _is_goal(event):
            scorer = _scoring_team(event, home, away)
            if scorer == home:
                sh += 1
            elif scorer == away:
                sa += 1

    return {team: dict(vals) for team, vals in out.items()}


def _profile(acc: dict):
    n = int(acc.get("matches", 0))
    dp = (acc.get("dp_success", 0.0) + 2.0) / (acc.get("dp_opp", 0.0) + 4.0)
    eq = (acc.get("equalizer", 0.0) + 0.5) / (acc.get("trail1_min", 0.0) + 30.0) * 90.0
    rb = (acc.get("rebreak", 0.0) + 0.5) / (acc.get("posteq_tied_min", 0.0) + 30.0) * 90.0
    la = (acc.get("late_tied_shots", 0.0) + 1.0) / (acc.get("late_tied_min", 0.0) + 30.0) * 90.0
    return n, dp, eq, rb, la


def _build_rows(comp_file: dict, matches: list[dict], events: dict[int, list[dict]]):
    match_rows = []
    skipped_nonregular = 0
    for raw in matches:
        row = _regular_match_row(raw, comp_file.get(int(raw["wyId"])))
        if row is None:
            skipped_nonregular += 1
            continue
        match_rows.append(row)
    frame = pd.DataFrame(match_rows).sort_values(["dt", "match_id"]).reset_index(drop=True)

    team_acc = defaultdict(lambda: defaultdict(float))
    comp_goals = defaultdict(list)
    rows = []
    for date, group in frame.groupby("date", sort=True):
        for _, r in group.iterrows():
            home, away, cid = int(r.home), int(r.away), int(r.cid)
            hist = comp_goals[cid]
            lgh = float(np.mean([x[0] for x in hist])) if hist else 1.4
            lga = float(np.mean([x[1] for x in hist])) if hist else 1.1
            lm = max((lgh + lga) / 2.0, 0.05)

            hn, hdp, heq, hrb, hla = _profile(team_acc[home])
            an, adp, aeq, arb, ala = _profile(team_acc[away])

            def goal_rates(team):
                acc = team_acc[team]
                n = int(acc.get("matches", 0))
                gf = acc.get("gf", 0.0) / n if n else lm
                ga = acc.get("ga", 0.0) / n if n else lm
                return n, gf, ga

            hgn, hgf0, hga0 = goal_rates(home)
            agn, agf0, aga0 = goal_rates(away)
            hgf = (hgf0 * hgn + PRIOR_EQ_MATCHES * lm) / (hgn + PRIOR_EQ_MATCHES)
            hga = (hga0 * hgn + PRIOR_EQ_MATCHES * lm) / (hgn + PRIOR_EQ_MATCHES)
            agf = (agf0 * agn + PRIOR_EQ_MATCHES * lm) / (agn + PRIOR_EQ_MATCHES)
            aga = (aga0 * agn + PRIOR_EQ_MATCHES * lm) / (agn + PRIOR_EQ_MATCHES)
            lh = float(np.clip(lgh * (hgf / lm) * (aga / lm), *LAMBDA_CLIP))
            la = float(np.clip(lga * (agf / lm) * (hga / lm), *LAMBDA_CLIP))
            p_home, p_draw, p_away, p_one, q0 = _score_matrix(lh, la)
            q0 = float(np.clip(q0, 1e-6, 1 - 1e-6))

            goal_diff = int(r.hg) - int(r.ag)
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
                    "target": target,
                    "y": 1 if target == "D" else 0,
                }
            )

        for _, r in group.iterrows():
            home, away, cid = int(r.home), int(r.away), int(r.cid)
            state = _match_state_stats(r.to_dict(), events[int(r.match_id)])
            for team, gf, ga in (
                (home, float(r.hg), float(r.ag)),
                (away, float(r.ag), float(r.hg)),
            ):
                acc = team_acc[team]
                acc["matches"] += 1.0
                acc["gf"] += gf
                acc["ga"] += ga
                for key, value in state[team].items():
                    acc[key] += float(value)
            comp_goals[cid].append((float(r.hg), float(r.ag)))

    return pd.DataFrame(rows).sort_values(["dt", "match_id"]).reset_index(drop=True), skipped_nonregular


def _greedy_pairs(frame: pd.DataFrame, prefix: str):
    draws = frame[frame["target"] == "D"].sort_values(["dt", "match_id"])
    wins = frame[frame["target"] == "OW"].copy()
    available = set(wins.index.tolist())
    out = []
    for draw_index, draw in draws.iterrows():
        candidates = []
        for win_index in list(available):
            win = wins.loc[win_index]
            if int(win.cid) != int(draw.cid):
                continue
            diffs = {key: abs(float(draw[key]) - float(win[key])) for key in MATCH_CALIPERS}
            if any(diffs[key] > MATCH_CALIPERS[key] for key in MATCH_CALIPERS):
                continue
            distance = sum((diffs[key] / MATCH_CALIPERS[key]) ** 2 for key in MATCH_CALIPERS)
            candidates.append(
                (
                    distance,
                    abs((draw.dt - win.dt).total_seconds()),
                    int(win.match_id),
                    win_index,
                )
            )
        if not candidates:
            continue
        _, _, _, win_index = min(candidates)
        available.remove(win_index)
        pair_id = f"{prefix}-{len(out)+1:04d}"
        out.append((pair_id, draw_index, win_index))
    if not out:
        return pd.DataFrame(), []

    rows = []
    pair_meta = []
    for pair_id, draw_index, win_index in out:
        draw = frame.loc[draw_index]
        win = frame.loc[win_index]
        for role, row in (("D", draw), ("OW", win)):
            item = row.to_dict()
            item["pair_id"] = pair_id
            item["pair_role"] = role
            rows.append(item)
        pair_meta.append(
            {
                "pair_id": pair_id,
                "draw_match_id": int(draw.match_id),
                "onegoal_match_id": int(win.match_id),
                "competition_id": int(draw.cid),
                "distance": float(
                    sum(
                        ((float(draw[key]) - float(win[key])) / MATCH_CALIPERS[key]) ** 2
                        for key in MATCH_CALIPERS
                    )
                ),
                "abs_q_draw_cond_diff": abs(float(draw.q_draw_cond) - float(win.q_draw_cond)),
                "abs_ha_gap_diff": abs(float(draw.abs_ha_gap) - float(win.abs_ha_gap)),
                "abs_lambda_total_diff": abs(float(draw.lambda_total) - float(win.lambda_total)),
            }
        )
    return pd.DataFrame(rows), pair_meta


def _metric(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    log_loss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    brier = float(np.mean((p - y) ** 2))
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    accuracy = float(((p >= 0.5).astype(int) == y).mean())
    return {"rows": int(len(y)), "log_loss": log_loss, "brier": brier, "auc": auc, "accuracy": accuracy}


def _pair_accuracy(frame: pd.DataFrame, p):
    temp = frame[["pair_id", "pair_role"]].copy()
    temp["p"] = np.asarray(p, dtype=float)
    wins = ties = total = 0
    for _, group in temp.groupby("pair_id"):
        if len(group) != 2:
            continue
        p_draw = float(group.loc[group.pair_role == "D", "p"].iloc[0])
        p_win = float(group.loc[group.pair_role == "OW", "p"].iloc[0])
        total += 1
        if p_draw > p_win:
            wins += 1
        elif p_draw == p_win:
            ties += 1
    return float((wins + 0.5 * ties) / total) if total else float("nan")


def _fit_fold(train_pairs: pd.DataFrame, test_pairs: pd.DataFrame):
    y_train = train_pairs["y"].to_numpy(int)
    y_test = test_pairs["y"].to_numpy(int)
    baseline = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=LOGISTIC_C, max_iter=5000, class_weight=None, random_state=0),
    )
    candidate = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=LOGISTIC_C, max_iter=5000, class_weight=None, random_state=0),
    )
    baseline.fit(train_pairs[BASELINE_FEATURES], y_train)
    candidate.fit(train_pairs[CANDIDATE_FEATURES], y_train)
    p_baseline = baseline.predict_proba(test_pairs[BASELINE_FEATURES])[:, 1]
    p_candidate = candidate.predict_proba(test_pairs[CANDIDATE_FEATURES])[:, 1]
    metric_baseline = _metric(y_test, p_baseline)
    metric_candidate = _metric(y_test, p_candidate)
    pair_baseline = _pair_accuracy(test_pairs, p_baseline)
    pair_candidate = _pair_accuracy(test_pairs, p_candidate)
    delta = {
        "log_loss": metric_candidate["log_loss"] - metric_baseline["log_loss"],
        "brier": metric_candidate["brier"] - metric_baseline["brier"],
        "auc": metric_candidate["auc"] - metric_baseline["auc"],
        "accuracy": metric_candidate["accuracy"] - metric_baseline["accuracy"],
        "pair_accuracy": pair_candidate - pair_baseline,
    }
    return (
        p_baseline,
        p_candidate,
        metric_baseline,
        metric_candidate,
        delta,
        pair_baseline,
        pair_candidate,
    )


def _bootstrap_pair_delta(test_rows: pd.DataFrame, p_baseline: np.ndarray, p_candidate: np.ndarray):
    y = test_rows["y"].to_numpy(int)
    pb = np.clip(p_baseline, 1e-12, 1 - 1e-12)
    pc = np.clip(p_candidate, 1e-12, 1 - 1e-12)
    loss_b = -(y * np.log(pb) + (1 - y) * np.log(1 - pb))
    loss_c = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
    temp = test_rows[["pair_id"]].copy()
    temp["delta"] = loss_c - loss_b
    pair_delta = temp.groupby("pair_id")["delta"].mean().to_numpy(float)
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


def _paired_feature_diffs(test_rows: pd.DataFrame):
    result = {}
    for feature in STATE_FEATURES:
        diffs = []
        for _, group in test_rows.groupby("pair_id"):
            if len(group) != 2:
                continue
            draw_value = float(group.loc[group.pair_role == "D", feature].iloc[0])
            win_value = float(group.loc[group.pair_role == "OW", feature].iloc[0])
            diffs.append(draw_value - win_value)
        arr = np.asarray(diffs, dtype=float)
        result[feature] = {
            "pairs": int(len(arr)),
            "mean_draw_minus_onegoal": float(arr.mean()) if len(arr) else None,
            "median_draw_minus_onegoal": float(np.median(arr)) if len(arr) else None,
            "positive_share": float((arr > 0).mean()) if len(arr) else None,
        }
    return result


def run(a01: Path, a02: Path, out: Path):
    comp_file, matches, events, union_sha = _merge_sources(a01, a02)
    rows, skipped_nonregular = _build_rows(comp_file, matches, events)
    eligible = rows[
        (rows["hn"] >= MIN_PRIOR_TEAM_MATCHES)
        & (rows["an"] >= MIN_PRIOR_TEAM_MATCHES)
        & (rows["target"].isin(["D", "OW"]))
    ].copy()
    eligible = eligible.sort_values(["dt", "match_id"]).reset_index(drop=True)

    unique_dates = sorted(eligible["date"].unique())
    if len(unique_dates) < 10:
        raise RuntimeError("insufficient chronological coverage")
    cut_indices = [
        max(1, min(len(unique_dates) - 1, int(len(unique_dates) * q)))
        for q in (0.4, 0.6, 0.8)
    ]
    cut_dates = [unique_dates[i] for i in cut_indices]
    fold_specs = [
        ("fold_1", cut_dates[0], cut_dates[0], cut_dates[1]),
        ("fold_2", cut_dates[1], cut_dates[1], cut_dates[2]),
        ("fold_3", cut_dates[2], cut_dates[2], None),
    ]

    fold_results = {}
    pooled_rows = []
    pooled_pb = []
    pooled_pc = []
    coverage_failures = []

    for name, train_end, test_start, test_end in fold_specs:
        train = eligible[eligible["date"] < train_end].copy()
        test = eligible[eligible["date"] >= test_start].copy()
        if test_end is not None:
            test = test[test["date"] < test_end].copy()

        train_pairs, train_meta = _greedy_pairs(train, f"{name}-train")
        test_pairs, test_meta = _greedy_pairs(test, f"{name}-test")
        train_n = len(train_meta)
        test_n = len(test_meta)

        fold_entry = {
            "train_date_max_exclusive": str(train_end),
            "test_date_min_inclusive": str(test_start),
            "test_date_max_exclusive": str(test_end) if test_end is not None else None,
            "train_target_rows": int(len(train)),
            "test_target_rows": int(len(test)),
            "train_pairs": int(train_n),
            "test_pairs": int(test_n),
            "train_match_rate_vs_draws": float(train_n / max(1, int((train.target == "D").sum()))),
            "test_match_rate_vs_draws": float(test_n / max(1, int((test.target == "D").sum()))),
            "test_pair_balance": {
                "mean_abs_q_draw_cond_diff": float(np.mean([x["abs_q_draw_cond_diff"] for x in test_meta])) if test_meta else None,
                "mean_abs_ha_gap_diff": float(np.mean([x["abs_ha_gap_diff"] for x in test_meta])) if test_meta else None,
                "mean_abs_lambda_total_diff": float(np.mean([x["abs_lambda_total_diff"] for x in test_meta])) if test_meta else None,
            },
        }
        if train_n < MIN_TRAIN_PAIRS or test_n < MIN_TEST_PAIRS:
            fold_entry["status"] = "STOP_COVERAGE"
            coverage_failures.append(name)
            fold_results[name] = fold_entry
            continue

        (
            p_baseline,
            p_candidate,
            metric_baseline,
            metric_candidate,
            delta,
            pair_baseline,
            pair_candidate,
        ) = _fit_fold(train_pairs, test_pairs)
        fold_entry.update(
            {
                "status": "EVALUATED",
                "baseline": {**metric_baseline, "pair_accuracy": pair_baseline},
                "candidate": {**metric_candidate, "pair_accuracy": pair_candidate},
                "delta_candidate_minus_baseline": delta,
                "paired_feature_differences": _paired_feature_diffs(test_pairs),
            }
        )
        fold_results[name] = fold_entry
        pooled_rows.append(test_pairs.copy())
        pooled_pb.append(p_baseline)
        pooled_pc.append(p_candidate)

    source_summary = {
        "packages": ["A01", "A02"],
        "source_matches": 800,
        "union_ids_sha256_sorted": union_sha,
        "regular_matches": int(len(rows)),
        "skipped_nonregular_matches": int(skipped_nonregular),
        "eligible_draw_or_onegoal_rows": int(len(eligible)),
        "eligible_draws": int((eligible.target == "D").sum()),
        "eligible_onegoal_wins": int((eligible.target == "OW").sum()),
    }

    if coverage_failures:
        result = {
            "schema_version": "C069_MATCHED_PAIR_DRAW_STATE_R1",
            "status": "STOP_COVERAGE",
            "verdict": "MATCHED_PAIR_R1_COVERAGE_NOT_SUFFICIENT",
            "source": source_summary,
            "contract": {
                "estimand": "draw_vs_one_goal_win_conditional",
                "same_match_events_used_for_prediction": False,
                "same_utc_date_predict_before_update": True,
                "minimum_prior_selected_matches_each_team": MIN_PRIOR_TEAM_MATCHES,
                "matching": {"exact_competition": True, "calipers": MATCH_CALIPERS, "without_replacement": True},
                "state_features": STATE_FEATURES,
                "baseline_features": BASELINE_FEATURES,
                "candidate_features": CANDIDATE_FEATURES,
                "logistic_C": LOGISTIC_C,
            },
            "folds": fold_results,
            "coverage_failures": coverage_failures,
            "boundary": {
                "post_view_development_only": True,
                "protected_samples_used": False,
                "new_A_packages_opened": False,
                "formal_weight": 0,
                "scientific_pass": False,
                "confirmation_pass": False,
            },
        }
    else:
        all_test = pd.concat(pooled_rows, ignore_index=True)
        p_baseline = np.concatenate(pooled_pb)
        p_candidate = np.concatenate(pooled_pc)
        metric_baseline = _metric(all_test["y"].to_numpy(int), p_baseline)
        metric_candidate = _metric(all_test["y"].to_numpy(int), p_candidate)
        pair_baseline = _pair_accuracy(all_test, p_baseline)
        pair_candidate = _pair_accuracy(all_test, p_candidate)
        bootstrap = _bootstrap_pair_delta(all_test, p_baseline, p_candidate)
        fold_logloss_wins = sum(
            1
            for value in fold_results.values()
            if value["delta_candidate_minus_baseline"]["log_loss"] < 0
        )
        delta = {
            "log_loss": metric_candidate["log_loss"] - metric_baseline["log_loss"],
            "brier": metric_candidate["brier"] - metric_baseline["brier"],
            "auc": metric_candidate["auc"] - metric_baseline["auc"],
            "accuracy": metric_candidate["accuracy"] - metric_baseline["accuracy"],
            "pair_accuracy": pair_candidate - pair_baseline,
        }
        signal = (
            delta["log_loss"] < 0
            and bootstrap["ci90_high"] < 0
            and fold_logloss_wins >= 2
            and delta["brier"] <= 0
            and pair_candidate > pair_baseline
        )
        result = {
            "schema_version": "C069_MATCHED_PAIR_DRAW_STATE_R1",
            "status": "POSTVIEW_MATCHED_PAIR_DEVELOPMENT_COMPLETE",
            "verdict": (
                "MATCHED_PAIR_STATE_DYNAMICS_DEVELOPMENT_SIGNAL"
                if signal
                else "MATCHED_PAIR_STATE_DYNAMICS_STABLE_INCREMENT_NOT_ESTABLISHED"
            ),
            "source": source_summary,
            "contract": {
                "estimand": "draw_vs_one_goal_win_conditional",
                "same_match_events_used_for_prediction": False,
                "same_utc_date_predict_before_update": True,
                "minimum_prior_selected_matches_each_team": MIN_PRIOR_TEAM_MATCHES,
                "match_periods_used": ["1H", "2H"],
                "regular_duration_only": True,
                "matching": {
                    "exact_competition": True,
                    "calipers": MATCH_CALIPERS,
                    "distance": "sum_squared_caliper_normalized",
                    "without_replacement": True,
                    "market_input": "UNAVAILABLE_IN_A01_A02_NOT_FAKED",
                },
                "state_features": STATE_FEATURES,
                "baseline_features": BASELINE_FEATURES,
                "candidate_features": CANDIDATE_FEATURES,
                "logistic_C": LOGISTIC_C,
                "class_weight": None,
                "folds": "growing_train_three_nonoverlapping_chronological_test_blocks_by_date_40_60_80",
                "primary_metric": "binary_log_loss_candidate_minus_baseline",
                "secondary_metrics": ["brier", "auc", "accuracy", "within_pair_accuracy"],
                "bootstrap_unit": "matched_pair",
            },
            "folds": fold_results,
            "pooled": {
                "test_rows": int(len(all_test)),
                "test_pairs": int(all_test["pair_id"].nunique()),
                "baseline": {**metric_baseline, "pair_accuracy": pair_baseline},
                "candidate": {**metric_candidate, "pair_accuracy": pair_candidate},
                "delta_candidate_minus_baseline": delta,
                "fold_logloss_wins": int(fold_logloss_wins),
                "bootstrap_logloss_delta": bootstrap,
                "paired_feature_differences": _paired_feature_diffs(all_test),
            },
            "boundary": {
                "post_view_development_only": True,
                "protected_samples_used": False,
                "new_A_packages_opened": False,
                "formal_weight": 0,
                "scientific_pass": False,
                "confirmation_pass": False,
                "formal_promotion_allowed": False,
            },
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--a01", required=True)
    parser.add_argument("--a02", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    run(Path(args.a01), Path(args.a02), Path(args.out))
