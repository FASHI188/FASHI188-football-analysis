from __future__ import annotations

import argparse,hashlib,importlib,json,math,pathlib,re,statistics,sys
from collections import defaultdict
from datetime import timedelta
from typing import Any

ROOT=pathlib.Path('.').resolve();CTX=ROOT/'football-data/research/context_translator_v1';sys.path.insert(0,str(CTX))
import source_ingest as si
from candidate_b import build_probability_mass_scenarios,capability_residual,candidate_contract
from player_strength import estimate_player_vectors
from test_translator import matrix_mean

V2_RUN=33348991436
V2_HEAD='ef830299e8ee37749ac083e007b4947f8e72d7b7'
V2_PRED_SHA='92dc38866e6e46b167ed6bf0bcfc6f6e0e8b85e57e68cb3a571d3c44fc9461a7'
SB_COMMIT='b0bc9f22dd77c206ddedc1d742893b3bbe64baec'
POOL_SHA='2b4fe4be50cf4639624a2e31b3a7651ffc01ccf8afe6b7d58845fa4871aa4334'
OLD_PRED_SHA='313cdf1c449fc23a6ac6820e9cf71883e83305e9f3f981034ed48e255835899d'
N=272
MODELS=('baseline','old_l1_l2','candidate_b1','candidate_b1_b2')
OUTCOMES=('home','draw','away')


