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

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--registration',type=Path,required=True)
    ap.add_argument('--model-registration',type=Path,required=True)
    ap.add_argument('--source-dir',type=Path,required=True)
    ap.add_argument('--parent-code',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    r=json.loads(a.registration.read_text())
    assert r['status']=='PRE_REGISTERED_THIRD100_IDENTITY_SELECTION_NO_LABEL_ACCESS'
    assert r['hard_limits']['winner_labels_allowed_in_this_stage'] is False
    model=json.loads(a.model_registration.read_text())
    assert model['status']=='MODEL_REPRESENTATION_FROZEN_BEFORE_THIRD100_IDENTITY_LOCK'
    parent=load_parent(a.parent_code)
    rows,_=parent.load_feature_rows(a.source_dir,{})
    start=datetime.fromisoformat(r['population']['holdout_start'])
    train=[x for x in rows.values() if x['dt']<start]
    hold=[x for x in rows.values() if x['dt']>=start]
    assert len(train)==r['population']['expected_training_eligible_rows']
    assert len(hold)==r['population']['expected_holdout_eligible_rows']
    ordered=sorted(hold,key=lambda x:sha_text(f'51139|{x["identity"]}'))
    first,second,third=ordered[:100],ordered[100:200],ordered[200:300]
    def setsha(xs):return sha_text('\n'.join(sorted(x['identity'] for x in xs))+'\n')
    s1,s2,s3=setsha(first),setsha(second),setsha(third)
    assert s1==r['population']['first100_identity_sha256']
    assert s2==r['population']['second100_identity_sha256']
    assert not ({x['identity'] for x in first+second}&{x['identity'] for x in third})
    csvp=a.out_dir/'locked100_r39e.csv'
    with csvp.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['identity','source_file','match_id','match_datetime'])
        for x in sorted(third,key=lambda z:(z['dt'],z['identity'])):
            w.writerow([x['identity'],x['source_file'],x['match_id'],x['dt'].isoformat()])
    receipt={
      'schema_version':r['schema_version'],'status':'LOCKED_R39E_THIRD_FIXED100_NO_LABELS',
      'registration_canonical_sha256':canonical_sha(r),'model_registration_canonical_sha256':canonical_sha(model),
      'training_eligible_rows':len(train),'holdout_eligible_rows':len(hold),
      'first100_identity_sha256':s1,'second100_identity_sha256':s2,'third100_identity_sha256':s3,
      'first200_third100_overlap':0,'locked100_csv_sha256':sha_file(csvp),
      'no_label_audit':{'score_values_accessed':0,'result_values_accessed':0,'prediction_metrics_computed':0,'model_fits':0,'thresholds_selected':0},
      'hard_limits':r['hard_limits']}
    (a.out_dir/'identity_lock_receipt_r39e.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
