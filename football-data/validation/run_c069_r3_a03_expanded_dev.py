from __future__ import annotations

import argparse
import copy
from pathlib import Path

import evaluate_c069_r3_a03_expanded_dev as r3


MARKER_EVENT_NAME = "__C069_CANONICAL_GOAL_MARKER__"
RAW_EVENT_LOOKBACK = 2


def _tags(event: dict) -> set[int]:
    return {int(x["id"]) for x in event.get("tags", []) if "id" in x}


def _period(event: dict):
    value = event.get("matchPeriod")
    return value if value in {"1H", "2H"} else None


def _opponent(team_id: int, home: int, away: int) -> int:
    if team_id == home:
        return away
    if team_id == away:
        return home
    raise RuntimeError(f"event team {team_id} not in match teams {home}/{away}")


def _recognized_scorer(event: dict, home: int, away: int):
    if _period(event) is None:
        return None
    team_id = int(event.get("teamId", 0) or 0)
    if team_id not in {home, away}:
        return None
    tags = _tags(event)
    if 102 in tags:
        return _opponent(team_id, home, away)
    if 101 in tags and event.get("eventName") in {"Shot", "Free Kick"}:
        return team_id
    return None


def _canonicalize_events(match_row: dict, raw_events: list[dict]) -> tuple[list[dict], int]:
    home = int(match_row["home"])
    away = int(match_row["away"])
    canonical = list(raw_events)
    fallback_count = 0

    for idx, event in enumerate(raw_events):
        period = _period(event)
        if period is None:
            continue
        tags = _tags(event)
        if 101 not in tags or event.get("eventName") != "Save attempt":
            continue
        goalkeeper_team = int(event.get("teamId", 0) or 0)
        if goalkeeper_team not in {home, away}:
            continue
        scorer = _opponent(goalkeeper_team, home, away)

        duplicate = False
        for previous_idx in range(max(0, idx - RAW_EVENT_LOOKBACK), idx):
            previous = raw_events[previous_idx]
            if _period(previous) != period:
                continue
            if _recognized_scorer(previous, home, away) == scorer:
                duplicate = True
                break
        if duplicate:
            continue

        marker = copy.deepcopy(event)
        marker["eventName"] = MARKER_EVENT_NAME
        marker["subEventName"] = "Save-attempt-only goal marker"
        marker["teamId"] = scorer
        # Keep this outside Shot so Late-Tied Aggression cannot change as a side
        # effect of score-stream repair. Timestamp and original tag101 evidence stay.
        canonical.append(marker)
        fallback_count += 1

    return canonical, fallback_count


def _install_canonical_goal_parser() -> None:
    original_load = r3.r1run._load_evaluator
    original_score_event = r3.r1run._score_event

    def score_event(event: dict) -> bool:
        if event.get("eventName") == MARKER_EVENT_NAME:
            return True
        return original_score_event(event)

    def load_evaluator():
        evaluator = original_load()
        original_match_state_stats = evaluator._match_state_stats

        def match_state_stats(match_row: dict, raw_events: list[dict]):
            canonical, fallback_count = _canonicalize_events(match_row, raw_events)
            if fallback_count:
                print(
                    f"C069_GOAL_FALLBACK match={int(match_row['match_id'])} "
                    f"markers={fallback_count} rule=raw_event_lookback_{RAW_EVENT_LOOKBACK}"
                )
            return original_match_state_stats(match_row, canonical)

        evaluator._match_state_stats = match_state_stats
        return evaluator

    r3.r1run._score_event = score_event
    r3.r1run._load_evaluator = load_evaluator


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--a01", required=True)
    p.add_argument("--a02", required=True)
    p.add_argument("--a03", required=True)
    p.add_argument("--matches-zip", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    _install_canonical_goal_parser()
    r3.run(
        Path(a.a01),
        Path(a.a02),
        Path(a.a03),
        Path(a.matches_zip),
        Path(a.out),
    )


if __name__ == "__main__":
    main()
