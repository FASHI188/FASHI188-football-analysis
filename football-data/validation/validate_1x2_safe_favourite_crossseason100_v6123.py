#!/usr/bin/env python3
"""V6.12.3 fixed-rule cross-season 100-match diagnostic.

No threshold selection. Evaluate pre-specified p>=0.58 / p>=0.60 gates with and without
late-season safe-midtable favourite exclusion on the final 100 matches of 2024/25 and
2025/26. Standings use only prior calendar-date results; same-day results are batch-updated.
Research only; no formal probability/weight change.
"""
from __future__ import annotations
import csv,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from platform_core import canonical_team_name,load_aliases,parse_match_date

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"v6_1x2_safe_favourite_crossseason100_v6123_status.json"
COMPS=["ENG_PremierLeague","GER_Bundesliga","ITA_SerieA","FRA_Ligue1","ESP_LaLiga"]
SEASONS=("2024/25","2025/26")
N={"ENG_PremierLeague":20,"GER_Bundesliga":18,"ITA_SerieA":20,"FRA_Ligue1":18,"ESP_LaLiga":20}
TOTAL={k:2*(v-1) for k,v in N.items()}

def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 and math.isfinite(x) else None

def devig(a,b,c):
    q=[1/a,1/b,1/c];s=sum(q);return tuple(x/s for x in q)

def odds(r):
    for cols in (("PSH","PSD","PSA"),("B365H","B365D","B365A"),("AvgH","AvgD","AvgA")):
        x=[f(r.get(c)) for c in cols]
        if all(v is not None for v in x):return devig(*x)
    return None

def load(season):
    aliases=load_aliases();out={}
    for cid in COMPS:
        d=ROOT/"processed"/cid
        if not d.exists():continue
        for path in sorted(d.glob("*.csv")):
            with path.open("r",encoding="utf-8-sig",newline="") as fh:
                for r0 in csv.DictReader(fh):
                    r={str(k):"" if v is None else str(v) for k,v in r0.items() if k}
                    if str(r.get("season") or r.get("Season") or "").strip()!=season:continue
                    if not r.get("HomeTeam") or not r.get("AwayTeam") or not r.get("Date"):continue
                    try:hg=int(float(r.get("FTHG","")));ag=int(float(r.get("FTAG","")))
                    except:continue
                    p=odds(r)
                    if p is None:continue
                    try:dt=parse_match_date(r["Date"],season)
                    except:continue
                    h=canonical_team_name(cid,r["HomeTeam"],aliases);a=canonical_team_name(cid,r["AwayTeam"],aliases)
                    act="home" if hg>ag else "away" if ag>hg else "draw"
                    out[(cid,dt.date().isoformat(),h,a)]={"competition_id":cid,"date":dt.date().isoformat(),"home":h,"away":a,"hg":hg,"ag":ag,"actual":act,"p":p}
    return sorted(out.values(),key=lambda r:(r["date"],r["competition_id"],r["home"],r["away"]))

def enrich(rows):
    st=defaultdict(lambda:defaultdict(lambda:{"played":0,"pts":0,"gf":0,"ga":0}))
    for r in rows:_=st[r["competition_id"]][r["home"]];_=st[r["competition_id"]][r["away"]]
    out=[];i=0
    while i<len(rows):
        date=rows[i]["date"];j=i
        while j<len(rows) and rows[j]["date"]==date:j+=1
        group=rows[i:j];ranks={}
        for cid in {x["competition_id"] for x in group}:
            teams=list(st[cid]);ordered=sorted(teams,key=lambda t:(st[cid][t]["pts"],st[cid][t]["gf"]-st[cid][t]["ga"],st[cid][t]["gf"],t),reverse=True);ranks[cid]={t:k+1 for k,t in enumerate(ordered)}
        for r in group:
            cid=r["competition_id"];n=N[cid];hr=ranks[cid][r["home"]];ar=ranks[cid][r["away"]]
            hl=TOTAL[cid]-st[cid][r["home"]]["played"];al=TOTAL[cid]-st[cid][r["away"]]["played"]
            late=hl<=6 and al<=6
            hu=late and (hr<=6 or hr>n-6);au=late and (ar<=6 or ar>n-6);hs=late and not hu;as_=late and not au
            pick=max(range(3),key=lambda k:r["p"][k]);fav="home" if pick==0 else "away" if pick==2 else "draw"
            fs=hs if fav=="home" else as_ if fav=="away" else False
            ou=au if fav=="home" else hu if fav=="away" else False
            out.append({**r,"maxp":max(r["p"]),"fav_safe":fs,"opp_urgent":ou,"conflict":bool(fs and ou)})
        for r in group:
            cid=r["competition_id"];h=st[cid][r["home"]];a=st[cid][r["away"]];h["played"]+=1;a["played"]+=1;h["gf"]+=r["hg"];h["ga"]+=r["ag"];a["gf"]+=r["ag"];a["ga"]+=r["hg"]
            if r["hg"]>r["ag"]:h["pts"]+=3
            elif r["hg"]<r["ag"]:a["pts"]+=3
            else:h["pts"]+=1;a["pts"]+=1
        i=j
    return out

def correct(r):return ("home","draw","away")[max(range(3),key=lambda k:r["p"][k])]==r["actual"]
def stats(rows,gate):
    s=[r for r in rows if gate(r)];hits=sum(correct(r) for r in s);return {"count":len(s),"hits":hits,"accuracy":hits/len(s) if s else None}

def season_report(season):
    rows=enrich(load(season));test=rows[-100:]
    rep={"all":stats(test,lambda r:True),"safe_favourite":stats(test,lambda r:r["fav_safe"]),"safe_favourite_vs_urgent":stats(test,lambda r:r["conflict"])}
    for p in (0.58,0.60):
        rep[f"p_ge_{p:.2f}"]=stats(test,lambda r,p=p:r["maxp"]>=p)
        rep[f"p_ge_{p:.2f}_exclude_safe_favourite"]=stats(test,lambda r,p=p:r["maxp"]>=p and not r["fav_safe"])
        rep[f"p_ge_{p:.2f}_exclude_safe_vs_urgent"]=stats(test,lambda r,p=p:r["maxp"]>=p and not r["conflict"])
    return {"rows":len(rows),"test_first":test[0]["date"],"test_last":test[-1]["date"],"metrics":rep}

def main():
    reports={s:season_report(s) for s in SEASONS}
    payload={"schema_version":"V6.12.3-safe-favourite-crossseason100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","classification":"RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_ODDS_TIMESTAMP","governance":{"fixed_rules_no_test_selection":True,"standings_prior_dates_only":True,"same_day_batch_update":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},"reports":reports}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(reports,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
