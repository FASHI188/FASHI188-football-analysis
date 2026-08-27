#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R40C_DIR = HERE.parent / "top1_r40c_role_aware_expected_xi"
sys.path.insert(0, str(R40C_DIR))
import run_experiment_r40c as r40c  # noqa: E402

r9 = r40c.r9
r33 = r40c.r33
NAMES = r40c.POSITIONAL_RESULT_NAMES

# Frozen before this outcome audit is evaluated.
SEASON_GAP_DAYS = 45
MAX_TARGET_DAYS_AFTER_OPENER = 21
MIN_OOS_MATCHES = 60
MIN_GAIN_HITS = 1
MIN_POSITIVE_BLOCKS = 2
MAX_NEGATIVE_BLOCKS = 1
MAX_LOGLOSS_WORSEN = 0.001


def expected_from_xis(xis):
    xs = list(xis)[-r40c.LOOKBACK_XI:]
    if len(xs) < r40c.MIN_PRIOR_XI:
        return None
    raw = defaultdict(float)
    for lag, xi in enumerate(reversed(xs)):
        w = r40c.DECAY ** lag
        for pid in xi:
            raw[int(pid)] += w
    ranked = sorted(raw.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:11]
    if len(ranked) < 10:
        return None
    return tuple(int(pid) for pid, _ in ranked)


def lineup(player_map, row, team_id):
    xs = player_map.get((str(row["game_id"]), str(team_id)), [])
    ids = []
    roles = {}
    for pid, role in xs:
        pid = int(pid)
        if pid not in ids:
            ids.append(pid)
        if role in r40c.ROLES:
            roles[pid] = role
    if len(ids) != 11:
        return None, {}
    return tuple(ids), roles


def build_plan_specs(rows, player_map):
    team_matches = defaultdict(list)
    for row in rows:
        dt = pd.Timestamp(row["date"])
        fid = str(row["game_id"])
        for side in ("home", "away"):
            tid = str(row[f"{side}_team"])
            xi, roles = lineup(player_map, row, tid)
            team_matches[tid].append({
                "date": dt,
                "fixture_id": fid,
                "team_id": tid,
                "xi": xi,
                "roles": roles,
            })

    target_plans = {}
    opener_requests = {}
    detected_openers = 0
    for tid, ms in team_matches.items():
        ms.sort(key=lambda x: (x["date"], x["fixture_id"]))
        hist = deque(maxlen=r40c.LOOKBACK_XI)
        prev_date = None
        pending = None
        for m in ms:
            if pending is not None:
                days = int((m["date"] - pending["opener_date"]).days)
                if 0 < days <= MAX_TARGET_DAYS_AFTER_OPENER:
                    spec = {
                        **pending,
                        "target_fixture_id": m["fixture_id"],
                        "target_date": m["date"].date().isoformat(),
                        "target_days_after_opener": days,
                    }
                    target_plans[(m["fixture_id"], tid)] = spec
                    opener_requests[(pending["opener_fixture_id"], tid)] = spec
                pending = None

            if prev_date is not None:
                gap = int((m["date"] - prev_date).days)
                if gap >= SEASON_GAP_DAYS and len(hist) >= r40c.MIN_PRIOR_XI and m["xi"] is not None:
                    stale = expected_from_xis(hist)
                    if stale is not None:
                        detected_openers += 1
                        pending = {
                            "team_id": tid,
                            "opener_fixture_id": m["fixture_id"],
                            "opener_date": m["date"],
                            "opener_xi": m["xi"],
                            "opener_roles": m["roles"],
                            "legacy_expected": stale,
                            "gap_days": gap,
                            "roster_shock": 11 - len(set(stale) & set(m["xi"])),
                        }
            if m["xi"] is not None:
                hist.append(frozenset(m["xi"]))
            prev_date = m["date"]
    return target_plans, opener_requests, detected_openers


def snapshot_values(spec, ledger):
    out = {}
    opener_roles = spec["opener_roles"]
    for pid in set(spec["legacy_expected"]) | set(spec["opener_xi"]):
        result, attack, defense, n = ledger.values(pid)
        role = ledger.last_role.get(pid)
        if role not in r40c.ROLES:
            role = opener_roles.get(pid)
        out[int(pid)] = {
            "result": float(result),
            "role": role if role in r40c.ROLES else None,
            "n": int(n),
        }
    return out


def frozen_side(pids, snap):
    buckets = {role: [] for role in r40c.ROLES}
    known = 0
    for pid in pids:
        v = snap.get(int(pid), {"result": 0.0, "role": None, "n": 0})
        role = v.get("role")
        if role in r40c.ROLES:
            known += 1
            buckets[role].append(float(v["result"]))
    out = {"role_known_share": known / len(pids) if pids else 0.0}
    for role in r40c.ROLES:
        out[f"{role}_result"] = float(np.mean(buckets[role])) if buckets[role] else 0.0
    return out


