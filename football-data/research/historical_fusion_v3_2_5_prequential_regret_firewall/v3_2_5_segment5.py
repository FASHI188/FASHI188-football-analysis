#!/usr/bin/env python3
import argparse, importlib.util, itertools, json, math, pathlib, sys

EPS=1e-15
TOL=1e-12
REGRET_TOL=1e-15

class V325Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path)); mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def write_json(path,obj): pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))

def time_key(r):
    for k in ('kickoff_utc','kickoff_at','kickoff','datetime','date'):
        if k in r and r[k] not in (None,''):
            return str(r[k])
    raise V325Error('no PIT-safe kickoff/date key on row')

def result_index(r):
    for k in ('result_index','outcome_index','y'):
        if k in r:
            v=r[k]
            if isinstance(v,(int,float)) and int(v) in (0,1,2): return int(v)
    for k in ('outcome','result','result_1x2'):
        if k in r:
            v=str(r[k]).strip().upper()
            if v in ('H','HOME','1'): return 0
            if v in ('D','DRAW','X'): return 1
            if v in ('A','AWAY','2'): return 2
    pairs=(('home_goals','away_goals'),('goals_home','goals_away'),('h_goals','a_goals'),('hg','ag'))
    for hk,ak in pairs:
        if hk in r and ak in r:
            h=float(r[hk]); a=float(r[ak]); return 0 if h>a else (1 if h==a else 2)
    raise V325Error('no completed-result fields on row')

def group_rows(rows):
    ordered=sorted(rows,key=lambda r:(time_key(r),str(r['fixture_id'])))
    out=[]
    for _,g in itertools.groupby(ordered,key=time_key): out.append(list(g))
    return out

def apply_firewall(rows,bmap,shadow_pm,shadow_dg):
    pm={}; dg={}; regret=0.0; shadow_n=executed=blocked=0; open_groups=closed_groups=0; targets={'H':0,'D':0,'A':0}; tvs=[]; trace=[]
    for grp in group_rows(rows):
        gate_open=(regret <= REGRET_TOL)
        if gate_open: open_groups+=1
        else: closed_groups+=1
        before=regret; group_shadow=[]
        for r in grp:
            fid=r['fixture_id']; b=list(map(float,bmap[fid])); sp=list(map(float,shadow_pm[fid])); sd=dict(shadow_dg[fid]); sh=bool(sd.get('v324_executed_switch',False))
            if sh: shadow_n+=1
            actual=bool(sh and gate_open)
            if actual:
                p=sp; executed+=1; tv=float(sd.get('v324_projection_tv',0.0)); tvs.append(tv); targets['HDA'[top1(p)]]+=1
            else:
                p=b
                if sh: blocked+=1
            fd=dict(sd)
            fd['v325_shadow_executable_switch']=sh
            fd['v325_firewall_open_before_kickoff']=gate_open
            fd['v325_firewall_regret_before_kickoff']=before
            fd['v325_executed_switch']=actual
            fd['v324_executed_switch']=actual
            if sh and not actual: fd['v324_projection_reason']='blocked_by_prequential_shadow_logloss_regret'
            pm[fid]=p; dg[fid]=fd
            if sh: group_shadow.append((r,b,sp))
        group_delta=0.0
        for r,b,sp in group_shadow:
            y=result_index(r)
            group_delta += math.log(max(b[y],EPS))-math.log(max(sp[y],EPS))
        regret += group_delta
        if group_shadow:
            trace.append({'time_key':time_key(grp[0]),'gate_open_before':gate_open,'shadow_proposals':len(group_shadow),'regret_before':before,'group_shadow_logloss_delta':group_delta,'regret_after':regret})
    stats={
        'shadow_executable_switch_n':shadow_n,
        'firewall_executed_switch_n':executed,
        'firewall_blocked_switch_n':blocked,
        'firewall_open_group_n':open_groups,
        'firewall_closed_group_n':closed_groups,
        'season_final_shadow_regret':regret,
        'mean_total_variation_on_executed':(sum(tvs)/len(tvs) if tvs else 0.0),
        'max_total_variation_on_executed':(max(tvs) if tvs else 0.0),
        'switch_target_counts':targets,
        'regret_trace':trace,
    }
    return pm,dg,stats

