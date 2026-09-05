from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', type=pathlib.Path, required=True)
    ap.add_argument('--db', type=pathlib.Path, required=True)
    ap.add_argument('--schedule-evidence', type=pathlib.Path, required=True)
    ap.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    c = json.loads(a.contract.read_text())
    assert c['status'] == 'FROZEN_ZERO_MODEL_SOURCE_FEASIBILITY_AUDIT'
    assert c['boundaries']['model_fit'] == 0
    assert c['boundaries']['candidate_probability'] == 0
    assert c['boundaries']['outcome_label_access'] == 0
    assert c['strict_pit_rules']['realized_weather_forbidden'] is True
    assert c['strict_pit_rules']['reanalysis_weather_forbidden'] is True
    assert c['strict_pit_rules']['current_venue_assumption_for_historical_fixture_forbidden'] is True

    sched = json.loads(a.schedule_evidence.read_text())
    if sched.get('status') != c['frozen_source_estate']['stage6_pre_c_schedule_status']:
        raise RuntimeError('stage6 pre-C schedule status drift')
    if sched.get('target_n') != int(c['cohort']['expected_target_n']):
        raise RuntimeError('stage6 pre-C target_n drift')
    if sched.get('2023_opened') is not False or sched.get('result_or_goal_fields_read') is not False:
        raise RuntimeError('schedule evidence boundary violation')

    con = sqlite3.connect(f'file:{a.db}?mode=ro', uri=True)
    tables = [str(r[0]) for r in con.execute("select name from sqlite_master where type='table' order by name")]
    schema: dict[str, list[str]] = {}
    for t in tables:
        schema[t] = [str(r[1]) for r in con.execute(f'pragma table_info({qident(t)})')]

    tokens = tuple(str(x).lower() for x in c['schema_inventory']['geography_tokens'])
    geo_columns: list[dict] = []
    for t, cols in schema.items():
        for col in cols:
            low = col.lower()
            matches = sorted({tok for tok in tokens if re.search(r'(^|_)' + re.escape(tok) + r'($|_)', low)})
            if matches:
                geo_columns.append({'table': t, 'column': col, 'tokens': matches})

    league_marks = ','.join('?' for _ in c['cohort']['leagues'])
    season_marks = ','.join('?' for _ in c['cohort']['target_seasons'])
    params = [*c['cohort']['leagues'], *c['cohort']['target_seasons']]
    target_n = int(con.execute(
        f'select count(*) from general_game_stats where league in ({league_marks}) and season in ({season_marks})',
        params,
    ).fetchone()[0])
    per_season = {
        str(s): int(con.execute(
            f'select count(*) from general_game_stats where league in ({league_marks}) and season=?',
            [*c['cohort']['leagues'], s],
        ).fetchone()[0])
        for s in c['cohort']['target_seasons']
    }

    match_cols = set(schema.get('general_game_stats', []))
    venue_name_tokens = ('venue', 'stadium', 'ground')
    venue_cols = [col for col in match_cols if any(tok in col.lower() for tok in venue_name_tokens)]
    lat_cols = [col for col in match_cols if col.lower() in {'lat', 'latitude', 'venue_lat', 'venue_latitude'} or 'latitude' in col.lower()]
    lon_cols = [col for col in match_cols if col.lower() in {'lon', 'lng', 'longitude', 'venue_lon', 'venue_lng', 'venue_longitude'} or 'longitude' in col.lower()]

    nonnull_coverage: dict[str, float] = {}
    for col in sorted(set(venue_cols + lat_cols + lon_cols)):
        n = int(con.execute(
            f'select count(*) from general_game_stats where league in ({league_marks}) and season in ({season_marks}) and {qident(col)} is not null and trim(cast({qident(col)} as text)) != ""',
            params,
        ).fetchone()[0])
        nonnull_coverage[col] = n / target_n if target_n else 0.0
    con.close()

    venue_identity_coverage = max((nonnull_coverage[x] for x in venue_cols), default=0.0)
    coordinate_coverage = 0.0
    if lat_cols and lon_cols:
        coordinate_coverage = min(
            max((nonnull_coverage[x] for x in lat_cols), default=0.0),
            max((nonnull_coverage[x] for x in lon_cols), default=0.0),
        )

    registered_coords = c['frozen_source_estate']['registered_historical_fixture_venue_coordinate_source'] is not None
    registered_weather = c['frozen_source_estate']['registered_fixed_lead_weather_forecast_archive_covering_all_2020_2022'] is not None
    strict_venue_ready = registered_coords and venue_identity_coverage >= float(c['gates']['minimum_match_specific_venue_identity_coverage']) and coordinate_coverage >= float(c['gates']['minimum_coordinate_coverage'])
    strict_weather_ready = registered_weather

    checks = {
        'target_n_exact': target_n == int(c['gates']['target_n_exact']),
        'per_season_n_exact': all(per_season[str(s)] == int(c['gates']['per_season_n_exact']) for s in c['cohort']['target_seasons']),
        'stage6_schedule_evidence_zero_label': sched.get('2023_opened') is False and sched.get('result_or_goal_fields_read') is False,
        'stage6_schedule_no_final_backfill': sched.get('final_schedule_backfill_used') is False,
        'registered_historical_fixture_venue_coordinate_source': registered_coords,
        'match_specific_venue_identity_coverage': venue_identity_coverage >= float(c['gates']['minimum_match_specific_venue_identity_coverage']),
        'coordinate_coverage': coordinate_coverage >= float(c['gates']['minimum_coordinate_coverage']),
        'registered_fixed_lead_weather_forecast_archive_2020_2022': registered_weather,
    }
    source_pass = strict_venue_ready or strict_weather_ready

    out = {
        'schema_version': 'football3-environment-travel-venue-source-audit-result-v1',
        'status': c['terminal']['pass'] if source_pass else c['terminal']['fail'],
        'source_only': True,
        'model_fit': 0,
        'candidate_probability': 0,
        'outcome_label_access': 0,
        'target_n': target_n,
        'per_season': per_season,
        'database_tables': tables,
        'database_geography_columns': geo_columns,
        'general_game_stats_columns': sorted(match_cols),
        'match_level_venue_columns': sorted(venue_cols),
        'match_level_latitude_columns': sorted(lat_cols),
        'match_level_longitude_columns': sorted(lon_cols),
        'match_level_candidate_nonnull_coverage': nonnull_coverage,
        'match_specific_venue_identity_coverage': venue_identity_coverage,
        'coordinate_coverage': coordinate_coverage,
        'registered_historical_fixture_venue_coordinate_source': registered_coords,
        'registered_fixed_lead_weather_forecast_archive_covering_all_2020_2022': registered_weather,
        'stage6_pre_c_schedule': {
            'status': sched.get('status'),
            'pit_complete_n': sched.get('pit_complete_n'),
            'pit_complete_fraction': sched.get('pit_complete_fraction'),
            'missing_reasons': sched.get('missing_reasons'),
            'france_policy': sched.get('france_policy'),
            'final_schedule_backfill_used': sched.get('final_schedule_backfill_used'),
            'final_lineup_or_minutes_used': sched.get('final_lineup_or_minutes_used'),
        },
        'strict_pit_weather_policy': {
            'realized_weather_forbidden': True,
            'reanalysis_weather_forbidden': True,
            'forecast_issued_before_kickoff_required': True,
        },
        'checks': checks,
        'scientific_source_pass': source_pass,
        'historical_confirmation_2023_labels_opened': False,
        'prospective_1335_data_touched': False,
        'formal_weight': 0,
        'next_step': 'FREEZE_ONE_SOURCE_BACKED_ENVIRONMENT_MECHANISM' if source_pass else 'CLOSE_ENVIRONMENT_CURRENT_SOURCE_ESTATE_NO_FIT',
    }
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'environment_source_audit.json').write_text(json.dumps(out, sort_keys=True, indent=2) + '\n')
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
