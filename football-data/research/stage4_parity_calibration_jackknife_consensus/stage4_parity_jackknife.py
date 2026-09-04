#!/usr/bin/env python3
import argparse, importlib.util, itertools, json, math, pathlib, sys
EPS=1e-15
TOL=1e-12
class Stage4Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path)); mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def write_json(path,obj): pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))

def minimum_boundary_projection(base,target,weak,epsilon=1e-9):
    b=list(map(float,base)); bt=top1(b); target=int(target); weak=int(weak); epsilon=float(epsilon)
    if target==bt: return b,{'executed':False,'reason':'same_argmax','target':target,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
    competitors=[i for i in range(3) if i!=target]; best=None
    for k in (1,2):
        for bind in itertools.combinations(competitors,k):
            if bt not in bind: continue
            S=(target,)+tuple(bind); c=(sum(b[i] for i in S)-epsilon)/len(S); p=b[:]; p[target]=c+epsilon
            for j in bind: p[j]=c
            if any((not math.isfinite(x)) or x<=EPS for x in p): continue
            if abs(sum(p)-1.0)>1e-10: continue
            if any(p[target] < p[j]+epsilon-5e-13 for j in competitors): continue
            if p[weak] < b[weak]-TOL: continue
            cost=sum((p[i]-b[i])**2 for i in range(3)); tv=0.5*sum(abs(p[i]-b[i]) for i in range(3)); rec=(cost,tv,p,bind)
            if best is None or rec[:2] < best[:2]: best=rec
    if best is None: return b,{'executed':False,'reason':'weak_floor_or_projection_infeasible','target':target,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
    cost,tv,p,bind=best
    if top1(p)!=target: raise Stage4Error('projection did not create target Top1')
    return p,{'executed':True,'reason':'projected','target':target,'baseline_top1':bt,'binding_competitors':list(bind),'total_variation':tv,'l2_sq':cost}

def jackknife_consensus(full_target,full_eligible,jack_targets,jack_eligible,min_units=2):
    if not full_eligible: return False,'full_not_eligible'
    if len(jack_targets) < int(min_units): return False,'insufficient_training_seasons'
    if len(jack_targets)!=len(jack_eligible): raise Stage4Error('jackknife shape mismatch')
    if not all(bool(x) for x in jack_eligible): return False,'jackknife_not_eligible'
    if not all(int(x)==int(full_target) for x in jack_targets): return False,'jackknife_direction_disagreement'
    return True,'unanimous'

def exact_switch_matrices(rows,pm,bmats,dg,v311,v322):
    switched=[r for r in rows if dg[r['fixture_id']].get('stage4_executed_switch',False)]
    mm={r['fixture_id']:[[float(x) for x in row] for row in bmats[r['fixture_id']]] for r in rows}
    if switched:
        spm={r['fixture_id']:pm[r['fixture_id']] for r in switched}; sbm={r['fixture_id']:bmats[r['fixture_id']] for r in switched}; sdg={r['fixture_id']:dg[r['fixture_id']] for r in switched}
        sm,_,_=v322.matrices(switched,spm,sbm,v311,sdg); mm.update(sm)
    integ=0.0; unchanged=0.0
    for r in rows:
        fid=r['fixture_id']; ip=v311.integrate(mm[fid]); integ=max(integ,max(abs(float(ip[i])-float(pm[fid][i])) for i in range(3)))
        if not dg[fid].get('stage4_executed_switch',False): unchanged=max(unchanged,max(abs(mm[fid][h][a]-bmats[fid][h][a]) for h in range(15) for a in range(15)))
    return mm,integ,unchanged

def evaluate(rows,pm,bmap,bmats,dg,fold_map,gates,v311,v322,require_folds):
    mm,integ,unchanged=exact_switch_matrices(rows,pm,bmats,dg,v311,v322)
    ev=v322.evaluate(rows,pm,bmap,mm,bmats,dg,fold_map,gates,'B',require_folds)
    ev['deltas']['matrix_to_1x2_max_abs_error']=integ; ev['deltas']['outside_executed_switch_matrix_max_abs_error']=unchanged
    ev['checks']['matrix_gate']=integ<=gates['matrix_to_1x2_max_abs_error']+TOL and unchanged<=TOL
    ev['checks']['all_pass']=all(ev['checks'].values())
    return ev,mm

def predict_one_season(s,rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323):
    params=c4['frozen_direction_generator']['params']; epsilon=c4['projection']['epsilon']; min_units=c4['reliability_gate']['minimum_distinct_training_seasons']
    tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]; te=[r for r in rows if r['season']==s]; train_seasons=sorted({int(r['season']) for r in tr})
    pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322); qfull,dfull,fitfull=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322)
    jack=[]
    if len(train_seasons)>=int(min_units):
        for dropped in train_seasons:
            trj=[r for r in tr if int(r['season'])!=dropped]
            if not trj: continue
            prefj=v323.prefit_target(trj,s,bmap,snaps,core,c3,v322); qj,dj,fitj=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,prefj,v322); jack.append((dropped,qj,dj,fitj))
    pm={}; outdg={}; tvs=[]
    stats={'season':s,'training_seasons':train_seasons,'jackknife_model_n':len(jack),'full_fit':fitfull,'full_direction_proposal_n':0,'jackknife_consensus_switch_n':0,'jackknife_blocked_switch_n':0,'insufficient_training_seasons_blocked_n':0,'weak_floor_fallback_n':0,'switch_target_counts':{'H':0,'D':0,'A':0},'consensus_reason_counts':{},'mean_total_variation_on_executed':0.0,'max_total_variation_on_executed':0.0}
    for r in te:
        fid=r['fixture_id']; b=list(map(float,bmap[fid])); bt=top1(b); qt=top1(qfull[fid]); d=dict(dfull[fid]); eligible=bool(d.get('eligible',False)); proposed=eligible and bt!=qt
        if proposed: stats['full_direction_proposal_n']+=1
        jtargets=[top1(qj[fid]) for _,qj,_,_ in jack]; jeligible=[bool(dj[fid].get('eligible',False)) for _,_,dj,_ in jack]
        ok,reason=(jackknife_consensus(qt,eligible,jtargets,jeligible,min_units) if proposed else (False,'not_full_proposal'))
        stats['consensus_reason_counts'][reason]=stats['consensus_reason_counts'].get(reason,0)+1
        if proposed and not ok:
            stats['jackknife_blocked_switch_n']+=1
            if reason=='insufficient_training_seasons': stats['insufficient_training_seasons_blocked_n']+=1
        _,weak=v322.fav_weak(b)
        if ok:
            p,pr=minimum_boundary_projection(b,qt,weak,epsilon)
            if pr['executed']:
                stats['jackknife_consensus_switch_n']+=1; stats['switch_target_counts']['HDA'[qt]]+=1; tvs.append(pr['total_variation'])
            else: stats['weak_floor_fallback_n']+=1
        else:
            p=b; pr={'executed':False,'reason':reason,'target':qt,'baseline_top1':bt,'total_variation':0.0,'l2_sq':0.0}
        if p[weak] < b[weak]-TOL: raise Stage4Error('weak floor violated')
        if abs(sum(p)-1)>1e-10 or any((not math.isfinite(x)) or x<=0 for x in p): raise Stage4Error('invalid candidate probability')
        pm[fid]=p; d.update({'stage4_full_direction_proposed':proposed,'stage4_jackknife_model_n':len(jack),'stage4_jackknife_targets':jtargets,'stage4_jackknife_eligible':jeligible,'stage4_consensus':ok,'stage4_consensus_reason':reason,'stage4_executed_switch':pr['executed'],'stage4_projection_reason':pr['reason'],'stage4_projection_tv':pr['total_variation'],'stage4_target':qt}); outdg[fid]=d
    if tvs: stats['mean_total_variation_on_executed']=sum(tvs)/len(tvs); stats['max_total_variation_on_executed']=max(tvs)
    return te,pm,outdg,stats

