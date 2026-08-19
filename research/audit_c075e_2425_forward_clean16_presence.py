#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from pathlib import Path

SOURCE_COMMIT="ea767ac28cf9a2d737bb3e4ce65aa4b1f4ac9361"
FILES=[
"2024-25/at.1.json","2024-25/at.2.json","2024-25/au.1.json","2024-25/de.2.json",
"2024-25/en.2.json","2024-25/en.3.json","2024-25/en.4.json","2024-25/es.2.json",
"2024-25/fr.2.json","2024-25/gr.1.json","2024-25/it.2.json","2024-25/ma.1.json",
"2024-25/mx.1.json","2024-25/pt.1.json","2024-25/sco.1.json","2024-25/tr.1.json"]
BANNED_TOP={"2024-25/en.1.json","2024-25/es.1.json","2024-25/it.1.json","2024-25/de.1.json","2024-25/fr.1.json","2024-25/nl.1.json","2024-25/be.1.json"}
CONSUMED={"2019/br.1.json","2019/cn.1.json","2019/jp.1.json","2020/br.1.json","2020/cn.1.json","2020/jp.1.json","2025/ar.1.json","2025/br.1.json","2025/br.2.json","2025/cn.1.json","2025/co.1.json","2025/jp.1.json","2025/mls.json"}
FAILED={"2024-25/dz.1.json","2024-25/eg.1.json"}
STR=r'"((?:\\.|[^"\\])*)"'; DATE=re.compile(r'"date"\s*:\s*'+STR); T1=re.compile(r'"team1"\s*:\s*'+STR); T2=re.compile(r'"team2"\s*:\s*'+STR); FT=re.compile(r'"ft"\s*:\s*\[')

def git(*a,cwd): return subprocess.check_output(["git",*a],cwd=cwd,text=True).strip()
def dec(x): return json.loads('"'+x+'"')
def digest(keys): return hashlib.sha256(("\n".join(sorted(keys))+"\n").encode()).hexdigest()
def objects(text):
 m=re.search(r'"matches"\s*:\s*\[',text)
 if not m: raise RuntimeError('matches array absent')
 i=m.end(); n=len(text); ins=False; esc=False; arr=1
 while i<n and arr>0:
  c=text[i]
  if ins:
   if esc: esc=False
   elif c=='\\': esc=True
   elif c=='"': ins=False
   i+=1; continue
  if c=='"': ins=True; i+=1; continue
  if c=='[': arr+=1; i+=1; continue
  if c==']': arr-=1; i+=1; continue
  if c!='{' or arr!=1: i+=1; continue
  st=i; b=0; s=False; e=False
  while i<n:
   ch=text[i]
   if s:
    if e:e=False
    elif ch=='\\':e=True
    elif ch=='"':s=False
   else:
    if ch=='"':s=True
    elif ch=='{':b+=1
    elif ch=='}':
     b-=1
     if b==0: i+=1; yield text[st:i]; break
   i+=1
  else: raise RuntimeError('unterminated object')
def ident(o):
 d=DATE.search(o); h=T1.search(o); a=T2.search(o)
 if not(d and h and a): return None
 return dec(d.group(1)),dec(h.group(1)),dec(a.group(1))

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--source-root',required=True); ap.add_argument('--out-dir',required=True); z=ap.parse_args(); root=Path(z.source_root); out=Path(z.out_dir); out.mkdir(parents=True,exist_ok=True)
 if git('rev-parse','HEAD',cwd=root)!=SOURCE_COMMIT: raise RuntimeError('source drift')
 if set(FILES)&(BANNED_TOP|CONSUMED|FAILED): raise RuntimeError('fixed pool violates exclusion')
 keys=[]; report={}; present_total=0
 for rel in FILES:
  p=root/rel
  if not p.is_file(): raise RuntimeError(f'missing {rel}')
  n=pr=0; fkeys=[]
  for o in objects(p.read_text(encoding='utf-8')):
   x=ident(o)
   if x is None: raise RuntimeError(f'identity failure {rel}')
   k=f'{rel}|{x[0]}|{x[1]}|{x[2]}'; keys.append(k); fkeys.append(k); n+=1
   if FT.search(o): pr+=1; present_total+=1
  frac=pr/n if n else 0
  report[rel]={"identity_count":n,"score_ft_array_token_present_count":pr,"score_ft_presence_fraction":frac,"git_blob_sha":git('rev-parse',f'HEAD:{rel}',cwd=root),"byte_length":p.stat().st_size,"identity_sha256":digest(fkeys)}
 dup=len(keys)-len(set(keys)); overall=present_total/len(keys); minimum=min(v['score_ft_presence_fraction'] for v in report.values())
 gate={"fixed_file_count_16":len(FILES)==16,"identity_count_ge_5000":len(keys)>=5000,"duplicate_identity_count_zero":dup==0,"overall_presence_ge_0_98":overall>=.98,"each_file_presence_ge_0_95":minimum>=.95,"consumed_overlap_zero":not(set(FILES)&CONSUMED),"viewed_top_overlap_zero":not(set(FILES)&BANNED_TOP),"failed_presence_files_excluded":not(set(FILES)&FAILED)}
 passed=all(gate.values())
 s={"schema_version":"C075E_2425_FORWARD_CLEAN16_PRESENCE_AUDIT_V1","status":"PASS_ZERO_VALUE_FORWARD_SOURCE_GATE" if passed else "FAIL_SOURCE_GATE","source":{"repository":"openfootball/football.json","commit":SOURCE_COMMIT},"fixed_file_count":len(FILES),"identity_count":len(keys),"identity_sha256":digest(keys),"duplicate_identity_count":dup,"score_ft_presence_count":present_total,"score_ft_presence_fraction":overall,"minimum_per_file_presence_fraction":minimum,"files":report,"gate":gate,"label_boundary":{"score_numbers_captured":False,"score_numbers_converted":False,"score_numbers_stored":False,"score_numbers_hashed":False,"goal_totals_computed":False,"tail_membership_computed":False,"model_fit":False,"only_score_ft_array_token_presence_inspected":True},"protected_boundaries":{"C075C_consumed_tail_labels_reused":False,"C071_reserve_52180_opened":False,"C070F_confirmation1597_opened":False,"A05_opened":False,"protected_opened":False,"unified_matrix_generated":False,"formal_weight":0}}
 (out/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 with (out/'identity_manifest.jsonl').open('w',encoding='utf-8') as f:
  for k in sorted(keys): f.write(json.dumps({'identity_key':k},ensure_ascii=False)+'\n')
 print(json.dumps(s,ensure_ascii=False,indent=2)); return 0 if passed else 2
if __name__=='__main__': raise SystemExit(main())
