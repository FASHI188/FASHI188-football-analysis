#!/usr/bin/env python3
"""V6.21.2 derive immutable special-market sidecars from frozen Kambi PIT snapshots.

No network requests. Every sidecar is derived from one immutable normalized prospective
snapshot and its exact parent raw Kambi response. Captures Correct Score, full-time Total
Goals half-line ladder, BTTS, and full-time team-total ladders. Missing surfaces remain
missing; no probabilities or buckets are fabricated.
"""
from __future__ import annotations
import hashlib,json,re,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import v6_special_market_forward_eval_v6211 as sp

SRC=ROOT/'evidence'/'markets_prospective'
DST=ROOT/'evidence'/'markets_special_prospective'
OUT=ROOT/'manifests'/'v6_special_market_sidecar_v6212_status.json'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text(encoding='utf-8'))

def team_totals(env):
 out=defaultdict(list)
 for off in (env.get('payload') or {}).get('betOffers') or []:
  if not isinstance(off,dict) or sp.lifetime(off)!='FULL_TIME':continue
  lab=sp.label(off);low=lab.casefold()
  if not low.startswith('total goals by '):continue
  team=lab[len('Total Goals by '):].strip()
  os=sp.outcomes(off);over=next((o for o in os if o.get('type')=='OT_OVER'),None);under=next((o for o in os if o.get('type')=='OT_UNDER'),None)
  if not isinstance(over,dict) or not isinstance(under,dict):continue
  oo,uo=sp.dec_odds(over.get('odds')),sp.dec_odds(under.get('odds'))
  if not oo or not uo or str(over.get('status'))!='OPEN' or str(under.get('status'))!='OPEN':continue
  rawline=over.get('line') if over.get('line') is not None else under.get('line')
  try:line=float(rawline)/1000.0
  except:continue
  inv_o,inv_u=1/oo,1/uo;z=inv_o+inv_u
  out[team].append({'line':line,'over_odds':oo,'under_odds':uo,'p_over_devig':inv_o/z,'p_under_devig':inv_u/z,'offer_id':off.get('id'),'changedDate_over':over.get('changedDate'),'changedDate_under':under.get('changedDate')})
 for team in out:out[team].sort(key=lambda r:r['line'])
 return dict(out)

def main():
 DST.mkdir(parents=True,exist_ok=True);files=sorted(SRC.glob('*kambi_active*.json'));counts=Counter();written=0;identical=0;errors=[];rows=[]
 for p in files:
  try:
   snap=load(p);sa=snap.get('source_adapter') or {};raw_rel=str(sa.get('parent_raw_evidence_path') or '')
   if not raw_rel:continue
   raw=ROOT/raw_rel;env=load(raw)
   if str(env.get('observed_at_utc'))!=str(snap.get('source_observed_at_utc')):raise ValueError('observation timestamp mismatch')
   if str(env.get('payload_sha256'))!=str(sa.get('parent_raw_response_sha256')):raise ValueError('parent raw hash mismatch')
   cs=sp.correct_score_surface(env);tl=sp.total_ladder(env);bt=sp.btts(env);tt=team_totals(env)
   if cs:counts['correct_score_rank']+=1
   if cs and cs.get('probability_complete'):counts['correct_score_complete_probability']+=1
   if tl and tl.get('line_count'):counts['total_ladder']+=1
   if tl and tl.get('complete_0_7plus'):counts['complete_total_distribution']+=1
   if bt:counts['btts']+=1
   if tt:counts['team_totals']+=1
   payload={'schema_version':'V6.21.2-special-market-sidecar-r1','classification':'DERIVED_FROM_IMMUTABLE_PREMATCH_RAW','competition_id':snap.get('competition_id'),'season':snap.get('season'),'home_team':snap.get('home_team'),'away_team':snap.get('away_team'),'kickoff_utc':snap.get('kickoff_utc'),'freeze_utc':snap.get('freeze_utc'),'provider_name':snap.get('provider_name'),'provider_group':snap.get('provider_group'),'source_normalized_path':str(p.relative_to(ROOT)),'source_normalized_sha256':sha(p),'source_raw_path':raw_rel,'source_raw_file_sha256':sha(raw),'source_raw_payload_sha256':env.get('payload_sha256'),'correct_score':cs,'total_goals_ladder':tl,'btts':bt,'team_total_goals':tt,'governance':{'network_refetch':False,'postmatch_data':False,'missing_surface_fabrication':False,'formal_weight':0,'current_rule_change':False}}
   dest=DST/f'{p.stem}__special.json';text=json.dumps(payload,ensure_ascii=False,indent=2)+'\n'
   if dest.exists():
    if dest.read_text(encoding='utf-8')!=text:raise FileExistsError(f'immutable sidecar collision: {dest.name}')
    identical+=1
   else:dest.write_text(text,encoding='utf-8');written+=1
   rows.append({'source':str(p.relative_to(ROOT)),'sidecar':str(dest.relative_to(ROOT)),'correct_score':bool(cs),'score_probability_complete':bool(cs and cs.get('probability_complete')),'total_lines':int((tl or {}).get('line_count') or 0),'total_complete':bool(tl and tl.get('complete_0_7plus')),'btts':bool(bt),'team_total_teams':len(tt)})
  except Exception as e:errors.append({'source':str(p.relative_to(ROOT)),'error':f'{type(e).__name__}: {e}'})
 status='PASS' if not errors else 'WARN_ERRORS'
 receipt={'schema_version':'V6.21.2-special-market-sidecar-status-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':status,'source_snapshot_count':len(files),'sidecar_written':written,'sidecar_identical_existing':identical,'surface_counts':dict(counts),'error_count':len(errors),'errors':errors[:50],'rows':rows,'governance':{'immutable_sidecars':True,'network_refetch':False,'formal_weight':0,'current_rule_change':False,'missing_surface_fabrication':False}}
 OUT.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':status,'source_snapshot_count':len(files),'written':written,'identical':identical,'surface_counts':dict(counts),'errors':len(errors)},ensure_ascii=False,indent=2));return 0 if not errors else 2
if __name__=='__main__':raise SystemExit(main())
