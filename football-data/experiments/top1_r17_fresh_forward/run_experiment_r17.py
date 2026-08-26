#!/usr/bin/env python3
from __future__ import annotations

import difflib
import gzip
import json
import math
import sys
import unicodedata
import urllib.request
import zlib
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R14_DIR = HERE.parent / "top1_r14_compact_draw"
R16_DIR = HERE.parent / "top1_r16_global_elo"
sys.path.insert(0, str(R14_DIR)); sys.path.insert(0, str(R16_DIR))
import run_experiment_r14 as r14  # noqa: E402
import run_experiment_r16 as r16  # noqa: E402

r12 = r14.r12; r9 = r14.r9
START = "2026-07-05"
END = "2026-08-25"
SEASON = 2026
UNDERSTAT_LEAGUES = ("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1", "RFPL")
DRAW_GATE = 0.08
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    for x in ("football club", " fc", "fc ", " afc", "cf ", " cf", "&", ".", "-", "'", "/"):
        s = s.replace(x, " ")
    s = " ".join(s.split())
    aliases = {
        "wolverhampton wanderers": "wolves", "brighton hove albion": "brighton",
        "tottenham hotspur": "tottenham", "bayern munchen": "bayern munich",
        "borussia monchengladbach": "borussia m gladbach", "internazionale": "inter",
        "olympique marseille": "marseille", "olympique de marseille": "marseille",
        "olympique lyonnais": "lyon", "paris saint germain": "paris saint germain",
        "koln": "cologne", "1 koln": "cologne",
    }
    return aliases.get(s, s)


def ajax(league):
    url = f"https://understat.com/getLeagueData/{league}/{SEASON}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "football3-r17-fresh-forward/1.0",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Encoding": "identity",
        "Referer": f"https://understat.com/league/{league}/{SEASON}",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read(); enc = (resp.headers.get("Content-Encoding") or "").lower()
    if raw[:2] == b"\x1f\x8b" or "gzip" in enc: raw = gzip.decompress(raw)
    elif "deflate" in enc: raw = zlib.decompress(raw)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or "dates" not in payload: raise RuntimeError(f"bad Understat payload {league}")
    d = payload["dates"]
    return list(d.values()) if isinstance(d, dict) else list(d)


def text_cols(df):
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype == object]


def league_id(leagues, key):
    spec = {
        "EPL": ("england", ("premier league",)), "La_liga": ("spain", ("la liga", "laliga")),
        "Bundesliga": ("germany", ("bundesliga",)), "Serie_A": ("italy", ("serie a",)),
        "Ligue_1": ("france", ("ligue 1",)), "RFPL": ("russia", ("premier league",)),
    }[key]
    cols = text_cols(leagues); hits=[]
    for row in leagues.itertuples(index=False):
        d=row._asdict(); blob=" | ".join(str(d.get(c) or "") for c in cols).lower()
        if spec[0] in blob and any(k in blob for k in spec[1]): hits.append(str(int(d["id"])))
    if len(set(hits)) != 1: raise RuntimeError(f"league map ambiguous {key}: {hits}")
    return hits[0]


def team_index(teams, allowed):
    cols = [c for c in text_cols(teams) if c in {"name", "fd_name"} or "name" in c.lower()]
    idx=[]
    for row in teams.itertuples(index=False):
        d=row._asdict(); tid=str(int(d["id"]))
        if allowed and tid not in allowed: continue
        names=[]
        for c in cols:
            v=d.get(c)
            if v is not None and str(v).lower() != "nan": names.append(norm(v))
        if names: idx.append((tid, sorted(set(names))))
    return idx


def resolve_team(title, primary, fallback):
    q=norm(title)
    def score(idx):
        out=[]
        for tid,names in idx:
            best=0.0
            for n in names:
                if q == n: best=max(best,1.0)
                elif q in n or n in q: best=max(best,0.88*min(len(q),len(n))/max(len(q),len(n))+0.10)
                best=max(best,difflib.SequenceMatcher(None,q,n).ratio())
            out.append((best,tid,names))
        out.sort(reverse=True); return out
    cand=score(primary)
    if not cand or cand[0][0] < .72: cand=score(fallback)
    if not cand or cand[0][0] < .72: return None, cand[:3]
    if len(cand)>1 and cand[0][0] < .98 and cand[0][0]-cand[1][0] < .04: return None, cand[:3]
    return cand[0][1], cand[:3]


def goal_only_base_update(st,row,p):
    d=date.fromisoformat(row["date"]); c=row["competition_id"]; h=row["home_team"]; a=row["away_team"]
    hg=int(row["home_goals"]); ag=int(row["away_goals"]); hs=st.v[(c,h,"H")]; av=st.v[(c,a,"A")]
    hs.n+=1; hs.gf+=hg; hs.ga+=ag; av.n+=1; av.gf+=ag; av.ga+=hg
    st.cn[c]+=1; st.ch[c]+=hg; st.ca[c]+=ag
    H=st.touch(h,d); A=st.touch(a,d)
    eh=r9.clamp((hg-p["latent_h"])/(1+p["latent_h"]),-2,2); ea=r9.clamp((ag-p["latent_a"])/(1+p["latent_a"]),-2,2)
    H.attack=r9.clamp(H.attack+r9.LR*eh,-1.2,1.2); A.defence=r9.clamp(A.defence+r9.LR*eh,-1.2,1.2)
    A.attack=r9.clamp(A.attack+r9.LR*ea,-1.2,1.2); H.defence=r9.clamp(H.defence+r9.LR*ea,-1.2,1.2)
    H.matches+=1; A.matches+=1


