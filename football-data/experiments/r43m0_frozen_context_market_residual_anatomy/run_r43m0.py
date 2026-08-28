#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from platform_core import normalize_team_token

TRUTH=ROOT/'forward'/'v6_market_first_events_v651.json'
CTX=ROOT/'forward'/'v6_context_conditioned_selector_events_v6495.json'
AXES=ROOT/'forward'/'v6_match_context_axes_events_v6490.json'
OUT=HERE/'results'/'summary_r43m0_frozen_context_market_residual_anatomy.json'
C=('home','draw','away')

def load(p): return json.loads(p.read_text(encoding='utf-8'))
def dt(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def key(f):
    return (str(f['competition_id']),dt(f['kickoff_at']).astimezone(timezone.utc).replace(microsecond=0).isoformat(),normalize_team_token(str(f['home_team'])),normalize_team_token(str(f['away_team'])))
def norm(p):
    z={k:float(p[k]) for k in C}; s=sum(z.values()); return {k:z[k]/s for k in C}
def pick(p): return max(C,key=lambda k:(float(p[k]),-C.index(k)))
def absence_bin(d):
    d=int(d)
    if d<=-2:return 'HOME_FEWER_BY_2PLUS'
    if d>=2:return 'HOME_MORE_BY_2PLUS'
    return 'ROUGHLY_BALANCED_MINUS1_TO_PLUS1'

def row_metrics(rows):
    eps=1e-15; hits=0; ll=br=rps=0.0; actual=Counter(); picks=Counter(); truep=0.0; drawp=[]
    for r in rows:
        p=r['p']; y=r['truth']; idx=C.index(y); pk=pick(p)
        hits+=int(pk==y); actual[y]+=1; picks[pk]+=1; truep+=p[y]
        ll += -math.log(max(eps,min(1.0-eps,p[y])))
        br += sum((p[k]-(1.0 if k==y else 0.0))**2 for k in C)
        o1=1.0 if idx<=0 else 0.0; o2=1.0 if idx<=1 else 0.0; c1=p['home']; c2=c1+p['draw']; rps += ((c1-o1)**2+(c2-o2)**2)/2.0
        if y=='draw': drawp.append(p['draw'])
    n=len(rows)
    return {'count':n,'hits':hits,'accuracy':hits/n if n else None,'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,'mean_probability_assigned_to_truth':truep/n if n else None,'actual_draw_rate':actual['draw']/n if n else None,'mean_draw_probability_on_actual_draws':sum(drawp)/len(drawp) if drawp else None,'actuals':dict(actual),'top1_picks':dict(picks)}

def stratify(rows, name, fn):
    groups=defaultdict(list)
    for r in rows: groups[str(fn(r))].append(r)
    return {'axis':name,'groups':{k:row_metrics(v) for k,v in sorted(groups.items())}}

def run():
    t=load(TRUTH); predkey={}; truth={}
    for e in t['events']:
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN': predkey[str(e['match_id'])]=key(e['payload']['fixture_identity'])
        elif e.get('event_type')=='RESULT_SETTLED': truth[str(e['match_id'])]=str(e['payload']['result']['actual_result'])
    truth_by_key={predkey[mid]:y for mid,y in truth.items() if mid in predkey}

    axes_by_key={}
    ax=load(AXES)
    for e in ax.get('events',[]):
        if e.get('event_type')!='MATCH_CONTEXT_AXES_FROZEN': continue
        f=e.get('payload',{}).get('fixture_identity') or {}
        try: axes_by_key[key(f)]=e
        except Exception: pass

    ctx=load(CTX); rows=[]; rejects=Counter(); selected_field_counts=Counter()
    for e in ctx.get('events',[]):
        if e.get('event_type')!='CONTEXT_SELECTOR_PREDICTION_FROZEN': continue
        try:
            pl=e['payload']; f=pl['fixture_identity']; k=key(f); y=truth_by_key.get(k)
            if y is None: rejects['no_settled_overlap']+=1; continue
            freeze=dt(pl['decision_freeze_at_utc']); kick=dt(f['kickoff_at'])
            if freeze>=kick: rejects['not_prematch']+=1; continue
            p=norm(pl['prediction']['probabilities']); cf=pl.get('context_features') or {}; sel=pl.get('selector') or {}
            # selected is logged for audit only and is never used to include/exclude or alter a row.
            selected_field_counts[str(bool(sel.get('selected')))] += 1
            ae=axes_by_key.get(k); apl=(ae or {}).get('payload') or {}
            material_count=int(apl.get('confirmed_material_roster_delta_count') or cf.get('confirmed_material_roster_delta_count') or 0)
            pending_count=int(apl.get('pending_delta_count') or cf.get('pending_delta_count_not_applied') or 0)
            home_abs=int(cf.get('home_absence_count') or 0); away_abs=int(cf.get('away_absence_count') or 0); diff=home_abs-away_abs
            pk=pick(p)
            if pk=='home': picked_minus_other=home_abs-away_abs
            elif pk=='away': picked_minus_other=away_abs-home_abs
            else: picked_minus_other=0
            rows.append({'truth':y,'p':p,'pick':pk,'both_availability':bool(cf.get('both_availability_evidence')),'home_availability':bool(cf.get('home_availability_evidence')),'away_availability':bool(cf.get('away_availability_evidence')),'full_predicted_xi':bool(cf.get('full_predicted_xi')),'manager_both_verified':bool(cf.get('manager_both_verified')),'source_conflict':bool(cf.get('source_conflict')),'absence_diff':diff,'absence_bin':absence_bin(diff),'picked_minus_other_absences':picked_minus_other,'picked_absence_relation':'PICKED_MORE_BY_2PLUS' if picked_minus_other>=2 else 'PICKED_FEWER_BY_2PLUS' if picked_minus_other<=-2 else 'PICKED_ROUGHLY_BALANCED','material_delta_count':material_count,'material_delta_any':material_count>=1,'pending_delta_count':pending_count,'pending_delta_any':pending_count>=1,'axes_present':ae is not None,'source_tier':str(cf.get('source_tier') or 'UNKNOWN')})
        except Exception:
            rejects['parse_error']+=1

    strata=[]
    for name,fn in [
        ('both_availability_evidence',lambda r:r['both_availability']),
        ('full_predicted_xi',lambda r:r['full_predicted_xi']),
        ('manager_both_verified',lambda r:r['manager_both_verified']),
        ('source_conflict',lambda r:r['source_conflict']),
        ('home_minus_away_absence_bin',lambda r:r['absence_bin']),
        ('market_picked_side_absence_relation',lambda r:r['picked_absence_relation']),
        ('confirmed_material_roster_delta_any',lambda r:r['material_delta_any']),
        ('pending_material_delta_any',lambda r:r['pending_delta_any']),
        ('source_tier',lambda r:r['source_tier']),
    ]: strata.append(stratify(rows,name,fn))

    # Diagnostic-only contrast ranking. This is descriptive and cannot create a rule.
    contrasts=[]
    for s in strata:
        eligible=[(g,m) for g,m in s['groups'].items() if m['count']>=5]
        if len(eligible)>=2:
            eligible=sorted(eligible,key=lambda z:z[1]['accuracy'])
            lo,hi=eligible[0],eligible[-1]
            contrasts.append({'axis':s['axis'],'low_group':lo[0],'low_n':lo[1]['count'],'low_accuracy':lo[1]['accuracy'],'high_group':hi[0],'high_n':hi[1]['count'],'high_accuracy':hi[1]['accuracy'],'accuracy_gap_pp':100.0*(hi[1]['accuracy']-lo[1]['accuracy']),'truth_probability_gap':hi[1]['mean_probability_assigned_to_truth']-lo[1]['mean_probability_assigned_to_truth']})
    contrasts.sort(key=lambda z:abs(z['accuracy_gap_pp']),reverse=True)

    out={'schema_version':'football3-r43m0-frozen-context-market-residual-anatomy-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_DIAGNOSTIC_ON_PREMATCH_FROZEN_CONTEXT','question':'Do prospectively frozen lineup/availability/manager/roster-context axes identify reproducible-looking market residual regimes without using the old selector or altering probabilities?',
         'governance':{'prematch_frozen_context_only':True,'existing_market_probabilities_only':True,'existing_settlements_only':True,'old_selector_selected_field_used_for_decision':False,'all_overlapping_rows_included':True,'no_bet_or_veto':False,'probability_adjustment':False,'parameter_search':False,'threshold_search':False,'rule_creation':False,'formal_promotion_allowed':False},
         'coverage':{'context_prediction_events':sum(1 for e in ctx.get('events',[]) if e.get('event_type')=='CONTEXT_SELECTOR_PREDICTION_FROZEN'),'settled_overlap':len(rows),'axes_present_on_overlap':sum(r['axes_present'] for r in rows),'selector_selected_field_audit_counts':dict(selected_field_counts),'rejects':dict(sorted(rejects.items()))},
         'overall_market_at_context_freeze':row_metrics(rows),'strata':strata,'descriptive_contrasts_min_group5':contrasts,
         'interpretation_contract':'Post-outcome diagnostics only. No stratum, threshold, veto, selector, or probability transport may be promoted or tuned on these outcomes. Any mechanism hypothesis must be frozen before a new forward target batch.'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2)); return out

def verify():
    x=load(OUT); assert x['status']=='COMPLETE' and x['formal_weight']==0; g=x['governance']; assert g['old_selector_selected_field_used_for_decision'] is False and g['all_overlapping_rows_included'] is True and g['probability_adjustment'] is False and g['rule_creation'] is False and g['formal_promotion_allowed'] is False; print('R43M0 contract verified')
if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
