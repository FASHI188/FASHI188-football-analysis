#!/usr/bin/env python3
"""Zero-label source-quality audit for the frozen FPLCache PIT availability artifact.

This audit never opens match outcomes and never trains/scores a model. It tests whether the
frozen timestamped snapshots contain usable *pre-match information changes* and detects raw
FPL chance-field lifecycle behavior that should be quarantined before any labelled experiment.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
from pathlib import Path
from typing import Any

ALIASES = {
    "Man United": "Man Utd",
    "Sheffield United": "Sheffield Utd",
    "Tottenham": "Spurs",
}
PAIRS = (
    ("T_MINUS_24H", "T_MINUS_6H"),
    ("T_MINUS_6H", "T_MINUS_90M"),
    ("T_MINUS_24H", "T_MINUS_90M"),
)


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    root = args.artifact_dir
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    fixtures = read_jsonl_gz(root / "fixture_cutoff_map.jsonl.gz")

    path_teams: dict[str, set[str]] = collections.defaultdict(set)
    for row in fixtures:
        for label in ("T_MINUS_24H", "T_MINUS_6H", "T_MINUS_90M"):
            snap = row["cutoffs"][label]["snapshot"]
            if snap is None:
                continue
            path = str(snap["path"])
            path_teams[path].add(ALIASES.get(row["home_team"], row["home_team"]))
            path_teams[path].add(ALIASES.get(row["away_team"], row["away_team"]))

    # Store only teams required by fixture/cutoff mapping; this is much smaller than the full raw archive.
    states: dict[tuple[str, str], dict[int, tuple[Any, ...]]] = {}
    with gzip.open(root / "availability_snapshots.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            rec = json.loads(line)
            path = str(rec["source"]["path"])
            wanted = path_teams.get(path)
            if not wanted:
                continue
            team_names = {team["id"]: team["name"] for team in rec.get("teams", [])}
            grouped: dict[str, dict[int, tuple[Any, ...]]] = collections.defaultdict(dict)
            for player in rec.get("players", []):
                team = team_names.get(player.get("team"))
                if team not in wanted:
                    continue
                pid = int(player.get("id") or 0)
                grouped[team][pid] = (
                    player.get("status"),                         # 0
                    player.get("chance_of_playing_this_round"),   # 1
                    player.get("chance_of_playing_next_round"),   # 2
                    str(player.get("news") or "").strip(),       # 3
                    player.get("news_added"),                     # 4 provenance only
                    number(player.get("minutes")),                # 5 strictly snapshot-observed importance proxy
                    number(player.get("starts")),                 # 6
                    number(player.get("selected_by_percent")),    # 7
                    number(player.get("now_cost")),               # 8
                    str(player.get("web_name") or ""),            # 9
                )
            for team, players in grouped.items():
                states[(path, team)] = players

    unresolved: list[dict[str, Any]] = []
    for row in fixtures:
        for label in ("T_MINUS_24H", "T_MINUS_6H", "T_MINUS_90M"):
            path = row["cutoffs"][label]["snapshot"]["path"]
            for side in ("home", "away"):
                raw = row[f"{side}_team"]
                team = ALIASES.get(raw, raw)
                if (path, team) not in states:
                    unresolved.append({"season": row["season"], "date": row["date"], "team": raw, "label": label, "path": path})

    pair_reports: dict[str, Any] = {}
    for left, right in PAIRS:
        counts: collections.Counter[str] = collections.Counter()
        by_season: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        for row in fixtures:
            fixture_stable = False
            fixture_stable_270 = False
            fixture_status = False
            for side in ("home", "away"):
                team = ALIASES.get(row[f"{side}_team"], row[f"{side}_team"])
                p1 = row["cutoffs"][left]["snapshot"]["path"]
                p2 = row["cutoffs"][right]["snapshot"]["path"]
                d1 = states[(p1, team)]
                d2 = states[(p2, team)]
                stable_events = []
                status_events = []
                chance_events = []
                chance_without_stable = []
                for pid in set(d1) & set(d2):
                    x, y = d1[pid], d2[pid]
                    stable_changed = (x[0], x[3]) != (y[0], y[3])
                    status_changed = x[0] != y[0]
                    chance_changed = (x[1], x[2]) != (y[1], y[2])
                    if stable_changed:
                        stable_events.append((pid, x, y))
                    if status_changed:
                        status_events.append((pid, x, y))
                    if chance_changed:
                        chance_events.append((pid, x, y))
                        if not stable_changed:
                            chance_without_stable.append((pid, x, y))

                if stable_events:
                    counts["team_sides_status_or_news_change"] += 1
                    by_season[row["season"]]["team_sides_status_or_news_change"] += 1
                    fixture_stable = True
                established = [event for event in stable_events if max(event[1][5], event[2][5]) >= 270.0]
                if established:
                    counts["team_sides_status_or_news_change_minutes_ge270"] += 1
                    by_season[row["season"]]["team_sides_status_or_news_change_minutes_ge270"] += 1
                    fixture_stable_270 = True
                if status_events:
                    counts["team_sides_status_change"] += 1
                    by_season[row["season"]]["team_sides_status_change"] += 1
                    fixture_status = True

                counts["player_status_or_news_change_events"] += len(stable_events)
                counts["player_status_or_news_change_events_minutes_ge270"] += len(established)
                counts["player_status_change_events"] += len(status_events)
                counts["player_chance_change_events"] += len(chance_events)
                counts["player_chance_change_without_status_or_news_change_events"] += len(chance_without_stable)

            counts["fixture_count"] += 1
            by_season[row["season"]]["fixture_count"] += 1
            if fixture_stable:
                counts["fixtures_status_or_news_change"] += 1
                by_season[row["season"]]["fixtures_status_or_news_change"] += 1
            if fixture_stable_270:
                counts["fixtures_status_or_news_change_minutes_ge270"] += 1
                by_season[row["season"]]["fixtures_status_or_news_change_minutes_ge270"] += 1
            if fixture_status:
                counts["fixtures_status_change"] += 1
                by_season[row["season"]]["fixtures_status_change"] += 1

        chance_total = int(counts["player_chance_change_events"])
        chance_noise = int(counts["player_chance_change_without_status_or_news_change_events"])
        pair_reports[f"{left}->{right}"] = {
            **dict(counts),
            "chance_change_without_status_or_news_fraction": (chance_noise / chance_total) if chance_total else 0.0,
            "by_season": {season: dict(values) for season, values in sorted(by_season.items())},
        }

    coverage = manifest["coverage"]
    coverage_complete = (
        int(coverage.get("mapped_cutoff_count", -1)) == len(fixtures) * 3
        and int(coverage.get("missing_cutoff_count", -1)) == 0
        and int(coverage.get("stale_cutoff_count", -1)) == 0
    )
    team_resolution_complete = len(unresolved) == 0
    stable_dynamic = pair_reports["T_MINUS_24H->T_MINUS_6H"]["player_status_or_news_change_events"] > 0
    late_chance_noise = pair_reports["T_MINUS_6H->T_MINUS_90M"]["chance_change_without_status_or_news_fraction"]
    quarantine_raw_chance = late_chance_noise >= 0.80

    if coverage_complete and team_resolution_complete and stable_dynamic:
        decision = "PASS_TIMESTAMPED_STATUS_NEWS_SOURCE"
    else:
        decision = "STOP_SOURCE_QUALITY_GATE"

    report = {
        "schema_version": "football3-fplcache-pit-availability-zero-label-audit-v1",
        "classification": "ZERO_LABEL_SOURCE_QUALITY_ONLY_NO_SCORING_NO_TRAINING",
        "input": {
            "source_repository": manifest["source_repository"],
            "source_head_sha": manifest["source_head_sha"],
            "fixture_count": len(fixtures),
            "selected_unique_snapshot_count": manifest["source_inventory"]["selected_unique_snapshot_count"],
            "mapped_cutoff_count": coverage["mapped_cutoff_count"],
            "missing_cutoff_count": coverage["missing_cutoff_count"],
            "stale_cutoff_count": coverage["stale_cutoff_count"],
            "global_median_staleness_minutes": coverage["global_median_staleness_minutes"],
            "global_max_staleness_minutes": coverage["global_max_staleness_minutes"],
        },
        "checks": {
            "coverage_complete": coverage_complete,
            "fixture_team_resolution_complete": team_resolution_complete,
            "status_or_news_is_dynamic_between_t24h_and_t6h": stable_dynamic,
            "raw_chance_fields_show_late_window_lifecycle_instability": quarantine_raw_chance,
        },
        "unresolved_fixture_team_count": len(unresolved),
        "unresolved_examples": unresolved[:20],
        "pair_reports": pair_reports,
        "field_adjudication": {
            "status": "ALLOW_PIT_FEATURE_CANDIDATE",
            "news": "ALLOW_PIT_FEATURE_CANDIDATE_AS_STRUCTURED_STATE_CHANGE_AFTER_TEXT_NORMALIZATION",
            "news_added": "PROVENANCE_ONLY_DO_NOT_USE_AS_NUMERIC_PREDICTOR",
            "chance_of_playing_this_round": "QUARANTINE_UNTIL_FPL_EVENT_ROLLOVER_SEMANTICS_ARE_ANCHORED" if quarantine_raw_chance else "REVIEW",
            "chance_of_playing_next_round": "QUARANTINE_WITH_THIS_ROUND_UNTIL_EVENT_SEMANTICS_ARE_ANCHORED" if quarantine_raw_chance else "REVIEW",
            "minutes_starts_selected_cost": "IMPORTANCE_CONTEXT_ONLY_STRICTLY_AS_OBSERVED_IN_THE_SAME_PREMATCH_SNAPSHOT",
        },
        "recommended_frozen_windows": {
            "primary_change_window": "T_MINUS_24H_TO_T_MINUS_6H",
            "final_state_window": "T_MINUS_90M_LATEST_AVAILABLE_AT_OR_BEFORE_CUTOFF",
            "note": "The source is roughly six-hour cadence; T-90m means latest strictly-before-cutoff snapshot, not necessarily a snapshot exactly 90 minutes before kickoff.",
        },
        "decision": decision,
        "next_authorization_boundary": "Any match-outcome scoring, training, tuning or model-weight experiment requires separate authorization.",
        "governance": {
            "match_outcomes_read": False,
            "labels_scored": False,
            "training_performed": False,
            "tuning_performed": False,
            "formal_weight_change": False,
            "current_change": False,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "decision": decision,
        "fixture_count": len(fixtures),
        "unresolved_fixture_team_count": len(unresolved),
        "t24_to_t6_fixtures_status_or_news_change": pair_reports["T_MINUS_24H->T_MINUS_6H"]["fixtures_status_or_news_change"],
        "t24_to_t6_fixtures_established_change": pair_reports["T_MINUS_24H->T_MINUS_6H"]["fixtures_status_or_news_change_minutes_ge270"],
        "t6_to_t90_chance_noise_fraction": late_chance_noise,
        "raw_chance_fields_quarantined": quarantine_raw_chance,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if decision.startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
