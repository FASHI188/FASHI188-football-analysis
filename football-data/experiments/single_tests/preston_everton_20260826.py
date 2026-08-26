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
TARGET_DAY = "2026-08-26"


def fit_k1_and_state():
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


def string_cols(df: pd.DataFrame):
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype == object]


def resolve_team(teams: pd.DataFrame, needle: str, alternates: tuple[str, ...] = ()):
    id_col = "id" if "id" in teams.columns else "team_id" if "team_id" in teams.columns else None
    if id_col is None:
        raise RuntimeError(f"cannot identify team id column: {list(teams.columns)}")
    text_cols = string_cols(teams)
    keys = tuple(x.lower() for x in (needle,) + alternates)
    candidates = []
    for row in teams.itertuples(index=False):
        d = row._asdict()
        texts = [str(d.get(c) or "").strip() for c in text_cols]
        low = [t.lower() for t in texts]
        blob = " | ".join(low)
        score = 0
        for k in keys:
            if any(k == t for t in low):
                score = max(score, 100)
            if k in blob:
                score = max(score, 60 + len(k))
        if score:
            try:
                tid = int(d[id_col])
            except Exception:
                continue
            candidates.append((score, tid, texts))
    if not candidates:
        raise RuntimeError(f"no team match for {needle}; columns={list(teams.columns)}")
    candidates.sort(key=lambda x: (-x[0], x[1]))
    top = candidates[0]
    return {"team_id": str(top[1]), "texts": top[2], "candidates": candidates[:6]}


def resolve_league(leagues: pd.DataFrame, keys: tuple[str, ...]):
    id_col = "id" if "id" in leagues.columns else "league_id" if "league_id" in leagues.columns else None
    if id_col is None:
        raise RuntimeError(f"cannot identify league id column: {list(leagues.columns)}")
    text_cols = string_cols(leagues)
    candidates = []
    for row in leagues.itertuples(index=False):
        d = row._asdict()
        texts = [str(d.get(c) or "").strip() for c in text_cols]
        low = [t.lower() for t in texts]
        blob = " | ".join(low)
        score = 0
        for key in keys:
            k = key.lower()
            if any(k == t for t in low):
                score = max(score, 100)
            if k in blob:
                score = max(score, 60 + len(k))
        if "england" in blob:
            score += 5
        if score:
            try:
                lid = int(d[id_col])
            except Exception:
                continue
            candidates.append((score, lid, texts))
    if not candidates:
        raise RuntimeError(f"league not found for keys={keys}; columns={list(leagues.columns)}")
    candidates.sort(key=lambda x: (-x[0], x[1]))
    top = candidates[0]
    return {"league_id": str(top[1]), "texts": top[2], "candidates": candidates[:8]}


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


def poisson_scorelines(mu_h: float, mu_a: float, max_goals: int = 7):
    out = []
    for h in range(max_goals + 1):
        ph = math.exp(-mu_h) * mu_h**h / math.factorial(h)
        for a in range(max_goals + 1):
            pa = math.exp(-mu_a) * mu_a**a / math.factorial(a)
            out.append({"score": f"{h}-{a}", "probability": ph * pa})
    out.sort(key=lambda x: -x["probability"])
    return out[:10]


