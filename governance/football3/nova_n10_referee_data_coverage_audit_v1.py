#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any

class AuditError(RuntimeError): pass
def req(c,m):
    if not c: raise AuditError(m)
def sha256(b:bytes)->str: return hashlib.sha256(b).hexdigest()

def run(registry:Path,out:Path):
    p=json.loads(registry.read_text())
    req(p['status']=='DESIGN_LOCKED_ZERO_LABEL','STATUS')
    req(p['target']['completed_matches_only'] is True,'COMPLETED_ONLY')
    req(p['target']['competitions']==['EPL','Bundesliga','La_liga','Ligue_1','Serie_A'],'BIG5')
    req(p['hard_rules']['target_result_labels_read'] is False,'LABEL_RULE')
    req(p['hard_rules']['training_allowed'] is False and p['hard_rules']['scoring_allowed'] is False,'NO_MODEL')
    cs=p['candidates']; req(len(cs)==7,'CANDIDATE_N')
    ids=[x['id'] for x in cs]; req(len(ids)==len(set(ids)),'DUP_ID')
    d=p['discovery_summary']
    req(d['official_target_season_sample_pit_evidence_league_n']==3,'SAMPLE_LEAGUES')
    req(d['blocked_league_n']==2,'BLOCKED_N')
    req(d['full_season_big5_inventory_built'] is False and d['full_big5_data_ready'] is False,'NOT_READY')
    req(d['classification']=='STOP_DATA_COVERAGE','CLASS')
    official=[x for x in cs if x.get('competition') in p['target']['competitions']]
    req(set(x['competition'] for x in official)==set(p['target']['competitions']),'OFFICIAL_BIG5_ROWS')
    for x in cs:
        req(bool(x.get('reason')),'REASON:'+x['id'])
        if x.get('sample_pit_status')=='PASS':
            req(any(k.startswith('published') for k in x),'PASS_WITHOUT_PUBLICATION:'+x['id'])
    s=p['safety']
    req(s['result_labels_read']==0 and s['score_values_read']==0 and s['training_performed'] is False and s['scoring_performed'] is False,'SAFETY')
    req(s['formal_v2_changed'] is False and s['current_changed'] is False and s['production_changed'] is False and s['candidate_weight']==0 and s['matrix_delta']==0,'NO_STATE_CHANGE')
    out.mkdir(parents=True,exist_ok=True)
    receipt={
      'schema_version':'football3-nova-n10-referee-data-coverage-receipt-v1',
      'status':'N10_REFEREE_DATA_COVERAGE_AUDIT_COMPLETE',
      'classification':'STOP_DATA_COVERAGE',
      'exact_base':p['exact_base'],
      'registry_sha256':sha256(registry.read_bytes()),
      'candidate_source_family_n':len(cs),
      'sample_pit_evidence_leagues':d['official_target_season_sample_pit_evidence_leagues'],
      'sample_pit_evidence_league_n':d['official_target_season_sample_pit_evidence_league_n'],
      'blocked_leagues':d['blocked_leagues'],
      'blocked_league_n':d['blocked_league_n'],
      'full_big5_data_ready':False,
      'result_labels_read':0,'score_values_read':0,'training_performed':False,'scoring_performed':False,
      'formal_v2_changed':False,'current_changed':False,'production_changed':False,
      'candidate_weight':0,'matrix_delta':0,
      'next_step':'CONTINUE_FREE_LEGAL_REFEREE_SOURCE_SEARCH_OR_BUILD_OFFICIAL_ARCHIVE_COLLECTORS; DO_NOT_START_REFEREE_OOF'
    }
    (out/'coverage_receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    print(json.dumps(receipt,sort_keys=True)); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument('--registry',type=Path,required=True); a.add_argument('--out',type=Path,required=True)
    x=a.parse_args(); run(x.registry,x.out)
if __name__=='__main__': main()
