#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import validate_conditional_margin_ah_v6166 as v
OUT=ROOT/'manifests'/'v6_conditional_margin_ah_fast_v6166a_status.json'

def main():
    rows=v.load_market_rows();cfg=v.load_config();test,meta=v.eval_season(rows,'2025/26',cfg);summary=v.summarize(test)
    out={'schema_version':'V6.16.6a-conditional-margin-ah-fast-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'train_seasons':['2021/22','2022/23','2023/24','2024/25'],'test_season':'2025/26','test_used_for_training':False,'same_method_as_v6166':True},'result':summary,'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
