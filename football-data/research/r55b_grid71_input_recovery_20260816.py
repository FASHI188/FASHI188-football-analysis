#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import html
import io
import json
import math
import re
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'football-data' / 'research'
PARTS = [R / f'r55_candidate_identity_part{i}.csv' for i in range(1, 5)]
OUT = R / 'r55b_grid71_input_recovery_20260816.json'

ELIGIBLE = {
    'England: Premier League',
    'England: Championship',
    'England: League One',
    'England: League Two',
}
SOURCES = {
    'England: Premier League': ('Premier.csv', '6d94d4ae4e3a995261b5c3aa92e48ddf05d46e74'),
    'England: Championship': ('Championship.csv', '57cf8071f86b45870018bf90554f612c66b281d1'),
    'England: League One': ('League 1.csv', 'c7f3399aa0f6b7ca8c8b10844fc7bbba78de9ab2'),
    'England: League Two': ('League 2.csv', '3aa62ec193d7e590004c34ff08e91c2b57122c76'),
}
CONTAMINATED = {'2142033','2157450','2157451','2148155','2157452','879658','879659','879663','879664','879665'}
ALIASES = {
    'newcastleutd':'newcastle','sheffieldutd':'sheffieldunited','cambridgeutd':'cambridge','oxfordutd':'oxford',
    'gillinghamfc':'gillingham','nottingham':'nottmforest','nottinghamforest':'nottmforest',
    'miltonkeynesdons':'mkdons','dagenhamred':'dagenhamredbridge','dagenhamredbridge':'dagenhamredbridge',
    'hullcity':'hull','stokecity':'stoke','swansea':'swansea','crawleytown':'crawleytown',
}
TARGET_N = 300
TARGET_SHA = '7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb'
CAL_CUTOFF = '2016-03-01 00:00:00'
MIN_BOOKMAKERS = 8
CAL_MATCHES_MEMBER = 'odds_series_matches.csv.gz'
CAL_ODDS_MEMBER = 'odds_series.csv.gz'
TARGET_ODDS_MEMBER = 'odds_series_b.csv.gz'
PARTITION_RESOLUTION = 'football-data/research/r55b_archive_partition_resolution_20260816.json'


def norm(s: str) -> str:
    s = html.unescape(s or '').lower().replace('&', ' and ')
    s = re.sub(r'\bfc\b', '', s)
    s = re.sub(r'[^a-z0-9]+', '', s)
    return ALIASES.get(s, s)


def fodd(v):
    try:
        x = float(v)
        return x if math.isfinite(x) and x > 1.0 else None
    except Exception:
        return None


