#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
R9B_DIR = HERE.parents[0] / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9B_DIR))
import run_experiment_r9b as r9  # noqa: E402

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
OUT = HERE / "results"
TARGET_DAY = "2026-08-26"  # UEFA local calendar day; Beijing is 2026-08-27 03:00


def fit_k1_and_state():
    # Rebuild the exact R9b frozen 20k snapshot and its strict-prior training path.
    r9.freeze()
    rows = r9.load()
    st = r9.S()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            p = st.pred(row)
            pred.append({"date": ds, "y": r9.actual(row), "raw": p})
            pending.append((row, p))
        for row, p in pending:
            st.update(row, p)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    train = pred[b1:b2]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = [x["y"] for x in train]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(x["raw"]) for x in train], y)
    return rows, st, k1


def download_table(name: str) -> Path:
    p = HERE / f"_{name}.parquet"
    r9.download(f"{HF}/{name}.parquet?download=true", p)
    return p


def resolve_team(teams: pd.DataFrame, needle: str, alternates: tuple[str, ...]):
    id_col = "id" if "id" in teams.columns else "team_id" if "team_id" in teams.columns else None
    if id_col is None:
        raise RuntimeError(f"cannot identify team id column: {list(teams.columns)}")
    text_cols = [c for c in teams.columns if teams[c].dtype == object]
    keys = tuple(x.lower() for x in (needle,) + alternates)
    candidates = []
    for row in teams.itertuples(index=False):
        d = row._asdict()
        texts = [str(d.get(c) or "").strip() for c in text_cols]
        blob = " | ".join(texts).lower()
        score = 0
        for k in keys:
            if k == blob:
                score = max(score, 100)
            if any(k == t.lower() for t in texts):
                score = max(score, 90)
            if k in blob:
                score = max(score, 60 + len(k))
        if score:
            candidates.append((score, int(d[id_col]), texts))
    if not candidates:
        raise RuntimeError(f"no team match for {needle}; columns={list(teams.columns)}")
    candidates.sort(key=lambda x: (-x[0], x[1]))
    top = candidates[0]
    return {"team_id": str(top[1]), "texts": top[2], "candidates": candidates[:8]}


def resolve_ucl_league(leagues: pd.DataFrame) -> str:
    id_col = "id" if "id" in leagues.columns else "league_id" if "league_id" in leagues.columns else None
    if id_col is None:
        raise RuntimeError(f"cannot identify league id column: {list(leagues.columns)}")
    text_cols = [c for c in leagues.columns if leagues[c].dtype == object]
    hits = []
    for row in leagues.itertuples(index=False):
        d = row._asdict()
        blob = " | ".join(str(d.get(c) or "") for c in text_cols).lower()
        if "champions league" in blob and "women" not in blob and "youth" not in blob:
            hits.append((int(d[id_col]), blob))
    if not hits:
        raise RuntimeError("UEFA Champions League league id not found")
    # API-Football UCL is conventionally ID 2; prefer an exact-looking UEFA row if present.
    hits.sort(key=lambda x: (0 if x[0] == 2 else 1, len(x[1])))
    return str(hits[0][0])


def latest_comp_for_team(rows, tid: str, exclude: str):
    found = []
    for row in rows:
        if row["competition_id"] == exclude:
            continue
        if row["home_team"] == tid or row["away_team"] == tid:
            found.append((row["date"], row["competition_id"]))
    if not found:
        return f"DOMESTIC_{tid}"
    found.sort()
    return found[-1][1]


def class_probs(model, raw):
    p = model.predict_proba([r9.feat_k1(raw)])[0]
    classes = list(model[-1].classes_)
    v = np.zeros(3, dtype=float)
    for cls, prob in zip(classes, p):
        v[int(cls)] = float(prob)
    v = np.clip(v, 1e-12, None)
    v /= v.sum()
    return {
        "home": float(v[0]),
        "draw": float(v[1]),
        "away": float(v[2]),
        "top1": ("home", "draw", "away")[int(np.argmax(v))],
    }


