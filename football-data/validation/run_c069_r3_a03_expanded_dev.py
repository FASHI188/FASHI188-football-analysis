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
            home = int(match_row["home"])
            away = int(match_row["away"])
            ordinary_goals = []
            save101 = []
            for event in raw_events:
                ps = _period_sec(event)
                if ps is None:
                    continue
                tags = _tags(event)
                if 101 not in tags:
                    continue
                period, sec = ps
                name = event.get("eventName")
                team_id = int(event.get("teamId", 0) or 0)
                if name in {"Shot", "Free Kick"}:
                    ordinary_goals.append((period, sec, team_id))
                elif name == "Save attempt":
                    save101.append((event, period, sec, team_id))

            canonical = list(raw_events)
            fallback_count = 0
            for event, period, sec, goalkeeper_team in save101:
                opponent = away if goalkeeper_team == home else home if goalkeeper_team == away else None
                if opponent is None:
                    raise RuntimeError(
                        f"tag101 Save attempt has non-match team match={match_row['match_id']} "
                        f"event={event.get('id')} team={goalkeeper_team}"
                    )
                duplicate = any(
                    p == period
                    and scorer_team == opponent
                    and abs(goal_sec - sec) <= DEDUP_WINDOW_SECONDS
                    for p, goal_sec, scorer_team in ordinary_goals
                )
                if duplicate:
                    continue

                marker = copy.deepcopy(event)
                marker["eventName"] = MARKER_EVENT_NAME
                marker["subEventName"] = "Save-attempt-only goal marker"
                marker["teamId"] = opponent
                # Preserve the original timestamp and tag-101 evidence, while keeping
                # this marker outside Shot so Late-Tied Aggression is not post-hoc altered.
                canonical.append(marker)
                fallback_count += 1

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
