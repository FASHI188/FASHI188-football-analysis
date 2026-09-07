#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

DATE_RE = re.compile(r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$')
MATCH_RE = re.compile(r'^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s+(\d+)-(\d+)(?:\s+\([^)]*\))?\s*$')
MATCH_COUNT_RE = re.compile(r'^\s*#\s*Matches\s+(\d+)\s*$')
MONTHS = {m: i for i, m in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'), 1)}
SEASON_CODE = {'2021/22':'2122','2022/23':'2223','2023/24':'2324','2024/25':'2425','2025/26':'2526'}
OUTPUT_CODE = {
    'ENG_PremierLeague': 'E1',
    'ESP_LaLiga': 'SP2',
    'GER_Bundesliga': 'D2',
    'ITA_SerieA': 'I2',
    'FRA_Ligue1': 'F2',
}
EXPECTED_SCHEMA = 'football3-promoted-cold-start-pre-label-source-transition-v1'


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(f'blob {len(data)}\0'.encode('ascii'))
    h.update(data)
    return h.hexdigest()


def parse_football_txt(text: str, source_name: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    current_date: date | None = None
    current_year: int | None = None
    current_month: int | None = None
    declared_matches: int | None = None
    suspicious: list[tuple[int, str]] = []

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip('\n')
        mc = MATCH_COUNT_RE.match(line)
        if mc:
            declared_matches = int(mc.group(1))
            continue

        dm = DATE_RE.match(line)
        if dm:
            month = MONTHS[dm.group(2)]
            day = int(dm.group(3))
            explicit_year = dm.group(4)
            if explicit_year:
                current_year = int(explicit_year)
            elif current_year is None:
                raise RuntimeError(f'{source_name}:{lineno}: date lacks initial year: {line!r}')
            elif current_month is not None and month < current_month:
                current_year += 1
            current_month = month
            current_date = date(current_year, month, day)
            continue

        mm = MATCH_RE.match(line)
        if mm:
            if current_date is None:
                raise RuntimeError(f'{source_name}:{lineno}: match before date header: {line!r}')
            home = mm.group(2).strip()
            away = mm.group(3).strip()
            if not home or not away:
                raise RuntimeError(f'{source_name}:{lineno}: empty team name: {line!r}')
            rows.append({
                'Date': current_date.strftime('%d/%m/%Y'),
                'HomeTeam': home,
                'AwayTeam': away,
                'FTHG': int(mm.group(4)),
                'FTAG': int(mm.group(5)),
            })
            continue

        if ' v ' in line and line.strip() and not line.lstrip().startswith('#'):
            suspicious.append((lineno, line))

    if suspicious:
        sample = suspicious[:5]
        raise RuntimeError(f'{source_name}: unparsed fixture-like lines: {sample}')
    if not rows:
        raise RuntimeError(f'{source_name}: no parsed matches')
    if declared_matches is None:
        raise RuntimeError(f'{source_name}: missing declared match count')
    if len(rows) != declared_matches:
        raise RuntimeError(f'{source_name}: parsed match count {len(rows)} != declared {declared_matches}')
    return rows, declared_matches


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--raw-root', required=True)
    ap.add_argument('--out-root', required=True)
    ap.add_argument('--manifest', required=True)
    args = ap.parse_args()

    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('schema_version') != EXPECTED_SCHEMA:
        raise RuntimeError('unexpected source-transition schema')
    if receipt.get('status') != 'FROZEN_BEFORE_CANDIDATE_EVALUATION':
        raise RuntimeError('source-transition receipt is not frozen')
    if receipt.get('candidate_metric_observations_before_transition') != 0:
        raise RuntimeError('source transition is not pre-metric')
    if receipt.get('target_file_count') != 25:
        raise RuntimeError('expected exactly 25 frozen source files')

    raw_root = Path(args.raw_root)
    out_root = Path(args.out_root)
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[tuple[str, str]] = set()
    files_out: list[dict[str, Any]] = []
    total_rows = 0

    for repo in receipt['repositories']:
        repository = str(repo['repository'])
        commit = str(repo['commit'])
        comp = str(repo['competition_id'])
        if comp not in OUTPUT_CODE:
            raise RuntimeError(f'unexpected competition {comp}')
        for f in repo['files']:
            season = str(f['season'])
            if season not in SEASON_CODE:
                raise RuntimeError(f'unexpected season {season}')
            key = (comp, season)
            if key in seen:
                raise RuntimeError(f'duplicate competition-season {key}')
            seen.add(key)

            source_path = str(f['path'])
            raw_path = raw_root / comp / f'{season.replace("/", "-")}.txt'
            if not raw_path.is_file():
                raise RuntimeError(f'missing frozen raw source {raw_path}')
            raw = raw_path.read_bytes()
            expected_bytes = int(f['bytes'])
            expected_blob = str(f['git_blob_sha'])
            if len(raw) != expected_bytes:
                raise RuntimeError(f'{raw_path}: bytes {len(raw)} != frozen {expected_bytes}')
            actual_blob = git_blob_sha(raw)
            if actual_blob != expected_blob:
                raise RuntimeError(f'{raw_path}: git blob {actual_blob} != frozen {expected_blob}')

            text = raw.decode('utf-8-sig')
            rows, declared_matches = parse_football_txt(text, f'{repository}@{commit}:{source_path}')

            dst = out_root / SEASON_CODE[season] / f'{OUTPUT_CODE[comp]}.csv'
            dst.parent.mkdir(parents=True, exist_ok=True)
            with dst.open('w', encoding='utf-8', newline='') as out:
                w = csv.DictWriter(out, fieldnames=['Date','HomeTeam','AwayTeam','FTHG','FTAG'], lineterminator='\n')
                w.writeheader()
                w.writerows(rows)
            converted = dst.read_bytes()
            total_rows += len(rows)
            files_out.append({
                'competition_id': comp,
                'season': season,
                'lower_tier': repo['lower_tier'],
                'repository': repository,
                'commit': commit,
                'source_path': source_path,
                'source_url': f'https://raw.githubusercontent.com/{repository}/{commit}/{source_path}',
                'git_blob_sha': actual_blob,
                'raw_bytes': len(raw),
                'raw_sha256': sha256_bytes(raw),
                'declared_matches': declared_matches,
                'parsed_matches': len(rows),
                'converted_path': str(dst),
                'converted_bytes': len(converted),
                'converted_sha256': sha256_bytes(converted),
                'converted_fields': ['Date','HomeTeam','AwayTeam','FTHG','FTAG'],
                'license': 'CC0-1.0',
                'xg_fields_present': False,
                'odds_fields_present': False,
            })

    expected = {(c, s) for c in OUTPUT_CODE for s in SEASON_CODE}
    if seen != expected:
        raise RuntimeError(f'frozen source coverage mismatch missing={sorted(expected-seen)} extra={sorted(seen-expected)}')

    payload = {
        'schema_version': 'football3-openfootball-lower-tier-result-source-manifest-v1',
        'receipt_path': str(receipt_path),
        'receipt_sha256': sha256_bytes(receipt_path.read_bytes()),
        'file_count': len(files_out),
        'competition_count': len(OUTPUT_CODE),
        'season_count': len(SEASON_CODE),
        'total_parsed_matches': total_rows,
        'parser_contract': 'format-only date/header/home-v-away/final-score parser; no team aliases, fuzzy identity, odds or xG inference',
        'files': sorted(files_out, key=lambda x: (x['competition_id'], x['season'])),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')
    print(json.dumps({'status':'PASS','files':len(files_out),'total_parsed_matches':total_rows}, sort_keys=True))


if __name__ == '__main__':
    main()
