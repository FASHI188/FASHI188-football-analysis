from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3

BIG5 = ("Bundesliga", "EPL", "La liga", "Ligue 1", "Serie A")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def one(con: sqlite3.Connection, sql: str, params=()):
    return con.execute(sql, params).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=pathlib.Path, required=True)
    ap.add_argument("--contract", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    contract = json.loads(args.contract.read_text())
    if contract["status"] != "FROZEN_BEFORE_NEW_DIRECTION_DEVELOPMENT":
        raise RuntimeError("contract status drift")

    con = sqlite3.connect(str(args.db))
    qs = ",".join("?" for _ in BIG5)

    total_matches = one(con, "select count(*) from general_game_stats")
    total_events = one(con, "select count(*) from game_events")
    total_lineups = one(con, "select count(*) from lineup_stats")

    big5_state_n = one(
        con,
        f"select count(*) from general_game_stats where league in ({qs}) and season between 2014 and 2023",
        BIG5,
    )
    development_n = one(
        con,
        f"select count(*) from general_game_stats where league in ({qs}) and season between 2018 and 2022",
        BIG5,
    )
    confirmation_n = one(
        con,
        f"select count(*) from general_game_stats where league in ({qs}) and season=2023",
        BIG5,
    )
    partial_2024_n = one(
        con,
        f"select count(*) from general_game_stats where league in ({qs}) and season=2024",
        BIG5,
    )

    field_expr = {
        "xg": "h_xg is not null and a_xg is not null",
        "shots": "h_shot is not null and a_shot is not null and h_shotOnTarget is not null and a_shotOnTarget is not null",
        "process": "h_deep is not null and a_deep is not null and h_ppda is not null and a_ppda is not null",
        "schedule": "date is not null and h_id is not null and a_id is not null",
    }
    coverage = {}
    for name, expr in field_expr.items():
        coverage[name] = {
            "state_2014_2023": one(
                con,
                f"select count(*) from general_game_stats where league in ({qs}) and season between 2014 and 2023 and {expr}",
                BIG5,
            ),
            "development_2018_2022": one(
                con,
                f"select count(*) from general_game_stats where league in ({qs}) and season between 2018 and 2022 and {expr}",
                BIG5,
            ),
            "confirmation_2023": one(
                con,
                f"select count(*) from general_game_stats where league in ({qs}) and season=2023 and {expr}",
                BIG5,
            ),
        }

    # Frozen DB has no indexes on the child match_id columns. Aggregate each child
    # table once, then join to the historical universe; avoid O(matches * child rows)
    # correlated scans. This changes only audit mechanics, never cohort/data roles.
    event_match_n = one(
        con,
        f"""
        select count(*)
        from general_game_stats g
        join (select distinct match_id from game_events) e on e.match_id=g.id
        where g.league in ({qs}) and g.season between 2014 and 2023
        """,
        BIG5,
    )
    lineup_match_n = one(
        con,
        f"""
        select count(*)
        from general_game_stats g
        join (select distinct match_id from lineup_stats) l on l.match_id=g.id
        where g.league in ({qs}) and g.season between 2014 and 2023
        """,
        BIG5,
    )
    lineup_two_team_n = one(
        con,
        f"""
        select count(*)
        from general_game_stats g
        join (
          select match_id
          from lineup_stats
          group by match_id
          having count(distinct team_id)=2
        ) l on l.match_id=g.id
        where g.league in ({qs}) and g.season between 2014 and 2023
        """,
        BIG5,
    )

    seasons = []
    for season, league, n, first_date, last_date in con.execute(
        f"""
        select season,league,count(*),min(date),max(date)
        from general_game_stats
        where league in ({qs}) and season between 2014 and 2024
        group by season,league order by season,league
        """,
        BIG5,
    ):
        seasons.append({
            "season": int(season),
            "league": league,
            "n": int(n),
            "first_date": first_date,
            "last_date": last_date,
        })

    min_date, max_date = con.execute(
        f"select min(date),max(date) from general_game_stats where league in ({qs})",
        BIG5,
    ).fetchone()
    con.close()

    gates = contract["inventory_gates"]
    checks = {
        "state_history_n": big5_state_n >= int(gates["min_big5_state_history_n"]),
        "development_n": development_n >= int(gates["min_development_n"]),
        "historical_confirmation_n": confirmation_n >= int(gates["min_historical_confirmation_n"]),
        "process_state_full": coverage["process"]["state_2014_2023"] == big5_state_n,
        "process_development_full": coverage["process"]["development_2018_2022"] == development_n,
        "process_confirmation_full": coverage["process"]["confirmation_2023"] == confirmation_n,
        "event_match_coverage_full": event_match_n == big5_state_n,
        "lineup_match_coverage_full": lineup_match_n == big5_state_n,
        "lineup_two_team_coverage_full": lineup_two_team_n == big5_state_n,
        "schedule_fields_full": coverage["schedule"]["state_2014_2023"] == big5_state_n,
    }

    if not gates.get("require_full_process_fields", True):
        checks["process_state_full"] = True
        checks["process_development_full"] = True
        checks["process_confirmation_full"] = True
    if not gates.get("require_full_event_match_coverage", True):
        checks["event_match_coverage_full"] = True
    if not gates.get("require_full_lineup_match_coverage", True):
        checks["lineup_match_coverage_full"] = True
        checks["lineup_two_team_coverage_full"] = True

    inventory_pass = all(checks.values())
    download_reasons = [k for k, v in checks.items() if not v]

    feasibility = {
        "strength_gap_and_upset_regime": coverage["xg"]["development_2018_2022"] == development_n and coverage["process"]["development_2018_2022"] == development_n,
        "balanced_match_draw_discrimination": coverage["xg"]["development_2018_2022"] == development_n and coverage["process"]["development_2018_2022"] == development_n,
        "joint_score_low_score_collapse": coverage["shots"]["development_2018_2022"] == development_n and event_match_n == big5_state_n,
        "schedule_rest_congestion": coverage["schedule"]["state_2014_2023"] == big5_state_n,
        "rotation_pressure_from_prior_lineups": lineup_match_n == big5_state_n and lineup_two_team_n == big5_state_n,
        "match_importance_from_pit_table_state": coverage["schedule"]["state_2014_2023"] == big5_state_n,
    }

    result = {
        "schema_version": "football3-historical-expanded-pit-inventory-v1",
        "status": "HISTORICAL_INVENTORY_PASS" if inventory_pass else "HISTORICAL_INVENTORY_DOWNLOAD_REQUIRED",
        "research_only": True,
        "database_sha256": sha256(args.db),
        "label_values_selected": False,
        "prospective_1335_data_touched": False,
        "total_database": {
            "matches": total_matches,
            "shot_events": total_events,
            "lineup_rows": total_lineups,
            "min_big5_date": min_date,
            "max_big5_date": max_date,
        },
        "cohorts": {
            "state_2014_2023_n": big5_state_n,
            "development_2018_2022_n": development_n,
            "locked_historical_confirmation_2023_n": confirmation_n,
            "partial_2024_inventory_only_n": partial_2024_n,
        },
        "field_coverage": coverage,
        "event_match_n_2014_2023": event_match_n,
        "lineup_match_n_2014_2023": lineup_match_n,
        "lineup_two_team_n_2014_2023": lineup_two_team_n,
        "season_league_inventory": seasons,
        "candidate_direction_feasibility": feasibility,
        "checks": checks,
        "inventory_pass": inventory_pass,
        "download_required": not inventory_pass,
        "download_reasons": download_reasons,
        "next_step": "BUILD_PIT_FEATURES_ON_2018_2022_WITH_2023_LABELS_LOCKED" if inventory_pass else "DOWNLOAD_ADDITIONAL_COMPLETED_HISTORY_FOR_FAILED_GATES",
    }
    (args.out / "historical_inventory.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if inventory_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
