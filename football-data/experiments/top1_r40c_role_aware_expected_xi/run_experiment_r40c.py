#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R40B_DIR = HERE.parent / "top1_r40b_expected_xi_player_strength"
sys.path.insert(0, str(R40B_DIR))
import run_experiment_r40b as r40b  # noqa: E402

r9 = r40b.r9
r33 = r40b.r33
PLAYERS_URL = r33.PLAYERS_URL
EXPECTED_PLAYERS_SHA = r40b.EXPECTED_FIXTURE_PLAYERS_SHA256

DECAY = 0.78
LOOKBACK_XI = 8
MIN_PRIOR_XI = 3
SHRINK = 8.0
ROLES = ("G", "D", "M", "F")

POSITIONAL_RESULT_NAMES = []
for role in ROLES:
    POSITIONAL_RESULT_NAMES += [f"home_{role}_result", f"away_{role}_result", f"diff_{role}_result"]
POSITIONAL_RESULT_NAMES += ["home_role_known_share", "away_role_known_share", "role_known_share_diff"]

POSITIONAL_XG_NAMES = []
for role in ROLES:
    POSITIONAL_XG_NAMES += [f"home_{role}_attack", f"away_{role}_attack", f"diff_{role}_attack"]
    POSITIONAL_XG_NAMES += [f"home_{role}_defense", f"away_{role}_defense", f"diff_{role}_defense"]
POSITIONAL_XG_NAMES += ["home_role_known_share", "away_role_known_share", "role_known_share_diff"]

ROLE_BALANCE_NAMES = []
for role in ROLES:
    ROLE_BALANCE_NAMES += [f"home_{role}_share", f"away_{role}_share", f"diff_{role}_share"]
ROLE_BALANCE_NAMES += ["home_xi_certainty", "away_xi_certainty", "xi_certainty_diff"]

