#!/usr/bin/env python3
"""V6.13.3 third disjoint Fast100 replication for the fixed p>=0.58 injury-onset exclusion.

Pre-specified rule only: among p>=0.58 market selections, exclude matches where the
market favorite's expected XI contains any player with an injury onset 1-14 days before
the target match. No threshold/window/reason selection. Replication block is the 100
injury-exposed matches immediately preceding the V6.13.2 block.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import validate_1x2_injury_onset_fast100_v6131 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_injury_onset_replication100_v6133_status.json'

def main():
    injuries,source=base.load_injury_onsets();rows=base.build_rows(injuries)
    scope=[r for r in rows if r['season'] in base.TARGET_SEASONS]
    affected=[r for r in scope if r['home_injured_players_14'] or r['away_injured_players_14']]
    if len(affected)<300:raise RuntimeError(f'need >=300 affected matches, found {len(affected)}')
    test=affected[-300:-200];block2=affected[-200:-100];block1=affected[-100:]
    def keys(rs):return {(r['competition_id'],r['date'],r['home'],r['away']) for r in rs}
    if keys(test)&keys(block2) or keys(test)&keys(block1):raise RuntimeError('replication overlap detected')
    base58=base.stat(test,lambda r:max(r['opening'])>=0.58)
    filtered=base.stat(test,lambda r:max(r['opening'])>=0.58 and r['fav_injured_players_14']==0)
    uplift=None if base58['accuracy'] is None or filtered['accuracy'] is None else (filtered['accuracy']-base58['accuracy'])*100.0
    payload={'schema_version':'V6.13.3-injury-onset-third-replication100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_INJURY_ONSET_DATE_NO_ORIGINAL_PUBLICATION_TIMESTAMP','governance':{'fixed_p58_rule_only':True,'fixed_14d_window':True,'fixed_any_expected_xi_injury_onset':True,'only_injury_from_date_used':True,'disjoint_from_v6131_v6132':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'affected_total':len(affected),'replication_count':len(test),'replication_first':test[0]['date'],'replication_last':test[-1]['date'],'replication_by_season':{s:sum(r['season']==s for r in test) for s in sorted(base.TARGET_SEASONS)}},'test':{'p_ge_0.58':base58,'p_ge_0.58_exclude_fav_any_14d':filtered,'favorite_any_14d':base.stat(test,lambda r:r['fav_injured_players_14']>=1),'all_affected14':base.stat(test)},'uplift_pp':uplift}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'sample':payload['sample'],'test':payload['test'],'uplift_pp':uplift},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
