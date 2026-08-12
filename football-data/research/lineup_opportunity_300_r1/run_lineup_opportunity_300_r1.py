#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

SOURCE_COMMIT = "8c97b2adb123863c3dd581e730f1360e89815ac2"
PRIOR_MINUTES = 450.0
RIDGE_ALPHA = 10.0
BOOTSTRAP_REPS = 10000
BOOTSTRAP_SEED = 20260812

GATE_COLS = [
    "fixture", "kickoff_time", "team", "position", "element", "was_home", "starts"
]
MODEL_COLS = GATE_COLS + [
    "minutes", "expected_goals", "expected_assists"
]

BASE_FEATURES = [
    "home",
    "team_roll5_xg",
    "opp_roll5_xgc",
    "team_exp_xg",
    "opp_exp_xgc",
]

LINEUP_FEATURES = [
    "def_count", "mid_count", "fwd_count",
    "prev_xi_overlap", "recent5_top11_overlap",
    "xi_xg90_sum", "xi_xg90_mean", "xi_xg90_std", "xi_xg90_top3_share",
    "xi_xa90_sum", "xi_xa90_mean", "xi_xa90_std", "xi_xa90_top3_share",
    "xi_xgi90_sum", "xi_xgi90_mean", "xi_xgi90_std", "xi_xgi90_top3_share",
    "low_history_count",
    "def_xgi90_sum", "mid_xgi90_sum", "fwd_xgi90_sum",
    "fwd_zero", "fwd_two_plus", "top3_same_position",
]
CAND_FEATURES = BASE_FEATURES + LINEUP_FEATURES


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def to_bool_series(s):
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )


def load_fixtures(path):
    fx = pd.read_csv(path)
    need = {"id", "event", "kickoff_time", "finished", "team_h", "team_a"}
    missing = sorted(need - set(fx.columns))
    if missing:
        raise RuntimeError(f"fixtures missing columns: {missing}")
    fx["kickoff_dt"] = pd.to_datetime(fx["kickoff_time"], utc=True, errors="coerce")
    fx["finished_bool"] = to_bool_series(fx["finished"])
    if fx["kickoff_dt"].isna().any():
        raise RuntimeError("fixtures contains invalid kickoff_time")
    fx["id"] = pd.to_numeric(fx["id"], errors="raise").astype(int)
    fx = fx.sort_values(["kickoff_dt", "id"], kind="stable").reset_index(drop=True)
    return fx


def dedup_identity(df):
    d = df.copy()
    d["fixture"] = pd.to_numeric(d["fixture"], errors="raise").astype(int)
    d["element"] = pd.to_numeric(d["element"], errors="raise").astype(int)
    d["starts"] = pd.to_numeric(d["starts"], errors="coerce")
    d["was_home_norm"] = to_bool_series(d["was_home"])
    key = ["fixture", "element"]
    dup_mask = d.duplicated(key, keep=False)
    dup_rows = d.loc[dup_mask].copy()
    duplicate_key_count = int(dup_rows[key].drop_duplicates().shape[0])

    conflict_keys = []
    identity_compare_cols = ["kickoff_time", "team", "position", "was_home_norm", "starts"]
    if duplicate_key_count:
        for k, g in dup_rows.groupby(key, sort=False):
            conflict = False
            for c in identity_compare_cols:
                vals = g[c].astype(str).fillna("<NA>").unique()
                if len(vals) > 1:
                    conflict = True
                    break
            if conflict:
                conflict_keys.append({"fixture": int(k[0]), "element": int(k[1])})

    d = d.drop_duplicates(key, keep="first").copy()
    return d, duplicate_key_count, conflict_keys


