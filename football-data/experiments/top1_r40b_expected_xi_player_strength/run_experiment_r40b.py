#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R33_DIR = HERE.parent / "top1_r33_prior_player_continuity"
sys.path.insert(0, str(R33_DIR))
import run_experiment_r33 as r33  # noqa: E402

r9 = r33.r9

EXPECTED_FIXTURES_SHA256 = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_MATCH_STATS_SHA256 = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
EXPECTED_FIXTURE_PLAYERS_SHA256 = "a315191ffac285a11597758c859cd88b97ea8aba89374a8fb299ee754a2f76ad"

DECAY = 0.78
LOOKBACK_XI = 8
MIN_PRIOR_XI = 3
SHRINK = 8.0
RESID_CLIP = 2.0
XG_RESID_CLIP = 3.0

RESULT_NAMES = [
    "home_xi_known", "away_xi_known", "both_xi_known",
    "home_result_strength", "away_result_strength", "result_strength_diff",
    "home_player_exp_log", "away_player_exp_log", "player_exp_log_diff",
    "home_residual_known_share", "away_residual_known_share", "residual_known_share_diff",
]
PROCESS_NAMES = [
    "home_xi_known", "away_xi_known", "both_xi_known",
    "home_attack_xg_resid", "away_attack_xg_resid", "attack_xg_resid_diff",
    "home_defense_xg_resid", "away_defense_xg_resid", "defense_xg_resid_diff",
    "home_net_xg_resid", "away_net_xg_resid", "net_xg_resid_diff",
    "home_player_exp_log", "away_player_exp_log", "player_exp_log_diff",
    "home_residual_known_share", "away_residual_known_share", "residual_known_share_diff",
]
COMBINED_NAMES = [
    "home_xi_known", "away_xi_known", "both_xi_known",
    "home_xi_certainty", "away_xi_certainty", "xi_certainty_diff",
    "home_result_strength", "away_result_strength", "result_strength_diff",
    "home_attack_xg_resid", "away_attack_xg_resid", "attack_xg_resid_diff",
    "home_defense_xg_resid", "away_defense_xg_resid", "defense_xg_resid_diff",
    "home_net_xg_resid", "away_net_xg_resid", "net_xg_resid_diff",
    "home_result_top3", "away_result_top3", "result_top3_diff",
    "home_result_bottom3", "away_result_bottom3", "result_bottom3_diff",
    "home_player_exp_log", "away_player_exp_log", "player_exp_log_diff",
    "home_residual_known_share", "away_residual_known_share", "residual_known_share_diff",
]
FEATURE_SETS = {
    "EXPECTED_XI_RESULT_RESIDUAL": RESULT_NAMES,
    "EXPECTED_XI_XG_PROCESS_RESIDUAL": PROCESS_NAMES,
    "EXPECTED_XI_COMBINED_PLAYER_STRENGTH": COMBINED_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


class TeamXIState:
    def __init__(self):
        self.xis = deque(maxlen=LOOKBACK_XI)
        self.matches = 0


class PlayerLedger:
    def __init__(self):
        self.result_sum = defaultdict(float)
        self.attack_xg_sum = defaultdict(float)
        self.defense_xg_sum = defaultdict(float)
        self.n = defaultdict(int)

    def player_values(self, p):
        n = self.n[p]
        den = n + SHRINK
        return (
            self.result_sum[p] / den,
            self.attack_xg_sum[p] / den,
            self.defense_xg_sum[p] / den,
            n,
        )

    def update(self, starters, result_resid, attack_xg_resid, defense_xg_resid):
        rr = float(np.clip(result_resid, -RESID_CLIP, RESID_CLIP))
        ar = float(np.clip(attack_xg_resid, -XG_RESID_CLIP, XG_RESID_CLIP))
        dr = float(np.clip(defense_xg_resid, -XG_RESID_CLIP, XG_RESID_CLIP))
        for p in starters:
            self.result_sum[p] += rr
            self.attack_xg_sum[p] += ar
            self.defense_xg_sum[p] += dr
            self.n[p] += 1


def expected_xi_features(st: TeamXIState, ledger: PlayerLedger):
    xs = list(st.xis)
    if len(xs) < MIN_PRIOR_XI:
        return {
            "xi_known": 0.0,
            "xi_certainty": 0.0,
            "result_strength": 0.0,
            "attack_xg_resid": 0.0,
            "defense_xg_resid": 0.0,
            "net_xg_resid": 0.0,
            "result_top3": 0.0,
            "result_bottom3": 0.0,
            "player_exp_log": 0.0,
            "residual_known_share": 0.0,
        }
    scores = defaultdict(float)
    denom = 0.0
    for lag, xi in enumerate(reversed(xs)):
        w = DECAY ** lag
        denom += w
        for p in xi:
            scores[p] += w
    ranked = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:11]
    players = [p for p, _ in ranked]
    if len(players) < 10:
        return {
            "xi_known": 0.0,
            "xi_certainty": 0.0,
            "result_strength": 0.0,
            "attack_xg_resid": 0.0,
            "defense_xg_resid": 0.0,
            "net_xg_resid": 0.0,
            "result_top3": 0.0,
            "result_bottom3": 0.0,
            "player_exp_log": 0.0,
            "residual_known_share": 0.0,
        }
    certainty = float(np.mean([s / denom for _, s in ranked])) if denom else 0.0
    vals = [ledger.player_values(p) for p in players]
    result = np.asarray([v[0] for v in vals], dtype=float)
    attack = np.asarray([v[1] for v in vals], dtype=float)
    defense = np.asarray([v[2] for v in vals], dtype=float)
    ns = np.asarray([v[3] for v in vals], dtype=float)
    ordered = np.sort(result)
    return {
        "xi_known": 1.0,
        "xi_certainty": certainty,
        "result_strength": float(result.mean()),
        "attack_xg_resid": float(attack.mean()),
        "defense_xg_resid": float(defense.mean()),
        "net_xg_resid": float((attack - defense).mean()),
        "result_top3": float(ordered[-3:].mean()),
        "result_bottom3": float(ordered[:3].mean()),
        "player_exp_log": float(np.mean(np.log1p(ns))),
        "residual_known_share": float(np.mean(ns > 0)),
    }


