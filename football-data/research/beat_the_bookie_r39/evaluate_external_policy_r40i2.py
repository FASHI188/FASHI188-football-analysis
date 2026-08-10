#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,importlib.util,json,math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path


def load_module(name,path):
    s=importlib.util.spec_from_file_location(name,path)
    if s is None or s.loader is None: raise RuntimeError(f'cannot import {path}')
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def auc(scores,labels):
    n=len(scores); order=sorted(range(n),key=lambda i:scores[i]); ranks=[0.0]*n; i=0
    while i<n:
        j=i+1
        while j<n and scores[order[j]]==scores[order[i]]: j+=1
        avg=((i+1)+j)/2.0
        for k in range(i,j): ranks[order[k]]=avg
        i=j
    pos=sum(labels); neg=n-pos
    if pos==0 or neg==0: return None
    rank_pos=sum(ranks[i] for i,y in enumerate(labels) if y==1)
    return (rank_pos-pos*(pos+1)/2.0)/(pos*neg)


def argmax3(p):
    best=0
    for i in (1,2):
        if p[i]>p[best]: best=i
    return best


def metrics(rows,key):
    eps=1e-15; ll=br=rp=dll=0.0; correct=tp=fp=fn=0; ds=[]; dy=[]
    for r in rows:
        p=r[key]; y=r['y']; one=[0.0,0.0,0.0]; one[y]=1.0
        ll-=math.log(max(eps,min(1-eps,p[y])))
        br+=sum((p[i]-one[i])**2 for i in range(3))
        rp+=((p[0]-one[0])**2+((p[0]+p[1])-(one[0]+one[1]))**2)/2.0
        isd=1 if y==1 else 0; pd=max(eps,min(1-eps,p[1])); dll-=isd*math.log(pd)+(1-isd)*math.log(1-pd)
        pred=argmax3(p); correct+=int(pred==y)
        if pred==1 and y==1: tp+=1
        elif pred==1 and y!=1: fp+=1
        elif pred!=1 and y==1: fn+=1
        ds.append(p[1]); dy.append(isd)
    n=len(rows); prec=tp/(tp+fp) if tp+fp else 0.0; rec=tp/(tp+fn) if tp+fn else 0.0; f1=2*prec*rec/(prec+rec) if prec+rec else 0.0
    return {'n':n,'accuracy':correct/n,'hda_logloss':ll/n,'hda_brier':br/n,'rps':rp/n,'draw_logloss':dll/n,'draw_auc':auc(ds,dy),'draw_top1_precision':prec,'draw_top1_recall':rec,'draw_top1_f1':f1,'predicted_draws':tp+fp,'actual_draws':sum(dy)}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True); ap.add_argument('--r40h1-registration',type=Path,required=True); ap.add_argument('--r40h1-code',type=Path,required=True)
    ap.add_argument('--odds-csv',type=Path,required=True); ap.add_argument('--policy-results',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text()); fr=json.loads(a.r40h1_registration.read_text()); m=load_module('r40h1',a.r40h1_code)
    assert reg['status']=='PRE_REGISTERED_ONE_TIME_EXTERNAL_POLICY_EVALUATION'; assert reg['hard_limits']['blind_result_values_allowed'] is False; assert reg['hard_limits']['model_fit_allowed'] is False
    assert m.hfile(a.odds_csv)==reg['source_binding']['odds_csv_sha256']

    model=fr['frozen_internal_model']; expected=['match_id','date_start','competition_name','date_created','home_team_name','away_team_name','home_team_odd','away_team_odd','tie_odd']
    identities={}; inconsistent=set()
    with a.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f); assert list(rd.fieldnames or [])==expected
        for r in rd:
            mid=r['match_id'].strip(); ko=m.dt(r['date_start']); obs=m.dt(r['date_created'])
            if not mid or ko is None or obs is None: continue
            ident=(r['date_start'].strip(),r['competition_name'].strip(),r['home_team_name'].strip(),r['away_team_name'].strip())
            if mid not in identities: identities[mid]=ident
            elif identities[mid]!=ident: inconsistent.add(mid)
    snaps=defaultdict(dict); conflicted=set()
    with a.odds_csv.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f)
        for r in rd:
            mid=r['match_id'].strip(); ko=m.dt(r['date_start']); obs=m.dt(r['date_created'])
            if not mid or ko is None or obs is None or mid in inconsistent or not obs<ko: continue
            if not all(m.valid_odd(r[k]) for k in ('home_team_odd','away_team_odd','tie_odd')): continue
            odds=(float(r['home_team_odd']),float(r['tie_odd']),float(r['away_team_odd']))
            prior=snaps[mid].get(obs)
            if prior is None: snaps[mid][obs]=odds
            elif prior!=odds: conflicted.add(mid)
    rows=[]
    for mid,ident in identities.items():
        if mid in inconsistent or mid in conflicted: continue
        ko=m.dt(ident[0]); times=sorted(snaps.get(mid,{}))
        if len(times)<3: continue
        qs={}; picked=[]
        for hh in (24,6,1):
            c=[x for x in times if x<=ko-timedelta(hours=hh)]
            if not c: break
            t=c[-1]; picked.append(t); qs[hh]=m.devig(*snaps[mid][t])
        if len(picked)!=3 or len(set(picked))<2: continue
        q24,q6,q1=qs[24],qs[6],qs[1]; feat=m.feature(q24,q6,q1)
        z=[(feat[j]-model['training_mean'][j])/model['training_std'][j] for j in range(10)]
        bc=model['calibration_beta']; bt=model['trajectory_beta']
        cal=m.threeway(q1,m.sigmoid(bc[0]+bc[1]*z[0])); traj=m.threeway(q1,m.sigmoid(bt[0]+sum(bt[j+1]*z[j] for j in range(10))))
        rows.append({'match_id':mid,'kickoff':ko,'competition':ident[1],'home':ident[2],'away':ident[3],'block':'','features':feat,'baseline':[float(x) for x in q1],'calibration':cal,'trajectory':traj})
    rows.sort(key=lambda r:(r['kickoff'],m.mid_key(r['match_id'])))
    for i,r in enumerate(rows): r['block']='fit' if i<12936 else 'policy' if i<17248 else 'blind'
    policy=rows[12936:17248]
    assert len(policy)==reg['frozen_policy']['count']
    assert m.identity_hash(policy)==reg['frozen_policy']['identity_sha256']
    policy_pred_hash=m.prediction_hash(policy)
    if policy_pred_hash!=reg['frozen_policy']['prediction_sha256']: raise RuntimeError(f'policy prediction hash changed {policy_pred_hash}')

    label_map=reg['parent_r40i1']['source_native_mapping']; results={}
    with a.policy_results.open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f); expected_res=['match_id','date_start','competition_name','home_team_name','away_team_name','home_team_score','away_team_score','final_result']; assert list(rd.fieldnames or [])==expected_res
        for r in rd:
            mid=r['match_id'].strip()
            if mid in results: raise RuntimeError(f'duplicate policy result {mid}')
            tok=r['final_result'].strip(); assert tok in label_map
            lab=label_map[tok]; hs=int(float(r['home_team_score'])); aas=int(float(r['away_team_score'])); derived='H' if hs>aas else 'A' if hs<aas else 'D'; assert derived==lab
            results[mid]=(lab,r)
    assert len(results)==4312
    idx={'H':0,'D':1,'A':2}; evaluated=[]
    for r in policy:
        if r['match_id'] not in results: raise RuntimeError('missing policy label')
        rr=dict(r); rr['y']=idx[results[r['match_id']][0]]; evaluated.append(rr)
    mb=metrics(evaluated,'baseline'); mc=metrics(evaluated,'calibration'); mt=metrics(evaluated,'trajectory')
    g=reg['policy_gate']; gates={
        'trajectory_hda_logloss_better_than_baseline':mt['hda_logloss']<mb['hda_logloss'],
        'trajectory_hda_logloss_better_than_calibration':mt['hda_logloss']<mc['hda_logloss'],
        'trajectory_draw_logloss_better_than_baseline':mt['draw_logloss']<mb['draw_logloss'],
        'trajectory_draw_logloss_better_than_calibration':mt['draw_logloss']<mc['draw_logloss'],
        'trajectory_draw_auc_better_than_baseline':mt['draw_auc']>mb['draw_auc'],
        'trajectory_draw_auc_better_than_calibration':mt['draw_auc']>mc['draw_auc'],
        'trajectory_brier_not_worse_than_baseline':mt['hda_brier']<=mb['hda_brier'],
        'trajectory_rps_not_worse_than_baseline':mt['rps']<=mb['rps'],
        'trajectory_accuracy_delta_vs_baseline':(mt['accuracy']-mb['accuracy'])*100.0>=g['trajectory_accuracy_delta_vs_baseline_min_pp'],
        'no_threshold_or_subgroup_selection':True,
        'policy_prediction_hash_exact':True,'policy_results_exact':len(evaluated)==4312,'blind_result_values_accessed_zero':True,'model_fits_zero':True,
    }
    passed=all(gates.values()); status='PASS_R40I2_EXTERNAL_POLICY_GATE' if passed else 'STOP_R40I2_EXTERNAL_POLICY_GATE_BLIND_UNOPENED'
    out={'schema_version':reg['schema_version'],'status':status,'policy':{'count':4312,'identity_sha256':reg['frozen_policy']['identity_sha256'],'prediction_sha256':policy_pred_hash,'baseline':mb,'calibration':mc,'trajectory':mt,'deltas_trajectory_vs_baseline':{k:mt[k]-mb[k] for k in ('accuracy','hda_logloss','hda_brier','rps','draw_logloss','draw_auc')},'deltas_trajectory_vs_calibration':{k:mt[k]-mc[k] for k in ('accuracy','hda_logloss','hda_brier','rps','draw_logloss','draw_auc')}},'gates':gates,'access_audit':{'policy_result_values_accessed':4312,'blind_result_values_accessed':0,'model_fits':0,'thresholds_selected':0,'subgroup_selection':0,'internal_fifth_fixed100_accessed':0},'next_stage_authorization':'ONE_TIME_BLIND_EVALUATION_PREREGISTRATION_ALLOWED' if passed else 'CLOSE_CROSS_SOURCE_TRAJECTORY_BLIND_UNOPENED','hard_limits':reg['hard_limits']}
    (a.out_dir/'external_policy_evaluation_status_r40i2.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
