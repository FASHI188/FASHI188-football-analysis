#!/usr/bin/env python3
"""Research-only coverage audit for direct pre-match score signals in Kambi raw envelopes."""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'evidence'/'direct_provider_probes'/'kambi'
OUT=ROOT/'manifests'/'v6_score_signal_coverage_v6172_status.json'

def eng(o):
    if not isinstance(o,dict): return ''
    return str(o.get('englishLabel') or o.get('englishName') or o.get('label') or o.get('name') or '').strip()

def prematch(o):
    tags={str(x).upper() for x in (o.get('tags') or [])}
    return not tags or 'OFFERED_PREMATCH' in tags

def main():
    counts=Counter(); examples=defaultdict(list); total_files=0
    team_total_offer_counts=[]; correct_score_outcome_counts=[]; scorer_offer_counts=[]; sot_offer_counts=[]; shot_offer_counts=[]
    for p in sorted(RAW.rglob('*.json')) if RAW.exists() else []:
        try: env=json.loads(p.read_text(encoding='utf-8'))
        except Exception: continue
        payload=env.get('payload') if isinstance(env,dict) else None
        offers=(payload or {}).get('betOffers') if isinstance(payload,dict) else None
        if not isinstance(offers,list): continue
        total_files+=1; counts['events']+=1
        fam=Counter(); team_names=set(); cs_out=0
        for o in offers:
            if not isinstance(o,dict) or not prematch(o): continue
            c=eng(o.get('criterion') or {}); t=eng(o.get('betOfferType') or {})
            if c=='Correct Score' and t=='Correct Score':
                fam['correct_score']+=1; cs_out=max(cs_out,len(o.get('outcomes') or []))
            if c=='Both Teams To Score' and t=='Yes/No': fam['btts']+=1
            if c.startswith('Total Goals by ') and t in {'Over/Under','Asian Over/Under'}:
                fam['team_total']+=1; team_names.add(c[len('Total Goals by '):].strip())
            if c=='To Score' and 'Player Occurrence' in t: fam['player_to_score']+=1
            if "Player's shots on target" in c and t=='Player Occurrence Line': fam['player_sot']+=1
            if c=="Player's shots (Settled using Opta data)" and t=='Player Occurrence Line': fam['player_shots']+=1
            if c=='Goalkeeper Saves (Settled using Opta data)' and t=='Over/Under': fam['gk_saves']+=1
        for k in ('correct_score','btts','team_total','player_to_score','player_sot','player_shots','gk_saves'):
            if fam[k]: counts[f'events_with_{k}']+=1
        if len(team_names)>=2: counts['events_with_2plus_distinct_team_totals']+=1
        if fam['correct_score'] and fam['btts'] and len(team_names)>=2: counts['events_with_cs_btts_2teamtotals']+=1
        if fam['player_to_score'] and fam['player_sot']: counts['events_with_scorer_and_sot']+=1
        if fam['correct_score'] and len(examples['correct_score'])<5:
            examples['correct_score'].append({'path':str(p.relative_to(ROOT)),'event_id':env.get('event_id'),'outcomes':cs_out})
        if len(team_names)>=2 and len(examples['team_totals'])<5:
            examples['team_totals'].append({'path':str(p.relative_to(ROOT)),'event_id':env.get('event_id'),'teams':sorted(team_names),'offer_count':fam['team_total']})
        team_total_offer_counts.append(fam['team_total']); correct_score_outcome_counts.append(cs_out); scorer_offer_counts.append(fam['player_to_score']); sot_offer_counts.append(fam['player_sot']); shot_offer_counts.append(fam['player_shots'])
    def avg(xs): return sum(xs)/len(xs) if xs else 0.0
    report={'schema_version':'V6.17.2-score-signal-coverage-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','counts':dict(sorted(counts.items())),'averages_per_event':{'team_total_offers':avg(team_total_offer_counts),'correct_score_outcomes_max':avg(correct_score_outcome_counts),'player_to_score_offers':avg(scorer_offer_counts),'player_sot_offers':avg(sot_offer_counts),'player_shot_offers':avg(shot_offer_counts)},'examples':dict(examples),'governance':{'research_only':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
