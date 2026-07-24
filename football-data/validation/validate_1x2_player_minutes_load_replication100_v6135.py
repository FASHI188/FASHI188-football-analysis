#!/usr/bin/env python3
"""V6.13.5 fixed disjoint replication of V6.13.4 360-minute load-difference filter.

Rule is frozen: p>=0.58 selections, exclude matches where expected-XI favorite accumulated
>=360 more actual club minutes than the opponent over the strictly prior 1-7 calendar-day
window. No threshold/window selection in this replication.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import validate_1x2_player_minutes_load_fast100_v6134 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_player_minutes_load_replication100_v6135_status.json'

def main():
    hist,source=base.load_club_appearances();rows=base.build(hist)
    if len(rows)<200:raise RuntimeError(f'need >=200 rows, found {len(rows)}')
    discovery=rows[-100:];test=rows[-200:-100]
    def keys(rs):return {(r['competition_id'],r['date'],r['home'],r['away']) for r in rs}
    if keys(test)&keys(discovery):raise RuntimeError('replication overlap detected')
    p58=base.stat(test,lambda r:max(r['opening'])>=0.58)
    filtered=base.stat(test,lambda r:max(r['opening'])>=0.58 and r['minute_diff7']<360)
    highload=base.stat(test,lambda r:r['minute_diff7']>=360)
    uplift=None if p58['accuracy'] is None or filtered['accuracy'] is None else (filtered['accuracy']-p58['accuracy'])*100.0
    payload={'schema_version':'V6.13.5-player-minutes-load-replication100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_ACTUAL_CLUB_MINUTES','governance':{'fixed_p58_threshold':True,'fixed_7d_window':True,'fixed_360_minute_difference_filter':True,'disjoint_from_v6134':True,'target_actual_xi_excluded':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'replication_count':100,'replication_first':test[0]['date'],'replication_last':test[-1]['date'],'discovery_first':discovery[0]['date'],'discovery_last':discovery[-1]['date']},'test':{'p_ge_0.58':p58,'p_ge_0.58_exclude_diff360':filtered,'favorite_load_diff_ge_360':highload,'all':base.stat(test)},'uplift_pp':uplift}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'sample':payload['sample'],'test':payload['test'],'uplift_pp':uplift},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
