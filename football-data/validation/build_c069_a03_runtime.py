#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

SEED = "A_SERIES_WYSCOUT_20260818_R1"
MATCHES_SHA = "c8f92bb7533e5c127e043cee764c991b5c25b4f5e70a65be931baae0b1765ce9"
EVENTS_SHA = "877e015b716ffdeea18f04418e3f24fed307ed03c37ff305cabe1f47c4822a45"
START = 800
STOP = 1200
EXPECTED_COUNT = STOP - START


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(matches_zip: str, events_zip: str, out: str) -> None:
    if sha256_file(matches_zip) != MATCHES_SHA:
        raise RuntimeError("matches source hash mismatch")
    if sha256_file(events_zip) != EVENTS_SHA:
        raise RuntimeError("events source hash mismatch")

    mz = zipfile.ZipFile(matches_zip)
    ez = zipfile.ZipFile(events_zip)
    all_matches = []
    for name in mz.namelist():
        if not name.startswith("matches_") or not name.endswith(".json"):
            continue
        competition_file = Path(name).stem.removeprefix("matches_")
        for match in json.loads(mz.read(name)):
            match_id = int(match["wyId"])
            selection_sha = hashlib.sha256(f"{SEED}|{match_id}".encode()).hexdigest()
            all_matches.append((selection_sha, match_id, competition_file, match))

    all_matches.sort(key=lambda x: x[0])
    if len(all_matches) < STOP:
        raise RuntimeError(f"source universe too small: {len(all_matches)} < {STOP}")
    selected = all_matches[START:STOP]
    if len(selected) != EXPECTED_COUNT:
        raise RuntimeError(f"A03 selection count {len(selected)} != {EXPECTED_COUNT}")

    ids = [str(x[1]) for x in selected]
    ids_sha = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    wanted = {x[1] for x in selected}

    events_by_match: dict[int, list[dict]] = {}
    for name in ez.namelist():
        if not name.startswith("events_") or not name.endswith(".json"):
            continue
        for event in json.loads(ez.read(name)):
            match_id = int(event["matchId"])
            if match_id in wanted:
                events_by_match.setdefault(match_id, []).append(event)

    if set(events_by_match) != wanted:
        missing = sorted(wanted - set(events_by_match))
        raise RuntimeError(
            f"A03 event coverage={len(events_by_match)} expected={EXPECTED_COUNT}; missing={missing[:10]}"
        )

    manifest = []
    for package_rank, (selection_sha, match_id, competition_file, match) in enumerate(selected, 1):
        manifest.append(
            {
                "package_id": "A03",
                "rank": package_rank,
                "source_global_rank_one_based": START + package_rank,
                "match_id": match_id,
                "competition_file": competition_file,
                "selection_sha256": selection_sha,
                "event_count": len(events_by_match[match_id]),
            }
        )

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "PACKAGE.json",
            json.dumps(
                {
                    "package_id": "A03",
                    "series": "A",
                    "semantics": "RESEARCH_DEVELOPMENT_ONLY_NOT_PROTECTED",
                    "source": "Pappalardo/Wyscout public event dataset",
                    "match_count": EXPECTED_COUNT,
                    "seed": SEED,
                    "source_global_rank_one_based_start": START + 1,
                    "source_global_rank_one_based_stop": STOP,
                    "ids_sha256": ids_sha,
                },
                indent=2,
            ),
        )
        z.writestr(
            "MANIFEST.jsonl",
            "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in manifest),
        )
        z.writestr(
            "matches.jsonl",
            "".join(json.dumps(x[3], separators=(",", ":")) + "\n" for x in selected),
        )
        for _, match_id, _, _ in selected:
            z.writestr(
                f"events/{match_id}.json",
                json.dumps(events_by_match[match_id], separators=(",", ":")),
            )

    print(
        json.dumps(
            {
                "status": "A03_RUNTIME_REBUILT",
                "source_universe_matches": len(all_matches),
                "matches": EXPECTED_COUNT,
                "global_rank_one_based": [START + 1, STOP],
                "ids_sha256": ids_sha,
                "out": str(out_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches-zip", required=True)
    parser.add_argument("--events-zip", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    main(args.matches_zip, args.events_zip, args.out)