def context_features(row, team_states, ledger):
    h = expected_xi_features(team_states[row["home_team"]], ledger)
    a = expected_xi_features(team_states[row["away_team"]], ledger)
    z = {
        "home_xi_known": h["xi_known"],
        "away_xi_known": a["xi_known"],
        "both_xi_known": h["xi_known"] * a["xi_known"],
    }
    for key in (
        "xi_certainty", "result_strength", "attack_xg_resid", "defense_xg_resid",
        "net_xg_resid", "result_top3", "result_bottom3", "player_exp_log", "residual_known_share",
    ):
        z[f"home_{key}"] = h[key]
        z[f"away_{key}"] = a[key]
        z[f"{key}_diff"] = h[key] - a[key]
    return z


def build_history():
    rows = r9.load()
    manifest = json.loads((r9.DATA / "source_manifest_r9b.json").read_text(encoding="utf-8"))
    if manifest["fixtures_sha256"] != EXPECTED_FIXTURES_SHA256:
        raise RuntimeError("fixtures source drift")
    if manifest["match_stats_sha256"] != EXPECTED_MATCH_STATS_SHA256:
        raise RuntimeError("match_stats source drift")

    player_path, player_sha, player_df = r33.download_players()
    if player_sha != EXPECTED_FIXTURE_PLAYERS_SHA256:
        raise RuntimeError(f"fixture_players source drift: {player_sha}")
    player_map, matched_rows = r33.build_player_map(player_df, rows)

    base = r9.S()
    team_states = defaultdict(TeamXIState)
    ledger = PlayerLedger()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = context_features(row, team_states, ledger)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))

        # Strict PIT: only after every target on this date has been predicted do we reveal
        # target outcomes/xG and actual target XI to future matches.
        for row, raw in pending:
            fid = str(row["game_id"])
            hi = player_map.get((fid, row["home_team"]))
            ai = player_map.get((fid, row["away_team"]))
            y = r9.actual(row)
            home_u = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            away_u = 1.0 - home_u
            home_e = float(raw["p_home"] + 0.5 * raw["p_draw"])
            away_e = float(raw["p_away"] + 0.5 * raw["p_draw"])
            hx = float(row["home_xg"])
            ax = float(row["away_xg"])
            if hi and hi.get("starters"):
                starters = hi["starters"]
                ledger.update(
                    starters,
                    home_u - home_e,
                    hx - float(raw["xg_mu_home"]),
                    ax - float(raw["xg_mu_away"]),
                )
                team_states[row["home_team"]].xis.append(frozenset(starters))
                team_states[row["home_team"]].matches += 1
            if ai and ai.get("starters"):
                starters = ai["starters"]
                ledger.update(
                    starters,
                    away_u - away_e,
                    ax - float(raw["xg_mu_away"]),
                    hx - float(raw["xg_mu_home"]),
                )
                team_states[row["away_team"]].xis.append(frozenset(starters))
                team_states[row["away_team"]].matches += 1
            base.update(row, raw)

    source_meta = {
        "fixtures_sha256": manifest["fixtures_sha256"],
        "match_stats_sha256": manifest["match_stats_sha256"],
        "fixture_players_sha256": player_sha,
        "fixture_players_rows_total": int(len(player_df)),
        "snapshot_fixture_player_rows_matched": int(matched_rows),
        "current_match_starting_xi_used": False,
        "player_updates_after_prediction_date_only": True,
    }
    try:
        player_path.unlink()
    except Exception:
        pass
    return pred, source_meta


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
    if not rows:
        return {}
    c = [r["context_features"] for r in rows]
    return {
        "rows": len(rows),
        "home_expected_xi_known_rate": float(np.mean([x["home_xi_known"] for x in c])),
        "away_expected_xi_known_rate": float(np.mean([x["away_xi_known"] for x in c])),
        "both_expected_xi_known_rate": float(np.mean([x["both_xi_known"] for x in c])),
        "mean_home_xi_certainty": float(np.mean([x["home_xi_certainty"] for x in c])),
        "mean_away_xi_certainty": float(np.mean([x["away_xi_certainty"] for x in c])),
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
        raise RuntimeError(f"R40B K1 validation reproduction failed: {base_v['hits']}")

    candidates = []
    models = {}
    for name, names in FEATURE_SETS.items():
        model = fit_model(train, names)
        models[name] = model
        vr = decorate(model, val, names)
        vm = r33.metrics(vr)
        pair = r33.paired_blocks(val_base, vr)
        gain = int(vm["hits"] - base_v["hits"])
        logdelta = float(vm["logloss"] - base_v["logloss"])
        viable = (
            gain >= MIN_VALIDATION_GAIN_HITS
            and pair["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and pair["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
            and logdelta <= MAX_VALIDATION_LOGLOSS_WORSEN
        )
        candidates.append({
            "name": name,
            "features": names,
            "viable": viable,
            "validation": vm,
            "gain_hits": gain,
            "gain_top1_pp": 100.0 * (vm["top1_accuracy"] - base_v["top1_accuracy"]),
            "logloss_delta": logdelta,
            "brier_delta": float(vm["brier"] - base_v["brier"]),
            "rps_delta": float(vm["rps"] - base_v["rps"]),
            "paired": pair,
        })

    viable = [x for x in candidates if x["viable"]]
    selected = max(
        viable,
        key=lambda x: (x["gain_hits"], -x["logloss_delta"], x["paired"]["positive_time_blocks"]),
    ) if viable else None

    test_confirmation = None
    replicated = False
    stop = "NO_VALIDATION_ROBUST_EXPECTED_XI_PLAYER_STRENGTH_GAIN"
    if selected is not None:
        test_base = r33.baseline_decorate(k1, test)
        base_t = r33.metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R40B K1 test reproduction failed: {base_t['hits']}")
        tr = decorate(models[selected["name"]], test, selected["features"])
        tm = r33.metrics(tr)
        pair = r33.paired_blocks(test_base, tr)
        gain = int(tm["hits"] - base_t["hits"])
        logdelta = float(tm["logloss"] - base_t["logloss"])
        replicated = (
            gain >= MIN_TEST_GAIN_HITS
            and pair["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and pair["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and logdelta <= MAX_TEST_LOGLOSS_WORSEN
        )
        test_confirmation = {
            "baseline": base_t,
            "candidate": tm,
            "gain_hits": gain,
            "gain_top1_pp": 100.0 * (tm["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": logdelta,
            "brier_delta": float(tm["brier"] - base_t["brier"]),
            "rps_delta": float(tm["rps"] - base_t["rps"]),
            "paired": pair,
        }
        stop = None if replicated else "FROZEN_EXPECTED_XI_PLAYER_STRENGTH_FAILED_HISTORICAL_TEST_CONFIRMATION"

    summary = {
        "schema_version": "football3-top1-r40b-expected-xi-player-strength",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_STRICT_PRIOR_PLAYER_QUALITY_LAYER_AFTER_R40A",
        "formal_weight": 0,
        "governance": {
            "base_r40a_commit": "b4b799890b41d398c3aa59ee584e1fcbc3c7f691",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_xg_and_player_updates_withheld": True,
            "current_match_starting_xi_used": False,
            "current_match_injury_or_suspension_used": False,
            "odds_used": False,
            "market_prices_used": False,
            "feature_family_grid_predeclared": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_used_for_candidate_selection": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Does the identity and strictly-prior quality of the expected XI add stable 1X2 Top1 information beyond K1, after R33 showed simple continuity was insufficient?",
        "information_family": {
            "family": "EXPECTED_XI_PLAYER_QUALITY",
            "source": source_meta,
            "expected_xi_rule": {"lookback_matches": LOOKBACK_XI, "decay": DECAY, "min_prior_xi": MIN_PRIOR_XI},
            "player_strength_rule": {
                "shrink": SHRINK,
                "result_residual": "team result utility minus pre-match raw-model expected utility, allocated only after target match",
                "attack_xg_residual": "post-match team xG minus pre-match xG mean, allocated only after target match",
                "defense_xg_residual": "post-match opponent xG minus pre-match opponent xG mean, allocated only after target match",
            },
            "candidate_feature_sets": FEATURE_SETS,
            "validation_coverage": coverage(val),
            "test_coverage": coverage(test),
        },
        "selection_contract": {
            "min_validation_gain_hits": MIN_VALIDATION_GAIN_HITS,
            "min_positive_validation_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "max_validation_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN,
            "min_test_gain_hits": MIN_TEST_GAIN_HITS,
            "min_positive_test_blocks": MIN_POSITIVE_TEST_BLOCKS,
            "max_negative_test_blocks": MAX_NEGATIVE_TEST_BLOCKS,
            "max_test_logloss_worsen": MAX_TEST_LOGLOSS_WORSEN,
        },
        "controls": {
            "K1_validation": base_v,
            "R31_rest_schedule": "CLOSED_NO_VALIDATION_ROBUST_GAIN",
            "R33_simple_player_continuity": "CLOSED_NO_VALIDATION_ROBUST_GAIN",
            "R40A_recognition": "COMPLETE",
        },
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": test_confirmation,
        "decision": {
            "historically_confirmed": replicated,
            "action": "KEEP_EXPECTED_XI_PLAYER_STRENGTH_FOR_INDEPENDENT_REPLICATION" if replicated else "DO_NOT_PROMOTE_R40B",
            "stop_reason": stop,
        },
        "next_if_fail": "DO_NOT_RETUNE_PLAYER_QUALITY_ON_TEST; add a new timestamp-auditable availability source (injury/suspension/confirmed-XI) or switch this recognized player layer to total-goals evaluation.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r40b.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r40b.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_xg_and_player_updates_withheld"]
    assert not g["current_match_starting_xi_used"] and not g["current_match_injury_or_suspension_used"]
    assert not g["odds_used"] and not g["market_prices_used"] and not g["test_used_for_candidate_selection"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert s["information_family"]["source"]["fixture_players_sha256"] == EXPECTED_FIXTURE_PLAYERS_SHA256
    print("R40B_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r40b.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
