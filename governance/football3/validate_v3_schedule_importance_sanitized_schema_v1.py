#!/usr/bin/env python3
import argparse,hashlib,json,re,subprocess,unicodedata
from pathlib import Path
C=Path(__file__).with_name("v3_schedule_importance_sanitized_schema_contract_v1.json")
DATE=re.compile(r"^\d{4}-\d{2}-\d{2}$"); TIME=re.compile(r"^\d{1,2}:\d{2}$")
class Stop(RuntimeError): pass
def cmd(repo,*a):
 p=subprocess.run(["git","-C",str(repo),*a],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode: raise Stop("STOP_SOURCE_GIT")
 return p.stdout
def bsha(b):
 h=hashlib.sha1(); h.update(f"blob {len(b)}\0".encode()); h.update(b); return h.hexdigest()
def blobs(repo):
 out={}
 for x in cmd(repo,"ls-tree","-r","-l","HEAD").decode().splitlines():
  h,path=x.split("\t",1); _,typ,sha,size=h.split(" ",3)
  if typ=="blob": out[path]=(sha,int(size))
 return out
def inventory(c,bs):
 r=c["inventory_rules"]; sel=[]; counts={k:0 for k in r["league_files"]}
 for path,(sha,n) in bs.items():
  q=path.split("/")
  if len(q)!=2 or q[0] not in r["allowed_seasons"] or q[1] not in counts or path==r["excluded_path"]: continue
  if any(path.startswith(x) for x in r["future_season_prefixes"]): raise Stop("STOP_FUTURE_SELECTED")
  if n<r["minimum_blob_size"]: raise Stop("STOP_BLOB_TOO_SMALL")
  sel.append((path,sha,n)); counts[q[1]]+=1
 if r["excluded_path"] not in bs: raise Stop("STOP_EXCLUDED_PATH_MISSING")
 if counts!=r["league_files"] or len(sel)!=r["expected_selected_file_count"]: raise Stop("STOP_COVERAGE")
 sel.sort(); dig=hashlib.sha256("\n".join(f"{a}|{b}|{d}" for a,b,d in sel).encode()).hexdigest()
 if dig!=c["parent_zero_label_evidence"]["inventory_sha256"]: raise Stop("STOP_PARENT_INVENTORY_SHA")
 return sel,counts,dig
class S:
 def __init__(self,b): self.b=b; self.i=0; self.n=len(b)
 def ws(self):
  while self.i<self.n and self.b[self.i] in b" \t\r\n": self.i+=1
 def ex(self,x):
  self.ws()
  if self.i>=self.n or self.b[self.i]!=x: raise Stop("STOP_JSON")
  self.i+=1
 def rawstr(self):
  self.ws()
  if self.i>=self.n or self.b[self.i]!=34: raise Stop("STOP_STRING")
  a=self.i; self.i+=1
  while self.i<self.n:
   if self.b[self.i]==34: self.i+=1; return a,self.i
   self.i+=2 if self.b[self.i]==92 else 1
  raise Stop("STOP_STRING")
 def string(self):
  a,z=self.rawstr()
  try:return json.loads(self.b[a:z].decode())
  except: raise Stop("STOP_STRING")
 def kind(self):
  self.ws()
  if self.i>=self.n: raise Stop("STOP_JSON")
  x=self.b[self.i]
  return "object" if x==123 else "array" if x==91 else "string" if x==34 else "null" if self.b.startswith(b"null",self.i) else "scalar"
 def skip(self):
  self.ws(); x=self.b[self.i]
  if x==34: self.rawstr(); return
  if x in (123,91):
   st=[125 if x==123 else 93]; self.i+=1
   while self.i<self.n and st:
    x=self.b[self.i]
    if x==34: self.rawstr(); continue
    if x==123: st.append(125)
    elif x==91: st.append(93)
    elif x==st[-1]: st.pop()
    self.i+=1
   if st: raise Stop("STOP_JSON")
   return
  while self.i<self.n and self.b[self.i] not in b",}] \t\r\n": self.i+=1
 def strnull(self):
  self.ws()
  if self.b.startswith(b"null",self.i): self.i+=4; return None
  return self.string()
def norm(x): return "".join(c for c in unicodedata.normalize("NFKC",x).casefold() if c.isalnum())
def sanitize(raw,path,allowed):
 s=S(raw); s.ex(123); rows=[]; seen_matches=False; stats={"seen":0,"done":0,"drop":0,"score":0,"opaque":0,"tp":0,"tm":0}
 while 1:
  s.ws()
  if s.b[s.i]==125: s.i+=1; break
  k=s.string(); s.ex(58)
  if k!="matches": s.skip()
  else:
   if seen_matches: raise Stop("STOP_DUP_MATCHES")
   seen_matches=True; s.ex(91); idx=0; fixtures=set(); nmap={}
   while 1:
    s.ws()
    if s.b[s.i]==93: s.i+=1; break
    s.ex(123); f={}; keys=set(); sk=None; score=False
    while 1:
     s.ws()
     if s.b[s.i]==125: s.i+=1; break
     k=s.string()
     if k in keys: raise Stop("STOP_DUP_KEY")
     keys.add(k); s.ex(58)
     if k in {"round","date","time","team1","team2"}: f[k]=s.strnull()
     elif k=="score": score=True; sk=s.kind(); stats["score"]+=1; s.skip(); stats["opaque"]+=sk=="object"
     else: s.skip()
     s.ws()
     if s.b[s.i]==44: s.i+=1
     elif s.b[s.i]!=125: raise Stop("STOP_JSON")
    stats["seen"]+=1
    if score and sk=="object":
     for k in ("date","team1","team2"):
      if not isinstance(f.get(k),str) or not f[k].strip(): raise Stop("STOP_SAFE_FIELD")
     if not DATE.fullmatch(f["date"]): raise Stop("STOP_DATE")
     if not isinstance(f.get("round"),str) or not f["round"].strip(): raise Stop("STOP_ROUND")
     t=f.get("time")
     if t is None: stats["tm"]+=1
     elif isinstance(t,str) and TIME.fullmatch(t) and 0<=int(t.split(":")[0])<=23 and 0<=int(t.split(":")[1])<=59: stats["tp"]+=1
     else: raise Stop("STOP_TIME")
     if f["team1"]==f["team2"]: raise Stop("STOP_IDENTITY")
     for team in (f["team1"],f["team2"]):
      n=norm(team)
      if not n: raise Stop("STOP_IDENTITY")
      if n in nmap and nmap[n]!=team: raise Stop("STOP_IDENTITY_COLLISION")
      nmap[n]=team
     q=(f["date"],f["team1"],f["team2"])
     if q in fixtures: raise Stop("STOP_DUP_FIXTURE")
     fixtures.add(q)
     row={"source_path":path,"row_index":idx,"round":f["round"],"date":f["date"],"time":t,"team1":f["team1"],"team2":f["team2"]}
     if set(row)!=set(allowed): raise Stop("STOP_OUTPUT_KEYS")
     rows.append(row); stats["done"]+=1
    else: stats["drop"]+=1
    idx+=1; s.ws()
    if s.b[s.i]==44: s.i+=1
    elif s.b[s.i]!=93: raise Stop("STOP_JSON")
  s.ws()
  if s.b[s.i]==44: s.i+=1
  elif s.b[s.i]!=125: raise Stop("STOP_JSON")
 s.ws()
 if s.i!=s.n or not seen_matches: raise Stop("STOP_JSON")
 return rows,stats
def receipt(c): return {"schema_version":"football3-v3-schedule-importance-sanitized-schema-receipt-v1","phase":"SANITIZED_SCHEMA_OBJECT_AUDIT","target_population":"COMPLETED_MATCHES_ONLY","parent_head_sha":c["parent_zero_label_evidence"]["head_sha"],"parent_inventory_sha256":c["parent_zero_label_evidence"]["inventory_sha256"],"source_commit_sha":c["source"]["commit_sha"],"source_tree_sha":c["source"]["tree_sha"],"selected_file_count":0,"source_objects_opened":0,"source_blob_sha_verified":0,"raw_source_bytes_read":0,"sanitized_rows_written":0,"incomplete_rows_dropped":0,"score_fields_seen":0,"score_object_spans_skipped_opaque":0,"score_values_decoded":0,"result_or_goal_values_decoded":0,"result_or_goal_values_emitted":0,"future_matches_allowed":False,"stage6_touched":False,"training":False,"tuning":False,"formal_weight":0,"matrix_delta":0,"data_ready":False}
def audit(c,repo,sout,mout):
 r=receipt(c)
 try:
  if c.get("status")!="DESIGN_LOCKED": raise Stop("STOP_CONTRACT")
  if cmd(repo,"rev-parse","HEAD").decode().strip()!=c["source"]["commit_sha"]: raise Stop("STOP_SOURCE_COMMIT")
  if cmd(repo,"rev-parse","HEAD^{tree}").decode().strip()!=c["source"]["tree_sha"]: raise Stop("STOP_SOURCE_TREE")
  bs=blobs(repo)
  for path,key in ((c["source"]["license_path"],"license_blob_sha"),(c["source"]["readme_path"],"readme_blob_sha")):
   if path not in bs or bs[path][0]!=c["source"][key]: raise Stop("STOP_PROVENANCE")
  sel,counts,dig=inventory(c,bs); r.update(selected_file_count=len(sel),league_file_counts=counts,inventory_sha256=dig)
  allrows=[]; fs=[]
  for path,sha,size in sel:
   raw=cmd(repo,"cat-file","blob",sha); r["source_objects_opened"]+=1; r["raw_source_bytes_read"]+=len(raw)
   if len(raw)!=size or bsha(raw)!=sha: raise Stop("STOP_BLOB_BINDING")
   r["source_blob_sha_verified"]+=1
   rows,st=sanitize(raw,path,c["sanitizer_contract"]["allowed_output_keys"])
   allrows+=rows; fs.append({"source_path":path,"source_blob_sha":sha,"source_blob_size":size,"sanitized_row_count":len(rows),"match_objects_seen":st["seen"],"completed_rows_written":st["done"],"incomplete_rows_dropped":st["drop"],"time_present_completed":st["tp"],"time_missing_completed":st["tm"]})
   r["incomplete_rows_dropped"]+=st["drop"]; r["score_fields_seen"]+=st["score"]; r["score_object_spans_skipped_opaque"]+=st["opaque"]
  allowed=set(c["sanitizer_contract"]["allowed_output_keys"]); forbidden=set(c["sanitizer_contract"]["forbidden_output_keys"])
  if any(set(x)!=allowed or set(x)&forbidden for x in allrows): raise Stop("STOP_OUTPUT_LEAK")
  data="".join(json.dumps(x,sort_keys=True,separators=(",",":"))+"\n" for x in allrows).encode()
  sout.parent.mkdir(parents=True,exist_ok=True); sout.write_bytes(data)
  safe=[json.loads(x) for x in sout.read_text().splitlines() if x]
  if len(safe)!=len(allrows) or any(set(x)!=allowed or set(x)&forbidden for x in safe): raise Stop("STOP_OUTPUT_REPARSE")
  manifest={"schema_version":"football3-v3-schedule-importance-sanitized-manifest-v1","source_commit_sha":c["source"]["commit_sha"],"source_tree_sha":c["source"]["tree_sha"],"parent_inventory_sha256":dig,"selected_file_count":len(sel),"sanitized_row_count":len(allrows),"files":fs,"allowed_output_keys":sorted(allowed)}
  mout.write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
  r.update(sanitized_rows_written=len(allrows),sanitized_sha256=hashlib.sha256(data).hexdigest(),manifest_sha256=hashlib.sha256(mout.read_bytes()).hexdigest(),time_present_completed=sum(x["time_present_completed"] for x in fs),time_missing_completed=sum(x["time_missing_completed"] for x in fs))
  if r["source_objects_opened"]!=94 or r["source_blob_sha_verified"]!=94: raise Stop("STOP_OBJECT_COUNT")
  if not allrows or r["score_values_decoded"] or r["result_or_goal_values_decoded"] or r["result_or_goal_values_emitted"]: raise Stop("STOP_LABEL_ACCESS")
  r["data_ready"]=True; r["decision"]="PASS_SANITIZED_SCHEMA_OBJECT_AUDIT_NEXT_SCHEDULE_FEATURE_CONSTRUCTION_PREREG"
 except Stop as e: r["decision"]=str(e)
 return r
def main():
 a=argparse.ArgumentParser(); a.add_argument("--source-repo",required=True); a.add_argument("--output",required=True); a.add_argument("--sanitized-output",required=True); a.add_argument("--manifest-output",required=True); z=a.parse_args()
 c=json.loads(C.read_text()); r=audit(c,Path(z.source_repo),Path(z.sanitized_output),Path(z.manifest_output)); Path(z.output).write_text(json.dumps(r,sort_keys=True,indent=2)+"\n"); print(json.dumps({"decision":r["decision"],"selected_file_count":r["selected_file_count"],"sanitized_rows_written":r["sanitized_rows_written"],"score_values_decoded":r["score_values_decoded"]},sort_keys=True))
if __name__=="__main__": main()
