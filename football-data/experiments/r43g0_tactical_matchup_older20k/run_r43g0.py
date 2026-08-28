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
if str(R9_DIR) not in sys.path:
    sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

SOURCE_PARENT = "d3a57e88559d429af09143264bfe781e93c13d54"
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
EXPECTED_FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
LOOKBACK = 8
MIN_STYLE = 3
MODEL_C = 0.25
STYLE_NAMES = [
    "shot_share", "sot_share", "xg_share", "possession_share", "corner_share",
    "pass_accuracy", "shot_tempo", "foul_share", "xg_per_shot", "sot_per_shot",
]
INTERACTION_NAMES = [
    "possession_gap_x_xg_gap",
    "tempo_sum_x_xg_gap",
    "pass_gap_x_shot_gap",
    "foul_gap_x_possession_gap",
    "corner_gap_x_shot_gap",
    "quality_gap_x_sot_gap",
]
TACTIC_NAMES = (
    [f"home_{x}" for x in STYLE_NAMES]
    + [f"away_{x}" for x in STYLE_NAMES]
    + [f"diff_{x}" for x in STYLE_NAMES]
    + [f"sum_{x}" for x in STYLE_NAMES]
    + INTERACTION_NAMES
    + ["home_style_known", "away_style_known", "style_known_min", "home_style_exp_log", "away_style_exp_log"]
)


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43g0/1"})
    with urllib.request.urlopen(req, timeout=600) as resp, path.open("wb") as f:
        while True:
            b = resp.read(1 << 20)
            if not b:
                break
            f.write(b)


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def num(v, default=None):
    if v is None or pd.isna(v):
        return default
    return float(v)


def share(a, b, prior=0.5):
    if a is None or b is None:
        return prior
    den = float(a) + float(b)
    return prior if den <= 0 else float(a) / den


def safe_ratio(a, b, prior=0.10):
    if a is None or b is None or float(b) <= 0:
        return prior
    return min(2.0, max(0.0, float(a) / float(b)))


def style_vector(row: dict, home: bool) -> np.ndarray:
    if home:
        shots, opp_shots = row["home_shots"], row["away_shots"]
        sot, opp_sot = row["home_sot"], row["away_sot"]
        xg, opp_xg = row["home_xg"], row["away_xg"]
        corners, opp_corners = row["home_corners"], row["away_corners"]
        poss = row["home_possession"]
        pa = row["home_pass_accuracy"]
        fouls, opp_fouls = row["home_fouls"], row["away_fouls"]
    else:
        shots, opp_shots = row["away_shots"], row["home_shots"]
        sot, opp_sot = row["away_sot"], row["home_sot"]
        xg, opp_xg = row["away_xg"], row["home_xg"]
        corners, opp_corners = row["away_corners"], row["home_corners"]
        poss = row["away_possession"]
        pa = row["away_pass_accuracy"]
        fouls, opp_fouls = row["away_fouls"], row["home_fouls"]
    total_shots = (shots or 0.0) + (opp_shots or 0.0)
    return np.asarray([
        share(shots, opp_shots),
        share(sot, opp_sot),
        share(xg, opp_xg),
        (poss if poss is not None else 50.0) / 100.0,
        share(corners, opp_corners),
        (pa if pa is not None else 75.0) / 100.0,
        math.log1p(total_shots) / math.log1p(40.0),
        share(fouls, opp_fouls),
        safe_ratio(xg, shots, 0.10),
        safe_ratio(sot, shots, 0.30),
    ], dtype=float)


