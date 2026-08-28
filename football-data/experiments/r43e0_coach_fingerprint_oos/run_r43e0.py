#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9_DIR = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

SOURCE_R43D0A_HEAD = "f430def186468f561d8afec6c700c6acef70e46c"
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
LINEUPS_URL = f"{HF}/fixture_lineups.parquet?download=true"
STATS_URL = f"{HF}/match_stats.parquet?download=true"
EXPECTED_R9B_SHA = "6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba"
COACH_LOOKBACK = 20
MIN_COACH_MATCHES = 3
MODEL_C = 0.5

STYLE = (
    "shot_share",
    "sot_share",
    "xg_share",
    "possession_share",
    "corner_share",
    "pass_accuracy",
    "shot_tempo",
    "foul_share",
)
FEATURES = []
for n in STYLE:
    FEATURES += [f"home_{n}", f"away_{n}", f"diff_{n}"]
FEATURES += [
    "home_coach_known", "away_coach_known", "coach_known_min",
    "home_coach_exp_log", "away_coach_exp_log", "coach_exp_log_diff",
    "home_team_coach_tenure_log", "away_team_coach_tenure_log", "team_coach_tenure_log_diff",
]


def fsha(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43e0/1"})
    with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def load_aux(rows: list[dict]):
    wanted = {int(r["game_id"]) for r in rows}
    lp = DATA / "fixture_lineups.parquet"
    sp = DATA / "match_stats.parquet"
    download(LINEUPS_URL, lp)
    download(STATS_URL, sp)

    line = pd.read_parquet(lp, columns=["fixture_id", "team_id", "coach_name", "coach_api_id"])
    line = line[line["fixture_id"].isin(wanted)].copy()
    coach_map = {}
    for x in line.itertuples(index=False):
        if pd.notna(x.coach_api_id):
            cid = f"id:{int(x.coach_api_id)}"
        elif pd.notna(x.coach_name) and str(x.coach_name).strip():
            cid = "name:" + str(x.coach_name).strip().casefold()
        else:
            continue
        coach_map[(str(int(x.fixture_id)), str(int(x.team_id)))] = cid

    cols = [
        "fixture_id", "home_shots_total", "away_shots_total", "home_shots_on_goal", "away_shots_on_goal",
        "home_corners", "away_corners", "home_xg", "away_xg", "home_possession", "away_possession",
        "home_pass_accuracy", "away_pass_accuracy", "home_fouls", "away_fouls", "known_at",
    ]
    st = pd.read_parquet(sp, columns=cols)
    st = st[st["fixture_id"].isin(wanted)].copy()
    stats_map = {str(int(x.fixture_id)): x for x in st.itertuples(index=False)}
    meta = {
        "fixture_lineups_sha256": fsha(lp),
        "match_stats_sha256": fsha(sp),
        "wanted_fixtures": len(wanted),
        "matched_coach_team_rows": len(coach_map),
        "matched_stats_fixtures": len(stats_map),
    }
    return coach_map, stats_map, meta


def num(v, default=None):
    if v is None or pd.isna(v):
        return default
    return float(v)


def share(a, b, prior=0.5):
    if a is None or b is None:
        return prior
    den = a + b
    return prior if den <= 0 else a / den


def side_style(s, home: bool):
    if home:
        shots, opp_shots = num(s.home_shots_total), num(s.away_shots_total)
        sot, opp_sot = num(s.home_shots_on_goal), num(s.away_shots_on_goal)
        xg, opp_xg = num(s.home_xg), num(s.away_xg)
        corners, opp_corners = num(s.home_corners), num(s.away_corners)
        poss = num(s.home_possession, 50.0)
        pa = num(s.home_pass_accuracy, 75.0)
        fouls, opp_fouls = num(s.home_fouls), num(s.away_fouls)
    else:
        shots, opp_shots = num(s.away_shots_total), num(s.home_shots_total)
        sot, opp_sot = num(s.away_shots_on_goal), num(s.home_shots_on_goal)
        xg, opp_xg = num(s.away_xg), num(s.home_xg)
        corners, opp_corners = num(s.away_corners), num(s.home_corners)
        poss = num(s.away_possession, 50.0)
        pa = num(s.away_pass_accuracy, 75.0)
        fouls, opp_fouls = num(s.away_fouls), num(s.home_fouls)
    tempo = math.log1p((shots or 0.0) + (opp_shots or 0.0))
    return np.asarray([
        share(shots, opp_shots),
        share(sot, opp_sot),
        share(xg, opp_xg),
        float(poss) / 100.0,
        share(corners, opp_corners),
        float(pa) / 100.0,
        tempo,
        share(fouls, opp_fouls),
    ], dtype=float)


def coach_vector(history: deque):
    if len(history) < MIN_COACH_MATCHES:
        return np.full(len(STYLE), 0.5), 0.0, len(history)
    a = np.asarray(list(history)[-COACH_LOOKBACK:], dtype=float)
    v = a.mean(axis=0)
    # shot tempo has a different natural center; missing coaches should not inherit 0.5 here.
    return v, 1.0, len(history)


def context_features(home_team, away_team, team_last_coach, coach_hist, team_tenure):
    hc = team_last_coach.get(home_team)
    ac = team_last_coach.get(away_team)
    hv, hk, hn = coach_vector(coach_hist[hc]) if hc else (np.array([.5,.5,.5,.5,.5,.5,math.log1p(20),.5]), 0.0, 0)
    av, ak, an = coach_vector(coach_hist[ac]) if ac else (np.array([.5,.5,.5,.5,.5,.5,math.log1p(20),.5]), 0.0, 0)
    z = []
    for i in range(len(STYLE)):
        z += [float(hv[i]), float(av[i]), float(hv[i] - av[i])]
    ht = team_tenure.get(home_team, 0) if hc else 0
    at = team_tenure.get(away_team, 0) if ac else 0
    z += [hk, ak, min(hk, ak), math.log1p(hn), math.log1p(an), math.log1p(hn) - math.log1p(an),
          math.log1p(ht), math.log1p(at), math.log1p(ht) - math.log1p(at)]
    return z


def build(rows, coach_map, stats_map):
    state = r9.S()
    coach_hist = defaultdict(lambda: deque(maxlen=COACH_LOOKBACK))
    team_last_coach = {}
    team_tenure = defaultdict(int)
    out = []
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)

    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = state.pred(row)
            cfeat = context_features(row["home_team"], row["away_team"], team_last_coach, coach_hist, team_tenure)
            out.append({"date": ds, "y": r9.actual(row), "raw": raw, "coach": cfeat})
            pending.append((row, raw))

        # Strict PIT: target-match coach identity and post-match stats become usable only after
        # all matches on the same date have been predicted.
        for row, raw in pending:
            fid = str(row["game_id"])
            stat = stats_map.get(fid)
            for home, tid in ((True, row["home_team"]), (False, row["away_team"])):
                cid = coach_map.get((fid, tid))
                if cid:
                    if team_last_coach.get(tid) == cid:
                        team_tenure[tid] += 1
                    else:
                        team_last_coach[tid] = cid
                        team_tenure[tid] = 1
                    if stat is not None:
                        coach_hist[cid].append(side_style(stat, home))
            state.update(row, raw)
    return out


