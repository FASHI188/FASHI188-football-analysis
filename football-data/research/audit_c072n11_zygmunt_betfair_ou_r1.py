#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,json,math,re
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from pathlib import Path

PREFERRED={0.5,1.5,2.5,3.5,4.5}
CUTOFFS=(24,6,1)
MARKET_RE=re.compile(r'^Over/Under (\d+\.5) Goals$',re.I)
DT_FORMATS=("%d-%m-%Y %H:%M:%S","%d-%m-%Y %H:%M","%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y-%m-%dT%H:%M:%S")

def nh(x): return x.replace('\ufeff','').strip().upper().replace(' ','_')
def nt(x): return ' '.join(x.strip().casefold().split())
def parse_dt(x):
    s=x.strip()
    for f in DT_FORMATS:
        try:return datetime.strptime(s,f)
        except ValueError:pass
    return None

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def sniff(path):
    with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:s=f.read(65536)
    try:return csv.Sniffer().sniff(s,delimiters=',;\t|')
    except csv.Error:return csv.excel

def is_soccer(x):return nt(x) in {'1','1.0','soccer','football'}
def is_pre(x):return x.strip().upper() in {'PE','PRE','PRE_EVENT','0','FALSE','N'}

def base_description(desc,event):
    d=nt(desc); e=nt(event)
    for sep in (' / ',' | ',' - ',' : ',' > '):
        suffix=sep+e
        if d.endswith(suffix):return d[:-len(suffix)].strip(' /|-:>')
    if d.endswith(e):
        z=d[:-len(e)].strip(' /|-:>')
        if z:return z
    return d