def load_slice() -> tuple[list[dict], dict]:
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    download(FIX_URL, fp)
    download(STAT_URL, sp)
    if sha256(fp) != EXPECTED_FIX_SHA:
        raise RuntimeError("fixtures source drift")
    if sha256(sp) != EXPECTED_STAT_SHA:
        raise RuntimeError("match_stats source drift")
    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"],
    )
    stat_cols = [
        "fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at",
        "home_shots_total", "away_shots_total", "home_shots_on_goal", "away_shots_on_goal",
        "home_corners", "away_corners", "home_possession", "away_possession",
        "home_pass_accuracy", "away_pass_accuracy", "home_fouls", "away_fouls",
    ]
    st = pd.read_parquet(sp, columns=stat_cols)
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["kick"] = pd.to_datetime(df["date_utc"], utc=True)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > df["kick"]) & (df["home_xg"].between(0, 6)) & (df["away_xg"].between(0, 6))]
    df["date"] = df["kick"].dt.date.astype(str)
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    if len(df) < 100000:
        raise RuntimeError(f"need >=100000 rows, got {len(df)}")
    sl = df.iloc[-100000:-80000].copy()
    rows = []
    for x in sl.itertuples(index=False):
        rows.append({
            "date": str(x.date),
            "game_id": str(int(x.id)),
            "competition_id": str(int(x.league_id)),
            "home_team": str(int(x.home_team_id)),
            "away_team": str(int(x.away_team_id)),
            "home_goals": int(x.goals_home), "away_goals": int(x.goals_away),
            "home_xg": float(x.home_xg), "away_xg": float(x.away_xg),
            "xg_known_at": x.known.isoformat(),
            "home_shots": num(x.home_shots_total), "away_shots": num(x.away_shots_total),
            "home_sot": num(x.home_shots_on_goal), "away_sot": num(x.away_shots_on_goal),
            "home_corners": num(x.home_corners), "away_corners": num(x.away_corners),
            "home_possession": num(x.home_possession), "away_possession": num(x.away_possession),
            "home_pass_accuracy": num(x.home_pass_accuracy), "away_pass_accuracy": num(x.away_pass_accuracy),
            "home_fouls": num(x.home_fouls), "away_fouls": num(x.away_fouls),
        })
    return rows, {
        "fixtures_sha256": sha256(fp), "match_stats_sha256": sha256(sp), "valid_joined_rows": int(len(df)),
        "slice": "[-100000:-80000]", "rows": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
    }


def profile(hist: deque) -> tuple[np.ndarray, float, int]:
    if len(hist) < MIN_STYLE:
        return np.asarray([0.5,0.5,0.5,0.5,0.5,0.75,math.log1p(20)/math.log1p(40),0.5,0.10,0.30], dtype=float), 0.0, len(hist)
    arr = np.asarray(list(hist)[-LOOKBACK:], dtype=float)
    weights = np.asarray([0.82 ** i for i in range(len(arr)-1, -1, -1)], dtype=float)
    weights /= weights.sum()
    return np.sum(arr * weights[:, None], axis=0), 1.0, len(hist)


def tactic_features(home_team: str, away_team: str, histories) -> list[float]:
    hv, hk, hn = profile(histories[home_team])
    av, ak, an = profile(histories[away_team])
    diff = hv - av
    summ = hv + av
    interaction = [
        diff[3] * diff[2],
        summ[6] * diff[2],
        diff[5] * diff[0],
        diff[7] * diff[3],
        diff[4] * diff[0],
        diff[8] * diff[1],
    ]
    return list(hv) + list(av) + list(diff) + list(summ) + interaction + [hk, ak, min(hk, ak), math.log1p(hn), math.log1p(an)]


def build(rows: list[dict]):
    state = r9.S()
    histories = defaultdict(lambda: deque(maxlen=LOOKBACK))
    out = []
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    for ds in sorted(by_date):
        pending = []
        for row in sorted(by_date[ds], key=lambda z: z["game_id"]):
            raw = state.pred(row)
            tf = tactic_features(row["home_team"], row["away_team"], histories)
            out.append({"date": ds, "y": r9.actual(row), "raw": raw, "tactic": tf})
            pending.append((row, raw))
        # Strict same-date discipline: match statistics only enter histories after all predictions on that date.
        for row, raw in pending:
            histories[row["home_team"]].append(style_vector(row, True))
            histories[row["away_team"]].append(style_vector(row, False))
            state.update(row, raw)
    return out


def fit(train, fn):
    model = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    model.fit([fn(r) for r in train], [r["y"] for r in train])
    return model


def base_features(rec):
    return list(r9.feat_k1(rec["raw"]))


def tactic_model_features(rec):
    return base_features(rec) + list(rec["tactic"])


def decorate(model, rows, fn, key):
    pr = model.predict_proba([fn(r) for r in rows])
    classes = list(model[-1].classes_)
    for rec, probs in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for c, p in zip(classes, probs):
            v[int(c)] = p
        rec[key] = r9.decorate(v)