def goal_update(st, row, include_xg: bool):
    # Same goal/latent update as R9b. xG history is updated only when source-quality is symmetric.
    p = st.pred(row)
    d = date.fromisoformat(row["date"])
    c = row["competition_id"]
    h = row["home_team"]
    a = row["away_team"]
    hg = int(row["home_goals"])
    ag = int(row["away_goals"])
    hs = st.v[(c, h, "H")]
    av = st.v[(c, a, "A")]
    hs.n += 1; hs.gf += hg; hs.ga += ag
    av.n += 1; av.gf += ag; av.ga += hg
    st.cn[c] += 1; st.ch[c] += hg; st.ca[c] += ag
    H = st.touch(h, d); A = st.touch(a, d)
    eh = r9.clamp((hg - p["latent_h"]) / (1 + p["latent_h"]), -2, 2)
    ea = r9.clamp((ag - p["latent_a"]) / (1 + p["latent_a"]), -2, 2)
    H.attack = r9.clamp(H.attack + r9.LR * eh, -1.2, 1.2)
    A.defence = r9.clamp(A.defence + r9.LR * eh, -1.2, 1.2)
    A.attack = r9.clamp(A.attack + r9.LR * ea, -1.2, 1.2)
    H.defence = r9.clamp(H.defence + r9.LR * ea, -1.2, 1.2)
    H.matches += 1; A.matches += 1
    if include_xg:
        hx = float(row["home_xg"]); ax = float(row["away_xg"])
        st.xgn[c] += 1; st.xgh[c] += hx; st.xga[c] += ax
        st.xhist[h].append((d, hx, ax)); st.xhist[a].append((d, ax, hx))
    return p


def main():
    rows, st, k1 = fit_k1_and_state()

    tp = download_table("teams")
    lp = download_table("leagues")
    teams = pd.read_parquet(tp)
    leagues = pd.read_parquet(lp)
    aek = resolve_team(teams, "AEK Athens", ("AEK", "Athens AEK"))
    levski = resolve_team(teams, "Levski Sofia", ("Levski", "PFC Levski Sofia"))
    ucl = resolve_ucl_league(leagues)
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)

    aek_id = aek["team_id"]
    levski_id = levski["team_id"]
    aek_dom = latest_comp_for_team(rows, aek_id, ucl)
    levski_dom = latest_comp_for_team(rows, levski_id, ucl)

    target = {
        "date": TARGET_DAY,
        "game_id": "WEB_AEK_LEVSKI_20260826",
        "competition_id": ucl,
        "home_team": aek_id,
        "away_team": levski_id,
        "home_goals": 0,
        "away_goals": 0,
        "home_xg": 0.0,
        "away_xg": 0.0,
    }

    raw_snapshot = st.pred(target)
    p_snapshot = class_probs(k1, raw_snapshot)

    updates = [
        {
            "label": "Levski Sofia 0-0 AEK Athens, UCL playoff first leg",
            "row": {"date": "2026-08-18", "game_id": "WEB_FIRST_LEG", "competition_id": ucl,
                    "home_team": levski_id, "away_team": aek_id, "home_goals": 0, "away_goals": 0,
                    "home_xg": 1.08, "away_xg": 1.10},
            "include_xg": True,
            "evidence": "published post-match first-leg xG; symmetric source",
        },
        {
            "label": "AEK Athens 4-0 Iraklis, Greek Super League",
            "row": {"date": "2026-08-22", "game_id": "WEB_AEK_IRAKLIS", "competition_id": aek_dom,
                    "home_team": aek_id, "away_team": "WEB_IRAKLIS", "home_goals": 4, "away_goals": 0,
                    "home_xg": 0.0, "away_xg": 0.0},
            "include_xg": False,
            "evidence": "score used; xG deliberately omitted from state update for symmetric treatment",
        },
        {
            "label": "Levski Sofia 6-0 Spartak Varna, Bulgarian First League",
            "row": {"date": "2026-08-22", "game_id": "WEB_LEVSKI_SPARTAK", "competition_id": levski_dom,
                    "home_team": levski_id, "away_team": "WEB_SPARTAK_VARNA", "home_goals": 6, "away_goals": 0,
                    "home_xg": 0.0, "away_xg": 0.0},
            "include_xg": False,
            "evidence": "score used; xG deliberately omitted from state update for symmetric treatment",
        },
    ]
    for u in updates:
        goal_update(st, u["row"], u["include_xg"])

    raw_updated = st.pred(target)
    p_updated = class_probs(k1, raw_updated)

    out = {
        "status": "COMPLETE",
        "model": "R9b K1 retained baseline after R10/R11 diagnostics",
        "target": {
            "display": "AEK Athens vs Levski Sofia",
            "uefa_day": TARGET_DAY,
            "beijing_time": "2026-08-27 03:00",
            "competition_id": ucl,
            "home_team_id": aek_id,
            "away_team_id": levski_id,
        },
        "governance": {
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "post_match_target_data_used": False,
            "strict_prior_snapshot": True,
            "same_day_leakage": False,
        },
        "team_resolution": {"aek": aek, "levski": levski, "aek_domestic_comp": aek_dom, "levski_domestic_comp": levski_dom},
        "snapshot_only": {"probabilities": p_snapshot, "raw": raw_snapshot},
        "pre_match_updated": {"probabilities": p_updated, "raw": raw_updated},
        "pre_match_updates": updates,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "aek_levski_20260826.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
