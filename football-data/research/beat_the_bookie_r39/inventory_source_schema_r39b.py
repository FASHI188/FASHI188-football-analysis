#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def norm(value: str) -> str:
    return value.replace('\ufeff', '').strip()


def csv_header(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8-sig', errors='replace', newline='') as f:
        sample = f.read(65536)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        try:
            header = [norm(x) for x in next(reader)]
        except StopIteration:
            header = []
    return {'header': header, 'data_rows_read': 0}


def sqlite_schema(path: Path) -> dict[str, Any]:
    uri = f"file:{path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        result = []
        for table in tables:
            safe = table.replace("'", "''")
            columns = [
                {'cid': r[0], 'name': r[1], 'type': r[2], 'notnull': r[3], 'pk': r[5]}
                for r in con.execute(f"PRAGMA table_info('{safe}')").fetchall()
            ]
            result.append({'table': table, 'columns': columns})
        return {'tables': result, 'data_rows_read': 0}
    finally:
        con.close()


def looks_sqlite(path: Path) -> bool:
    try:
        with path.open('rb') as f:
            return f.read(16) == b'SQLite format 3\x00'
    except OSError:
        return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--registration', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--out-dir', type=Path, required=True)
    args = p.parse_args()

    reg = json.loads(args.registration.read_text(encoding='utf-8'))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(x for x in args.source_dir.rglob('*') if x.is_file())
    inventory = []
    csv_count = 0
    sqlite_count = 0
    for path in files:
        rel = path.relative_to(args.source_dir).as_posix()
        item: dict[str, Any] = {
            'path': rel,
            'bytes': path.stat().st_size,
            'suffix': path.suffix.lower(),
            'sha256': sha256_file(path),
        }
        if path.suffix.lower() in {'.csv', '.tsv', '.txt'}:
            try:
                item['csv_schema'] = csv_header(path)
                csv_count += 1
            except Exception as exc:
                item['csv_schema_error'] = type(exc).__name__
        elif path.suffix.lower() in {'.sqlite', '.sqlite3', '.db'} or looks_sqlite(path):
            try:
                item['sqlite_schema'] = sqlite_schema(path)
                sqlite_count += 1
            except Exception as exc:
                item['sqlite_schema_error'] = type(exc).__name__
        inventory.append(item)

    text = json.dumps(inventory, ensure_ascii=False).casefold()
    time_tokens = ['time', 'timestamp', 'datetime', 'date', 'hour', 'minute', 'kickoff', 'start']
    odds_tokens = ['odd', 'home', 'draw', 'away', 'bookmaker', 'provider', '1x2']
    match_tokens = ['match', 'game', 'fixture', 'event', 'id']
    result_tokens = ['score', 'result', 'goal', 'winner']
    token_audit = {
        'time_tokens_present': [t for t in time_tokens if t in text],
        'odds_tokens_present': [t for t in odds_tokens if t in text],
        'match_tokens_present': [t for t in match_tokens if t in text],
        'result_tokens_present_in_schema_names_only': [t for t in result_tokens if t in text],
    }

    has_time = bool(token_audit['time_tokens_present'])
    has_odds = bool(token_audit['odds_tokens_present'])
    has_match = bool(token_audit['match_tokens_present'])
    status = (
        'PASS_R39B_SOURCE_SCHEMA_IDENTIFIED_NO_ROWS_READ'
        if files and has_time and has_odds and has_match
        else 'STOP_R39B_SOURCE_SCHEMA_NOT_IDENTIFIABLE_NO_ROWS_READ'
    )

    payload = {
        'schema_version': reg['schema_version'],
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'source': reg['source'],
        'inventory_summary': {
            'files': len(files),
            'csv_like_files_schema_read': csv_count,
            'sqlite_files_schema_read': sqlite_count,
            'total_bytes': sum(x['bytes'] for x in inventory),
        },
        'schema_token_audit': token_audit,
        'no_label_audit': {
            'data_rows_read': 0,
            'result_values_read': 0,
            'score_values_read': 0,
            'prediction_metrics_computed': 0,
            'match_identities_locked': 0,
        },
        'hard_limits': reg['hard_limits'],
    }
    (args.out_dir / 'status.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (args.out_dir / 'source_schema_inventory.json').write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    manifest = []
    for path in sorted(args.out_dir.iterdir()):
        if path.is_file() and path.name != 'manifest.json':
            manifest.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': sha256_file(path)})
    (args.out_dir / 'manifest.json').write_text(
        json.dumps({'schema': 'r39b-schema-manifest', 'files': manifest}, indent=2), encoding='utf-8'
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
