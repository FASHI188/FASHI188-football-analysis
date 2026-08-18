from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def tag_ids(event: dict) -> list[int]:
    return sorted(int(x["id"]) for x in event.get("tags", []) if "id" in x)


def load_match(package: Path, match_id: int):
    z = zipfile.ZipFile(package)
    if "matches.jsonl" not in z.namelist():
        return None
    matches = [json.loads(line) for line in z.read("matches.jsonl").decode().splitlines() if line]
    match = next((x for x in matches if int(x["wyId"]) == match_id), None)
    if match is None:
        return None
    event_name = f"events/{match_id}.json"
    if event_name not in z.namelist():
        raise RuntimeError(f"match {match_id} found in {package} but event payload missing")
    return match, json.loads(z.read(event_name))


def print_diag(package: Path, match: dict, events: list[dict], match_id: int) -> None:
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
        "package": str(package),
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

    interesting_idx = []
    focus = []
    for idx, event in enumerate(events):
        tags = tag_ids(event)
        name = str(event.get("eventName", ""))
        sub = str(event.get("subEventName", ""))
        if 101 in tags or 102 in tags:
            focus.append(idx)
        if 101 in tags or 102 in tags or name in {"Shot", "Free Kick", "Save attempt"} or "Penalty" in sub or "Goal" in sub:
            interesting_idx.append(idx)

    neighbor_idx = set(interesting_idx)
    for i in focus:
        neighbor_idx.update(range(max(0, i - 3), min(len(events), i + 4)))

    print("DIAG_INTERESTING_COUNT", len(interesting_idx))
    print("DIAG_FOCUS_COUNT", len(focus))
    for idx in sorted(neighbor_idx):
        event = events[idx]
        row = {
            "idx": idx,
            "focus_score_tag": idx in focus,
            "id": event.get("id"),
            "period": event.get("matchPeriod"),
            "sec": event.get("eventSec"),
            "teamId": event.get("teamId"),
            "playerId": event.get("playerId"),
            "eventName": event.get("eventName"),
            "subEventName": event.get("subEventName"),
            "eventId": event.get("eventId"),
            "subEventId": event.get("subEventId"),
            "tags": tag_ids(event),
            "positions": event.get("positions"),
        }
        print("DIAG_EVENT", json.dumps(row, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--package", action="append", required=True, help="runtime A package; repeatable")
    p.add_argument("--match-id", required=True, type=int)
    a = p.parse_args()
    found = False
    for package_s in a.package:
        package = Path(package_s)
        loaded = load_match(package, a.match_id)
        if loaded is None:
            continue
        if found:
            raise RuntimeError(f"match {a.match_id} appears in more than one package")
        found = True
        print_diag(package, loaded[0], loaded[1], a.match_id)
    if not found:
        raise RuntimeError(f"match {a.match_id} not found in supplied packages")