def parse_fd_date(s: str) -> str:
    for fmt in ('%d/%m/%y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except Exception:
            pass
    raise ValueError(s)


def parse_score(score: str) -> tuple[int, int]:
    m = re.fullmatch(r'\s*(\d+)\s*:\s*(\d+)\s*', score or '')
    if not m:
        raise ValueError(score)
    return int(m.group(1)), int(m.group(2))


def fetch_blob(sha: str) -> str:
    url = f'https://api.github.com/repos/jokecamp/FootballData/git/blobs/{sha}'
    req = urllib.request.Request(url, headers={'Accept':'application/vnd.github+json','User-Agent':'r55b-grid71-recovery/2.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        obj = json.load(resp)
    raw = base64.b64decode(obj['content'])
    actual = hashlib.sha1((f'blob {len(raw)}\0').encode() + raw).hexdigest()
    if actual != sha:
        raise SystemExit(f'FOOTBALLDATA_BLOB_HASH_FAIL: expected={sha} actual={actual}')
    return raw.decode('latin1')


def load_ou():
    ou = {}
    meta = {}
    for league, (name, sha) in SOURCES.items():
        text = fetch_blob(sha)
        reader = csv.DictReader(io.StringIO(text))
        required = {'Date','HomeTeam','AwayTeam','BbAv>2.5','BbAv<2.5'}
        if not required.issubset(set(reader.fieldnames or [])):
            raise SystemExit(f'OU_HEADER_FAIL {league}: {sorted(required-set(reader.fieldnames or []))}')
        kept = 0
        for row in reader:
            over = fodd(row.get('BbAv>2.5'))
            under = fodd(row.get('BbAv<2.5'))
            if over is None or under is None:
                continue
            d = parse_fd_date(row['Date'])
            key = (league, d, norm(row['HomeTeam']), norm(row['AwayTeam']))
            if key in ou:
                raise SystemExit(f'OU_DUPLICATE_IDENTITY: {key}')
            inv_o, inv_u = 1.0/over, 1.0/under
            s = inv_o + inv_u
            ou[key] = {
                'q_over_2_5': inv_o/s,
                'q_under_2_5': inv_u/s,
                'source_blob_sha': sha,
            }
            kept += 1
        meta[league] = {'filename':name, 'blob_sha':sha, 'usable_ou_rows':kept}
    return ou, meta


def load_frozen_target(ou):
    cands = []
    for p in PARTS:
        with p.open(encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                if row['match_id'] in CONTAMINATED:
                    raise SystemExit(f'CONTAMINATED_TARGET_ID_PRESENT: {row["match_id"]}')
                cands.append(row)
    if len(cands) != 400:
        raise SystemExit(f'TARGET_CANDIDATE_COUNT_FAIL: {len(cands)}')
    matched = []
    for row in cands:
        d = row['match_datetime'][:10]
        key = (row['league'], d, norm(row['home_team']), norm(row['away_team']))
        hit = ou.get(key)
        if hit:
            matched.append({**row, **hit})
    matched.sort(key=lambda r: (r['match_datetime'], int(r['match_id'])))
    if len(matched) < TARGET_N:
        raise SystemExit(f'TARGET_MATCH_GATE_FAIL: strict_matched={len(matched)}')
    frozen = matched[:TARGET_N]
    ids = '\n'.join(r['match_id'] for r in frozen).encode()
    sample_sha = hashlib.sha256(ids).hexdigest()
    if sample_sha != TARGET_SHA:
        raise SystemExit(f'TARGET_HASH_FAIL: expected={TARGET_SHA} actual={sample_sha}')
    return frozen, len(matched)


def member(zf: zipfile.ZipFile, basename: str) -> str:
    hits = [n for n in zf.namelist() if Path(n).name == basename]
    if len(hits) != 1:
        raise SystemExit(f'ARCHIVE_MEMBER_GATE_FAIL {basename}: {hits}')
    return hits[0]


def grid71_header(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw, gzip.GzipFile(fileobj=raw) as gz, io.TextIOWrapper(gz, encoding='utf-8', newline='') as text:
        header = [x.strip() for x in next(csv.reader(text))]
    required = {'match_id'} | {f'{o}_b{b}_71' for b in range(1,33) for o in ('home','draw','away')}
    missing = required - set(header)
    if missing:
        raise SystemExit(f'GRID71_HEADER_FAIL {name}: missing={sorted(missing)[:20]} count={len(missing)}')
    return header


def grid71_prob_from_row(row, idx):
    hs, ds, aas = [], [], []
    for b in range(1, 33):
        h = fodd(row[idx[f'home_b{b}_71']])
        d = fodd(row[idx[f'draw_b{b}_71']])
        a = fodd(row[idx[f'away_b{b}_71']])
        if h is None or d is None or a is None:
            continue
        hs.append(1.0/h)
        ds.append(1.0/d)
        aas.append(1.0/a)
    n = len(hs)
    if n < MIN_BOOKMAKERS:
        return None
    mh, md, ma = sum(hs)/n, sum(ds)/n, sum(aas)/n
    s = mh + md + ma
    return {'qH':mh/s, 'qD':md/s, 'qA':ma/s, 'complete_grid71_bookmakers':n}


def load_grid_for_ids(zf: zipfile.ZipFile, name: str, wanted: set[str]):
    grid = {}
    with zf.open(name) as raw, gzip.GzipFile(fileobj=raw) as gz, io.TextIOWrapper(gz, encoding='utf-8', newline='') as text:
        reader = csv.reader(text)
        header = [x.strip() for x in next(reader)]
        idx = {x:i for i,x in enumerate(header)}
        required = {'match_id'} | {f'{o}_b{b}_71' for b in range(1,33) for o in ('home','draw','away')}
        missing = required - set(idx)
        if missing:
            raise SystemExit(f'GRID71_HEADER_FAIL {name}: missing={len(missing)}')
        for row in reader:
            mid = row[idx['match_id']].strip()
            if mid not in wanted:
                continue
            if mid in grid:
                raise SystemExit(f'GRID71_DUPLICATE_MATCH_ID {name}: {mid}')
            q = grid71_prob_from_row(row, idx)
            if q is not None:
                grid[mid] = q
    return grid


def load_calibration_candidates(zf: zipfile.ZipFile, ou):
    candidates = {}
    score_parse_fail = []
    name = member(zf, CAL_MATCHES_MEMBER)
    with zf.open(name) as raw, gzip.GzipFile(fileobj=raw) as gz, io.TextIOWrapper(gz, encoding='latin1', newline='') as text:
        reader = csv.DictReader(text)
        reader.fieldnames = [x.strip() for x in (reader.fieldnames or [])]
        required = {'match_id','league','home_team','away_team','score','match_datetime'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f'CAL_MATCHES_HEADER_FAIL: {sorted(missing)}')
        for original in reader:
            row = {k:(v.strip() if isinstance(v,str) else v) for k,v in original.items()}
            league = row['league']
            dt = row['match_datetime']
            if league not in ELIGIBLE or not dt or not (dt < CAL_CUTOFF):
                continue
            d = dt[:10]
            hit = ou.get((league, d, norm(row['home_team']), norm(row['away_team'])))
            if not hit:
                continue
            mid = row['match_id']
            if mid in candidates:
                raise SystemExit(f'CAL_DUPLICATE_MATCH_ID: {mid}')
            try:
                hs, aas = parse_score(row['score'])
            except Exception:
                score_parse_fail.append({'match_id':mid,'score':row['score']})
                continue
            candidates[mid] = {
                'match_id':mid,
                'league':league,
                'match_datetime':dt,
                'match_date':d,
                'home_team':row['home_team'],
                'away_team':row['away_team'],
                'home_score':hs,
                'away_score':aas,
                'total_goals':hs+aas,
                'actual_t_ge_3':int(hs+aas >= 3),
                **hit,
            }
    if score_parse_fail:
        raise SystemExit(f'CAL_SCORE_PARSE_FAIL count={len(score_parse_fail)} preview={score_parse_fail[:10]}')
    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--archive', required=True)
    args = ap.parse_args()

    ou, ou_meta = load_ou()
    frozen_target, target_strict_matched = load_frozen_target(ou)
    target_by_id = {r['match_id']:r for r in frozen_target}

    with zipfile.ZipFile(args.archive) as zf:
        cal_odds_name = member(zf, CAL_ODDS_MEMBER)
        target_odds_name = member(zf, TARGET_ODDS_MEMBER)
        cal_header = grid71_header(zf, cal_odds_name)
        target_header = grid71_header(zf, target_odds_name)
        cal_grid71_cols = {x for x in cal_header if re.fullmatch(r'(home|draw|away)_b\d+_71', x)}
        target_grid71_cols = {x for x in target_header if re.fullmatch(r'(home|draw|away)_b\d+_71', x)}
        if cal_grid71_cols != target_grid71_cols or len(cal_grid71_cols) != 96:
            raise SystemExit('ARCHIVE_PARTITION_SCHEMA_GATE_FAIL')

        calibration_candidates = load_calibration_candidates(zf, ou)
        overlap = set(calibration_candidates) & set(target_by_id)
        if overlap:
            raise SystemExit(f'CAL_TARGET_MATCH_ID_OVERLAP: {sorted(overlap)[:20]}')

        cal_grid = load_grid_for_ids(zf, cal_odds_name, set(calibration_candidates))
        target_grid = load_grid_for_ids(zf, target_odds_name, set(target_by_id))

    calibration = []
    calibration_grid_missing = []
    for mid, row in calibration_candidates.items():
        q = cal_grid.get(mid)
        if q is None:
            calibration_grid_missing.append(mid)
            continue
        calibration.append({**row, **q})
    calibration.sort(key=lambda r: (r['match_datetime'], int(r['match_id'])))

    target = []
    target_grid_missing = []
    for row in frozen_target:
        mid = row['match_id']
        q = target_grid.get(mid)
        if q is None:
            target_grid_missing.append(mid)
            continue
        target.append({
            'match_id':mid,
            'league':row['league'],
            'match_datetime':row['match_datetime'],
            'home_team':row['home_team'],
            'away_team':row['away_team'],
            'q_over_2_5':row['q_over_2_5'],
            'q_under_2_5':row['q_under_2_5'],
            'source_blob_sha':row['source_blob_sha'],
            **q,
        })

    cal_counts = Counter(r['league'] for r in calibration)
    target_counts = Counter(r['league'] for r in target)
    target_ids = '\n'.join(r['match_id'] for r in target).encode()
    target_recovered_sha = hashlib.sha256(target_ids).hexdigest() if len(target) == TARGET_N else None

    status = (
        'PASS_COMPLETE_INPUT_RECOVERY'
        if len(calibration) >= 300 and len(target) == TARGET_N and target_recovered_sha == TARGET_SHA
        else 'FAIL_INPUT_RECOVERY_GATE'
    )

    payload = {
        'schema':'R55B_GRID71_INPUT_RECOVERY_R2',
        'classification':'RESEARCH_ONLY_TECHNICAL_RECOVERY_FORMAL_WEIGHT_0',
        'status':status,
        'prereg':'football-data/research/r55b_pretarget_ou_alpha_prereg_20260814.json',
        'archive_partition_resolution':PARTITION_RESOLUTION,
        'source_archive':{
            'release_tag':'football-data-archive-v1',
            'asset_name':'archive.1.1.zip',
            'sha_recheck_performed':False,
            'calibration_matches_member':CAL_MATCHES_MEMBER,
            'calibration_grid71_member':CAL_ODDS_MEMBER,
            'target_grid71_member':TARGET_ODDS_MEMBER,
        },
        'partition_schema_audit':{
            'calibration_member_column_count':len(cal_header),
            'target_member_column_count':len(target_header),
            'grid71_hda_column_count':len(cal_grid71_cols),
            'grid71_column_sets_equal':cal_grid71_cols == target_grid71_cols,
            'scientific_rule_change':False,
        },
        'grid71_rule':{
            'bookmakers':32,
            'grid':71,
            'minimum_complete_bookmakers':MIN_BOOKMAKERS,
            'construction':'for each bookmaker require complete H/D/A odds >1; inverse each price; average inverse prices by outcome; normalize the three outcome means',
        },
        'calibration_rule':{
            'cutoff_exclusive':CAL_CUTOFF,
            'identity':'same calendar date + normalized home + normalized away + league',
            'eligible_leagues':sorted(ELIGIBLE),
            'minimum_n':300,
        },
        'ou_source_meta':ou_meta,
        'audit':{
            'target_candidate_count':400,
            'target_ou_strict_matched_count':target_strict_matched,
            'frozen_target_expected_n':TARGET_N,
            'frozen_target_recovered_n':len(target),
            'frozen_target_expected_sha256':TARGET_SHA,
            'frozen_target_recovered_sha256':target_recovered_sha,
            'target_grid71_missing_count':len(target_grid_missing),
            'target_grid71_missing_ids':target_grid_missing[:50],
            'calibration_identity_ou_matched_before_grid_gate':len(calibration_candidates),
            'calibration_recovered_n':len(calibration),
            'calibration_grid71_missing_count':len(calibration_grid_missing),
            'calibration_grid71_missing_ids':calibration_grid_missing[:50],
            'calibration_by_league':dict(cal_counts),
            'target_by_league':dict(target_counts),
            'calibration_time_window':[calibration[0]['match_datetime'],calibration[-1]['match_datetime']] if calibration else None,
            'target_time_window':[target[0]['match_datetime'],target[-1]['match_datetime']] if target else None,
            'calibration_min_complete_bookmakers':min((r['complete_grid71_bookmakers'] for r in calibration), default=None),
            'target_min_complete_bookmakers':min((r['complete_grid71_bookmakers'] for r in target), default=None),
            'calibration_score_labels_read':len(calibration_candidates),
            'target_score_fields_read_from_archive':0,
            'target_used_to_fit_alpha':False,
            'alpha_fit_performed':False,
        },
        'calibration':calibration,
        'target':target,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print(json.dumps({'status':status, 'partition_schema_audit':payload['partition_schema_audit'], **payload['audit']}, ensure_ascii=False, indent=2, sort_keys=True))
    if status != 'PASS_COMPLETE_INPUT_RECOVERY':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
