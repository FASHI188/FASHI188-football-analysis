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
EXP = HERE.parent
R14_DIR = EXP / "top1_r14_compact_draw"
sys.path.insert(0, str(R14_DIR))
import run_experiment_r14 as r14  # noqa: E402

r12 = r14.r12
r9 = r14.r9
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
OUT = HERE / "results"
TARGET_DAY = "2026-08-26"
DRAW_GATE = 0.08


def fit_locked_models_and_states():
    r12.freeze_gate()
    rows = r9.load()
    base = r9.S()
    draw = r12.DrawState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = base.pred(row)
            df = draw.features(row, raw)
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "df": df})
            pending.append((row, raw))
        for row, raw in pending:
            base.update(row, raw)
            draw.update(row)
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    train = pred[b1:b2]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = [x["y"] for x in train]
    def mdl():
        return make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    k1 = mdl(); k3 = mdl()
    k1.fit([r9.feat_k1(x["raw"]) for x in train], y)
    k3.fit([r9.feat_k1(x["raw"]) + r14.compact(x["df"]) for x in train], y)
    return rows, base, draw, k1, k3


def download_table(name: str) -> Path:
    p = HERE / f"_{name}.parquet"
    r9.download(f"{HF}/{name}.parquet?download=true", p)
    return p


def string_columns(df: pd.DataFrame):
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype == object]


def resolve_team(teams: pd.DataFrame, needle: str, alternates: tuple[str, ...]):
    id_col = "id" if "id" in teams.columns else "team_id" if "team_id" in teams.columns else None
    if id_col is None:
        raise RuntimeError(f"cannot identify team id column: {list(teams.columns)}")
    text_cols = string_columns(teams)
    keys = tuple(x.lower() for x in (needle,) + alternates)
    candidates = []
    for row in teams.itertuples(index=False):
        d = row._asdict()
        texts = [str(d.get(c) or "").strip() for c in text_cols]
        blob = " | ".join(texts).lower()
        score = 0
        for k in keys:
            if any(k == t.lower() for t in texts): score = max(score, 100)
            if k in blob: score = max(score, 60 + len(k))
        if score:
            candidates.append((score, int(d[id_col]), texts))
    if not candidates:
        raise RuntimeError(f"no team match for {needle}")
    candidates.sort(key=lambda x: (-x[0], x[1]))
    top = candidates[0]
    return {"team_id": str(top[1]), "texts": top[2], "candidates": candidates[:5]}


def resolve_ucl_league(leagues: pd.DataFrame) -> str:
    id_col = "id" if "id" in leagues.columns else "league_id" if "league_id" in leagues.columns else None
    text_cols = string_columns(leagues)
    hits = []
    for row in leagues.itertuples(index=False):
        d = row._asdict(); blob = " | ".join(str(d.get(c) or "") for c in text_cols).lower()
        if "champions league" in blob and "women" not in blob and "youth" not in blob:
            hits.append((int(d[id_col]), blob))
    if not hits: raise RuntimeError("UEFA Champions League league id not found")
    hits.sort(key=lambda x: (0 if x[0] == 2 else 1, len(x[1])))
    return str(hits[0][0])


def latest_comp_for_team(rows, tid: str, exclude: str):
    found = [(r["date"], r["competition_id"]) for r in rows if r["competition_id"] != exclude and (r["home_team"] == tid or r["away_team"] == tid)]
    if not found: return f"DOMESTIC_{tid}"
    found.sort(); return found[-1][1]


def named(p):
    return {"home": p["p_home"], "draw": p["p_draw"], "away": p["p_away"], "top1": ("home", "draw", "away")[int(p["top1"])]}


def predict(k1, k3, draw_state, target, raw):
    df = draw_state.features(target, raw)
    p1 = r14.decorate_probs(k1, [r9.feat_k1(raw)])[0]
    p3 = r14.decorate_probs(k3, [r9.feat_k1(raw) + r14.compact(df)])[0]
    edge = float(p3["p_draw"] - max(p3["p_home"], p3["p_away"]))
    gate_top1 = 1 if int(p1["top1"]) != 1 and int(p3["top1"]) == 1 and edge >= DRAW_GATE else int(p1["top1"])
    return {"K1": named(p1), "K3": named(p3), "R15_gate": {"top1": ("home", "draw", "away")[gate_top1], "draw_edge": edge, "activated": gate_top1 != int(p1["top1"])}}


