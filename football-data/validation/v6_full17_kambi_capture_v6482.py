#!/usr/bin/env python3
"""Capture synchronized Kambi 1X2/AH/OU snapshots for all 17 formal domains.

Competition and team identity are fail-closed: exact normalized competition aliases from
V6.48.2 config plus exact current-team identity/registered aliases from the V6.48.2
registry. No fuzzy team or competition matching. A single-provider snapshot can feed
research forward ledgers, but is explicitly not independent-provider consensus and does
not by itself authorize formal market coordination or EV.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from kambi_v523_adapter_v5511 import extract
from prospective_market_snapshot_v523 import canonical_sha256, validate

REGISTRY = ROOT / "config" / "v6_full17_identity_registry_v6482.json"
GROUP_ALIASES = ROOT / "config" / "v6_full17_kambi_group_aliases_v6482.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "full17"
MANIFEST = ROOT / "manifests" / "v6_full17_kambi_capture_v6482_status.json"
LIST_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
DETAIL_PREFIX = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/betoffer/event"
PARAMS = {"lang": "nl_NL", "market": "NL", "client_id": 2, "channel_id": 1, "useCombined": "true"}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/6.48.2; +https://github.com/FASHI188/FASHI188-football-analysis)"
TRANSLATE = str.maketrans({"ø":"o","Ø":"o","ł":"l","Ł":"l","đ":"d","Đ":"d","ð":"d","Ð":"d","þ":"th","Þ":"th","æ":"ae","Æ":"ae","œ":"oe","Œ":"oe"})


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dt(value: object) -> datetime:
    x = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return x.astimezone(timezone.utc)


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").translate(TRANSLATE)).casefold()
    out = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def safe(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def fetch_json(url: str, params: dict[str, object], timeout: int = 35) -> tuple[dict[str, Any], bytes, str, int, str, str]:
    q = dict(params); q["ncid"] = int(time.time() * 1000)
    full = f"{url}?{urlencode(q)}"
    observed = now_utc()
    req = Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed public Kambi endpoint only
        raw = resp.read(); status = int(getattr(resp, "status", 200)); ctype = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Kambi response is not a JSON object")
    return payload, raw, full, status, ctype, observed


def event_payload(wrapper: dict[str, Any]) -> dict[str, Any]:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return event if isinstance(event, dict) else {}


def group_name(event: dict[str, Any]) -> str:
    group = event.get("group")
    if isinstance(group, dict):
        return str(group.get("englishName") or group.get("name") or "")
    return str(group or "")


def load_group_map() -> tuple[dict[str, str], str]:
    raw = GROUP_ALIASES.read_bytes(); data = json.loads(raw.decode("utf-8"))
    aliases: dict[str, str] = {}
    for cid, names in (data.get("domains") or {}).items():
        for name in names or []:
            token = norm(name)
            if not token:
                continue
            previous = aliases.get(token)
            if previous is not None and previous != cid:
                raise ValueError(f"competition alias collision:{name}:{previous}/{cid}")
            aliases[token] = str(cid)
    return aliases, hashlib.sha256(raw).hexdigest()


def load_identity() -> tuple[dict[str, dict[str, str]], dict[str, str], str]:
    raw = REGISTRY.read_bytes(); data = json.loads(raw.decode("utf-8"))
    maps: dict[str, dict[str, str]] = {}
    seasons: dict[str, str] = {}
    for cid, comp in (data.get("competitions") or {}).items():
        aliases: dict[str, str] = {}
        for team in comp.get("teams") or []:
            canonical = str(team.get("canonical_name") or "").strip()
            token = str(team.get("normalized_identity") or norm(canonical)).strip()
            if token and canonical:
                aliases[token] = canonical
            for source_token in team.get("provider_alias_tokens") or []:
                source_token = str(source_token).strip()
                if source_token:
                    prev = aliases.get(source_token)
                    if prev is not None and prev != canonical:
                        raise ValueError(f"team alias collision:{cid}:{source_token}:{prev}/{canonical}")
                    aliases[source_token] = canonical
        maps[str(cid)] = aliases
        seasons[str(cid)] = str(comp.get("processed_latest_season_hint") or "")
    return maps, seasons, hashlib.sha256(raw).hexdigest()


def write_raw(cid: str, home: str, away: str, event_id: int, event: dict[str, Any], detail: dict[str, Any], observed: str, url: str, raw: bytes) -> tuple[Path, str]:
    digest = hashlib.sha256(raw).hexdigest(); token = observed.replace(":", "").replace("+00:00", "Z")
    path = RAW_ROOT / f"{safe(cid)}__{safe(home)}__{safe(away)}__{event_id}__{token}.json"
    envelope = {
        "schema_version": "V6.48.2-kambi-full17-raw-envelope-r1",
        "provider_name": "BetCity NL", "provider_group": "kambi", "observed_at_utc": observed,
        "event_id": event_id, "request_url": url, "payload_sha256": digest,
        "list_event_identity": {"id": event_id, "homeName": event.get("homeName"), "awayName": event.get("awayName"), "start": event.get("start"), "state": event.get("state"), "group": event.get("group")},
        "payload": detail, "formal_evidence_parent": True, "research_probe_only": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("payload_sha256") != digest:
            raise FileExistsError(f"immutable raw collision:{path}")
    else:
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, digest


def formal_path(snapshot: dict[str, Any]) -> Path:
    token = str(snapshot["freeze_utc"]).replace(":", "").replace("+00:00", "Z")
    return SNAPSHOT_ROOT / f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__kambi__{token}.json"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--batch-start-utc", required=False); args = parser.parse_args()
    batch_start = dt(args.batch_start_utc) if args.batch_start_utc else datetime.now(timezone.utc) - __import__('datetime').timedelta(minutes=10)
    group_map, group_sha = load_group_map(); identity, season_hints, identity_sha = load_identity()
    receipt: dict[str, Any] = {
        "schema_version": "V6.48.2-kambi-full17-capture-r1",
        "generated_at_utc": now_utc(), "formal_current_version": "V5.0.1",
        "status": "PASS_NO_TARGET_EVENTS", "batch_start_utc": batch_start.replace(microsecond=0).isoformat(),
        "target_domain_count": 17, "provider_name": "BetCity NL", "provider_group": "kambi",
        "identity_registry_path": str(REGISTRY.relative_to(ROOT)), "identity_registry_sha256": identity_sha,
        "group_alias_path": str(GROUP_ALIASES.relative_to(ROOT)), "group_alias_sha256": group_sha,
        "target_group_event_count": 0, "not_started_target_event_count": 0, "identity_resolved_count": 0,
        "identity_unresolved_count": 0, "detail_or_market_fail_count": 0, "formal_snapshot_count_written": 0,
        "formal_snapshot_count_available": 0, "events": [], "group_inventory": {},
        "formal_weight_change": False, "runtime_probability_change": False, "current_rule_change": False,
    }
    try:
        listing, listing_raw, list_url, list_status, list_ctype, list_observed = fetch_json(LIST_URL, {**PARAMS, "useCombinedLive": "true"})
        wrappers = [x for x in listing.get("events", []) if isinstance(x, dict)]
        receipt["list_view"] = {"observed_at_utc": list_observed, "request_url": list_url, "http_status": list_status, "content_type": list_ctype, "event_count": len(wrappers), "raw_response_sha256": hashlib.sha256(listing_raw).hexdigest()}
    except Exception as exc:
        receipt["status"] = "LISTVIEW_FAIL_CLOSED"; receipt["error"] = f"{type(exc).__name__}: {exc}"
        MANIFEST.parent.mkdir(parents=True, exist_ok=True); MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2)); return 2

    group_counts: Counter[str] = Counter()
    for wrapper in wrappers:
        event = event_payload(wrapper); group = group_name(event); group_counts[group] += 1
        cid = group_map.get(norm(group))
        if cid is None:
            continue
        receipt["target_group_event_count"] += 1
        source_home = str(event.get("homeName") or "").strip(); source_away = str(event.get("awayName") or "").strip()
        row: dict[str, Any] = {"competition_id": cid, "group_english_name": group, "event_id": event.get("id"), "source_home": source_home, "source_away": source_away, "provider_start": event.get("start"), "provider_state": event.get("state"), "status": "FAIL_CLOSED"}
        if str(event.get("state") or "") != "NOT_STARTED":
            row["status"] = "NOT_PREMATCH"; receipt["events"].append(row); continue
        receipt["not_started_target_event_count"] += 1
        try:
            kickoff_dt = dt(event.get("start")); kickoff = kickoff_dt.replace(microsecond=0).isoformat()
            if kickoff_dt <= datetime.now(timezone.utc):
                row["status"] = "KICKOFF_NOT_FUTURE"; receipt["events"].append(row); continue
        except Exception as exc:
            row["status"] = "KICKOFF_INVALID"; row["error"] = f"{type(exc).__name__}: {exc}"; receipt["events"].append(row); continue

        aliases = identity.get(cid) or {}
        home = aliases.get(norm(source_home)); away = aliases.get(norm(source_away))
        row["canonical_home"] = home; row["canonical_away"] = away
        if home is None or away is None or home == away:
            receipt["identity_unresolved_count"] += 1; row["status"] = "EXACT_CURRENT_TEAM_IDENTITY_UNRESOLVED"; receipt["events"].append(row); continue
        receipt["identity_resolved_count"] += 1

        try:
            event_id = int(event.get("id"))
            detail, detail_raw, detail_url, _, _, observed = fetch_json(f"{DETAIL_PREFIX}/{event_id}.json", {**PARAMS, "includeParticipants": "true", "range_start": 0, "range_size": 0})
            if dt(observed) < batch_start:
                raise ValueError("detail observation precedes batch start")
            raw_path, raw_digest = write_raw(cid, home, away, event_id, event, detail, observed, detail_url, detail_raw)
            envelope = json.loads(raw_path.read_text(encoding="utf-8"))
            extracted = extract(envelope, home_team=source_home, away_team=source_away)
            one, ah, ou = extracted["one_x_two"], extracted["asian_handicap"], extracted["over_under"]
            season_hint = season_hints.get(cid) or str(kickoff_dt.year)
            snapshot: dict[str, Any] = {
                "competition_id": cid, "season": season_hint, "home_team": home, "away_team": away,
                "kickoff_utc": kickoff, "settlement_scope": "90m_including_stoppage",
                "freeze_utc": observed, "accessed_at_utc": observed, "source_observed_at_utc": observed,
                "surface_observed_at_utc": {"one_x_two": observed, "asian_handicap": observed, "over_under": observed},
                "source_url": detail_url, "provider_name": "BetCity NL", "provider_group": "kambi",
                "one_x_two": {"home": one["home"], "draw": one["draw"], "away": one["away"]},
                "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
                "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
                "source_adapter": {
                    "schema_version": "V6.48.2-kambi-full17-capture-r1",
                    "accepted_market_adapter": "V5.5.11-kambi-v523-adapter-r1",
                    "parent_raw_evidence_path": str(raw_path.relative_to(ROOT)), "parent_raw_response_sha256": raw_digest,
                    "source_display_names": {"home": source_home, "away": source_away},
                    "canonical_identity": {"home": home, "away": away},
                    "identity_resolution": {"method": "exact_current_registry_or_exact_registered_alias", "fuzzy_matching_used": False, "competition_group_alias_exact": True},
                    "identity_registry_path": str(REGISTRY.relative_to(ROOT)), "identity_registry_sha256": identity_sha,
                    "group_alias_path": str(GROUP_ALIASES.relative_to(ROOT)), "group_alias_sha256": group_sha,
                    "one_x_two_offer_id": one["offer_id"], "asian_handicap_offer_id": ah["offer_id"], "over_under_offer_id": ou["offer_id"],
                    "asian_handicap_candidate_count": extracted["candidate_counts"]["asian_handicap"], "over_under_candidate_count": extracted["candidate_counts"]["over_under"],
                    "kambi_integer_scaling": {"odds_divisor": 1000, "line_divisor": 1000},
                },
                "observation_semantics": {"retrospective_backfill": False, "source_observed_at_utc": "fresh Kambi event-detail direct observation", "surface_observed_at_utc": "same Kambi event-detail response for 1X2/AH/OU"},
                "promotion_semantics": {"single_provider_pit_evidence": True, "independent_provider_consensus": False, "promotion_sample_eligible": False},
            }
            snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
            v = validate(snapshot)
            if not v.get("passed") or not v.get("formal_pit_eligible"):
                raise ValueError(f"V5.2.3 failed:{v.get('errors')}")
            out = formal_path(snapshot); out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable PIT collision:{out}")
                row["status"] = "ALREADY_PRESENT_IDENTICAL"
            else:
                out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"); row["status"] = "VALID_KAMBI_FULL17_PIT_WRITTEN"; receipt["formal_snapshot_count_written"] += 1
            receipt["formal_snapshot_count_available"] += 1
            row["formal_snapshot_path"] = str(out.relative_to(ROOT)); row["detail_observed_at_utc"] = observed
            row["one_x_two"] = snapshot["one_x_two"]; row["asian_handicap"] = snapshot["asian_handicap"]; row["over_under"] = snapshot["over_under"]
        except Exception as exc:
            receipt["detail_or_market_fail_count"] += 1; row["status"] = "DETAIL_OR_MARKET_FAIL_CLOSED"; row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["events"].append(row)

    receipt["group_inventory"] = dict(sorted(group_counts.items()))
    receipt["mapped_domain_event_counts"] = dict(sorted(Counter(str(r.get("competition_id")) for r in receipt["events"] if r.get("competition_id")).items()))
    if receipt["formal_snapshot_count_available"] > 0:
        receipt["status"] = "PASS_FULL17_CAPTURE_WITH_VALID_PIT"
    elif receipt["target_group_event_count"] > 0:
        receipt["status"] = "WARN_TARGET_EVENTS_BUT_NO_VALID_PIT"
    MANIFEST.parent.mkdir(parents=True, exist_ok=True); MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("status","target_group_event_count","not_started_target_event_count","identity_resolved_count","identity_unresolved_count","formal_snapshot_count_written","formal_snapshot_count_available","mapped_domain_event_counts")}, ensure_ascii=False, indent=2))
    return 0 if receipt["status"].startswith("PASS") or receipt["status"].startswith("WARN") else 2


if __name__ == "__main__":
    raise SystemExit(main())
