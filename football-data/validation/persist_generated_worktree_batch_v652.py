#!/usr/bin/env python3
"""Persist an allowlisted generated worktree as one Git commit using GitHub Git Data API.

Designed for high-cardinality evidence jobs such as V6.5.2 Active-Kambi, where the legacy Contents
API helper creates one commit per file. This helper preserves the same hard allowlist/CURRENT ban but
creates blobs + one tree + one commit, then fast-forwards the target branch. Concurrent unrelated
branch movement is handled with bounded refetch/rebase retries; force-push is never used.

Use only for generated artifacts whose producer already has its own single-run concurrency contract.
"""
from __future__ import annotations
import argparse,base64,json,os,subprocess,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[2];MAX_ATTEMPTS=5

def request(headers:dict[str,str],method:str,url:str,payload:dict[str,Any]|None=None,allow_conflict:bool=False):
 data=None if payload is None else json.dumps(payload).encode('utf-8');req=urllib.request.Request(url,data=data,headers=headers,method=method)
 try:
  with urllib.request.urlopen(req,timeout=45) as response:
   raw=response.read().decode('utf-8');return response.status,json.loads(raw) if raw else None
 except urllib.error.HTTPError as exc:
  body=exc.read().decode('utf-8',errors='replace')
  if allow_conflict and exc.code in {409,422}:return exc.code,body
  raise RuntimeError(f'GitHub API {method} {url} failed: {exc.code} {body}') from exc

def status_entries()->list[tuple[str,str]]:
 proc=subprocess.run(['git','status','--porcelain=v1','-z','--untracked-files=all'],cwd=ROOT,check=True,capture_output=True);items=proc.stdout.decode('utf-8','strict').split('\0');out=[];i=0
 while i<len(items):
  item=items[i]
  if not item:i+=1;continue
  status=item[:2];path=item[3:]
  if status[0] in {'R','C'}:
   i+=1
  out.append((status,path));i+=1
 return out

def allowed(path:str,prefixes:tuple[str,...])->bool:
 return 'CURRENT_唯一正式规则' not in Path(path).name and any(path.startswith(prefix) for prefix in prefixes)

def main()->int:
 parser=argparse.ArgumentParser();parser.add_argument('--branch',default='main');parser.add_argument('--message',required=True);parser.add_argument('--allow-prefix',action='append',dest='prefixes',required=True);args=parser.parse_args();prefixes=tuple(args.prefixes)
 repo=os.environ.get('GH_REPOSITORY') or os.environ.get('GITHUB_REPOSITORY');token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
 if not repo or not token:raise SystemExit('GH_REPOSITORY/GITHUB_REPOSITORY and GH_TOKEN/GITHUB_TOKEN required')
 entries=status_entries();blocked=[(s,p) for s,p in entries if not allowed(p,prefixes)]
 if blocked:raise SystemExit(f'refusing paths outside generated allowlist: {blocked}')
 if not entries:print(json.dumps({'status':'no_changes'}));return 0
 headers={'Accept':'application/vnd.github+json','Authorization':f'Bearer {token}','X-GitHub-Api-Version':'2022-11-28','User-Agent':'football-batch-persist-v652'};api=f'https://api.github.com/repos/{repo}'
 blobs:dict[str,str|None]={}
 for status,rel in entries:
  if 'D' in status:blobs[rel]=None;continue
  path=ROOT/rel
  if not path.is_file():raise SystemExit(f'changed generated path not regular file: {rel}')
  encoded=base64.b64encode(path.read_bytes()).decode('ascii');code,detail=request(headers,'POST',f'{api}/git/blobs',{'content':encoded,'encoding':'base64'})
  if code!=201 or not isinstance(detail,dict) or not detail.get('sha'):raise RuntimeError(f'blob create failed: {rel}: {code} {detail}')
  blobs[rel]=str(detail['sha'])
 for attempt in range(1,MAX_ATTEMPTS+1):
  _,ref=request(headers,'GET',f'{api}/git/ref/heads/{urllib.parse.quote(args.branch,safe="")}');head=str(ref['object']['sha']);_,commit=request(headers,'GET',f'{api}/git/commits/{head}');base_tree=str(commit['tree']['sha']);tree_entries=[]
  for rel,blob_sha in sorted(blobs.items()):
   tree_entries.append({'path':rel,'mode':'100644','type':'blob','sha':blob_sha})
  code,tree=request(headers,'POST',f'{api}/git/trees',{'base_tree':base_tree,'tree':tree_entries})
  if code!=201 or not isinstance(tree,dict) or not tree.get('sha'):raise RuntimeError(f'tree create failed: {code} {tree}')
  tree_sha=str(tree['sha'])
  if tree_sha==base_tree:print(json.dumps({'status':'remote_already_matches','file_count':len(entries),'attempt':attempt},ensure_ascii=False));return 0
  code,new_commit=request(headers,'POST',f'{api}/git/commits',{'message':args.message,'tree':tree_sha,'parents':[head]})
  if code!=201 or not isinstance(new_commit,dict) or not new_commit.get('sha'):raise RuntimeError(f'commit create failed: {code} {new_commit}')
  new_sha=str(new_commit['sha']);code,detail=request(headers,'PATCH',f'{api}/git/refs/heads/{urllib.parse.quote(args.branch,safe="")}',{'sha':new_sha,'force':False},allow_conflict=True)
  if code==200:
   print(json.dumps({'status':'persisted_batch','file_count':len(entries),'commit_sha':new_sha,'parent_sha':head,'attempt':attempt,'force':False},ensure_ascii=False));return 0
  if attempt==MAX_ATTEMPTS:raise RuntimeError(f'failed to fast-forward batch commit: {code} {detail}')
  time.sleep(min(5,attempt))
 return 1
if __name__=='__main__':raise SystemExit(main())