def predict_seasons(seasons,rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323,fold_map=None,gates_key=None):
    allr=[]; pm={}; dg={}; per=[]
    for s in seasons:
        te,p,d,st=predict_one_season(s,rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323); allr.extend(te); pm.update(p); dg.update(d); per.append(st)
    base={r['fixture_id']:bmap[r['fixture_id']] for r in allr}; bm={r['fixture_id']:bmats[r['fixture_id']] for r in allr}; gates=c4[gates_key]
    ev,mm=evaluate(allr,pm,base,bm,dg,fold_map,gates,v311,v322,require_folds=(gates_key=='hard_gates_2021_2022'))
    agg={'full_direction_proposal_n':sum(x['full_direction_proposal_n'] for x in per),'jackknife_model_n':sum(x['jackknife_model_n'] for x in per),'jackknife_consensus_switch_n':sum(x['jackknife_consensus_switch_n'] for x in per),'jackknife_blocked_switch_n':sum(x['jackknife_blocked_switch_n'] for x in per),'insufficient_training_seasons_blocked_n':sum(x['insufficient_training_seasons_blocked_n'] for x in per),'weak_floor_fallback_n':sum(x['weak_floor_fallback_n'] for x in per),'switch_target_counts':{k:sum(x['switch_target_counts'][k] for x in per) for k in 'HDA'}}
    den=agg['jackknife_consensus_switch_n']; agg['mean_total_variation_on_executed']=(sum(x['mean_total_variation_on_executed']*x['jackknife_consensus_switch_n'] for x in per)/den if den else 0.0); agg['max_total_variation_on_executed']=max([x['max_total_variation_on_executed'] for x in per] or [0.0]); ev['stage4_jackknife']=agg
    return {'seasons':per,'pooled':ev,'all_pass':ev['checks']['all_pass']},pm,dg,mm

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'): ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c4=json.loads(a.contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c4['status']!='FROZEN_BEFORE_STAGE4_TARGET_SCORING': raise Stage4Error('stage4 contract drift')
    if c3['status']!='FROZEN_BEFORE_V3_2_3_TARGET_SCORING': raise Stage4Error('v323 contract drift')
    expected={'cap':0.02,'draw_lambda':16.0,'draw_scale':0.25,'half_life':1.0,'side_lambda':4.0,'side_scale':0.25,'support_tau':1.5}
    if c4['frozen_direction_generator']['params']!=expected: raise Stage4Error('frozen T2 params drift')
    if c4['reliability_gate']['threshold_grid']!='NONE' or c4['reliability_gate']['numeric_threshold']!='NONE': raise Stage4Error('jackknife gate must be parameter free')
    if c4['data_roles']['2023']!='CLOSED_IN_STAGE4': raise Stage4Error('2023 must remain closed')
    v323=loadmod('stage4_v323',a.v323); v322=loadmod('stage4_v322',a.v322); a.v32dev_module=loadmod('stage4_v32dev',a.v32dev); core=loadmod('stage4_core',a.core); v311=loadmod('stage4_v311',a.v311); v31=loadmod('stage4_v31',a.v31); usr=loadmod('stage4_usr',a.usr1); v2=loadmod('stage4_v2',a.v2); xg=loadmod('stage4_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec); write_json(a.out/'pre2023_process_receipt.json',procrec); write_json(a.out/'pre2023_baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec}); write_json(a.out/'dynamic_state_selection_2019_pre2023.json',stateboard)
    sanity,_,_,_=predict_seasons((2019,2020),rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323,None,'sanity_gates_2019_2020'); write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-stage4-parity-jackknife-final-v1','status':c4['terminal']['development_failure'],'reason':'fixed_jackknife_consensus_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    rolling,_,_,_=predict_seasons((2021,2022),rows,bmap,bmats,snaps,core,c3,c4,v311,v322,v323,fold_map,'hard_gates_2021_2022'); write_json(a.out/'rolling_2021_2022.json',rolling)
    status=c4['terminal']['development_success'] if rolling['all_pass'] else c4['terminal']['development_failure']
    final={'schema_version':'football3-stage4-parity-jackknife-final-v1','status':status,'reason':('all_stage4_hard_gates_passed' if rolling['all_pass'] else 'fixed_jackknife_consensus_failed_2021_2022_hard_gates'),'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
    write_json(a.out/'final_status.json',final)
    if rolling['all_pass']:
        write_json(a.out/'stage4_candidate_freeze_for_stage5.json',{'candidate':'STAGE4_PARITY_JACKKNIFE_CONSENSUS','direction_family':'T2','direction_params':c4['frozen_direction_generator']['params'],'reliability_gate':c4['reliability_gate'],'projection':c4['projection'],'rolling_2021_2022':rolling['pooled'],'frozen_for_stage5':True,'2023_opened':False,'3504_opened':False})
    print(json.dumps(final,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
