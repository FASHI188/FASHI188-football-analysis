#!/usr/bin/env python3
"""V5.0.2 audited identity bridge between public lineup labels and formal matches.

The bridge is deliberately fail-closed. It never relies on a loose fuzzy-name
match alone. Team identities are accepted through either:

1. an exact normalized/canonical name match that is unique in the competition;
2. a mutual-best, high-overlap schedule fingerprint across seasons and home/away
   roles, with a required separation from the second-best candidate.

After the team crosswalk is frozen, every fixture must agree on competition,
season, calendar date, home/away orientation and final score between the public
Transfermarkt games table and the formal processed match. Ambiguous or
score-conflicting rows are excluded and reported.

Outputs are research identity assets only. No football probability or formal
weight is changed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
ENGINE = FOOTBALL / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import (  # noqa: E402
    canonical_team_name,
    load_aliases,
    normalize_team_token,
    read_processed_matches,
    sha256_file,
)

MAP_PATH = FOOTBALL / "config" / "transfermarkt_lineup_map_v502.json"
OUT = FOOTBALL / "manifests" / "lineup_match_identity_v502_status.json"
REPORT_DIR = FOOTBALL / "manifests" / "lineup_match_identity_v502"
LINK_DIR = FOOTBALL / "player_xi_links"

USER_AGENT = "FASHI188-football-analysis/5.0.2 identity-audit"
MIN_SOURCE_EVENTS = 15
MIN_PROCESSED_EVENTS = 15
MIN_FINGERPRINT_F1 = 0.90
MIN_BEST_MARGIN = 0.10
MIN_SOURCE_FIXTURE_COVERAGE = 0.98
MIN_PROCESSED_FIXTURE_COVERAGE = 0.95


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def iter_csv_gz(path: Path) -> Iterable[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def parse_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def read_lineup_fixtures(competition_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    path = FOOTBALL / "lineups" / competition_id / "historical_lineups.jsonl"
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    if not path.is_file():
        return [], ["lineup_file_missing"]
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            fixture_id = str(row.get("fixture_id") or "").strip()
            if not fixture_id:
                raise ValueError("missing fixture_id")
            grouped[fixture_id].append(row)
        except Exception as exc:
            errors.append(f"line_{line_number}:{exc}")

    fixtures: list[dict[str, Any]] = []
    for fixture_id, rows in sorted(grouped.items()):
        home_rows = [row for row in rows if str(row.get("home_away") or "").lower() == "home"]
        away_rows = [row for row in rows if str(row.get("home_away") or "").lower() == "away"]
        if len(rows) != 2 or len(home_rows) != 1 or len(away_rows) != 1:
            errors.append(
                f"fixture_{fixture_id}:expected_one_home_one_away_rows got={len(rows)}/{len(home_rows)}/{len(away_rows)}"
            )
            continue
        home = home_rows[0]
        away = away_rows[0]
        common_fields = ("competition_id", "season", "kickoff_utc", "game_id")
        if any(str(home.get(field)) != str(away.get(field)) for field in common_fields):
            errors.append(f"fixture_{fixture_id}:home_away_common_field_mismatch")
            continue
        try:
            kickoff = datetime.fromisoformat(str(home["kickoff_utc"]).replace("Z", "+00:00"))
        except Exception:
            errors.append(f"fixture_{fixture_id}:invalid_kickoff")
            continue
        fixtures.append({
            "competition_id": competition_id,
            "season": str(home.get("season") or ""),
            "date": kickoff.date().isoformat(),
            "fixture_id": fixture_id,
            "game_id": str(home.get("game_id") or "").strip(),
            "home_source_team_id": str(home.get("team_source_id") or "").strip(),
            "away_source_team_id": str(away.get("team_source_id") or "").strip(),
            "home_source_name": str(home.get("team") or "").strip(),
            "away_source_name": str(away.get("team") or "").strip(),
            "home_starters": [str(item) for item in home.get("starters") or []],
            "away_starters": [str(item) for item in away.get("starters") or []],
            "home_source_observed_at_utc": str(home.get("source_observed_at_utc") or ""),
            "away_source_observed_at_utc": str(away.get("source_observed_at_utc") or ""),
        })
    return fixtures, errors


def source_game_scores(
    games_path: Path,
    source_competition_id: str,
    game_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    scores: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in iter_csv_gz(games_path):
        if str(row.get("competition_id") or "").strip() != source_competition_id:
            continue
        game_id_raw = str(row.get("game_id") or "").strip()
        try:
            game_id = str(int(game_id_raw))
        except ValueError:
            continue
        if game_id not in game_ids:
            continue
        home_goals = parse_int(row.get("home_club_goals"))
        away_goals = parse_int(row.get("away_club_goals"))
        if home_goals is None or away_goals is None:
            errors.append(f"game_{game_id}:missing_score")
            continue
        scores[game_id] = {
            "home_goals": home_goals,
            "away_goals": away_goals,
            "source_home_club_id": str(parse_int(row.get("home_club_id")) or ""),
            "source_away_club_id": str(parse_int(row.get("away_club_id")) or ""),
            "source_home_name": str(row.get("home_club_name") or "").strip(),
            "source_away_name": str(row.get("away_club_name") or "").strip(),
            "source_url": str(row.get("url") or "").strip(),
        }
    return scores, errors


def event_f1(left: set[tuple[str, str, str]], right: set[tuple[str, str, str]]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return 2.0 * overlap / (len(left) + len(right))


def build_team_crosswalk(
    competition_id: str,
    source_fixtures: list[dict[str, Any]],
    processed_matches,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    aliases = load_aliases()
    source_names: dict[str, set[str]] = defaultdict(set)
    source_events: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for fixture in source_fixtures:
        source_names[fixture["home_source_team_id"]].add(fixture["home_source_name"])
        source_names[fixture["away_source_team_id"]].add(fixture["away_source_name"])
        source_events[fixture["home_source_team_id"]].add((fixture["season"], fixture["date"], "home"))
        source_events[fixture["away_source_team_id"]].add((fixture["season"], fixture["date"], "away"))

    processed_events: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for match in processed_matches:
        processed_events[match.home_team].add((str(match.season), match.date.date().isoformat(), "home"))
        processed_events[match.away_team].add((str(match.season), match.date.date().isoformat(), "away"))

    processed_by_token: dict[str, list[str]] = defaultdict(list)
    for team in sorted(processed_events):
        processed_by_token[normalize_team_token(team)].append(team)

    crosswalk: dict[str, dict[str, Any]] = {}
    used_processed: set[str] = set()
    exact_conflicts: list[dict[str, Any]] = []
    for source_id in sorted(source_events):
        candidate_names = sorted(source_names[source_id])
        candidate_tokens: set[str] = set()
        for raw_name in candidate_names:
            canonical = canonical_team_name(competition_id, raw_name, aliases)
            candidate_tokens.add(normalize_team_token(canonical))
        candidates = sorted({
            team for token in candidate_tokens for team in processed_by_token.get(token, [])
        })
        if len(candidates) == 1 and candidates[0] not in used_processed:
            team = candidates[0]
            crosswalk[source_id] = {
                "processed_team": team,
                "method": "exact_normalized_unique",
                "confidence": 1.0,
                "source_names": candidate_names,
                "source_event_count": len(source_events[source_id]),
                "processed_event_count": len(processed_events[team]),
                "fingerprint_f1": event_f1(source_events[source_id], processed_events[team]),
            }
            used_processed.add(team)
        elif len(candidates) > 1:
            exact_conflicts.append({
                "source_team_id": source_id,
                "source_names": candidate_names,
                "processed_candidates": candidates,
            })

    unresolved_source = [source_id for source_id in sorted(source_events) if source_id not in crosswalk]
    unresolved_processed = [team for team in sorted(processed_events) if team not in used_processed]
    scores: dict[str, list[tuple[float, str]]] = {}
    for source_id in unresolved_source:
        ranked = sorted(
            (
                (event_f1(source_events[source_id], processed_events[team]), team)
                for team in unresolved_processed
                if len(source_events[source_id]) >= MIN_SOURCE_EVENTS
                and len(processed_events[team]) >= MIN_PROCESSED_EVENTS
            ),
            key=lambda item: (-item[0], item[1]),
        )
        scores[source_id] = ranked

    processed_best: dict[str, tuple[float, str] | None] = {}
    for team in unresolved_processed:
        ranked = sorted(
            (
                (event_f1(source_events[source_id], processed_events[team]), source_id)
                for source_id in unresolved_source
                if len(source_events[source_id]) >= MIN_SOURCE_EVENTS
                and len(processed_events[team]) >= MIN_PROCESSED_EVENTS
            ),
            key=lambda item: (-item[0], item[1]),
        )
        processed_best[team] = ranked[0] if ranked else None

    fingerprint_rejections: list[dict[str, Any]] = []
    for source_id in unresolved_source:
        ranked = scores[source_id]
        if not ranked:
            fingerprint_rejections.append({
                "source_team_id": source_id,
                "reason": "no_eligible_processed_candidate",
                "source_names": sorted(source_names[source_id]),
            })
            continue
        best_score, best_team = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        mutual = processed_best.get(best_team)
        mutual_ok = bool(mutual and mutual[1] == source_id)
        margin = best_score - second_score
        accepted = (
            best_score >= MIN_FINGERPRINT_F1
            and margin >= MIN_BEST_MARGIN
            and mutual_ok
            and best_team not in used_processed
        )
        if accepted:
            crosswalk[source_id] = {
                "processed_team": best_team,
                "method": "mutual_schedule_fingerprint",
                "confidence": best_score,
                "source_names": sorted(source_names[source_id]),
                "source_event_count": len(source_events[source_id]),
                "processed_event_count": len(processed_events[best_team]),
                "fingerprint_f1": best_score,
                "second_best_f1": second_score,
                "best_margin": margin,
                "mutual_best": True,
            }
            used_processed.add(best_team)
        else:
            fingerprint_rejections.append({
                "source_team_id": source_id,
                "source_names": sorted(source_names[source_id]),
                "best_processed_team": best_team,
                "best_f1": best_score,
                "second_best_f1": second_score,
                "best_margin": margin,
                "mutual_best": mutual_ok,
                "reason": "fingerprint_gate_failed",
            })

    audit = {
        "source_team_count": len(source_events),
        "processed_team_count": len(processed_events),
        "mapped_source_team_count": len(crosswalk),
        "mapped_processed_team_count": len({item["processed_team"] for item in crosswalk.values()}),
        "exact_mapping_count": sum(item["method"] == "exact_normalized_unique" for item in crosswalk.values()),
        "fingerprint_mapping_count": sum(item["method"] == "mutual_schedule_fingerprint" for item in crosswalk.values()),
        "unmapped_source_team_ids": sorted(set(source_events) - set(crosswalk)),
        "unused_processed_teams": sorted(set(processed_events) - {item["processed_team"] for item in crosswalk.values()}),
        "exact_conflicts": exact_conflicts,
        "fingerprint_rejections": fingerprint_rejections,
        "thresholds": {
            "minimum_source_events": MIN_SOURCE_EVENTS,
            "minimum_processed_events": MIN_PROCESSED_EVENTS,
            "minimum_fingerprint_f1": MIN_FINGERPRINT_F1,
            "minimum_best_margin": MIN_BEST_MARGIN,
            "mutual_best_required": True,
        },
    }
    return crosswalk, audit


def audit_domain(
    competition_id: str,
    mapping: dict[str, Any],
    games_path: Path,
    *,
    write: bool,
) -> dict[str, Any]:
    source_fixtures, lineup_errors = read_lineup_fixtures(competition_id)
    processed_matches = read_processed_matches(competition_id)
    processed_seasons = {str(match.season) for match in processed_matches}
    source_fixtures = [fixture for fixture in source_fixtures if fixture["season"] in processed_seasons]
    game_ids = {fixture["game_id"] for fixture in source_fixtures}
    scores, score_errors = source_game_scores(
        games_path,
        str(mapping["source_competition_id"]),
        game_ids,
    )
    crosswalk, crosswalk_audit = build_team_crosswalk(
        competition_id,
        source_fixtures,
        processed_matches,
    )

    processed_index: dict[tuple[str, str, str, str], list[Any]] = defaultdict(list)
    for match in processed_matches:
        key = (
            str(match.season),
            match.date.date().isoformat(),
            normalize_team_token(match.home_team),
            normalize_team_token(match.away_team),
        )
        processed_index[key].append(match)

    links: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    score_conflicts: list[dict[str, Any]] = []
    source_orientation_conflicts: list[dict[str, Any]] = []
    used_processed_keys: set[tuple[str, str, str, str]] = set()

    for fixture in source_fixtures:
        home_map = crosswalk.get(fixture["home_source_team_id"])
        away_map = crosswalk.get(fixture["away_source_team_id"])
        if home_map is None or away_map is None:
            unmatched.append({
                "fixture_id": fixture["fixture_id"],
                "season": fixture["season"],
                "date": fixture["date"],
                "home_source_name": fixture["home_source_name"],
                "away_source_name": fixture["away_source_name"],
                "reason": "team_identity_unmapped",
            })
            continue
        key = (
            fixture["season"],
            fixture["date"],
            normalize_team_token(home_map["processed_team"]),
            normalize_team_token(away_map["processed_team"]),
        )
        candidates = processed_index.get(key, [])
        if not candidates:
            unmatched.append({
                "fixture_id": fixture["fixture_id"],
                "season": fixture["season"],
                "date": fixture["date"],
                "mapped_home": home_map["processed_team"],
                "mapped_away": away_map["processed_team"],
                "reason": "processed_fixture_not_found",
            })
            continue
        if len(candidates) != 1:
            ambiguous.append({
                "fixture_id": fixture["fixture_id"],
                "key": key,
                "candidate_count": len(candidates),
            })
            continue
        match = candidates[0]
        source_score = scores.get(fixture["game_id"])
        if source_score is None:
            unmatched.append({
                "fixture_id": fixture["fixture_id"],
                "reason": "source_score_missing",
            })
            continue
        expected_home_id = str(source_score.get("source_home_club_id") or "")
        expected_away_id = str(source_score.get("source_away_club_id") or "")
        actual_home_id = fixture["home_source_team_id"].split(":")[-1]
        actual_away_id = fixture["away_source_team_id"].split(":")[-1]
        if expected_home_id != actual_home_id or expected_away_id != actual_away_id:
            source_orientation_conflicts.append({
                "fixture_id": fixture["fixture_id"],
                "lineup_home_id": actual_home_id,
                "lineup_away_id": actual_away_id,
                "games_home_id": expected_home_id,
                "games_away_id": expected_away_id,
            })
            continue
        if (
            int(match.home_goals) != int(source_score["home_goals"])
            or int(match.away_goals) != int(source_score["away_goals"])
        ):
            score_conflicts.append({
                "fixture_id": fixture["fixture_id"],
                "processed_score": [int(match.home_goals), int(match.away_goals)],
                "source_score": [int(source_score["home_goals"]), int(source_score["away_goals"])],
                "season": fixture["season"],
                "date": fixture["date"],
                "home_team": match.home_team,
                "away_team": match.away_team,
            })
            continue
        if key in used_processed_keys:
            ambiguous.append({
                "fixture_id": fixture["fixture_id"],
                "key": key,
                "reason": "multiple_source_fixtures_map_to_one_processed_fixture",
            })
            continue
        used_processed_keys.add(key)
        links.append({
            "competition_id": competition_id,
            "season": fixture["season"],
            "date": fixture["date"],
            "source_fixture_id": fixture["fixture_id"],
            "source_game_id": fixture["game_id"],
            "home_team": match.home_team,
            "away_team": match.away_team,
            "home_source_team_id": fixture["home_source_team_id"],
            "away_source_team_id": fixture["away_source_team_id"],
            "home_identity_method": home_map["method"],
            "away_identity_method": away_map["method"],
            "home_identity_confidence": home_map["confidence"],
            "away_identity_confidence": away_map["confidence"],
            "home_goals": int(match.home_goals),
            "away_goals": int(match.away_goals),
            "home_starters": fixture["home_starters"],
            "away_starters": fixture["away_starters"],
            "home_source_observed_at_utc": fixture["home_source_observed_at_utc"],
            "away_source_observed_at_utc": fixture["away_source_observed_at_utc"],
            "source_url": source_score.get("source_url"),
            "processed_source_path": match.source_path,
        })

    processed_relevant = [
        match for match in processed_matches if str(match.season) in {fixture["season"] for fixture in source_fixtures}
    ]
    source_coverage = len(links) / len(source_fixtures) if source_fixtures else 0.0
    processed_coverage = len(links) / len(processed_relevant) if processed_relevant else 0.0
    mapped_team_coverage = (
        crosswalk_audit["mapped_source_team_count"] / crosswalk_audit["source_team_count"]
        if crosswalk_audit["source_team_count"]
        else 0.0
    )
    passed = (
        not lineup_errors
        and not ambiguous
        and not score_conflicts
        and not source_orientation_conflicts
        and not score_errors
        and mapped_team_coverage >= 0.98
        and source_coverage >= MIN_SOURCE_FIXTURE_COVERAGE
        and processed_coverage >= MIN_PROCESSED_FIXTURE_COVERAGE
    )
    report = {
        "schema_version": "V5.0.2-lineup-match-identity-domain-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "IDENTITY_BRIDGE_PASS" if passed else "IDENTITY_BRIDGE_FAIL",
        "source_fixture_count": len(source_fixtures),
        "processed_relevant_fixture_count": len(processed_relevant),
        "linked_fixture_count": len(links),
        "source_fixture_coverage": source_coverage,
        "processed_fixture_coverage": processed_coverage,
        "mapped_source_team_coverage": mapped_team_coverage,
        "lineup_error_count": len(lineup_errors),
        "score_source_error_count": len(score_errors),
        "unmatched_fixture_count": len(unmatched),
        "ambiguous_fixture_count": len(ambiguous),
        "score_conflict_count": len(score_conflicts),
        "source_orientation_conflict_count": len(source_orientation_conflicts),
        "crosswalk": crosswalk,
        "crosswalk_audit": crosswalk_audit,
        "lineup_error_examples": lineup_errors[:25],
        "score_source_error_examples": score_errors[:25],
        "unmatched_examples": unmatched[:50],
        "ambiguous_examples": ambiguous[:25],
        "score_conflict_examples": score_conflicts[:25],
        "source_orientation_conflict_examples": source_orientation_conflicts[:25],
        "thresholds": {
            "minimum_source_fixture_coverage": MIN_SOURCE_FIXTURE_COVERAGE,
            "minimum_processed_fixture_coverage": MIN_PROCESSED_FIXTURE_COVERAGE,
            "minimum_mapped_source_team_coverage": 0.98,
            "ambiguity_allowed": 0,
            "score_conflict_allowed": 0,
            "orientation_conflict_allowed": 0,
        },
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Identity bridge only. Linked fixture rows may enter lineup-only shadow research; unmatched or conflicting rows remain excluded.",
    }
    if write:
        atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
        atomic_write_jsonl(LINK_DIR / competition_id / "fixture_lineup_links.jsonl", links)
    return report


def run(*, write: bool) -> dict[str, Any]:
    config = load_json(MAP_PATH)
    enabled = {
        competition_id: mapping
        for competition_id, mapping in config["competition_map"].items()
        if bool(mapping.get("enabled_pilot"))
    }
    games_filename = str(config["source_dataset"]["files"]["games"])
    games_url = f"{str(config['source_dataset']['base_url']).rstrip('/')}/{games_filename}"
    with tempfile.TemporaryDirectory(prefix="lineup-identity-v502-") as tmp_dir:
        games_path = Path(tmp_dir) / games_filename
        download(games_url, games_path)
        reports: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for competition_id, mapping in enabled.items():
            try:
                reports[competition_id] = audit_domain(
                    competition_id,
                    mapping,
                    games_path,
                    write=write,
                )
            except Exception as exc:
                failures[competition_id] = str(exc)
        passed = [cid for cid, report in reports.items() if report["status"] == "IDENTITY_BRIDGE_PASS"]
        failed = [cid for cid, report in reports.items() if report["status"] != "IDENTITY_BRIDGE_PASS"]
        manifest = {
            "schema_version": "V5.0.2-lineup-match-identity-aggregate-r1",
            "generated_at_utc": utc_now(),
            "status": "PASS" if not failures and len(passed) == len(enabled) else "FAIL",
            "requested_domains": sorted(enabled),
            "passed_domains": sorted(passed),
            "failed_domains": sorted(failed),
            "execution_failures": failures,
            "source_games_url": games_url,
            "source_games_size_bytes": games_path.stat().st_size,
            "source_games_sha256": sha256_file(games_path),
            "reports": {
                cid: {
                    "status": report["status"],
                    "linked_fixture_count": report["linked_fixture_count"],
                    "source_fixture_coverage": report["source_fixture_coverage"],
                    "processed_fixture_coverage": report["processed_fixture_coverage"],
                    "mapped_source_team_coverage": report["mapped_source_team_coverage"],
                    "unmatched_fixture_count": report["unmatched_fixture_count"],
                    "ambiguous_fixture_count": report["ambiguous_fixture_count"],
                    "score_conflict_count": report["score_conflict_count"],
                }
                for cid, report in reports.items()
            },
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
            "policy": "Identity audit only; no model training or probability influence.",
        }
        if write:
            atomic_write_json(OUT, manifest)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run(write=not args.check_only)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
