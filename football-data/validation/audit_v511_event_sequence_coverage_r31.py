#!/usr/bin/env python3
"""R31 repository-wide event-sequence coverage audit.

Read-only audit. It determines whether the existing repository contains actual historical
match-event sequences capable of estimating equalizer / lead-loss hazards. It does not
train a model, call a provider, or modify formal assets.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "manifests" / "v511_event_sequence_coverage_r31_status.json"

DATA_ROOT_NAMES = ("processed", "forward", "research", "inbox", "raw")
CSV_SUFFIXES = {".csv", ".tsv"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}

ALIASES = {
    "event_minute": {
        "event_minute", "goal_minute", "minute", "elapsed", "elapsed_minute",
        "time_elapsed", "match_minute", "incident_minute", "period_minute",
    },
    "event_type": {"event_type", "type", "incident_type", "detail", "event"},
    "event_team": {"team", "team_id", "team_name", "side", "club"},
    "score_after_event": {
        "score", "score_after_event", "home_score", "away_score", "score_home", "score_away",
    },
    "goal_actor": {"scorer", "player", "player_id", "assist", "goal_scorer"},
    "first_goal": {"first_goal", "first_goal_team", "opening_goal", "first_scorer"},
    "equalizer": {"equalizer", "equaliser", "equalized", "equalised", "levelled", "leveled"},
    "lead_state": {
        "score_state", "leading_team", "lead_duration", "went_ahead", "lead_changes",
        "minutes_leading", "minutes_trailing", "minutes_level",
    },
    "late_goal": {"late_goal", "goal_after_70", "goal_after_75", "goal_after_80"},
    "substitution_event": {
        "substitution", "substitution_minute", "sub_in", "sub_out", "substitute",
    },
    "red_card_event": {"red_card_minute", "card_minute", "dismissal_minute"},
    "halftime_state": {"hthg", "htag", "htr", "home_goals_half", "away_goals_half"},
    "aggregate_cards": {"hr", "ar", "hy", "ay", "home_red", "away_red"},
    "aggregate_shots": {"hs", "as", "hst", "ast", "home_shots", "away_shots"},
}


def norm(value: Any) -> str:
    return str(value).strip().casefold().replace(" ", "_").replace("-", "_")


def categories(keys: Iterable[str]) -> set[str]:
    normalized = {norm(key) for key in keys}
    found: set[str] = set()
    for name, aliases in ALIASES.items():
        if normalized & aliases:
            found.add(name)
    return found


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def scan_csv(path: Path) -> dict[str, Any]:
    delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
    try:
        with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            headers = list(reader.fieldnames or [])
            found = categories(headers)
            relevant_headers = {
                header: name
                for header in headers
                for name, aliases in ALIASES.items()
                if norm(header) in aliases
            }
            nonempty_counts: Counter[str] = Counter()
            rows = 0
            for row in reader:
                rows += 1
                if rows > 5000:
                    break
                for header, name in relevant_headers.items():
                    if nonempty(row.get(header)):
                        nonempty_counts[name] += 1
        actual = sorted(name for name in found if nonempty_counts[name] > 0)
        return {
            "path": path.relative_to(ROOT).as_posix(),
            "kind": "csv",
            "rows_sampled": rows,
            "header_categories": sorted(found),
            "actual_nonempty_categories": actual,
            "relevant_columns": sorted(relevant_headers),
            "error": None,
        }
    except Exception as exc:  # audit must report, not hide, unreadable assets
        return {
            "path": path.relative_to(ROOT).as_posix(),
            "kind": "csv",
            "rows_sampled": 0,
            "header_categories": [],
            "actual_nonempty_categories": [],
            "relevant_columns": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def iter_json_objects(path: Path, limit: int = 2000) -> Iterable[Any]:
    if path.suffix.casefold() in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= limit:
                    break
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    value = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    if isinstance(value, list):
        for item in value[:limit]:
            yield item
    elif isinstance(value, dict):
        yield value


def walk_keys(value: Any, depth: int = 0) -> Iterable[tuple[str, Any]]:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from walk_keys(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:100]:
            yield from walk_keys(child, depth + 1)


def scan_json(path: Path) -> dict[str, Any]:
    found: set[str] = set()
    actual: set[str] = set()
    object_count = 0
    try:
        for obj in iter_json_objects(path):
            object_count += 1
            pairs = list(walk_keys(obj))
            keys = [key for key, _ in pairs]
            found |= categories(keys)
            for key, value in pairs:
                normalized = norm(key)
                for name, aliases in ALIASES.items():
                    if normalized in aliases and nonempty(value):
                        actual.add(name)
        return {
            "path": path.relative_to(ROOT).as_posix(),
            "kind": "json",
            "objects_sampled": object_count,
            "header_categories": sorted(found),
            "actual_nonempty_categories": sorted(actual),
            "error": None,
        }
    except Exception as exc:
        return {
            "path": path.relative_to(ROOT).as_posix(),
            "kind": "json",
            "objects_sampled": object_count,
            "header_categories": sorted(found),
            "actual_nonempty_categories": sorted(actual),
            "error": f"{type(exc).__name__}: {exc}",
        }


def strict_event_capable(found: set[str]) -> bool:
    # Minute alone is not enough. A usable event row must also identify an event type and
    # a team/score/actor so the score path can be reconstructed without outcome guessing.
    return "event_minute" in found and "event_type" in found and bool(
        found & {"event_team", "score_after_event", "goal_actor"}
    )


def discover_files() -> list[Path]:
    files: list[Path] = []
    for name in DATA_ROOT_NAMES:
        root = ROOT / name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.casefold()
            if suffix in CSV_SUFFIXES | JSON_SUFFIXES:
                files.append(path)
    return sorted(files)


def run(output: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for path in discover_files():
        if path.suffix.casefold() in CSV_SUFFIXES:
            results.append(scan_csv(path))
        else:
            results.append(scan_json(path))

    strict_files = []
    halftime_files = []
    aggregate_stats_files = []
    event_like_but_incomplete = []
    errors = []
    category_file_counts: Counter[str] = Counter()
    for row in results:
        actual = set(row["actual_nonempty_categories"])
        for name in actual:
            category_file_counts[name] += 1
        if strict_event_capable(actual):
            strict_files.append(row["path"])
        elif actual & {
            "event_minute", "event_type", "event_team", "score_after_event", "goal_actor",
            "first_goal", "equalizer", "lead_state", "late_goal", "substitution_event", "red_card_event",
        }:
            event_like_but_incomplete.append({"path": row["path"], "categories": sorted(actual)})
        if "halftime_state" in actual:
            halftime_files.append(row["path"])
        if actual & {"aggregate_cards", "aggregate_shots"}:
            aggregate_stats_files.append(row["path"])
        if row.get("error"):
            errors.append({"path": row["path"], "error": row["error"]})

    available = len(strict_files) > 0
    status = {
        "schema_version": "v511_event_sequence_coverage_r31_status.1",
        "status": (
            "PASS_R31_EQUALIZER_HAZARD_INPUT_AVAILABLE"
            if available
            else "FAIL_R31_EVENT_SEQUENCE_INPUT_UNAVAILABLE"
        ),
        "classification": "VIEWED_EXISTING_DATA_COVERAGE_AUDIT",
        "formal_weight": 0,
        "files_scanned": len(results),
        "csv_files_scanned": sum(row["kind"] == "csv" for row in results),
        "json_files_scanned": sum(row["kind"] == "json" for row in results),
        "unreadable_files": len(errors),
        "category_file_counts": dict(sorted(category_file_counts.items())),
        "strict_event_sequence_file_count": len(strict_files),
        "strict_event_sequence_files": strict_files[:200],
        "event_like_but_incomplete_count": len(event_like_but_incomplete),
        "event_like_but_incomplete": event_like_but_incomplete[:200],
        "halftime_state_file_count": len(halftime_files),
        "aggregate_match_stats_file_count": len(aggregate_stats_files),
        "errors": errors[:200],
        "ruling": {
            "equalizer_hazard_screen10_allowed": available,
            "halftime_to_fulltime_transition_research_allowed": len(halftime_files) > 0,
            "aggregate_cards_or_shots_are_event_sequences": False,
            "minute_level_events_may_be_inferred_from_final_or_halftime_scores": False,
            "new_provider_collection_performed": False,
            "next_action_if_unavailable": "build fixed 0-0 lane and/or halftime-to-fulltime transition audit; do not fabricate event minutes",
        },
        "hard_limits": {
            "research_only": True,
            "model_training_performed": False,
            "provider_requests": 0,
            "formal_promotion_allowed": False,
            "current_or_main_mutation_allowed": False,
            "current_match_probability_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def self_test() -> None:
    assert strict_event_capable({"event_minute", "event_type", "event_team"})
    assert strict_event_capable({"event_minute", "event_type", "score_after_event"})
    assert not strict_event_capable({"event_minute"})
    assert not strict_event_capable({"halftime_state", "aggregate_cards"})
    print(json.dumps({"self_test": "PASS"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
