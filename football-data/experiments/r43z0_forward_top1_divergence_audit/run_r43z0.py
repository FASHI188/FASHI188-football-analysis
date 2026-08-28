#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
U1=ROOT/'forward'/'r43u1_pristine_forward_events.json'
Y0=ROOT/'forward'/'r43y0_draw_calibration_forward_events.json'
B1=ROOT/'forward'/'r43_batch01_pristine_lockbox.json'
B2=ROOT/'forward'/'r43_batch02_pristine_lockbox.json'
OUT=ROOT/'experiments'/'r43z0_forward_top1_divergence_audit'/'results'/'summary_r43z0_forward_top1_divergence.json'
CLASSES=('home','draw','away')
SEALED_N=41

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def top1(p):return max(CLASSES,key=lambda k:(float(p[k]),-CLASSES.index(k)))
def margin(p):
    v=sorted((float(p[k]) for k in CLASSES),reverse=True);return v[0]-v[1]

def run():
    b1=load(B1);b2=load(B2)
    assert b1.get('status')=='SEALED_PRE_SETTLEMENT'
    assert b2.get('status')=='SEALED_PRE_SETTLEMENT'
    u=(load(U1).get('events') or [])[:SEALED_N]
    y=(load(Y0).get('events') or [])[:SEALED_N]
    if len(u)!=SEALED_N or len(y)!=SEALED_N:raise RuntimeError('sealed 41-event prefix unavailable')
    ub={str(e['match_id']):e for e in u};yb={str(e['match_id']):e for e in y}
    if set(ub)!=set(yb):raise RuntimeError('sealed U1/Y0 match-id sets differ')
    rows=[]
    for mid,e in ub.items():
        pl=e['payload'];mp=pl['market_probabilities'];up=pl['r43u0_probabilities'];ye=yb[mid];yp=ye['payload']['r43y0_probabilities']
        mt,ut,yt=top1(mp),top1(up),top1(yp)
        rows.append({
          'match_id':mid,
          'kickoff_at':pl['fixture_identity']['kickoff_at'],
          'competition_id':pl['fixture_identity']['competition_id'],
          'home_team':pl['fixture_identity']['home_team'],
          'away_team':pl['fixture_identity']['away_team'],
          'market_top1':mt,'u0_top1':ut,'y0_top1':yt,
          'market_probabilities':mp,'u0_probabilities':up,'y0_probabilities':yp,
          'market_margin':margin(mp),'u0_margin':margin(up),'y0_margin':margin(yp),
          'u0_draw_minus_market':float(up['draw'])-float(mp['draw']),
          'y0_draw_minus_u0':float(yp['draw'])-float(up['draw']),
          'u0_changes_market_top1':ut!=mt,
          'y0_changes_u0_top1':yt!=ut,
          'y0_changes_market_top1':yt!=mt,
        })
    rows.sort(key=lambda r:(r['kickoff_at'],r['match_id']))
    u_changes=[r for r in rows if r['u0_changes_market_top1']]
    y_changes=[r for r in rows if r['y0_changes_u0_top1']]
    dd=[r['u0_draw_minus_market'] for r in rows]
    bands={str(int(t*100)):sum(1 for r in rows if r['u0_margin']<=t) for t in (0.01,0.02,0.05,0.10,0.15,0.20)}
    n=len(rows);k=len(y_changes)
    out={
      'schema_version':'football3-r43z0-zero-label-forward-top1-divergence-v1',
      'status':'COMPLETE',
      'classification':'ZERO_LABEL_PRE_OUTCOME_FORWARD_GEOMETRY_AUDIT',
      'formal_weight':0,
      'governance':{
        'sealed_prediction_ledgers_only':True,
        'outcome_access':False,
        'parameter_search':False,
        'threshold_search':False,
        'model_change':False,
        'prediction_change':False,
        'post_outcome_rule_creation':False,
        'batch01_and_batch02_lockboxes_required':True,
        'main_merge':False,
        'publication':False
      },
      'coverage':{'sealed_matches':n,'u1_events':len(u),'y0_events':len(y)},
      'direct_market_vs_u0':{
        'market_top1_picks':dict(Counter(r['market_top1'] for r in rows)),
        'u0_top1_picks':dict(Counter(r['u0_top1'] for r in rows)),
        'same_top1_count':n-len(u_changes),
        'changed_top1_count':len(u_changes),
        'changed_rows':u_changes,
        'u0_draw_probability_higher_than_market_count':sum(1 for x in dd if x>0),
        'u0_draw_probability_lower_than_market_count':sum(1 for x in dd if x<0),
        'mean_u0_draw_minus_market':sum(dd)/n,
        'min_u0_draw_minus_market':min(dd),
        'max_u0_draw_minus_market':max(dd),
        'u0_top1_margin_band_counts_pp_le':bands,
        'mathematical_top1_hit_delta_bound_on_sealed41':[0,0],
        'interpretation':'U0 and direct market make identical Top1 decisions on all sealed matches; therefore their Top1 hit counts must be identical on these 41 regardless of future outcomes.'
      },
      'u0_vs_y0':{
        'u0_top1_picks':dict(Counter(r['u0_top1'] for r in rows)),
        'y0_top1_picks':dict(Counter(r['y0_top1'] for r in rows)),
        'changed_top1_count':k,
        'changed_to_draw_count':sum(1 for r in y_changes if r['y0_top1']=='draw'),
        'changed_rows':y_changes,
        'mathematical_y0_minus_u0_hit_delta_bounds':[-k,k],
        'mathematical_accuracy_delta_bounds_pp':[-100*k/n,100*k/n],
        'interpretation':'Only the pre-outcome Y0 decision changes can alter Top1 hit count versus U0/direct market on the sealed 41-match cohort.'
      },
      'research_implication':{
        'u0_role_on_sealed41':'CALIBRATION_ONLY_FOR_TOP1_DECISION; proper scores may differ but Top1 cannot improve over direct market.',
        'y0_role_on_sealed41':'ONLY_CURRENT_DECISION_CHANGING_FORWARD_CANDIDATE',
        'no_retuning_on_future_outcomes':True,
        'next_evidence_need':'Accumulate more pristine Y0 natural-decision divergences; score all sealed matches and the changed-decision subset without changing the fixed intercept.'
      },
      'rows':rows,
      'action':'KEEP_U0_AS_PROPER_SCORE_CALIBRATION_TEST_AND_USE_Y0_ONLY_AS_PREREGISTERED_TOP1_DIVERGENCE_TEST_NO_RETUNING'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2))
    return out

def verify():
    x=load(OUT);g=x['governance'];m=x['direct_market_vs_u0'];y=x['u0_vs_y0']
    assert x['status']=='COMPLETE' and x['coverage']['sealed_matches']==41
    assert g['outcome_access'] is False and g['parameter_search'] is False and g['threshold_search'] is False and g['model_change'] is False and g['prediction_change'] is False
    assert m['same_top1_count']==41 and m['changed_top1_count']==0 and m['mathematical_top1_hit_delta_bound_on_sealed41']==[0,0]
    assert y['changed_top1_count']==3 and y['changed_to_draw_count']==3
    print('R43Z0 zero-label forward Top1 divergence audit verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
