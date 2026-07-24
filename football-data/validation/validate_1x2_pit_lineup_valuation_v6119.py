#!/usr/bin/env python3
"""V6.11.9 PIT expected-lineup valuation incremental 1X2 test.

Adds strictly-prior Transfermarkt player valuations to the already-audited V6.11.7c
expected-lineup experiment. 2025/26 remains untouched for model/hyperparameter
selection. Target-match actual XI valuation is evaluated only as an oracle upper
bound and never enters operational features.
"""
from __future__ import annotations

import csv, gzip, io, json, math, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

import validate_1x2_pit_lineup_increment_v6117c as fixed
b = fixed.base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_1x2_pit_lineup_valuation_v6119_status.json"
VALUATIONS_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/player_valuations.csv.gz"


def pid(x: str) -> str:
    return str(x).split(":")[-1].strip()


def load_valuations():
    req=urllib.request.Request(VALUATIONS_URL,headers={"User-Agent":"football-analysis-research/1.0"})
    with urllib.request.urlopen(req,timeout=120) as resp: raw=resp.read()
    text=gzip.decompress(raw).decode("utf-8-sig",errors="replace")
    hist=defaultdict(list); rows=0
    for r in csv.DictReader(io.StringIO(text)):
        rows+=1
        p=pid(r.get("player_id") or ""); d=str(r.get("date") or "")[:10]
        if not p or not d: continue
        try: dt=datetime.fromisoformat(d); v=float(r.get("market_value_in_eur") or 0)
        except Exception: continue
        if v>0: hist[p].append((dt,v))
    for p in hist: hist[p].sort(key=lambda z:z[0])
    return hist,{"url":VALUATIONS_URL,"compressed_bytes":len(raw),"rows":rows,"players":len(hist)}


def prior_value(hist,p,target):
    arr=hist.get(pid(p),())
    best=None
    for d,v in arr:
        if d>=target: break
        best=v
    return best


def value_features(xi, probs, recent_hist, vh, target):
    vals=[]; covered=0
    for p in xi:
        v=prior_value(vh,p,target)
        if v is not None: covered+=1; vals.append((p,v))
    vv=sorted((v for _,v in vals),reverse=True)
    total=sum(vv)
    top3=sum(vv[:3]); top5=sum(vv[:5])
    # Recent-player pool is formed only from earlier observed XIs.
    pool=set(p for _,x in recent_hist[-8:] for p in x)
    pool_vals=sorted((prior_value(vh,p,target) or 0.0 for p in pool),reverse=True)
    best11=sum(pool_vals[:11])
    last=set(recent_hist[-1][1]) if recent_hist else set()
    last_val=sum(prior_value(vh,p,target) or 0.0 for p in last)
    weighted=0.0; wden=0.0
    for p in xi:
        v=prior_value(vh,p,target)
        if v is not None:
            q=float(probs.get(p,0.0)) if probs is not None else 1.0
            weighted += v*q; wden += q
    return {
      "log_total":math.log1p(total),
      "log_top3":math.log1p(top3),
      "log_top5":math.log1p(top5),
      "coverage":covered/max(1,len(xi)),
      "pool_loss":(best11-total)/best11 if best11>0 else 0.0,
      "vs_last":(total-last_val)/last_val if last_val>0 else 0.0,
      "weighted_mean_log":math.log1p(weighted/wden) if wden>0 else 0.0,
    }


def build_value_map(matches,vh):
    lineups={cid:b._load_lineups(cid) for cid in b.COMPETITIONS}
    th=defaultdict(list); out={}
    for r in matches:
        cid,season,date=r['competition_id'],r['season'],r['date']
        hk=(season,date,r['home']); ak=(season,date,r['away'])
        ha=lineups[cid].get(hk); aa=lineups[cid].get(ak)
        hkey=(cid,season,r['home']); akey=(cid,season,r['away'])
        target=datetime.fromisoformat(date[:10])
        if ha and aa:
            hp=b._predicted_xi(th[hkey]); ap=b._predicted_xi(th[akey])
            if hp and ap:
                hxi,hprob=hp; axi,aprob=ap
                h=value_features(hxi,hprob,th[hkey],vh,target)
                a=value_features(axi,aprob,th[akey],vh,target)
                # Oracle uses actual target XI only as an upper-bound diagnostic.
                ho=value_features(ha['starters'],None,th[hkey],vh,target)
                ao=value_features(aa['starters'],None,th[akey],vh,target)
                out[(cid,season,date,r['home'],r['away'])]=(h,a,ho,ao)
            th[hkey].append((date,ha['starters'])); th[akey].append((date,aa['starters']))
    return out


def append_pair(basefeat,h,a):
    f=list(basefeat)
    for k in ("log_total","log_top3","log_top5","coverage","pool_loss","vs_last","weighted_mean_log"):
        f.extend([h[k],a[k],h[k]-a[k]])
    return f


def main():
    vh,source=load_valuations()
    matches=b._load_matches()
    rows=b._build_dataset(matches)
    vm=build_value_map(matches,vh)
    enriched=[]
    for r in rows:
        key=(r['competition_id'],r['season'],r['date'],r['home'],r['away'])
        z=vm.get(key)
        if not z: continue
        h,a,ho,ao=z; x=dict(r)
        x['estimated_opening_valuation_features']=append_pair(r['estimated_opening_features'],h,a)
        x['estimated_closing_valuation_features']=append_pair(r['estimated_closing_features'],h,a)
        x['oracle_opening_valuation_features']=append_pair(r['oracle_opening_features'],ho,ao)
        x['valuation_pair_coverage']=(h['coverage']+a['coverage'])/2
        enriched.append(x)
    test=[r for r in enriched if r['season']==b.TEST_SEASON]
    if len(test)<1000: raise RuntimeError(f'insufficient enriched test rows: {len(test)}')
    opening_market=b._acc(test,lambda r:b._market_pick(r['opening']))
    closing_market=b._acc(test,lambda r:b._market_pick(r['closing']))
    eo=b._fit_eval(enriched,'estimated_opening_valuation_features')
    ec=b._fit_eval(enriched,'estimated_closing_valuation_features')
    oo=b._fit_eval(enriched,'oracle_opening_valuation_features')
    payload={
      'schema_version':'V6.11.9-pit-lineup-valuation-1x2-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      'status':'PASS','formal_current_version':'V5.0.1','market_data_classification':'RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP',
      'source':source,
      'governance':{'research_only':True,'valuation_strictly_before_match_date':True,'same_season_prior_lineups_only':True,'target_actual_xi_excluded_from_operational_features':True,'oracle_actual_xi_is_upper_bound_not_operational':True,'test_season_never_used_for_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},
      'sample':{'enriched_rows':len(enriched),'test_rows':len(test),'mean_test_valuation_coverage':float(np.mean([r['valuation_pair_coverage'] for r in test]))},
      'newer_season_test':{'opening_market':opening_market,'closing_market':closing_market,'opening_plus_expected_xi_value':eo,'closing_plus_expected_xi_value':ec,'opening_plus_oracle_actual_xi_value_upper_bound':oo}
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(payload['newer_season_test'],ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
