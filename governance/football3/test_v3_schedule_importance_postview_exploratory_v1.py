import importlib.util,json,tempfile
from pathlib import Path
P=Path(__file__).with_name('run_v3_schedule_importance_postview_exploratory_v1.py');s=importlib.util.spec_from_file_location('m',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def test_lock():
 c=json.loads(Path(__file__).with_name('v3_schedule_importance_postview_exploratory_contract_v1.json').read_text()); assert (c['independent'],c['promotion'],c['completed_rows'],c['excluded_missing_ft'])==(False,False,30335,196); assert m.COMPLETED_N+m.MISSING_N==m.FEATURE_N
def test_base_binding(): assert m.BASE_BLOB=='62d10c76bc683269f01e0b5ba66dcf6a86d33223'
def test_completed_filter():
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)/'s';p.mkdir();(p/'x.json').write_text(json.dumps({'matches':[{'date':'d','round':'r','team1':'A','team2':'B','score':{'ft':[1,0]}},{'date':'d','round':'r','team1':'C','team2':'D'}]})); rows=[{'source_path':'s/x.json','row_index':i,'date':'d','round':'r','team1':a,'team2':b} for i,(a,b) in enumerate([('A','B'),('C','D')])]; old=(m.COMPLETED_N,m.MISSING_N);m.COMPLETED_N,m.MISSING_N=1,1
  try:k,y,z,cc=m.completed(rows,d);assert len(k)==len(z)==1 and y[('s/x.json',0)]=='H' and cc=={'H':1}
  finally:m.COMPLETED_N,m.MISSING_N=old
