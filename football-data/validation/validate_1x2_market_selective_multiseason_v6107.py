#!/usr/bin/env python3
"""Fixed-rule multi-season validation for market-first 1X2 hit rate.

No fitting is performed. Rules discovered in V6.10.3 are frozen and replayed on two
completed seasons per domain when legacy closing odds are available:
- calendar-year competitions: 2024 and 2025
- autumn/spring + UCL: 2024/25 and 2025/26

Primary frozen thresholds: 0.56, 0.58, 0.60. Full-coverage market favorite is also
reported. Legacy prices lack original quote timestamps and remain retrospective only.
"""
from __future__ import annotations
import csv,json,random,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/"validation";E=ROOT/"engine"
for p in(V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
from platform_core import canonical_team_name,load_aliases,load_json,parse_match_date,read_processed_matches
from diagnose_1x2_market_anchor_v697 import _devig,_pick_probs
OUT=ROOT/"manifests"/"v6_1x2_market_selective_multiseason_v6107_status.json"
FORMAL=ROOT/"manifests"/"formal_core_v460_status.json"
CAL={"SWE_Allsvenskan","NOR_Eliteserien","JPN_J1","KOR_KLeague1","BRA_SerieA","ARG_Primera","USA_MLS"}
TH=(0.56,0.58,0.60); PRI=(("PSCH","PSCD","PSCA","Pinnacle_closing"),("B365CH","B365CD","B365CA","Bet365_closing"),("AvgCH","AvgCD","AvgCA","Average_closing"))
def _seasons(cid):return("2024","2025") if cid in CAL else("2024/25","2025/26")
def _f(v):
 try:x=float(str(v).strip())
 except:return None
 return x if x>1 else None
def _odds(raw):
 for h,d,a,label in PRI:
  v=[_f(raw.get(h)),_f(raw.get(d)),_f(raw.get(a))]
  if all(x is not None for x in v):return _devig(*v),label
 return None
def _key(season,date,h,a):return(season,date,h,a)
def _actual(hg,ag):return"home" if hg>ag else"draw" if hg==ag else"away"
def _eval(rows,pmin=0.0):
 s=[]
 for r in rows:
  pick=_pick_probs(r["p"])
  if r["p"][pick]>=pmin:s.append((r,pick))
 hits=sum(1 for r,p in s if r["actual"]==p)
 return{"count":len(s),"hits":hits,"coverage":len(s)/len(rows) if rows else 0,"accuracy":hits/len(s) if s else None}
def _extract(cid):
 aliases=load_aliases(); wanted=set(_seasons(cid)); matches=read_processed_matches(cid); lookup={_key(m.season,m.date.isoformat(),m.home_team,m.away_team):_actual(m.home_goals,m.away_goals) for m in matches if m.season in wanted};out={};providers=Counter();d=ROOT/"processed"/cid
 if not d.exists():return [],{}
 for path in sorted(d.glob("*.csv")):
  with path.open("r",encoding="utf-8-sig",newline="") as f:
   for rr in csv.DictReader(f):
    raw={str(k):"" if v is None else str(v) for k,v in rr.items() if k};season=str(raw.get("season") or raw.get("Season") or "").strip()
    if season not in wanted or not raw.get("Date") or not raw.get("HomeTeam") or not raw.get("AwayTeam"):continue
    try:date=parse_match_date(raw["Date"],season).isoformat()
    except:continue
    h=canonical_team_name(cid,raw["HomeTeam"],aliases);a=canonical_team_name(cid,raw["AwayTeam"],aliases);key=_key(season,date,h,a);actual=lookup.get(key);o=_odds(raw)
    if actual is None or o is None or key in out:continue
    p,label=o;out[key]={"competition_id":cid,"season":season,"date":date,"home":h,"away":a,"actual":actual,"p":p,"provider":label};providers[label]+=1
 return list(out.values()),dict(providers)
def main():
 competitions=sorted((load_json(FORMAL).get("reports") or {}).keys());allrows=[];per={};providers=Counter()
 for cid in competitions:
  rows,pc=_extract(cid);allrows+=rows;providers.update(pc);per[cid]={}
  for season in _seasons(cid):
   sub=[r for r in rows if r["season"]==season];per[cid][season]={"available":len(sub),"full":_eval(sub,0.0),**{f"p{int(t*100)}":_eval(sub,t) for t in TH}}
 overall={"full":_eval(allrows,0.0),**{f"p{int(t*100)}":_eval(allrows,t) for t in TH}}
 byseason={}
 for bucket in ("older","newer"):
  rows=[]
  for cid in competitions:
   season=_seasons(cid)[0 if bucket=="older" else 1];rows += [r for r in allrows if r["competition_id"]==cid and r["season"]==season]
  byseason[bucket]={"count":len(rows),"full":_eval(rows,0.0),**{f"p{int(t*100)}":_eval(rows,t) for t in TH}}
 # deterministic independent 50-match samples per competition-season for full market favorite
 samples=[]
 for cid in competitions:
  for season in _seasons(cid):
   sub=[r for r in allrows if r["competition_id"]==cid and r["season"]==season]
   if not sub:continue
   rng=random.Random(f"{cid}|{season}|20260724");ss=rng.sample(sub,min(50,len(sub)));e=_eval(ss,0.0);samples.append({"competition_id":cid,"season":season,**e})
 payload={"schema_version":"V6.10.7-market-selective-multiseason-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","fixed_thresholds":list(TH),"overall":overall,"season_buckets":byseason,"by_competition_season":per,"full_market_random_50_samples":samples,"provider_counts":dict(providers),"governance":{"research_only":True,"no_threshold_tuning_on_these_seasons":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps({"overall":overall,"season_buckets":byseason,"sample_count":len(samples)},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
