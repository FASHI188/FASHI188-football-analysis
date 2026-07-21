#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "kambi_four_league_discovery_v5523_status.json"
ENDPOINT = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
PARAMS = {
    "lang": "nl_NL",
    "market": "NL",
    "client_id": 2,
    "channel_id": 1,
    "useCombined": "true",
    "useCombinedLive": "true",
}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.23; +https://github.com/FASHI188/FASHI188-football-analysis)"

TARGETS = {
    "POR_PrimeiraLiga": {"league_tokens": ["primeira liga"]},
    "ESP_LaLiga": {"league_tokens": ["la liga", "laliga"]},
    "FRA_Ligue1": {"league_tokens": ["ligue 1"]},
    "GER_Bundesliga": {"league_tokens": ["bundesliga"]},
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def fetch() -> tuple[dict, bytes, str, int, str, str]:
    params = dict(PARAMS)
    params["ncid"] = int(time.time() * 1000)
    url = f"{ENDPOINT}?{urlencode(params)}"
    observed = now_utc()
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=35) as resp:  # nosec - fixed public Kambi endpoint only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError("Kambi listView response lacks events list")
    return data, raw, url, status, content_type, observed


def event_meta(wrapper: dict) -> dict:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    if not isinstance(event, dict):
        return {}
    return {
        "id": event.get("id"),
        "name": event.get("name"),
        "homeName": event.get("homeName"),
        "awayName": event.get("awayName"),
        "start": event.get("start"),
        "state": event.get("state"),
        "group": event.get("group"),
        "groupId": event.get("groupId"),
        "path": event.get("path"),
        "league": event.get("league"),
        "country": event.get("country"),
    }


def descriptor(meta: dict) -> str:
    return " | ".join(str(meta.get(key)) for key in ("group", "path", "league", "country", "name") if meta.get(key))


def matches_target(cid: str, meta: dict) -> bool:
    text = norm(descriptor(meta))
    return any(token in text for token in TARGETS[cid]["league_tokens"])


def main() -> int:
    receipt = {
        "schema_version": "V5.5.23-kambi-four-league-discovery-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "BetCity NL / Kambi",
        "provider_group": "kambi",
        "status": "BLOCKED",
        "formal_snapshot_written": False,
        "promotion_sample_count_change": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Metadata discovery only. League-name matching does not authorize formal PIT capture; exact current-season identity, event-detail full 1X2/AH/OU and V5.2.3 validation remain mandatory.",
    }
    try:
        data, raw, url, status, content_type, observed = fetch()
        events = [x for x in data.get("events", []) if isinstance(x, dict)]
        group_counter: Counter[str] = Counter()
        path_counter: Counter[str] = Counter()
        target_matches: dict[str, list[dict]] = {cid: [] for cid in TARGETS}
        for wrapper in events:
            meta = event_meta(wrapper)
            group_counter[str(meta.get("group") or "")] += 1
            path = str(meta.get("path") or "")
            if path:
                path_counter[path] += 1
            for cid in TARGETS:
                if matches_target(cid, meta):
                    target_matches[cid].append(meta)
        receipt.update({
            "status": "PASS_DISCOVERY",
            "observed_at_utc": observed,
            "request_url": url,
            "http_status": status,
            "content_type": content_type,
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
            "event_count": len(events),
            "top_groups": group_counter.most_common(80),
            "top_paths": path_counter.most_common(80),
            "target_leagues": {
                cid: {"matched_event_count": len(rows), "events": rows[:50]}
                for cid, rows in target_matches.items()
            },
            "four_league_matched_event_count": sum(len(rows) for rows in target_matches.values()),
        })
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
