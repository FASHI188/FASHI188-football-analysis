#!/usr/bin/env python3
"""Generate strict historical V6.16.4 joint-IPF rows for selective audits."""
from __future__ import annotations
import json,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import validate_joint_market_ipf_crossseason_v6164 as cross
import validate_joint_market_ipf_v6163 as joint
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches
OUT=ROOT/'validation'/'cache'/'v6169_joint_ipf_selection_rows.json'
SEASONS=('2022/23','2023/24','2024/25','2025/26');COMPS=joint.COMPS;TOTAL_KEYS=joint.TOTAL_KEYS

def build(cid,season,cfg):
    look=cross.market_lookup(cid,season);params=ou.params_by_season(cid).get(season)
    if not params:return []
    ms=[m for m in read_processed_matches(cid) if str(m.season)==season];bd=defaultdict(list)
    for m in ms:bd[m.date].append(m)
    hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,season);out=[]
    wc=int(cfg['validation']['warmup_competition_matches']);wt=int(cfg['validation']['warmup_team_matches'])
    for dt in sorted(bd):
      for m in sorted(bd[dt],key=lambda z:(z.home_team,z.away_team)):
        mk=look.get((m.date.isoformat(),m.home_team,m.away_team))
        if len(hist)>=wc and hc[m.home_team]>=wt and ac[m.away_team]>=wt and mk:
            try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
            except Exception:p=None
            if p:
                prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);mat,a=joint.ipf(prior,mk['one_x_two'],float(mk['p_over25']))
                if mat is not None and a.get('converged'):
                    marg=derive_score_marginals(mat);tp=[float(marg['total_goals'][k]) for k in TOTAL_KEYS];tr=sorted(enumerate(tp),key=lambda x:(-x[1],x[0]));sr=sorted(mat,key=lambda c:(-float(c['probability']),int(c['home_goals']),int(c['away_goals'])));actual=(m.home_goals,m.away_goals);actual_t=min(7,sum(actual));one=marg['1x2']
                    out.append({'season':season,'date':m.date.isoformat(),'competition_id':cid,'total_p':tr[0][1],'total_gap':tr[0][1]-tr[1][1],'total_hit':int(tr[0][0]==actual_t),'score_p':float(sr[0]['probability']),'score_gap':float(sr[0]['probability'])-float(sr[1]['probability']),'score_hit':int((int(sr[0]['home_goals']),int(sr[0]['away_goals']))==actual),'result_pmax':max(float(one[k]) for k in ('home','draw','away'))})
        hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
    return out

def main():
    cfg=load_config();rows=[]
    for s in SEASONS:
      for cid in COMPS:rows+=build(cid,s,cfg)
    rows.sort(key=lambda r:(r['date'],r['competition_id']));payload={'schema_version':'V6.16.9-selection-rows-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','row_count':len(rows),'seasons':list(SEASONS),'rows':rows}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'row_count':len(rows),'by_season':{s:sum(r['season']==s for r in rows) for s in SEASONS}},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