FEATURE_SETS = {
    "POSITIONAL_RESULT_STRENGTH": POSITIONAL_RESULT_NAMES,
    "POSITIONAL_XG_ATTACK_DEFENSE": POSITIONAL_XG_NAMES,
    "POSITIONAL_COMBINED": POSITIONAL_RESULT_NAMES + POSITIONAL_XG_NAMES + ROLE_BALANCE_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def norm_role(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    s = str(x).strip().upper()
    if not s:
        return None
    c = s[0]
    return c if c in ROLES else None


class TeamState:
    def __init__(self):
        self.xis = deque(maxlen=LOOKBACK_XI)


class Ledger:
    def __init__(self):
        self.result_sum = defaultdict(float)
        self.attack_sum = defaultdict(float)
        self.defense_sum = defaultdict(float)
        self.n = defaultdict(int)
        self.last_role = {}

    def values(self, pid):
        n = self.n[pid]
        den = n + SHRINK
        return self.result_sum[pid] / den, self.attack_sum[pid] / den, self.defense_sum[pid] / den, n

    def update(self, starter_rows, result_resid, attack_resid, defense_resid):
        for pid, role in starter_rows:
            self.result_sum[pid] += float(np.clip(result_resid, -2.0, 2.0))
            self.attack_sum[pid] += float(np.clip(attack_resid, -3.0, 3.0))
            self.defense_sum[pid] += float(np.clip(defense_resid, -3.0, 3.0))
            self.n[pid] += 1
            if role:
                self.last_role[pid] = role


def expected_players(st: TeamState):
    xs = list(st.xis)
    if len(xs) < MIN_PRIOR_XI:
        return None, 0.0
    raw = defaultdict(float)
    total = 0.0
    for lag, xi in enumerate(reversed(xs)):
        w = DECAY ** lag
        total += w
        for pid in xi:
            raw[pid] += w
    ranked = sorted(raw.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:11]
    if len(ranked) < 10:
        return None, 0.0
    certainty = float(np.mean([w / total for _, w in ranked])) if total else 0.0
    return [pid for pid, _ in ranked], certainty


def side_features(st: TeamState, ledger: Ledger):
    pids, certainty = expected_players(st)
    if not pids:
        out = {"xi_known": 0.0, "xi_certainty": 0.0, "role_known_share": 0.0}
        for role in ROLES:
            out[f"{role}_result"] = 0.0
            out[f"{role}_attack"] = 0.0
            out[f"{role}_defense"] = 0.0
            out[f"{role}_share"] = 0.0
        return out

    buckets = {role: [] for role in ROLES}
    known = 0
    for pid in pids:
        role = ledger.last_role.get(pid)
        if role in ROLES:
            known += 1
            buckets[role].append(ledger.values(pid))
    out = {"xi_known": 1.0, "xi_certainty": certainty, "role_known_share": known / len(pids)}
    for role in ROLES:
        vals = buckets[role]
        if vals:
            out[f"{role}_result"] = float(np.mean([x[0] for x in vals]))
            out[f"{role}_attack"] = float(np.mean([x[1] for x in vals]))
            out[f"{role}_defense"] = float(np.mean([x[2] for x in vals]))
        else:
            out[f"{role}_result"] = out[f"{role}_attack"] = out[f"{role}_defense"] = 0.0
        out[f"{role}_share"] = len(vals) / len(pids)
    return out


def context_features(row, states, ledger):
    h = side_features(states[row["home_team"]], ledger)
    a = side_features(states[row["away_team"]], ledger)
    z = {
        "home_xi_certainty": h["xi_certainty"], "away_xi_certainty": a["xi_certainty"],
        "xi_certainty_diff": h["xi_certainty"] - a["xi_certainty"],
        "home_role_known_share": h["role_known_share"], "away_role_known_share": a["role_known_share"],
        "role_known_share_diff": h["role_known_share"] - a["role_known_share"],
    }
    for role in ROLES:
        for key in ("result", "attack", "defense", "share"):
            z[f"home_{role}_{key}"] = h[f"{role}_{key}"]
            z[f"away_{role}_{key}"] = a[f"{role}_{key}"]
            z[f"diff_{role}_{key}"] = h[f"{role}_{key}"] - a[f"{role}_{key}"]
    return z


def download_player_rows(rows):
    data = HERE / "data"
    data.mkdir(parents=True, exist_ok=True)
    path = data / "fixture_players.parquet"
    req = urllib.request.Request(PLAYERS_URL, headers={"User-Agent": "football3-r40c"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    sha = r40b.r33.fsha(path)
    if sha != EXPECTED_PLAYERS_SHA:
        raise RuntimeError(f"fixture_players source drift: {sha}")
    wanted = {int(r["game_id"]) for r in rows}
    df = pd.read_parquet(path, columns=["fixture_id", "team_id", "player_id", "is_starter", "position"])
    df = df[(df["fixture_id"].isin(wanted)) & (df["is_starter"] == True)].copy()
    out = {}
    for (fid, tid), g in df.groupby(["fixture_id", "team_id"], sort=False):
        starters = []
        for x in g.itertuples(index=False):
            starters.append((int(x.player_id), norm_role(x.position)))
        out[(str(int(fid)), str(int(tid)))] = starters
    return out, sha, int(len(df)), path


def build_history():
    rows = r9.load()
    player_map, player_sha, matched_starters, path = download_player_rows(rows)
    base = r9.S()
    states = defaultdict(TeamState)
    ledger = Ledger()
    pred = []
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            raw = base.pred(row)
            cf = context_features(row, states, ledger)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))
        for row, raw in pending:
            fid = str(row["game_id"])
            hi = player_map.get((fid, row["home_team"]), [])
            ai = player_map.get((fid, row["away_team"]), [])
            y = r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"])
            ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[row["home_team"]].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[row["away_team"]].xis.append(frozenset(pid for pid, _ in ai))
            base.update(row, raw)
    try:
        path.unlink()
    except Exception:
        pass
    return pred, {"fixture_players_sha256": player_sha, "matched_starter_rows": matched_starters}


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
        for cls, p in zip(classes, probs):
            v[int(cls)] = float(p)
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def coverage(rows):
    c = [r["context_features"] for r in rows]
    return {
        "rows": len(rows),
        "home_role_known_share_mean": float(np.mean([x["home_role_known_share"] for x in c])),
        "away_role_known_share_mean": float(np.mean([x["away_role_known_share"] for x in c])),
        "home_goalkeeper_share_mean": float(np.mean([x["home_G_share"] for x in c])),
        "away_goalkeeper_share_mean": float(np.mean([x["away_G_share"] for x in c])),
    }


def run():
    pred, source_meta = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]
    k1 = r33.baseline_model(train)
    val_base = r33.baseline_decorate(k1, val)
    base_v = r33.metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R40C K1 validation reproduction failed: {base_v['hits']}")

    candidates, models = [], {}
    for name, names in FEATURE_SETS.items():
        model = fit_model(train, names)
        models[name] = model
        vr = decorate(model, val, names)
        vm = r33.metrics(vr)
        pair = r33.paired_blocks(val_base, vr)
        gain = int(vm["hits"] - base_v["hits"])
        logdelta = float(vm["logloss"] - base_v["logloss"])
        viable = gain >= MIN_VALIDATION_GAIN_HITS and pair["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS and pair["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS and logdelta <= MAX_VALIDATION_LOGLOSS_WORSEN
        candidates.append({"name": name, "features": names, "viable": viable, "validation": vm, "gain_hits": gain, "gain_top1_pp": 100.0 * (vm["top1_accuracy"] - base_v["top1_accuracy"]), "logloss_delta": logdelta, "brier_delta": float(vm["brier"] - base_v["brier"]), "rps_delta": float(vm["rps"] - base_v["rps"]), "paired": pair})

    viable = [x for x in candidates if x["viable"]]
    selected = max(viable, key=lambda x: (x["gain_hits"], -x["logloss_delta"], x["paired"]["positive_time_blocks"])) if viable else None
    test_confirmation = None
    confirmed = False
    stop = "NO_VALIDATION_ROBUST_ROLE_AWARE_EXPECTED_XI_GAIN"
    if selected is not None:
        test_base = r33.baseline_decorate(k1, test)
        base_t = r33.metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R40C K1 test reproduction failed: {base_t['hits']}")
        tr = decorate(models[selected["name"]], test, selected["features"])
        tm = r33.metrics(tr)
        pair = r33.paired_blocks(test_base, tr)
        gain = int(tm["hits"] - base_t["hits"])
        logdelta = float(tm["logloss"] - base_t["logloss"])
        confirmed = gain >= MIN_TEST_GAIN_HITS and pair["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS and pair["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS and logdelta <= MAX_TEST_LOGLOSS_WORSEN
        test_confirmation = {"baseline": base_t, "candidate": tm, "gain_hits": gain, "gain_top1_pp": 100.0 * (tm["top1_accuracy"] - base_t["top1_accuracy"]), "logloss_delta": logdelta, "brier_delta": float(tm["brier"] - base_t["brier"]), "rps_delta": float(tm["rps"] - base_t["rps"]), "paired": pair}
        stop = None if confirmed else "FROZEN_ROLE_AWARE_EXPECTED_XI_FAILED_HISTORICAL_TEST_CONFIRMATION"

    summary = {
        "schema_version": "football3-top1-r40c-role-aware-expected-xi",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_STRICT_PRIOR_POSITIONAL_PLAYER_QUALITY_AFTER_R40A2",
        "formal_weight": 0,
        "governance": {"base_r40a2_commit": "3bb255ca3b9dde342f834b891b9efef45429a624", "snapshot_rows": 20000, "strict_prior_features": True, "same_date_results_xg_roles_and_player_updates_withheld": True, "current_match_starting_xi_used": False, "current_match_position_used": False, "injury_or_suspension_used": False, "odds_used": False, "market_prices_used": False, "feature_family_grid_predeclared": True, "model_hyperparameter_search_used": False, "candidate_selected_on_validation_only": True, "test_used_for_candidate_selection": False, "formal_promotion_allowed_from_this_run": False},
        "question": "Does role-aware expected-XI player strength (G/D/M/F separated) add stable 1X2 Top1 information beyond K1?",
        "information_family": {"family": "ROLE_AWARE_EXPECTED_XI_PLAYER_QUALITY", "source": source_meta, "roles": list(ROLES), "candidate_feature_sets": FEATURE_SETS, "validation_coverage": coverage(val), "test_coverage": coverage(test)},
        "selection_contract": {"min_validation_gain_hits": MIN_VALIDATION_GAIN_HITS, "min_positive_validation_blocks": MIN_POSITIVE_VALIDATION_BLOCKS, "max_negative_validation_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS, "max_validation_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN, "min_test_gain_hits": MIN_TEST_GAIN_HITS, "min_positive_test_blocks": MIN_POSITIVE_TEST_BLOCKS, "max_negative_test_blocks": MAX_NEGATIVE_TEST_BLOCKS, "max_test_logloss_worsen": MAX_TEST_LOGLOSS_WORSEN},
        "controls": {"K1_validation": base_v, "R40B": "DO_NOT_PROMOTE_EXPECTED_XI_UNPOSITIONED_PLAYER_STRENGTH"},
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": test_confirmation,
        "decision": {"historically_confirmed": confirmed, "action": "KEEP_R40C_FOR_INDEPENDENT_REPLICATION" if confirmed else "DO_NOT_PROMOTE_R40C", "stop_reason": stop},
        "next_if_fail": "Do not retune on this test. Preserve the recognition layer and seek timestamp-auditable current availability information, especially confirmed XI/injury/suspension, before more 1X2 player engineering.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r40c.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def verify():
    s = json.loads((OUT / "summary_r40c.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_xg_roles_and_player_updates_withheld"]
    assert not g["current_match_starting_xi_used"] and not g["current_match_position_used"]
    assert not g["injury_or_suspension_used"] and not g["odds_used"] and not g["market_prices_used"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    print("R40C_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r40c.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
