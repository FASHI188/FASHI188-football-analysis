#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R32_DIR = HERE.parent / "top1_r32_coach_formation_context"
sys.path.insert(0, str(R32_DIR))
import run_experiment_r32 as r32  # noqa: E402

r9 = r32.r9

PLAYERS_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/fixture_players.parquet?download=true"

STABILITY_NAMES = [
    "home_player_history_known", "away_player_history_known", "both_player_history_known",
    "home_last_xi_size", "away_last_xi_size", "last_xi_size_diff",
    "home_last_transition_overlap11", "away_last_transition_overlap11", "transition_overlap11_diff",
    "home_last_transition_jaccard", "away_last_transition_jaccard", "transition_jaccard_diff",
    "home_mean_overlap11_3", "away_mean_overlap11_3", "mean_overlap11_3_diff",
    "home_mean_jaccard_3", "away_mean_jaccard_3", "mean_jaccard_3_diff",
    "home_distinct_starters5", "away_distinct_starters5", "distinct_starters5_diff",
    "home_core_starters3of5", "away_core_starters3of5", "core_starters3of5_diff",
    "home_history_matches_log", "away_history_matches_log",
]
CAPTAIN_NAMES = [
    "home_captain_known", "away_captain_known", "both_captain_known",
    "home_captain_tenure_log", "away_captain_tenure_log", "captain_tenure_log_diff",
    "home_recent_captain_change_le3", "away_recent_captain_change_le3", "either_recent_captain_change_le3",
]
FEATURE_SETS = {
    "STARTER_STABILITY_ONLY": STABILITY_NAMES,
    "CAPTAIN_ONLY": CAPTAIN_NAMES,
    "PLAYER_PLUS_CAPTAIN": STABILITY_NAMES + CAPTAIN_NAMES,
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


def download_players():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "fixture_players.parquet"
    req = urllib.request.Request(PLAYERS_URL, headers={"User-Agent": "football3-research-r33"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    sha = fsha(path)
    cols = ["fixture_id", "team_id", "player_id", "is_starter", "captain"]
    df = pd.read_parquet(path, columns=cols)
    return path, sha, df


def build_player_map(df, rows):
    wanted = {int(r["game_id"]) for r in rows}
    sub = df[df["fixture_id"].isin(wanted)].copy()
    out = {}
    for (fid, tid), g in sub.groupby(["fixture_id", "team_id"], sort=False):
        starters = frozenset(int(x) for x in g.loc[g["is_starter"] == True, "player_id"].dropna().tolist())
        caps = [int(x) for x in g.loc[g["captain"] == True, "player_id"].dropna().tolist()]
        captain = caps[0] if caps else None
        out[(str(int(fid)), str(int(tid)))] = {"starters": starters, "captain": captain}
    return out, int(len(sub))


class TeamPlayerState:
    def __init__(self):
        self.xis = deque(maxlen=6)
        self.matches = 0
        self.last_captain = None
        self.captain_tenure = 0
        self.since_captain_change = None


def overlap11(a, b):
    if not a or not b:
        return 0.0
    return float(len(a & b) / 11.0)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    u = a | b
    return float(len(a & b) / len(u)) if u else 0.0


def team_features(st: TeamPlayerState):
    xs = list(st.xis)
    known = float(len(xs) >= 1)
    last = xs[-1] if xs else frozenset()
    if len(xs) >= 2:
        ov_last = overlap11(xs[-1], xs[-2])
        jac_last = jaccard(xs[-1], xs[-2])
    else:
        ov_last = jac_last = 0.0
    transitions = list(zip(xs[:-1], xs[1:]))[-3:]
    ov3 = float(np.mean([overlap11(a, b) for a, b in transitions])) if transitions else 0.0
    jac3 = float(np.mean([jaccard(a, b) for a, b in transitions])) if transitions else 0.0
    recent5 = xs[-5:]
    cnt = Counter(p for xi in recent5 for p in xi)
    distinct5 = float(len(cnt))
    core = float(sum(int(v >= 3) for v in cnt.values()))
    cap_known = float(st.last_captain is not None)
    cap_tenure = math.log1p(min(30, st.captain_tenure)) if cap_known else 0.0
    cap_change = float(st.since_captain_change is not None and st.since_captain_change <= 2)
    return {
        "player_history_known": known,
        "last_xi_size": float(len(last)),
        "last_transition_overlap11": ov_last,
        "last_transition_jaccard": jac_last,
        "mean_overlap11_3": ov3,
        "mean_jaccard_3": jac3,
        "distinct_starters5": distinct5,
        "core_starters3of5": core,
        "history_matches_log": math.log1p(min(100, st.matches)),
        "captain_known": cap_known,
        "captain_tenure_log": cap_tenure,
        "recent_captain_change_le3": cap_change,
    }


def context_features(row, states):
    h = team_features(states[row["home_team"]])
    a = team_features(states[row["away_team"]])
    z = {
        "home_player_history_known": h["player_history_known"],
        "away_player_history_known": a["player_history_known"],
        "both_player_history_known": h["player_history_known"] * a["player_history_known"],
        "home_last_xi_size": h["last_xi_size"], "away_last_xi_size": a["last_xi_size"],
        "last_xi_size_diff": h["last_xi_size"] - a["last_xi_size"],
        "home_last_transition_overlap11": h["last_transition_overlap11"], "away_last_transition_overlap11": a["last_transition_overlap11"],
        "transition_overlap11_diff": h["last_transition_overlap11"] - a["last_transition_overlap11"],
        "home_last_transition_jaccard": h["last_transition_jaccard"], "away_last_transition_jaccard": a["last_transition_jaccard"],
        "transition_jaccard_diff": h["last_transition_jaccard"] - a["last_transition_jaccard"],
        "home_mean_overlap11_3": h["mean_overlap11_3"], "away_mean_overlap11_3": a["mean_overlap11_3"],
        "mean_overlap11_3_diff": h["mean_overlap11_3"] - a["mean_overlap11_3"],
        "home_mean_jaccard_3": h["mean_jaccard_3"], "away_mean_jaccard_3": a["mean_jaccard_3"],
        "mean_jaccard_3_diff": h["mean_jaccard_3"] - a["mean_jaccard_3"],
        "home_distinct_starters5": h["distinct_starters5"], "away_distinct_starters5": a["distinct_starters5"],
        "distinct_starters5_diff": h["distinct_starters5"] - a["distinct_starters5"],
        "home_core_starters3of5": h["core_starters3of5"], "away_core_starters3of5": a["core_starters3of5"],
        "core_starters3of5_diff": h["core_starters3of5"] - a["core_starters3of5"],
        "home_history_matches_log": h["history_matches_log"], "away_history_matches_log": a["history_matches_log"],
        "home_captain_known": h["captain_known"], "away_captain_known": a["captain_known"],
        "both_captain_known": h["captain_known"] * a["captain_known"],
        "home_captain_tenure_log": h["captain_tenure_log"], "away_captain_tenure_log": a["captain_tenure_log"],
        "captain_tenure_log_diff": h["captain_tenure_log"] - a["captain_tenure_log"],
        "home_recent_captain_change_le3": h["recent_captain_change_le3"],
        "away_recent_captain_change_le3": a["recent_captain_change_le3"],
        "either_recent_captain_change_le3": float(h["recent_captain_change_le3"] or a["recent_captain_change_le3"]),
    }
    return z


def update_team(st: TeamPlayerState, info):
    if info is None:
        return
    xi = info.get("starters") or frozenset()
    if xi:
        st.xis.append(xi)
        st.matches += 1
    captain = info.get("captain")
    if captain is not None:
        if st.last_captain is None:
            st.last_captain = captain
            st.captain_tenure = 1
            st.since_captain_change = None
        elif captain == st.last_captain:
            st.captain_tenure += 1
            if st.since_captain_change is not None:
                st.since_captain_change += 1
        else:
            st.last_captain = captain
            st.captain_tenure = 1
            st.since_captain_change = 0


def build_history():
    rows = r9.load()
    path, sha, df = download_players()
    player_map, matched_rows = build_player_map(df, rows)
    base = r9.S()
    states = defaultdict(TeamPlayerState)
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = context_features(row, states)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))
        # Strict prior: current-date results/xG and current-match player identities are
        # withheld until every fixture on that date has been predicted.
        for row, raw in pending:
            base.update(row, raw)
            fid = str(row["game_id"])
            update_team(states[row["home_team"]], player_map.get((fid, row["home_team"])))
            update_team(states[row["away_team"]], player_map.get((fid, row["away_team"])))
    meta = {
        "fixture_players_url": PLAYERS_URL,
        "fixture_players_sha256": sha,
        "fixture_players_rows_total": int(len(df)),
        "snapshot_fixture_player_rows_matched": matched_rows,
        "snapshot_matches_with_both_team_player_rows": int(sum(
            1 for r in rows
            if (str(r["game_id"]), r["home_team"]) in player_map and (str(r["game_id"]), r["away_team"]) in player_map
        )),
        "snapshot_matches_with_both_starting_xi": int(sum(
            1 for r in rows
            if len(player_map.get((str(r["game_id"]), r["home_team"]), {}).get("starters", ())) >= 10
            and len(player_map.get((str(r["game_id"]), r["away_team"]), {}).get("starters", ())) >= 10
        )),
    }
    try:
        path.unlink()
    except Exception:
        pass
    return pred, meta


def x_for(rec, names):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in names]