def goal_only_strength_update(st,row):
    h,a=row["home_team"],row["away_team"]; hg,ag=int(row["home_goals"]),int(row["away_goals"])
    rh,ra=st.elo[h],st.elo[a]; exp=1/(1+10**((ra-(rh+60))/400)); s=1 if hg>ag else .5 if hg==ag else 0
    de=20*(s-exp); st.elo[h]=rh+de; st.elo[a]=ra-de; st.n[h]+=1; st.n[a]+=1
    st.gf[h]+=hg; st.ga[h]+=ag; st.gf[a]+=ag; st.ga[a]+=hg


def probs(model,X):
    pr=model.predict_proba(X); classes=list(model[-1].classes_); out=[]
    for row in pr:
        v=np.zeros(3)
        for c,p in zip(classes,row): v[int(c)]=p
        out.append(r9.decorate(v))
    return out


def metric_decision(rows,key):
    n=len(rows); hits=sum(r[key]==r["y"] for r in rows); picks=[0,0,0]; hh=[0,0,0]
    for r in rows: picks[r[key]]+=1; hh[r[key]]+=int(r[key]==r["y"])
    return {"count":n,"hits":hits,"top1_accuracy":hits/n,"top1_picks":{"home":picks[0],"draw":picks[1],"away":picks[2]},"top1_hits":{"home":hh[0],"draw":hh[1],"away":hh[2]}}