def context_from_pids(home_pids, away_pids, home_snap, away_snap):
    h = frozen_side(home_pids, home_snap)
    a = frozen_side(away_pids, away_snap)
    z = {
        "home_role_known_share": h["role_known_share"],
        "away_role_known_share": a["role_known_share"],
        "role_known_share_diff": h["role_known_share"] - a["role_known_share"],
    }
    for role in r40c.ROLES:
        z[f"home_{role}_result"] = h[f"{role}_result"]
        z[f"away_{role}_result"] = a[f"{role}_result"]
        z[f"diff_{role}_result"] = h[f"{role}_result"] - a[f"{role}_result"]
    return z


def model_prob(model, raw, cf):
    x = list(r9.feat_k1(raw)) + [float(cf[n]) for n in NAMES]
    pr = model.predict_proba([x])[0]
    classes = list(model[-1].classes_)
    v = np.zeros(3, dtype=float)
    for cls, p in zip(classes, pr):
        v[int(cls)] = float(p)
    return r9.decorate(v)


def subset_metrics(records, mask):
    a = [x["legacy"] for x in records if mask(x)]
    b = [x["bridge"] for x in records if mask(x)]
    if not a:
        return {"n": 0}
    am, bm = r33.metrics(a), r33.metrics(b)
    return {
        "n": len(a),
        "legacy": am,
        "bridge": bm,
        "gain_hits": int(bm["hits"] - am["hits"]),
        "gain_top1_pp": 100.0 * float(bm["top1_accuracy"] - am["top1_accuracy"]),
        "logloss_delta": float(bm["logloss"] - am["logloss"]),
        "brier_delta": float(bm["brier"] - am["brier"]),
        "rps_delta": float(bm["rps"] - am["rps"]),
    }


