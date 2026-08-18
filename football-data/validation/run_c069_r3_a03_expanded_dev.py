from __future__ import annotations

import argparse
import copy
from pathlib import Path

import evaluate_c069_r3_a03_expanded_dev as r3


MARKER_EVENT_NAME = "__C069_CANONICAL_GOAL_MARKER__"
DEDUP_WINDOW_SECONDS = 10.0


def _tags(event: dict) -> set[int]:
    return {int(x["id"]) for x in event.get("tags", []) if "id" in x}


def _period_sec(event: dict):
    period = event.get("matchPeriod")
    if period not in {"1H", "2H"}:
        return None
    return period, float(event.get("eventSec", 0.0))


def _opponent(team_id: int, home: int, away: int) -> int:
    if team_id == home:
        return away
    if team_id == away:
        return home
    raise RuntimeError(f"event team {team_id} not in match teams {home}/{away}")


def _canonicalize_events(match_row: dict, raw_events: list[dict]) -> tuple[list[dict], int]:
    home = int(match_row["home"])
    away = int(match_row["away"])
    recognized_goals: list[tuple[str, float, int]] = []
    save101: list[tuple[dict, str, float, int]] = []

    for event in raw_events:
        ps = _period_sec(event)
        if ps is None:
            continue
        tags = _tags(event)
        period, sec = ps
        name = event.get("eventName")
        team_id = int(event.get("teamId", 0) or 0)
        if team_id not in {home, away}:
            continue

        # Tag 102 is authoritative own-goal semantics in the frozen R1 scorer:
        # the score is credited to the event team's opponent.
        if 102 in tags:
            recognized_goals.append((period, sec, _opponent(team_id, home, away)))
            continue

        if 101 in tags and name in {"Shot", "Free Kick"}:
            recognized_goals.append((period, sec, team_id))
        elif 101 in tags and name == "Save attempt":
            save101.append((event, period, sec, team_id))

    canonical = list(raw_events)
    fallback_count = 0
    for event, period, sec, goalkeeper_team in save101:
        scorer = _opponent(goalkeeper_team, home, away)
        duplicate = any(
            p == period
            and existing_scorer == scorer
            and abs(goal_sec - sec) <= DEDUP_WINDOW_SECONDS
            for p, goal_sec, existing_scorer in recognized_goals
        )
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
        recognized_goals.append((period, sec, scorer))

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
                    f"markers={fallback_count} dedup_window_s={DEDUP_WINDOW_SECONDS:g}"
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
