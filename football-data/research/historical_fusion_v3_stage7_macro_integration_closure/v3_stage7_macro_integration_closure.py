#!/usr/bin/env python3
import argparse, json, pathlib, hashlib

EQ_STATUS='STAGE7_ORIGINAL_COMPOSITION_EQUIVALENT_TO_V324_BLOCKED_BY_EXISTING_FOLD_LL'
MISMATCH_STATUS='STAGE7_CLOSURE_CONTRACT_MISMATCH_STOP'

def load(path):
    return json.loads(pathlib.Path(path).read_text())

def dump(path,obj):
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n')

def artifact_file(root, rel):
    p=pathlib.Path(root)/rel
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p

def internal_sha_audit(root):
    root=pathlib.Path(root)
    m=load(root/'artifact_manifest.json')
    errs=[]
    for rel,rec in m.get('files',{}).items():
        p=root/rel
        if not p.is_file(): errs.append({'file':rel,'error':'missing'}); continue
        got=hashlib.sha256(p.read_bytes()).hexdigest()
        if got!=rec.get('sha256'): errs.append({'file':rel,'error':'sha256','got':got,'expected':rec.get('sha256')})
    return m,errs

def close(contract_path,v311_root,v324_root,usr1_root,v326_root,out_dir):
    c=load(contract_path)
    out=pathlib.Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    checks={}
    def ck(name, value): checks[name]=bool(value)

    m311,e311=internal_sha_audit(v311_root); m324,e324=internal_sha_audit(v324_root); musr,eusr=internal_sha_audit(usr1_root); m326,e326=internal_sha_audit(v326_root)
    c311=load(artifact_file(v311_root,'contracts/V3_1_1_JOINT_SCORE_CONTRACT.json'))
    c324=load(artifact_file(v324_root,'contracts/V3_2_4_MINIMAL_BOUNDARY_PROJECTION_CONTRACT.json'))
    f324=load(artifact_file(v324_root,'evidence/final_status.json'))
    fusr=load(artifact_file(usr1_root,'evidence/final_status.json'))
    f326=load(artifact_file(v326_root,'evidence/final_status.json'))

    lin=c['frozen_lineage']
    ck('contract_frozen',c.get('status')=='FROZEN_BEFORE_STAGE7_CLOSURE_AUDIT')
    ck('no_new_parameters',all(c['original_stage7_composition'][k]=='NONE' for k in ('new_parameters','new_thresholds','new_selector','new_score_shape_mechanism')))
    ck('no_new_label_scoring',c['data_roles']['2019_2022']=='ALREADY_CONSUMED_BY_FROZEN_COMPONENTS_NO_NEW_SCORING')
    ck('v311_internal_sha',not e311); ck('v324_internal_sha',not e324); ck('usr1_internal_sha',not eusr); ck('v326_internal_sha',not e326)
    ck('v311_head',m311.get('head')==lin['v3_1_1']['head'])
    ck('v324_head',m324.get('head')==lin['v3_2_4']['head'])
    ck('usr1_head',musr.get('head')==lin['usr1_upset_safe']['head'])
    ck('v326_head',m326.get('head')==lin['v3_2_6']['head'])
    ck('formal_head_consistent',m311.get('formal_v2_head')==m324.get('formal_v2_head')==musr.get('formal_v2_head')==m326.get('formal_v2_head')==lin['formal_v2']['head'])
    ck('usr1_passed_research',musr.get('status')==lin['usr1_upset_safe']['status'] and fusr.get('research_only') is True and fusr.get('promotion_allowed') is False)
    usr=c311['frozen_inputs']['usr1']
    ck('usr1_embedded_in_v311',usr.get('artifact_id')==lin['usr1_upset_safe']['artifact_id'] and usr.get('digest')==lin['usr1_upset_safe']['artifact_digest'] and m311.get('usr1_head')==lin['usr1_upset_safe']['head'])
    mc311=c311['matrix_contract']
    ck('v311_weak_side_exact_formal',mc311.get('weak_side_region_mass')=='EXACT_FROZEN_FORMAL_V2_TARGET_WITHIN_1E-12')
    ck('v311_1x2_from_matrix',mc311.get('one_x_two_source')=='INTEGRATE_FINAL_MATRIX_ONLY' and m311.get('one_x_two_integrated_from_matrix_only') is True)
    ck('v324_uses_v311',c324['lineage'].get('v3_1_1_head')==lin['v3_1_1']['head'] and m324.get('v311_head')==lin['v3_1_1']['head'])
    ck('v324_is_stage7_direction_core',c324['projection'].get('name')=='DETERMINISTIC_MINIMUM_L2_TOP1_BOUNDARY_PROJECTION' and c324['projection'].get('new_learned_gate')=='NONE' and c324['projection'].get('new_threshold_grid')=='NONE')
    ck('v324_weak_floor_retained',c324['projection'].get('weak_side_floor')=='P_weak_after >= P_weak_before per fixture; if the mathematically required projection would decrease the frozen weak side, fall back exactly to frozen V3.1.1')
    ck('v324_matrix_exact_stage7',c324['matrix_contract'].get('candidate_matrix')=='FROZEN_V3_1_1_WITHIN_OUTCOME_REGION_SHAPE_I_PROJECTED_TO_MINIMAL_CANDIDATE_1X2' and c324['matrix_contract'].get('one_x_two_source')=='INTEGRATE_FINAL_MATRIX_ONLY')
    ck('v326_diagnostic_zero_weight',lin['v3_2_6'].get('prediction_weight')==0 and lin['v3_2_6'].get('role')=='DIAGNOSTIC_ONLY_NOT_STACKED' and f326.get('status')=='V3_2_6_TEMPORAL_HORIZON_CONSENSUS_REJECTED_NO_RESCUE_ON_CONSUMED_DEVELOPMENT')
    ck('v324_frozen_terminal',f324.get('status')=='V3_2_4_MINIMAL_BOUNDARY_PROJECTION_REJECTED_NO_RESCUE_ON_CONSUMED_DEVELOPMENT' and f324.get('2023_opened') is False and f324.get('3504_opened') is False)
    r=f324['rolling_2021_2022']; rchecks=r['checks']
    failed=[k for k,v in rchecks.items() if k!='all_pass' and not v]
    ck('v324_only_fold_ll_failed',failed==['fold_ll_gate'] and rchecks.get('all_pass') is False)
    ck('gate_contract_exact',c['inherited_hard_gates_2021_2022']==c324['hard_gates_2021_2022'])
    ck('formal_unchanged_receipts',all(x.get('formal_v2_unchanged') is True for x in (m311,m324,musr,m326)) and all(x.get('CURRENT_changed') is False for x in (m311,m324,musr,m326)))

    equivalent=all(checks.values())
    status=EQ_STATUS if equivalent else MISMATCH_STATUS
    receipt={
      'schema_version':'football3-v3-stage7-closure-receipt-v1','status':status,'equivalent_to_v324':equivalent,'new_label_scoring':False,
      'double_count_usr1_avoided':True,'v326_prediction_weight':0,'checks':checks,'mismatch_checks':[k for k,v in checks.items() if not v],
      'inherited_v324_status':f324.get('status'),'inherited_rolling_2021_2022':r,'inherited_failed_gates':failed,'2023_opened':False,'3504_opened':False,
      'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,
      'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,
      'scientific_interpretation':('The original Stage7 composition contains no independent prediction degree of freedom beyond the already-scored V3.2.4 candidate. USR1 is already embedded in V3.1.1 and must not be reapplied; V3.2.6 has zero prediction weight. Therefore Stage7 inherits V3.2.4 exactly and remains blocked by fold log-loss 4/6 versus required 6/6.' if equivalent else 'Frozen lineage or composition contract mismatch; no alternative post-view combination is allowed.')
    }
    final={k:receipt[k] for k in ['schema_version','status','equivalent_to_v324','new_label_scoring','double_count_usr1_avoided','v326_prediction_weight','inherited_v324_status','inherited_failed_gates','2023_opened','3504_opened','research_only','post_view','fresh_confirmation','promotion_allowed','formal_v2_unchanged','v3_1_1_unchanged','CURRENT_changed','production_pointer_changed','formal_enablement_changed','formal_weights_changed','scientific_interpretation']}
    final['rolling_2021_2022_summary']={'checks':r['checks'],'deltas':r['deltas'],'folds':r['folds'],'baseline':r['baseline'],'candidate':r['candidate'],'parity_baseline':r['parity_baseline'],'parity_candidate':r['parity_candidate'],'parity_n':r['parity_n']}
    dump(out/'closure_receipt.json',receipt);dump(out/'final_status.json',final)
    return receipt

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--contract',required=True);ap.add_argument('--v311',required=True);ap.add_argument('--v324',required=True);ap.add_argument('--usr1',required=True);ap.add_argument('--v326',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();r=close(a.contract,a.v311,a.v324,a.usr1,a.v326,a.out); print(json.dumps({'status':r['status'],'equivalent_to_v324':r['equivalent_to_v324'],'failed_gates':r['inherited_failed_gates']},sort_keys=True))
if __name__=='__main__': main()
