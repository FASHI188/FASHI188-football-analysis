#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'research/betfair_basic_trajectory_r1/ingest_betfair_basic_trajectory_r1.py'
HELPER_CFG = ROOT / 'research/betfair_basic_trajectory_r1/preregistration.json'
SOURCE_ROOT = Path('data/dados_historicos/por_evento_id')
SOURCE_COMMIT = '90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff'
PT_RE = re.compile(r'"pt"\s*:\s*(\d+)')

CUTOFFS_MINUTES = (180, 120, 90, 60, 45, 30, 15, 10, 5)
STALE_SECONDS = (300, 600, 900, 1800, 3600, 7200, 14400)
SPAN_SECONDS = (120, 300, 600, 1800, 3600, 7200)
QUOTE_FIELDS = ('atb', 'atl', 'batb', 'batl')


class DiagnosticError(RuntimeError):
    pass


@dataclass(frozen=True)
class Obs:
    t: datetime
    p: float


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise DiagnosticError(f'JSON object required: {path}')
    return value


def dump(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    if load(path) != value:
        raise DiagnosticError('persist/reload mismatch')


def epoch(value: int | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def dtiso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise DiagnosticError('timezone missing')
    return parsed.astimezone(timezone.utc)


def helper() -> Any:
    spec = importlib.util.spec_from_file_location('r3a_helper', HELPER)
    if spec is None or spec.loader is None:
        raise DiagnosticError('helper unavailable')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_files(checkout: Path) -> list[Path]:
    root = checkout / SOURCE_ROOT
    if not root.is_dir():
        raise DiagnosticError('source root missing')
    required = (b'"marketType":"MATCH_ODDS"', b'"eventTypeId":"1"', b'"The Draw"')
    result: list[Path] = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        with path.open('rb') as handle:
            prefix = handle.read(2_000_000)
        if all(token in prefix for token in required):
            result.append(path)
    return result


def latest(history: list[Obs], target: datetime) -> Obs | None:
    rows = [item for item in history if item.t <= target]
    return max(rows, key=lambda item: item.t) if rows else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    pos = (len(rows) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return rows[lo]
    return rows[lo] * (hi - pos) + rows[hi] * (pos - lo)


def parse_market(path: Path, h: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    market_id = event_id = None
    market_time: datetime | None = None
    mapping: dict[str, Any] | None = None
    histories: dict[int, list[Obs]] = defaultdict(list)
    quote_fields_seen = Counter()
    previous_pt: int | None = None
    inplay = False
    first_pre_kickoff_pt: datetime | None = None
    last_pre_kickoff_pt: datetime | None = None

    with path.open('r', encoding='utf-8-sig') as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            match = PT_RE.search(raw)
            if match is None:
                raise DiagnosticError('pt_missing')
            numeric_pt = int(match.group(1))
            timestamp = epoch(numeric_pt)
            if previous_pt is not None and numeric_pt < previous_pt:
                raise DiagnosticError('pt_non_monotonic')
            previous_pt = numeric_pt
            if market_time is not None and timestamp >= market_time:
                break
            message = json.loads(raw)
            for change in message.get('mc') or []:
                if not isinstance(change, dict):
                    continue
                incoming_market_id = change.get('id')
                if incoming_market_id is not None:
                    incoming_market_id = str(incoming_market_id)
                    if market_id is not None and incoming_market_id != market_id:
                        raise DiagnosticError('market_id_changed')
                    market_id = incoming_market_id
                definition = change.get('marketDefinition')
                if isinstance(definition, dict):
                    if str(definition.get('eventTypeId')) != '1' or definition.get('marketType') != 'MATCH_ODDS':
                        raise DiagnosticError('not_football_match_odds')
                    incoming_time = dtiso(str(definition.get('marketTime')))
                    incoming_event = str(definition.get('eventId') or '')
                    if not incoming_event or timestamp >= incoming_time:
                        raise DiagnosticError('bad_identity_or_time')
                    if market_time is not None and incoming_time != market_time:
                        raise DiagnosticError('market_time_changed')
                    if event_id is not None and incoming_event != event_id:
                        raise DiagnosticError('event_id_changed')
                    market_time = incoming_time
                    event_id = incoming_event
                    if definition.get('inPlay') is True:
                        inplay = True
                        break
                    if len(definition.get('runners') or []) != 3:
                        raise DiagnosticError('runner_count_not_three')
                    mapping = h.runner_map(definition, cfg)
                    ids = [int(mapping['home_id']), int(mapping['draw_id']), int(mapping['away_id'])]
                    if len(set(ids)) != 3:
                        raise DiagnosticError('runner_map_collision')
                for runner_change in change.get('rc') or []:
                    if not isinstance(runner_change, dict) or runner_change.get('id') is None:
                        continue
                    runner_id = int(runner_change['id'])
                    for field in QUOTE_FIELDS:
                        if field in runner_change:
                            quote_fields_seen[field] += 1
                    if 'ltp' not in runner_change:
                        continue
                    try:
                        price = float(runner_change['ltp'])
                    except (TypeError, ValueError) as exc:
                        raise DiagnosticError('ltp_invalid_type') from exc
                    if not math.isfinite(price) or price < 1.01:
                        raise DiagnosticError('ltp_invalid_value')
                    histories[runner_id].append(Obs(timestamp, price))
            if inplay:
                break
            if market_time is not None and timestamp < market_time:
                first_pre_kickoff_pt = first_pre_kickoff_pt or timestamp
                last_pre_kickoff_pt = timestamp

    if inplay:
        raise DiagnosticError('inplay_before_complete_parse')
    if market_id is None or event_id is None or market_time is None or mapping is None:
        raise DiagnosticError('identity_incomplete')
    ids = [int(mapping['home_id']), int(mapping['draw_id']), int(mapping['away_id'])]
    return {
        'market_id': market_id,
        'event_id': event_id,
        'market_time': market_time,
        'runner_ids': ids,
        'histories': histories,
        'quote_fields_seen': dict(quote_fields_seen),
        'first_pre_kickoff_pt': first_pre_kickoff_pt,
        'last_pre_kickoff_pt': last_pre_kickoff_pt,
    }


def snapshot_features(parsed: dict[str, Any], cutoff_minutes: int) -> dict[str, Any]:
    target = parsed['market_time'] - timedelta(minutes=cutoff_minutes)
    selected: list[Obs] = []
    missing = 0
    for runner_id in parsed['runner_ids']:
        observation = latest(parsed['histories'].get(runner_id, []), target)
        if observation is None:
            missing += 1
        else:
            selected.append(observation)
    if missing:
        return {'complete': False, 'missing_runner_count': missing}
    ages = [(target - item.t).total_seconds() for item in selected]
    span = (max(item.t for item in selected) - min(item.t for item in selected)).total_seconds()
    return {'complete': True, 'max_age_seconds': max(ages), 'span_seconds': span}


def r2_first_failure(parsed: dict[str, Any]) -> str:
    for cutoff, stale, span in ((90, 900, 300), (15, 300, 120)):
        features = snapshot_features(parsed, cutoff)
        prefix = f'T{cutoff}'
        if not features['complete']:
            return f'{prefix}_missing_explicit_ltp'
        if features['max_age_seconds'] > stale:
            return f'{prefix}_staleness_exceeded'
        if features['span_seconds'] > span:
            return f'{prefix}_runner_span_exceeded'
    return 'R2_eligible'


def run(checkout: Path, output: Path) -> None:
    h = helper()
    cfg = load(HELPER_CFG)
    files = candidate_files(checkout)
    parse_reasons = Counter()
    r2_reasons = Counter()
    quote_presence = Counter()
    lead_times: list[float] = []
    complete_by_cutoff = Counter()
    coverage_grid: dict[str, int] = Counter()
    valid_markets = 0

    for path in files:
        try:
            parsed = parse_market(path, h, cfg)
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            parse_reasons[reason] += 1
            continue
        valid_markets += 1
        r2_reasons[r2_first_failure(parsed)] += 1
        for field, count in parsed['quote_fields_seen'].items():
            if count > 0:
                quote_presence[field] += 1
        first_pt = parsed['first_pre_kickoff_pt']
        if first_pt is not None:
            lead_times.append((parsed['market_time'] - first_pt).total_seconds())
        for cutoff in CUTOFFS_MINUTES:
            features = snapshot_features(parsed, cutoff)
            if not features['complete']:
                continue
            complete_by_cutoff[str(cutoff)] += 1
            for stale in STALE_SECONDS:
                if features['max_age_seconds'] > stale:
                    continue
                for span in SPAN_SECONDS:
                    if features['span_seconds'] <= span:
                        coverage_grid[f'T{cutoff}|stale={stale}|span={span}'] += 1

    report = {
        'schema_version': 'BETFAIR-DRAW-TRAJECTORY-COVERAGE-DIAGNOSTIC-R3A',
        'status': 'COMPLETE_NO_LABEL_COVERAGE_DIAGNOSTIC',
        'source_commit': SOURCE_COMMIT,
        'candidate_files': len(files),
        'valid_identity_markets': valid_markets,
        'parse_or_identity_failure_count': sum(parse_reasons.values()),
        'parse_or_identity_failure_reasons': dict(sorted(parse_reasons.items())),
        'r2_first_failure_reasons': dict(sorted(r2_reasons.items())),
        'explicit_quote_field_market_presence': dict(sorted(quote_presence.items())),
        'ltp_complete_three_runner_count_by_cutoff_minutes': dict(sorted(complete_by_cutoff.items(), key=lambda item: int(item[0]), reverse=True)),
        'ltp_coverage_grid': dict(sorted(coverage_grid.items())),
        'first_pre_kickoff_message_lead_seconds': {
            'count': len(lead_times),
            'p05': percentile(lead_times, 0.05),
            'p25': percentile(lead_times, 0.25),
            'p50': percentile(lead_times, 0.50),
            'p75': percentile(lead_times, 0.75),
            'p95': percentile(lead_times, 0.95),
        },
        'winner_labels_read': 0,
        'post_kickoff_messages_parsed': 0,
        'raw_names_prices_or_stream_messages_persisted': False,
        'per_market_rows_persisted': False,
        'model_fits': 0,
        'thresholds_selected': 0,
        'formal_weight': 0,
        'formal_model_changes': 0,
        'formal_data_changes': 0,
        'formal_config_changes': 0,
        'CURRENT_changes': 0,
    }
    dump(output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-checkout', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source_checkout, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
