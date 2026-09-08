#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, pathlib, sys, tempfile, unittest
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
REPO=HERE.parents[1]
BRIDGE_WORKFLOW=REPO/'.github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml'
FORMAL_WORKFLOW=REPO/'.github/workflows/football3-formal-gpt-runner-integration-v1.yml'
BINDING=HERE/'request_sha_binding_v1.py'
spec=importlib.util.spec_from_file_location('bridge',HERE/'auto_dispatch_bridge_v1.py'); bridge=importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
contract=bridge.request_contract

class T(unittest.TestCase):
    def req(self,rid='r1'):
        return {'schema_version':contract.SCHEMA,'mode':'predict','request_id':rid,'match':{'competition_id':'ITA_SerieA','season':'2026/27','kickoff':'2026-09-07T18:45:00+00:00','cutoff':'2026-09-07T17:45:00+00:00','home_team_name':'Udinese','away_team_name':'Lazio'}}
    def body(self,r): return bridge.BEGIN_MARKER+'\n'+json.dumps(r,separators=(',',':'))+'\n'+bridge.END_MARKER
    def err(self,r,code):
        with self.assertRaisesRegex(bridge.BridgeError,code): bridge.parse_request_body(self.body(r))
    def test_real_nested_and_modes(self):
        for mode in ('predict','PROSPECTIVE_FORMAL_PREDICTION','ACTIVE_AT_CUTOFF_REPLAY','CURRENT_MODEL_RETROSPECTIVE_REPLAY'):
            r=self.req(mode); r['mode']=mode; p=bridge.parse_request_body(self.body(r)); self.assertEqual(p['match']['competition_id'],'ITA_SerieA'); self.assertEqual(contract.execution_request(p)['mode'],'predict')
    def test_missing_or_illegal_mode(self):
        r=self.req(); r.pop('mode'); self.err(r,'FORMAL_REQUEST_TOP_LEVEL_REQUIRED_FIELD_MISSING')
        r=self.req(); r['mode']='BAD'; self.err(r,'FORMAL_REQUEST_MODE_INVALID')
    def test_missing_match(self):
        r=self.req(); r.pop('match'); self.err(r,'FORMAL_REQUEST_MATCH_INVALID')
    def test_invalid_competition(self):
        r=self.req(); r['match']['competition_id']='NOPE'; self.err(r,'FORMAL_REQUEST_COMPETITION_ID_INVALID')
    def test_identical_teams(self):
        r=self.req(); r['match']['away_team_name']='Udinese FC'; self.err(r,'FORMAL_REQUEST_TEAMS_IDENTICAL')
    def test_datetime_guards(self):
        r=self.req(); r['match']['kickoff']='2026-09-07T18:45:00'; self.err(r,'FORMAL_REQUEST_KICKOFF_INVALID')
        r=self.req(); r['match']['cutoff']='bad'; self.err(r,'FORMAL_REQUEST_CUTOFF_INVALID')
        r=self.req(); r['match']['cutoff']=r['match']['kickoff']; self.err(r,'FORMAL_REQUEST_CUTOFF_NOT_BEFORE_KICKOFF')
    def test_same_id_changed_content(self):
        a=bridge.parse_request_body(self.body(self.req('same'))); b=self.req('same'); b['match']['away_team_name']='Inter'; b=bridge.parse_request_body(self.body(b)); sa=contract.request_sha256(a); sb=contract.request_sha256(b); self.assertNotEqual(sa,sb); reservation,_=bridge.ledger_names('same',sa)
        with self.assertRaisesRegex(bridge.BridgeError,'AUTO_DISPATCH_REQUEST_ID_CONTENT_MISMATCH'): bridge.classify_ledgers('same',sb,[{'id':1,'name':reservation,'expired':False}])
    def test_prepare_body_race(self):
        a=bridge.parse_request_body(self.body(self.req('race'))); audit={'request_id':'race','request_sha256':contract.request_sha256(a)}; bridge.assert_request_unchanged(audit,a); b=json.loads(json.dumps(a)); b['match']['kickoff']='2026-09-07T19:45:00+00:00'
        with self.assertRaisesRegex(bridge.BridgeError,'AUTO_DISPATCH_REQUEST_CHANGED_AFTER_RESERVATION'): bridge.assert_request_unchanged(audit,b)
    def test_dispatch_payload_sha(self):
        a=bridge.parse_request_body(self.body(self.req('payload'))); sha=contract.request_sha256(a); p=bridge.build_dispatch_payload(sha); self.assertEqual(p['inputs'],{'request_pr_number':'341','expected_request_sha256':sha}); self.assertEqual(p['ref'],bridge.CANONICAL_REF)
    def test_changed_file_gate(self):
        bridge.validate_carrier_files([bridge.CARRIER_FILE])
        for files in ([bridge.CARRIER_FILE,'README.md'],[bridge.CARRIER_FILE,'.github/workflows/evil.yml'],[bridge.CARRIER_FILE,'scripts/evil.py']):
            with self.assertRaisesRegex(bridge.BridgeError,'AUTO_DISPATCH_CARRIER_CHANGED_FILES_UNAUTHORIZED'): bridge.validate_carrier_files(files)
    def test_duplicate_and_reservation(self):
        a=bridge.parse_request_body(self.body(self.req('ledger'))); sha=contract.request_sha256(a); reservation,completed=bridge.ledger_names('ledger',sha); self.assertEqual(bridge.classify_ledgers('ledger',sha,[{'id':1,'name':completed}])[0],'DUPLICATE_COMPLETED'); self.assertEqual(bridge.classify_ledgers('ledger',sha,[{'id':2,'name':reservation}])[0],'RESERVATION_CONFLICT')
    def test_canonical_move_and_permission(self):
        bridge.assert_canonical_unchanged({'canonical_execution_sha':'1'*40},'1'*40)
        with self.assertRaisesRegex(bridge.BridgeError,'AUTO_DISPATCH_CANONICAL_MOVED_AFTER_RESERVATION'): bridge.assert_canonical_unchanged({'canonical_execution_sha':'1'*40},'2'*40)
        for p in ('read','triage',None):
            with self.assertRaisesRegex(bridge.BridgeError,'AUTO_DISPATCH_ACTOR_PERMISSION_DENIED'): bridge.validate_permission(p)
    def test_new_run_located(self):
        sha='a'*64; head='b'*40; title=f'{bridge.FORMAL_RUN_PREFIX} {sha}'; runs=[{'id':1,'event':'workflow_dispatch','head_branch':bridge.CANONICAL_REF,'head_sha':head,'display_title':title},{'id':2,'event':'workflow_dispatch','head_branch':bridge.CANONICAL_REF,'head_sha':head,'display_title':title}]; self.assertEqual(bridge.select_new_formal_run({1},runs,sha,head)['id'],2)
    def test_unprivileged_candidate_workflow(self):
        text=BRIDGE_WORKFLOW.read_text(); self.assertNotIn('\n  pull_request_target:',text); self.assertNotIn('\n      actions: write',text); self.assertIn('github.event.pull_request.number != 341',text); self.assertIn('TRUSTED_AUTO_DISPATCH_TRIGGER_UNAVAILABLE',text)
    def test_formal_runner_sha_contract(self):
        text=FORMAL_WORKFLOW.read_text()
        for token in ('expected_request_sha256:','request_contract_v1','PRODUCTION_EXPECTED_REQUEST_SHA_MISSING','PRODUCTION_REQUEST_SHA_MISMATCH','PRODUCTION_REQUEST_CARRIER_CHANGED_FILES_UNAUTHORIZED','run-name:'): self.assertIn(token,text)
    def test_binding_receipt_and_full_sha_verify(self):
        spec=importlib.util.spec_from_file_location('binding',BINDING); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); r=bridge.parse_request_body(self.body(self.req('bind'))); sha=contract.request_sha256(r)
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td); (p/'r').write_bytes(contract.canonical_bytes(r)); (p/'t').write_text(json.dumps({'expected_request_sha256':sha,'request_sha256':sha,'request_sha_verified':True})); (p/'b').write_text(json.dumps({'request_sha256':sha,'request_id':'bind','request_carrier_ref':bridge.CARRIER_REF,'request_carrier_head':'1'*40,'checkout_head_sha':'2'*40,'canonical_base_ref':bridge.CANONICAL_REF,'runner_code_source':'CHECKED_OUT_LIVE_CANONICAL_BASE'})); receipt=m.verify(str(p/'r'),str(p/'t'),str(p/'b')); self.assertEqual(receipt['carrier_head_sha'],'1'*40); self.assertEqual(receipt['canonical_execution_sha'],'2'*40)
            changed=json.loads((p/'r').read_text()); changed['match']['away_team_name']='Inter'; (p/'r').write_text(json.dumps(changed))
            with self.assertRaisesRegex(m.RequestBindingError,'PRODUCTION_REQUEST_SHA_MISMATCH'): m.verify(str(p/'r'),str(p/'t'),str(p/'b'))

if __name__=='__main__': unittest.main()
