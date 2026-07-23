#!/usr/bin/env python3
"""V6.5.2 prediction-only runtime wrapper for the frozen V6.5.1 market-first epoch.

V6.5.1's prediction rule, freeze timestamp, de-vig method, 1-72h lead gate, thresholds and immutable
ledger schema are unchanged. This wrapper disables the legacy processed-training-repository
`settle_open()` path. RESULT_SETTLED events are written only by
`v6_market_first_result_resolver_v651.py`, which binds an official result receipt/hash.

This closes dual-writer settlement risk without rewriting any existing prediction or result event.
"""
from __future__ import annotations
import json,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_market_first_forward_v651 as base

def prediction_only_settle_scan(now,ledger)->dict[str,Any]:
    predictions=base.prediction_events(ledger);settled=base.settlement_events(ledger)
    return {'legacy_processed_settlement_disabled':len(predictions)-len(settled),'official_result_resolver_only_writer':True}

def main()->int:
    base.settle_open=prediction_only_settle_scan
    code=base.main()
    result=json.loads(base.OUT.read_text(encoding='utf-8'));g=result.setdefault('governance',{});g['legacy_processed_training_settlement_disabled']=True;g['official_result_resolver_only_settlement_writer']=True;g['prediction_rule_unchanged_by_v652_wrapper']=True;g['formal_weight_change']=False;g['runtime_probability_change']=False;g['current_rule_change']=False
    base.OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result.get('status'),'evaluation_status':result.get('evaluation_status'),'prediction_count':result.get('prediction_count'),'settled_count':result.get('settled_count'),'open_prediction_count':result.get('open_prediction_count'),'settlement_scan':result.get('settlement_scan'),'governance':{k:g.get(k) for k in ('legacy_processed_training_settlement_disabled','official_result_resolver_only_settlement_writer','prediction_rule_unchanged_by_v652_wrapper')}},ensure_ascii=False,indent=2))
    return code
if __name__=='__main__':raise SystemExit(main())