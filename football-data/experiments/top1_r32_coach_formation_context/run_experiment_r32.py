#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R31_DIR = HERE.parent / "top1_r31_rest_schedule_context"
sys.path.insert(0, str(R31_DIR))
import run_experiment_r31 as r31  # noqa: E402

r9 = r31.r9

LINEUP_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/fixture_lineups.parquet?download=true"

COACH_NAMES = [
    "home_coach_known",
    "away_coach_known",
    "both_coach_known",
    "home_coach_tenure_log",
    "away_coach_tenure_log",
    "coach_tenure_log_diff",
    "home_recent_coach_change_le3",
    "away_recent_coach_change_le3",
    "either_recent_coach_change_le3",
    "home_long_coach_tenure_ge10",
    "away_long_coach_tenure_ge10",
]
FORMATION_NAMES = [
    "home_formation_known",
    "away_formation_known",
    "both_formation_known",
    "home_prev_backline",
    "away_prev_backline",
    "prev_backline_diff",
    "home_prev_three_back",
    "away_prev_three_back",
    "home_prev_four_back",
    "away_prev_four_back",
    "home_prev_five_back",
    "away_prev_five_back",
    "home_formation_switch_rate5",
    "away_formation_switch_rate5",
    "formation_switch_rate5_diff",
    "home_distinct_formations5",
    "away_distinct_formations5",
    "distinct_formations5_diff",
    "home_formation_history_log",
    "away_formation_history_log",
]
FEATURE_SETS = {
    "COACH_ONLY": COACH_NAMES,
    "FORMATION_ONLY": FORMATION_NAMES,
    "COACH_PLUS_FORMATION": COACH_NAMES + FORMATION_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS_FOR_BATCH005 = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_lineups():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "fixture_lineups.parquet"
    req = urllib.request.Request(LINEUP_URL, headers={"User-Agent": "football3-research-r32"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    sha = fsha(path)
    df = pd.read_parquet(path, columns=["fixture_id", "team_id", "coach_name", "coach_api_id", "formation"])
    return path, sha, df


def coach_key(row):
    x = row.coach_api_id
    if pd.notna(x):
        try:
            return f"id:{int(x)}"
        except Exception:
            pass
    name = row.coach_name
    if pd.notna(name):
        s = " ".join(str(name).strip().lower().split())
        if s:
            return f"name:{s}"
    return None


def norm_formation(x):
    if pd.isna(x):
        return None
    s = str(x).strip()
    parts = s.split("-")
    if len(parts) < 2:
        return None
    try:
        nums = [int(z) for z in parts]
    except Exception:
        return None
    if any(z <= 0 or z > 6 for z in nums):
        return None
    return "-".join(str(z) for z in nums)


def build_lineup_map(df, rows):
    wanted = {str(r["game_id"]) for r in rows}
    out = {}
    matched_rows = 0
    for rec in df.itertuples(index=False):
        fid = str(int(rec.fixture_id))
        if fid not in wanted:
            continue
        tid = str(int(rec.team_id))
        out[(fid, tid)] = {
            "coach": coach_key(rec),
            "formation": norm_formation(rec.formation),
        }
        matched_rows += 1
    return out, matched_rows


class TeamContext:
    def __init__(self):
        self.last_coach = None
        self.coach_tenure = 0
        self.since_coach_change = None
        self.coach_known_matches = 0
        self.formations = deque(maxlen=6)
        self.formation_known_matches = 0
        self.lineup_rows = 0


def backline(formation):
    if formation is None:
        return 0.0
    try:
        x = int(formation.split("-")[0])
    except Exception:
        return 0.0
    return float(x if 2 <= x <= 5 else 0)


def formation_switch_rate(hist):
    xs = list(hist)[-5:]
    if len(xs) < 2:
        return 0.0
    return float(sum(int(a != b) for a, b in zip(xs[:-1], xs[1:])) / (len(xs) - 1))


def formation_distinct(hist):
    xs = list(hist)[-5:]
    return float(len(set(xs))) if xs else 0.0


def one_team_features(st: TeamContext):
    ck = float(st.last_coach is not None)
    tenure_log = math.log1p(min(30, st.coach_tenure)) if ck else 0.0
    recent_change = float(st.since_coach_change is not None and st.since_coach_change <= 2)
    long_tenure = float(st.coach_tenure >= 10 and ck)

    prev = st.formations[-1] if st.formations else None
    fk = float(prev is not None)
    b = backline(prev)
    return {
        "coach_known": ck,
        "coach_tenure_log": tenure_log,
        "recent_coach_change_le3": recent_change,
        "long_coach_tenure_ge10": long_tenure,
        "formation_known": fk,
        "prev_backline": b,
        "prev_three_back": float(b == 3),
        "prev_four_back": float(b == 4),
        "prev_five_back": float(b == 5),
        "formation_switch_rate5": formation_switch_rate(st.formations),
        "distinct_formations5": formation_distinct(st.formations),
        "formation_history_log": math.log1p(min(50, st.formation_known_matches)),
    }


def context_features(row, states):
    h = one_team_features(states[row["home_team"]])
    a = one_team_features(states[row["away_team"]])
    return {
        "home_coach_known": h["coach_known"],
        "away_coach_known": a["coach_known"],
        "both_coach_known": h["coach_known"] * a["coach_known"],
        "home_coach_tenure_log": h["coach_tenure_log"],
        "away_coach_tenure_log": a["coach_tenure_log"],
        "coach_tenure_log_diff": h["coach_tenure_log"] - a["coach_tenure_log"],
        "home_recent_coach_change_le3": h["recent_coach_change_le3"],
        "away_recent_coach_change_le3": a["recent_coach_change_le3"],
        "either_recent_coach_change_le3": float(h["recent_coach_change_le3"] or a["recent_coach_change_le3"]),
        "home_long_coach_tenure_ge10": h["long_coach_tenure_ge10"],
        "away_long_coach_tenure_ge10": a["long_coach_tenure_ge10"],
        "home_formation_known": h["formation_known"],
        "away_formation_known": a["formation_known"],
        "both_formation_known": h["formation_known"] * a["formation_known"],
        "home_prev_backline": h["prev_backline"],
        "away_prev_backline": a["prev_backline"],
        "prev_backline_diff": h["prev_backline"] - a["prev_backline"],
        "home_prev_three_back": h["prev_three_back"],
        "away_prev_three_back": a["prev_three_back"],
        "home_prev_four_back": h["prev_four_back"],
        "away_prev_four_back": a["prev_four_back"],
        "home_prev_five_back": h["prev_five_back"],
        "away_prev_five_back": a["prev_five_back"],
        "home_formation_switch_rate5": h["formation_switch_rate5"],
        "away_formation_switch_rate5": a["formation_switch_rate5"],
        "formation_switch_rate5_diff": h["formation_switch_rate5"] - a["formation_switch_rate5"],
        "home_distinct_formations5": h["distinct_formations5"],
        "away_distinct_formations5": a["distinct_formations5"],
        "distinct_formations5_diff": h["distinct_formations5"] - a["distinct_formations5"],
        "home_formation_history_log": h["formation_history_log"],
        "away_formation_history_log": a["formation_history_log"],
    }


def update_team(st: TeamContext, info):
    if info is None:
        return
    st.lineup_rows += 1
    coach = info.get("coach")
    if coach is not None:
        if st.last_coach is None:
            st.last_coach = coach
            st.coach_tenure = 1
            st.since_coach_change = None
        elif coach == st.last_coach:
            st.coach_tenure += 1
            if st.since_coach_change is not None:
                st.since_coach_change += 1
        else:
            st.last_coach = coach
            st.coach_tenure = 1
            st.since_coach_change = 0
        st.coach_known_matches += 1

    formation = info.get("formation")
    if formation is not None:
        st.formations.append(formation)
        st.formation_known_matches += 1


def build_history():
    rows = r9.load()
    path, lineup_sha, df = download_lineups()
    lineups, matched_lineup_rows = build_lineup_map(df, rows)

    base = r9.S()
    states = defaultdict(TeamContext)
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = context_features(row, states)
            pred.append({
                "date": day,
                "y": r9.actual(row),
                "raw": raw,
                "context_features": cf,
            })
            pending.append((row, raw))

        # Strict prior: current-date results, xG, coaches and formations are all
        # withheld until every match on this date has been predicted.
        for row, raw in pending:
            base.update(row, raw)
            fid = str(row["game_id"])
            update_team(states[row["home_team"]], lineups.get((fid, row["home_team"])))
            update_team(states[row["away_team"]], lineups.get((fid, row["away_team"])))

    meta = {
        "fixture_lineups_url": LINEUP_URL,
        "fixture_lineups_sha256": lineup_sha,
        "fixture_lineups_rows_total": int(len(df)),
        "snapshot_fixture_lineup_rows_matched": int(matched_lineup_rows),
        "snapshot_matches_with_any_lineup_row": int(sum(
            1 for r in rows
            if (str(r["game_id"]), r["home_team"]) in lineups
            or (str(r["game_id"]), r["away_team"]) in lineups
        )),
        "snapshot_matches_with_both_lineup_rows": int(sum(
            1 for r in rows
            if (str(r["game_id"]), r["home_team"]) in lineups
            and (str(r["game_id"]), r["away_team"]) in lineups
        )),
    }
    try:
        path.unlink()
    except Exception:
        pass
    return pred, meta


def x_for(rec, feature_names):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in feature_names]


def fit_model(train, feature_names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([x_for(r, feature_names) for r in train], [r["y"] for r in train])
    return m


def decorate(model, rows, feature_names):
    pr = model.predict_proba([x_for(r, feature_names) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, row in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, row):
            v[int(cls)] = float(p)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def baseline_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    return m


def baseline_decorate(k1, rows):
    pr = k1.predict_proba([r9.feat_k1(r["raw"]) for r in rows])
    classes = list(k1[-1].classes_)
    out = []
    for src, row in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, row):
            v[int(cls)] = float(p)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def metrics(rows):
    return r9.metrics([{"y": r["y"], "P": r["P"]} for r in rows], "P")


def date_blocks(rows, n=4):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), n)
    out = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist():
            out[d] = i
    return out


def paired_blocks(base_rows, candidate_rows):
    block_map = date_blocks(base_rows, 4)
    blocks = {str(i): {"count": 0, "base_hits": 0, "candidate_hits": 0, "net": 0} for i in range(4)}
    gain = loss = 0
    for b, c in zip(base_rows, candidate_rows):
        if b["date"] != c["date"] or b["y"] != c["y"]:
            raise RuntimeError("R32 paired rows misaligned")
        y = b["y"]
        cb = int(b["P"]["top1"] == y)
        cc = int(c["P"]["top1"] == y)
        gain += int(cc and not cb)
        loss += int(cb and not cc)
        z = blocks[str(block_map[b["date"]])]
        z["count"] += 1
        z["base_hits"] += cb
        z["candidate_hits"] += cc
    for z in blocks.values():
        z["net"] = z["candidate_hits"] - z["base_hits"]
    return {
        "challenger_gain": gain,
        "challenger_loss": loss,
        "net_hits": gain - loss,
        "positive_time_blocks": sum(int(z["net"] > 0) for z in blocks.values()),
        "negative_time_blocks": sum(int(z["net"] < 0) for z in blocks.values()),
        "time_blocks": blocks,
    }


def info_coverage(rows):
    n = len(rows)
    if n == 0:
        return {}
    return {
        "rows": n,
        "home_prior_coach_known_rate": float(sum(r["context_features"]["home_coach_known"] for r in rows) / n),
        "away_prior_coach_known_rate": float(sum(r["context_features"]["away_coach_known"] for r in rows) / n),
        "both_prior_coach_known_rate": float(sum(r["context_features"]["both_coach_known"] for r in rows) / n),
        "home_prior_formation_known_rate": float(sum(r["context_features"]["home_formation_known"] for r in rows) / n),
        "away_prior_formation_known_rate": float(sum(r["context_features"]["away_formation_known"] for r in rows) / n),
        "both_prior_formation_known_rate": float(sum(r["context_features"]["both_formation_known"] for r in rows) / n),
    }


def run():
    pred, source_meta = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    k1 = baseline_model(train)
    val_base = baseline_decorate(k1, val0)
    base_v = metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R32 K1 validation reproduction gate failed: {base_v['hits']}")

    r31_summary = json.loads((R31_DIR / "results" / "summary_r31.json").read_text(encoding="utf-8"))
    if r31_summary["batch005_decision"]["eligible"]:
        raise RuntimeError("R32 requires frozen R31 non-promotion control")

    candidates = []
    models = {}
    for name, features in FEATURE_SETS.items():
        model = fit_model(train, features)
        models[name] = model
        val = decorate(model, val0, features)
        mv = metrics(val)
        paired = paired_blocks(val_base, val)
        gain = mv["hits"] - base_v["hits"]
        ll_delta = mv["logloss"] - base_v["logloss"]
        viable = (
            gain >= MIN_VALIDATION_GAIN_HITS
            and paired["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and paired["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
            and ll_delta <= MAX_VALIDATION_LOGLOSS_WORSEN
        )
        candidates.append({
            "name": name,
            "features": features,
            "viable": viable,
            "validation": mv,
            "gain_hits": gain,
            "gain_top1_pp": 100 * (mv["top1_accuracy"] - base_v["top1_accuracy"]),
            "logloss_delta": ll_delta,
            "brier_delta": mv["brier"] - base_v["brier"],
            "rps_delta": mv["rps"] - base_v["rps"],
            "paired": paired,
        })

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(
            viable,
            key=lambda x: (
                x["gain_hits"],
                x["paired"]["positive_time_blocks"],
                -x["paired"]["negative_time_blocks"],
                -x["logloss_delta"],
            ),
        )
        selected_name = selected["name"]
        test_base = baseline_decorate(k1, test0)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R32 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[selected_name], test0, FEATURE_SETS[selected_name])
        mt = metrics(test)
        paired_t = paired_blocks(test_base, test)
        test_gain = mt["hits"] - base_t["hits"]
        test_ll_delta = mt["logloss"] - base_t["logloss"]
        historical_test = {
            "baseline": base_t,
            "candidate": mt,
            "gain_hits": test_gain,
            "gain_top1_pp": 100 * (mt["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": test_ll_delta,
            "brier_delta": mt["brier"] - base_t["brier"],
            "rps_delta": mt["rps"] - base_t["rps"],
            "paired": paired_t,
        }
        batch005_eligible = (
            test_gain >= MIN_TEST_GAIN_HITS_FOR_BATCH005
            and paired_t["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and paired_t["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and test_ll_delta <= MAX_TEST_LOGLOSS_WORSEN
        )
        stop_reason = None if batch005_eligible else "FROZEN_COACH_FORMATION_FEATURE_SET_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        historical_test = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_COACH_FORMATION_GAIN"

    summary = {
        "schema_version": "football3-top1-r32-coach-formation-context",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_NEW_PREMATCH_INFORMATION_FAMILY_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "f401b3afaa697b9a3687fe66e2e071496f724a49",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "same_date_lineup_updates_withheld": True,
            "current_match_lineup_or_formation_used": False,
            "current_match_coach_label_used": False,
            "historical_lineup_rows_used_only_after_match_date": True,
            "odds_used": False,
            "market_prices_used": False,
            "injury_data_used": False,
            "weather_used": False,
            "feature_family_grid_predeclared": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Do strictly prior coach continuity and prior formation-stability signals add stable 1X2 Top1 information beyond K1 after rest/schedule failed historical confirmation?",
        "prematch_information_family": {
            "family": "HISTORICAL_COACH_AND_FORMATION_CONTEXT",
            "source": source_meta,
            "causal_contract": "fixture_lineups from the current match are never visible to that match; a match's coach/formation row updates team state only after all matches on that date have been predicted",
            "candidate_feature_sets": FEATURE_SETS,
            "validation_coverage": info_coverage(val0),
            "test_coverage": info_coverage(test0),
        },
        "selection_contract": {
            "min_validation_gain_hits": MIN_VALIDATION_GAIN_HITS,
            "min_positive_validation_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "max_validation_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN,
            "min_test_gain_hits_for_batch005": MIN_TEST_GAIN_HITS_FOR_BATCH005,
            "min_positive_test_blocks": MIN_POSITIVE_TEST_BLOCKS,
            "max_negative_test_blocks": MAX_NEGATIVE_TEST_BLOCKS,
            "max_test_logloss_worsen": MAX_TEST_LOGLOSS_WORSEN,
        },
        "controls": {
            "K1_validation": base_v,
            "R31_stop_reason": r31_summary["batch005_decision"]["stop_reason"],
        },
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": historical_test,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_COACH_FORMATION_MODEL" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "ADD_PRIOR_PLAYER_CONTINUITY_INFORMATION_FAMILY_FROM_FIXTURE_PLAYERS; DO_NOT_USE_CURRENT_MATCH_STARTING_XI WITHOUT_A_KNOWN_AT_GUARD",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r32.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r32.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"] and g["same_date_lineup_updates_withheld"]
    assert not g["current_match_lineup_or_formation_used"] and not g["current_match_coach_label_used"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["model_hyperparameter_search_used"] and g["candidate_selected_on_validation_only"]
    assert not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert len(s["validation_candidates"]) == len(FEATURE_SETS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_feature_set"] is not None
        assert s["historical_test_confirmation"]["gain_hits"] >= MIN_TEST_GAIN_HITS_FOR_BATCH005
    print("R32_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r32.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
