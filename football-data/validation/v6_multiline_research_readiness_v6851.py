#!/usr/bin/env python3
"""V6.8.5.1 research-only readiness audit for prospective multiline freezes.

This audit does not create predictions, formal freezes, sidecars, or backfills. It only
checks whether the already-immutable Kambi full-market ladder bundles contain sufficient
prematch event identity to support a separate prospective research freeze path.
"""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'evidence'/'market_ladders_v680'/'kambi_full_time_ladders.json'
OUT=ROOT/'manifests'/'v6_multiline_research_readiness_v6851_status.json'
EPOCH=datetime.fromisoformat('2026-07-23T06:28:17+00:00')

def dt(v):
    if not v:return None
    try:
        x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if x.tzinfo is None:x=x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc)
    except:return None

def main():
    data=json.loads(SRC.read_text(encoding='utf-8'))
    bundles=data.get('bundles') or []
    c=Counter(); lead_minutes=[]; recent=[]
    now=datetime.now(timezone.utc)
    for b in bundles:
        c['total']+=1
        home=str(b.get('home_team_source') or '').strip();away=str(b.get('away_team_source') or '').strip()
        ko=dt(b.get('kickoff_utc'));obs=dt(b.get('observed_at_utc'))
        identity=bool(home and away and ko)
        c['identity_complete']+=int(identity)
        c['observed_present']+=int(obs is not None)
        c['kickoff_present']+=int(ko is not None)
        c['post_epoch_observed']+=int(obs is not None and obs>=EPOCH)
        c['observed_before_kickoff']+=int(obs is not None and ko is not None and obs<ko)
        c['identity_complete_prematch']+=int(identity and obs is not None and obs<ko)
        c['identity_complete_post_epoch_prematch']+=int(identity and obs is not None and obs>=EPOCH and obs<ko)
        c['future_as_of_now']+=int(ko is not None and ko>now)
        c['identity_complete_future_as_of_now']+=int(identity and ko>now)
        if identity and obs is not None and obs<ko:
            lead=(ko-obs).total_seconds()/60.0;lead_minutes.append(lead)
            if obs>=EPOCH:
                recent.append({'event_id':b.get('event_id'),'home':home,'away':away,'competition_source':b.get('competition_source'),'event_state':b.get('event_state'),'observed_at_utc':obs.isoformat(),'kickoff_utc':ko.isoformat(),'lead_minutes':lead,'total_lines':(b.get('diagnostics') or {}).get('distinct_total_line_count'),'ah_lines':(b.get('diagnostics') or {}).get('distinct_ah_line_count')})
    lead_minutes.sort()
    recent.sort(key=lambda r:(r['observed_at_utc'],str(r['event_id'])))
    payload={
      'schema_version':'V6.8.5.1-multiline-research-readiness-r1',
      'generated_at_utc':now.replace(microsecond=0).isoformat(),
      'status':'PASS' if c['identity_complete_post_epoch_prematch'] else 'NO_POST_EPOCH_IDENTIFIED_PREMATCH_BUNDLES',
      'formal_current_version':'V5.0.1',
      'counts':dict(c),
      'lead_minutes':{
        'min':lead_minutes[0] if lead_minutes else None,
        'median':lead_minutes[len(lead_minutes)//2] if lead_minutes else None,
        'max':lead_minutes[-1] if lead_minutes else None,
      },
      'post_epoch_prematch_examples':recent[-20:],
      'governance':{
        'research_only':True,
        'creates_formal_request':False,
        'creates_prediction_freeze':False,
        'creates_sidecar':False,
        'historical_backfill':False,
        'formal_probability_change':False,
        'formal_weight_change':False,
        'current_rule_change':False,
      }
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
