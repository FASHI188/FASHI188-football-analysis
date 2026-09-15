#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import tempfile
from collections import Counter
from typing import Any

import nova_n1_2024_feature_source_freeze_v1 as freeze

SAFE_VALUE_COLUMNS = (
    "date", "league", "season", "h_deep", "a_deep", "h_ppda", "a_ppda",
)
FORBIDDEN_VALUE_COLUMNS = {
    "h_goals", "a_goals", "h_xg", "a_xg", "result", "isresult", "winner",
}


def _table_schema_inventory(con: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for (table,) in con.execute("select name from sqlite_master where type='table' order by name"):
        cols = freeze.columns(con, str(table))
        names = sorted(cols.values(), key=str.casefold)
        out.append({
            "table": str(table),
            "columns": names,
            "has_locked_match_feature_schema": {x.casefold() for x in freeze.QUERY_COLUMNS} <= set(cols),
            "has_team_history_candidate_schema": {"date", "league", "season", "deep", "ppda"} <= set(cols),
        })
    return out


def build_inventory(db: pathlib.Path) -> dict[str, Any]:
    if {x.casefold() for x in SAFE_VALUE_COLUMNS} & {x.casefold() for x in FORBIDDEN_VALUE_COLUMNS}:
        raise freeze.FeatureFreezeError("inventory safe columns intersect forbidden label/result columns")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        table, cols = freeze.find_game_table(con)
        actual = [cols[x.casefold()] for x in SAFE_VALUE_COLUMNS]
        sql = (
            "select " + ",".join(freeze.q(x) for x in actual)
            + " from " + freeze.q(table)
            + f" where {freeze.q(cols['league'])} in ({','.join('?' for _ in freeze.EXPECTED_LEAGUE_COUNTS)})"
            + f" order by {freeze.q(cols['date'])} asc"
        )
        rows = list(con.execute(sql, list(freeze.EXPECTED_LEAGUE_COUNTS)))
        schemas = _table_schema_inventory(con)
    finally:
        con.close()

    by_league = Counter()
    by_season = Counter()
    by_league_season = Counter()
    target_by_league = Counter()
    target_complete_by_league = Counter()
    date_min: dict[str, str] = {}
    date_max: dict[str, str] = {}
    target_season_keys = set()

    for raw in rows:
        x = dict(zip(SAFE_VALUE_COLUMNS, raw))
        league = str(x["league"])
        season = str(x["season"])
        date = str(x["date"])[:10]
        by_league[league] += 1
        by_season[season] += 1
        by_league_season[(league, season)] += 1
        date_min[league] = min(date_min.get(league, date), date)
        date_max[league] = max(date_max.get(league, date), date)

        if freeze.TARGET_START_DATE <= date < freeze.TARGET_END_EXCLUSIVE:
            target_by_league[league] += 1
            target_season_keys.add(season)
            vals = (x["h_deep"], x["a_deep"], x["h_ppda"], x["a_ppda"])
            try:
                d1, d2, p1, p2 = map(float, vals)
                complete = d1 >= 0 and d2 >= 0 and p1 > 0 and p2 > 0
            except (TypeError, ValueError):
                complete = False
            if complete:
                target_complete_by_league[league] += 1

    return {
        "schema_version": "football3-nova-n1-2024-feature-inventory-v1",
        "status": "FEATURE_SOURCE_INVENTORY_ONLY",
        "query_value_columns": list(SAFE_VALUE_COLUMNS),
        "forbidden_value_columns_read": [],
        "labels_read": 0,
        "score_or_result_columns_read": False,
        "xg_columns_read": False,
        "raw_database_persisted": False,
        "target_date_window": [freeze.TARGET_START_DATE, freeze.TARGET_END_EXCLUSIVE],
        "all_big5_rows_n": sum(by_league.values()),
        "all_big5_by_league": dict(sorted(by_league.items())),
        "all_big5_by_season": dict(sorted(by_season.items())),
        "all_big5_by_league_season": {f"{league}|{season}": n for (league, season), n in sorted(by_league_season.items())},
        "date_min_by_league": dict(sorted(date_min.items())),
        "date_max_by_league": dict(sorted(date_max.items())),
        "target_window_n": sum(target_by_league.values()),
        "target_window_by_league": dict(sorted(target_by_league.items())),
        "target_window_complete_deep_ppda_n": sum(target_complete_by_league.values()),
        "target_window_complete_deep_ppda_by_league": dict(sorted(target_complete_by_league.items())),
        "target_window_source_season_keys": sorted(target_season_keys),
        "table_schema_inventory": schemas,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    raw, fetch_audit = freeze.download_public_dataset()
    with tempfile.TemporaryDirectory(prefix="football3_nova_n1_inventory_") as td:
        db, db_meta = freeze.materialize_database(raw, pathlib.Path(td))
        inv = build_inventory(db)

    inv.update({
        "provider": freeze.PROVIDER,
        "source_page": freeze.KAGGLE_PAGE,
        "download_url": freeze.KAGGLE_DOWNLOAD,
        "license": freeze.LICENSE,
        "archive_sha256": freeze.sha_bytes(raw),
        "archive_bytes": len(raw),
        "fetch_audit": fetch_audit,
        **db_meta,
        "raw_archive_persisted": False,
        "raw_database_persisted": False,
        "test_result_vault_opened": False,
        "test_labels_read": False,
        "inventory_at": freeze.now(),
    })
    p = args.out / "feature_source_inventory.json"
    p.write_text(json.dumps(inv, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": inv["status"],
        "archive_sha256": inv["archive_sha256"],
        "target_window_n": inv["target_window_n"],
        "target_window_complete_deep_ppda_n": inv["target_window_complete_deep_ppda_n"],
        "target_window_by_league": inv["target_window_by_league"],
        "target_window_source_season_keys": inv["target_window_source_season_keys"],
        "labels_read": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
