#!/usr/bin/env python3
import argparse, importlib.util, json, math, pathlib, sys
TOL=1e-12
class V326Error(RuntimeError): pass
def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path)); mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod
def write_json(path,obj): pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))
def apply_consensus(rows,bmap,shadow_pm,shadow_dg,q_by_h,d_by_h,horizons):
    pm={}; dg={}; blocked=passed=shadow_n=draw_n=0; targets={'H':0,'D':0,'A':0}; patterns={}
    for r in rows:
        fid=r['fixture_id']; b=list(map(float,bmap[fid])); sd=dict(shadow_dg[fid]); sh=bool(sd.get('v324_executed_switch',False)); shadow_n+=int(sh)
        hs=[]
        for h in horizons:
            elig=bool(d_by_h[h][fid].get('eligible',False)); hs.append(top1(q_by_h[h][fid]) if elig else None)
        key='/'.join('-' if x is None else 'HDA'[x] for x in hs); patterns[key]=patterns.get(key,0)+1
        bt=top1(b); unanimous=(all(x is not None for x in hs) and len(set(hs))==1 and hs[0]!=bt)
        if unanimous and hs[0]==1: draw_n+=1
        actual=bool(sh and unanimous)
        if actual:
            p=list(map(float,shadow_pm[fid])); passed+=1; targets['HDA'[top1(p)]]+=1
        else:
            p=b
            if sh: blocked+=1
        fd=dict(sd); fd['v326_horizon_targets']=hs; fd['v326_unanimous_direction']=unanimous; fd['v326_executed_switch']=actual; fd['v324_executed_switch']=actual
        if sh and not actual: fd['v324_projection_reason']='blocked_by_temporal_horizon_direction_disagreement'
        pm[fid]=p; dg[fid]=fd
    return pm,dg,{'shadow_executable_switch_n':shadow_n,'unanimous_consensus_switch_n':passed,'consensus_blocked_switch_n':blocked,'horizon_target_pattern_counts':patterns,'unanimous_draw_target_n':draw_n,'switch_target_counts':targets}
def predict_seasons(seasons,rows,bmap,bmats,snaps,core,c3,c6,v311,v322,v323,v324,fold_map=None,gates_key=None):
    baseparams=dict(c6['frozen_base_candidate']['frozen_t2_params']); horizons=tuple(map(float,c6['consensus']['training_half_life_seasons'])); allr=[]; pm={}; dg={}; recs=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]; te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322); qh={}; dh={}; fits={}
        for h in horizons:
            p=dict(baseparams); p['half_life']=h; q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,p,pref,v322); qh[h]=q; dh[h]=d; fits[str(h)]=fit
        h1=horizons[0]; shadow_pm,shadow_dg,shadow=v324.project_direction_map(te,bmap,qh[h1],dh[h1],v322,c6['frozen_base_candidate']['projection_epsilon'])
        p,pd,cs=apply_consensus(te,bmap,shadow_pm,shadow_dg,qh,dh,horizons)
        recs.append({'season':s,'fits':fits,'shadow_direction_proposal_n':shadow['direction_proposal_n'],**cs}); allr.extend(te); pm.update(p); dg.update(pd)
    base={r['fixture_id']:bmap[r['fixture_id']] for r in allr}; bm={r['fixture_id']:bmats[r['fixture_id']] for r in allr}; gates=c6[gates_key]
    ev,mm=v324.evaluate(allr,pm,base,bm,dg,fold_map,gates,v311,v322,require_folds=(gates_key=='hard_gates_2021_2022'))
    agg={'shadow_direction_proposal_n':sum(x['shadow_direction_proposal_n'] for x in recs),'shadow_executable_switch_n':sum(x['shadow_executable_switch_n'] for x in recs),'unanimous_consensus_switch_n':sum(x['unanimous_consensus_switch_n'] for x in recs),'consensus_blocked_switch_n':sum(x['consensus_blocked_switch_n'] for x in recs),'unanimous_draw_target_n':sum(x['unanimous_draw_target_n'] for x in recs),'switch_target_counts':{k:sum(x['switch_target_counts'][k] for x in recs) for k in 'HDA'}}
    pat={}
    for x in recs:
        for k,v in x['horizon_target_pattern_counts'].items(): pat[k]=pat.get(k,0)+v
    agg['horizon_target_pattern_counts']=pat; ev['v326_consensus']=agg
    return {'seasons':recs,'pooled':ev,'all_pass':ev['checks']['all_pass']},pm,dg,mm
