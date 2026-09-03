#!/usr/bin/env python3
import argparse, importlib.util, itertools, json, math, pathlib

EPS=1e-15
TOL=1e-12

class V324Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path)); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def write_json(path,obj): pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))

def minimum_boundary_projection(base,target,weak,epsilon=1e-9):
    b=list(map(float,base)); bt=top1(b); target=int(target); weak=int(weak); epsilon=float(epsilon)
    if target==bt:
        return b,{'executed':False,'reason':'same_argmax','target':target,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
    competitors=[i for i in range(3) if i!=target]
    best=None
    # The binding set contains target plus one or both competitors. Enumerating all
    # active sets is exact for the 3-class Euclidean projection onto a strict Top1 cone.
    for k in (1,2):
        for bind in itertools.combinations(competitors,k):
            S=(target,)+tuple(bind)
            if bt not in bind: continue
            c=(sum(b[i] for i in S)-epsilon)/len(S)
            p=b[:]; p[target]=c+epsilon
            for j in bind: p[j]=c
            if any((not math.isfinite(x)) or x<=EPS for x in p): continue
            if abs(sum(p)-1.0)>1e-10: continue
            if any(p[target] < p[j]+epsilon-5e-13 for j in competitors): continue
            if p[weak] < b[weak]-TOL: continue
            cost=sum((p[i]-b[i])**2 for i in range(3))
            tv=0.5*sum(abs(p[i]-b[i]) for i in range(3))
            rec=(cost,tv,p,bind)
            if best is None or rec[:2] < best[:2]: best=rec
    if best is None:
        return b,{'executed':False,'reason':'weak_floor_or_projection_infeasible','target':target,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
    cost,tv,p,bind=best
    if top1(p)!=target: raise V324Error('projection did not create target Top1')
    return p,{'executed':True,'reason':'projected','target':target,'baseline_top1':bt,'binding_competitors':list(bind),'total_variation':tv,'l2_sq':cost}

def project_direction_map(rows,bmap,qmap,dg,v322,epsilon):
    pm={}; outdg={}; proposals=executed=fallback=0; tvs=[]; targets={'H':0,'D':0,'A':0}
    for r in rows:
        fid=r['fixture_id']; b=list(map(float,bmap[fid])); q=list(map(float,qmap[fid])); d=dict(dg[fid]); bt=top1(b); qt=top1(q); _,weak=v322.fav_weak(b)
        proposed=(bt!=qt)
        if proposed: proposals+=1
        if not d.get('eligible',False) or not proposed:
            p=b; pr={'executed':False,'reason':'not_eligible_or_no_direction_switch','target':qt,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
        else:
            p,pr=minimum_boundary_projection(b,qt,weak,epsilon)
            if pr['executed']:
                executed+=1; tvs.append(pr['total_variation']); targets['HDA'[qt]]+=1
            else: fallback+=1
        if p[weak] < b[weak]-TOL: raise V324Error('weak floor violated')
        if abs(sum(p)-1)>1e-10 or any((not math.isfinite(x)) or x<=0 for x in p): raise V324Error('invalid projected probability')
        pm[fid]=p; d.update({'v324_direction_proposed':proposed,'v324_executed_switch':pr['executed'],'v324_projection_reason':pr['reason'],'v324_projection_tv':pr['total_variation'],'v324_target':qt}); outdg[fid]=d
    stats={'direction_proposal_n':proposals,'executed_switch_n':executed,'weak_floor_fallback_n':fallback,'mean_total_variation_on_executed':(sum(tvs)/len(tvs) if tvs else 0.0),'max_total_variation_on_executed':(max(tvs) if tvs else 0.0),'switch_target_counts':targets}
    return pm,outdg,stats

def exact_switch_matrices(rows,pm,bmats,dg,v311,v322):
    switched=[r for r in rows if dg[r['fixture_id']].get('v324_executed_switch',False)]
    mm={r['fixture_id']:[[float(x) for x in row] for row in bmats[r['fixture_id']]] for r in rows}
    if switched:
        spm={r['fixture_id']:pm[r['fixture_id']] for r in switched}; sbm={r['fixture_id']:bmats[r['fixture_id']] for r in switched}; sdg={r['fixture_id']:dg[r['fixture_id']] for r in switched}
        sm,_,_=v322.matrices(switched,spm,sbm,v311,sdg)
        mm.update(sm)
    integ=0.0; unchanged=0.0
    for r in rows:
        fid=r['fixture_id']; ip=v311.integrate(mm[fid]); integ=max(integ,max(abs(float(ip[i])-float(pm[fid][i])) for i in range(3)))
        if not dg[fid].get('v324_executed_switch',False): unchanged=max(unchanged,max(abs(mm[fid][h][a]-bmats[fid][h][a]) for h in range(15) for a in range(15)))
    return mm,integ,unchanged

def evaluate(rows,pm,bmap,bmats,dg,fold_map,gates,v311,v322,require_folds):
    mm,integ,unchanged=exact_switch_matrices(rows,pm,bmats,dg,v311,v322)
    ev=v322.evaluate(rows,pm,bmap,mm,bmats,dg,fold_map,gates,'B',require_folds)
    ev['deltas']['matrix_to_1x2_max_abs_error']=integ; ev['deltas']['outside_executed_switch_matrix_max_abs_error']=unchanged
    ev['checks']['matrix_gate']=integ<=gates['matrix_to_1x2_max_abs_error']+TOL and unchanged<=TOL
    ev['checks']['all_pass']=all(ev['checks'].values())
    return ev,mm

def predict_seasons(seasons,rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323,fold_map=None,gates_key=None):
    params=c4['direction_generator']['frozen_t2_params']; epsilon=c4['projection']['epsilon']; allr=[]; pm={}; dg={}; stats=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]; te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322); q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322)
        p,pd,st=project_direction_map(te,bmap,q,d,v322,epsilon); st={'season':s,'fit':fit,**st}; stats.append(st); allr.extend(te); pm.update(p); dg.update(pd)
    base={r['fixture_id']:bmap[r['fixture_id']] for r in allr}; bm={r['fixture_id']:bmats[r['fixture_id']] for r in allr}
    gates=c4[gates_key] if gates_key else None
    ev,mm=evaluate(allr,pm,base,bm,dg,fold_map,gates,v311,v322,require_folds=(gates_key=='hard_gates_2021_2022'))
    agg={'direction_proposal_n':sum(x['direction_proposal_n'] for x in stats),'executed_switch_n':sum(x['executed_switch_n'] for x in stats),'weak_floor_fallback_n':sum(x['weak_floor_fallback_n'] for x in stats)}
    tvden=agg['executed_switch_n']; agg['mean_total_variation_on_executed']=(sum(x['mean_total_variation_on_executed']*x['executed_switch_n'] for x in stats)/tvden if tvden else 0.0); agg['max_total_variation_on_executed']=max([x['max_total_variation_on_executed'] for x in stats] or [0.0]); agg['switch_target_counts']={k:sum(x['switch_target_counts'][k] for x in stats) for k in 'HDA'}
    ev['v324_projection']=agg
    return {'seasons':stats,'pooled':ev,'all_pass':ev['checks']['all_pass']},pm,dg,mm

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c4=json.loads(a.contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c4['status']!='FROZEN_BEFORE_V3_2_4_TARGET_SCORING' or c3['status']!='FROZEN_BEFORE_V3_2_3_TARGET_SCORING': raise V324Error('contract drift')
    if c4['direction_generator']['frozen_t2_params']!={'cap':0.02,'draw_lambda':16.0,'draw_scale':0.25,'half_life':1.0,'side_lambda':4.0,'side_scale':0.25,'support_tau':1.5}: raise V324Error('frozen direction params drift')
    v323=loadmod('v324_v323',a.v323); v322=loadmod('v324_v322',a.v322); a.v32dev_module=loadmod('v324_v32dev',a.v32dev); core=loadmod('v324_core',a.core); v311=loadmod('v324_v311',a.v311); v31=loadmod('v324_v31',a.v31); usr=loadmod('v324_usr',a.usr1); v2=loadmod('v324_v2',a.v2); xg=loadmod('v324_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec); write_json(a.out/'pre2023_process_receipt.json',procrec); write_json(a.out/'pre2023_baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec}); write_json(a.out/'dynamic_state_selection_2019_pre2023.json',stateboard)
    sanity,_,_,_=predict_seasons((2019,2020),rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323,None,'sanity_gates_2019_2020'); write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-2-4-final-v1','status':c4['terminal']['development_failure'],'reason':'fixed_minimum_projection_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    rolling,_,_,_=predict_seasons((2021,2022),rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323,fold_map,'hard_gates_2021_2022'); write_json(a.out/'rolling_2021_2022.json',rolling)
    if not rolling['all_pass']:
        final={'schema_version':'football3-v3-2-4-final-v1','status':c4['terminal']['development_failure'],'reason':'fixed_minimum_projection_failed_2021_2022_hard_gates','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    freeze={'candidate':'V3_2_4_FIXED_MINIMUM_BOUNDARY_PROJECTION','direction_family':'T2','direction_params':c4['direction_generator']['frozen_t2_params'],'projection':c4['projection'],'rolling_2021_2022':rolling['pooled'],'frozen_before_2023':True,'source_max_season_seen_at_freeze':2022}; write_json(a.out/'candidate_freeze_before_2023.json',freeze)
    rows23,fold23,_,rowrec23,proc23,procrec23,bmap23,bmats23,baserec23,state_params23,snaps23,stateboard23=v322.stage(2023,a,c3,core,v311,v31,usr,v2,xg)
    if state_params23!=state_params: raise V324Error('state selection drift after 2023 reopen')
    diag,_,_,_=predict_seasons((2023,),rows23,bmap23,bmats23,snaps23,core,c3,c4,v311,v322,v323,None,'hard_gates_2023_candidate_fixed'); write_json(a.out/'postfreeze_2023_row_receipt.json',rowrec23); write_json(a.out/'candidate_fixed_2023.json',diag)
    status=c4['terminal']['candidate_fixed_2023_success'] if diag['all_pass'] else c4['terminal']['candidate_fixed_2023_failure']
    final={'schema_version':'football3-v3-2-4-final-v1','status':status,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'candidate_fixed_2023':diag['pooled'],'source_max_season_loaded_after_freeze':2023,'2023_opened':True,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