def snap(trades_by_selection,scheduled,hours,mode):
    cutoff=scheduled-timedelta(hours=hours)
    if len(trades_by_selection)!=2:return False
    for trades in trades_by_selection.values():
        completed=[z for z in trades if z[1] <= cutoff]
        straddle=[z for z in trades if z[0] <= cutoff < z[1]]
        if mode=='strict':
            if straddle or not completed:return False
        elif mode=='proxy':
            if not completed:return False
        else:raise ValueError(mode)
    return True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-dir',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    root=Path(a.source_dir);paths=sorted(p for p in root.rglob('*.csv') if p.is_file())
    forbidden={'WIN_FLAG','SETTLED_DATE','RESULT','SCORE','GOALS'}
    required={'SPORTS_ID','EVENT_ID','FULL_DESCRIPTION','SCHEDULED_OFF','EVENT','SELECTION_ID','SELECTION','ODDS','LATEST_TAKEN','FIRST_TAKEN','IN_PLAY'}
    files=[];stats=Counter();forbidden_present=set();headers=[]
    # line_market key = deterministic reconstructed match + line. EVENT_ID is diagnostic only.
    markets={}; base_to_schedules=defaultdict(set); base_to_eventids=defaultdict(set); identity_examples={}
    for path in paths:
        files.append({'path':str(path.relative_to(root)),'bytes':path.stat().st_size,'sha256':sha(path)})
        d=sniff(path)
        with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            r=csv.reader(f,d)
            try:h=next(r)
            except StopIteration:continue
            H=[nh(x) for x in h];headers.append(H);idx={x:i for i,x in enumerate(H)};forbidden_present|=(set(H)&forbidden)
            if required-set(H):stats['files_missing_required']+=1;continue
            for row in r:
                stats['rows_seen']+=1
                if len(row)<len(H):continue
                if not is_soccer(row[idx['SPORTS_ID']]) or not is_pre(row[idx['IN_PLAY']]):continue
                event=row[idx['EVENT']].strip();m=MARKET_RE.fullmatch(event)
                if not m:continue
                line=float(m.group(1));stats['exact_fullmatch_ou_rows']+=1
                scheduled=parse_dt(row[idx['SCHEDULED_OFF']]);first=parse_dt(row[idx['FIRST_TAKEN']]);latest=parse_dt(row[idx['LATEST_TAKEN']])
                if scheduled is None or first is None or latest is None or latest<first:stats['bad_time_rows']+=1;continue
                if first>=scheduled:continue
                latest=min(latest,scheduled-timedelta(microseconds=1))
                try:o=float(row[idx['ODDS']])
                except:continue
                if not math.isfinite(o) or o<=1:continue
                desc=row[idx['FULL_DESCRIPTION']].strip();base=base_description(desc,event)
                if not base:stats['empty_base_identity']+=1;continue
                sched_key=scheduled.isoformat();match_key=f'{base}||{sched_key}'
                event_id=row[idx['EVENT_ID']].strip();sid=row[idx['SELECTION_ID']].strip();sname=nt(row[idx['SELECTION']])
                if not sid or not ('over' in sname or 'under' in sname):continue
                key=(match_key,line)
                z=markets.setdefault(key,{'base':base,'scheduled':scheduled,'event_ids':set(),'trades':defaultdict(list),'selection_names':{}})
                z['event_ids'].add(event_id);z['selection_names'][sid]=sname;z['trades'][sid].append((first,latest,o))
                base_to_schedules[base].add(sched_key);base_to_eventids[base].add(event_id)
                if len(identity_examples)<10:identity_examples.setdefault(match_key,{'description':desc,'event':event,'line':line})
    # fail closed on malformed line markets; multiple EVENT_IDs for same match-line are allowed diagnostically but counted.
    clean={k:v for k,v in markets.items() if len(v['trades'])==2}
    duplicate_line_markets=sum(len(v['event_ids'])>1 for v in clean.values())
    match_lines=defaultdict(set)
    strict_avail={c:defaultdict(set) for c in CUTOFFS};proxy_avail={c:defaultdict(set) for c in CUTOFFS}
    per_line=Counter();strict_line={c:Counter() for c in CUTOFFS};proxy_line={c:Counter() for c in CUTOFFS}
    for (mk,line),v in clean.items():
        match_lines[mk].add(line);per_line[str(line)]+=1
        for c in CUTOFFS:
            if snap(v['trades'],v['scheduled'],c,'strict'):
                strict_avail[c][mk].add(line);strict_line[c][str(line)]+=1
            if snap(v['trades'],v['scheduled'],c,'proxy'):
                proxy_avail[c][mk].add(line);proxy_line[c][str(line)]+=1
    def counts(avail,c):
        vals=[lines & PREFERRED for lines in avail[c].values()]
        return {'ge2':sum(len(x)>=2 for x in vals),'ge3':sum(len(x)>=3 for x in vals),'all5':sum(PREFERRED<=x for x in vals)}
    strict_multi={f'T-{c}h':counts(strict_avail,c) for c in CUTOFFS};proxy_multi={f'T-{c}h':counts(proxy_avail,c) for c in CUTOFFS}
    all_mks=set(proxy_avail[6])|set(proxy_avail[1])
    joint_proxy_ge2=joint_proxy_ge3=joint_strict_ge2=joint_strict_ge3=0
    for mk in all_mks:
        p=(proxy_avail[6].get(mk,set()) & proxy_avail[1].get(mk,set()) & PREFERRED)
        s=(strict_avail[6].get(mk,set()) & strict_avail[1].get(mk,set()) & PREFERRED)
        joint_proxy_ge2+=len(p)>=2;joint_proxy_ge3+=len(p)>=3;joint_strict_ge2+=len(s)>=2;joint_strict_ge3+=len(s)>=3
    crossline=sum(len(lines & PREFERRED)>=2 for lines in match_lines.values())
    if joint_proxy_ge2>=100:terminal='ZYGMUNT_R1_MULTILINE_STRUCTURE_PASS'
    elif crossline>0:terminal='ZYGMUNT_R1_MULTILINE_STRUCTURE_LIMITED'
    else:terminal='ZYGMUNT_R1_MULTILINE_STRUCTURE_STOP'
    out={
      'schema_version':'C072N11_ZYGMUNT_R1_MATCH_IDENTITY_V1','project':'football3','source':'zygmunt/betfair-sports',
      'files':files,'headers':headers,'forbidden_columns_present':sorted(forbidden_present),'forbidden_field_values_materialized':0,'winner_or_result_values_materialized':0,
      'stats':dict(stats),'clean_exact_ou_line_markets':len(clean),'reconstructed_matches':len(match_lines),'matches_with_ge2_preferred_lines_anytime':crossline,
      'matches_with_ge3_preferred_lines_anytime':sum(len(v&PREFERRED)>=3 for v in match_lines.values()),'matches_with_all5_preferred_lines_anytime':sum(PREFERRED<=v for v in match_lines.values()),
      'per_line_reconstructed_match_counts':dict(sorted(per_line.items(),key=lambda z:float(z[0]))),'duplicate_match_line_multiple_event_ids':duplicate_line_markets,
      'base_descriptions_with_multiple_scheduled_off':sum(len(v)>1 for v in base_to_schedules.values()),'identity_examples':list(identity_examples.values()),
      'strict_per_line_cutoff':{f'T-{c}h':dict(strict_line[c]) for c in CUTOFFS},'proxy_per_line_cutoff':{f'T-{c}h':dict(proxy_line[c]) for c in CUTOFFS},
      'strict_multiline_cutoff':strict_multi,'proxy_multiline_cutoff':proxy_multi,
      'joint_T6_T1_same_lines':{'strict_ge2':joint_strict_ge2,'strict_ge3':joint_strict_ge3,'proxy_ge2':joint_proxy_ge2,'proxy_ge3':joint_proxy_ge3},
      'model_fit':0,'c073_c077_scientific_results_used':False,'c070f_confirmation_opened':False,'terminal':terminal,'authorization':'NO_TARGET_ACCESS'
    }
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
if __name__=='__main__':main()
