#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,io,json,zipfile
from datetime import datetime,timezone
from pathlib import Path


def classify(columns:list[str])->dict[str,list[str]]:
    groups={k:[] for k in [
      '1X2 market','totals/over-under market','asian handicap/spread market','BTTS market',
      'team identity/history context','competition/round/venue context','lineup/injury context','score/result labels','other']}
    for col in columns:
        x=col.casefold()
        if any(k in x for k in ('score','result','goal_home','goal_away','home_goal','away_goal','fthg','ftag')):
            g='score/result labels'
        elif any(k in x for k in ('over','under','total','o2.5','u2.5','ou_','goals_line')):
            g='totals/over-under market'
        elif any(k in x for k in ('handicap','asian','spread','ah_','hcp')):
            g='asian handicap/spread market'
        elif any(k in x for k in ('btts','both_teams','both teams')):
            g='BTTS market'
        elif any(k in x for k in ('home_odd','draw_odd','away_odd','odds_home','odds_draw','odds_away','home_b','draw_b','away_b')):
            g='1X2 market'
        elif any(k in x for k in ('team','home_name','away_name','home_team','away_team','club')):
            g='team identity/history context'
        elif any(k in x for k in ('league','country','competition','round','season','venue','stadium','neutral','match_date','match_time')):
            g='competition/round/venue context'
        elif any(k in x for k in ('lineup','injury','injured','suspension','player','formation')):
            g='lineup/injury context'
        else:
            g='other'
        groups[g].append(col)
    return {k:v for k,v in groups.items() if v}


def header_from_member(z:zipfile.ZipFile,name:str)->list[str]|None:
    lower=name.casefold()
    if lower.endswith('.csv.gz'):
        with z.open(name,'r') as raw:
            with gzip.GzipFile(fileobj=raw,mode='rb') as gz:
                with io.TextIOWrapper(gz,encoding='utf-8-sig',newline='') as text:
                    return next(csv.reader(text))
    if lower.endswith('.csv'):
        with z.open(name,'r') as raw:
            with io.TextIOWrapper(raw,encoding='utf-8-sig',newline='') as text:
                return next(csv.reader(text))
    return None


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--archive',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    assert reg['status']=='PRE_REGISTERED_ARCHIVE_MANIFEST_AND_HEADERS_ONLY_NO_DATA_ROWS'
    assert reg['allowed_operations']['read_any_csv_data_record'] is False
    members=[];candidate_groups={};headers_read=0
    with zipfile.ZipFile(a.archive) as z:
        for info in z.infolist():
            item={'name':info.filename,'compressed_size':info.compress_size,'uncompressed_size':info.file_size,'is_directory':info.is_dir()}
            if not info.is_dir():
                header=header_from_member(z,info.filename)
                if header is not None:
                    headers_read+=1;item['csv_header']=header;item['column_count']=len(header);item['column_groups_from_names_only']=classify(header)
                    for group,cols in item['column_groups_from_names_only'].items():
                        candidate_groups.setdefault(group,[]).append({'member':info.filename,'columns':cols})
            members.append(item)
    result={
      'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'status':'PASS_R40A_ZERO_ROW_SOURCE_ASSET_INVENTORY','archive_member_count':len(members),'tabular_headers_parsed':headers_read,
      'members':members,'candidate_groups_from_column_names_only':candidate_groups,
      'audit':{'csv_data_records_parsed':0,'score_or_result_values_read':0,'holdout_labels_read':0,'holdout_identities_read':0,'models_fit':0,'prediction_metrics_computed':0,'identity_locks_created':0},
      'hard_limits':reg['hard_limits']}
    (a.out_dir/'orthogonal_asset_inventory_r40a.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