def predict_seasons(seasons,rows,bmap,bmats,snaps,core,c3,c5,v311,v322,v323,v324,fold_map=None,gates_key=None):
    params=c5['frozen_base_candidate']['frozen_t2_params']; allr=[]; pm={}; dg={}; stats=[]
    for s in seasons:
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]; te=[r for r in rows if r['season']==s]
        pref=v323.prefit_target(tr,s,bmap,snaps,core,c3,v322)
        q,d,fit=v323.predict_prefit('T2',te,bmap,snaps,core,c3,params,pref,v322)
        shadow_pm,shadow_dg,shadow_stats=v324.project_direction_map(te,bmap,q,d,v322,c5['frozen_base_candidate']['projection_epsilon'])
        p,pd,fw=apply_firewall(te,bmap,shadow_pm,shadow_dg)
        st={'season':s,'fit':fit,'shadow_direction_proposal_n':shadow_stats['direction_proposal_n'],**fw}
        stats.append(st); allr.extend(te); pm.update(p); dg.update(pd)
    base={r['fixture_id']:bmap[r['fixture_id']] for r in allr}; bm={r['fixture_id']:bmats[r['fixture_id']] for r in allr}
    gates=c5[gates_key] if gates_key else None
    ev,mm=v324.evaluate(allr,pm,base,bm,dg,fold_map,gates,v311,v322,require_folds=(gates_key=='hard_gates_2021_2022'))
    agg={
        'shadow_direction_proposal_n':sum(x['shadow_direction_proposal_n'] for x in stats),
        'shadow_executable_switch_n':sum(x['shadow_executable_switch_n'] for x in stats),
        'firewall_executed_switch_n':sum(x['firewall_executed_switch_n'] for x in stats),
        'firewall_blocked_switch_n':sum(x['firewall_blocked_switch_n'] for x in stats),
        'firewall_open_group_n':sum(x['firewall_open_group_n'] for x in stats),
        'firewall_closed_group_n':sum(x['firewall_closed_group_n'] for x in stats),
        'season_final_shadow_regret':{str(x['season']):x['season_final_shadow_regret'] for x in stats},
        'switch_target_counts':{k:sum(x['switch_target_counts'][k] for x in stats) for k in 'HDA'}
    }
    den=agg['firewall_executed_switch_n']; agg['mean_total_variation_on_executed']=(sum(x['mean_total_variation_on_executed']*x['firewall_executed_switch_n'] for x in stats)/den if den else 0.0); agg['max_total_variation_on_executed']=max([x['max_total_variation_on_executed'] for x in stats] or [0.0])
    ev['v325_firewall']=agg
    return {'seasons':stats,'pooled':ev,'all_pass':ev['checks']['all_pass']},pm,dg,mm

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v324_contract','v324','v323_contract','v323','v322','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c5=json.loads(a.contract.read_text()); c4=json.loads(a.v324_contract.read_text()); c3=json.loads(a.v323_contract.read_text())
    if c5['status']!='FROZEN_BEFORE_V3_2_5_TARGET_SCORING': raise V325Error('V3.2.5 contract drift')
    if c4['status']!='FROZEN_BEFORE_V3_2_4_TARGET_SCORING' or c3['status']!='FROZEN_BEFORE_V3_2_3_TARGET_SCORING': raise V325Error('ancestor contract drift')
    if c5['firewall']['threshold']!=0.0 or c5['firewall']['threshold_grid']!='NONE' or c5['firewall']['window']!='EXPANDING_WITHIN_SEASON' or c5['firewall']['decay']!='NONE': raise V325Error('firewall mechanics drift')
    if c5['frozen_base_candidate']['frozen_t2_params']!=c4['direction_generator']['frozen_t2_params']: raise V325Error('T2 drift from V3.2.4')
    if abs(c5['frozen_base_candidate']['projection_epsilon']-c4['projection']['epsilon'])>0: raise V325Error('projection epsilon drift')
    v324=loadmod('v325_v324',a.v324); v323=loadmod('v325_v323',a.v323); v322=loadmod('v325_v322',a.v322); a.v32dev_module=loadmod('v325_v32dev',a.v32dev); core=loadmod('v325_core',a.core); v311=loadmod('v325_v311',a.v311); v31=loadmod('v325_v31',a.v31); usr=loadmod('v325_usr',a.usr1); v2=loadmod('v325_v2',a.v2); xg=loadmod('v325_xg',a.xg)
    rows,fold_map,fold_rows,rowrec,proc,procrec,bmap,bmats,baserec,state_params,snaps,stateboard=v322.stage(2022,a,c3,core,v311,v31,usr,v2,xg)
    write_json(a.out/'pre2023_row_receipt.json',rowrec); write_json(a.out/'pre2023_process_receipt.json',procrec); write_json(a.out/'pre2023_baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec}); write_json(a.out/'dynamic_state_selection_2019_pre2023.json',stateboard)
    sanity,_,_,_=predict_seasons((2019,2020),rows,bmap,bmats,snaps,core,c3,c5,v311,v322,v323,v324,None,'sanity_gates_2019_2020'); write_json(a.out/'sanity_2019_2020.json',sanity)
    if not sanity['all_pass']:
        final={'schema_version':'football3-v3-2-5-final-v1','status':c5['terminal']['development_failure'],'reason':'fixed_prequential_firewall_failed_2019_2020_sanity','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    rolling,_,_,_=predict_seasons((2021,2022),rows,bmap,bmats,snaps,core,c3,c5,v311,v322,v323,v324,fold_map,'hard_gates_2021_2022'); write_json(a.out/'rolling_2021_2022.json',rolling)
    if not rolling['all_pass']:
        final={'schema_version':'football3-v3-2-5-final-v1','status':c5['terminal']['development_failure'],'reason':'fixed_prequential_firewall_failed_2021_2022_hard_gates','research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'source_max_season_loaded':2022,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    freeze={'candidate':'V3_2_5_FIXED_PREQUENTIAL_REGRET_FIREWALL','firewall':c5['firewall'],'base_candidate':c5['frozen_base_candidate'],'rolling_2021_2022':rolling['pooled'],'frozen_before_2023':True,'source_max_season_seen_at_freeze':2022}; write_json(a.out/'candidate_freeze_before_2023.json',freeze)
    rows23,fold23,_,rowrec23,proc23,procrec23,bmap23,bmats23,baserec23,state_params23,snaps23,stateboard23=v322.stage(2023,a,c3,core,v311,v31,usr,v2,xg)
    if state_params23!=state_params: raise V325Error('state selection drift after 2023 reopen')
    diag,_,_,_=predict_seasons((2023,),rows23,bmap23,bmats23,snaps23,core,c3,c5,v311,v322,v323,v324,None,'hard_gates_2023_candidate_fixed'); write_json(a.out/'postfreeze_2023_row_receipt.json',rowrec23); write_json(a.out/'candidate_fixed_2023.json',diag)
    status=c5['terminal']['candidate_fixed_2023_success'] if diag['all_pass'] else c5['terminal']['candidate_fixed_2023_failure']
    final={'schema_version':'football3-v3-2-5-final-v1','status':status,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'rolling_2021_2022':rolling['pooled'],'candidate_fixed_2023':diag['pooled'],'source_max_season_loaded_after_freeze':2023,'2023_opened':True,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
