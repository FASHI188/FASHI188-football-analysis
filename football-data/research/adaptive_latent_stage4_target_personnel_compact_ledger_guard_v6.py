#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,unicodedata
from pathlib import Path
from urllib.parse import urlsplit
IDENTITY='8bfa3c98c1895085d94c1e6f1b5b7b80b5d56d6e1eefd615b137126604d45b11'; ORDERED='499e462f6cce50f05e575a0232c73c8d8b5234ad06c70551cf49b09353bbca76'; PARENT='7a0dabbcc1cac49cab82ee842a41a2547d7e4d20'
COMPS=['ENG_PremierLeague','ESP_LaLiga','GER_Bundesliga','ITA_SerieA','FRA_Ligue1']
HOSTS={'ENG_PremierLeague':{'www.premierleague.com'},'ESP_LaLiga':{'www.uefa.com','www.fcbarcelona.com','www.realracingclub.es'},'GER_Bundesliga':{'www.bundesliga.com'},'ITA_SerieA':{'www.legaseriea.it','www.veneziafc.it'},'FRA_Ligue1':{'www.uefa.com','www.asmonaco.com','angers-sco.fr'}}
EVIDENCE={'CURRENT_ROSTER_SNAPSHOT':'CURRENT_SOURCE_COMPLETE','OFFICIAL_EDITORIAL_FULL_SQUAD':'DECLARED_FULL_EDITORIAL_SQUAD','MATCH_SQUAD_POINT':'MATCH_ONLY'}
class LedgerError(ValueError): pass
def _url(u,cid):
 p=urlsplit(u)
 if p.scheme!='https' or p.hostname not in HOSTS[cid] or p.netloc!=p.hostname or p.username or p.password or p.query or p.fragment: raise LedgerError('source url denied')
def _pid(cid,tid,u,n):
 b=json.dumps({'competition_id':cid,'team_canonical_id':tid,'source_url':u,'person_name':n},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(); return 'source-person:'+hashlib.sha256(b).hexdigest()
def validate(root:Path):
 idx=json.loads((root/'adaptive_latent_stage4_target_personnel_compact_ledger_index_v6.json').read_text())
 req={'schema','project_id','generated_at_utc','source_commit_parent','identity_lock_sha256','ordered_identity_sha256','target_row_count','target_team_count','competition_count','person_row_count','competition_person_row_counts','competition_team_counts','evidence_team_counts','files','identity_basis','formal_pit_eligible','formal_weight','real_labels_read','real_match_rows_read','training','tuning','real_scoring','provider_api','secret_access','CURRENT_change','merge','status'}
 if type(idx) is not dict or set(idx)!=req: raise LedgerError('index exact-key drift')
 if idx['schema']!='football3_adaptive_latent_stage4_target_personnel_compact_ledger_index_v6' or idx['project_id']!='football3' or idx['source_commit_parent']!=PARENT or idx['identity_lock_sha256']!=IDENTITY or idx['ordered_identity_sha256']!=ORDERED: raise LedgerError('index identity drift')
 if (idx['target_row_count'],idx['target_team_count'],idx['competition_count'],idx['person_row_count'])!=(21,38,5,1097) or set(idx['files'])!=set(COMPS) or idx['identity_basis']!='OFFICIAL_DISPLAY_NAME_SOURCE_SCOPE': raise LedgerError('frozen aggregate drift')
 if idx['formal_pit_eligible'] is not False or idx['formal_weight']!=0.0 or idx['real_labels_read']!=0 or idx['real_match_rows_read']!=0: raise LedgerError('formal/label boundary')
 for k in ('training','tuning','real_scoring','provider_api','secret_access','CURRENT_change','merge'):
  if idx[k] is not False: raise LedgerError(k+' must be false')
 teams={}; global_ids=set(); league_person={}; league_team={}; evid={}
 for cid in COMPS:
  pc=tc=0
  for fm in idx['files'][cid]:
   if type(fm) is not dict or set(fm)!={'file','sha256','team_count','person_count'}: raise LedgerError('file metadata drift')
   fn=fm['file']; b=(root/fn).read_bytes()
   if hashlib.sha256(b).hexdigest()!=fm['sha256']: raise LedgerError('file sha drift')
   lines=b.decode('utf-8').splitlines()
   if not lines or lines[0]!='football3_target_personnel_compact_ledger_v6\t'+cid: raise LedgerError('file header drift')
   local_tc=local_pc=0; current=None; local_names=set()
   for line in lines[1:]:
    a=line.split('\t')
    if a[0]=='T':
     if len(a)!=10: raise LedgerError('team field count')
     _,tid,name,authority,url,observed,published,precision,etype,scope=a
     if not tid.startswith('official-schedule-team:') or not name or not authority or not observed.endswith('Z') or precision not in {'DATE','UNKNOWN'} or etype not in EVIDENCE or EVIDENCE[etype]!=scope: raise LedgerError('team row denied')
     if (published=='-')!=(precision=='UNKNOWN'): raise LedgerError('publication precision')
     _url(url,cid); key=(cid,tid)
     if key in teams: raise LedgerError('duplicate team')
     teams[key]=name; evid[etype]=evid.get(etype,0)+1; current=(tid,url); local_names=set(); local_tc+=1; tc+=1
    elif a[0]=='P':
     if len(a)!=3 or current is None: raise LedgerError('person row placement')
     _,pos,name=a
     if pos not in {'GK','DEF','MID','FWD'}: raise LedgerError('position')
     n=unicodedata.normalize('NFKC',name).strip()
     if not n or n!=name or any(unicodedata.category(ch).startswith('C') for ch in n) or n.casefold() in local_names: raise LedgerError('person name')
     local_names.add(n.casefold()); pid=_pid(cid,current[0],current[1],n)
     if pid in global_ids: raise LedgerError('person id collision')
     global_ids.add(pid); local_pc+=1; pc+=1
    else: raise LedgerError('row type')
   if (local_tc,local_pc)!=(fm['team_count'],fm['person_count']): raise LedgerError('file counts')
  league_person[cid]=pc; league_team[cid]=tc
 inv=json.loads((root/'target_inventory_candidate_v3.json').read_text())
 if inv['target_row_count']!=21 or inv['identity_lock_sha256']!=IDENTITY or inv['ordered_identity_sha256']!=ORDERED or inv['real_target_values_read']!=0 or inv['formal_pit_eligible'] is not False: raise LedgerError('inventory drift')
 target={(x['competition_id'],x[s]) for x in inv['targets'] for s in ('home_team_id','away_team_id')}
 if target!=set(teams) or league_person!=idx['competition_person_row_counts'] or league_team!=idx['competition_team_counts'] or evid!=idx['evidence_team_counts'] or len(global_ids)!=1097: raise LedgerError('aggregate/target coverage drift')
 return {'teams':len(teams),'rows':len(global_ids),'evidence':evid,'files':sum(len(v) for v in idx['files'].values())}
if __name__=='__main__': print('PASS compact personnel ledger',validate(Path(__file__).resolve().parent))