def run_gate(fixtures_path, merged_path, outdir, write_json=True):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    fx = load_fixtures(fixtures_path)
    if len(fx) != 380:
        fixture_count_warning = f"fixture_rows={len(fx)} expected=380"
    else:
        fixture_count_warning = None
    selected = fx.tail(300).copy()
    selected_ids = set(selected["id"].astype(int).tolist())

    d = pd.read_csv(merged_path, usecols=lambda c: c in GATE_COLS)
    missing = sorted(set(GATE_COLS) - set(d.columns))
    if missing:
        raise RuntimeError(f"merged gate columns missing: {missing}")
    d, dup_key_count, conflict_keys = dedup_identity(d)
    sub = d[d["fixture"].isin(selected_ids)].copy()

    anomalies = []
    if conflict_keys:
        anomalies.append({"type": "conflicting_duplicate_keys", "count": len(conflict_keys), "examples": conflict_keys[:20]})

    present_fixtures = set(sub["fixture"].unique().tolist())
    missing_fixtures = sorted(selected_ids - present_fixtures)
    extra_fixtures = sorted(present_fixtures - selected_ids)
    if missing_fixtures:
        anomalies.append({"type": "missing_selected_fixtures", "count": len(missing_fixtures), "examples": missing_fixtures[:20]})
    if extra_fixtures:
        anomalies.append({"type": "unexpected_extra_fixtures", "count": len(extra_fixtures), "examples": extra_fixtures[:20]})

    required_nulls = {}
    for c in ["fixture", "kickoff_time", "team", "position", "element", "was_home_norm", "starts"]:
        n = int(sub[c].isna().sum())
        required_nulls[c] = n
        if n:
            anomalies.append({"type": "missing_required_field", "field": c, "count": n})

    team_match_rows = []
    for fixture_id in sorted(selected_ids):
        frows = sub[sub["fixture"] == fixture_id]
        teams = [x for x in frows["team"].dropna().unique().tolist()]
        if len(teams) != 2:
            anomalies.append({"type": "fixture_team_count", "fixture": int(fixture_id), "team_count": len(teams), "teams": teams})
            continue
        home_team_flags = {}
        for team in teams:
            t = frows[frows["team"] == team]
            home_vals = t["was_home_norm"].dropna().unique().tolist()
            if len(home_vals) != 1:
                anomalies.append({"type": "team_home_flag_inconsistent", "fixture": int(fixture_id), "team": str(team), "values": [str(v) for v in home_vals]})
                home_flag = None
            else:
                home_flag = bool(home_vals[0])
            home_team_flags[str(team)] = home_flag

            starters = t[pd.to_numeric(t["starts"], errors="coerce") == 1].copy()
            xi_n = int(len(starters))
            gk_n = int((starters["position"] == "GK").sum())
            pos_missing = int(starters["position"].isna().sum())
            team_match_rows.append({
                "fixture": int(fixture_id),
                "team": str(team),
                "xi_n": xi_n,
                "gk_n": gk_n,
                "position_missing": pos_missing,
                "was_home": home_flag,
            })
            if xi_n != 11:
                anomalies.append({"type": "xi_count", "fixture": int(fixture_id), "team": str(team), "xi_n": xi_n})
            if gk_n != 1:
                anomalies.append({"type": "gk_count", "fixture": int(fixture_id), "team": str(team), "gk_n": gk_n})
            if pos_missing:
                anomalies.append({"type": "starter_position_missing", "fixture": int(fixture_id), "team": str(team), "count": pos_missing})
        flags = list(home_team_flags.values())
        if sorted([f for f in flags if f is not None]) != [False, True]:
            anomalies.append({"type": "fixture_home_away_pair_invalid", "fixture": int(fixture_id), "flags": home_team_flags})

    tm = pd.DataFrame(team_match_rows)
    gate_pass = (len(anomalies) == 0 and len(tm) == 600)

    shell_counts = {}
    if gate_pass:
        shell = []
        for (fixture_id, team), t in sub.groupby(["fixture", "team"], sort=False):
            starters = t[pd.to_numeric(t["starts"], errors="coerce") == 1]
            counts = starters["position"].value_counts()
            shell.append(f"{int(counts.get('DEF',0))}-{int(counts.get('MID',0))}-{int(counts.get('FWD',0))}")
        shell_counts = dict(Counter(shell).most_common())

    result = {
        "study_id": "lineup_opportunity_300_r1",
        "mode": "ZERO_LABEL_GATE",
        "source_commit": SOURCE_COMMIT,
        "label_columns_loaded": False,
        "fixtures_sha256": sha256_file(fixtures_path),
        "merged_sha256": sha256_file(merged_path),
        "fixture_rows": int(len(fx)),
        "fixture_unique_ids": int(fx["id"].nunique()),
        "finished_true": int((fx["finished_bool"] == True).sum()),
        "fixture_count_warning": fixture_count_warning,
        "selected_match_count": 300,
        "selected_first_kickoff": selected.iloc[0]["kickoff_dt"].isoformat(),
        "selected_last_kickoff": selected.iloc[-1]["kickoff_dt"].isoformat(),
        "selected_event_min": int(pd.to_numeric(selected["event"], errors="coerce").min()),
        "selected_event_max": int(pd.to_numeric(selected["event"], errors="coerce").max()),
        "duplicate_fixture_element_keys": dup_key_count,
        "conflicting_duplicate_keys": conflict_keys,
        "required_field_nulls": required_nulls,
        "team_match_count_after_dedup": int(len(tm)),
        "xi_exact_11_count": int((tm["xi_n"] == 11).sum()) if len(tm) else 0,
        "gk_exact_1_count": int((tm["gk_n"] == 1).sum()) if len(tm) else 0,
        "position_complete_count": int((tm["position_missing"] == 0).sum()) if len(tm) else 0,
        "shell_counts": shell_counts,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies[:200],
        "gate_status": "PASS" if gate_pass else "FAIL",
    }
    if write_json:
        with open(outdir / "data_gate_r1.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        tm.to_csv(outdir / "team_match_gate_rows_r1.csv", index=False)
    return result


def safe_float(s):
    return pd.to_numeric(s, errors="coerce")


def aggregate_team_matches(d, fixtures):
    d = d.copy()
    d["minutes_num"] = safe_float(d["minutes"])
    d["xg_num"] = safe_float(d["expected_goals"])
    d["xa_num"] = safe_float(d["expected_assists"])

    bad_xg = d[(d["minutes_num"] > 0) & (d["xg_num"].isna())]
    bad_xa = d[(d["minutes_num"] > 0) & (d["xa_num"].isna())]
    if len(bad_xg) or len(bad_xa):
        raise RuntimeError(f"LABEL_INTEGRITY_FAIL bad_xg={len(bad_xg)} bad_xa={len(bad_xa)}")

    d["minutes_num"] = d["minutes_num"].fillna(0.0)
    d["xg_num"] = d["xg_num"].fillna(0.0)
    d["xa_num"] = d["xa_num"].fillna(0.0)

    team_rows = []
    grouped = d.groupby(["fixture", "team"], sort=False)
    for (fixture, team), g in grouped:
        home_vals = g["was_home_norm"].dropna().unique().tolist()
        if len(home_vals) != 1:
            raise RuntimeError(f"home flag inconsistent fixture={fixture} team={team}")
        starters = g[pd.to_numeric(g["starts"], errors="coerce") == 1]
        team_rows.append({
            "fixture": int(fixture),
            "team": str(team),
            "home": int(bool(home_vals[0])),
            "team_xg": float(g["xg_num"].sum()),
            "team_xa": float(g["xa_num"].sum()),
            "xi": tuple(sorted(starters["element"].astype(int).tolist())),
        })
    tm = pd.DataFrame(team_rows)

    fxsmall = fixtures[["id", "kickoff_dt"]].copy().rename(columns={"id": "fixture"})
    tm = tm.merge(fxsmall, on="fixture", how="left", validate="many_to_one")
    if tm["kickoff_dt"].isna().any():
        raise RuntimeError("team-match missing fixture kickoff")

    opp_map = {}
    for fixture, g in tm.groupby("fixture", sort=False):
        if len(g) != 2:
            raise RuntimeError(f"fixture {fixture} has {len(g)} team rows in model aggregation")
        a, b = g.iloc[0], g.iloc[1]
        opp_map[(int(fixture), str(a["team"]))] = str(b["team"])
        opp_map[(int(fixture), str(b["team"]))] = str(a["team"])
    tm["opponent"] = [opp_map[(int(r.fixture), str(r.team))] for r in tm.itertuples()]
    return tm, d


def prior_rate(player_state, pos_state, global_state, element, pos, metric):
    ps = player_state.get(int(element), {"minutes": 0.0, "xg": 0.0, "xa": 0.0})
    pmin = float(ps["minutes"])
    pval = float(ps[metric])

    pos_s = pos_state.get(str(pos), {"minutes": 0.0, "xg": 0.0, "xa": 0.0})
    if pos_s["minutes"] > 0:
        prior_per_min = float(pos_s[metric]) / float(pos_s["minutes"])
    elif global_state["minutes"] > 0:
        prior_per_min = float(global_state[metric]) / float(global_state["minutes"])
    else:
        prior_per_min = 0.0
    rate90 = 90.0 * (pval + prior_per_min * PRIOR_MINUTES) / (pmin + PRIOR_MINUTES)
    return rate90, pmin


def aggregate_stats(vals):
    arr = np.asarray(vals, dtype=float)
    if len(arr) != 11:
        raise RuntimeError(f"expected 11 XI values, got {len(arr)}")
    total = float(arr.sum())
    top3 = float(np.sort(arr)[-3:].sum())
    return {
        "sum": total,
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=0)),
        "top3_share": float(top3 / total) if total > 0 else 0.0,
    }


