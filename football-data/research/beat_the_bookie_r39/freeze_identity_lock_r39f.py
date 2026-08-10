#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,importlib.util,json
from datetime import datetime
from pathlib import Path


def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def sha_file(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical_sha(o)->str:return sha_text(json.dumps(o,sort_keys=True,separators=(',',':')))

def load_parent(path:Path):
    spec=importlib.util.spec_from_file_location('r39c_parent',path)
    if spec is None or spec.loader is None:raise RuntimeError('cannot import parent')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def setsha(xs):return sha_text('\n'.join(sorted(x['identity'] for x in xs))+'\n')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--model-registration',type=Path,required=True)
    ap.add_argument('--source-dir',type=Path,required=True)
    ap.add_argument('--parent-code',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    model=json.loads(a.model_registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_FOURTH100_IDENTITY_SELECTION_NO_LABEL_ACCESS'
    assert model['status']=='MODEL_REPRESENTATION_FROZEN_BEFORE_FOURTH100_IDENTITY_LOCK'
    assert reg['hard_limits']['winner_labels_allowed_in_this_stage'] is False
    parent=load_parent(a.parent_code)
    rows,_=parent.load_feature_rows(a.source_dir,{})
    start=datetime.fromisoformat(reg['population']['holdout_start'])
    train=[x for x in rows.values() if x['dt']<start]
    hold=[x for x in rows.values() if x['dt']>=start]
    assert len(train)==reg['population']['expected_training_eligible_rows']
    assert len(hold)==reg['population']['expected_holdout_eligible_rows']
    ordered=sorted(hold,key=lambda x:sha_text(f'51139|{x["identity"]}'))
    first,second,third,fourth=ordered[:100],ordered[100:200],ordered[200:300],ordered[300:400]
    s1,s2,s3,s4=map(setsha,(first,second,third,fourth))
    assert s1==reg['population']['first100_identity_sha256']
    assert s2==reg['population']['second100_identity_sha256']
    assert s3==reg['population']['third100_identity_sha256']
    consumed={x['identity'] for x in first+second+third};fresh={x['identity'] for x in fourth}
    assert len(fourth)==100 and not (consumed&fresh)
    csvp=a.out_dir/'locked100_r39f.csv'
    with csvp.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['identity','source_file','match_id','match_datetime'])
        for x in sorted(fourth,key=lambda z:(z['dt'],z['identity'])):
            w.writerow([x['identity'],x['source_file'],x['match_id'],x['dt'].isoformat()])
    receipt={
      'schema_version':reg['schema_version'],'status':'LOCKED_R39F_FOURTH_FIXED100_NO_LABELS',
      'registration_canonical_sha256':canonical_sha(reg),'model_registration_canonical_sha256':canonical_sha(model),
      'training_eligible_rows':len(train),'holdout_eligible_rows':len(hold),
      'first100_identity_sha256':s1,'second100_identity_sha256':s2,'third100_identity_sha256':s3,'fourth100_identity_sha256':s4,
      'first300_fourth100_overlap':0,'locked100_csv_sha256':sha_file(csvp),
      'no_label_audit':{'score_values_accessed':0,'result_values_accessed':0,'prediction_metrics_computed':0,'model_fits':0,'thresholds_selected':0},
      'hard_limits':reg['hard_limits']}
    (a.out_dir/'identity_lock_receipt_r39f.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
