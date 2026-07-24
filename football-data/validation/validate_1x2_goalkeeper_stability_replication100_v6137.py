#!/usr/bin/env python3
"""V6.13.7 fixed disjoint replication of goalkeeper-instability Fast100 signal.

Frozen rule from V6.13.6: among p>=0.58 market selections, exclude the match when the
market favorite used at least two distinct starting goalkeepers over its strictly prior
three same-season league matches. No threshold/window selection here.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import validate_1x2_goalkeeper_stability_fast100_v6136 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_goalkeeper_stability_replication100_v6137_status.json'

def main():
    pos,source=base.fetch_players();rows=base.build(pos);affected=[r for r in rows if r['fav_gk_unstable3'] or r['dog_gk_unstable3']]
    if len(affected)<200:raise RuntimeError(f'need >=200 affected matches, found {len(affected)}')
    discovery=affected[-100:];test=affected[-200:-100]
    def keys(rs):return {(r['competition_id'],r['date'],r['home'],r['away']) for r in rs}
    if keys(test)&keys(discovery):raise RuntimeError('replication overlap detected')
    p58=base.stat(test,lambda r:max(r['opening'])>=0.58)
    filtered=base.stat(test,lambda r:max(r['opening'])>=0.58 and not r['fav_gk_unstable3'])
    unstable=base.stat(test,lambda r:r['fav_gk_unstable3'])
    uplift=None if p58['accuracy'] is None or filtered['accuracy'] is None else (filtered['accuracy']-p58['accuracy'])*100.0
    payload={'schema_version':'V6.13.7-goalkeeper-stability-replication100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_LINEUP_GOALKEEPER_HISTORY','governance':{'fixed_p58_threshold':True,'fixed_prior3_goalkeeper_window':True,'fixed_exclude_favorite_two_plus_distinct_gk':True,'disjoint_from_v6136':True,'target_actual_lineup_excluded':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'affected_total':len(affected),'replication_count':100,'replication_first':test[0]['date'],'replication_last':test[-1]['date'],'replication_by_season':{s:sum(r['season']==s for r in test) for s in sorted(base.TARGET_SEASONS)},'discovery_first':discovery[0]['date'],'discovery_last':discovery[-1]['date']},'test':{'p_ge_0.58':p58,'p_ge_0.58_exclude_fav_unstable3':filtered,'favorite_unstable3':unstable,'all_affected':base.stat(test)},'uplift_pp':uplift}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'sample':payload['sample'],'test':payload['test'],'uplift_pp':uplift},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