def generate_feature_rows(tm, d, fixtures):
    player_groups = {(int(k[0]), str(k[1])): g.copy()
                     for k, g in d.groupby(["fixture", "team"], sort=False)}

    team_hist = defaultdict(list)
    player_state = {}
    pos_state = defaultdict(lambda: {"minutes": 0.0, "xg": 0.0, "xa": 0.0})
    global_state = {"minutes": 0.0, "xg": 0.0, "xa": 0.0}

    ordered_fx = fixtures.sort_values(["kickoff_dt", "id"], kind="stable").copy()
    feature_rows = []
    sort_rank = {int(fid): i for i, fid in enumerate(ordered_fx["id"].tolist())}

    for kickoff, batch in ordered_fx.groupby("kickoff_dt", sort=True):
        batch_ids = [int(x) for x in batch["id"].tolist()]
        pending_updates = []

        for fixture_id in batch_ids:
            current_pair = tm[tm["fixture"] == fixture_id]
            if len(current_pair) != 2:
                continue
            pair_xg = {str(r.team): float(r.team_xg) for r in current_pair.itertuples()}
            pair_xa = {str(r.team): float(r.team_xa) for r in current_pair.itertuples()}
            for row in current_pair.itertuples(index=False):
                team = str(row.team)
                opp = str(row.opponent)
                pg = player_groups[(fixture_id, team)]
                starters = pg[pd.to_numeric(pg["starts"], errors="coerce") == 1].copy()
                if len(starters) != 11:
                    raise RuntimeError(f"XI integrity changed in model stage fixture={fixture_id} team={team} n={len(starters)}")

                hist_t = team_hist[team]
                hist_o = team_hist[opp]
                eligible = (len(hist_t) >= 1 and len(hist_o) >= 1)

                feat = {
                    "fixture": fixture_id,
                    "fixture_sort_rank": sort_rank[fixture_id],
                    "kickoff_time": pd.Timestamp(kickoff).isoformat(),
                    "team": team,
                    "opponent": opp,
                    "home": int(row.home),
                    "team_xg": float(row.team_xg),
                    "team_xa": float(row.team_xa),
                    "model_eligible": int(eligible),
                }

                feat["team_roll5_xg"] = float(np.mean([h["xg"] for h in hist_t[-5:]])) if hist_t else np.nan
                feat["opp_roll5_xgc"] = float(np.mean([h["xgc"] for h in hist_o[-5:]])) if hist_o else np.nan
                feat["team_exp_xg"] = float(np.mean([h["xg"] for h in hist_t])) if hist_t else np.nan
                feat["opp_exp_xgc"] = float(np.mean([h["xgc"] for h in hist_o])) if hist_o else np.nan

                counts = starters["position"].value_counts()
                feat["def_count"] = int(counts.get("DEF", 0))
                feat["mid_count"] = int(counts.get("MID", 0))
                feat["fwd_count"] = int(counts.get("FWD", 0))

                current_xi = set(starters["element"].astype(int).tolist())
                prev_xi = set(hist_t[-1]["xi"]) if hist_t else set()
                feat["prev_xi_overlap"] = int(len(current_xi & prev_xi))

                start_counts = Counter()
                for h in hist_t[-5:]:
                    start_counts.update(h["xi"])
                ranked = sorted(start_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:11]
                top11_recent = {int(k) for k, _ in ranked}
                feat["recent5_top11_overlap"] = int(len(current_xi & top11_recent))

                xi_xg90, xi_xa90, xi_xgi90 = [], [], []
                xi_detail = []
                low_hist = 0
                for s in starters.itertuples(index=False):
                    elem = int(s.element)
                    pos = str(s.position)
                    xg90, pmin = prior_rate(player_state, pos_state, global_state, elem, pos, "xg")
                    xa90, _ = prior_rate(player_state, pos_state, global_state, elem, pos, "xa")
                    xgi90 = xg90 + xa90
                    xi_xg90.append(xg90)
                    xi_xa90.append(xa90)
                    xi_xgi90.append(xgi90)
                    xi_detail.append((elem, pos, xgi90))
                    if pmin < 270.0:
                        low_hist += 1

                for prefix, vals in [("xi_xg90", xi_xg90), ("xi_xa90", xi_xa90), ("xi_xgi90", xi_xgi90)]:
                    st = aggregate_stats(vals)
                    for k, v in st.items():
                        feat[f"{prefix}_{k}"] = v
                feat["low_history_count"] = int(low_hist)

                for pos, key in [("DEF", "def_xgi90_sum"), ("MID", "mid_xgi90_sum"), ("FWD", "fwd_xgi90_sum")]:
                    feat[key] = float(sum(v for _, p, v in xi_detail if p == pos))
                feat["fwd_zero"] = int(feat["fwd_count"] == 0)
                feat["fwd_two_plus"] = int(feat["fwd_count"] >= 2)
                top3_detail = sorted(xi_detail, key=lambda z: (-z[2], z[0]))[:3]
                feat["top3_same_position"] = int(len({p for _, p, _ in top3_detail}) == 1)
                feature_rows.append(feat)

                pending_updates.append((fixture_id, team, opp, tuple(sorted(current_xi)), pg.copy(),
                                        pair_xg[team], pair_xg[opp], pair_xa[team]))

        for fixture_id, team, opp, xi, pg, team_xg, opp_xg, team_xa in pending_updates:
            team_hist[team].append({"fixture": fixture_id, "xg": team_xg, "xgc": opp_xg, "xa": team_xa, "xi": xi})
            played = pg[pg["minutes_num"] > 0]
            for p in played.itertuples(index=False):
                elem = int(p.element)
                pos = str(p.position)
                st = player_state.setdefault(elem, {"minutes": 0.0, "xg": 0.0, "xa": 0.0})
                st["minutes"] += float(p.minutes_num)
                st["xg"] += float(p.xg_num)
                st["xa"] += float(p.xa_num)
                pos_state[pos]["minutes"] += float(p.minutes_num)
                pos_state[pos]["xg"] += float(p.xg_num)
                pos_state[pos]["xa"] += float(p.xa_num)
                global_state["minutes"] += float(p.minutes_num)
                global_state["xg"] += float(p.xg_num)
                global_state["xa"] += float(p.xa_num)

    features = pd.DataFrame(feature_rows)
    return features