def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c6=json.loads(a.contract.read_text()); c4=json.loads(a.v324_contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c6['status']!='FROZEN_BEFORE_V3_2_6_TARGET_SCORING': raise V326Error('V3.2.6 contract drift')
    if c4['status']!='FROZEN_BEFORE_V3_2_4_TARGET_SCORING' or c3['status']!='FROZEN_BEFORE_V3_2_3_TARGET_SCORING': raise V326Error('ancestor contract drift')
    if c6['frozen_base_candidate']['frozen_t2_params']!=c4['direction_generator']['frozen_t2_params']: raise V326Error('T2 drift')
    if c6['consensus']['training_half_life_seasons']!=c3['frozen_grid']['training_half_life_seasons']: raise V326Error('horizon grid drift')
    if c6['consensus']['threshold_grid']!='NONE' or not c6['consensus']['all_horizons_use_same_other_t2_parameters']: raise V326Error('consensus mechanics drift')
    v324=loadmod('v326_v324',a.v324); v323=loadmod('v326_v323',a.v323); v322=loadmod('v326_v322',a.v322); a.v32dev_module=loadmod('v326_v32dev',a.v32dev); core=loadmod('v326_core',a.core); v311=loadmod('v326_v311',a.v311); v31=loadmod('v326_v31',a.v31); usr=loadmod('v326_usr',a.usr1); v2=loadmod('v326_v2',a.v2); xg=loadmod('v326_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec); write_json(a.out/'pre2023_process_receipt.json',procrec); write_json(a.out/'pre2023_baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec}); write_json(a.out/'dynamic_state_selection_2019_pre2023.json',stateboard)
    sanity,_,_,_=predict_seasons((2019,2020),rows,bmap,bmats,snaps,core,c3,c6,v311,v322,v323,v324,None,'sanity_gates_2019_2020'); write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-2-6-final-v1','status':c6['terminal']['development_failure'],'reason':'fixed_temporal_horizon_consensus_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    rolling,_,_,_=predict_seasons((2021,2022),rows,bmap,bmats,snaps,core,c3,c6,v311,v322,v323,v324,fold_map,'hard_gates_2021_2022'); write_json(a.out/'rolling_2021_2022.json',rolling)
    if not rolling['all_pass']:
        final={'schema_version':'football3-v3-2-6-final-v1','status':c6['terminal']['development_failure'],'reason':'fixed_temporal_horizon_consensus_failed_2021_2022_hard_gates','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    freeze={'candidate':'V3_2_6_FIXED_TEMPORAL_HORIZON_CONSENSUS','consensus':c6['consensus'],'base_candidate':c6['frozen_base_candidate'],'rolling_2021_2022':rolling['pooled'],'frozen_before_2023':True,'source_max_season_seen_at_freeze':2022}; write_json(a.out/'candidate_freeze_before_2023.json',freeze)
    rows23,fold23,_,rowrec23,proc23,procrec23,bmap23,bmats23,baserec23,state_params23,snaps23,stateboard23=v322.stage(2023,a,c3,core,v311,v31,usr,v2,xg)
    if state_params23!=state_params: raise V326Error('state selection drift after 2023 reopen')
    diag,_,_,_=predict_seasons((2023,),rows23,bmap23,bmats23,snaps23,core,c3,c6,v311,v322,v323,v324,None,'hard_gates_2023_candidate_fixed'); write_json(a.out/'postfreeze_2023_row_receipt.json',rowrec23); write_json(a.out/'candidate_fixed_2023.json',diag)
    status=c6['terminal']['candidate_fixed_2023_success'] if diag['all_pass'] else c6['terminal']['candidate_fixed_2023_failure']
    final={'schema_version':'football3-v3-2-6-final-v1','status':status,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'candidate_fixed_2023':diag['pooled'],'source_max_season_loaded_after_freeze':2023,'2023_opened':True,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
