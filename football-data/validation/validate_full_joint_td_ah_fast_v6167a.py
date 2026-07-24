#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import validate_full_joint_td_ah_v6167 as v
OUT=ROOT/'manifests'/'v6_full_joint_td_ah_fast_v6167a_status.json'

def main():
    rows=v.cond.load_market_rows();cfg=v.load_config();test,meta=v.eval_season(rows,'2025/26',cfg);s=v.summarize(test)
    out={'schema_version':'V6.16.7a-full-joint-TD-AH-fast-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'train_seasons':['2021/22','2022/23','2023/24','2024/25'],'test_season':'2025/26','same_method_as_v6167':True,'no_new_parameters':True},'result':s,'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(s,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
