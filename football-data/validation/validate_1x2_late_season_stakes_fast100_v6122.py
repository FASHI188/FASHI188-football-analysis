#!/usr/bin/env python3
"""V6.12.2 research-only 100-match rapid screen for late-season task/standings risk.

Standings are rebuilt strictly from matches on earlier calendar dates. All matches on the
same date receive features from the same pre-date table and are only applied afterward.
No website table position or postmatch target information is used.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from platform_core import canonical_team_name, load_aliases, parse_match_date

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"v6_1x2_late_season_stakes_fast100_v6122_status.json"
COMPS=["ENG_PremierLeague","GER_Bundesliga","ITA_SerieA","FRA_Ligue1","ESP_LaLiga"]
SEASON="2025/26"
LEAGUE_SIZE={"ENG_PremierLeague":20,"GER_Bundesliga":18,"ITA_SerieA":20,"FRA_Ligue1":18,"ESP_LaLiga":20}
TOTAL_MATCHES={k:2*(n-1) for k,n in LEAGUE_SIZE.items()}


def f(v):
    try:x=float(str(v).strip())
    except Exception:return None
    return x if x>1 and math.isfinite(x) else None

def devig(h,d,a):
    q=[1/h,1/d,1/a]; s=sum(q); return tuple(x/s for x in q)

def odds(raw):
    for cols in (("PSH","PSD","PSA"),("B365H","B365D","B365A"),("AvgH","AvgD","AvgA")):
        x=[f(raw.get(c)) for c in cols]
        if all(v is not None for v in x): return devig(*x)
    return None

def load_rows():
    aliases=load_aliases(); out={}
    for cid in COMPS:
        d=ROOT/"processed"/cid
        if not d.exists():continue
        for path in sorted(d.glob("*.csv")):
            with path.open("r",encoding="utf-8-sig",newline="") as fh:
                for raw0 in csv.DictReader(fh):
                    raw={str(k):"" if v is None else str(v) for k,v in raw0.items() if k}
                    season=str(raw.get("season") or raw.get("Season") or "").strip()
                    if season!=SEASON or not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):continue
                    try:hg=int(float(raw.get("FTHG",""))); ag=int(float(raw.get("FTAG","")))
                    except Exception:continue
                    p=odds(raw)
                    if p is None:continue
                    try:dt=parse_match_date(raw["Date"],season)
                    except Exception:continue
                    home=canonical_team_name(cid,raw["HomeTeam"],aliases); away=canonical_team_name(cid,raw["AwayTeam"],aliases)
                    actual="home" if hg>ag else "away" if ag>hg else "draw"
                    key=(cid,dt.date().isoformat(),home,away)
                    out[key]={"competition_id":cid,"date":dt.date().isoformat(),"home":home,"away":away,"hg":hg,"ag":ag,"actual":actual,"p":p}
    return sorted(out.values(),key=lambda r:(r["date"],r["competition_id"],r["home"],r["away"]))


def table_snapshot(state,cid):
    teams=list(state[cid])
    ranked=sorted(teams,key=lambda t:(state[cid][t]["pts"],state[cid][t]["gf"]-state[cid][t]["ga"],state[cid][t]["gf"],t),reverse=True)
    return {t:i+1 for i,t in enumerate(ranked)}


def enrich(rows):
    state=defaultdict(lambda:defaultdict(lambda:{"p":0,"pts":0,"gf":0,"ga":0}))
    # pre-register all teams so early tables have the full league size
    for r in rows:
        _=state[r["competition_id"]][r["home"]]; _=state[r["competition_id"]][r["away"]]
    out=[]; i=0
    while i<len(rows):
        date=rows[i]["date"]; j=i
        while j<len(rows) and rows[j]["date"]==date:j+=1
        group=rows[i:j]
        ranks={cid:table_snapshot(state,cid) for cid in {r["competition_id"] for r in group}}
        for r in group:
            cid=r["competition_id"]; n=LEAGUE_SIZE[cid]; tm=TOTAL_MATCHES[cid]
            hr=ranks[cid][r["home"]]; ar=ranks[cid][r["away"]]
            hp=state[cid][r["home"]]["p"]; ap=state[cid][r["away"]]["p"]
            hleft=tm-hp; aleft=tm-ap
            late=(hleft<=6 and aleft<=6)
            h_urgent=late and (hr<=6 or hr>n-6); a_urgent=late and (ar<=6 or ar>n-6)
            h_safe=late and not h_urgent; a_safe=late and not a_urgent
            pick=max(range(3),key=lambda k:r["p"][k])
            fav_side="home" if pick==0 else "away" if pick==2 else "draw"
            fav_safe=(h_safe if fav_side=="home" else a_safe if fav_side=="away" else False)
            fav_urgent=(h_urgent if fav_side=="home" else a_urgent if fav_side=="away" else False)
            opp_urgent=(a_urgent if fav_side=="home" else h_urgent if fav_side=="away" else False)
            opp_safe=(a_safe if fav_side=="home" else h_safe if fav_side=="away" else False)
            pp=sorted(r["p"],reverse=True); maxp=pp[0]
            out.append({**r,"home_rank":hr,"away_rank":ar,"home_left":hleft,"away_left":aleft,"late":late,
                        "home_urgent":h_urgent,"away_urgent":a_urgent,"home_safe":h_safe,"away_safe":a_safe,
                        "fav_side":fav_side,"fav_safe":fav_safe,"fav_urgent":fav_urgent,"opp_urgent":opp_urgent,"opp_safe":opp_safe,
                        "conflict_safe_fav_vs_urgent_opp":bool(fav_safe and opp_urgent),"maxp":maxp})
        # update after all same-date features fixed
        for r in group:
            cid=r["competition_id"]; h=state[cid][r["home"]]; a=state[cid][r["away"]]
            h["p"]+=1;a["p"]+=1;h["gf"]+=r["hg"];h["ga"]+=r["ag"];a["gf"]+=r["ag"];a["ga"]+=r["hg"]
            if r["hg"]>r["ag"]:h["pts"]+=3
            elif r["hg"]<r["ag"]:a["pts"]+=3
            else:h["pts"]+=1;a["pts"]+=1
        i=j
    return out


def correct(r):
    pred=("home","draw","away")[max(range(3),key=lambda k:r["p"][k])]
    return pred==r["actual"]

def stats(rows,gate):
    sel=[r for r in rows if gate(r)]; hits=sum(correct(r) for r in sel)
    return {"count":len(sel),"coverage":len(sel)/len(rows),"hits":hits,"accuracy":hits/len(sel) if sel else None}

def select(val,cands,min_count=40):
    board=[]
    for label,gate,params in cands:
        s=stats(val,gate)
        if s["count"]>=min_count:board.append((s["accuracy"],s["count"],label,gate,params,s))
    if not board:return None
    board.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True);return board[0]


def main():
    rows=enrich(load_rows())
    if len(rows)<300:raise RuntimeError(f"insufficient rows {len(rows)}")
    val=rows[-300:-100];test=rows[-100:]
    families={}
    basec=[]; conflict=[]; no_safe=[]; favurg=[]
    for p in (0.52,0.54,0.56,0.58,0.60,0.62):
        basec.append((f"p>={p:.2f}",lambda r,p=p:r["maxp"]>=p,{"p":p}))
        conflict.append((f"p>={p:.2f}&exclude_safe_fav_vs_urgent",lambda r,p=p:r["maxp"]>=p and not r["conflict_safe_fav_vs_urgent_opp"],{"p":p,"exclude_conflict":True}))
        no_safe.append((f"p>={p:.2f}&fav_not_safe_midtable",lambda r,p=p:r["maxp"]>=p and not r["fav_safe"],{"p":p,"fav_not_safe":True}))
        favurg.append((f"p>={p:.2f}&fav_urgent",lambda r,p=p:r["maxp"]>=p and r["fav_urgent"],{"p":p,"fav_urgent":True}))
    families["plain_probability"]=basec;families["exclude_safe_favourite_vs_urgent_opponent"]=conflict;families["exclude_safe_midtable_favourite"]=no_safe;families["urgent_favourite_only"]=favurg
    result={}
    for name,cands in families.items():
        ch=select(val,cands,40)
        if ch is None:result[name]={"status":"NO_VALID_RULE"};continue
        _,_,label,gate,params,vs=ch;result[name]={"selected_rule":label,"params":params,"validation":vs,"test":stats(test,gate)}
    diagnostics={
        "test_late_count":sum(r["late"] for r in test),
        "test_safe_fav_vs_urgent_opp":stats(test,lambda r:r["conflict_safe_fav_vs_urgent_opp"]),
        "test_favourite_urgent":stats(test,lambda r:r["fav_urgent"]),
        "test_favourite_safe":stats(test,lambda r:r["fav_safe"]),
    }
    payload={"schema_version":"V6.12.2-late-season-stakes-fast100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","classification":"RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_ODDS_TIMESTAMP",
             "governance":{"standings_strictly_prior_calendar_dates":True,"same_day_results_not_used_for_same_day_features":True,"validation_matches":200,"test_matches":100,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},
             "sample":{"rows":len(rows),"validation_first":val[0]["date"],"validation_last":val[-1]["date"],"test_first":test[0]["date"],"test_last":test[-1]["date"]},"selected_families":result,"diagnostics":diagnostics}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps({"families":result,"diag":diagnostics},indent=2));return 0

if __name__=="__main__":raise SystemExit(main())
