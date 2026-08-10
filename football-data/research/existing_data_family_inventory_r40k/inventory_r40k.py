#!/usr/bin/env python3
from __future__ import annotations

import hashlib,json,os,re
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path('football-data')
if not ROOT.exists(): raise SystemExit('FOOTBALL_DATA_ROOT_MISSING')
DATA_EXT={'.csv','.tsv','.json','.jsonl','.parquet','.feather','.sqlite','.db','.zip','.gz','.bz2','.xz','.pkl','.pickle','.npy','.npz'}
TOKENS={
 'market_odds':re.compile(r'odds|market|book|asian|handicap|total|btts|betfair|exchange|price|trajectory',re.I),
 'lineup_player':re.compile(r'lineup|squad|player|transfer|valuation|formation|starter|xi',re.I),
 'injury_availability':re.compile(r'injur|suspend|absence|availability',re.I),
 'event_xg_stats':re.compile(r'xg|event|shot|possession|corner|card|foul|stats?',re.I),
 'schedule_workload':re.compile(r'fixture|schedule|rest|travel|workload|congestion',re.I),
 'referee_weather':re.compile(r'referee|weather|temperature|wind|rain',re.I),
 'score_result':re.compile(r'score|result|goal',re.I),
 'training_pit':re.compile(r'training|point_in_time|pit',re.I),
}
records=[]
for p in sorted(ROOT.rglob('*')):
    if not p.is_file(): continue
    rel=p.as_posix(); size=p.stat().st_size; ext=p.suffix.lower()
    records.append({'path':rel,'size_bytes':size,'extension':ext,'is_data_extension':ext in DATA_EXT,'is_research_path':'/research/' in rel})

ext_counts=Counter(r['extension'] or '<none>' for r in records)
topdir=defaultdict(lambda:{'files':0,'bytes':0,'data_files':0})
for r in records:
    parts=Path(r['path']).parts
    top=parts[1] if len(parts)>1 else '<root>'
    topdir[top]['files']+=1; topdir[top]['bytes']+=r['size_bytes']; topdir[top]['data_files']+=int(r['is_data_extension'])

nonresearch_data=[r for r in records if r['is_data_extension'] and not r['is_research_path']]
family_hits={k:[] for k in TOKENS}
for r in nonresearch_data:
    for name,pat in TOKENS.items():
        if pat.search(r['path']): family_hits[name].append(r)

# De-duplicated candidate files by family; only metadata, never file contents.
family_summary={}
for name,rows in family_hits.items():
    family_summary[name]={
      'file_count':len(rows),
      'bytes':sum(r['size_bytes'] for r in rows),
      'largest_files':sorted(rows,key=lambda r:(-r['size_bytes'],r['path']))[:30],
    }

largest_nonresearch=sorted(nonresearch_data,key=lambda r:(-r['size_bytes'],r['path']))[:200]
all_nonresearch_paths=[r for r in nonresearch_data]

out={
 'schema_version':'R40K-EXISTING-DATA-FAMILY-INVENTORY-1.0',
 'status':'PASS_R40K_PATH_ONLY_DATA_INVENTORY',
 'root':'football-data',
 'total_files':len(records),
 'total_bytes':sum(r['size_bytes'] for r in records),
 'data_extension_files':sum(r['is_data_extension'] for r in records),
 'nonresearch_data_files':len(nonresearch_data),
 'extension_counts':dict(sorted(ext_counts.items())),
 'top_level_summary':dict(sorted(topdir.items())),
 'family_summary':family_summary,
 'largest_nonresearch_data_files':largest_nonresearch,
 'all_nonresearch_data_files':all_nonresearch_paths,
 'hard_boundaries':{
   'file_contents_read':0,
   'data_rows_read':0,
   'target_label_values_read':0,
   'model_fits':0,
   'candidate_probabilities':0,
   'external_network_requests':0,
   'football_api_requests':0,
   'new_data_collection':False,
   'formal_weight':0,
   'formal_model_mutation':False,
   'formal_data_mutation':False,
   'current_rule_mutation':False,
   'main_mutation':False,
 }
}
Path('/tmp/r40k-output').mkdir(parents=True,exist_ok=True)
raw=json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
Path('/tmp/r40k-output/r40k_inventory.json').write_text(raw,encoding='utf-8')
Path('/tmp/r40k-output/r40k_inventory.sha256').write_text(hashlib.sha256(raw.encode()).hexdigest()+'\n',encoding='ascii')
print(json.dumps({
 'status':out['status'],'total_files':out['total_files'],'data_extension_files':out['data_extension_files'],'nonresearch_data_files':out['nonresearch_data_files'],
 'top_level_summary':out['top_level_summary'],
 'family_counts':{k:{'file_count':v['file_count'],'bytes':v['bytes']} for k,v in family_summary.items()},
 'largest_nonresearch_data_files':largest_nonresearch[:50],
 'hard_boundaries':out['hard_boundaries']
},ensure_ascii=False,indent=2))
