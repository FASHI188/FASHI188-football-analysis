#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,json,math,re
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from pathlib import Path

PREFERRED=(0.5,1.5,2.5,3.5,4.5)
CUTOFFS=(24,6,1)
DT_FORMATS=("%d-%m-%Y %H:%M:%S","%d-%m-%Y %H:%M","%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y-%m-%dT%H:%M:%S")
LINE_PATTERNS=(
 re.compile(r"(?:over\s*/?\s*under|total(?:\s+goals?)?|goals?)\D{0,12}(\d+(?:\.\d+)?)",re.I),
 re.compile(r"(\d+(?:\.\d+)?)\s*(?:goals?)",re.I),
 re.compile(r"(?:over|under)\s*(\d+(?:\.\d+)?)",re.I),
)

def nh(x:str)->str:return x.replace('\ufeff','').strip().upper().replace(' ','_')
def nt(x:str)->str:return ' '.join(x.strip().casefold().split())
def dt(x:str):
    s=x.strip()
    for f in DT_FORMATS:
        try:return datetime.strptime(s,f)
        except ValueError:pass
    return None

def sha(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def sniff(path:Path):
    with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:s=f.read(65536)
    try:return csv.Sniffer().sniff(s,delimiters=',;\t|')
    except csv.Error:return csv.excel

def line_from(text:str):
    vals=[]
    for p in LINE_PATTERNS:
        for m in p.finditer(text):
            try:
                v=float(m.group(1))
                if v>0 and abs(v*2-round(v*2))<1e-9:vals.append(v)
            except:pass
    # A valid totals identity must also contain total-goal semantics.
    t=nt(text)
    if not (('over' in t or 'under' in t or 'total' in t) and ('goal' in t or 'over' in t or 'under' in t)):
        return None
    return vals[0] if vals else None

def is_soccer(x:str):return nt(x) in {'1','1.0','soccer','football'}
def is_pre(x:str):return x.strip().upper() in {'PE','PRE','PRE_EVENT','0','FALSE','N'}

def snapshot(selection_trades,scheduled,cutoff_h,mode):
    cutoff=scheduled-timedelta(hours=cutoff_h)
    chosen=[]
    for trades in selection_trades.values():
        completed=[z for z in trades if z[1] <= cutoff]
        straddle=[z for z in trades if z[0] <= cutoff < z[1]]
        if mode=='strict':
            if straddle or not completed:return None
            z=max(completed,key=lambda a:a[1]); effective=z[1]
        elif mode=='proxy':
            if not completed:return None
            z=max(completed,key=lambda a:a[1]); effective=z[1]
        else:raise ValueError(mode)
        chosen.append((z[2],effective))
    if len(chosen)<2:return None
    return {'n_selections':len(chosen),'latest_effective':max(x[1] for x in chosen).isoformat()}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-dir',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    root=Path(a.source_dir);paths=sorted(p for p in root.rglob('*.csv') if p.is_file())
    files=[];headers=[];stats=Counter();forbidden_present=set();forbidden_values_materialized=0
    markets={}; candidate_event_names=Counter(); discovered=Counter(); time_min=time_max=None
    required={'SPORTS_ID','EVENT_ID','FULL_DESCRIPTION','SCHEDULED_OFF','EVENT','SELECTION_ID','SELECTION','ODDS','LATEST_TAKEN','FIRST_TAKEN','IN_PLAY'}
    forbidden={'WIN_FLAG','SETTLED_DATE','RESULT','SCORE','GOALS'}
    for path in paths:
        files.append({'path':str(path.relative_to(root)),'bytes':path.stat().st_size,'sha256':sha(path)})
        d=sniff(path)
        with path.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            r=csv.reader(f,d)
            try:h=next(r)
            except StopIteration:continue
            H=[nh(x) for x in h];headers.append(H);idx={x:i for i,x in enumerate(H)};forbidden_present |= (set(H)&forbidden)
            if required-set(H):stats['files_missing_required']+=1;continue
            for row in r:
                stats['rows_seen']+=1
                if len(row)<len(H):stats['short_rows']+=1;continue
                if not is_soccer(row[idx['SPORTS_ID']]):continue
                stats['soccer_rows']+=1
                if not is_pre(row[idx['IN_PLAY']]):continue
                stats['soccer_pre_rows']+=1
                event=row[idx['EVENT']].strip();desc=row[idx['FULL_DESCRIPTION']].strip();sel=row[idx['SELECTION']].strip()
                text=' | '.join((event,desc,sel));ln=line_from(text)
                if ln is None:continue
                stats['ou_candidate_rows']+=1;discovered[str(ln)]+=1;candidate_event_names[event[:120]]+=1
                scheduled=dt(row[idx['SCHEDULED_OFF']]);first=dt(row[idx['FIRST_TAKEN']]);latest=dt(row[idx['LATEST_TAKEN']])
                if not scheduled or not first or not latest or latest<first:stats['bad_time_rows']+=1;continue
                if first>=scheduled:continue
                latest=min(latest,scheduled-timedelta(microseconds=1))
                try:o=float(row[idx['ODDS']])
                except:continue
                if not math.isfinite(o) or o<=1:continue
                eid=row[idx['EVENT_ID']].strip();sid=row[idx['SELECTION_ID']].strip()
                if not eid or not sid:continue
                key=(eid,ln)
                m=markets.setdefault(key,{'scheduled':scheduled,'scheduled_values':set(),'selection_names':{},'trades':defaultdict(list)})
                m['scheduled_values'].add(scheduled);m['selection_names'][sid]=sel;m['trades'][sid].append((first,latest,o))
                time_min=first if time_min is None else min(time_min,first);time_max=latest if time_max is None else max(time_max,latest)
    clean={k:v for k,v in markets.items() if len(v['scheduled_values'])==1 and len(v['trades'])>=2}
    event_lines=defaultdict(set);strict=defaultdict(dict);proxy=defaultdict(dict)
    for (eid,ln),m in clean.items():
        event_lines[eid].add(ln)
        for c in CUTOFFS:
            if snapshot(m['trades'],m['scheduled'],c,'strict'):strict[ln][c]=strict[ln].get(c,0)+1
            if snapshot(m['trades'],m['scheduled'],c,'proxy'):proxy[ln][c]=proxy[ln].get(c,0)+1
    def at_least_two_cutoffs(tbl,ln):return sum(tbl.get(ln,{}).get(c,0)>=100 for c in CUTOFFS)>=2
    strict25=at_least_two_cutoffs(strict,2.5);proxy25=at_least_two_cutoffs(proxy,2.5)
    ge2=sum(len(v & set(PREFERRED))>=2 for v in event_lines.values());ge3=sum(len(v & set(PREFERRED))>=3 for v in event_lines.values());all5=sum(set(PREFERRED)<=v for v in event_lines.values())
    if strict25:terminal='ZYGMUNT_OU_SOURCE_PASS'
    elif clean and proxy25:terminal='ZYGMUNT_OU_SOURCE_LIMITED'
    elif clean:terminal='ZYGMUNT_OU_SOURCE_LIMITED'
    else:terminal='ZYGMUNT_OU_SOURCE_STOP'
    out={
      'schema_version':'C072N11_ZYGMUNT_BETFAIR_OU_ZERO_LABEL_V1','project':'football3','source':'zygmunt/betfair-sports',
      'files':files,'headers':headers,'forbidden_columns_present':sorted(forbidden_present),'forbidden_field_values_materialized':forbidden_values_materialized,
      'stats':dict(stats),'clean_ou_event_line_markets':len(clean),'unique_ou_events':len(event_lines),'discovered_line_row_counts':dict(sorted(discovered.items(),key=lambda z:float(z[0]))),
      'preferred_line_event_counts':{str(x):sum(x in v for v in event_lines.values()) for x in PREFERRED},
      'strict_cutoff_counts':{str(ln):{f'T-{c}h':n for c,n in sorted(d.items())} for ln,d in strict.items()},
      'proxy_cutoff_counts':{str(ln):{f'T-{c}h':n for c,n in sorted(d.items())} for ln,d in proxy.items()},
      'events_ge2_preferred_lines':ge2,'events_ge3_preferred_lines':ge3,'events_all5_preferred_lines':all5,
      'candidate_event_names_top20':candidate_event_names.most_common(20),'source_time_min':time_min.isoformat() if time_min else None,'source_time_max':time_max.isoformat() if time_max else None,
      'strict_ou25_100plus_at_two_cutoffs':strict25,'proxy_ou25_100plus_at_two_cutoffs':proxy25,
      'winner_or_result_values_materialized':0,'model_fit':0,'c073_c077_scientific_results_used':False,'c070f_confirmation_opened':False,
      'terminal':terminal,'authorization':'NO_TARGET_ACCESS'
    }
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
if __name__=='__main__':main()