def sha(p):
    h=hashlib.sha256()
    with pathlib.Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def canon(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def readjl(p):return [json.loads(x) for x in pathlib.Path(p).read_text().splitlines() if x.strip()]
def dump(p,x):pathlib.Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n')

def norm_matrix(mat):
    vals=[float(v) for r in mat for v in r];s=sum(vals)
    if not mat or not mat[0] or s<=0 or any((not math.isfinite(v) or v<0) for v in vals):raise RuntimeError('invalid matrix')
    return [[float(v)/s for v in r] for r in mat]

def pred(mat,eng):
    m=norm_matrix(mat);p=eng.matrix_1x2(m);z={'p_home':float(p['home']),'p_draw':float(p['draw']),'p_away':float(p['away']),'score_matrix':m}
    if abs(z['p_home']+z['p_draw']+z['p_away']-1)>1e-8:raise RuntimeError('1x2 mass')
    return z

def mix(items,eng):
    total=sum(w for _,w in items);base=items[0][0]['score_matrix'];out=[[0.]*len(base[0]) for _ in base]
    for p,w0 in items:
        w=w0/total
        for i,r in enumerate(p['score_matrix']):
            for j,v in enumerate(r):out[i][j]+=w*float(v)
    return pred(out,eng)
def move(a,b):return [float(a['p_home'])-float(b['p_home']),float(a['p_draw'])-float(b['p_draw']),float(a['p_away'])-float(b['p_away'])]
def vnorm(v):return math.sqrt(sum(x*x for x in v))
def meanmove(a,b):return sum(abs(x) for x in move(a,b))/3


class History:
    def __init__(self,pairs):
        self.events=[];self.segments=[];self.usage=defaultdict(list);self.evidence={};self.i=0;self.queue=[]
        for r,s in pairs:
            rel=si._iso(si._dt(r['cutoff'],'cutoff')+timedelta(hours=si.RELEASE_HOURS));self.queue.append({'release_at':rel,'r':r,'s':s})
        self.queue.sort(key=lambda x:(x['release_at'],x['s']['match_id']))
    def release_before(self,cutoff):
        co=si._dt(cutoff,'cutoff')
        while self.i<len(self.queue) and si._dt(self.queue[self.i]['release_at'],'release_at')<co:
            x=self.queue[self.i];self.i+=1;s=x['s'];mid=int(s['match_id']);er=f'data/events/{mid}.json';lr=f'data/lineups/{mid}.json';erb,lrb=si._get(er),si._get(lr)
            self.evidence[er]={'sha256':hashlib.sha256(erb).hexdigest(),'bytes':len(erb)};self.evidence[lr]={'sha256':hashlib.sha256(lrb).hexdigest(),'bytes':len(lrb)}
            rows,u,seg=si._history(json.loads(erb),json.loads(lrb),x['release_at'],str(s['home_team_id']),str(s['away_team_id']));self.events.extend(rows);self.segments.append(seg)
            for tid,players in u.items():self.usage[tid].append({'players':players,'known_at':x['release_at'],'match_id':mid})

def team_map(pairs):
    a={};rev={}
    for r,s in pairs:
        for vid,sid in ((str(r['home_team_id']),str(s['home_team_id'])),(str(r['away_team_id']),str(s['away_team_id']))):
            if vid in a and a[vid]!=sid:raise RuntimeError('V2->StatsBomb identity collision')
            if sid in rev and rev[sid]!=vid:raise RuntimeError('StatsBomb->V2 identity collision')
            a[vid]=sid;rev[sid]=vid
    return a

def engine():
    p=ROOT/'football-data/new_engine_v2_joint_score';sys.path.insert(0,str(p));sys.modules.pop('engine',None)
    try:return importlib.import_module('engine')
    finally:sys.path.pop(0)
def effect_prediction(base,effect,lock,eng):
    if not effect.active:return pred(base,eng)
    bh,ba=matrix_mean(base);feat={'mu_home':max(1e-8,bh*math.exp(effect.log_mu_home_delta)),'mu_away':max(1e-8,ba*math.exp(effect.log_mu_away_delta)),'home_evidence':10.,'away_evidence':10.}
    m=eng.joint_matrix(lock['joint_family'],feat,dispersion_home=float(lock.get('dispersion_home',50.)),dispersion_away=float(lock.get('dispersion_away',50.)),dependence=float(lock['dependence']),max_goals=int(lock['max_goals']))
    return pred(m,eng)


def predict_phase(v2,prior,source,out):
    out.mkdir(parents=True,exist_ok=True);am=json.load(open(v2/'artifact_manifest.json'))
    assert am['run_id']==V2_RUN and am['head']==V2_HEAD and am['prediction_sha256']==V2_PRED_SHA
    if (v2/'dataset/evaluation_label_vault.jsonl').exists():raise RuntimeError('labels present during prediction')
    inv=json.load(open(source/'statsbomb_source_inventory_receipt.json'));assert inv['exact_commit']==si.SB_COMMIT==SB_COMMIT
    pool=json.load(open(prior/'blind_pool_manifest.json'));assert pool['pool_sha256']==POOL_SHA and pool['n']==N
    oldp=prior/'blind_predictions.jsonl';assert sha(oldp)==OLD_PRED_SHA;old=readjl(oldp);ids=[str(x['fixture_id']) for x in pool['rows']];assert [str(x['fixture_id']) for x in old]==ids
    _,mapped=si._map_inventory(v2,source,out);ev=readjl(v2/'dataset/evaluation_features.jsonl');eids={str(x['fixture_id']) for x in ev}
    gp=[(r,s) for r,s in mapped if str(r['fixture_id']) in eids and str(r['competition_id'])=='GER1' and si._season(str(r['season']))=='2023/24']
    if not gp:raise RuntimeError('no GER1 source mapping')
    tm=team_map(gp);hist=History(gp);eng=engine();lock=json.load(open(v2/'locks/v2_lock.json'));rows=[]
    for o in old:
        hist.release_before(o['cutoff']);base=o['baseline'];b1=pred(base['score_matrix'],eng);b12=pred(base['score_matrix'],eng);b1a=b2a=b12a=False;reason=None;e1=None;scdump=[];edump=[]
        ht=tm.get(str(o['home_team_id']));at=tm.get(str(o['away_team_id']))
        if not ht or not at:reason='IDENTITY_FALLBACK'
        else:
            pe=[e for e in hist.events if str(e['team_id']) in {ht,at}];vectors=estimate_player_vectors(pe,hist.segments,as_of=o['cutoff']) if pe else {};he=si._expected(ht,hist.usage,o['cutoff']);ae=si._expected(at,hist.usage,o['cutoff']);sc=build_probability_mass_scenarios(he,ae,cutoff=o['cutoff']) if he and ae else []
            if not vectors or not sc:reason='INSUFFICIENT_SHARED_PIT_DATA'
            else:
                m=sc[0];e1=capability_residual(vectors=vectors,usage=hist.usage,home_team_id=ht,away_team_id=at,home_player_ids=m.home_player_ids,away_player_ids=m.away_player_ids,cutoff=o['cutoff'])
                if e1.active:b1=effect_prediction(base['score_matrix'],e1,lock,eng);b1a=True
                else:reason=e1.reason
                if b1a and len(sc)>=2:
                    items=[];ok=True
                    for s in sc:
                        e=capability_residual(vectors=vectors,usage=hist.usage,home_team_id=ht,away_team_id=at,home_player_ids=s.home_player_ids,away_player_ids=s.away_player_ids,cutoff=o['cutoff']);scdump.append(s.to_dict());edump.append({'scenario_id':s.scenario_id,'probability':s.probability,'effect':e.to_dict()})
                        if not e.active:ok=False;break
                        items.append((effect_prediction(base['score_matrix'],e,lock,eng),s.probability))
                    if ok and len(items)==len(sc):b12=mix(items,eng);b2a=b12a=True
                    else:reason='B2_SCENARIO_RESIDUAL_INSUFFICIENT_EXACT_V2_FALLBACK'
                elif b1a:reason='B2_MASS_INSUFFICIENT_EXACT_V2_FALLBACK'
        rows.append({'fixture_id':str(o['fixture_id']),'competition_id':o['competition_id'],'season':o['season'],'cutoff':o['cutoff'],'home_team_id':o['home_team_id'],'away_team_id':o['away_team_id'],'shared_cold_start_bucket':o.get('shared_cold_start_bucket'),'research_status':'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC','baseline':base,'old_l1_l2':o['candidate'],'candidate_b1':b1,'candidate_b1_b2':b12,'old_l1_l2_signal_active':bool(o.get('translator_signal_active')),'b1_active':b1a,'b2_active':b2a,'b12_active':b12a,'fallback_reason':reason,'b1_effect':None if e1 is None else e1.to_dict(),'b2_scenarios':scdump,'b2_effects':edump})
    assert len(rows)==N and [r['fixture_id'] for r in rows]==ids
    pp=out/'candidate_b_predictions.jsonl';pp.write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for r in rows))
    moves={m:[meanmove(r[m],r['baseline']) for r in rows] for m in ('old_l1_l2','candidate_b1','candidate_b1_b2')};opp=den=cancel=0;fr=[]
    for r in rows:
        v1=move(r['candidate_b1'],r['baseline']);v2=move(r['candidate_b1_b2'],r['candidate_b1']);vf=move(r['candidate_b1_b2'],r['baseline'])
        if r['b1_active'] and r['b12_active'] and vnorm(v1)>1e-14 and vnorm(v2)>1e-14:
            den+=1
            if sum(a*b for a,b in zip(v1,v2))<0:
                opp+=1
                if vnorm(vf)<vnorm(v1):cancel+=1;fr.append(1-vnorm(vf)/vnorm(v1))
    reasons=defaultdict(int)
    for r in rows:
        if r['fallback_reason']:reasons[r['fallback_reason']]+=1
    pre={'schema_version':'football3-context-translator-candidate-b-pre-score-v1','status':'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC','formal_promotion_eligible':False,'formal_weight':0,'labels_read_in_prediction_phase':False,'n':N,'allowed_fixture_pool_sha256':POOL_SHA,'allowed_fixture_set_sha256':canon(sorted(ids)),'prior_failed_prediction_sha256':OLD_PRED_SHA,'candidate_b_prediction_sha256':sha(pp),'contract':candidate_contract(),'trigger':{'old_l1_l2_n':sum(r['old_l1_l2_signal_active'] for r in rows),'candidate_b1_n':sum(r['b1_active'] for r in rows),'candidate_b2_redistribution_n':sum(r['b2_active'] for r in rows),'candidate_b1_b2_n':sum(r['b12_active'] for r in rows),'candidate_b1_fallback_n':sum(not r['b1_active'] for r in rows),'candidate_b1_b2_fallback_n':sum(not r['b12_active'] for r in rows),'fallback_reasons':dict(sorted(reasons.items()))},'mean_probability_move_vs_baseline':{m:statistics.fmean(v) if v else 0. for m,v in moves.items()},'direction_diagnostics':{'b1_b2_opposite_direction_n':opp,'b1_b2_direction_comparable_n':den,'b1_b2_opposite_direction_rate':None if not den else opp/den,'post_stack_cancellation_n':cancel,'post_stack_cancellation_rate_on_comparable':None if not den else cancel/den,'mean_cancellation_fraction_when_present':None if not fr else statistics.fmean(fr)},'source':{'statsbomb_exact_commit':si.SB_COMMIT,'release_assumption_hours':si.RELEASE_HOURS,'source_raw_in_artifact':False}}
    dump(out/'candidate_contract.json',candidate_contract());dump(out/'pre_score_diagnostic.json',pre);dump(out/'prior_source_evidence_manifest.json',{'schema_version':'football3-candidate-b-source-evidence-v1','evidence':hist.evidence});print(json.dumps({'prediction_sha256':pre['candidate_b_prediction_sha256'],'trigger':pre['trigger'],'direction':pre['direction_diagnostics']},indent=2))