def run():
    rows = r9.load()
    player_map, player_sha, matched_starters, player_path = r40c.download_player_rows(rows)
    target_plans, opener_requests, detected_openers = build_plan_specs(rows, player_map)

    base = r9.S()
    states = defaultdict(r40c.TeamState)
    ledger = r40c.Ledger()
    pred = []
    frozen_snaps = {}
    raw_targets = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending_updates = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            fid = str(row["game_id"])
            raw = base.pred(row)
            normal_cf = r40c.context_features(row, states, ledger)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": normal_cf})

            for side in ("home", "away"):
                tid = str(row[f"{side}_team"])
                req = opener_requests.get((fid, tid))
                if req is not None:
                    frozen_snaps[(req["target_fixture_id"], tid)] = snapshot_values(req, ledger)

            htid, atid = str(row["home_team"]), str(row["away_team"])
            hp = target_plans.get((fid, htid))
            ap = target_plans.get((fid, atid))
            hs = frozen_snaps.get((fid, htid))
            a_s = frozen_snaps.get((fid, atid))
            if hp is not None and ap is not None and hs is not None and a_s is not None:
                legacy_cf = context_from_pids(hp["legacy_expected"], ap["legacy_expected"], hs, a_s)
                bridge_cf = context_from_pids(hp["opener_xi"], ap["opener_xi"], hs, a_s)
                raw_targets.append({
                    "date": day,
                    "fixture_id": fid,
                    "y": r9.actual(row),
                    "raw": raw,
                    "legacy_cf": legacy_cf,
                    "bridge_cf": bridge_cf,
                    "home_roster_shock": int(hp["roster_shock"]),
                    "away_roster_shock": int(ap["roster_shock"]),
                    "mean_roster_shock": 0.5 * (hp["roster_shock"] + ap["roster_shock"]),
                    "max_roster_shock": max(hp["roster_shock"], ap["roster_shock"]),
                    "home_gap_days": int(hp["gap_days"]),
                    "away_gap_days": int(ap["gap_days"]),
                })
            pending_updates.append((row, raw))

        # Same-date post-prediction updates, matching R40C chronology discipline.
        for row, raw in pending_updates:
            fid = str(row["game_id"])
            hi = player_map.get((fid, str(row["home_team"])), [])
            ai = player_map.get((fid, str(row["away_team"])), [])
            y = r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"])
            ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[str(row["home_team"])].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[str(row["away_team"])].xis.append(frozenset(pid for pid, _ in ai))
            base.update(row, raw)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    train = pred[b1:b2]
    train_end_date = max(x["date"] for x in train)
    model = r40c.fit_model(train, NAMES)

    records = []
    for x in raw_targets:
        # Strictly after the frozen model-training era; no target label can affect coefficients.
        if x["date"] <= train_end_date:
            continue
        lp = model_prob(model, x["raw"], x["legacy_cf"])
        bp = model_prob(model, x["raw"], x["bridge_cf"])
        records.append({
            **{k: x[k] for k in ("date", "fixture_id", "y", "home_roster_shock", "away_roster_shock", "mean_roster_shock", "max_roster_shock", "home_gap_days", "away_gap_days")},
            "legacy": {"date": x["date"], "y": x["y"], "P": lp},
            "bridge": {"date": x["date"], "y": x["y"], "P": bp},
        })

    legacy = [x["legacy"] for x in records]
    bridge = [x["bridge"] for x in records]
    if legacy:
        lm, bm = r33.metrics(legacy), r33.metrics(bridge)
        pair = r33.paired_blocks(legacy, bridge)
        gain = int(bm["hits"] - lm["hits"])
        logdelta = float(bm["logloss"] - lm["logloss"])
        passed = bool(
            len(records) >= MIN_OOS_MATCHES
            and gain >= MIN_GAIN_HITS
            and pair["positive_time_blocks"] >= MIN_POSITIVE_BLOCKS
            and pair["negative_time_blocks"] <= MAX_NEGATIVE_BLOCKS
            and logdelta <= MAX_LOGLOSS_WORSEN
        )
        main = {
            "n": len(records),
            "legacy": lm,
            "bridge": bm,
            "gain_hits": gain,
            "gain_top1_pp": 100.0 * float(bm["top1_accuracy"] - lm["top1_accuracy"]),
            "logloss_delta": logdelta,
            "brier_delta": float(bm["brier"] - lm["brier"]),
            "rps_delta": float(bm["rps"] - lm["rps"]),
            "paired": pair,
        }
    else:
        passed = False
        main = {"n": 0}

    result = {
        "schema_version": "football3-r42g-historical-season-bridge-outcome-audit-v1",
        "status": "COMPLETE",
        "classification": "CHRONOLOGY_PRESERVING_OOS_INCREMENTAL_BRIDGE_AUDIT_FIXED_R40C_MODEL",
        "formal_weight": 0,
        "question": "On post-training second matches after >=45-day team gaps, does replacing stale pre-gap expected XI membership with the completed opener XI improve fixed R40C 1X2 probability quality?",
        "governance": {
            "model_family": "R40C_POSITIONAL_RESULT_STRENGTH",
            "model_hyperparameters_reused": True,
            "parameter_search": False,
            "target_result_used_only_for_scoring": True,
            "target_confirmed_xi_used": False,
            "opener_result_score_xg_used_in_bridge_player_strength": False,
            "opener_xi_membership_used": True,
            "bridge_player_strength_frozen_pre_opener": True,
            "same_K1_raw_probability_used_for_legacy_and_bridge": True,
            "evaluation_strictly_after_model_training_end_date": True,
            "season_gap_days_frozen": SEASON_GAP_DAYS,
            "max_target_days_after_opener_frozen": MAX_TARGET_DAYS_AFTER_OPENER,
        },
        "source": {
            "snapshot_rows": len(rows),
            "fixture_players_sha256": player_sha,
            "matched_starter_rows": matched_starters,
            "model_train_rows": len(train),
            "model_train_end_date": train_end_date,
            "detected_gap_openers": detected_openers,
            "two_sided_candidate_matches_before_oos_cut": len(raw_targets),
        },
        "main_oos": main,
        "subgroups": {
            "max_roster_shock_5_plus": subset_metrics(records, lambda x: x["max_roster_shock"] >= 5),
            "both_roster_shock_3_plus": subset_metrics(records, lambda x: x["home_roster_shock"] >= 3 and x["away_roster_shock"] >= 3),
            "mean_roster_shock_5_plus": subset_metrics(records, lambda x: x["mean_roster_shock"] >= 5),
        },
        "gate": {
            "min_oos_matches": MIN_OOS_MATCHES,
            "min_gain_hits": MIN_GAIN_HITS,
            "min_positive_blocks": MIN_POSITIVE_BLOCKS,
            "max_negative_blocks": MAX_NEGATIVE_BLOCKS,
            "max_logloss_worsen": MAX_LOGLOSS_WORSEN,
            "passed": passed,
            "action": "KEEP_BRIDGE_FOR_PROSPECTIVE_ACCUMULATION" if passed else "BRIDGE_IMPROVES_XI_IDENTIFICATION_BUT_OUTCOME_VALUE_NOT_CONFIRMED",
        },
        "limitations": [
            "R40C itself failed its prior formal historical-test Log Loss gate, so this audit cannot promote R40C as a formal model.",
            "The audit tests incremental stale-XI versus opener-XI membership under one fixed R40C model; it does not estimate injury/suspension value.",
            "Season boundaries are inferred by a >=45-day observed team gap rather than provider season metadata.",
            "Normal K1 team-state updates may include the opener result; this is held identical between stale and bridge variants, so only the player-XI feature delta is under test.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42g_outcome_audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "oos_records_r42g.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in records) + ("\n" if records else ""), encoding="utf-8")
    try:
        Path(player_path).unlink()
    except Exception:
        pass
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42g_outcome_audit.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["parameter_search"] is False
    assert g["target_confirmed_xi_used"] is False
    assert g["target_result_used_only_for_scoring"] is True
    assert g["bridge_player_strength_frozen_pre_opener"] is True
    assert g["same_K1_raw_probability_used_for_legacy_and_bridge"] is True
    if d["main_oos"].get("n", 0):
        assert d["main_oos"]["n"] >= 1
    print("R42G_HISTORICAL_SEASON_BRIDGE_OUTCOME_AUDIT_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42g_outcome_audit.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