def gaussian_nll(y, mu, sigma):
    sigma = max(float(sigma), 0.05)
    return 0.5 * np.log(2 * np.pi * sigma * sigma) + 0.5 * ((y - mu) / sigma) ** 2


def fit_predict(train, test, features, target):
    xtr = train[features].astype(float).to_numpy()
    xte = test[features].astype(float).to_numpy()
    ytr = np.log1p(train[target].astype(float).to_numpy())
    yte = np.log1p(test[target].astype(float).to_numpy())

    scaler = StandardScaler()
    xtr_s = scaler.fit_transform(xtr)
    xte_s = scaler.transform(xte)
    model = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True)
    model.fit(xtr_s, ytr)
    pred_log = model.predict(xte_s)
    sigma = max(float(np.std(ytr, ddof=1)), 0.05)
    nll = gaussian_nll(yte, pred_log, sigma)
    pred = np.maximum(0.0, np.expm1(pred_log))
    return pred_log, pred, nll, sigma


def calibration(obs, pred):
    obs = np.asarray(obs, dtype=float)
    pred = np.asarray(pred, dtype=float)
    if len(obs) < 3 or np.std(pred) < 1e-12:
        return {"intercept": None, "slope": None}
    slope, intercept = np.polyfit(pred, obs, 1)
    return {"intercept": float(intercept), "slope": float(slope)}