def outcome(l):
    h=int(l['home_goals']);a=int(l['away_goals']);return 'home' if h>a else 'draw' if h==a else 'away'
def probs(p):
    z={'home':float(p['p_home']),'draw':float(p['p_draw']),'away':float(p['p_away'])}
    if any(v<0 or not math.isfinite(v) for v in z.values()) or abs(sum(z.values())-1)>1e-8:raise RuntimeError('bad probabilities')
    return z
def metrics(ordered,model):
    if not ordered:return {'n':0,'logloss':None,'brier':None,'rps':None,'top1':None,'mean_p_draw':None}
    ll=br=rp=top=pd=0.
    for r,l in ordered:
        p=probs(r[model]);y=outcome(l);ll+=-math.log(max(1e-15,p[y]));br+=sum((p[k]-(1. if y==k else 0.))**2 for k in OUTCOMES);rp+=((p['home']-(1. if y=='home' else 0.))**2+((p['home']+p['draw'])-(1. if y in {'home','draw'} else 0.))**2)/2;top+=max(OUTCOMES,key=lambda k:p[k])==y;pd+=p['draw']
    n=len(ordered);return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rp/n,'top1':top/n,'mean_p_draw':pd/n}
def allowed_labels(path,allowed):
    rx=re.compile(r'"fixture_id"\s*:\s*"([^"]+)"');out={}
    with pathlib.Path(path).open() as f:
        for line in f:
            m=rx.search(line)
            if not m:raise RuntimeError('label row missing fixture_id')
            if m.group(1) not in allowed:continue
            z=json.loads(line);out[str(z['fixture_id'])]=z
    if set(out)!=allowed:raise RuntimeError(f'label whitelist incomplete {len(out)} {len(allowed)}')
    return out
