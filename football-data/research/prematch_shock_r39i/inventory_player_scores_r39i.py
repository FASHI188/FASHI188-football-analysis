#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,zipfile
from pathlib import Path
from datetime import datetime,timezone

DATE_NAMES={'date','datetime','game_date','valuation_date','transfer_date'}
DATE_SCAN_TABLES={'games.csv','player_valuations.csv'}
FORBIDDEN_VALUE_NAMES={'home_club_goals','away_club_goals','home_goals','away_goals','goals','assists','minutes_played','yellow_cards','red_cards','result','score'}

def norm(s:str)->str:
    return str(s).strip().lower().replace(' ','_').replace('-','_')

def parse_date(v:str):
    t=str(v).strip()
    if not t:return None
    for f in ('%Y-%m-%d','%Y-%m-%d %H:%M:%S','%d/%m/%Y'):
        try:return datetime.strptime(t[:19],f).date()
        except:pass
    return None

def fast_line_count(z:zipfile.ZipFile,info)->int:
    n=0
    with z.open(info) as raw:
        for chunk in iter(lambda:raw.read(1024*1024),b''):
            n+=chunk.count(b'\n')
    return max(0,n-1)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--zip',required=True);ap.add_argument('--registration',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    reg=json.loads(Path(a.registration).read_text())
    required=set(reg['required_candidate_tables']);preferred=set(reg['preferred_optional_tables'])
    rows=[]
    with zipfile.ZipFile(a.zip) as z:
        members={Path(i.filename).name:i for i in z.infolist() if not i.is_dir()}
        for name in sorted(required|preferred):
            item={'table':name,'present':name in members}
            if name not in members:
                rows.append(item);continue
            info=members[name]
            with z.open(info) as raw:
                header=raw.readline().decode('utf-8-sig','replace')
            cols=next(csv.reader([header])) if header else []
            date_cols=[c for c in cols if norm(c) in DATE_NAMES or norm(c).endswith('_date')]
            n=fast_line_count(z,info);dmin=dmax=None
            # games gives canonical match dates; game_lineups inherits date through game_id.
            # player_valuations needs its own timestamp. All other large tables are schema
            # + fast row count only. No outcome/performance value is interpreted.
            if name in DATE_SCAN_TABLES and date_cols:
                wanted=set(date_cols)
                with z.open(info) as raw:
                    text=(line.decode('utf-8-sig','replace') for line in raw)
                    rd=csv.DictReader(text)
                    for r in rd:
                        for c in wanted:
                            d=parse_date(r.get(c,''))
                            if d is not None:
                                dmin=d if dmin is None or d<dmin else dmin
                                dmax=d if dmax is None or d>dmax else dmax
            item.update({
              'row_count':n,'columns':cols,'date_columns':date_cols,
              'date_scanned':name in DATE_SCAN_TABLES and bool(date_cols),
              'min_date':str(dmin) if dmin else None,'max_date':str(dmax) if dmax else None,
              'linkage_columns':[c for c in cols if norm(c) in {'game_id','club_id','player_id','home_club_id','away_club_id'}],
              'lineup_type_column':[c for c in cols if norm(c) in {'type','lineup_type','position'}],
              'forbidden_value_columns_present':[c for c in cols if norm(c) in FORBIDDEN_VALUE_NAMES]
            })
            rows.append(item)
    by={x['table']:x for x in rows};missing=[x for x in required if not by.get(x,{}).get('present')]
    gl=by.get('game_lineups.csv',{})
    lineup_link_ok=gl.get('present') and {'game_id','club_id','player_id'}.issubset({norm(c) for c in gl.get('columns',[])})
    status='PASS_R39I_SOURCE_SCHEMA_FEASIBLE_ZERO_LABEL' if not missing and lineup_link_ok else 'STOP_R39I_SOURCE_SCHEMA_INSUFFICIENT'
    out={'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,'missing_required_tables':sorted(missing),'lineup_linkage_ok':bool(lineup_link_ok),'tables':rows,'audit_access':{'score_values':0,'result_labels':0,'player_match_performance_values':0,'model_fit':0,'threshold_selection':0,'fixed100_labels':0},'hard_limits':reg['hard_limits']}
    Path(a.out_dir).mkdir(parents=True,exist_ok=True);Path(a.out_dir,'source_inventory_r39i.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps({'status':status,'lineup_linkage_ok':bool(lineup_link_ok),'missing_required_tables':sorted(missing),'tables':[{k:x.get(k) for k in ('table','present','row_count','min_date','max_date','linkage_columns','lineup_type_column')} for x in rows]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
