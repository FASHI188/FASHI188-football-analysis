#!/usr/bin/env python3
"""R44L2 frozen retrospective model execution.

The script first re-validates the frozen non-label sources and the full 380-match
structural coverage. Only after that gate passes does it download the FPL
merged_gw xG/xA label file.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from run_zero_label_gate_r44l2 import canon, date_from_iso, norm

OUT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "r44l2_model_output")
FPL_COMMIT = "8c97b2adb123863c3dd581e730f1360e89815ac2"
FPL_BASE = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/{FPL_COMMIT}/data/2025-26"
TM_BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"

NONLABEL_URLS = {
    "fpl_fixtures": f"{FPL_BASE}/fixtures.csv",
    "fpl_teams": f"{FPL_BASE}/teams.csv",
    "tm_games": f"{TM_BASE}/games.csv.gz",
    "tm_game_lineups": f"{TM_BASE}/game_lineups.csv.gz",
}
LABEL_URL = f"{FPL_BASE}/gws/merged_gw.csv"
EXPECTED_HASHES = {
    "fpl_fixtures": "2d7e3950d346df14ca486cb09e9b9ba406d37d943775244eed06cdc021ffb3a9",
    "fpl_teams": "b29df099cb0ad25413e284e53116099b0e0496874f99743dbc0870d8241b46c5",
    "tm_games": "585f593b2add005ad803fd999355ef70d5de44ef021d37787064fdffcb3ba484",
    "tm_game_lineups": "6b2fc04ae307390c4d2044659b91c0da314c6a20eefd4a2f13e1468ac06c874b",
    "fpl_merged_gw": "0d09f1f1cb1b5520ec8e2f25238aa652efe2a263d8ca7cb2b6538b27bf86727d",
}

RIDGE_ALPHA = 10.0
BOOTSTRAP_REPS = 10000
BOOTSTRAP_SEED = 20260812

ROLE_ORDER = [
    "Centre-Back", "Left-Back", "Right-Back", "Defensive Midfield",
    "Central Midfield", "Attacking Midfield", "Left Midfield", "Right Midfield",
    "Left Winger", "Right Winger", "Centre-Forward", "Second Striker",
]
ROLE_FEATURES = [
    "tm_centre_back_count", "tm_left_back_count", "tm_right_back_count",
    "tm_defensive_midfield_count", "tm_central_midfield_count",
    "tm_attacking_midfield_count", "tm_left_midfield_count", "tm_right_midfield_count",
    "tm_left_winger_count", "tm_right_winger_count", "tm_centre_forward_count",
    "tm_second_striker_count",
]
POSITION_ALLOWED = set(ROLE_ORDER) | {"Goalkeeper"}

BASE_FEATURES = [
    "home", "team_roll5_xg", "opp_roll5_xgc", "team_exp_xg", "opp_exp_xgc",
]
STRUCT_FEATURES = ROLE_FEATURES + [
    "formation_line_count", "formation_line_1", "formation_line_2",
    "formation_line_3", "formation_line_4",
    "prev_xi_overlap", "recent5_top11_overlap", "formation_changed", "role_l1_change",
]
CAND_FEATURES = BASE_FEATURES + STRUCT_FEATURES


def get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "r44l2-model/1.0"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return resp.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def csv_df(data: bytes, gzipped: bool = False, usecols=None) -> pd.DataFrame:
    raw = gzip.decompress(data) if gzipped else data
    return pd.read_csv(io.BytesIO(raw), usecols=usecols)


def to_bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )


def formation_geometry(value: str) -> list[int]:
    nums = [int(x) for x in re.findall(r"\d+", str(value))]
    if not nums or len(nums) > 4:
        raise RuntimeError(f"unsupported formation geometry: {value!r}")
    return nums + [0] * (4 - len(nums))


def build_nonlabel_structure(blobs: dict[str, bytes]):
    hashes = {name: sha256(data) for name, data in blobs.items()}
    for name, digest in hashes.items():
        if digest != EXPECTED_HASHES[name]:
            raise RuntimeError(f"SOURCE_HASH_DRIFT {name}: {digest} != {EXPECTED_HASHES[name]}")

    teams = csv_df(blobs["fpl_teams"], usecols=["id", "name"])
    team_name = {str(int(r.id)): canon(str(r.name)) for r in teams.itertuples(index=False)}

    fx = csv_df(blobs["fpl_fixtures"], usecols=["id", "kickoff_time", "team_h", "team_a"])
    fx = fx.dropna(subset=["id", "kickoff_time", "team_h", "team_a"]).copy()
    fx["id"] = pd.to_numeric(fx["id"], errors="raise").astype(int)
    fx["team_h"] = pd.to_numeric(fx["team_h"], errors="raise").astype(int)
    fx["team_a"] = pd.to_numeric(fx["team_a"], errors="raise").astype(int)
    fx["kickoff_dt"] = pd.to_datetime(fx["kickoff_time"], utc=True, errors="raise")
    fx = fx.sort_values(["kickoff_dt", "id"], kind="stable").reset_index(drop=True)
    if len(fx) != 380 or fx["id"].nunique() != 380:
        raise RuntimeError(f"fixture identity fail rows={len(fx)} unique={fx['id'].nunique()}")
    fx["home_team"] = [team_name[str(x)] for x in fx["team_h"]]
    fx["away_team"] = [team_name[str(x)] for x in fx["team_a"]]

    identity_to_fixture = {}
    for r in fx.itertuples(index=False):
        key = (date_from_iso(str(r.kickoff_time)), str(r.home_team), str(r.away_team))
        if key in identity_to_fixture:
            raise RuntimeError(f"duplicate FPL identity {key}")
        identity_to_fixture[key] = int(r.id)

    games = csv_df(
        blobs["tm_games"], gzipped=True,
        usecols=[
            "game_id", "competition_id", "date", "home_club_id", "away_club_id",
            "home_club_name", "away_club_name", "home_club_formation", "away_club_formation",
        ],
    )
    games = games[games["competition_id"].astype(str) == "GB1"].copy()
    match_map = {}
    fixture_to_game = {}
    for r in games.itertuples(index=False):
        key = (str(r.date), canon(str(r.home_club_name)), canon(str(r.away_club_name)))
        if key not in identity_to_fixture:
            continue
        fid = identity_to_fixture[key]
        if fid in fixture_to_game:
            raise RuntimeError(f"ambiguous TM identity fixture={fid}")
        fixture_to_game[fid] = str(r.game_id)
        match_map[str(r.game_id)] = {
            "fixture": fid,
            "home_team": key[1], "away_team": key[2],
            "home_club_id": str(r.home_club_id), "away_club_id": str(r.away_club_id),
            "home_formation": str(r.home_club_formation), "away_formation": str(r.away_club_formation),
        }
    if len(fixture_to_game) != 380:
        missing = sorted(set(fx["id"].tolist()) - set(fixture_to_game))
        raise RuntimeError(f"TM identity coverage fail matched={len(fixture_to_game)} missing={missing[:20]}")

    lineups = csv_df(
        blobs["tm_game_lineups"], gzipped=True,
        usecols=["game_id", "club_id", "type", "player_id", "player_name", "position"],
    )
    starters = defaultdict(dict)
    conflicts = []
    for r in lineups.itertuples(index=False):
        gid = str(r.game_id)
        if gid not in match_map or "start" not in norm(str(r.type)):
            continue
        club = str(r.club_id)
        pid = str(r.player_id) if pd.notna(r.player_id) else norm(str(r.player_name))
        position = str(r.position).strip() if pd.notna(r.position) else ""
        payload = (str(r.player_name), position)
        key = (gid, club)
        if pid in starters[key] and starters[key][pid] != payload:
            conflicts.append((gid, club, pid))
        else:
            starters[key][pid] = payload
    if conflicts:
        raise RuntimeError(f"conflicting starter duplicates: {conflicts[:20]}")

    structure = {}
    for gid, game in match_map.items():
        for side in ("home", "away"):
            team = game[f"{side}_team"]
            club = game[f"{side}_club_id"]
            formation = game[f"{side}_formation"].strip()
            lineup = starters.get((gid, club), {})
            if len(lineup) != 11 or not formation:
                raise RuntimeError(f"structural integrity fail fixture={game['fixture']} team={team} n={len(lineup)} formation={formation!r}")
            positions = [v[1] for v in lineup.values()]
            if any(not p for p in positions):
                raise RuntimeError(f"empty starter position fixture={game['fixture']} team={team}")
            unknown = sorted(set(positions) - POSITION_ALLOWED)
            if unknown:
                raise RuntimeError(f"unexpected frozen position fixture={game['fixture']} team={team}: {unknown}")
            if positions.count("Goalkeeper") != 1:
                raise RuntimeError(f"goalkeeper integrity fail fixture={game['fixture']} team={team}")
            counts = Counter(positions)
            roles = [int(counts.get(pos, 0)) for pos in ROLE_ORDER]
            geo = formation_geometry(formation)
            structure[(int(game["fixture"]), team)] = {
                "xi": tuple(sorted(lineup.keys())),
                "formation": formation,
                "roles": tuple(roles),
                "formation_geo": tuple(geo),
            }
    if len(structure) != 760:
        raise RuntimeError(f"structure team-row coverage fail: {len(structure)} != 760")
    return fx, structure, hashes


def load_labels(data: bytes, fx: pd.DataFrame):
    digest = sha256(data)
    if digest != EXPECTED_HASHES["fpl_merged_gw"]:
        raise RuntimeError(f"SOURCE_HASH_DRIFT fpl_merged_gw: {digest} != {EXPECTED_HASHES['fpl_merged_gw']}")
    cols = ["fixture", "team", "element", "was_home", "starts", "minutes", "expected_goals", "expected_assists"]
    d = csv_df(data, usecols=cols)
    d["fixture"] = pd.to_numeric(d["fixture"], errors="raise").astype(int)
    d["element"] = pd.to_numeric(d["element"], errors="raise").astype(int)
    d["team_canon"] = d["team"].astype(str).map(canon)
    d["was_home_norm"] = to_bool_series(d["was_home"])
    d["starts_num"] = pd.to_numeric(d["starts"], errors="coerce")
    d["minutes_num"] = pd.to_numeric(d["minutes"], errors="coerce")
    d["xg_num"] = pd.to_numeric(d["expected_goals"], errors="coerce")
    d["xa_num"] = pd.to_numeric(d["expected_assists"], errors="coerce")

    key = ["fixture", "element"]
    dup = d[d.duplicated(key, keep=False)].copy()
    if len(dup):
        compare = ["team_canon", "was_home_norm", "starts_num", "minutes_num", "xg_num", "xa_num"]
        conflicts = []
        for k, g in dup.groupby(key, sort=False):
            if any(g[c].fillna("<NA>").astype(str).nunique() > 1 for c in compare):
                conflicts.append(k)
        if conflicts:
            raise RuntimeError(f"merged label duplicate conflicts: {conflicts[:20]}")
    d = d.drop_duplicates(key, keep="first").copy()

    bad_xg = d[(d["minutes_num"].fillna(0) > 0) & d["xg_num"].isna()]
    bad_xa = d[(d["minutes_num"].fillna(0) > 0) & d["xa_num"].isna()]
    if len(bad_xg) or len(bad_xa):
        raise RuntimeError(f"LABEL_INTEGRITY_FAIL bad_xg={len(bad_xg)} bad_xa={len(bad_xa)}")
    d["xg_num"] = d["xg_num"].fillna(0.0)
    d["xa_num"] = d["xa_num"].fillna(0.0)

    fixture_set = set(fx["id"].tolist())
    d = d[d["fixture"].isin(fixture_set)].copy()
    rows = []
    for (fixture, team), g in d.groupby(["fixture", "team_canon"], sort=False):
        rows.append({
            "fixture": int(fixture), "team": str(team),
            "team_xg": float(g["xg_num"].sum()), "team_xa": float(g["xa_num"].sum()),
        })
    tm = pd.DataFrame(rows)
    if len(tm) != 760 or tm["fixture"].nunique() != 380:
        raise RuntimeError(f"label team-match coverage fail rows={len(tm)} fixtures={tm['fixture'].nunique()}")

    label_map = {(int(r.fixture), str(r.team)): (float(r.team_xg), float(r.team_xa)) for r in tm.itertuples(index=False)}
    for r in fx.itertuples(index=False):
        for team in (str(r.home_team), str(r.away_team)):
            if (int(r.id), team) not in label_map:
                raise RuntimeError(f"label identity missing fixture={r.id} team={team}")
    return label_map, digest


def generate_features(fx: pd.DataFrame, structure: dict, label_map: dict) -> pd.DataFrame:
    team_hist = defaultdict(list)
    feature_rows = []
    rank = {int(fid): i for i, fid in enumerate(fx["id"].tolist())}

    for kickoff, batch in fx.groupby("kickoff_dt", sort=True):
        pending = []
        for fr in batch.sort_values("id").itertuples(index=False):
            fid = int(fr.id)
            teams = [(str(fr.home_team), str(fr.away_team), 1), (str(fr.away_team), str(fr.home_team), 0)]
            pair_xg = {team: label_map[(fid, team)][0] for team, _, _ in teams}
            pair_xa = {team: label_map[(fid, team)][1] for team, _, _ in teams}
            for team, opp, home in teams:
                hist_t = team_hist[team]
                hist_o = team_hist[opp]
                eligible = len(hist_t) >= 1 and len(hist_o) >= 1
                st = structure[(fid, team)]
                roles = np.asarray(st["roles"], dtype=float)
                geo = list(st["formation_geo"])

                row = {
                    "fixture": fid,
                    "fixture_sort_rank": rank[fid],
                    "kickoff_time": pd.Timestamp(kickoff).isoformat(),
                    "team": team,
                    "opponent": opp,
                    "home": home,
                    "team_xg": pair_xg[team],
                    "team_xa": pair_xa[team],
                    "model_eligible": int(eligible),
                    "team_roll5_xg": float(np.mean([h["xg"] for h in hist_t[-5:]])) if hist_t else np.nan,
                    "opp_roll5_xgc": float(np.mean([h["xgc"] for h in hist_o[-5:]])) if hist_o else np.nan,
                    "team_exp_xg": float(np.mean([h["xg"] for h in hist_t])) if hist_t else np.nan,
                    "opp_exp_xgc": float(np.mean([h["xgc"] for h in hist_o])) if hist_o else np.nan,
                }
                for name, value in zip(ROLE_FEATURES, roles):
                    row[name] = float(value)
                row["formation_line_count"] = float(sum(1 for x in geo if x > 0))
                for i, value in enumerate(geo, start=1):
                    row[f"formation_line_{i}"] = float(value)

                current_xi = set(st["xi"])
                if hist_t:
                    prev = hist_t[-1]
                    row["prev_xi_overlap"] = float(len(current_xi & set(prev["xi"])))
                    row["formation_changed"] = float(st["formation"] != prev["formation"])
                    row["role_l1_change"] = float(np.abs(roles - np.asarray(prev["roles"], dtype=float)).sum())
                else:
                    row["prev_xi_overlap"] = 0.0
                    row["formation_changed"] = 0.0
                    row["role_l1_change"] = 0.0

                start_counts = Counter()
                for h in hist_t[-5:]:
                    start_counts.update(h["xi"])
                ranked = sorted(start_counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:11]
                recent_top11 = {pid for pid, _ in ranked}
                row["recent5_top11_overlap"] = float(len(current_xi & recent_top11)) if hist_t else 0.0
                feature_rows.append(row)
                pending.append((team, opp, st, pair_xg[team], pair_xg[opp], pair_xa[team]))

        for team, opp, st, xg, xgc, xa in pending:
            team_hist[team].append({
                "xg": xg, "xgc": xgc, "xa": xa,
                "xi": tuple(st["xi"]), "formation": st["formation"], "roles": tuple(st["roles"]),
            })

    return pd.DataFrame(feature_rows)


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
    return pred, nll, sigma


def calibration(obs, pred):
    obs = np.asarray(obs, dtype=float); pred = np.asarray(pred, dtype=float)
    if len(obs) < 3 or np.std(pred) < 1e-12:
        return {"intercept": None, "slope": None}
    slope, intercept = np.polyfit(pred, obs, 1)
    return {"intercept": float(intercept), "slope": float(slope)}


def metrics(obs, pred):
    obs = np.asarray(obs, dtype=float); pred = np.asarray(pred, dtype=float)
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
    out = np.empty(BOOTSTRAP_REPS, dtype=float)
    for i in range(0, BOOTSTRAP_REPS, 1000):
        k = min(1000, BOOTSTRAP_REPS - i)
        idx = rng.integers(0, len(md), size=(k, len(md)))
        out[i:i+k] = md[idx].mean(axis=1)
    return {
        "reps": BOOTSTRAP_REPS, "seed": BOOTSTRAP_SEED,
        "mean": float(out.mean()), "p05": float(np.quantile(out, 0.05)), "p95": float(np.quantile(out, 0.95)),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 1: frozen non-label identity/structure recheck.
    nonlabel = {name: get_bytes(url) for name, url in NONLABEL_URLS.items()}
    fx, structure, nonlabel_hashes = build_nonlabel_structure(nonlabel)

    # Phase 2: labels are opened only after Phase 1 has passed.
    label_bytes = get_bytes(LABEL_URL)
    label_map, label_hash = load_labels(label_bytes, fx)
    features = generate_features(fx, structure, label_map)

    selected = fx.tail(300).copy()
    selected_ids = selected["id"].astype(int).tolist()
    selected_rank = {fid: i + 1 for i, fid in enumerate(selected_ids)}
    formal = features[features["fixture"].isin(selected_ids)].copy()
    formal["formal_match_index"] = formal["fixture"].map(selected_rank)
    if len(formal) != 600 or formal["fixture"].nunique() != 300:
        raise RuntimeError(f"formal coverage fail rows={len(formal)} fixtures={formal['fixture'].nunique()}")
    if int(formal["model_eligible"].sum()) != 600:
        bad = formal[formal["model_eligible"] != 1][["fixture", "team"]].to_dict("records")
        raise RuntimeError(f"formal OOS contains ineligible rows: {bad[:20]}")
    if formal[CAND_FEATURES].isna().any().any():
        badcols = formal[CAND_FEATURES].columns[formal[CAND_FEATURES].isna().any()].tolist()
        raise RuntimeError(f"formal feature NaN: {badcols}")

    eligible = features[features["model_eligible"] == 1].copy()
    pred_chunks = []
    folds = []
    for fold in (1, 2, 3):
        lo = (fold - 1) * 100
        hi = fold * 100
        test_ids = set(selected_ids[lo:hi])
        first_fid = selected_ids[lo]
        first_kickoff = fx.loc[fx["id"] == first_fid, "kickoff_dt"].iloc[0]
        ek = pd.to_datetime(eligible["kickoff_time"], utc=True)
        train = eligible[ek < first_kickoff].copy()
        test = formal[formal["fixture"].isin(test_ids)].copy()
        if len(test) != 200 or test["fixture"].nunique() != 100 or len(train) < 100:
            raise RuntimeError(f"fold integrity fail fold={fold} train={len(train)} test={len(test)} matches={test['fixture'].nunique()}")

        b_xg, b_nll_xg, sig_xg = fit_predict(train, test, BASE_FEATURES, "team_xg")
        l_xg, l_nll_xg, _ = fit_predict(train, test, CAND_FEATURES, "team_xg")
        b_xa, b_nll_xa, sig_xa = fit_predict(train, test, BASE_FEATURES, "team_xa")
        l_xa, l_nll_xa, _ = fit_predict(train, test, CAND_FEATURES, "team_xa")

        p = test[["fixture", "formal_match_index", "kickoff_time", "team", "opponent", "home", "team_xg", "team_xa"]].copy()
        p["fold"] = fold
        p["pred_b0_xg"] = b_xg; p["pred_l2_xg"] = l_xg
        p["nll_b0_xg"] = b_nll_xg; p["nll_l2_xg"] = l_nll_xg; p["delta_nll_xg"] = l_nll_xg - b_nll_xg
        p["pred_b0_xa"] = b_xa; p["pred_l2_xa"] = l_xa
        p["nll_b0_xa"] = b_nll_xa; p["nll_l2_xa"] = l_nll_xa; p["delta_nll_xa"] = l_nll_xa - b_nll_xa
        pred_chunks.append(p)
        folds.append({
            "fold": fold, "train_team_rows": int(len(train)), "train_matches": int(train["fixture"].nunique()),
            "test_team_rows": int(len(test)), "test_matches": int(test["fixture"].nunique()),
            "sigma_xg_shared": float(sig_xg), "sigma_xa_shared": float(sig_xa),
            "nll_b0_xg": float(np.mean(b_nll_xg)), "nll_l2_xg": float(np.mean(l_nll_xg)),
            "delta_nll_xg": float(np.mean(l_nll_xg - b_nll_xg)),
            "nll_b0_xa": float(np.mean(b_nll_xa)), "nll_l2_xa": float(np.mean(l_nll_xa)),
            "delta_nll_xa": float(np.mean(l_nll_xa - b_nll_xa)),
        })

    preds = pd.concat(pred_chunks, ignore_index=True).sort_values(["formal_match_index", "team"], kind="stable").reset_index(drop=True)
    pooled_delta = float(preds["delta_nll_xg"].mean())
    pooled = {
        "nll_b0_xg": float(preds["nll_b0_xg"].mean()), "nll_l2_xg": float(preds["nll_l2_xg"].mean()),
        "delta_nll_xg": pooled_delta,
        "nll_b0_xa": float(preds["nll_b0_xa"].mean()), "nll_l2_xa": float(preds["nll_l2_xa"].mean()),
        "delta_nll_xa": float(preds["delta_nll_xa"].mean()),
        "b0_xg": metrics(preds["team_xg"], preds["pred_b0_xg"]), "l2_xg": metrics(preds["team_xg"], preds["pred_l2_xg"]),
        "b0_xa": metrics(preds["team_xa"], preds["pred_b0_xa"]), "l2_xa": metrics(preds["team_xa"], preds["pred_l2_xa"]),
    }
    boot = bootstrap_match_delta(preds)
    fold_improve_count = sum(1 for r in folds if r["delta_nll_xg"] < 0)

    loo = {}
    for team in sorted(preds["team"].unique()):
        loo[str(team)] = float(preds[preds["team"] != team]["delta_nll_xg"].mean())
    loo_all_negative = all(v < 0 for v in loo.values())

    team_delta = preds.groupby("team")["delta_nll_xg"].sum()
    improvement = (-team_delta).clip(lower=0)
    positive_total = float(improvement.sum())
    if positive_total > 0:
        shares = (improvement / positive_total).sort_values(ascending=False)
        max_team = str(shares.index[0]); max_share = float(shares.iloc[0])
    else:
        max_team = None; max_share = 1.0

    gates = {
        "pooled_delta_negative": pooled_delta < 0,
        "bootstrap_upper_negative": boot["p95"] < 0,
        "fold_improvement_at_least_2_of_3": fold_improve_count >= 2,
        "xg_rmse_no_material_worse": pooled["l2_xg"]["rmse"] <= 1.02 * pooled["b0_xg"]["rmse"],
        "xg_mae_no_material_worse": pooled["l2_xg"]["mae"] <= 1.02 * pooled["b0_xg"]["mae"],
        "xa_rmse_no_material_worse": pooled["l2_xa"]["rmse"] <= 1.02 * pooled["b0_xa"]["rmse"],
        "xa_mae_no_material_worse": pooled["l2_xa"]["mae"] <= 1.02 * pooled["b0_xa"]["mae"],
        "leave_one_team_out_all_negative": loo_all_negative,
        "single_team_improvement_share_below_50pct": max_share < 0.50,
    }
    verdict = "RETROSPECTIVE_SIGNAL_PASS" if all(gates.values()) else "RETROSPECTIVE_SIGNAL_FAIL"

    result = {
        "study_id": "r44l2_lineup_role_opportunity_300",
        "formal_weight": 0, "pit_eligible": False, "historical_lineup_available_at": "UNVERIFIED",
        "labels_opened_only_after_nonlabel_recheck": True,
        "source_hashes": nonlabel_hashes | {"fpl_merged_gw": label_hash},
        "sample": {
            "season_matches": 380, "formal_matches": 300, "formal_team_rows": 600, "warmup_matches": 80,
            "first_formal_kickoff": selected.iloc[0]["kickoff_dt"].isoformat(),
            "last_formal_kickoff": selected.iloc[-1]["kickoff_dt"].isoformat(),
        },
        "algorithm": {
            "ridge_alpha": RIDGE_ALPHA, "bootstrap_reps": BOOTSTRAP_REPS, "bootstrap_seed": BOOTSTRAP_SEED,
            "baseline_features": BASE_FEATURES, "structural_features": STRUCT_FEATURES,
            "baseline_feature_count": len(BASE_FEATURES), "candidate_feature_count": len(CAND_FEATURES),
        },
        "folds": folds, "pooled": pooled,
        "bootstrap_match_cluster_delta_nll_xg": boot, "fold_improvement_count": fold_improve_count,
        "leave_one_team_out_delta_nll_xg": loo,
        "single_team_concentration": {
            "max_positive_improvement_team": max_team,
            "max_positive_improvement_share": max_share,
            "positive_improvement_total": positive_total,
        },
        "gates": gates, "verdict": verdict,
    }
    (OUT_DIR / "model_result_r44l2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    preds.to_csv(OUT_DIR / "predictions_r44l2.csv", index=False)
    features.to_csv(OUT_DIR / "feature_rows_all_380_r44l2.csv", index=False)

    lines = [
        "# R44L2 300-match lineup role/formation result", "",
        f"- verdict: `{verdict}`", "- formal_weight: `0`", "- pit_eligible: `false`",
        f"- pooled ΔNLL xG (L2-B0): `{pooled_delta:.10f}`",
        f"- bootstrap 90% CI: `[{boot['p05']:.10f}, {boot['p95']:.10f}]`",
        f"- folds improved: `{fold_improve_count}/3`",
        f"- xG RMSE B0/L2: `{pooled['b0_xg']['rmse']:.6f}` / `{pooled['l2_xg']['rmse']:.6f}`",
        f"- xG MAE B0/L2: `{pooled['b0_xg']['mae']:.6f}` / `{pooled['l2_xg']['mae']:.6f}`",
        f"- xA RMSE B0/L2: `{pooled['b0_xa']['rmse']:.6f}` / `{pooled['l2_xa']['rmse']:.6f}`",
        f"- xA MAE B0/L2: `{pooled['b0_xa']['mae']:.6f}` / `{pooled['l2_xa']['mae']:.6f}`",
        "", "## Gates",
    ]
    lines += [f"- {k}: `{'PASS' if v else 'FAIL'}`" for k, v in gates.items()]
    lines += ["", "## Boundary", "", "Historical lineup available_at is unverified. This run is retrospective only and can never grant non-zero formal weight by itself.", ""]
    (OUT_DIR / "run_summary_r44l2.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "verdict": verdict, "pooled_delta_nll_xg": pooled_delta,
        "bootstrap_p05": boot["p05"], "bootstrap_p95": boot["p95"],
        "fold_improvement_count": fold_improve_count, "gates": gates,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