def run():
    DATA.mkdir(parents=True,exist_ok=True); r12.freeze_gate(); base_rows=r9.load()
    locked=json.loads((HERE.parent/"top1_r15_draw_gate/results/summary_r15.json").read_text())
    if float(locked["selected_threshold"]) != DRAW_GATE: raise RuntimeError("R15 gate lock mismatch")

    # Rebuild exact historical states and training records before any fresh-label retrieval.
    bs=r9.S(); ds=r12.DrawState(); ss=r16.StrengthState(); hist=[]; by=defaultdict(list)
    for x in base_rows: by[x["date"]].append(x)
    for day in sorted(by):
        pending=[]
        for x in sorted(by[day],key=lambda z:z["game_id"]):
            raw=bs.pred(x); df=ds.features(x,raw); sf=ss.features(x); hist.append({"date":day,"y":r9.actual(x),"raw":raw,"df":df,"sf":sf}); pending.append((x,raw))
        for x,raw in pending: bs.update(x,raw); ds.update(x); ss.update(x)
    b1=r9.boundary(hist,r9.TARGET_BURN); b2=r9.boundary(hist,b1+r9.TARGET_TRAIN); train=hist[b1:b2]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[r["y"] for r in train]
    def mdl(): return make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    k1=mdl(); k3=mdl(); k4=mdl()
    k1.fit([r9.feat_k1(r["raw"]) for r in train],y)
    k3.fit([r9.feat_k1(r["raw"])+r14.compact(r["df"]) for r in train],y)
    k4.fit([r9.feat_k1(r["raw"])+r["sf"] for r in train],y)

    # Metadata for deterministic team/competition binding.
    tp=HERE/"_teams.parquet"; lp=HERE/"_leagues.parquet"
    r9.download(f"{HF}/teams.parquet?download=true",tp); r9.download(f"{HF}/leagues.parquet?download=true",lp)
    teams=pd.read_parquet(tp); leagues=pd.read_parquet(lp); tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)
    all_active={x["home_team"] for x in base_rows}|{x["away_team"] for x in base_rows}; fallback=team_index(teams,all_active)

    frozen=[]; mapping=[]; source_counts={}
    for lg in UNDERSTAT_LEAGUES:
        cid=league_id(leagues,lg); compteams={x["home_team"] for x in base_rows if x["competition_id"]==cid}|{x["away_team"] for x in base_rows if x["competition_id"]==cid}
        primary=team_index(teams,compteams); rows=ajax(lg); used=0
        for z in rows:
            dt=str(z.get("datetime") or "")[:10]; goals=z.get("goals") or {}; xg=z.get("xG") or {}
            if not (START <= dt <= END) or goals.get("h") is None or goals.get("a") is None: continue
            ht=str((z.get("h") or {}).get("title") or ""); at=str((z.get("a") or {}).get("title") or "")
            hid,hc=resolve_team(ht,primary,fallback); aid,ac=resolve_team(at,primary,fallback)
            mapping.append({"league":lg,"understat_home":ht,"home_team_id":hid,"home_candidates":hc,"understat_away":at,"away_team_id":aid,"away_candidates":ac})
            if hid is None or aid is None: continue
            frozen.append({"date":dt,"game_id":f"UNDERSTAT_{lg}_{z.get('id')}","competition_id":cid,"home_team":hid,"away_team":aid,"home_goals":int(goals["h"]),"away_goals":int(goals["a"]),"audit_home_xg":float(xg.get("h") or 0),"audit_away_xg":float(xg.get("a") or 0),"league":lg})
            used+=1
        source_counts[lg]=used
    frozen.sort(key=lambda x:(x["date"],x["game_id"])); total_candidates=len(mapping); coverage=len(frozen)/total_candidates if total_candidates else 0
    if len(frozen)<30 or coverage<.90: raise RuntimeError(f"fresh cohort gate failed rows={len(frozen)} mapping={coverage:.3f}")
    (DATA/"fresh_cohort_r17.json").write_text(json.dumps({"start":START,"end":END,"rows":frozen,"mapping_audit":mapping,"mapped_coverage":coverage,"source_counts":source_counts},indent=2,ensure_ascii=False)+"\n")

    # True fresh forward: same-date predictions first; compatible historical xG state is intentionally not updated with Understat xG.
    fresh=[]; fby=defaultdict(list)
    for x in frozen: fby[x["date"]].append(x)
    for day in sorted(fby):
        pending=[]
        for x in sorted(fby[day],key=lambda z:z["game_id"]):
            row={**x,"home_xg":0.0,"away_xg":0.0}; raw=bs.pred(row); df=ds.features(row,raw); sf=ss.features(row)
            p1=probs(k1,[r9.feat_k1(raw)])[0]; p3=probs(k3,[r9.feat_k1(raw)+r14.compact(df)])[0]; p4=probs(k4,[r9.feat_k1(raw)+sf])[0]
            cold=min(raw["home_history"],raw["away_history"])<5; route=p4 if cold else p1
            edge=p3["p_draw"]-max(p3["p_home"],p3["p_away"])
            g1=1 if p1["top1"]!=1 and p3["top1"]==1 and edge>=DRAW_GATE else p1["top1"]
            c1=1 if route["top1"]!=1 and p3["top1"]==1 and edge>=DRAW_GATE else route["top1"]
            rec={"date":day,"game_id":x["game_id"],"league":x["league"],"y":r9.actual(row),"K1":p1,"K3":p3,"K4":p4,"ROUTE":route,"G1_top1":g1,"C1_top1":c1,"cold_start":cold}; fresh.append(rec); pending.append((row,raw))
        for row,raw in pending: goal_only_base_update(bs,row,raw); ds.update(row); goal_only_strength_update(ss,row)

    def pm(key): return r9.metrics(fresh,key)
    summary={
        "schema_version":"football3-top1-r17-fresh-forward","status":"COMPLETE","classification":"FRESH_FORWARD_CONFIRMATION","formal_weight":0,
        "governance":{"base_snapshot_last_date":"2026-07-04","fresh_start":START,"fresh_end":END,"fresh_rows":len(fresh),"mapped_coverage":coverage,"rules_committed_before_fresh_label_retrieval":True,"same_date_results_withheld":True,"fresh_understat_xg_used_for_audit_only":True,"fresh_understat_xg_used_as_model_feature":False,"compatible_historical_xg_state_frozen_after_base":True,"odds_used":False,"market_prices_used":False,"manual_match_adjustment":False,"hyperparameter_search_on_fresh":False,"formal_promotion_allowed_from_this_run":False},
        "locked_models":{"K1":"R9b baseline","K3":"R14 compact draw model","K4":"R16 global strength model","G1":f"R15 K1 draw gate threshold {DRAW_GATE}","ROUTE":"K4 only when competition-specific min history <5, else K1","C1":"ROUTE plus locked R15 draw gate"},
        "source_counts":source_counts,
        "K1":pm("K1"),"K3":pm("K3"),"K4":pm("K4"),"ROUTE":pm("ROUTE"),"G1":metric_decision(fresh,"G1_top1"),"C1":metric_decision(fresh,"C1_top1"),
        "cold_start_count":sum(r["cold_start"] for r in fresh),
    }
    OUT.mkdir(parents=True,exist_ok=True); (OUT/"summary_r17.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(summary,indent=2))


def verify():
    s=json.loads((OUT/"summary_r17.json").read_text()); g=s["governance"]
    assert g["base_snapshot_last_date"]=="2026-07-04" and g["fresh_start"]==START
    assert g["rules_committed_before_fresh_label_retrieval"] and g["same_date_results_withheld"]
    assert g["fresh_understat_xg_used_for_audit_only"] and not g["fresh_understat_xg_used_as_model_feature"]
    assert not g["odds_used"] and not g["market_prices_used"] and not g["hyperparameter_search_on_fresh"]
    assert g["fresh_rows"]>=30 and g["mapped_coverage"]>=.90
    print("R17_VERIFY_PASS")

if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in {"run","verify"}: raise SystemExit("usage: run_experiment_r17.py {run|verify}")
    {"run":run,"verify":verify}[sys.argv[1]]()
