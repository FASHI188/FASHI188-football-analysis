#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import v6_draw_rank_attack_nb_v6512 as rich
import v6_draw_triage_decision_v6508 as tri
import v6_draw_residual_net_gain_v6507 as r7
OUT=ROOT/'manifests'/'v6_draw_rank_attack_nb_v6512_status.json'
FREEZE=ROOT/'manifests'/'v6_hierarchical_selector_forward_v6475_freeze.json'

def errx(r): return tri.extend_x(list(r['x'][:26]),r['competition_id'],r['pick'])

def main():
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
    train,valid,target,data=rich.build_records(freeze)
    et=[{**r,'x':errx(r)} for r in train]
    em=tri.fit_label(et,lambda r:r['actual']!=r['pick'])
    for rows in (valid,target):
        for r in rows:r['p_error_model']=r7.predict(em,errx(r))
    bacc=sum(r['actual']==r['pick'] for r in valid)/len(valid); berr=1-bacc; bdraw=sum(r['actual']=='draw' for r in valid)/len(valid)
    vc=[]
    for k in range(25,71):
        th=k/100;m=rich.eval_rule(valid,th,None);rem=[r for r in valid if r['p_error_model']>=th];n=len(rem)
        re=sum(r['actual']!=r['pick'] for r in rem)/n if n else 0;rd=sum(r['actual']=='draw' for r in rem)/n if n else 0
        m.update({'veto_threshold':th,'removed_error_rate':re,'removed_draw_rate':rd,'eligible':bool(n>=20 and m['retention_vs_base']>=.75 and m['accuracy']>bacc and re>berr and rd>bdraw)})
        vc.append(m)
    ve=[m for m in vc if m['eligible']]
    if not ve: raise RuntimeError('veto unavailable')
    cv=max(ve,key=lambda m:(m['accuracy'],m['retention_vs_base'],-m['veto_threshold']));veto=float(cv['veto_threshold'])
    model=rich.fit_nb(train)
    for rows in (valid,target):
        for r in rows:r['p_draw_nb']=rich.posterior(model,r)
    vv=rich.eval_rule(valid,veto,None);curve=[]
    for k in range(5,96):
        th=k/100;m=rich.eval_rule(valid,veto,th);m['draw_threshold']=th;m['eligible']=bool(m['draw_pick_count']>=20 and (m['draw_precision'] or 0)>=.50 and (m['accuracy'] or 0)>=.70 and m['executed_count']>vv['executed_count']);curve.append(m)
    ee=[m for m in curve if m['eligible']];chosen=max(ee,key=lambda m:(m['executed_count'],m['accuracy'],m['draw_precision'])) if ee else None
    dd=[m for m in curve if m['draw_pick_count']>=5];best=max(dd,key=lambda m:(m['draw_precision'] or 0,m['draw_pick_count'])) if dd else None
    dt=float(chosen['draw_threshold']) if chosen else None;tv=rich.eval_rule(target,veto,None);tr=rich.eval_rule(target,veto,dt) if dt is not None else tv
    x={'schema_version':'V6.51.2-rank-attack-draw-nb-r2','generated_at_utc':r7.now(),'formal_current_version':'V5.0.1','status':'PASS_RESEARCH_CHALLENGE' if chosen else 'REJECT_NO_VALIDATION_SAFE_RANK_ATTACK_DRAW_REENTRY','method':{'error_features':'exact V6.50.8','draw_features':'base plus pre-match rank and separated GF/GA','train':'pre-2024','threshold_selection':'2024 only','target_results_used_for_training_or_threshold':False},'data':{**data,'train_n':len(train),'validation_n':len(valid),'target_n':len(target)},'model':{'selected_features':model['selected_features']},'validation':{'chosen_veto':cv,'veto_only':vv,'chosen_draw':chosen,'best_draw_precision_diagnostic_min5':best},'target_2025':{'veto_only':tv,'with_rank_attack_draw_reentry':tr},'gates':{'target_accuracy_ge_70':bool((tr['accuracy'] or 0)>=.70),'target_draw_picks_positive':bool(tr['draw_pick_count']>0),'target_draw_precision_ge_50':bool((tr['draw_precision'] or 0)>=.50),'target_coverage_improved_vs_veto_only':bool(tr['executed_count']>tv['executed_count']),'formal_promotion_allowed':False},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'formal_probability_change':False,'formal_selector_threshold_change':False}}
    OUT.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':x['status'],'best':best,'chosen':chosen,'target':tr,'gates':x['gates']},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
