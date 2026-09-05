from __future__ import annotations

import argparse
import codecs
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import re
import sqlite3
import urllib.request


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; football3-research/1.0)',
            'Referer': 'https://understat.com/league/EPL',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip',
        },
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        data = r.read()
        encoding = (r.headers.get('Content-Encoding') or '').lower()
    if not data:
        raise RuntimeError(f'empty response: {url}')
    if encoding == 'gzip' or data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return data


def parse_embedded(html: bytes, var: str):
    text = html.decode('utf-8')
    patterns = [
        rf"(?:var|let|const)\s+{re.escape(var)}\s*=\s*JSON\.parse\(\s*'((?:\\.|[^'])*)'\s*\)",
        rf'(?:var|let|const)\s+{re.escape(var)}\s*=\s*JSON\.parse\(\s*"((?:\\.|[^"])*)"\s*\)',
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.S)
        if m:
            raw = m.group(1)
            decoded = codecs.escape_decode(raw.encode('utf-8'))[0].decode('utf-8')
            return json.loads(decoded)
    raise RuntimeError(f'{var} not found')


def load_provider_payload(page_url: str, ajax_url: str) -> tuple[dict, dict]:
    # Run 33946985994 proved the Actions transport did not expose embedded
    # teamsData on the league HTML. The frozen v1.1 source amendment therefore
    # permits only the same-provider getLeagueData transport; scientific fields,
    # replication gates and season identities are unchanged.
    ajax = fetch(ajax_url)
    try:
        obj = json.loads(ajax.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Understat AJAX response is not JSON: {ajax_url}') from exc
    if not isinstance(obj, dict) or not isinstance(obj.get('teams'), dict) or not isinstance(obj.get('dates'), list):
        raise RuntimeError(f'Understat AJAX shape drift: {ajax_url}')
    receipt = {
        'transport': 'getLeagueData',
        'page_identity_url': page_url,
        'source_url': ajax_url,
        'source_response_sha256': sha256_bytes(ajax),
        'source_response_bytes': len(ajax),
        'top_level_keys': sorted(obj.keys()),
    }
    return obj, receipt


def ppda_value(hist: dict) -> float:
    p = hist.get('ppda') or {}
    att = float(p['att'])
    deff = float(p['def'])
    if not math.isfinite(att) or not math.isfinite(deff) or deff <= 0:
        raise ValueError('invalid ppda components')
    return att / deff


def norm_datetime(x: str) -> str:
    return dt.datetime.fromisoformat(str(x)).strftime('%Y-%m-%d %H:%M:%S')


def build_season(season: int, page_url: str, ajax_url: str) -> tuple[list[dict], dict]:
    payload, transport_receipt = load_provider_payload(page_url, ajax_url)
    teams = payload['teams']
    dates = payload['dates']

    by_team_date_role: dict[tuple[str, str, str], dict] = {}
    titles: dict[str, str] = {}
    for tid, obj in teams.items():
        tid = str(obj.get('id', tid))
        titles[tid] = str(obj['title'])
        for h in obj.get('history', []):
            key = (tid, norm_datetime(h['date']), str(h['h_a']))
            if key in by_team_date_role:
                raise RuntimeError(f'duplicate team/date/role {key}')
            by_team_date_role[key] = h

    rows: list[dict] = []
    missing = []
    for g in dates:
        if not bool(g.get('isResult')):
            continue
        fid = int(g['id'])
        when = norm_datetime(g['datetime'])
        hobj = g['h']
        aobj = g['a']
        hid = str(hobj['id'])
        aid = str(aobj['id'])
        hh = by_team_date_role.get((hid, when, 'h'))
        ah = by_team_date_role.get((aid, when, 'a'))
        if hh is None or ah is None:
            missing.append({'id': fid, 'datetime': when, 'home_id': hid, 'away_id': aid})
            continue
        rows.append({
            'fixture_id': f'understat:{fid}',
            'understat_id': fid,
            'season_start_year': int(season),
            'datetime': when,
            'home_team_id': int(hid),
            'away_team_id': int(aid),
            'home_team': str(hobj.get('title') or titles.get(hid, '')),
            'away_team': str(aobj.get('title') or titles.get(aid, '')),
            'h_deep': float(hh['deep']),
            'a_deep': float(ah['deep']),
            'h_ppda': ppda_value(hh),
            'a_ppda': ppda_value(ah),
        })

    receipt = {
        'season_start_year': int(season),
        **transport_receipt,
        'completed_dates_fixtures': sum(bool(x.get('isResult')) for x in dates),
        'process_rows': len(rows),
        'missing_process_join_n': len(missing),
        'missing_process_join_examples': missing[:10],
        'team_count': len(titles),
    }
    return rows, receipt


def audit_against_frozen_db(rows: list[dict], db_path: pathlib.Path, c: dict) -> dict:
    season = int(c['replication_gate']['season'])
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    dbrows = [dict(r) for r in con.execute(
        "select fid,date,h_deep,a_deep,h_ppda,a_ppda from general_game_stats where league='EPL' and season=? order by fid",
        (season,),
    )]
    con.close()
    old = {int(r['fid']): r for r in dbrows}
    new = {int(r['understat_id']): r for r in rows}
    common_ids = sorted(set(old) & set(new))
    identity_match_rate = len(common_ids) / max(1, len(old))
    deep_tol = float(c['replication_gate']['deep_abs_tolerance'])
    ppda_tol = float(c['replication_gate']['ppda_abs_tolerance'])
    four_match = 0
    mismatches = []
    max_ppda_abs = 0.0
    max_deep_abs = 0.0
    for fid in common_ids:
        oldr = old[fid]
        newr = new[fid]
        diffs = {
            'h_deep': abs(float(oldr['h_deep']) - float(newr['h_deep'])),
            'a_deep': abs(float(oldr['a_deep']) - float(newr['a_deep'])),
            'h_ppda': abs(float(oldr['h_ppda']) - float(newr['h_ppda'])),
            'a_ppda': abs(float(oldr['a_ppda']) - float(newr['a_ppda'])),
        }
        max_deep_abs = max(max_deep_abs, diffs['h_deep'], diffs['a_deep'])
        max_ppda_abs = max(max_ppda_abs, diffs['h_ppda'], diffs['a_ppda'])
        ok = (
            diffs['h_deep'] <= deep_tol and diffs['a_deep'] <= deep_tol
            and diffs['h_ppda'] <= ppda_tol and diffs['a_ppda'] <= ppda_tol
        )
        if ok:
            four_match += 1
        elif len(mismatches) < 10:
            mismatches.append({'fid': fid, 'diffs': diffs, 'old': oldr, 'new': newr})
    match_rate = four_match / max(1, len(old))
    expected = int(c['seasons']['expected_completed_fixtures_each'])
    passed = (
        len(old) == expected and len(new) == expected
        and identity_match_rate >= float(c['replication_gate']['required_fixture_identity_match_rate'])
        and match_rate >= float(c['replication_gate']['required_four_field_match_rate'])
    )
    return {
        'old_db_rows': len(old),
        'downloaded_rows': len(new),
        'common_fixture_ids': len(common_ids),
        'identity_match_rate': identity_match_rate,
        'four_field_match_n': four_match,
        'four_field_match_rate': match_rate,
        'max_deep_abs_diff': max_deep_abs,
        'max_ppda_abs_diff': max_ppda_abs,
        'mismatch_examples': mismatches,
        'passed': passed,
    }


def write_jsonl(path: pathlib.Path, rows: list[dict]):
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', type=pathlib.Path, required=True)
    ap.add_argument('--source-amendment', type=pathlib.Path, required=True)
    ap.add_argument('--old-db', type=pathlib.Path, required=True)
    ap.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text(encoding='utf-8'))
    amend = json.loads(a.source_amendment.read_text(encoding='utf-8'))
    assert c['status'] == 'FROZEN_BEFORE_NETWORK_ACQUISITION'
    assert amend['status'] == 'FROZEN_AFTER_HTML_TRANSPORT_FAILURE_BEFORE_ANY_DATA_ACCEPTANCE'
    assert amend['supersedes_scientific_contract'] is False
    assert amend['allowed_change'] == 'transport only'
    assert c['governance']['no_candidate_scoring'] is True
    assert c['governance']['prospective_1335_not_read_or_joined'] is True
    assert amend['unchanged_rules']['required_four_field_match_rate'] == c['replication_gate']['required_four_field_match_rate']
    assert amend['unchanged_rules']['modern_expected_rows'] == c['seasons']['expected_modern_completed_fixtures']

    season_rows = {}
    receipts = {}
    for s in (2022, 2024, 2025):
        rows, rec = build_season(s, c['source']['urls'][str(s)], amend['ajax_urls'][str(s)])
        season_rows[s] = rows
        receipts[str(s)] = rec

    repl = audit_against_frozen_db(season_rows[2022], a.old_db, c)
    expected = int(c['seasons']['expected_completed_fixtures_each'])
    modern = season_rows[2024] + season_rows[2025]
    modern_counts_ok = (
        len(season_rows[2024]) == expected and len(season_rows[2025]) == expected
        and len(modern) == int(c['seasons']['expected_modern_completed_fixtures'])
    )
    modern_all_finite = all(
        all(math.isfinite(float(r[k])) for k in ('h_deep', 'a_deep', 'h_ppda', 'a_ppda'))
        for r in modern
    )
    status = c['terminal']['pass'] if repl['passed'] and modern_counts_ok and modern_all_finite else c['terminal']['fail']

    write_jsonl(a.out / 'epl_2024_2025_process_identity.jsonl', modern)
    write_jsonl(a.out / 'epl_2022_replication_process_identity.jsonl', season_rows[2022])
    dataset_sha = hashlib.sha256((a.out / 'epl_2024_2025_process_identity.jsonl').read_bytes()).hexdigest()
    receipt = {
        'schema_version': 'football3-epl-modern-process-acquisition-receipt-v1.1',
        'status': status,
        'research_only': True,
        'provider': c['source']['provider'],
        'transport_amendment': amend['schema_version'],
        'season_receipts': receipts,
        'replication_audit': repl,
        'modern_counts_ok': modern_counts_ok,
        'modern_all_finite': modern_all_finite,
        'modern_process_rows': len(modern),
        'modern_dataset_sha256': dataset_sha,
        'result_or_goal_fields_written_to_dataset': False,
        'training_performed': False,
        'tuning_performed': False,
        'candidate_scoring_performed': False,
        'historical_confirmation_2023_read': False,
        'prospective_1335_touched': False,
        'formal_v2_changed': False,
        'frozen_v3_1_1_changed': False,
        'stage6_b_changed': False,
        'CURRENT_changed': False,
        'production_pointer_changed': False,
        'formal_enablement_changed': False,
    }
    (a.out / 'acquisition_receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, sort_keys=True))
    return 0 if status == c['terminal']['pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
