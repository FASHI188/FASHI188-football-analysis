from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from run_c069_r3_a03_expanded_dev import MARKER_EVENT_NAME, _canonicalize_events, _tags


def load_package(path: Path):
    z = zipfile.ZipFile(path)
    package = json.loads(z.read("PACKAGE.json"))
    matches = [json.loads(line) for line in z.read("matches.jsonl").decode().splitlines() if line]
    events = {
        int(Path(name).stem): json.loads(z.read(name))
        for name in z.namelist()
        if name.startswith("events/") and name.endswith(".json")
    }
    return str(package.get("package_id")), matches, events


def teams_and_score(match: dict):
    teams = list(match["teamsData"].values())
    home = next(x for x in teams if x["side"] == "home")
    away = next(x for x in teams if x["side"] == "away")
    return int(home["teamId"]), int(away["teamId"]), int(home["score"]), int(away["score"])


def scorer(event: dict, home: int, away: int):
    period = event.get("matchPeriod")
    if period not in {"1H", "2H"}:
        return None
    team = int(event.get("teamId", 0) or 0)
    if team not in {home, away}:
        return None
    if event.get("eventName") == MARKER_EVENT_NAME:
        return team
    tags = _tags(event)
    if 102 in tags:
        return away if team == home else home
    if 101 in tags and event.get("eventName") in {"Shot", "Free Kick"}:
        return team
    return None


def diagnostic_events(raw_events: list[dict]) -> list[dict]:
    focus = [
        i for i, event in enumerate(raw_events)
        if 101 in _tags(event) or 102 in _tags(event)
    ]
    indices = set()
    for i in focus:
        indices.update(range(max(0, i - 3), min(len(raw_events), i + 4)))
    rows = []
    for idx in sorted(indices):
        event = raw_events[idx]
        rows.append(
            {
                "idx": idx,
                "score_tag_focus": idx in focus,
                "id": event.get("id"),
                "period": event.get("matchPeriod"),
                "sec": event.get("eventSec"),
                "teamId": event.get("teamId"),
                "playerId": event.get("playerId"),
                "eventName": event.get("eventName"),
                "subEventName": event.get("subEventName"),
                "eventId": event.get("eventId"),
                "subEventId": event.get("subEventId"),
                "tags": sorted(_tags(event)),
                "positions": event.get("positions"),
            }
        )
    return rows


def main(packages: list[Path], out: Path) -> None:
    all_matches = []
    all_events = {}
    package_by_match = {}
    package_ids = []
    for path in packages:
        package_id, matches, events = load_package(path)
        package_ids.append(package_id)
        for match in matches:
            mid = int(match["wyId"])
            if mid in package_by_match:
                raise RuntimeError(f"duplicate match id across packages {mid}")
            package_by_match[mid] = package_id
            all_matches.append(match)
        overlap = set(all_events) & set(events)
        if overlap:
            raise RuntimeError(f"event overlap across packages {sorted(overlap)[:10]}")
        all_events.update(events)

    mismatches = []
    fallback_matches = []
    regular = 0
    skipped_nonregular = 0
    for match in sorted(all_matches, key=lambda x: (x.get("dateutc", ""), int(x["wyId"]))):
        mid = int(match["wyId"])
        if str(match.get("duration", "Regular")) != "Regular":
            skipped_nonregular += 1
            continue
        regular += 1
        home, away, expected_h, expected_a = teams_and_score(match)
        row = {"match_id": mid, "home": home, "away": away}
        raw = all_events[mid]
        canonical, fallback_count = _canonicalize_events(row, raw)
        if fallback_count:
            fallback_matches.append({"match_id": mid, "package": package_by_match[mid], "markers": fallback_count})
        gh = ga = 0
        for event in canonical:
            s = scorer(event, home, away)
            if s == home:
                gh += 1
            elif s == away:
                ga += 1
        if (gh, ga) != (expected_h, expected_a):
            mismatches.append({
                "match_id": mid,
                "package": package_by_match[mid],
                "dateutc": match.get("dateutc"),
                "teams": {"home": home, "away": away},
                "expected": [expected_h, expected_a],
                "reconstructed": [gh, ga],
                "fallback_markers": fallback_count,
                "score_tag_neighborhoods": diagnostic_events(raw),
            })

    result = {
        "schema_version": "C069_R3_GOAL_PARSER_AUDIT_V3",
        "packages": package_ids,
        "source_matches": len(all_matches),
        "regular_matches": regular,
        "skipped_nonregular_matches": skipped_nonregular,
        "fallback_match_count": len(fallback_matches),
        "fallback_matches": fallback_matches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "status": "PASS" if not mismatches else "FAIL",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    compact = {
        "schema_version": result["schema_version"],
        "status": result["status"],
        "source_matches": result["source_matches"],
        "regular_matches": result["regular_matches"],
        "skipped_nonregular_matches": result["skipped_nonregular_matches"],
        "fallback_matches": result["fallback_matches"],
        "mismatch_count": result["mismatch_count"],
    }
    print("C069_AUDIT_SUMMARY", json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
    for mismatch in mismatches:
        print("C069_MISMATCH_DIAG", json.dumps(mismatch, ensure_ascii=False, separators=(",", ":")))
    if mismatches:
        raise RuntimeError(f"goal parser audit mismatches={len(mismatches)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--package", action="append", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    main([Path(x) for x in a.package], Path(a.out))
