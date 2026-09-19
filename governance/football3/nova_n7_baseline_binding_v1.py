from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
EXPECTED=1826
HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
SHA="1bfa3ff8721b75d6a2e1167da788e1712bdb74508623bb03c96a81c4186f4695"
class E(RuntimeError):pass
def req(c,m):
    if not c:raise E(m)
def run(prereg:Path,baseline:Path,out:Path):
    p=json.loads(prereg.read_text());req(p['status']=='DESIGN_LOCKED_PRELABEL','PREREG');req(p['formal_v2']['head']==HEAD,'HEAD')
    b=baseline.read_bytes();req(hashlib.sha256(b).hexdigest()==SHA,'SHA')
    rows=[json.loads(x) for x in b.splitlines() if x.strip()];req(len(rows)==EXPECTED,'N');req(len({r['n2_fixture_id'] for r in rows})==EXPECTED,'DUP')
    req(all(int(r['season_start'])==2022 and r['target_label_read'] is False and r['model_head']==HEAD for r in rows),'ROW_CONTRACT')
    rec={'schema_version':'football3-nova-n7-baseline-binding-v1','status':'N7_FORMAL_V2_2022_LABEL_FREE_BINDING_PASS','development_n':EXPECTED,'development_season':2022,'prediction_sha256':SHA,'formal_v2_head':HEAD,'result_labels_read':0,'score_values_read':0,'isolated_2021_labels_read':0,'isolated_2023_labels_read':0,'isolated_2025_labels_read':0,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'candidate_weight':0,'matrix_delta':0}
    out.mkdir(parents=True,exist_ok=True);(out/'baseline_binding_receipt.json').write_text(json.dumps(rec,indent=2,sort_keys=True)+'\n');return rec
def main():
    a=argparse.ArgumentParser();a.add_argument('--prereg',type=Path,required=True);a.add_argument('--baseline',type=Path,required=True);a.add_argument('--out',type=Path,required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.baseline,x.out),sort_keys=True))
if __name__=='__main__':main()
