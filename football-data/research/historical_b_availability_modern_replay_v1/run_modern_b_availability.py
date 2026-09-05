#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, pathlib, statistics
from collections import defaultdict

EPS=1e-15
class ScoreError(RuntimeError): pass

def read_json(path): return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
def read_jsonl(path):
    with pathlib.Path(path).open('r',encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)
def write_json(path,obj): pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def write_jsonl(path,rows):
    with pathlib.Path(path).open('w',encoding='utf-8',newline='\n') as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()
def logit(p):
    p=min(max(float(p),1e-9),1-1e-9); return math.log(p/(1-p))
def sigmoid(z):
    z=max(-40.0,min(40.0,float(z))); return 1/(1+math.exp(-z))
def integrate(m):
    p=[0.0,0.0,0.0]
    for h,row in enumerate(m):
        for a,x in enumerate(row): p[0 if h>a else 1 if h==a else 2]+=float(x)
    return p
def region_rescale(m,base_p,target_p):
    out=[]
    for h,row in enumerate(m):
        rr=[]
        for a,val in enumerate(row):
            k=0 if h>a else 1 if h==a else 2; bp=max(EPS,float(base_p[k]))
            rr.append(float(val)*float(target_p[k])/bp)
        out.append(rr)
    z=sum(sum(r) for r in out)
    if not math.isfinite(z) or z<=0: raise ScoreError('invalid matrix normalization')
    return [[x/z for x in row] for row in out]
def b_predict(base_p,snap,coef):
    if not snap or not snap.get('active'): return [float(x) for x in base_p],False
    d=float(base_p[1]); denom=float(base_p[0])+float(base_p[2])
    if denom<=0: return [float(x) for x in base_p],False
    qh=float(base_p[0])/denom; edge=max(-3.0,min(3.0,float(snap['edge'])))
    q=sigmoid(logit(qh)+float(coef)*edge)
    return [(1-d)*q,d,(1-d)*(1-q)],True
def availability_predict(base_p,tilt):
    t=float(tilt); z=float(base_p[0])*math.exp(t)+float(base_p[1])+float(base_p[2])*math.exp(-t)
    return [float(base_p[0])*math.exp(t)/z,float(base_p[1])/z,float(base_p[2])*math.exp(-t)/z]

class State:
    __slots__=('deep','press','n')
    def __init__(self): self.deep=0.0; self.press=0.0; self.n=0
    def update(self,deep,press,alpha):
        if self.n==0: self.deep=float(deep); self.press=float(press)
        else:
            self.deep=(1-alpha)*self.deep+alpha*float(deep); self.press=(1-alpha)*self.press+alpha*float(press)
        self.n+=1

def build_snaps(process_rows,half):
    alpha=1-math.exp(math.log(0.5)/float(half)); bytime=defaultdict(list)
    for g in process_rows: bytime[str(g['datetime'])].append(g)
    states={}; snaps={}
    for when in sorted(bytime):
        batch=sorted(bytime[when],key=lambda x:str(x['fixture_id']))
        for g in batch:
            fid=str(g['fixture_id']); h=str(int(g['home_team_id'])); a=str(int(g['away_team_id']))
            hs=states.get(h); aws=states.get(a)
            if hs is None or aws is None or hs.n<1 or aws.n<1:
                snaps[fid]={'active':False,'reason':'missing_team_state'}; continue
            vals=[s for s in states.values() if s.n>=1]
            md=sum(s.deep for s in vals)/len(vals); mp=sum(s.press for s in vals)/len(vals)
            sd=math.sqrt(sum((s.deep-md)**2 for s in vals)/len(vals)); sp=math.sqrt(sum((s.press-mp)**2 for s in vals)/len(vals))
            if sd<=1e-9 or sp<=1e-9:
                snaps[fid]={'active':False,'reason':'zero_league_sd'}; continue
            hp=.5*((hs.deep-md)/sd)+.5*((hs.press-mp)/sp); ap=.5*((aws.deep-md)/sd)+.5*((aws.press-mp)/sp)
            snaps[fid]={'active':True,'edge':hp-ap,'home_process':hp,'away_process':ap,'home_n':hs.n,'away_n':aws.n,'league_state_n':len(vals)}
        for g in batch:
            hd=math.log1p(max(0.0,float(g['h_deep']))); ad=math.log1p(max(0.0,float(g['a_deep'])))
            hp=-math.log(max(1e-12,float(g['h_ppda']))); ap=-math.log(max(1e-12,float(g['a_ppda'])))
            h=str(int(g['home_team_id'])); a=str(int(g['away_team_id']))
            states.setdefault(h,State()).update(hd,hp,alpha); states.setdefault(a,State()).update(ad,ap,alpha)
    return snaps

