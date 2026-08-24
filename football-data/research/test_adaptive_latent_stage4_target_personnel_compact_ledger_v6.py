#!/usr/bin/env python3
from pathlib import Path
import json,tempfile
from adaptive_latent_stage4_target_personnel_compact_ledger_guard_v6 import validate,LedgerError
ROOT=Path(__file__).resolve().parent
r=validate(ROOT); assert r['teams']==38 and r['rows']==1097 and r['files']==9
with tempfile.TemporaryDirectory() as td:
 d=Path(td)
 for f in ROOT.glob('adaptive_latent_stage4_target_personnel_compact_ledger_*_v6.tsv'): d.joinpath(f.name).write_bytes(f.read_bytes())
 d.joinpath('adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').write_bytes((ROOT/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').read_bytes()); d.joinpath('target_inventory_candidate_v3.json').write_bytes((ROOT/'target_inventory_candidate_v3.json').read_bytes())
 idx=json.loads((d/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').read_text()); idx['real_labels_read']=1; (d/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').write_text(json.dumps(idx))
 try: validate(d)
 except LedgerError: pass
 else: raise AssertionError('label mutation accepted')
with tempfile.TemporaryDirectory() as td:
 d=Path(td)
 for f in ROOT.glob('adaptive_latent_stage4_target_personnel_compact_ledger_*_v6.tsv'): d.joinpath(f.name).write_bytes(f.read_bytes())
 d.joinpath('adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').write_bytes((ROOT/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').read_bytes()); d.joinpath('target_inventory_candidate_v3.json').write_bytes((ROOT/'target_inventory_candidate_v3.json').read_bytes())
 f=next(d.glob('*GER_Bundesliga_part01_v6.tsv')); s=f.read_text(); f.write_text(s.replace('\tGK\t','\tBAD\t',1)); idx=json.loads((d/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').read_text()); import hashlib
 for fm in idx['files']['GER_Bundesliga']:
  if fm['file']==f.name: fm['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
 (d/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').write_text(json.dumps(idx))
 try: validate(d)
 except LedgerError: pass
 else: raise AssertionError('position mutation accepted')
print('PASS compact personnel ledger adversarial fail-closed')
