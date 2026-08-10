#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PLAYER_HREF = re.compile(r"^/en/players/(\d+)/[^/]+/overview$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise RuntimeError(message)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class LineupDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.testids: Counter[str] = Counter()
        self.starters: list[dict[str, str]] = []
        self._starter: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        tid = a.get("data-testid")
        if tid:
            self.testids[tid] += 1
        if tag == "a" and tid == "lineupsPlayer":
            m = PLAYER_HREF.fullmatch(a.get("href", ""))
            if not m:
                fail("LINEUPS_PLAYER_HREF_CONTRACT_FAILED")
            if self._starter is not None:
                fail("NESTED_LINEUPS_PLAYER_ANCHOR")
            self._starter = {"player_id": m.group(1), "name": ""}
        elif tag == "img" and self._starter is not None and not self._starter["name"]:
            alt = a.get("alt", "").strip()
            if alt:
                self._starter["name"] = alt

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._starter is not None:
            if not self._starter["name"]:
                fail("STARTER_NAME_MISSING")
            self.starters.append(self._starter)
            self._starter = None


def validate_contract(c: dict[str, Any]) -> None:
    if c.get("schema_version") != "R39S-CONFIRMED-XI-DETECTOR-1.0":
        fail("DETECTOR_CONTRACT_SCHEMA_MISMATCH")
    if c.get("research_only") is not True or c.get("formal_weight") != 0:
        fail("DETECTOR_RESEARCH_BOUNDARY_INVALID")
    b = c.get("hard_boundaries") or {}
    for k in ("blind100_labels_accessed", "research_target_labels_used", "model_fits", "candidate_probabilities", "football_api_requests", "api_keys_used"):
        if b.get(k) != 0:
            fail("DETECTOR_HARD_BOUNDARY_INVALID")


def classify(counts: dict[str, int], c: dict[str, Any]) -> str:
    confirmed = (
        counts.get("lineupsFormations", 0) == 1
        and counts.get("teamFormation", 0) == 2
        and counts.get("lineupsPlayer", 0) == 22
        and counts.get("lineupsSubs", 0) >= 1
        and counts.get("squads", 0) == 0
    )
    if confirmed:
        return "CONFIRMED_XI"
    pre = (
        counts.get("lineupsFormations", 0) == 0
        and counts.get("teamFormation", 0) == 0
        and counts.get("lineupsPlayer", 0) == 0
        and counts.get("lineupsSubs", 0) == 0
        and counts.get("squads", 0) >= 1
    )
    if pre:
        return "PRE_ANNOUNCEMENT_SQUADS"
    return "UNKNOWN_FAIL_CLOSED"


def detect(dom_raw: bytes, c: dict[str, Any]) -> dict[str, Any]:
    digest = sha256(dom_raw)
    if not HEX64.fullmatch(digest):
        fail("DOM_SHA_INVALID")
    parser = LineupDOMParser()
    parser.feed(dom_raw.decode("utf-8", errors="replace"))
    parser.close()
    counts = {k: int(v) for k, v in parser.testids.items()}
    state = classify(counts, c)
    starters = parser.starters
    if state == "CONFIRMED_XI":
        if len(starters) != 22:
            fail("CONFIRMED_XI_STARTER_COUNT_NOT_22")
        ids = [x["player_id"] for x in starters]
        if len(set(ids)) != 22:
            fail("CONFIRMED_XI_DUPLICATE_PLAYER_ID")
        home = starters[:11]
        away = starters[11:]
    else:
        if starters:
            fail("NON_CONFIRMED_STATE_HAS_STARTER_ANCHORS")
        home = []
        away = []
    return {
        "schema_version": "R39S-DETECTOR-RESULT-1.0",
        "state": state,
        "dom_sha256": digest,
        "dom_bytes": len(dom_raw),
        "source_native_counts": {
            "lineupsFormations": counts.get("lineupsFormations", 0),
            "teamFormation": counts.get("teamFormation", 0),
            "lineupsPlayer": counts.get("lineupsPlayer", 0),
            "lineupsSubs": counts.get("lineupsSubs", 0),
            "squads": counts.get("squads", 0),
            "squadsLists": counts.get("squadsLists", 0),
            "squadPlayerNationality": counts.get("squadPlayerNationality", 0)
        },
        "starter_count": len(starters),
        "home_starters": home,
        "away_starters": away,
        "formal_weight": 0
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--dom", required=True)
    ap.add_argument("--expected-state", choices=["CONFIRMED_XI", "PRE_ANNOUNCEMENT_SQUADS", "UNKNOWN_FAIL_CLOSED"])
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    c = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    validate_contract(c)
    result = detect(Path(args.dom).read_bytes(), c)
    if args.expected_state and result["state"] != args.expected_state:
        fail(f"EXPECTED_STATE_MISMATCH:{args.expected_state}:{result['state']}")
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"state": result["state"], "counts": result["source_native_counts"], "starter_count": result["starter_count"], "dom_sha256": result["dom_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R39S_DETECTOR_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
