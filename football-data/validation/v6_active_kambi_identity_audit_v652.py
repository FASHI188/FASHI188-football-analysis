#!/usr/bin/env python3
"""Read-only identity diagnostic for V6.5.2 active Kambi PIT capture.

This tool NEVER resolves a provider team automatically. It reproduces the latest current-team
canonical inventory from the weekly PIT evidence, reads unresolved provider names from the V6.5.2
receipt, and emits deterministic exact-normalized probes plus top string-similarity suggestions for
human/audited alias decisions. Similarity suggestions are diagnostic only and MUST NOT be consumed by
the capture resolver.
"""
from __future__ import annotations
import difflib,json,re,sys,unicodedata
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_active_kambi_market_capture_v652 as capture
ACTIVE=ROOT/'manifests'/'v6_active_kambi_market_capture_v652_status.json'
OUT=ROOT/'manifests'/'v6_active_kambi_identity_audit_v652_status.json'
BRA_STATE=re.compile(r'-(AC|AL|AM|AP|BA|CE|DF|ES|GO|MA|MG|MS|MT|PA|PB|PE|PI|PR|RJ|RN|RO|RR|RS|SC|SE|SP|TO)$',re.I)
def norm(v:Any)->str:
 t=unicodedata.normalize('NFKD',str(v or ''));t=''.join(ch for ch in t if not unicodedata.combining(ch)).casefold();return ' '.join(re.findall(r'[a-z0-9]+',t))
def main()->int:
 active=json.loads(ACTIVE.read_text(encoding='utf-8'));rows,_=capture.latest_team_rows();unresolved=active.get('unresolved_source_names') or {};payload={'schema_version':'V6.5.2-active-kambi-identity-audit-r1','status':'PASS','source_active_receipt_sha256':capture.file_sha(ACTIVE),'competitions':{},'governance':{'read_only':True,'automatic_alias_application':False,'fuzzy_resolution_allowed':False,'similarity_is_diagnostic_only':True,'current_weekly_team_identity_only':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
 for cid,names in sorted(unresolved.items()):
  canonical=sorted(team for comp,team in rows if comp==cid);canon_norm={norm(team):team for team in canonical};items=[]
  for source,count in sorted((names or {}).items()):
   source_norm=norm(source);exact=canon_norm.get(source_norm);bra_stripped=None;bra_exact=None
   if cid=='BRA_SerieA':
    bra_stripped=BRA_STATE.sub('',source).strip();bra_exact=canon_norm.get(norm(bra_stripped))
   scored=sorted(((difflib.SequenceMatcher(None,source_norm,norm(team)).ratio(),team) for team in canonical),reverse=True)[:5]
   items.append({'source_name':source,'event_count':count,'exact_normalized_match':exact,'brazil_state_suffix_stripped':bra_stripped,'brazil_state_suffix_exact_match':bra_exact,'diagnostic_similarity_top5':[{'canonical':team,'score':score} for score,team in scored]})
  payload['competitions'][cid]={'canonical_current_teams':canonical,'unresolved':items}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())