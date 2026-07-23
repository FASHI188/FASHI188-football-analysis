#!/usr/bin/env python3
"""V6.6.18 ingest Bahia/Flamengo strict-current rosters from the official CBF 2026 Série A registry.

CBF's team page is registration history with columns Nome | Apelido | Clube Atual. Only rows whose
Clube Atual still resolves to the target team count as current. Historical/transferred rows are
excluded. HTTPS certificate verification is mandatory; an explicit certifi/pip CA bundle is used
when available, never an unverified SSL context. Research context only; V5.0.1 is unchanged.
"""
from __future__ import annotations
import html,json,re,ssl,unicodedata,urllib.request
from datetime import datetime,timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];EVIDENCE=ROOT/'evidence'/'team_current_roster_weekly';OUT=ROOT/'manifests'/'v6_cbf_current_roster_v6618_status.json';UA='football-v6.6.18-cbf-current-roster/2.0';MIN_PLAYERS=18;MAX_PLAYERS=60
TARGETS={
 'Bahia':{'cbf_team_id':'61377','url':'https://www.cbf.com.br/futebol-brasileiro/times/campeonato-brasileiro/serie-a/2026/61377?tab=atletas','current_club_aliases':{'bahia','ec bahia','esporte clube bahia'}},
 'Flamengo':{'cbf_team_id':'20016','url':'https://www.cbf.com.br/futebol-brasileiro/times/campeonato-brasileiro/serie-a/2026/20016?tab=atletas','current_club_aliases':{'flamengo','cr flamengo','clube de regatas do flamengo'}}}
def norm(v:str)->str:
 t=unicodedata.normalize('NFKD',str(v)).encode('ascii','ignore').decode().casefold();return ' '.join(re.findall(r'[a-z0-9]+',t))
def person_norm(v:str)->str:
 t=unicodedata.normalize('NFKC',str(v)).casefold();return ' '.join(re.findall(r'[^\W_]+',t,flags=re.UNICODE))
def trusted_context()->tuple[ssl.SSLContext,str]:
 cafile=None;source='system_default'
 try:
  import certifi  # type: ignore
  cafile=certifi.where();source='certifi'
 except Exception:
  try:
   from pip._vendor import certifi as pip_certifi  # type: ignore
   cafile=pip_certifi.where();source='pip_vendored_certifi'
  except Exception:pass
 return (ssl.create_default_context(cafile=cafile),source) if cafile else (ssl.create_default_context(),source)
