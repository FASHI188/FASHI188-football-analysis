from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def tag_ids(event: dict) -> list[int]:
    return sorted(int(x["id"]) for x in event.get("tags", []) if "id" in x)


def main(a03: Path, match_id: int) -> None:
    z = zipfile.ZipFile(a03)
    matches = [json.loads(line) for line in z.read("matches.jsonl").decode().splitlines() if line]
    match = next((x for x in matches if int(x["wyId"]) == match_id), None)
    if match is None:
        raise RuntimeError(f"match {match_id} not in A03")
    events = json.loads(z.read(f"events/{match_id}.json"))
    teams = {
        int(v["teamId"]): {
            "side": v.get("side"),
            "score": v.get("score"),
            "scoreP": v.get("scoreP"),
            "scoreHT": v.get("scoreHT"),
            "scoreET": v.get("scoreET"),
        }
        for v in match["teamsData"].values()
    }
    print("DIAG_MATCH", json.dumps({
        "match_id": match_id,
        "dateutc": match.get("dateutc"),
        "duration": match.get("duration"),
        "competitionId": match.get("competitionId"),
        "roundId": match.get("roundId"),
        "gameweek": match.get("gameweek"),
        "winner": match.get("winner"),
        "status": match.get("status"),
        "teams": teams,
        "event_count": len(events),
    }, ensure_ascii=False, sort_keys=True))

    interesting = []
    for idx, event in enumerate(events):
        tags = tag_ids(event)
        name = str(event.get("eventName", ""))
        sub = str(event.get("subEventName", ""))
        # Print all score-like tags and every shot/free-kick/save/penalty-related action.
        if 101 in tags or 102 in tags or name in {"Shot", "Free Kick", "Save attempt"} or "Penalty" in sub or "Goal" in sub:
            interesting.append({
                "idx": idx,
                "id": event.get("id"),
                "period": event.get("matchPeriod"),
                "sec": event.get("eventSec"),
                "teamId": event.get("teamId"),
                "playerId": event.get("playerId"),
                "eventName": name,
                "subEventName": sub,
                "eventId": event.get("eventId"),
                "subEventId": event.get("subEventId"),
                "tags": tags,
                "positions": event.get("positions"),
            })
    print("DIAG_INTERESTING_COUNT", len(interesting))
    for row in interesting:
        print("DIAG_EVENT", json.dumps(row, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--a03", required=True)
    p.add_argument("--match-id", required=True, type=int)
    a = p.parse_args()
    main(Path(a.a03), a.match_id)
