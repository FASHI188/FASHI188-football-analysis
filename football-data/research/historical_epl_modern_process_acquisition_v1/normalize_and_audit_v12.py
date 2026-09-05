from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import pathlib
import sqlite3

Q4 = decimal.Decimal('0.0001')


def q4(v) -> float:
    return float(decimal.Decimal(str(v)).quantize(Q4, rounding=decimal.ROUND_HALF_UP))


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n')


def normalize(rows: list[dict]) -> list[dict]:
    out=[]
    for src in rows:
        r=dict(src)
        r['h_ppda']=q4(r['h_ppda'])
        r['a_ppda']=q4(r['a_ppda'])
        out.append(r)
    return out


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--amendment',type=pathlib.Path,required=True)
    ap.add_argument('--old-db',type=pathlib.Path,required=True)
    ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args()
    amendment=json.loads(a.amendment.read_text(encoding='utf-8'))
    assert amendment['status']=='FROZEN_AFTER_REPLICATION_ID_NAMESPACE_DIAGNOSTIC_BEFORE_ANY_CANDIDATE_SCORING'
    assert amendment['supersedes_scientific_contract'] is False
    assert amendment['unchanged_scientific_rules']['candidate_scoring_allowed'] is False
    assert amendment['unchanged_scientific_rules']['2023_allowed'] is False
    assert amendment['unchanged_scientific_rules']['prospective_1335_allowed'] is False

    rec_path=a.out/'acquisition_receipt.json'
    old_receipt=json.loads(rec_path.read_text(encoding='utf-8'))
    assert old_receipt['candidate_scoring_performed'] is False
    assert old_receipt['historical_confirmation_2023_read'] is False
    assert old_receipt['prospective_1335_touched'] is False

    rep_path=a.out/'epl_2022_replication_process_identity.jsonl'
    modern_path=a.out/'epl_2024_2025_process_identity.jsonl'
    rep_raw=read_jsonl(rep_path)
    modern_raw=read_jsonl(modern_path)
    assert len(rep_raw)==380
    assert len(modern_raw)==760
    pre_sha=hashlib.sha256(modern_path.read_bytes()).hexdigest()

    rep=normalize(rep_raw)
    modern=normalize(modern_raw)
    write_jsonl(rep_path,rep)
    write_jsonl(modern_path,modern)

    con=sqlite3.connect(str(a.old_db)); con.row_factory=sqlite3.Row
    old=[dict(r) for r in con.execute("select id,fid,date,h_id,a_id,h_deep,a_deep,h_ppda,a_ppda from general_game_stats where league='EPL' and season=2022 order by date,id")]
    con.close()
    assert len(old)==380
    old_by_id={int(r['id']):r for r in old}
    new_by_id={int(r['understat_id']):r for r in rep}
    common=sorted(set(old_by_id)&set(new_by_id))

    provider_id_match_n=len(common)
    secondary_identity_match_n=0
    four_field_match_n=0
    mismatches=[]
    max_deep_abs=0.0
    max_ppda_abs=0.0
    for mid in common:
        x=old_by_id[mid]; y=new_by_id[mid]
        secondary=(
            str(x['date'])==str(y['datetime']) and
            int(x['h_id'])==int(y['home_team_id']) and
            int(x['a_id'])==int(y['away_team_id'])
        )
        secondary_identity_match_n+=int(secondary)
        diffs={
            'h_deep':abs(float(x['h_deep'])-float(y['h_deep'])),
            'a_deep':abs(float(x['a_deep'])-float(y['a_deep'])),
            'h_ppda':abs(float(x['h_ppda'])-float(y['h_ppda'])),
            'a_ppda':abs(float(x['a_ppda'])-float(y['a_ppda'])),
        }
        max_deep_abs=max(max_deep_abs,diffs['h_deep'],diffs['a_deep'])
        max_ppda_abs=max(max_ppda_abs,diffs['h_ppda'],diffs['a_ppda'])
        fields=all(v<=1e-12 for v in diffs.values())
        four_field_match_n+=int(secondary and fields)
        if (not secondary or not fields) and len(mismatches)<10:
            mismatches.append({'provider_id':mid,'old_fid':int(x['fid']),'secondary_identity_match':secondary,'diffs':diffs,'old':x,'new':y})

    audit={
        'old_db_rows':len(old),
        'downloaded_rows':len(rep),
        'provider_id_column_old_db':'general_game_stats.id',
        'provider_id_column_download':'Understat dates.id / understat_id',
        'old_db_fid_is_distinct_cross_source_namespace':True,
        'provider_id_match_n':provider_id_match_n,
        'provider_id_match_rate':provider_id_match_n/len(old),
        'secondary_identity_match_n':secondary_identity_match_n,
        'secondary_identity_match_rate':secondary_identity_match_n/len(old),
        'four_field_match_n_after_round_half_up_4dp':four_field_match_n,
        'four_field_match_rate_after_round_half_up_4dp':four_field_match_n/len(old),
        'max_deep_abs_diff':max_deep_abs,
        'max_ppda_abs_diff_after_normalization':max_ppda_abs,
        'mismatch_examples':mismatches,
        'passed':(
            provider_id_match_n==380 and
            secondary_identity_match_n==380 and
            four_field_match_n==380
        )
    }
    post_sha=hashlib.sha256(modern_path.read_bytes()).hexdigest()
    receipt=dict(old_receipt)
    receipt.update({
        'schema_version':'football3-epl-modern-process-acquisition-receipt-v1.2',
        'status':'EPL_MODERN_PROCESS_ACQUISITION_REPLICATION_PASS' if audit['passed'] else 'EPL_MODERN_PROCESS_ACQUISITION_REPLICATION_FAIL',
        'identity_normalization_amendment':amendment['schema_version'],
        'pre_normalization_modern_dataset_sha256':pre_sha,
        'modern_dataset_sha256':post_sha,
        'ppda_storage_normalization':'ROUND_HALF_UP_4DP_TO_MATCH_FROZEN_DB_PIPELINE',
        'replication_audit':audit,
    })
    rec_path.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,sort_keys=True))
    return 0 if audit['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