class TableParser(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.in_tr=False;self.in_cell=False;self.cell_parts=[];self.row=[];self.rows=[]
 def handle_starttag(self,tag,attrs):
  tag=tag.lower()
  if tag=='tr':self.in_tr=True;self.row=[]
  elif tag in {'td','th'} and self.in_tr:self.in_cell=True;self.cell_parts=[]
 def handle_data(self,data):
  if self.in_cell:
   v=re.sub(r'\s+',' ',html.unescape(data)).strip()
   if v:self.cell_parts.append(v)
 def handle_endtag(self,tag):
  tag=tag.lower()
  if tag in {'td','th'} and self.in_cell:self.row.append(' '.join(self.cell_parts).strip());self.in_cell=False;self.cell_parts=[]
  elif tag=='tr' and self.in_tr:
   if self.row:self.rows.append(self.row)
   self.in_tr=False;self.row=[]
def fetch(url:str)->tuple[str,datetime,str|None,str]:
 context,ca_source=trusted_context();req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml'})
 with urllib.request.urlopen(req,timeout=30,context=context) as response:raw=response.read();charset=response.headers.get_content_charset() or 'utf-8'
 observed=datetime.now(timezone.utc).replace(microsecond=0)
 try:markup=raw.decode(charset,errors='strict')
 except Exception:markup=raw.decode('utf-8',errors='replace')
 return markup,observed,charset,ca_source
def extract_rows(markup:str)->tuple[list[dict[str,str]],dict[str,Any]]:
 p=TableParser();p.feed(markup);p.close();idx=None;header=None
 for i,row in enumerate(p.rows):
  n=[norm(c) for c in row]
  if len(n)>=3 and n[0]=='nome' and n[1]=='apelido' and n[2]=='clube atual':idx=i;header=row;break
 if idx is None:return [],{'table_rows_seen':len(p.rows),'reason':'cbf_nome_apelido_clube_atual_header_missing'}
 out=[]
 for row in p.rows[idx+1:]:
  if len(row)<3:continue
  nome,apelido,clube=row[0].strip(),row[1].strip(),row[2].strip()
  if nome and clube and norm(nome) not in {'nome','competicao','ano'}:out.append({'nome':nome,'apelido':apelido,'clube_atual':clube})
 return out,{'table_rows_seen':len(p.rows),'header':header,'registration_rows_parsed':len(out)}
def current_players(rows:list[dict[str,str]],aliases:set[str])->tuple[list[dict[str,Any]],dict[str,Any]]:
 target={norm(v) for v in aliases};kept=[];excluded=[];seen=set();dups=[]
 for row in rows:
  name=row['nome'].strip();key=person_norm(name)
  if norm(row['clube_atual']) not in target:excluded.append({'player_name':name,'nickname':row['apelido'],'clube_atual':row['clube_atual']});continue
  if not key:continue
  if key in seen:dups.append(name);continue
  seen.add(key);kept.append({'player_name':name,'nickname':row['apelido'] or None,'positions':[],'shirt_number':None,'squad_status':'cbf-current-club-registration','roster_source':'CBF official 2026 Serie A registration table filtered by Clube Atual'})
 return kept,{'current_rows_kept':len(kept),'historical_or_transferred_rows_excluded':len(excluded),'duplicate_current_names_collapsed':len(dups),'excluded_current_club_examples':excluded[:10]}
def main()->int:
 EVIDENCE.mkdir(parents=True,exist_ok=True);OUT.parent.mkdir(parents=True,exist_ok=True);records=[];audit=[];times=[]
 for team,cfg in TARGETS.items():
  row={'team_name':team,'source_url':cfg['url'],'cbf_team_id':cfg['cbf_team_id'],'status':'FAIL_CLOSED'}
  try:
   markup,observed,charset,ca_source=fetch(cfg['url']);times.append(observed);registrations,pa=extract_rows(markup);players,fa=current_players(registrations,set(cfg['current_club_aliases']));row.update({'source_observed_at_utc':observed.isoformat(),'http_charset':charset,'tls_ca_source':ca_source,'parse':pa,'filter':fa,'current_player_count':len(players)})
   valid=MIN_PLAYERS<=len(players)<=MAX_PLAYERS and len({person_norm(p['player_name']) for p in players})==len(players)
   if valid:
    records.append({'schema_version':'V6.6.9-current-roster-overlay-r1','competition_id':'BRA_SerieA','team_name':team,'observed_at_utc':observed.isoformat(),'roster_semantics':'CURRENT_REGISTERED_SQUAD','players':players,'sources':[{'source_name':'CBF official 2026 Campeonato Brasileiro Série A team registration page','source_url':cfg['url'],'source_tier':'tier_1_official','provider_group':'cbf_official','source_observed_at_utc':observed.isoformat(),'source_role':'current_registered_players_filtered_by_clube_atual'}],'source_metadata':{'cbf_team_id':cfg['cbf_team_id'],'competition':'Campeonato Brasileiro - Série A','year':2026,'table_semantics':'registration_history_with_current_club_column','current_club_filter_required':True,'tls_certificate_verified':True,'tls_ca_source':ca_source,'parse':pa,'filter':fa},'governance':{'current_at_observation_time':True,'single_source_player_list':True,'single_endpoint_player_list':True,'cross_source_union':False,'historical_registration_rows_excluded_when_clube_atual_differs':True,'tls_verification_required':True,'tls_verification_disabled':False,'unicode_safe_person_identity':True,'research_context_only':True,'formal_probability_use':False}});row['status']='PASS_STRICT_CURRENT'
   else:row['reason']='current_club_filtered_player_count_outside_strict_gate_or_duplicate_identity'
  except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
  audit.append(row)
 completed=max(times) if times else datetime.now(timezone.utc).replace(microsecond=0);evidence_path=None
 if records:
  evidence_path=EVIDENCE/f"cbf_current_rosters__{completed.strftime('%Y%m%dT%H%M%SZ')}.json";evidence_path.write_text(json.dumps({'schema_version':'V6.6.18-cbf-current-roster-weekly-aggregate-r2','observed_at_utc':completed.isoformat(),'records':records,'governance':{'official_cbf_source_only':True,'clube_atual_filter_mandatory':True,'historical_rows_not_counted_as_current':True,'tls_verification_required':True,'research_context_only':True,'formal_probability_use':False}},ensure_ascii=False,indent=2),encoding='utf-8')
 payload={'schema_version':'V6.6.18-cbf-current-roster-status-r2','generated_at_utc':completed.isoformat(),'formal_current_version':'V5.0.1','status':'PASS_COMPLETE' if len(records)==len(TARGETS) else 'WARN_PARTIAL' if records else 'FAIL_NO_VALID_ROSTERS','target_count':len(TARGETS),'valid_current_roster_count':len(records),'evidence_path':str(evidence_path.relative_to(ROOT)) if evidence_path else None,'audit':audit,'governance':{'official_source_only':True,'cbf_registration_history_not_assumed_current':True,'clube_atual_filter_required':True,'minimum_unique_current_players':MIN_PLAYERS,'tls_verification_required':True,'tls_verification_disabled':False,'explicit_trusted_ca_bundle_when_available':True,'no_cross_source_union':True,'research_context_only':True,'formal_probability_change':False,'formal_weight_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if records else 2
if __name__=='__main__':raise SystemExit(main())