def apply_update(st, row):
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

    preston = resolve_team(teams, "Preston North End", ("Preston",))
    everton = resolve_team(teams, "Everton")
    cup = resolve_league(leagues, ("League Cup", "Carabao Cup", "EFL Cup"))
    champ = resolve_league(leagues, ("Championship",))
    prem = resolve_league(leagues, ("Premier League",))
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)

    p_id = preston["team_id"]
    e_id = everton["team_id"]
    cup_id = cup["league_id"]
    champ_id = champ["league_id"]
    prem_id = prem["league_id"]

    target = {
        "date": TARGET_DAY,
        "game_id": "WEB_PRESTON_EVERTON_20260826",
        "competition_id": cup_id,
        "home_team": p_id,
        "away_team": e_id,
        "home_goals": 0,
        "away_goals": 0,
        "home_xg": 0.0,
        "away_xg": 0.0,
    }

    raw_pure = st.pred(target)
    pure = class_probs(k1, raw_pure)

    updates = [
        {
            "label": "Preston 1-1 Huddersfield, EFL Cup R1",
            "row": {"date":"2026-08-08","game_id":"WEB_PNE_HUD","competition_id":cup_id,
                    "home_team":p_id,"away_team":"WEB_HUDDERSFIELD","home_goals":1,"away_goals":1,
                    "home_xg":0.87,"away_xg":0.64},
            "source_note": "FotMob/Opta indexed match stats",
        },
        {
            "label": "Bolton 2-1 Preston, Championship",
            "row": {"date":"2026-08-15","game_id":"WEB_BOL_PNE","competition_id":champ_id,
                    "home_team":"WEB_BOLTON","away_team":p_id,"home_goals":2,"away_goals":1,
                    "home_xg":1.10,"away_xg":1.12},
            "source_note": "FotMob/Opta indexed match stats",
        },
        {
            "label": "Preston 1-3 Wolves, Championship",
            "row": {"date":"2026-08-22","game_id":"WEB_PNE_WOL","competition_id":champ_id,
                    "home_team":p_id,"away_team":"WEB_WOLVES","home_goals":1,"away_goals":3,
                    "home_xg":0.42,"away_xg":1.46},
            "source_note": "FOX Sports indexed match stats",
        },
        {
            "label": "Everton 2-0 Crystal Palace, Premier League",
            "row": {"date":"2026-08-22","game_id":"WEB_EVE_CRY","competition_id":prem_id,
                    "home_team":e_id,"away_team":"WEB_CRYSTAL_PALACE","home_goals":2,"away_goals":0,
                    "home_xg":1.16,"away_xg":2.04},
            "source_note": "FotMob/Opta indexed match stats",
        },
    ]
    for u in updates:
        apply_update(st, u["row"])

    raw_updated = st.pred(target)
    updated = class_probs(k1, raw_updated)

    context = {
        "not_used_as_probability_inputs": {
            "preston_absences": [
                "Lewis Gibson unavailable (suspension; groin issue also reported)",
                "Odel Offiah groin issue / availability concern",
                "Jusef Erabi doubtful after weekend knock",
                "Brad Potts reported out by Racing Post",
                "Theo Carroll ankle issue / assessment",
                "Milutin Osmajic moved to Genoa",
            ],
            "everton_absences": [
                "Tim Iroegbunam out with adductor strain",
                "Christian Norgaard not yet match fit",
                "James Garner only recently returned after hernia surgery; starting readiness uncertain",
            ],
            "rotation": "Everton expected to rotate several positions; Preston may also change personnel",
            "market_snapshot": {"preston_decimal":5.75,"draw_decimal":4.3333333333,"everton_decimal":1.5333333333},
        },
        "reason_not_used": "R9b K1 scientific contract has no player/lineup/odds feature interface; no manual probability adjustment allowed.",
    }

    out = {
        "status": "COMPLETE",
        "model": "R9b K1 retained baseline after R10/R11 diagnostics",
        "target": {
            "display": "Preston North End vs Everton",
            "competition": "EFL Cup second round",
            "local_day": TARGET_DAY,
            "beijing_time": "2026-08-27 03:00",
            "home_team_id": p_id,
            "away_team_id": e_id,
            "competition_id": cup_id,
        },
        "governance": {
            "pure_snapshot_available": True,
            "prematch_competitive_updates_used": True,
            "recent_competitive_results_used": True,
            "recent_xg_used": True,
            "friendlies_used": False,
            "injuries_used_as_model_features": False,
            "predicted_lineups_used_as_model_features": False,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "post_match_target_data_used": False,
        },
        "resolution": {"preston":preston,"everton":everton,"cup":cup,"championship":champ,"premier_league":prem},
        "pure_r9b_snapshot": {
            "probabilities": pure,
            "raw": raw_pure,
            "scorelines": poisson_scorelines(float(raw_pure["mu_home"]), float(raw_pure["mu_away"])),
        },
        "prematch_updated": {
            "probabilities": updated,
            "raw": raw_updated,
            "scorelines": poisson_scorelines(float(raw_updated["mu_home"]), float(raw_updated["mu_away"])),
        },
        "prematch_updates": updates,
        "context": context,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "preston_everton_20260826.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
