#!/usr/bin/env python3
"""V6.19.3 fixed-seed Fast100 total-goals diagnostic.

Samples exactly 100 PIT-eligible market-matched fixtures BEFORE model execution, then compares:
A formal direct P(T), B O/U2.5-only P(T), C old joint 1X2+OU IPF, D decoupled 1X2 + OU-total -> score reconciliation.
Research only; historical market rows have no original quote timestamps.
"""
from __future__ import annotations
import json,random,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import validate_architecture_order_v6190 as arch
import validate_joint_market_ipf_crossseason_v6164 as base
import validate_joint_market_ipf_v6163 as old_joint
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches
OUT=ROOT/'manifests'/'v6_total_goals_fast100_v6193_status.json'
SEED=20260725+6193;N=100;SEASONS=arch.SEASONS;COMPS=arch.COMPS

def topk_hit(p,actual,k):
    idx=sorted(range(8),key=lambda i:(-p[i],i))[:k];return int(actual in idx)
def summarize(rows,key):
    n=len(rows);rps=sum(r[f'{key}_rps'] for r in rows)/n;top1=sum(r[f'{key}_top1'] for r in rows)/n;top2=sum(r[f'{key}_top2'] for r in rows)/n
    mae=sum(abs(r[f'{key}_mode']-r['actual_total_bucket']) for r in rows)/n
    return {'count':n,'top1':top1,'top2':top2,'rps':rps,'mode_mae':mae,'mode_counts':dict(Counter(str(r[f'{key}_mode']) for r in rows))}

