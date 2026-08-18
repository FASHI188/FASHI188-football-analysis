from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def tag_ids(event: dict) -> list[int]:
    return [int(t["id"]) for t in event.get("tags", []) if "id" in t]


def inspect_package(path: Path, match_id: int) -> bool:
    with zipfile.ZipFile(path) as z:
        name = f"events/{match_id}.json"
        if name not in z.namelist():
            return False
        events = json.loads(z.read(name))
        matches = [json.loads(x) for x in z.read("matches.jsonl").decode().splitlines() if x]
        match = next(x for x in matches if int(x["wyId"]) == match_id)
        teams = list(match["teamsData"].values())
        home = next(t for t in teams if t["side"] == "home")
        away = next(t for t in teams if t["side"] == "away")
        print("MATCH", json.dumps({
            "wyId": match_id,
            "duration": match.get("duration"),
            "dateutc": match.get("dateutc"),
            "home": {"teamId": home["teamId"], "score": home["score"]},
            "away": {"teamId": away["teamId"], "score": away["score"]},
        }, sort_keys=True))
        selected = []
        for idx, event in enumerate(events):
            tags = tag_ids(event)
            if 101 not in tags and 102 not in tags:
                continue
            selected.append({
                "idx": idx,
                "id": event.get("id"),
                "period": event.get("matchPeriod"),
                "eventSec": event.get("eventSec"),
                "eventName": event.get("eventName"),
                "subEventName": event.get("subEventName"),
                "teamId": event.get("teamId"),
                "playerId": event.get("playerId"),
                "tags": tags,
                "positions": event.get("positions"),
            })
        print("GOAL_TAG_EVENTS", json.dumps(selected, sort_keys=True))
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a01", required=True)
    parser.add_argument("--a02", required=True)
    parser.add_argument("--match-id", type=int, default=1694390)
    args = parser.parse_args()
    found = inspect_package(Path(args.a01), args.match_id)
    found = inspect_package(Path(args.a02), args.match_id) or found
    if not found:
        raise RuntimeError(f"match {args.match_id} absent from A01/A02")


if __name__ == "__main__":
    main()
