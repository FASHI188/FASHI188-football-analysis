#!/usr/bin/env python3
"""R44L2 label-free training support gate for the full 380-match EPL season."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from run_zero_label_gate_r44l2 import (
    URLS,
    canon,
    date_from_iso,
    decoded_csv_bytes,
    get_bytes,
    selected_columns,
    sha256,
)

OUT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "r44l2_training_support_output")
EXPECTED_HASHES = {
    "fpl_fixtures": "2d7e3950d346df14ca486cb09e9b9ba406d37d943775244eed06cdc021ffb3a9",
    "fpl_teams": "b29df099cb0ad25413e284e53116099b0e0496874f99743dbc0870d8241b46c5",
    "tm_game_lineups": "6b2fc04ae307390c4d2044659b91c0da314c6a20eefd4a2f13e1468ac06c874b",
    "tm_games": "585f593b2add005ad803fd999355ef70d5de44ef021d37787064fdffcb3ba484",
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blobs = {name: get_bytes(url) for name, url in URLS.items()}
    hashes = {name: sha256(data) for name, data in blobs.items()}
    source_hash_match = hashes == EXPECTED_HASHES

    team_rows = selected_columns(blobs["fpl_teams"], ["id", "name"])
    team_name = {row["id"]: canon(row["name"]) for row in team_rows}

    fixture_rows = selected_columns(blobs["fpl_fixtures"], ["id", "kickoff_time", "team_h", "team_a"])
    fixture_rows = [r for r in fixture_rows if r["id"] and r["kickoff_time"]]
    fixture_rows.sort(key=lambda r: (r["kickoff_time"], int(r["id"])))

    fpl_matches = {}
    duplicate_identity = []
    for row in fixture_rows:
        key = (
            date_from_iso(row["kickoff_time"]),
            team_name.get(row["team_h"], f"UNKNOWN:{row['team_h']}"),
            team_name.get(row["team_a"], f"UNKNOWN:{row['team_a']}"),
        )
        if key in fpl_matches:
            duplicate_identity.append({"fixture_id": row["id"], "identity": key})
        fpl_matches[key] = row

    games = selected_columns(
        decoded_csv_bytes(blobs["tm_games"], gzipped=True),
        [
            "game_id", "competition_id", "date", "home_club_id", "away_club_id",
            "home_club_name", "away_club_name", "home_club_formation", "away_club_formation",
        ],
    )
    dates = {k[0] for k in fpl_matches}
    tm_by_identity = defaultdict(list)
    for row in games:
        if row["competition_id"] != "GB1" or row["date"] not in dates:
            continue
        key = (row["date"], canon(row["home_club_name"]), canon(row["away_club_name"]))
        if key in fpl_matches:
            tm_by_identity[key].append(row)

    matched = {}
    missing = []
    ambiguous = []
    for key, fixture in fpl_matches.items():
        candidates = tm_by_identity.get(key, [])
        if len(candidates) == 1:
            matched[candidates[0]["game_id"]] = candidates[0] | {"fpl_fixture_id": fixture["id"]}
        elif not candidates:
            missing.append({"fixture_id": fixture["id"], "date": key[0], "home": key[1], "away": key[2]})
        else:
            ambiguous.append({"fixture_id": fixture["id"], "identity": key, "game_ids": [x["game_id"] for x in candidates]})

    lineups = selected_columns(
        decoded_csv_bytes(blobs["tm_game_lineups"], gzipped=True),
        ["game_id", "club_id", "type", "player_id", "player_name", "position"],
    )
    selected_game_ids = set(matched)
    starters = defaultdict(dict)
    conflicting = []
    exact_duplicates = 0
    for row in lineups:
        if row["game_id"] not in selected_game_ids or "start" not in row["type"].strip().lower():
            continue
        key = (row["game_id"], row["club_id"])
        player_id = row["player_id"] or row["player_name"].strip().lower()
        payload = (row["player_name"], row["position"])
        if player_id in starters[key]:
            if starters[key][player_id] == payload:
                exact_duplicates += 1
            else:
                conflicting.append({"game_id": row["game_id"], "club_id": row["club_id"], "player_id": player_id})
        else:
            starters[key][player_id] = payload

    bad_team_matches = []
    team_match_rows = 0
    for game_id, game in matched.items():
        for side in ("home", "away"):
            team_match_rows += 1
            club_id = game[f"{side}_club_id"]
            lineup = starters.get((game_id, club_id), {})
            positions = [p[1].strip() for p in lineup.values()]
            formation = (game[f"{side}_club_formation"] or "").strip()
            if len(lineup) != 11 or any(not p for p in positions) or not formation:
                bad_team_matches.append({
                    "game_id": game_id,
                    "fixture_id": game["fpl_fixture_id"],
                    "side": side,
                    "starter_count": len(lineup),
                    "empty_positions": sum(1 for p in positions if not p),
                    "formation": formation,
                })

    checks = {
        "source_hashes_equal_formal_300_gate": source_hash_match,
        "fixture_rows_eq_380": len(fixture_rows) == 380,
        "fpl_identity_unique_380": len(fpl_matches) == 380 and not duplicate_identity,
        "tm_identity_matched_380": len(matched) == 380,
        "tm_identity_no_missing": not missing,
        "tm_identity_no_ambiguous": not ambiguous,
        "team_match_rows_eq_760": team_match_rows == 760,
        "all_team_matches_usable": team_match_rows == 760 and not bad_team_matches,
        "no_conflicting_starter_duplicates": not conflicting,
    }
    passed = all(checks.values())
    terminal = "PASS_R44L2_TRAINING_SUPPORT_380" if passed else "STOP_R44L2_TRAINING_SUPPORT_INCOMPLETE"
    result = {
        "study_id": "r44l2_lineup_role_opportunity_300",
        "stage": "label_free_training_support_gate",
        "formal_weight": 0,
        "pit_eligible": False,
        "terminal_status": terminal,
        "source_sha256": hashes,
        "expected_source_sha256": EXPECTED_HASHES,
        "checks": checks,
        "counts": {
            "fixtures": len(fixture_rows),
            "matched_games": len(matched),
            "team_match_rows": team_match_rows,
            "bad_team_matches": len(bad_team_matches),
            "missing_identity": len(missing),
            "ambiguous_identity": len(ambiguous),
            "conflicting_duplicates": len(conflicting),
            "exact_duplicate_rows_ignored": exact_duplicates,
        },
        "diagnostics": {
            "missing_identity": missing[:50],
            "ambiguous_identity": ambiguous[:50],
            "bad_team_matches": bad_team_matches[:50],
            "conflicting_duplicates": conflicting[:50],
        },
    }
    (OUT_DIR / "training_support_gate_r44l2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (OUT_DIR / "training_support_gate_r44l2.md").write_text(
        "# R44L2 Label-free Training Support Gate\n\n"
        f"- terminal_status: `{terminal}`\n"
        f"- fixtures: {len(fixture_rows)}/380\n"
        f"- matched games: {len(matched)}/380\n"
        f"- team-match rows: {team_match_rows}/760\n"
        f"- bad team-match rows: {len(bad_team_matches)}\n"
        f"- source hash match: {source_hash_match}\n\n"
        + "\n".join(f"- {k}: {'PASS' if v else 'FAIL'}" for k, v in checks.items())
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