def main():
    cfg=load_config();warmc=int(cfg['validation']['warmup_competition_matches']);warmt=int(cfg['validation']['warmup_team_matches'])
    contexts={};candidates=[]
    for s in SEASONS:
      for cid in COMPS:
        params=ou.params_by_season(cid).get(s)
        if not params:continue
        lookup=base.market_lookup(cid,s);matches=[m for m in read_processed_matches(cid) if str(m.season)==s];matches.sort(key=lambda m:(m.date,m.home_team,m.away_team));contexts[(cid,s)]={'matches':matches,'lookup':lookup,'params':params,'temp':ou.calibrator(cid,s)}
        bydate=defaultdict(list)
        for m in matches:bydate[m.date].append(m)
        histn=0;hc=Counter();ac=Counter()
        for dt in sorted(bydate):
          day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
          for m in day:
            if histn>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and (m.date.isoformat(),m.home_team,m.away_team) in lookup:
              candidates.append((cid,s,m.date.isoformat(),m.home_team,m.away_team))
          for m in day:histn+=1;hc[m.home_team]+=1;ac[m.away_team]+=1
    if len(candidates)<N:raise RuntimeError(f'eligible candidates {len(candidates)} < {N}')
    selected=random.Random(SEED).sample(candidates,N);rows=[];fail=[]
    for cid,s,di,home,away in selected:
      ctx=contexts[(cid,s)];target=next(m for m in ctx['matches'] if m.date.isoformat()==di and m.home_team==home and m.away_team==away)
      hist=[m for m in ctx['matches'] if m.date<target.date]
      try:pred=predict_from_history(hist,cid,s,home,away,target.date,selected_parameters=ctx['params'],use_team_effects=True)
      except Exception as e:fail.append({'id':[cid,s,di,home,away],'stage':'formal','error':str(e)});continue
      prior=temperature_scale_matrix(pred['probabilities']['score_matrix'],ctx['temp']);marg=derive_score_marginals(prior);mk=ctx['lookup'][(di,home,away)]
      tdict=ou.project(marg['total_goals'],float(mk['p_over25']))
      if tdict is None:fail.append({'id':[cid,s,di,home,away],'stage':'ou_project'});continue
      t_ou=[float(tdict[k]) for k in ou.TOTAL_KEYS];t_formal=arch.total_vec(prior);one=[float(x) for x in mk['one_x_two']]
      old,oa=old_joint.ipf(prior,one,float(mk['p_over25']));new,na=arch.reconcile(prior,one,t_ou)
      if old is None or not oa.get('converged'):fail.append({'id':[cid,s,di,home,away],'stage':'old_ipf'});continue
      if new is None or not na.get('converged'):fail.append({'id':[cid,s,di,home,away],'stage':'new_reconcile'});continue
      t_old=arch.total_vec(old);t_new=arch.total_vec(new);actual=min(7,target.home_goals+target.away_goals)
      item={'competition_id':cid,'season':s,'date':di,'home':home,'away':away,'actual_total_bucket':actual}
      for key,p in [('formal',t_formal),('ou',t_ou),('old',t_old),('new',t_new)]:
        mode=max(range(8),key=lambda i:p[i]);item[f'{key}_mode']=mode;item[f'{key}_top1']=int(mode==actual);item[f'{key}_top2']=topk_hit(p,actual,2);item[f'{key}_rps']=arch.rps8(p,actual)
      item['ou_changed_mode']=int(item['ou_mode']!=item['formal_mode']);item['ou_flip_to_hit']=int(item['formal_top1']==0 and item['ou_top1']==1);item['ou_flip_to_miss']=int(item['formal_top1']==1 and item['ou_top1']==0)
      rows.append(item)
    if len(rows)!=N:raise RuntimeError(f'Fast100 did not produce exactly 100 valid rows: {len(rows)} failures={len(fail)}')
    summary={k:summarize(rows,k) for k in ('formal','ou','old','new')}
    actual_counts=dict(Counter(str(r['actual_total_bucket']) for r in rows));formal_mode=dict(Counter(str(r['formal_mode']) for r in rows));ou_mode=dict(Counter(str(r['ou_mode']) for r in rows))
    two_three={'actual_2_or_3':sum(r['actual_total_bucket'] in (2,3) for r in rows),'formal_mode_2_or_3':sum(r['formal_mode'] in (2,3) for r in rows),'ou_mode_2_or_3':sum(r['ou_mode'] in (2,3) for r in rows),'formal_2v3_confusions':sum(r['actual_total_bucket'] in (2,3) and r['formal_mode'] in (2,3) and r['actual_total_bucket']!=r['formal_mode'] for r in rows),'ou_2v3_confusions':sum(r['actual_total_bucket'] in (2,3) and r['ou_mode'] in (2,3) and r['actual_total_bucket']!=r['ou_mode'] for r in rows)}
    payload={'schema_version':'V6.19.3-total-goals-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_FIXED_SEED_FAST100_RESEARCH','seed':SEED,'candidate_count':len(candidates),'sample_count':N,'design':{'sample_before_model_execution':True,'strict_daily_pit':True,'seasons':list(SEASONS),'competitions':list(COMPS),'arms':['formal direct P(T)','OU2.5-only total projection','old joint 1X2+OU IPF','decoupled 1X2 + OU-total reconciliation']},'summary':summary,'deltas_pp':{'ou_vs_formal_top1':(summary['ou']['top1']-summary['formal']['top1'])*100,'old_vs_formal_top1':(summary['old']['top1']-summary['formal']['top1'])*100,'new_vs_formal_top1':(summary['new']['top1']-summary['formal']['top1'])*100,'new_vs_old_top1':(summary['new']['top1']-summary['old']['top1'])*100},'diagnostic':{'actual_bucket_counts':actual_counts,'formal_mode_counts':formal_mode,'ou_mode_counts':ou_mode,'ou_changed_mode_count':sum(r['ou_changed_mode'] for r in rows),'ou_flip_to_hit':sum(r['ou_flip_to_hit'] for r in rows),'ou_flip_to_miss':sum(r['ou_flip_to_miss'] for r in rows),'two_three':two_three,'actual_tail_4plus':sum(r['actual_total_bucket']>=4 for r in rows),'formal_tail_mode_4plus':sum(r['formal_mode']>=4 for r in rows),'ou_tail_mode_4plus':sum(r['ou_mode']>=4 for r in rows)},'rows':rows,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'no_threshold_tuning':True,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'summary':summary,'deltas_pp':payload['deltas_pp'],'diagnostic':payload['diagnostic']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
