#!/usr/bin/env python3
import argparse, importlib.util, json, math, pathlib, sys
TOL=1e-12

class V327Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def top1(p):
    return max(range(3),key=lambda i:(float(p[i]),-i))

def is_pairwise_local_projection(pr, baseline_top1):
    if not bool(pr.get('executed',False)): return False
    bind=list(pr.get('binding_competitors',[]))
    return len(bind)==1 and int(bind[0])==int(baseline_top1)

def apply_geometry_safety(rows,bmap,qmap,dg,v322,v324,epsilon):
    pm={}; outdg={}
    proposals=shadow_exec=pairwise=blocked=draw_n=0
    pair_tvs=[]; multi_tvs=[]; targets={'H':0,'D':0,'A':0}
    for r in rows:
        fid=r['fixture_id']; b=list(map(float,bmap[fid])); q=list(map(float,qmap[fid])); d=dict(dg[fid])
        bt=top1(b); qt=top1(q); _,weak=v322.fav_weak(b)
        proposed=(bt!=qt); proposals+=int(proposed)
        shadow_p=b; pr={'executed':False,'reason':'not_eligible_or_no_direction_switch','target':qt,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
        if d.get('eligible',False) and proposed:
            shadow_p,pr=v324.minimum_boundary_projection(b,qt,weak,epsilon)
        sh=bool(pr.get('executed',False)); shadow_exec+=int(sh)
        local=is_pairwise_local_projection(pr,bt)
        if sh and local:
            p=list(map(float,shadow_p)); pairwise+=1; pair_tvs.append(float(pr['total_variation']))
            targets['HDA'[qt]]+=1; draw_n+=int(qt==1)
            reason='pairwise_local_projection'
        else:
            p=b
            if sh:
                blocked+=1; multi_tvs.append(float(pr['total_variation']))
                reason='blocked_multibinding_geometry'
            else:
                reason=pr.get('reason','not_executed')
        if abs(sum(p)-1.0)>1e-10 or any((not math.isfinite(x)) or x<=0 for x in p):
            raise V327Error('invalid probability')
        if p[weak] < b[weak]-TOL:
            raise V327Error('weak floor violated')
        if local:
            third=({0,1,2}-{bt,qt}).pop()
            if abs(float(p[third])-float(b[third]))>TOL:
                raise V327Error('third class changed under pairwise-local projection')
        fd=dict(d)
        fd.update({
            'v327_direction_proposed':proposed,
            'v327_shadow_v324_executed':sh,
            'v327_pairwise_local':local,
            'v327_executed_switch':bool(sh and local),
            'v327_geometry_reason':reason,
            'v327_projection_tv':float(pr.get('total_variation',0.0)),
            'v327_target':qt,
            'v324_executed_switch':bool(sh and local),
            'v324_projection_reason':reason,
            'v324_projection_tv':float(pr.get('total_variation',0.0)),
            'v324_target':qt,
        })
        pm[fid]=p; outdg[fid]=fd
    stats={
        'shadow_direction_proposal_n':proposals,
        'shadow_executable_switch_n':shadow_exec,
        'pairwise_local_switch_n':pairwise,
        'multibinding_blocked_switch_n':blocked,
        'pairwise_draw_target_n':draw_n,
        'switch_target_counts':targets,
        'mean_total_variation_pairwise':(sum(pair_tvs)/len(pair_tvs) if pair_tvs else 0.0),
        'max_total_variation_pairwise':(max(pair_tvs) if pair_tvs else 0.0),
        'mean_total_variation_multibinding':(sum(multi_tvs)/len(multi_tvs) if multi_tvs else 0.0),
        'max_total_variation_multibinding':(max(multi_tvs) if multi_tvs else 0.0),
    }
    return pm,outdg,stats

def predict_seasons(seasons,rows,bmap,bmats,snaps,core,c3,c7,v311,v322,v323,v324,fold_map=None,gates_key=None):
    params=dict(c7['frozen_base_candidate']['frozen_t2_params'])
    epsilon=float(c7['frozen_base_candidate']['projection_epsilon'])
    allr=[]; pm={}; dg={}; recs=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]
        te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322)
        q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322)
        p,pd,st=apply_geometry_safety(te,bmap,q,d,v322,v324,epsilon)
        recs.append({'season':s,'fit':fit,**st}); allr.extend(te); pm.update(p); dg.update(pd)
    base={r['fixture_id']:bmap[r['fixture_id']] for r in allr}
    bm={r['fixture_id']:bmats[r['fixture_id']] for r in allr}
    gates=c7[gates_key]
    ev,mm=v324.evaluate(allr,pm,base,bm,dg,fold_map,gates,v311,v322,require_folds=(gates_key=='hard_gates_2021_2022'))
    agg={}
    for k in ('shadow_direction_proposal_n','shadow_executable_switch_n','pairwise_local_switch_n','multibinding_blocked_switch_n','pairwise_draw_target_n'):
        agg[k]=sum(x[k] for x in recs)
    agg['switch_target_counts']={k:sum(x['switch_target_counts'][k] for x in recs) for k in 'HDA'}
    pden=agg['pairwise_local_switch_n']; mden=agg['multibinding_blocked_switch_n']
    agg['mean_total_variation_pairwise']=(sum(x['mean_total_variation_pairwise']*x['pairwise_local_switch_n'] for x in recs)/pden if pden else 0.0)
    agg['max_total_variation_pairwise']=max([x['max_total_variation_pairwise'] for x in recs] or [0.0])
    agg['mean_total_variation_multibinding']=(sum(x['mean_total_variation_multibinding']*x['multibinding_blocked_switch_n'] for x in recs)/mden if mden else 0.0)
    agg['max_total_variation_multibinding']=max([x['max_total_variation_multibinding'] for x in recs] or [0.0])
    ev['v327_geometry']=agg
    return {'seasons':recs,'pooled':ev,'all_pass':ev['checks']['all_pass']},pm,dg,mm

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c7=json.loads(a.contract.read_text()); c4=json.loads(a.v324_contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c7['status']!='FROZEN_BEFORE_V3_2_7_TARGET_SCORING': raise V327Error('V3.2.7 contract drift')
    if c4['status']!='FROZEN_BEFORE_V3_2_4_TARGET_SCORING' or c3['status']!='FROZEN_BEFORE_V3_2_3_TARGET_SCORING': raise V327Error('ancestor contract drift')
    if c7['frozen_base_candidate']['frozen_t2_params']!=c4['direction_generator']['frozen_t2_params']: raise V327Error('T2 drift')
    if float(c7['frozen_base_candidate']['projection_epsilon'])!=float(c4['projection']['epsilon']): raise V327Error('projection epsilon drift')
    g=c7['geometry_safety']
    if g['binding_competitor_cardinality_required']!=1 or not g['binding_competitor_must_equal_baseline_top1'] or not g['third_class_probability_must_remain_baseline']: raise V327Error('geometry rule drift')
    if g['numeric_risk_threshold']!='NONE' or g['threshold_grid']!='NONE' or g['result_feedback'] or g['temporal_consensus_gate']: raise V327Error('forbidden geometry tuning')
    v324=loadmod('v327_v324',a.v324); v323=loadmod('v327_v323',a.v323); v322=loadmod('v327_v322',a.v322)
    a.v32dev_module=loadmod('v327_v32dev',a.v32dev); core=loadmod('v327_core',a.core); v311=loadmod('v327_v311',a.v311)
    v31=loadmod('v327_v31',a.v31); usr=loadmod('v327_usr',a.usr1); v2=loadmod('v327_v2',a.v2); xg=loadmod('v327_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec); write_json(a.out/'pre2023_process_receipt.json',procrec)
    write_json(a.out/'pre2023_baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec})
    write_json(a.out/'dynamic_state_selection_2019_pre2023.json',stateboard)
    sanity,_,_,_=predict_seasons((2019,2020),rows,bmap,bmats,snaps,core,c3,c7,v311,v322,v323,v324,None,'sanity_gates_2019_2020')
    write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-2-7-final-v1','status':c7['terminal']['development_failure'],'reason':'fixed_pairwise_boundary_geometry_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    rolling,_,_,_=predict_seasons((2021,2022),rows,bmap,bmats,snaps,core,c3,c7,v311,v322,v323,v324,fold_map,'hard_gates_2021_2022')
    write_json(a.out/'rolling_2021_2022.json',rolling)
    if not rolling['all_pass']:
        final={'schema_version':'football3-v3-2-7-final-v1','status':c7['terminal']['development_failure'],'reason':'fixed_pairwise_boundary_geometry_failed_2021_2022_hard_gates','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    freeze={'candidate':'V3_2_7_FIXED_PAIRWISE_BOUNDARY_GEOMETRY','geometry_safety':c7['geometry_safety'],'base_candidate':c7['frozen_base_candidate'],'rolling_2021_2022':rolling['pooled'],'frozen_before_2023':True,'source_max_season_seen_at_freeze':2022}
    write_json(a.out/'candidate_freeze_before_2023.json',freeze)
    rows23,fold23,_,rowrec23,proc23,procrec23,bmap23,bmats23,baserec23,state_params23,snaps23,stateboard23=v322.stage(2023,a,c3,core,v311,v31,usr,v2,xg)
    if state_params23!=state_params: raise V327Error('state selection drift after 2023 reopen')
    diag,_,_,_=predict_seasons((2023,),rows23,bmap23,bmats23,snaps23,core,c3,c7,v311,v322,v323,v324,None,'hard_gates_2023_candidate_fixed')
    write_json(a.out/'postfreeze_2023_row_receipt.json',rowrec23); write_json(a.out/'candidate_fixed_2023.json',diag)
    status=c7['terminal']['candidate_fixed_2023_success'] if diag['all_pass'] else c7['terminal']['candidate_fixed_2023_failure']
    final={'schema_version':'football3-v3-2-7-final-v1','status':status,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'candidate_fixed_2023':diag['pooled'],'source_max_season_loaded_after_freeze':2023,'2023_opened':True,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
