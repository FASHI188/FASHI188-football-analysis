#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import v6170_formalprior_multiline_forward as v
from platform_core import load_json,parse_iso_datetime
OUT=ROOT/'manifests'/'v6170_formalprior_unavailable_audit.json'

def main():
    now=datetime.now(timezone.utc).replace(microsecond=0);counts=Counter();rows=[];bycomp=defaultdict(Counter)
    for p in sorted(v.SOURCE_FREEZES.glob('event_*.json')) if v.SOURCE_FREEZES.exists() else []:
        f=load_json(p);ident=f.get('fixture_identity') or {};eid=str(ident.get('event_id') or '');cid=str(ident.get('competition_id') or '');kick=parse_iso_datetime(str(ident.get('kickoff_utc') or ''),'kickoff_utc')
        if (v.FREEZE_DIR/f'event_{eid}.json').exists():status='ALREADY_V617_FROZEN';audit={}
        elif kick<=now:status='ALREADY_STARTED_AT_AUDIT';audit={}
        else:
            prior,audit=v.formal_prior(f);status='READY_NOW' if prior is not None else str(audit.get('status') or 'UNKNOWN_UNAVAILABLE')
        counts[status]+=1;bycomp[cid][status]+=1;rows.append({'event_id':eid,'competition_id':cid,'home_team':ident.get('home_team'),'away_team':ident.get('away_team'),'kickoff_utc':ident.get('kickoff_utc'),'status':status,'audit':audit})
    out={'schema_version':'V6.17.0-formalprior-unavailable-audit-r1','generated_at_utc':now.isoformat(),'status':'PASS','counts':dict(counts),'by_competition':{k:dict(c) for k,c in sorted(bycomp.items())},'rows':rows,'governance':{'diagnostic_only':True,'no_probability_change':True,'no_current_change':True}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'counts':dict(counts),'by_competition':out['by_competition']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