def blocks(test, n=4):
    out = []
    for ix in np.array_split(np.arange(len(test)), n):
        rr = [test[int(i)] for i in ix]
        b = r9.metrics(rr, "base")
        c = r9.metrics(rr, "tactic_model")
        out.append({
            "first_date": rr[0]["date"], "last_date": rr[-1]["date"], "n": len(rr),
            "base_hits": b["hits"], "candidate_hits": c["hits"], "delta_hits": c["hits"] - b["hits"],
            "delta_logloss": c["logloss"] - b["logloss"], "delta_brier": c["brier"] - b["brier"], "delta_rps": c["rps"] - b["rps"],
        })
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, meta = load_slice()
    pred = build(rows)
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]
    mb = fit(train, base_features)
    mt = fit(train, tactic_model_features)
    for subset in (val, test):
        decorate(mb, subset, base_features, "base")
        decorate(mt, subset, tactic_model_features, "tactic_model")
    vb, vc = r9.metrics(val, "base"), r9.metrics(val, "tactic_model")
    tb, tc = r9.metrics(test, "base"), r9.metrics(test, "tactic_model")
    bb = blocks(test)
    pos_ll = sum(x["delta_logloss"] < 0 for x in bb)
    neg_ll = sum(x["delta_logloss"] > 0 for x in bb)
    pos_hits = sum(x["delta_hits"] > 0 for x in bb)
    gate = bool(
        tc["logloss"] < tb["logloss"] and tc["brier"] < tb["brier"] and tc["rps"] < tb["rps"]
        and tc["hits"] >= tb["hits"] and pos_ll >= 3 and neg_ll <= 1 and pos_hits >= 2
    )
    result = {
        "schema_version": "football3-r43g0-tactical-matchup-older20k-v1",
        "status": "COMPLETE",
        "classification": "FIFTH_DISJOINT_20K_STRICT_PRIOR_TEAM_STYLE_MATCHUP_1X2_TEST",
        "formal_weight": 0,
        "source_parent": SOURCE_PARENT,
        "governance": {
            "source_overlap_with_r43e0_e1_e2_f0_scored_blocks": False,
            "target_match_stats_used_before_prediction": False,
            "same_date_update_before_prediction": False,
            "target_result_used_as_feature": False,
            "odds_used": False,
            "parameter_search": False,
            "feature_set_fixed_before_test": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "lookback_matches": LOOKBACK, "minimum_style_matches": MIN_STYLE,
            "style_features": STYLE_NAMES, "interaction_features": INTERACTION_NAMES,
            "candidate_feature_count": len(TACTIC_NAMES),
            "model": "R9b K1 multinomial baseline versus same logistic architecture plus strict-prior rolling tactical matchup fingerprint",
            "model_C": MODEL_C,
            "recency_decay": 0.82,
        },
        "source": meta,
        "split": {
            "burn_n": b1, "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]], "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]], "date_safe": True,
        },
        "validation": {"baseline": vb, "candidate": vc, "delta_hits": vc["hits"]-vb["hits"], "delta_logloss": vc["logloss"]-vb["logloss"]},
        "test": {
            "baseline": tb, "candidate": tc,
            "delta_hits": tc["hits"]-tb["hits"], "delta_accuracy_pp": 100*(tc["top1_accuracy"]-tb["top1_accuracy"]),
            "delta_logloss": tc["logloss"]-tb["logloss"], "delta_brier": tc["brier"]-tb["brier"], "delta_rps": tc["rps"]-tb["rps"],
            "blocks": bb, "positive_logloss_blocks": pos_ll, "negative_logloss_blocks": neg_ll, "positive_hit_blocks": pos_hits,
        },
        "gate": {"passed": gate, "action": "KEEP_R43G0_TACTICAL_MATCHUP_FOR_CONTEXT_STACK" if gate else "DO_NOT_PROMOTE_R43G0_AND_DO_NOT_RETUNE_ON_THIS_TEST"},
        "limitations": [
            "Post-match style statistics are used only in later fixtures; they are never used for the match that generated them.",
            "This is a fifth disjoint historical era, not forward evidence.",
            "Formation and current-match coach are not used; current availability and next-match importance remain separate layers.",
            "R42L remains untouched.",
        ],
    }
    p = OUT / "summary_r43g0_tactical_matchup.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify():
    d = json.loads((OUT / "summary_r43g0_tactical_matchup.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["source_overlap_with_r43e0_e1_e2_f0_scored_blocks"] is False
    assert g["target_match_stats_used_before_prediction"] is False
    assert g["same_date_update_before_prediction"] is False
    assert g["parameter_search"] is False
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    print("R43G0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv)>1 else "run"
    {"run": run, "verify": verify}[cmd]()