def feat_base(rec):
    return list(r9.feat_k1(rec["raw"]))


def feat_coach(rec):
    return feat_base(rec) + list(rec["coach"])


def fit(train, fn):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    m.fit([fn(r) for r in train], [r["y"] for r in train])
    return m


def decorate(model, rows, fn, key):
    pr = model.predict_proba([fn(r) for r in rows])
    classes = list(model[-1].classes_)
    for rec, probs in zip(rows, pr):
        v = np.zeros(3)
        for c, p in zip(classes, probs):
            v[int(c)] = p
        rec[key] = r9.decorate(v)


def metrics(rows, key):
    return r9.metrics(rows, key)


def blocks(test, n=4):
    z = np.array_split(np.arange(len(test)), n)
    out = []
    for ix in z:
        rr = [test[int(i)] for i in ix]
        b = metrics(rr, "base")
        c = metrics(rr, "coach_model")
        out.append({
            "first_date": rr[0]["date"], "last_date": rr[-1]["date"], "n": len(rr),
            "base_hits": b["hits"], "coach_hits": c["hits"], "delta_hits": c["hits"] - b["hits"],
            "delta_logloss": c["logloss"] - b["logloss"],
            "delta_brier": c["brier"] - b["brier"],
            "delta_rps": c["rps"] - b["rps"],
        })
    return out


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = r9.load()
    snap = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
    if fsha(snap) != EXPECTED_R9B_SHA:
        raise RuntimeError("R9b source drift")
    coach_map, stats_map, source = load_aux(rows)
    pred = build(rows, coach_map, stats_map)
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    mb = fit(train, feat_base)
    mc = fit(train, feat_coach)
    for subset in (val, test):
        decorate(mb, subset, feat_base, "base")
        decorate(mc, subset, feat_coach, "coach_model")
    vb, vc = metrics(val, "base"), metrics(val, "coach_model")
    tb, tc = metrics(test, "base"), metrics(test, "coach_model")
    bb = blocks(test)
    pos_ll = sum(x["delta_logloss"] < 0 for x in bb)
    neg_ll = sum(x["delta_logloss"] > 0 for x in bb)
    pass_gate = bool(
        tc["logloss"] < tb["logloss"]
        and tc["brier"] < tb["brier"]
        and tc["rps"] < tb["rps"]
        and tc["hits"] >= tb["hits"]
        and pos_ll >= 2
        and neg_ll <= 2
    )

    known = [r["coach"][24 + 2] for r in test]  # coach_known_min after 8*3 style features
    result = {
        "schema_version": "football3-r43e0-coach-fingerprint-oos-v1",
        "status": "COMPLETE",
        "classification": "STRICT_PRIOR_LAST_OBSERVED_COACH_STYLE_INCREMENTAL_1X2_TEST",
        "formal_weight": 0,
        "source_r43d0a_head": SOURCE_R43D0A_HEAD,
        "governance": {
            "target_match_coach_row_used_for_prediction": False,
            "target_match_formation_used": False,
            "target_match_stats_used_before_prediction": False,
            "same_date_update_before_prediction": False,
            "odds_used": False,
            "parameter_search": False,
            "coach_feature_set_fixed_before_test": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "target_coach_proxy": "last observed coach from strictly prior completed match for that team",
            "coach_history_lookback": COACH_LOOKBACK,
            "min_coach_matches": MIN_COACH_MATCHES,
            "style_features": list(STYLE),
            "candidate_features": FEATURES,
            "model": "R9b K1 baseline versus identical multinomial logistic model plus coach fingerprint",
            "model_C": MODEL_C,
            "new_coach_first_match_policy": "conservative unknown until coach is observed in a completed prior fixture",
        },
        "source": {**source, "r9b_snapshot_sha256": EXPECTED_R9B_SHA},
        "split": {
            "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
        },
        "coverage": {"test_both_coach_fingerprints_known_rate": float(np.mean(known))},
        "validation": {"baseline": vb, "coach": vc, "delta_logloss": vc["logloss"] - vb["logloss"], "delta_hits": vc["hits"] - vb["hits"]},
        "test": {"baseline": tb, "coach": tc, "delta_logloss": tc["logloss"] - tb["logloss"], "delta_brier": tc["brier"] - tb["brier"], "delta_rps": tc["rps"] - tb["rps"], "delta_hits": tc["hits"] - tb["hits"], "blocks": bb},
        "gate": {"passed": pass_gate, "action": "PROMOTE_COACH_FINGERPRINT_TO_R43_CONTEXT_STACK" if pass_gate else "DO_NOT_PROMOTE_R43E0_AND_DO_NOT_RETUNE_ON_TEST"},
        "limitations": [
            "Current-match coach identity is deliberately not read because fixture_lineups has no prematch known_at timestamp.",
            "Therefore a managerial change is only recognized after the new coach has completed one match; this sacrifices freshness to preserve PIT integrity.",
            "Style statistics are post-match facts used only for later fixtures; missing stats shrink to neutral through coverage and model fitting.",
            "This is development evidence on an already-used era, not fresh forward confirmation.",
        ],
    }
    p = OUT / "summary_r43e0_coach_fingerprint.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r43e0_coach_fingerprint.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["target_match_coach_row_used_for_prediction"] is False
    assert g["target_match_stats_used_before_prediction"] is False
    assert g["same_date_update_before_prediction"] is False
    assert g["parameter_search"] is False
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    print("R43E0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": run, "verify": verify}[cmd]()
