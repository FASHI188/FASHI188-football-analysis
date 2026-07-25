#!/usr/bin/env python3
"""V6.22.0 inventory full-time prematch Kambi Correct Score surfaces.

Read-only over already frozen raw Kambi envelopes.  The purpose is to determine whether
Correct Score markets are exhaustive partitions (numeric cells plus explicit aggregate
catch-all outcomes) or only partial quoted score lists.  No probability is inferred from
missing outcomes and no formal/current state is changed.
"""
from __future__ import annotations
import json,re
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'evidence'/'direct_provider_probes'/'kambi'/'active_leagues'
OUT=ROOT/'manifests'/'v6_correct_score_surface_inventory_v6220_status.json'
SCORE_RE=re.compile(r'^\s*(\d+)\s*[-:]\s*(\d+)\s*$')

def txt(x:Any)->str:return str(x or '').strip()
def label(o:dict[str,Any])->str:
 c=o.get('criterion') or {};return txt(c.get('englishLabel') or c.get('label'))
def lifetime(o:dict[str,Any])->str:return txt((o.get('criterion') or {}).get('lifetime')).upper()
def prematch(o:dict[str,Any])->bool:
 tags={txt(x).upper() for x in (o.get('tags') or [])};return not tags or 'OFFERED_PREMATCH' in tags

def main()->int:
 files=sorted(RAW.glob('*.json'));surfaces=[];nonnumeric=Counter();nonnumeric_open=Counter();numeric_domains=Counter();status_counts=Counter();changed_total=changed_ok=0;errors=[]
 event_ids=set();open_exhaustive_candidates=0
 for p in files:
  try:env=json.loads(p.read_text(encoding='utf-8'));offers=(env.get('payload') or {}).get('betOffers') or []
  except Exception as e:errors.append({'file':str(p.relative_to(ROOT)),'error':f'{type(e).__name__}: {e}'});continue
  for off in offers:
   if not isinstance(off,dict) or not prematch(off) or label(off).casefold()!='correct score' or lifetime(off) not in {'','FULL_TIME','MATCH'}:continue
   event_id=str(env.get('event_id') or p.stem);event_ids.add(event_id);rows=[];numeric=[];other=[]
   for x in off.get('outcomes') or []:
    if not isinstance(x,dict):continue
    nm=txt(x.get('englishLabel') or x.get('label'));m=SCORE_RE.match(nm);st=txt(x.get('status')).upper();od=x.get('odds');cd=x.get('changedDate');changed_total+=1;changed_ok+=int(bool(cd));status_counts[st]+=1
    row={'label':nm,'status':st,'odds':od,'changedDate':cd,'numeric_score':[int(m.group(1)),int(m.group(2))] if m else None}
    rows.append(row)
    if m:numeric.append(row)
    else:
     other.append(row);nonnumeric[nm]+=1
     if st=='OPEN' and od is not None:nonnumeric_open[nm]+=1
   if numeric:
    max_h=max(r['numeric_score'][0] for r in numeric);max_a=max(r['numeric_score'][1] for r in numeric);numeric_domains[(max_h,max_a,len(numeric))]+=1
   open_numeric=[r for r in numeric if r['status']=='OPEN' and r['odds'] is not None]
   open_other=[r for r in other if r['status']=='OPEN' and r['odds'] is not None]
   # Exhaustive is deliberately NOT asserted automatically. A candidate only means an
   # explicit non-numeric priced tail exists and can later be semantically classified.
   if open_other:open_exhaustive_candidates+=1
   surfaces.append({'file':str(p.relative_to(ROOT)),'event_id':event_id,'observed_at_utc':env.get('observed_at_utc'),'offer_id':off.get('id'),'outcome_count':len(rows),'numeric_count':len(numeric),'open_numeric_count':len(open_numeric),'nonnumeric_count':len(other),'open_priced_nonnumeric_count':len(open_other),'nonnumeric_outcomes':other,'all_outcomes_have_changedDate':bool(rows) and all(bool(r['changedDate']) for r in rows),'numeric_max_home':max((r['numeric_score'][0] for r in numeric),default=None),'numeric_max_away':max((r['numeric_score'][1] for r in numeric),default=None)})
 payload={'schema_version':'V6.22.0-correct-score-surface-inventory-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if not errors else 'WARN_ERRORS','classification':'READ_ONLY_FROZEN_RAW_INVENTORY','raw_file_count':len(files),'surface_count':len(surfaces),'unique_event_count':len(event_ids),'outcome_status_counts':dict(status_counts),'changedDate_coverage':changed_ok/changed_total if changed_total else None,'nonnumeric_label_counts':dict(nonnumeric),'open_priced_nonnumeric_label_counts':dict(nonnumeric_open),'surfaces_with_open_priced_nonnumeric_tail_candidate':open_exhaustive_candidates,'numeric_domain_patterns':[{'max_home':k[0],'max_away':k[1],'numeric_count':k[2],'surface_count':v} for k,v in numeric_domains.most_common()],'surfaces':surfaces,'errors':errors[:50],'decision':{'can_claim_exhaustive_correct_score_probability':False,'reason':'Semantic classification of every non-numeric tail outcome must be explicit before exhaustive market probabilities are allowed.'},'governance':{'network_fetch':False,'formal_weight':0,'formal_probability_change':False,'current_rule_change':False,'missing_outcome_probability_fabrication':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:payload[k] for k in ('status','raw_file_count','surface_count','unique_event_count','changedDate_coverage','nonnumeric_label_counts','open_priced_nonnumeric_label_counts','surfaces_with_open_priced_nonnumeric_tail_candidate')},ensure_ascii=False,indent=2));return 0 if not errors else 2
if __name__=='__main__':raise SystemExit(main())