def outcome_idx(hg,ag): return 0 if hg>ag else 1 if hg==ag else 2

def metric(rows,pkey,mkey=None):
    if not rows: raise ScoreError('empty metric cohort')
    ll=br=rps=score_ll=0.0; hit=0
    for r in rows:
        p=[float(x) for x in r[pkey]]; y=int(r['y'])
        ll-=math.log(max(EPS,p[y])); br+=sum((p[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
        c1=p[0]; c2=p[0]+p[1]; o1=1.0 if y==0 else 0.0; o2=1.0 if y in (0,1) else 0.0
        rps+=0.5*((c1-o1)**2+(c2-o2)**2); hit+=int(max(range(3),key=lambda i:p[i])==y)
        if mkey:
            hg=int(r['home_goals']); ag=int(r['away_goals'])
            if not (0<=hg<15 and 0<=ag<15): raise ScoreError('score outside 15x15 support')
            score_ll-=math.log(max(EPS,float(r[mkey][hg][ag])))
    n=len(rows); out={'n':n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_accuracy':hit/n,'top1_correct':hit}
    if mkey: out['exact_score_logloss']=score_ll/n
    return out

def paired_required_n(rows):
    vals=[]
    for r in rows:
        y=int(r['y']); vals.append(-math.log(max(EPS,float(r['baseline_b_p'][y]))) + math.log(max(EPS,float(r['candidate_p'][y]))))
    effect=sum(vals)/len(vals); sd=statistics.stdev(vals) if len(vals)>1 else 0.0
    z=1.959963984540054+0.8416212335729143; req=None if effect<=0 or sd<=0 else math.ceil((z*sd/effect)**2)
    return {'mean_logloss_improvement':effect,'paired_sd':sd,'required_n':req}
def eval_rows(rows):
    b=metric(rows,'baseline_b_p','baseline_b_matrix'); c=metric(rows,'candidate_p','candidate_matrix')
    return {'n':len(rows),'baseline_b':b,'candidate_b_plus_availability':c,
      'deltas':{'one_x_two_logloss_gain':b['logloss']-c['logloss'],'one_x_two_brier_delta':c['brier']-b['brier'],'one_x_two_rps_delta':c['rps']-b['rps'],'top1_delta':c['top1_accuracy']-b['top1_accuracy'],'top1_net_correct':c['top1_correct']-b['top1_correct'],'exact_score_logloss_gain':b['exact_score_logloss']-c['exact_score_logloss']},
      'paired_one_x_two':paired_required_n(rows)}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','process-dir','tilt-dir','stress-dir','history-dir','out'): ap.add_argument('--'+x,required=True)
    a=ap.parse_args(); c=read_json(a.contract)
    if c['status']!='FROZEN_BEFORE_B_ON_AVAILABILITY_COMBINATION_SCORING': raise ScoreError('contract status drift')
    auth=c['authorization']
    if not auth['outcome_scoring_allowed'] or any(auth[k] for k in ('training_allowed','tuning_allowed','parameter_search_allowed','candidate_selection_allowed','formal_weight_change_allowed','CURRENT_change_allowed','production_pointer_change_allowed','formal_enablement_change_allowed','promotion_allowed')): raise ScoreError('authorization drift')
    out=pathlib.Path(a.out); out.mkdir(parents=True,exist_ok=True)
    process_file=pathlib.Path(getattr(a,'process_dir'))/c['frozen_inputs']['modern_process']['process_file']
    tilt_file=pathlib.Path(getattr(a,'tilt_dir'))/c['frozen_inputs']['fplcache_temporal_tilt']['prediction_file']
    stress_file=pathlib.Path(getattr(a,'stress_dir'))/c['frozen_inputs']['v3_1_1_historical_stress']['prediction_file']
    label_file=pathlib.Path(getattr(a,'history_dir'))/c['frozen_inputs']['completed_history_labels']['label_file']
    if sha_file(process_file)!=c['frozen_inputs']['modern_process']['dataset_sha256']: raise ScoreError('modern process dataset SHA drift')
    process_rows=list(read_jsonl(process_file)); tilt_rows=list(read_jsonl(tilt_file)); stress_rows=[r for r in read_jsonl(stress_file) if r.get('league')=='EPL' and int(r.get('season')) in (2024,2025)]
    if len(process_rows)!=760 or len(tilt_rows)!=760 or len(stress_rows)!=760: raise ScoreError(f'input row count drift {len(process_rows)} {len(tilt_rows)} {len(stress_rows)}')
    pids={str(r['fixture_id']) for r in process_rows}; tmap={str(r['fixture_id']):r for r in tilt_rows}; smap={str(r['fixture_id']):r for r in stress_rows}
    if len(tmap)!=760 or len(smap)!=760 or pids!=set(tmap) or pids!=set(smap): raise ScoreError('fixture identity sets do not match 760/760')
    primary_ids={fid for fid,r in tmap.items() if bool(r.get('primary_exact_kickoff_identity'))}
    if len(primary_ids)!=int(c['scope']['primary_exact_kickoff_count']): raise ScoreError(f'primary identity drift {len(primary_ids)}')
    snaps=build_snaps(process_rows,float(c['stage6_b_transport']['half_life_matches']))
    b_active_all=sum(bool(snaps[fid].get('active')) for fid in pids); b_active_primary=sum(bool(snaps[fid].get('active')) for fid in primary_ids)
    if b_active_all!=int(c['scope']['expected_b_active_all760']) or b_active_primary!=int(c['scope']['expected_b_active_primary713']): raise ScoreError(f'B active drift {b_active_all} {b_active_primary}')
    pred_rows=[]; max_b_matrix_err=max_c_matrix_err=max_tilt=max_b_delta=max_avail_delta=0.0; coef=float(c['stage6_b_transport']['bridge_coefficient'])
    for g in sorted(process_rows,key=lambda r:(str(r['datetime']),str(r['fixture_id']))):
        fid=str(g['fixture_id']); pr=smap[fid]; tr=tmap[fid]; v=[float(x) for x in pr['v3_1_1_1x2']]; vm=pr['v3_1_1_matrix']
        bp,on=b_predict(v,snaps[fid],coef); bm=region_rescale(vm,v,bp); bi=integrate(bm)
        max_b_matrix_err=max(max_b_matrix_err,max(abs(bi[i]-bp[i]) for i in range(3))); max_b_delta=max(max_b_delta,max(abs(bp[i]-v[i]) for i in range(3)))
        tilt=float(tr['effective_tilt']); max_tilt=max(max_tilt,abs(tilt)); cp=availability_predict(bp,tilt); cm=region_rescale(bm,bp,cp); ci=integrate(cm)
        max_c_matrix_err=max(max_c_matrix_err,max(abs(ci[i]-cp[i]) for i in range(3))); max_avail_delta=max(max_avail_delta,max(abs(cp[i]-bp[i]) for i in range(3)))
        pred_rows.append({'fixture_id':fid,'season':int(g['season_start_year']),'home_team':g['home_team'],'away_team':g['away_team'],'kickoff':g['datetime'],'primary_exact_kickoff_identity':fid in primary_ids,'b_active':bool(on),'b_edge':float(snaps[fid].get('edge',0.0)) if on else None,'v3_1_1_p':v,'baseline_b_p':bp,'candidate_p':cp,'baseline_b_matrix':bm,'candidate_matrix':cm,'effective_tilt':tilt})
    label_free=[{k:v for k,v in r.items() if k not in ('baseline_b_matrix','candidate_matrix')} for r in pred_rows]
    write_jsonl(out/'predictions_label_free.jsonl',label_free); prediction_sha=sha_file(out/'predictions_label_free.jsonl')
    labels={str(r['fixture_id']):r for r in read_jsonl(label_file) if str(r['fixture_id']) in pids}
    if len(labels)!=760: raise ScoreError(f'label count drift {len(labels)}')
    scored=[]
    for r in pred_rows:
        lab=labels[r['fixture_id']]; x=dict(r); x['home_goals']=int(lab['home_goals']); x['away_goals']=int(lab['away_goals']); x['y']=outcome_idx(x['home_goals'],x['away_goals']); scored.append(x)
    primary=[r for r in scored if r['primary_exact_kickoff_identity']]; pooled=eval_rows(primary); blocks=[]; ll_ok=top_ok=True; active_by_season={}
    for s in (2024,2025):
        rr=[r for r in primary if r['season']==s]; e=eval_rows(rr); lok=e['deltas']['one_x_two_logloss_gain']>=-1e-15; tok=e['deltas']['top1_net_correct']>=0; ll_ok &= lok; top_ok &= tok; active_by_season[str(s)]=sum(r['b_active'] for r in rr); blocks.append({'season':s,**e,'logloss_nondegrade':lok,'top1_nondegrade':tok})
    diag=[]
    for r in primary:
        x=dict(r); x['candidate_p']=x['baseline_b_p']; x['candidate_matrix']=x['baseline_b_matrix']; x['baseline_b_p']=x['v3_1_1_p']; x['baseline_b_matrix']=smap[x['fixture_id']]['v3_1_1_matrix']; diag.append(x)
    b_vs_v311=eval_rows(diag); g=c['development_gates']; d=pooled['deltas']
    gates={'primary_fixture_count_exact':len(primary)==int(g['primary_fixture_count_exact']),'b_active_primary_exact':b_active_primary==int(g['b_active_primary_exact']),'b_active_primary_by_season_exact':active_by_season=={k:int(v) for k,v in c['scope']['expected_b_active_primary_by_season'].items()},'pooled_1x2_logloss_gain':d['one_x_two_logloss_gain']>=float(g['pooled_1x2_logloss_gain_min'])-1e-15,'pooled_1x2_brier_delta':d['one_x_two_brier_delta']<=float(g['pooled_1x2_brier_delta_max'])+1e-15,'pooled_1x2_rps_delta':d['one_x_two_rps_delta']<=float(g['pooled_1x2_rps_delta_max'])+1e-15,'pooled_top1_net_correct':d['top1_net_correct']>=int(g['pooled_top1_net_correct_min']),'both_season_blocks_1x2_logloss_nondegrade':ll_ok,'both_season_blocks_top1_nondegrade':top_ok,'exact_score_gain_identity':abs(d['exact_score_logloss_gain']-d['one_x_two_logloss_gain'])<=float(g['exact_score_logloss_gain_must_equal_1x2_logloss_gain_within']),'matrix_to_b_1x2':max_b_matrix_err<=float(g['matrix_to_b_1x2_max_abs_diff']),'matrix_to_b_plus_availability_1x2':max_c_matrix_err<=float(g['matrix_to_b_plus_availability_1x2_max_abs_diff']),'availability_effective_tilt_bound':max_tilt<=float(g['availability_effective_tilt_abs_max'])+1e-15}
    status=c['terminal']['pass'] if all(gates.values()) else c['terminal']['fail']
    result={'schema_version':'football3-modern-b-plus-fplcache-availability-dev-result-v1','status':status,'classification':c['classification'],'research_only':True,'fresh_confirmation':False,'promotion_allowed':False,'training_performed':False,'tuning_performed':False,'parameter_search_performed':False,'candidate_selection_performed':False,'historical_confirmation_2023_read':False,'prospective_1335_touched':False,'formal_v2_changed':False,'frozen_v3_1_1_changed':False,'stage6_b_frozen_candidate_changed':False,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'input_counts':{'process':len(process_rows),'tilt':len(tilt_rows),'stress_epl':len(stress_rows),'labels':len(labels)},'prediction_label_free_sha256':prediction_sha,'b_coverage':{'active_all760':b_active_all,'active_primary713':b_active_primary,'active_primary_by_season':active_by_season},'primary_b_plus_availability':pooled,'primary_season_blocks':blocks,'diagnostic_b_vs_v3_1_1':b_vs_v311,'max_b_probability_abs_delta_vs_v311':max_b_delta,'max_availability_probability_abs_delta_vs_b':max_avail_delta,'max_effective_tilt_abs':max_tilt,'matrix_to_b_1x2_max_abs_diff':max_b_matrix_err,'matrix_to_candidate_1x2_max_abs_diff':max_c_matrix_err,'gates':gates,'contract_sha256':sha_file(a.contract)}
    write_json(out/'development_score_result.json',result); write_json(out/'season_blocks.json',blocks)
    (out/'artifact_slug.txt').write_text(f"{status}__n_{len(primary)}__x1gain_{d['one_x_two_logloss_gain']:.6f}__top1net_{d['top1_net_correct']}__bactive_{b_active_primary}\n",encoding='utf-8')
    print(json.dumps({'status':status,'primary_n':len(primary),'b_active_primary':b_active_primary,'x1gain':d['one_x_two_logloss_gain'],'brier_delta':d['one_x_two_brier_delta'],'rps_delta':d['one_x_two_rps_delta'],'top1_net_correct':d['top1_net_correct'],'top1_delta':d['top1_delta'],'required_n':pooled['paired_one_x_two']['required_n'],'b_vs_v311_x1gain':b_vs_v311['deltas']['one_x_two_logloss_gain'],'b_vs_v311_top1_net_correct':b_vs_v311['deltas']['top1_net_correct'],'gates':gates},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
