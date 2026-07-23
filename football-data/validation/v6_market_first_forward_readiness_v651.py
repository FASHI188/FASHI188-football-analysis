#!/usr/bin/env python3
"""V6.5.1 read-only readiness audit for post-freeze prospective market evidence.

This diagnostic does NOT create predictions, change the frozen 1-72h lead window, or backfill the
market-first epoch. It explains why evidence observed after the V6.5.1 freeze is or is not eligible
for the already-frozen prediction rule. Lead time is measured exactly as V6.5.1 measures it:
`kickoff - source_observed_at`.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from platform_core import atomic_write_json, load_json, normalize_team_token, parse_iso_datetime

FREEZE=ROOT/'manifests'/'v6_market_first_forward_freeze_v651.json'
EVIDENCE=ROOT/'evidence'/'markets_prospective'
OUT=ROOT/'manifests'/'v6_market_first_forward_readiness_v651_status.json'


def bucket(hours:float)->str:
    if hours<0:return 'POST_KICKOFF_INVALID'
    if hours<1:return 'LT_1H_TOO_LATE'
    if hours<=72:return 'H1_72_ELIGIBLE'
    if hours<=168:return 'H72_168_EARLY'
    if hours<=720:return 'D7_30_EARLY'
    return 'GT_30D_EARLY'


def identity(raw:dict[str,Any])->tuple[str,str,str,str]:
    return (str(raw.get('competition_id') or ''),str(raw.get('kickoff_utc') or ''),normalize_team_token(str(raw.get('home_team') or '')),normalize_team_token(str(raw.get('away_team') or '')))


def main()->int:
    now=datetime.now(timezone.utc).replace(microsecond=0)
    freeze=load_json(FREEZE)
    frozen_at=parse_iso_datetime(str(freeze.get('freeze_timestamp_utc') or ''),'freeze_timestamp_utc')
    files=0; before=0; rejected=Counter(); by_bucket=Counter(); by_comp=defaultdict(Counter); unique={}; eligible_unique={}
    rows=[]
    for path in sorted(EVIDENCE.glob('*.json')) if EVIDENCE.exists() else []:
        files+=1
        try:
            raw=load_json(path)
            observed=parse_iso_datetime(str(raw.get('source_observed_at_utc') or raw.get('freeze_utc') or ''),'observed')
            kickoff=parse_iso_datetime(str(raw.get('kickoff_utc') or ''),'kickoff')
            if observed<frozen_at:
                before+=1;continue
            cid=str(raw.get('competition_id') or '')
            lead=(kickoff-observed).total_seconds()/3600.0
            b=bucket(lead);by_bucket[b]+=1;by_comp[cid][b]+=1
            key=identity(raw)
            prev=unique.get(key)
            if prev is None or observed<prev['observed']:
                unique[key]={'competition_id':cid,'kickoff':kickoff,'observed':observed,'lead_hours':lead,'bucket':b,'home_team':raw.get('home_team'),'away_team':raw.get('away_team'),'path':str(path.relative_to(ROOT))}
            if b=='H1_72_ELIGIBLE' and isinstance(raw.get('one_x_two'),dict) and str(raw.get('settlement_scope') or '') in {'90m_including_stoppage','90_minutes_including_stoppage'}:
                p=eligible_unique.get(key)
                if p is None or observed<p['observed']:
                    eligible_unique[key]={'competition_id':cid,'kickoff':kickoff,'observed':observed,'lead_hours':lead,'home_team':raw.get('home_team'),'away_team':raw.get('away_team'),'path':str(path.relative_to(ROOT))}
            elif b=='H1_72_ELIGIBLE':
                rejected['eligible_timing_but_surface_or_scope_invalid']+=1
        except Exception:
            rejected['parse_or_identity_error']+=1
    unique_rows=sorted(unique.values(),key=lambda r:(r['kickoff'],r['competition_id'],str(r['home_team'])))
    eligible_rows=sorted(eligible_unique.values(),key=lambda r:(r['kickoff'],r['competition_id'],str(r['home_team'])))
    payload={
        'schema_version':'V6.5.1-market-first-forward-readiness-r1',
        'generated_at_utc':now.isoformat(),
        'status':'PASS' if eligible_rows else 'WARN_NO_ELIGIBLE_POST_FREEZE_MARKET_MATCHES',
        'freeze_timestamp_utc':frozen_at.isoformat(),
        'evidence_files_seen':files,
        'before_epoch_freeze_files':before,
        'post_freeze_files':files-before,
        'post_freeze_file_lead_buckets':dict(sorted(by_bucket.items())),
        'post_freeze_competition_buckets':{cid:dict(sorted(counts.items())) for cid,counts in sorted(by_comp.items())},
        'post_freeze_unique_matches':len(unique_rows),
        'eligible_unique_matches':len(eligible_rows),
        'eligible_matches':[{**r,'kickoff':r['kickoff'].isoformat(),'observed':r['observed'].isoformat()} for r in eligible_rows],
        'next_post_freeze_matches':[{**r,'kickoff':r['kickoff'].isoformat(),'observed':r['observed'].isoformat()} for r in unique_rows[:30]],
        'rejections':dict(sorted(rejected.items())),
        'governance':{
            'read_only_diagnostic':True,
            'prediction_generation':False,
            'historical_backfill':False,
            'frozen_lead_window_changed':False,
            'lead_window_hours':[1,72],
            'formal_weight_change':False,
            'runtime_probability_change':False,
            'current_rule_change':False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True);atomic_write_json(OUT,payload);print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())