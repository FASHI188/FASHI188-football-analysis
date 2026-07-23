#!/usr/bin/env python3
"""V6.5.3 audited-provider-alias wrapper for the V6.5.2 Active-Kambi PIT collector.

The underlying market acquisition and frozen V6.5.1 timing rules are unchanged. This wrapper adds a
provider-specific, competition-scoped alias contract whose targets must exist in the latest weekly
current-team inventory. No fuzzy suggestion can enter runtime resolution.

For every newly-written formal snapshot that used a provider alias, the alias contract path/hash and
exact source->canonical mapping are bound into `source_adapter`, then the immutable snapshot hash and
V5.2.3 validation are recomputed before persistence. Existing committed snapshots are never mutated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))

import v6_active_kambi_market_capture_v652 as base
from prospective_market_snapshot_v523 import canonical_sha256, validate

ALIAS_PATH=ROOT/'config'/'v6_active_kambi_team_aliases_v652.json'
ORIGINAL_BUILD=base.build_identity_maps


def load_contract()->dict[str,Any]:
    x=json.loads(ALIAS_PATH.read_text(encoding='utf-8'))
    if x.get('schema_version')!='V6.5.2-active-kambi-team-aliases-r1':raise ValueError('unexpected active Kambi alias schema')
    g=x.get('governance') or {}
    if g.get('competition_scoped') is not True or g.get('provider_scoped') is not True or g.get('fuzzy_runtime_matching') is not False or g.get('global_team_aliases_overwritten') is not False:raise ValueError('active Kambi alias governance invalid')
    return x

CONTRACT=load_contract(); CONTRACT_SHA=base.file_sha(ALIAS_PATH)


def enhanced_build(rows:dict[tuple[str,str],dict[str,Any]]):
    maps,seasons=ORIGINAL_BUILD(rows)
    competitions=CONTRACT.get('competitions') or {}
    for cid,mapping in competitions.items():
        if cid not in maps:raise ValueError(f'alias competition not enabled: {cid}')
        canonical={team for comp,team in rows if comp==cid}
        for source,target in (mapping or {}).items():
            source=str(source or '').strip();target=str(target or '').strip()
            if target not in canonical:raise ValueError(f'alias target not in latest current-team inventory: {cid} {source!r}->{target!r}')
            token=base.norm(source)
            if not token:raise ValueError(f'empty normalized provider alias: {cid} {source!r}')
            previous=maps[cid].get(token)
            if previous not in {None,target,'__AMBIGUOUS__'}:raise ValueError(f'provider alias conflicts with current identity: {cid} {source!r}: {previous!r} vs {target!r}')
            maps[cid][token]=target
    return maps,seasons


def alias_used(cid:str,source:str,canonical:str)->dict[str,str]|None:
    mapping=((CONTRACT.get('competitions') or {}).get(cid) or {})
    target=mapping.get(source)
    if target==canonical:return {'source_name':source,'canonical_team':canonical}
    return None


def bind_alias_receipts()->dict[str,Any]:
    receipt=json.loads(base.OUT.read_text(encoding='utf-8'))
    receipt['schema_version']='V6.5.3-active-kambi-market-capture-r1'
    receipt['identity_alias_contract']={'path':str(ALIAS_PATH.relative_to(ROOT)),'sha256':CONTRACT_SHA,'schema_version':CONTRACT.get('schema_version'),'provider_group':CONTRACT.get('provider_group'),'mapping_count':sum(len(v or {}) for v in (CONTRACT.get('competitions') or {}).values()),'fuzzy_runtime_matching':False}
    rebound=0
    for row in receipt.get('events') or []:
        if not isinstance(row,dict) or row.get('status')!='VALID_ACTIVE_KAMBI_PIT_WRITTEN' or not row.get('formal_snapshot_path'):continue
        cid=str(row.get('competition_id') or '');home=str(row.get('canonical_home') or '');away=str(row.get('canonical_away') or '');used=[]
        h=alias_used(cid,str(row.get('source_home') or ''),home);a=alias_used(cid,str(row.get('source_away') or ''),away)
        if h:used.append({'side':'home',**h})
        if a:used.append({'side':'away',**a})
        if not used:continue
        path=ROOT/str(row['formal_snapshot_path']);snapshot=json.loads(path.read_text(encoding='utf-8'));adapter=snapshot.setdefault('source_adapter',{})
        adapter['provider_identity_alias_contract_path']=str(ALIAS_PATH.relative_to(ROOT));adapter['provider_identity_alias_contract_sha256']=CONTRACT_SHA;adapter['provider_identity_alias_contract_schema']=CONTRACT.get('schema_version');adapter['provider_identity_aliases_used']=used;adapter['fuzzy_runtime_identity_matching']=False
        snapshot['raw_snapshot_sha256']=canonical_sha256(snapshot);validation=validate(snapshot)
        if not validation.get('passed') or not validation.get('formal_pit_eligible'):raise ValueError(f'alias-bound snapshot failed V5.2.3: {path}: {validation.get("errors")}')
        path.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8');row['v523_validation']=validation;row['provider_identity_aliases_used']=used;row['provider_identity_alias_contract_sha256']=CONTRACT_SHA;rebound+=1
    receipt['alias_bound_snapshot_count']=rebound
    g=receipt.setdefault('governance',{});g['provider_specific_alias_contract']=True;g['provider_alias_contract_hash_bound_into_new_snapshots']=True;g['fuzzy_runtime_identity_matching']=False;g['global_team_aliases_modified_by_active_kambi']=False
    base.OUT.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt


def main()->int:
    base.build_identity_maps=enhanced_build
    code=base.main()
    receipt=bind_alias_receipts()
    print(json.dumps({'schema_version':receipt.get('schema_version'),'status':receipt.get('status'),'written':receipt.get('formal_snapshot_count_written'),'v651_timing_eligible':receipt.get('v651_timing_eligible_snapshot_count'),'identity_unresolved':receipt.get('identity_unresolved_count'),'alias_bound_snapshot_count':receipt.get('alias_bound_snapshot_count'),'alias_contract':receipt.get('identity_alias_contract')},ensure_ascii=False,indent=2))
    return code

if __name__=='__main__':raise SystemExit(main())