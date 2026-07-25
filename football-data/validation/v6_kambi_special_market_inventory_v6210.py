#!/usr/bin/env python3
"""V6.21.0 inventory frozen raw Kambi prematch special markets.

Read-only retrospective inventory over already frozen raw event-detail envelopes. It does
not fetch, predict, or change any formal probability. The goal is to determine whether
current raw evidence already contains targeted markets useful for independent score/total
tracks: correct score, exact total goals, BTTS, team totals and related score surfaces.
"""
from __future__ import annotations
import json,re
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'evidence'/'direct_provider_probes'/'kambi'/'active_leagues'
OUT=ROOT/'manifests'/'v6_kambi_special_market_inventory_v6210_status.json'

RULES={
 'correct_score': [r'correct score',r'exact score',r'juiste uitslag',r'exact resultaat'],
 'exact_total_goals': [r'exact total goals',r'exact number of goals',r'exact aantal doelpunten'],
 'btts': [r'both teams to score',r'beide teams.*scor',r'both teams.*goal'],
 'team_total_goals': [r'total goals by ',r'team total goals',r'totaal aantal doelpunten door '],
 'total_goals': [r'^total goals$',r'^totaal aantal doelpunten$'],
 'winning_margin': [r'winning margin',r'win margin',r'winstmarge'],
 'draw_no_bet': [r'draw no bet'],
 'half_full': [r'half time/full time',r'ht/ft',r'rust/eindresultaat'],
}

def classify(label):
 s=str(label or '').casefold()
 return [k for k,patterns in RULES.items() if any(re.search(p,s) for p in patterns)]

def main():
 files=sorted(RAW.glob('*.json')) if RAW.exists() else []
 cat=Counter();labels=Counter();events_by_cat=defaultdict(set);examples=defaultdict(list);offers=0;open_offers=0;outcomes=0;timestamped_outcomes=0
 for path in files:
  try:env=json.loads(path.read_text(encoding='utf-8'))
  except Exception:continue
  payload=env.get('payload') if isinstance(env,dict) else None
  if not isinstance(payload,dict):continue
  event_id=str(env.get('event_id') or path.stem)
  for offer in payload.get('betOffers') or []:
   if not isinstance(offer,dict):continue
   offers+=1
   crit=offer.get('criterion') or {}; label=str(crit.get('englishLabel') or crit.get('label') or '').strip(); labels[label]+=1
   os=[o for o in offer.get('outcomes') or [] if isinstance(o,dict)]
   outcomes+=len(os);timestamped_outcomes+=sum(bool(o.get('changedDate')) for o in os)
   if any(str(o.get('status') or '')=='OPEN' for o in os):open_offers+=1
   for c in classify(label):
    cat[c]+=1;events_by_cat[c].add(event_id)
    if len(examples[c])<8:
     examples[c].append({'file':str(path.relative_to(ROOT)),'event_id':event_id,'label':label,'criterion_id':crit.get('id'),'bet_offer_type':(offer.get('betOfferType') or {}).get('englishName') or (offer.get('betOfferType') or {}).get('name'),'outcome_count':len(os),'open_outcome_count':sum(str(o.get('status') or '')=='OPEN' for o in os),'all_outcomes_have_changedDate':bool(os) and all(bool(o.get('changedDate')) for o in os),'sample_outcomes':[{'label':o.get('englishLabel') or o.get('label'),'odds':o.get('odds'),'line':o.get('line'),'changedDate':o.get('changedDate'),'status':o.get('status')} for o in os[:6]]})
 payload={'schema_version':'V6.21.0-kambi-special-market-inventory-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','classification':'FROZEN_RAW_KAMBI_RESEARCH_INVENTORY','raw_file_count':len(files),'bet_offer_count':offers,'open_offer_count':open_offers,'outcome_count':outcomes,'timestamped_outcome_count':timestamped_outcomes,'timestamp_coverage':timestamped_outcomes/outcomes if outcomes else None,'special_market_offer_counts':dict(cat),'special_market_event_counts':{k:len(v) for k,v in events_by_cat.items()},'examples':dict(examples),'top_criterion_labels':labels.most_common(80),'decision_support':{'correct_score_available':cat['correct_score']>0,'exact_total_goals_available':cat['exact_total_goals']>0,'btts_available':cat['btts']>0,'team_total_goals_available':cat['team_total_goals']>0},'governance':{'read_only':True,'network_fetch':False,'formal_weight':0,'current_rule_change':False,'raw_evidence_mutation':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