def group(rows,labs,fn):return [(r,labs[r['fixture_id']]) for r in rows if fn(r,labs[r['fixture_id']])]
def scored(name,o):return {'group':name,'n':len(o),'models':{m:metrics(o,m) for m in MODELS}}


def score_phase(prior,label,out):
    pre=json.load(open(out/'pre_score_diagnostic.json'));pp=out/'candidate_b_predictions.jsonl';assert sha(pp)==pre['candidate_b_prediction_sha256'] and pre['labels_read_in_prediction_phase'] is False
    pool=json.load(open(prior/'blind_pool_manifest.json'));assert pool['pool_sha256']==POOL_SHA and pool['n']==N;rows=readjl(pp);ids=[str(x['fixture_id']) for x in pool['rows']];assert [r['fixture_id'] for r in rows]==ids
    labs=allowed_labels(label,set(ids));ordered=[(r,labs[r['fixture_id']]) for r in rows]
    for r,l in ordered:
        if str(r['cutoff'])!=str(l['cutoff']):raise RuntimeError('cutoff mismatch')
    allm={m:metrics(ordered,m) for m in MODELS};b=allm['baseline'];delta={m:{k:allm[m][k]-b[k] for k in ('logloss','brier','rps','top1')} for m in MODELS if m!='baseline'}
    groups={'actual_draw':scored('actual_draw',group(rows,labs,lambda r,l:outcome(l)=='draw')),'weak_team_win':scored('weak_team_win',group(rows,labs,lambda r,l:outcome(l) in {'home','away'} and outcome(l)==('home' if float(r['baseline']['p_home'])<float(r['baseline']['p_away']) else 'away')))}
    for y in OUTCOMES:groups['actual_'+y]=scored('actual_'+y,group(rows,labs,lambda r,l,y=y:outcome(l)==y))
    for y in OUTCOMES:groups['baseline_top1_'+y]=scored('baseline_top1_'+y,group(rows,labs,lambda r,l,y=y:max(OUTCOMES,key=lambda k:probs(r['baseline'])[k])==y))
    for bucket in sorted({str(r.get('shared_cold_start_bucket')) for r in rows}):groups['cold_start_'+bucket]=scored('cold_start_'+bucket,group(rows,labs,lambda r,l,b=bucket:str(r.get('shared_cold_start_bucket'))==b))
    for name,key,val in [('candidate_b1_active','b1_active',True),('candidate_b1_fallback','b1_active',False),('candidate_b1_b2_active','b12_active',True),('candidate_b1_b2_fallback','b12_active',False)]:groups[name]=scored(name,group(rows,labs,lambda r,l,key=key,val=val:bool(r[key]) is val))
    result={'schema_version':'football3-context-translator-candidate-b-post-view-diagnostic-v1','status':'POST_VIEW_DIAGNOSTIC','research_only':True,'formal_promotion_eligible':False,'formal_weight':0,'scientific_claim':'POST_VIEW_DIAGNOSTIC_ONLY_NOT_BLIND_NOT_CONFIRMATION_NOT_FORMAL_EVIDENCE','n':N,'allowed_pool_sha256':POOL_SHA,'labels_parsed_only_for_allowed_unsealed_fixture_ids':True,'new_fixture_labels_parsed_n':0,'models':allm,'delta_vs_protected_v2':delta,'operations':{'trigger_and_fallback':pre['trigger'],'mean_probability_move_vs_baseline':pre['mean_probability_move_vs_baseline'],'direction_diagnostics':pre['direction_diagnostics']},'subgroups':groups,'protected_v2_modified':False,'main_modified':False,'current_modified':False,'airtable_modified':False,'formal_enablement':False,'promotion_decision':'NOT_REQUESTED_AND_NOT_PERMITTED'}
    sp=out/'candidate_b_score.json';dump(sp,result);gate={'schema_version':'football3-context-translator-candidate-b-terminal-v1','pipeline_integrity':'PASS','status':'POST_VIEW_DIAGNOSTIC_COMPLETE_NO_ENABLEMENT','research_only':True,'formal_promotion_eligible':False,'n':N,'prediction_sha256':pre['candidate_b_prediction_sha256'],'score_sha256':sha(sp),'candidate_b1_delta_logloss':delta['candidate_b1']['logloss'],'candidate_b1_b2_delta_logloss':delta['candidate_b1_b2']['logloss'],'candidate_b1_b2_delta_brier':delta['candidate_b1_b2']['brier'],'candidate_b1_b2_delta_rps':delta['candidate_b1_b2']['rps'],'candidate_b1_b2_delta_top1':delta['candidate_b1_b2']['top1'],'terminal':'NO_ENABLEMENT_NO_PROMOTION_NO_FORMAL_WEIGHT_CHANGE'};dump(out/'candidate_b_gate.json',gate);print(json.dumps(gate,indent=2))


def main():
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest='cmd',required=True);p=sp.add_parser('predict');p.add_argument('--v2-artifact',type=pathlib.Path,required=True);p.add_argument('--prior-artifact',type=pathlib.Path,required=True);p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--out',type=pathlib.Path,required=True);s=sp.add_parser('score');s.add_argument('--prior-artifact',type=pathlib.Path,required=True);s.add_argument('--label-vault',type=pathlib.Path,required=True);s.add_argument('--out',type=pathlib.Path,required=True);a=ap.parse_args()
    if a.cmd=='predict':predict_phase(a.v2_artifact,a.prior_artifact,a.source,a.out)
    else:score_phase(a.prior_artifact,a.label_vault,a.out)
if __name__=='__main__':main()