def fit_model(train, names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([x_for(r, names) for r in train], [r["y"] for r in train])
    return m


def decorate(model, rows, names):
    pr = model.predict_proba([x_for(r, names) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, probs in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, probs): v[int(cls)] = float(p)
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def baseline_model(train):
    return r32.baseline_model(train)


def baseline_decorate(k1, rows):
    return r32.baseline_decorate(k1, rows)


def metrics(rows):
    return r32.metrics(rows)


def paired_blocks(base_rows, cand_rows):
    return r32.paired_blocks(base_rows, cand_rows)


def coverage(rows):
    n = len(rows)
    if not n: return {}
    c = [r["context_features"] for r in rows]
    return {
        "rows": n,
        "home_prior_player_history_known_rate": float(np.mean([x["home_player_history_known"] for x in c])),
        "away_prior_player_history_known_rate": float(np.mean([x["away_player_history_known"] for x in c])),
        "both_prior_player_history_known_rate": float(np.mean([x["both_player_history_known"] for x in c])),
        "home_prior_captain_known_rate": float(np.mean([x["home_captain_known"] for x in c])),
        "away_prior_captain_known_rate": float(np.mean([x["away_captain_known"] for x in c])),
    }


def run():
    pred, source_meta = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]
    k1 = baseline_model(train)
    val_base = baseline_decorate(k1, val)
    base_v = metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R33 K1 validation reproduction failed: {base_v['hits']}")

    candidates = []
    models = {}
    for name, names in FEATURE_SETS.items():
        m = fit_model(train, names)
        models[name] = m
        vr = decorate(m, val, names)
        vm = metrics(vr)
        pair = paired_blocks(val_base, vr)
        gain = int(vm["hits"] - base_v["hits"])
        logdelta = float(vm["logloss"] - base_v["logloss"])
        viable = (
            gain >= MIN_VALIDATION_GAIN_HITS
            and pair["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and pair["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
            and logdelta <= MAX_VALIDATION_LOGLOSS_WORSEN
        )
        candidates.append({
            "name": name, "features": names, "viable": viable, "validation": vm,
            "gain_hits": gain, "gain_top1_pp": 100.0 * (vm["top1_accuracy"] - base_v["top1_accuracy"]),
            "logloss_delta": logdelta, "brier_delta": float(vm["brier"] - base_v["brier"]),
            "rps_delta": float(vm["rps"] - base_v["rps"]), "paired": pair,
        })

    viable = [x for x in candidates if x["viable"]]
    selected = max(viable, key=lambda x: (x["gain_hits"], -x["logloss_delta"], x["paired"]["positive_time_blocks"])) if viable else None
    test_confirmation = None
    batch005 = False
    stop = "NO_VALIDATION_ROBUST_PRIOR_PLAYER_CONTINUITY_GAIN"
    if selected is not None:
        test_base = baseline_decorate(k1, test)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R33 K1 test reproduction failed: {base_t['hits']}")
        tr = decorate(models[selected["name"]], test, selected["features"])
        tm = metrics(tr)
        pair = paired_blocks(test_base, tr)
        gain = int(tm["hits"] - base_t["hits"])
        logdelta = float(tm["logloss"] - base_t["logloss"])
        batch005 = (
            gain >= MIN_TEST_GAIN_HITS_FOR_BATCH005
            and pair["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and pair["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and logdelta <= MAX_TEST_LOGLOSS_WORSEN
        )
        test_confirmation = {
            "baseline": base_t, "candidate": tm, "gain_hits": gain,
            "gain_top1_pp": 100.0 * (tm["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": logdelta, "brier_delta": float(tm["brier"] - base_t["brier"]),
            "rps_delta": float(tm["rps"] - base_t["rps"]), "paired": pair,
        }
        stop = None if batch005 else "FROZEN_PRIOR_PLAYER_CONTINUITY_FAILED_HISTORICAL_TEST_CONFIRMATION"

    summary = {
        "schema_version": "football3-top1-r33-prior-player-continuity",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_NEW_PREMATCH_INFORMATION_FAMILY_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "03c199cb345b3318c7d8ad8f042319e694c20bf5",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "same_date_player_updates_withheld": True,
            "current_match_starting_xi_used": False,
            "current_match_captain_used": False,
            "historical_player_rows_used_only_after_match_date": True,
            "odds_used": False, "market_prices_used": False,
            "feature_family_grid_predeclared": True, "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False, "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Do strictly prior starting-XI stability and captain-continuity states add stable 1X2 Top1 information beyond K1?",
        "prematch_information_family": {
            "family": "PRIOR_PLAYER_AND_CAPTAIN_CONTINUITY",
            "source": source_meta,
            "causal_contract": "current-match player identities are never visible to that match; fixture_players update team state only after all fixtures on the match date are predicted",
            "candidate_feature_sets": FEATURE_SETS,
            "validation_coverage": coverage(val), "test_coverage": coverage(test),
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
        "controls": {"K1_validation": base_v, "R32_stop_reason": "NO_VALIDATION_ROBUST_COACH_FORMATION_GAIN"},
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": test_confirmation,
        "batch005_decision": {
            "eligible": batch005,
            "action": "SPEND_BATCH005_ON_FROZEN_PRIOR_PLAYER_CONTINUITY" if batch005 else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop,
        },
        "next_if_fail": "ADD_ANOTHER_INDEPENDENT_STRICTLY_PRIOR_INFORMATION_FAMILY; DO_NOT_USE_CURRENT_MATCH_XI OR POSTMATCH_PLAYER_STATS",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r33.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r33.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"] and g["same_date_player_updates_withheld"]
    assert not g["current_match_starting_xi_used"] and not g["current_match_captain_used"]
    assert not g["odds_used"] and not g["market_prices_used"] and not g["test_used_for_candidate_selection"]
    assert not g["batch005_used"] and not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    if s["batch005_decision"]["eligible"]:
        assert s["selected_feature_set"] is not None and s["historical_test_confirmation"]["gain_hits"] >= 1
    print("R33_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r33.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
