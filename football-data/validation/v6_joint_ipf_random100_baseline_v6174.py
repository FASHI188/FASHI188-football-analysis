#!/usr/bin/env python3
"""Fixed-seed random-100 baseline from V6.16.4's 9,186 cross-season rows.
Research-only. No parameter tuning; simply reuses V6.16.4 row generator and samples 100 rows.
"""
from __future__ import annotations
import json,random,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import validate_joint_market_ipf_crossseason_v6164 as v6164
from football_v460_engine import load_config
OUT=ROOT/'manifests'/'v6_joint_ipf_random100_baseline_v6174_status.json'
SEED=6174001

def avg(rows,key): return sum(float(r[key]) for r in rows)/len(rows) if rows else None

def main():
    cfg=load_config();allrows=[];meta={}
    for season in v6164.SEASONS:
        meta[season]={}
        for cid in v6164.COMPS:
            rows,m=v6164.eval_comp_season(cid,season,cfg);allrows.extend(rows);meta[season][cid]=m
    rng=random.Random(SEED);sample=rng.sample(allrows,100) if len(allrows)>=100 else list(allrows)
    summary={
      'count':len(sample),
      'prior_exact_total_top1':avg(sample,'prior_total_top1'),
      'ipf_exact_total_top1':avg(sample,'ipf_total_top1'),
      'prior_score_top1':avg(sample,'prior_score_top1'),
      'ipf_score_top1':avg(sample,'ipf_score_top1'),
      'prior_score_top3':avg(sample,'prior_score_top3'),
      'ipf_score_top3':avg(sample,'ipf_score_top3'),
      'prior_1x2_top1':avg(sample,'prior_1x2_top1'),
      'ipf_1x2_top1':avg(sample,'ipf_1x2_top1'),
      'prior_total_rps':avg(sample,'prior_total_rps'),
      'ipf_total_rps':avg(sample,'ipf_total_rps'),
    }
    payload={'schema_version':'V6.17.4-v6164-random100-baseline-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if len(sample)==100 else 'PARTIAL','classification':'RETROSPECTIVE_FIXED_SEED_RANDOM100_FROM_V6164_CROSSSEASON_ROWS','random_seed':SEED,'population_count':len(allrows),'summary':summary,'sample':[{'date':r['date'],'competition_id':r['competition_id'],'season':r['season'],'prior_total_top1':r['prior_total_top1'],'ipf_total_top1':r['ipf_total_top1'],'prior_score_top1':r['prior_score_top1'],'ipf_score_top1':r['ipf_score_top1'],'prior_score_top3':r['prior_score_top3'],'ipf_score_top3':r['ipf_score_top3'],'prior_1x2_top1':r['prior_1x2_top1'],'ipf_1x2_top1':r['ipf_1x2_top1']} for r in sample],'governance':{'research_only':True,'no_test_tuning':True,'formal_weight_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'population_count':len(allrows),'summary':summary},ensure_ascii=False,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