def metrics(obs, pred):
    obs = np.asarray(obs, dtype=float)
    pred = np.asarray(pred, dtype=float)
    return {
        "rmse": float(np.sqrt(np.mean((obs - pred) ** 2))),
        "mae": float(np.mean(np.abs(obs - pred))),
        "calibration": calibration(obs, pred),
    }


def bootstrap_match_delta(preds):
    md = preds.groupby("fixture", sort=False)["delta_nll_xg"].mean().to_numpy(dtype=float)
    if len(md) != 300:
        raise RuntimeError(f"bootstrap expected 300 fixtures, got {len(md)}")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(md)
    out = np.empty(BOOTSTRAP_REPS, dtype=float)
    batch = 1000
    done = 0
    while done < BOOTSTRAP_REPS:
        k = min(batch, BOOTSTRAP_REPS - done)
        idx = rng.integers(0, n, size=(k, n))
        out[done:done+k] = md[idx].mean(axis=1)
        done += k
    return {
        "reps": BOOTSTRAP_REPS,
        "seed": BOOTSTRAP_SEED,
        "mean": float(out.mean()),
        "p05": float(np.quantile(out, 0.05)),
        "p95": float(np.quantile(out, 0.95)),
    }


def run_model(fixtures_path, merged_path, gate_json, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    with open(gate_json, "r", encoding="utf-8") as f:
        gate = json.load(f)
    if gate.get("gate_status") != "PASS":
        raise RuntimeError("DATA_GATE_FAIL: model stage blocked by gate status")

    fx = load_fixtures(fixtures_path)
    selected = fx.tail(300).copy()
    selected_ids_order = selected["id"].astype(int).tolist()
    selected_rank = {fid: i + 1 for i, fid in enumerate(selected_ids_order)}

    d = pd.read_csv(merged_path, usecols=lambda c: c in MODEL_COLS)
    missing = sorted(set(MODEL_COLS) - set(d.columns))
    if missing:
        raise RuntimeError(f"merged model columns missing: {missing}")
    d, _, conflicts = dedup_identity(d)
    if conflicts:
        raise RuntimeError(f"conflicting duplicate keys in model stage: {conflicts[:5]}")
    tm, d = aggregate_team_matches(d, fx)
    features = generate_feature_rows(tm, d, fx)

    formal = features[features["fixture"].isin(selected_ids_order)].copy()
    formal["formal_match_index"] = formal["fixture"].map(selected_rank)
    if len(formal) != 600 or formal["fixture"].nunique() != 300:
        raise RuntimeError(f"formal feature coverage invalid rows={len(formal)} fixtures={formal['fixture'].nunique()}")
    if int(formal["model_eligible"].sum()) != 600:
        bad = formal[formal["model_eligible"] != 1][["fixture","team"]].to_dict("records")
        raise RuntimeError(f"formal test has ineligible rows: {bad[:20]}")
    if formal[CAND_FEATURES].isna().any().any():
        badcols = formal[CAND_FEATURES].columns[formal[CAND_FEATURES].isna().any()].tolist()
        raise RuntimeError(f"formal features contain NaN: {badcols}")

    all_eligible = features[features["model_eligible"] == 1].copy()
    pred_chunks = []
    fold_results = []

    for fold in [1, 2, 3]:
        test_lo = (fold - 1) * 100 + 1
        test_hi = fold * 100
        test_fixture_ids = set(selected_ids_order[test_lo-1:test_hi])
        first_test_fixture = selected_ids_order[test_lo-1]
        first_test_kickoff = fx.loc[fx["id"] == first_test_fixture, "kickoff_dt"].iloc[0]
        eligible_kickoff = pd.to_datetime(all_eligible["kickoff_time"], utc=True)
        train = all_eligible[eligible_kickoff < first_test_kickoff].copy()
        test = formal[formal["fixture"].isin(test_fixture_ids)].copy()
        if len(test) != 200 or test["fixture"].nunique() != 100:
            raise RuntimeError(f"fold {fold} test size invalid rows={len(test)} matches={test['fixture'].nunique()}")
        if len(train) < 100:
            raise RuntimeError(f"fold {fold} training rows too small: {len(train)}")

        b_log, b_pred, b_nll, sigxg = fit_predict(train, test, BASE_FEATURES, "team_xg")
        l_log, l_pred, l_nll, _ = fit_predict(train, test, CAND_FEATURES, "team_xg")
        bx_log, bx_pred, bx_nll, sigxa = fit_predict(train, test, BASE_FEATURES, "team_xa")
        lx_log, lx_pred, lx_nll, _ = fit_predict(train, test, CAND_FEATURES, "team_xa")

        p = test[["fixture","formal_match_index","kickoff_time","team","opponent","home","team_xg","team_xa"]].copy()
        p["fold"] = fold
        p["pred_b0_xg"] = b_pred
        p["pred_l1_xg"] = l_pred
        p["nll_b0_xg"] = b_nll
        p["nll_l1_xg"] = l_nll
        p["delta_nll_xg"] = l_nll - b_nll
        p["pred_b0_xa"] = bx_pred
        p["pred_l1_xa"] = lx_pred
        p["nll_b0_xa"] = bx_nll
        p["nll_l1_xa"] = lx_nll
        p["delta_nll_xa"] = lx_nll - bx_nll
        pred_chunks.append(p)

        fold_results.append({
            "fold": fold,
            "train_team_rows": int(len(train)),
            "train_matches": int(train["fixture"].nunique()),
            "test_team_rows": int(len(test)),
            "test_matches": int(test["fixture"].nunique()),
            "sigma_xg_shared": float(sigxg),
            "sigma_xa_shared": float(sigxa),
            "nll_b0_xg": float(np.mean(b_nll)),
            "nll_l1_xg": float(np.mean(l_nll)),
            "delta_nll_xg": float(np.mean(l_nll - b_nll)),
            "nll_b0_xa": float(np.mean(bx_nll)),
            "nll_l1_xa": float(np.mean(lx_nll)),
            "delta_nll_xa": float(np.mean(lx_nll - bx_nll)),
        })

    preds = pd.concat(pred_chunks, ignore_index=True)
    preds = preds.sort_values(["formal_match_index","team"], kind="stable").reset_index(drop=True)

    pooled_delta = float(preds["delta_nll_xg"].mean())
    pooled = {
        "nll_b0_xg": float(preds["nll_b0_xg"].mean()),
        "nll_l1_xg": float(preds["nll_l1_xg"].mean()),
        "delta_nll_xg": pooled_delta,
        "nll_b0_xa": float(preds["nll_b0_xa"].mean()),
        "nll_l1_xa": float(preds["nll_l1_xa"].mean()),
        "delta_nll_xa": float(preds["delta_nll_xa"].mean()),
        "b0_xg": metrics(preds["team_xg"], preds["pred_b0_xg"]),
        "l1_xg": metrics(preds["team_xg"], preds["pred_l1_xg"]),
        "b0_xa": metrics(preds["team_xa"], preds["pred_b0_xa"]),
        "l1_xa": metrics(preds["team_xa"], preds["pred_l1_xa"]),
    }

    boot = bootstrap_match_delta(preds)
    fold_improve_count = sum(1 for r in fold_results if r["delta_nll_xg"] < 0)

    loo = {}
    for team in sorted(preds["team"].unique()):
        sub = preds[preds["team"] != team]
        loo[str(team)] = float(sub["delta_nll_xg"].mean())
    loo_all_negative = all(v < 0 for v in loo.values())

    team_delta = preds.groupby("team")["delta_nll_xg"].sum()
    team_improvement = (-team_delta).clip(lower=0)
    positive_total = float(team_improvement.sum())
    if positive_total > 0:
        shares = (team_improvement / positive_total).sort_values(ascending=False)
        max_team = str(shares.index[0])
        max_share = float(shares.iloc[0])
    else:
        max_team = None
        max_share = 1.0
    concentration_pass = max_share < 0.50

    gates = {
        "pooled_delta_negative": pooled_delta < 0,
        "bootstrap_upper_negative": boot["p95"] < 0,
        "fold_improvement_at_least_2_of_3": fold_improve_count >= 2,
        "xg_rmse_no_material_worse": pooled["l1_xg"]["rmse"] <= 1.02 * pooled["b0_xg"]["rmse"],
        "xg_mae_no_material_worse": pooled["l1_xg"]["mae"] <= 1.02 * pooled["b0_xg"]["mae"],
        "xa_rmse_no_material_worse": pooled["l1_xa"]["rmse"] <= 1.02 * pooled["b0_xa"]["rmse"],
        "xa_mae_no_material_worse": pooled["l1_xa"]["mae"] <= 1.02 * pooled["b0_xa"]["mae"],
        "leave_one_team_out_all_negative": loo_all_negative,
        "single_team_improvement_share_below_50pct": concentration_pass,
    }
    retrospective_pass = all(gates.values())
    verdict = "RETROSPECTIVE_SIGNAL_PASS" if retrospective_pass else "RETROSPECTIVE_SIGNAL_FAIL"

    result = {
        "study_id": "lineup_opportunity_300_r1",
        "source_commit": SOURCE_COMMIT,
        "formal_weight": 0,
        "historical_lineup_pit": "UNVERIFIED",
        "scientific_component_pass_allowed": False,
        "sample": {
            "formal_matches": 300,
            "formal_team_rows": 600,
            "warmup_matches": 80,
            "first_formal_kickoff": selected.iloc[0]["kickoff_dt"].isoformat(),
            "last_formal_kickoff": selected.iloc[-1]["kickoff_dt"].isoformat(),
        },
        "algorithm": {
            "prior_minutes": PRIOR_MINUTES,
            "ridge_alpha": RIDGE_ALPHA,
            "bootstrap_reps": BOOTSTRAP_REPS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "baseline_feature_count": len(BASE_FEATURES),
            "candidate_feature_count": len(CAND_FEATURES),
        },
        "folds": fold_results,
        "pooled": pooled,
        "bootstrap_match_cluster_delta_nll_xg": boot,
        "fold_improvement_count": fold_improve_count,
        "leave_one_team_out_delta_nll_xg": loo,
        "single_team_concentration": {
            "max_positive_improvement_team": max_team,
            "max_positive_improvement_share": max_share,
            "positive_improvement_total": positive_total,
        },
        "gates": gates,
        "verdict": verdict,
    }

    with open(outdir / "model_result_r1.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    preds.to_csv(outdir / "predictions_r1.csv", index=False)
    features.to_csv(outdir / "feature_rows_all_380_r1.csv", index=False)

    lines = [
        "# 300场首发结构 × 预期机会量 — 运行结果 R1",
        "",
        f"- verdict: `{verdict}`",
        "- formal_weight: `0`",
        "- historical lineup PIT: `UNVERIFIED`",
        "- scientific component pass: `NOT_ALLOWED_BY_THIS_RETROSPECTIVE_RUN`",
        f"- pooled ΔNLL xG (L1-B0): `{pooled_delta:.8f}`",
        f"- bootstrap 90% CI: `[{boot['p05']:.8f}, {boot['p95']:.8f}]`",
        f"- folds improved: `{fold_improve_count}/3`",
        f"- xG RMSE B0/L1: `{pooled['b0_xg']['rmse']:.6f}` / `{pooled['l1_xg']['rmse']:.6f}`",
        f"- xG MAE B0/L1: `{pooled['b0_xg']['mae']:.6f}` / `{pooled['l1_xg']['mae']:.6f}`",
        f"- xA RMSE B0/L1: `{pooled['b0_xa']['rmse']:.6f}` / `{pooled['l1_xa']['rmse']:.6f}`",
        f"- xA MAE B0/L1: `{pooled['b0_xa']['mae']:.6f}` / `{pooled['l1_xa']['mae']:.6f}`",
        "",
        "## Gates",
    ]
    for k, v in gates.items():
        lines.append(f"- {k}: `{'PASS' if v else 'FAIL'}`")
    lines.extend([
        "",
        "## Boundary",
        "",
        "该结果仅检验回顾性首发结构信号。历史首发缺少可证明的赛前 available_at，因此无论结果如何，不得写成正式模型晋级、SCIENTIFIC_COMPONENT_PASS 或非零正式权重。",
        "",
    ])
    (outdir / "run_summary_r1.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["gate", "model"], required=True)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--merged", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--gate-json")
    args = ap.parse_args()

    if args.mode == "gate":
        res = run_gate(args.fixtures, args.merged, args.outdir, write_json=True)
        print(json.dumps({
            "gate_status": res["gate_status"],
            "selected_match_count": res["selected_match_count"],
            "team_match_count_after_dedup": res["team_match_count_after_dedup"],
            "duplicate_fixture_element_keys": res["duplicate_fixture_element_keys"],
            "anomaly_count": res["anomaly_count"],
            "label_columns_loaded": res["label_columns_loaded"],
        }, indent=2))
        if res["gate_status"] != "PASS":
            raise SystemExit(2)
    else:
        if not args.gate_json:
            raise RuntimeError("--gate-json required for model mode")
        res = run_model(args.fixtures, args.merged, args.gate_json, args.outdir)
        print(json.dumps({
            "verdict": res["verdict"],
            "pooled_delta_nll_xg": res["pooled"]["delta_nll_xg"],
            "bootstrap_p95": res["bootstrap_match_cluster_delta_nll_xg"]["p95"],
            "fold_improvement_count": res["fold_improvement_count"],
            "gates": res["gates"],
        }, indent=2))


if __name__ == "__main__":
    main()
