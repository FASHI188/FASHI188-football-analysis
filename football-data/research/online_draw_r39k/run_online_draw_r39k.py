#!/usr/bin/env python3
from __future__ import annotations
import argparse,importlib.util,json
from datetime import datetime,timezone
from pathlib import Path

def load_core():
    p=Path(__file__).with_name('evaluate_online_draw_r39k.py')
    spec=importlib.util.spec_from_file_location('r39k_core',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--market-dir',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args();c=load_core()
    if a.self_test:
        c.self_test();print('PASS_R39K_BOUND_RUNNER_SELF_TEST');return
    pre=json.loads(a.prereg.read_text());assert pre['preregistration_status']=='MODEL_AND_PREEXISTING_HOLDOUT_FROZEN'
    rows=c.load_market(a.market_dir);pre_rows=sorted([r for r in rows if r['season'] in c.PRE_SEASONS],key=lambda r:(r['date'],r['div'],r['home'],r['away'],r['identity']));hold=sorted([r for r in rows if r['season']=='2526'],key=lambda r:(r['date'],r['div'],r['home'],r['away'],r['identity']))
    if len(pre_rows)!=pre['source_binding']['complete_preholdout_rows'] or len(hold)!=pre['source_binding']['complete_2526_rows']:raise RuntimeError(f'source count drift pre={len(pre_rows)} hold={len(hold)}')
    # The blind set predates R39K. No R39K sample reselection is permitted.
    fixed=sorted(hold,key=lambda r:c.htxt(f"51146|{r['identity']}"))[:100];sha=c.set_sha([r['identity'] for r in fixed])
    if sha!=pre['source_binding']['fixed100_identity_sha256']:raise RuntimeError(f'pre-existing fixed100 identity drift {sha}')
    cut_rows=[r for r in pre_rows if r['season']=='1920'];cuts=c.quantile_cutpoints(cut_rows)
    # Preholdout labels only. No 2025/26 file is opened by this call.
    lab=c.load_labels(a.raw_dir,c.PRE_SEASONS)
    if any(r['identity'] not in lab for r in pre_rows):raise RuntimeError('missing preholdout labels')
    gsel,gboard=c.choose_half(pre_rows,lab,cuts,'global',pre);rsel,rboard=c.choose_half(pre_rows,lab,cuts,'regime',pre)
    devseas=set(pre['hyperparameter_selection']['development_score_seasons']);dev=[r for r in pre_rows if r['season'] in devseas];mdev=c.metric_pack(dev,c.market_pred(dev),lab);grows=[r for r in pre_rows if r['season'] in ({'1920'}|devseas)];gpred,_,_=c.simulate(grows,lab,cuts,gsel['half_life_days'],'global',pre);rpred,_,_=c.simulate(grows,lab,cuts,rsel['half_life_days'],'regime',pre);gdev=c.metric_pack(dev,gpred,lab);rdev=c.metric_pack(dev,rpred,lab)
    devpass=rdev['HDA_LogLoss']<mdev['HDA_LogLoss'] and rdev['binary_Draw_LogLoss']<mdev['binary_Draw_LogLoss'] and rdev['HDA_LogLoss']<gdev['HDA_LogLoss']
    outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_counts':{'preholdout':len(pre_rows),'holdout_pool':len(hold)},'fixed100_origin':'pre-existing R39J blind set','fixed100_identity_sha256':sha,'regime_cutpoints_1920_market_only':cuts,'selected_half_lives':{'global':gsel['half_life_days'],'regime':rsel['half_life_days']},'candidate_leaderboards':{'global':gboard,'regime':rboard},'development':{'market':mdev,'global_online':gdev,'regime_online':rdev,'gate_pass':devpass},'training_labels_accessed':len(pre_rows),'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not devpass:
        c.write_stop(outdir,base,pre['hyperparameter_selection']['development_gate_for_regime']['if_fail'],{'development_pass':False,'selected_half_lives':base['selected_half_lives'],'fixed100_identity_sha256':sha});return
    gp,gdiag,gstate=c.simulate(pre_rows,lab,cuts,gsel['half_life_days'],'global',pre);rp,rdiag,rstate=c.simulate(pre_rows,lab,cuts,rsel['half_life_days'],'regime',pre);mp=c.market_pred(pre_rows)
    val=[r for r in pre_rows if r['season']==pre['confirmation_windows']['validation_season']];pol=[r for r in pre_rows if r['season']==pre['confirmation_windows']['policy_season']]
    v=c.segment_gate(val,mp,gp,rp,rdiag,lab,pre['validation_gate_all_required']);base['validation']=v
    if not v['gate_pass']:
        c.write_stop(outdir,base,pre['validation_gate_all_required']['if_fail'],{'development_pass':True,'validation_pass':False,'selected_half_lives':base['selected_half_lives'],'fixed100_identity_sha256':sha});return
    p=c.segment_gate(pol,mp,gp,rp,rdiag,lab,pre['policy_gate_all_required']);base['policy']=p
    if not p['gate_pass']:
        c.write_stop(outdir,base,pre['policy_gate_all_required']['if_fail'],{'development_pass':True,'validation_pass':True,'policy_pass':False,'selected_half_lives':base['selected_half_lives'],'fixed100_identity_sha256':sha});return
    freeze={'final_freeze_completed':True,'holdout_labels_accessed_before_freeze':0,'development_pass':True,'validation_pass':True,'policy_pass':True,'selected_half_lives':base['selected_half_lives'],'regime_cutpoints':cuts,'global_state_sha256':c.state_sha(gstate),'regime_state_sha256':c.state_sha(rstate),'fixed100_identity_sha256':sha,'frozen_at_utc':datetime.now(timezone.utc).isoformat()};(outdir/'freeze_receipt_r39k.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2))
    # Only after the freeze receipt exists: access exactly the pre-existing locked 100 labels.
    ids={r['identity'] for r in fixed};hlab=c.load_labels(a.raw_dir,{'2526'},ids)
    if set(hlab)!=ids:raise RuntimeError(f'holdout label access mismatch {len(hlab)} != 100')
    hg,hgd,_=c.simulate(fixed,hlab,cuts,gsel['half_life_days'],'global',pre,gstate);hr,hrd,_=c.simulate(fixed,hlab,cuts,rsel['half_life_days'],'regime',pre,rstate);hm=c.market_pred(fixed)
    hmarket=c.metric_pack(fixed,hm,hlab);hglobal=c.metric_pack(fixed,hg,hlab);hreg=c.metric_pack(fixed,hr,hlab);hpass=(hreg['HDA_LogLoss']<hmarket['HDA_LogLoss'] and hreg['binary_Draw_LogLoss']<hmarket['binary_Draw_LogLoss'] and hreg['HDA_Brier']<=hmarket['HDA_Brier'] and hreg['RPS']<=hmarket['RPS'] and hreg['HDA_LogLoss']<hglobal['HDA_LogLoss'] and hreg['binary_Draw_LogLoss']<hglobal['binary_Draw_LogLoss'])
    base['holdout']={'market':hmarket,'global_online':hglobal,'regime_online':hreg,'regime_cap_hit_fraction':c.cap_fraction(fixed,hrd),'gate_pass':hpass};base['holdout_labels_accessed']=100;base['status']=pre['blind_holdout_protocol']['pass_status'] if hpass else pre['blind_holdout_protocol']['fail_status'];(outdir/'r39k_result.json').write_text(json.dumps(base,ensure_ascii=False,indent=2));print(json.dumps({'status':base['status'],'selected_half_lives':base['selected_half_lives'],'validation':v,'policy':p,'holdout':base['holdout'],'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
