#!/usr/bin/env python3
"""V6.13.8 research-only Fast100: tactical formation continuity/stability.

All formation features use only same-season historical starting-lineup records strictly
before the target match. Target formation and target actual XI are never used as inputs.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_formation_stability_fast100_v6138_status.json'

def norm_form(v):
    s=str(v or '').strip().casefold()
    if not s:return None
    # Keep meaningful tactical labels but normalize whitespace.
    return ' '.join(s.split())

def build():
    matches=fixed.base._load_matches();lineups={cid:fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS};fh=defaultdict(list);out=[]
    for r in matches:
        cid=r['competition_id'];season=r['season'];ds=str(r['date'])[:10];hk=(cid,season,r['home']);ak=(cid,season,r['away'])
        if season=='2025/26' and len(fh[hk])>=3 and len(fh[ak])>=3:
            h3=fh[hk][-3:];a3=fh[ak][-3:];h5=fh[hk][-5:];a5=fh[ak][-5:]
            vals={'home_form_distinct3':len(set(h3)),'away_form_distinct3':len(set(a3)),'home_form_distinct5':len(set(h5)),'away_form_distinct5':len(set(a5)),'home_form_last_switch':int(h3[-1]!=h3[-2]),'away_form_last_switch':int(a3[-1]!=a3[-2])}
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            if fav=='home':fu3=vals['home_form_distinct3']>=2;fu5=vals['home_form_distinct5']>=3;fs=vals['home_form_last_switch'];du3=vals['away_form_distinct3']>=2
            elif fav=='away':fu3=vals['away_form_distinct3']>=2;fu5=vals['away_form_distinct5']>=3;fs=vals['away_form_last_switch'];du3=vals['home_form_distinct3']>=2
            else:fu3=fu5=fs=du3=False
            out.append({**r,'date':ds,'fav':fav,**vals,'fav_form_unstable3':bool(fu3),'fav_form_high_churn5':bool(fu5),'fav_form_last_switch':bool(fs),'dog_form_unstable3':bool(du3)})
        hi=lineups[cid].get((season,ds,r['home']));ai=lineups[cid].get((season,ds,r['away']))
        if hi:
            f=norm_form(hi.get('formation'))
            if f:fh[hk].append(f)
        if ai:
            f=norm_form(ai.get('formation'))
            if f:fh[ak].append(f)
    return out

def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def main():
    rows=build();affected=[r for r in rows if r['fav_form_unstable3'] or r['dog_form_unstable3']]
    if len(affected)<100:raise RuntimeError(f'insufficient formation-instability matches: {len(affected)}')
    test=affected[-100:]
    metrics={'all_affected':stat(test),'favorite_unstable3':stat(test,lambda r:r['fav_form_unstable3']),'favorite_high_churn5':stat(test,lambda r:r['fav_form_high_churn5']),'favorite_last_switch':stat(test,lambda r:r['fav_form_last_switch']),'underdog_unstable3':stat(test,lambda r:r['dog_form_unstable3']),'p_ge_0.58':stat(test,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav_unstable3':stat(test,lambda r:max(r['opening'])>=0.58 and not r['fav_form_unstable3']),'p_ge_0.58_exclude_fav_high_churn5':stat(test,lambda r:max(r['opening'])>=0.58 and not r['fav_form_high_churn5']),'p_ge_0.60':stat(test,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav_unstable3':stat(test,lambda r:max(r['opening'])>=0.60 and not r['fav_form_unstable3'])}
    payload={'schema_version':'V6.13.8-formation-stability-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_FORMATION_HISTORY','governance':{'same_season_prior_formations_only':True,'target_formation_excluded':True,'target_actual_xi_excluded':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'feature_rows':len(rows),'affected_rows':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':metrics}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['test'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