def goal_update(st, row, include_xg: bool):
    p = st.pred(row)
    d = date.fromisoformat(row["date"]); c = row["competition_id"]; h = row["home_team"]; a = row["away_team"]
    hg = int(row["home_goals"]); ag = int(row["away_goals"])
    hs = st.v[(c,h,"H")]; av = st.v[(c,a,"A")]
    hs.n += 1; hs.gf += hg; hs.ga += ag; av.n += 1; av.gf += ag; av.ga += hg
    st.cn[c] += 1; st.ch[c] += hg; st.ca[c] += ag
    H = st.touch(h,d); A = st.touch(a,d)
    eh = r9.clamp((hg-p["latent_h"])/(1+p["latent_h"]),-2,2); ea = r9.clamp((ag-p["latent_a"])/(1+p["latent_a"]),-2,2)
    H.attack = r9.clamp(H.attack+r9.LR*eh,-1.2,1.2); A.defence = r9.clamp(A.defence+r9.LR*eh,-1.2,1.2)
    A.attack = r9.clamp(A.attack+r9.LR*ea,-1.2,1.2); H.defence = r9.clamp(H.defence+r9.LR*ea,-1.2,1.2)
    H.matches += 1; A.matches += 1
    if include_xg:
        hx = float(row["home_xg"]); ax = float(row["away_xg"])
        st.xgn[c] += 1; st.xgh[c] += hx; st.xga[c] += ax
        st.xhist[h].append((d,hx,ax)); st.xhist[a].append((d,ax,hx))


def score_peaks(raw, topn=8):
    mh = float(raw["mu_home"]); ma = float(raw["mu_away"]); arr=[]
    for h in range(8):
        ph = math.exp(-mh) * mh**h / math.factorial(h)
        for a in range(8):
            pa = math.exp(-ma) * ma**a / math.factorial(a)
            arr.append({"score": f"{h}-{a}", "probability": ph*pa})
    arr.sort(key=lambda x: -x["probability"])
    return arr[:topn]


