from __future__ import annotations
import hashlib,json
from pathlib import Path
import pandas as pd
import pytest
from football3_core import Football3ContractError,key_set_sha256,ordered_key_sha256
from football3_label_access import LABEL_MANIFEST_SCHEMA,load_labels_with_frozen_manifest

def sha_bytes(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()
def file_sha(path:Path)->str:return sha_bytes(path.read_bytes())
def frozen_manifest(tmp_path:Path,label:Path,left:pd.DataFrame)->tuple[Path,str]:
 keys=[(x,) for x in left['gid'].tolist()]
 payload={'schema':LABEL_MANIFEST_SCHEMA,'keys':['gid'],'key_types':['string'],'row_count':len(keys),'ordered_keys_sha256':ordered_key_sha256(keys),'key_set_sha256':key_set_sha256(keys),'label_file_sha256':file_sha(label)}
 mp=tmp_path/'labels.identity.json';raw=(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode();mp.write_bytes(raw);return mp,sha_bytes(raw)
def test_valid_frozen_identity_manifest_allows_target_deserialization(tmp_path:Path):
 left=pd.DataFrame({'gid':['a','b'],'x':[1,2]});label=tmp_path/'labels.csv';label.write_text('gid,T\na,1\nb,2\n',encoding='utf-8');mp,msha=frozen_manifest(tmp_path,label,left)
 out=load_labels_with_frozen_manifest(left,label,mp,expected_manifest_sha256=msha,keys=['gid'],target_columns=['T'],expected_rows=2);assert out['T'].tolist()==[1,2]
def test_extra_target_row_is_blocked_before_target_deserialization(tmp_path:Path):
 left=pd.DataFrame({'gid':['a','b']});label=tmp_path/'labels.csv';label.write_text('gid,T\na,1\nb,2\n',encoding='utf-8');mp,msha=frozen_manifest(tmp_path,label,left);label.write_text('gid,T\na,1\nb,2\nc,7\n',encoding='utf-8')
 with pytest.raises(Football3ContractError,match='before target deserialization'):load_labels_with_frozen_manifest(left,label,mp,expected_manifest_sha256=msha,keys=['gid'],target_columns=['T'],expected_rows=2)
def test_missing_target_row_is_blocked_before_target_deserialization(tmp_path:Path):
 left=pd.DataFrame({'gid':['a','b']});label=tmp_path/'labels.csv';label.write_text('gid,T\na,1\nb,2\n',encoding='utf-8');mp,msha=frozen_manifest(tmp_path,label,left);label.write_text('gid,T\na,1\n',encoding='utf-8')
 with pytest.raises(Football3ContractError,match='before target deserialization'):load_labels_with_frozen_manifest(left,label,mp,expected_manifest_sha256=msha,keys=['gid'],target_columns=['T'],expected_rows=2)
def test_mutating_manifest_to_cover_extra_row_is_blocked_before_label_file_access(tmp_path:Path):
 left=pd.DataFrame({'gid':['a','b']});label=tmp_path/'labels.csv';label.write_text('gid,T\na,1\nb,2\n',encoding='utf-8');mp,msha=frozen_manifest(tmp_path,label,left);payload=json.loads(mp.read_text());label.write_text('gid,T\na,1\nb,2\nc,7\n',encoding='utf-8');payload['label_file_sha256']=file_sha(label);mp.write_text(json.dumps(payload,sort_keys=True),encoding='utf-8')
 with pytest.raises(Football3ContractError,match='before label file access'):load_labels_with_frozen_manifest(left,label,mp,expected_manifest_sha256=msha,keys=['gid'],target_columns=['T'],expected_rows=2)
def test_identity_order_or_key_type_drift_fail_closed(tmp_path:Path):
 left=pd.DataFrame({'gid':['a','b']});label=tmp_path/'labels.csv';label.write_text('gid,T\na,1\nb,2\n',encoding='utf-8');mp,msha=frozen_manifest(tmp_path,label,left)
 with pytest.raises(Football3ContractError):load_labels_with_frozen_manifest(pd.DataFrame({'gid':['b','a']}),label,mp,expected_manifest_sha256=msha,keys=['gid'],target_columns=['T'],expected_rows=2)
 with pytest.raises(Football3ContractError):load_labels_with_frozen_manifest(pd.DataFrame({'gid':[1,2]}),label,mp,expected_manifest_sha256=msha,keys=['gid'],target_columns=['T'],expected_rows=2)