def main():
    rows, st, draw_state, k1, k3 = fit_locked_models_and_states()
    tp = download_table("teams"); lp = download_table("leagues")
    teams = pd.read_parquet(tp); leagues = pd.read_parquet(lp)
    R = lambda n, a=(): resolve_team(teams, n, a)
    lyon = R("Lyon", ("Olympique Lyonnais", "Olympique Lyon"))
    fener = R("Fenerbahce", ("Fenerbahçe", "Fenerbahce SK", "Fenerbahçe SK"))
    sparta = R("Sparta Prague", ("Sparta Praha", "AC Sparta Praha"))
    gornik = R("Gornik Zabrze", ("Górnik Zabrze",))
    sturm = R("Sturm Graz", ("SK Sturm Graz",))
    gencler = R("Genclerbirligi", ("Gençlerbirliği",))
    konya = R("Konyaspor", ("Konyaspor" ,))
    toulouse = R("Toulouse", ("Toulouse FC",))
    ucl = resolve_ucl_league(leagues)
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)

    lid = lyon["team_id"]; fid = fener["team_id"]
    lyon_dom = latest_comp_for_team(rows, lid, ucl); fener_dom = latest_comp_for_team(rows, fid, ucl)
    target = {"date": TARGET_DAY, "game_id":"WEB_LYON_FENER_20260826", "competition_id":ucl, "home_team":lid, "away_team":fid, "home_goals":0, "away_goals":0, "home_xg":0.0, "away_xg":0.0}

    raw0 = st.pred(target); pred0 = predict(k1,k3,draw_state,target,raw0)

    def row(dt,gid,comp,h,a,hg,ag,hx=0.0,ax=0.0):
        return {"date":dt,"game_id":gid,"competition_id":comp,"home_team":h,"away_team":a,"home_goals":hg,"away_goals":ag,"home_xg":hx,"away_xg":ax}

    updates = [
        {"label":"Fenerbahce 1-0 Gornik Zabrze, UCL Q2 first leg", "include_xg":False, "row":row("2026-07-21","WEB_FEN_GOR_1",ucl,fid,gornik["team_id"],1,0), "evidence":"UEFA official score; xG omitted"},
        {"label":"Gornik Zabrze 1-1 Fenerbahce, UCL Q2 second leg", "include_xg":True, "row":row("2026-07-29","WEB_GOR_FEN_2",ucl,gornik["team_id"],fid,1,1,0.60,2.66), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Sparta Prague 2-1 Lyon, UCL Q3 first leg", "include_xg":False, "row":row("2026-08-04","WEB_SPA_LYO_1",ucl,sparta["team_id"],lid,2,1), "evidence":"UEFA official score; xG omitted"},
        {"label":"Fenerbahce 2-0 Sturm Graz, UCL Q3 first leg", "include_xg":True, "row":row("2026-08-05","WEB_FEN_STU_1",ucl,fid,sturm["team_id"],2,0,1.37,0.16), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Lyon 3-0 Sparta Prague, UCL Q3 second leg", "include_xg":True, "row":row("2026-08-11","WEB_LYO_SPA_2",ucl,lid,sparta["team_id"],3,0,3.32,0.55), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Sturm Graz 0-1 Fenerbahce, UCL Q3 second leg", "include_xg":True, "row":row("2026-08-11","WEB_STU_FEN_2",ucl,sturm["team_id"],fid,0,1,1.35,2.17), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Genclerbirligi 2-1 Fenerbahce, Super Lig", "include_xg":True, "row":row("2026-08-15","WEB_GEN_FEN",fener_dom,gencler["team_id"],fid,2,1,1.19,2.90), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Fenerbahce 1-1 Lyon, UCL playoff first leg", "include_xg":True, "row":row("2026-08-18","WEB_FEN_LYO_1",ucl,fid,lid,1,1,0.42,0.73), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Toulouse 0-2 Lyon, Ligue 1", "include_xg":True, "row":row("2026-08-22","WEB_TOU_LYO",lyon_dom,toulouse["team_id"],lid,0,2,2.77,0.70), "evidence":"FotMob/Opta post-match xG"},
        {"label":"Fenerbahce 4-2 Konyaspor, Super Lig", "include_xg":True, "row":row("2026-08-22","WEB_FEN_KON",fener_dom,fid,konya["team_id"],4,2,2.36,2.15), "evidence":"FotMob/Opta post-match xG"},
    ]
    for u in updates:
        goal_update(st,u["row"],u["include_xg"]); draw_state.update(u["row"])

    raw1 = st.pred(target); pred1 = predict(k1,k3,draw_state,target,raw1)
    out = {
        "status":"COMPLETE",
        "target":{"display":"Lyon vs Fenerbahce","uefa_day":TARGET_DAY,"beijing_time":"2026-08-27 03:00","competition_id":ucl,"home_team_id":lid,"away_team_id":fid},
        "governance":{"strict_prior_snapshot":True,"odds_used":False,"market_prices_used":False,"manual_probability_adjustment":False,"post_match_target_data_used":False,"same_day_leakage":False,"injury_or_lineup_manual_adjustment":False,"R15_gate_threshold":DRAW_GATE},
        "models":{"K1":"R9b retained baseline","K3":"R14 compact 9-feature challenger; first fresh confirmation winner by 1/81","R15_gate":"locked validation-selected draw gate"},
        "team_resolution":{"lyon":lyon,"fenerbahce":fener,"lyon_domestic_comp":lyon_dom,"fener_domestic_comp":fener_dom},
        "snapshot_only":{"prediction":pred0,"raw":raw0,"score_peaks":score_peaks(raw0)},
        "pre_match_updated":{"prediction":pred1,"raw":raw1,"score_peaks":score_peaks(raw1)},
        "pre_match_updates":updates,
        "external_context_not_model_inputs":["Lyon expected near first-choice XI; Khalis Merah unavailable per Ligue1 preview","Fenerbahce expected strong XI; lineup/injury reports were not converted into probabilities","No odds or market prices used"],
    }
    OUT.mkdir(parents=True,exist_ok=True)
    p=OUT/"lyon_fenerbahce_20260826.json"; p.write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__ == "__main__":
    main()
