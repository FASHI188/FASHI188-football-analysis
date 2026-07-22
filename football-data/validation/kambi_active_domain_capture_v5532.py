#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
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

REGISTRY = ROOT / "config" / "active_domain_identity_registry_v5532.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "active_domains"
MANIFEST = ROOT / "manifests" / "kambi_active_domain_capture_v5532_status.json"
LIST_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
DETAIL_PREFIX = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/betoffer/event"
PARAMS = {"lang": "nl_NL", "market": "NL", "client_id": 2, "channel_id": 1, "useCombined": "true"}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.32; +https://github.com/FASHI188/FASHI188-football-analysis)"
KICKOFF_TOLERANCE_SECONDS = 60

GROUP_MAP = {
    "MLS": ("USA_MLS", "2026"),
    "Brasileirao Serie A": ("BRA_SerieA", "2026"),
    "Liga Profesional Argentina": ("ARG_Primera", "2026"),
    "Allsvenskan": ("SWE_Allsvenskan", "2026"),
    "Eliteserien": ("NOR_Eliteserien", "2026"),
    "K-League 1": ("KOR_KLeague1", "2026"),
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def safe(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def fetch_json(url: str, params: dict[str, object], timeout: int = 35) -> tuple[dict, bytes, str, int, str, str]:
    query = dict(params)
    query["ncid"] = int(time.time() * 1000)
    full_url = f"{url}?{urlencode(query)}"
    observed = now_utc()
    req = Request(full_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed public Kambi endpoint only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Kambi response is not a JSON object")
    return payload, raw, full_url, status, content_type, observed


def event_payload(wrapper: dict[str, Any]) -> dict[str, Any]:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return event if isinstance(event, dict) else {}


def group_name(event: dict[str, Any]) -> str:
    group = event.get("group")
    if isinstance(group, dict):
        return str(group.get("englishName") or group.get("name") or "")
    return str(group or "")


def load_registry() -> tuple[dict[str, dict[str, str]], str]:
    raw = REGISTRY.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema_version") != "V5.5.32-active-domain-observed-identity-r1":
        raise ValueError("unexpected active-domain identity schema")
    maps: dict[str, dict[str, str]] = {}
    for _, (cid, _) in GROUP_MAP.items():
        comp = (data.get("competitions") or {}).get(cid) or {}
        aliases: dict[str, str] = {}
        if str(comp.get("status") or "").startswith("PASS_"):
            for team in comp.get("teams") or []:
                canonical = str(team.get("canonical_name") or "").strip()
                for value in [canonical, *(team.get("observed_variants") or [])]:
                    token = norm(value)
                    if token:
                        previous = aliases.get(token)
                        if previous is not None and previous != canonical:
                            raise ValueError(f"identity collision {cid}:{value}:{previous}/{canonical}")
                        aliases[token] = canonical
        maps[cid] = aliases
    return maps, hashlib.sha256(raw).hexdigest()


def snapshot_tokens(snapshot: dict[str, Any], side: str) -> set[str]:
    result = {norm(snapshot.get(f"{side}_team"))}
    source = ((snapshot.get("source_adapter") or {}).get("source_display_names") or {}).get(side)
    if source:
        result.add(norm(source))
    resolution = ((snapshot.get("source_adapter") or {}).get("identity_resolution") or {}).get(side) or {}
    if resolution.get("canonical"):
        result.add(norm(resolution.get("canonical")))
    return {value for value in result if value}


def fresh_marathon_index(batch_start: datetime) -> dict[tuple[str, str], list[tuple[datetime, dict[str, Any], Path]]]:
    index: dict[tuple[str, str], list[tuple[datetime, dict[str, Any], Path]]] = {}
    allowed = {(cid, season) for _, (cid, season) in GROUP_MAP.items()}
    for path in SNAPSHOT_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            key = (str(row.get("competition_id") or ""), str(row.get("season") or ""))
            if row.get("provider_group") != "marathonbet" or key not in allowed:
                continue
            observed = dt(str(row.get("freeze_utc")))
            if observed < batch_start:
                continue
            v = validate(row)
            if not v.get("passed") or not v.get("formal_pit_eligible"):
                continue
            index.setdefault(key, []).append((observed, row, path))
        except Exception:
            continue
    for key in index:
        index[key].sort(key=lambda item: item[0], reverse=True)
    return index


def exact_crosscheck(
    index: dict[tuple[str, str], list[tuple[datetime, dict[str, Any], Path]]],
    *, cid: str,
    season: str,
    source_home: str,
    source_away: str,
    kickoff: str,
    aliases: dict[str, str],
) -> tuple[dict[str, Any] | None, Path | None, str | None, str | None]:
    target_kickoff = dt(kickoff)
    resolved_home = aliases.get(norm(source_home))
    resolved_away = aliases.get(norm(source_away))
    matches = []
    for observed, row, path in index.get((cid, season), []):
        if abs((dt(str(row.get("kickoff_utc"))) - target_kickoff).total_seconds()) > KICKOFF_TOLERANCE_SECONDS:
            continue
        home_exact = norm(source_home) in snapshot_tokens(row, "home") or (resolved_home is not None and resolved_home == row.get("home_team"))
        away_exact = norm(source_away) in snapshot_tokens(row, "away") or (resolved_away is not None and resolved_away == row.get("away_team"))
        if home_exact and away_exact:
            matches.append((observed, row, path))
    if not matches:
        return None, None, resolved_home, resolved_away
    _, row, path = sorted(matches, key=lambda item: item[0], reverse=True)[0]
    return row, path, str(row.get("home_team")), str(row.get("away_team"))


def write_raw(cid: str, home: str, away: str, event_id: int, event: dict[str, Any], detail: dict[str, Any], observed: str, url: str, raw: bytes) -> tuple[Path, str]:
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = RAW_ROOT / f"{safe(cid)}__{safe(home)}__{safe(away)}__{event_id}__{token}.json"
    envelope = {
        "schema_version": "V5.5.32-kambi-active-domain-raw-envelope-r1",
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "observed_at_utc": observed,
        "event_id": event_id,
        "request_url": url,
        "payload_sha256": digest,
        "list_event_identity": {
            "id": event_id,
            "homeName": event.get("homeName"),
            "awayName": event.get("awayName"),
            "start": event.get("start"),
            "state": event.get("state"),
            "group": event.get("group"),
        },
        "payload": detail,
        "formal_evidence_parent": True,
        "research_probe_only": False,
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("payload_sha256") != digest:
            raise FileExistsError(f"immutable Kambi raw collision: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, digest


def formal_path(snapshot: dict[str, Any]) -> Path:
    token = snapshot["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return SNAPSHOT_ROOT / f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__kambi__{token}.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-start-utc", required=True)
    args = parser.parse_args()
    batch_start = dt(args.batch_start_utc)
    aliases_by_cid, registry_sha = load_registry()
    marathon = fresh_marathon_index(batch_start)

    receipt: dict[str, Any] = {
        "schema_version": "V5.5.32-kambi-active-domain-capture-r1",
        "generated_at_utc": now_utc(),
        "batch_start_utc": batch_start.replace(microsecond=0).isoformat(),
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "status": "NO_FORMAL_KAMBI_ACTIVE_DOMAIN_PIT",
        "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
        "identity_registry_sha256": registry_sha,
        "identity_crosscheck_provider_group": "marathonbet",
        "identity_crosscheck_only_no_market_splicing": True,
        "events": [],
        "target_group_event_count": 0,
        "crosschecked_event_count": 0,
        "formal_snapshot_count_written": 0,
        "formal_snapshot_count_available": 0,
        "identity_unresolved_count": 0,
        "crosscheck_missing_count": 0,
        "detail_or_market_fail_count": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "promotion_sample_count_change": 0,
    }

    try:
        listing, listing_raw, list_url, list_status, list_content_type, list_observed = fetch_json(
            LIST_URL, {**PARAMS, "useCombinedLive": "true"}
        )
        events = [row for row in listing.get("events", []) if isinstance(row, dict)]
        receipt["list_view"] = {
            "observed_at_utc": list_observed,
            "request_url": list_url,
            "http_status": list_status,
            "content_type": list_content_type,
            "event_count": len(events),
            "raw_response_sha256": hashlib.sha256(listing_raw).hexdigest(),
        }
    except Exception as exc:
        receipt["status"] = "LISTVIEW_FAIL_CLOSED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 2

    for wrapper in events:
        event = event_payload(wrapper)
        group = group_name(event)
        mapped = GROUP_MAP.get(group)
        if mapped is None:
            continue
        cid, season = mapped
        receipt["target_group_event_count"] += 1
        source_home = str(event.get("homeName") or "")
        source_away = str(event.get("awayName") or "")
        row: dict[str, Any] = {
            "competition_id": cid,
            "season": season,
            "group_english_name": group,
            "event_id": event.get("id"),
            "source_home": source_home,
            "source_away": source_away,
            "provider_start": event.get("start"),
            "provider_state": event.get("state"),
            "status": "FAIL_CLOSED",
        }
        if str(event.get("state") or "") != "NOT_STARTED":
            row["status"] = "NOT_PREMATCH"
            receipt["events"].append(row)
            continue
        try:
            kickoff = dt(str(event.get("start"))).replace(microsecond=0).isoformat()
        except Exception as exc:
            row["status"] = "KICKOFF_INVALID"
            row["error"] = f"{type(exc).__name__}: {exc}"
            receipt["events"].append(row)
            continue

        cross, cross_path, home, away = exact_crosscheck(
            marathon,
            cid=cid,
            season=season,
            source_home=source_home,
            source_away=source_away,
            kickoff=kickoff,
            aliases=aliases_by_cid.get(cid) or {},
        )
        row["canonical_home"] = home
        row["canonical_away"] = away
        if cross is None or cross_path is None:
            if home is None or away is None:
                receipt["identity_unresolved_count"] += 1
                row["status"] = "EXACT_CURRENT_SEASON_IDENTITY_UNRESOLVED"
            else:
                receipt["crosscheck_missing_count"] += 1
                row["status"] = "FRESH_MARATHON_EXACT_PAIR_CROSSCHECK_MISSING"
            receipt["events"].append(row)
            continue

        receipt["crosschecked_event_count"] += 1
        row["identity_crosscheck"] = {
            "provider_group": "marathonbet",
            "snapshot_path": str(cross_path.relative_to(ROOT)),
            "snapshot_sha256": cross.get("raw_snapshot_sha256"),
            "kickoff_utc": cross.get("kickoff_utc"),
            "freeze_utc": cross.get("freeze_utc"),
            "exact_source_or_registry_pair_match": True,
            "fuzzy_matching_used": False,
            "market_values_copied": False,
        }

        try:
            event_id = int(event.get("id"))
            detail, detail_raw, detail_url, _, _, observed = fetch_json(
                f"{DETAIL_PREFIX}/{event_id}.json",
                {**PARAMS, "includeParticipants": "true", "range_start": 0, "range_size": 0},
            )
            if dt(observed) < batch_start:
                raise ValueError("detail observation precedes batch start")
            raw_path, raw_digest = write_raw(cid, home, away, event_id, event, detail, observed, detail_url, detail_raw)
            envelope = json.loads(raw_path.read_text(encoding="utf-8"))
            extracted = extract(envelope, home_team=source_home, away_team=source_away)
            one = extracted["one_x_two"]
            ah = extracted["asian_handicap"]
            ou = extracted["over_under"]
            snapshot: dict[str, Any] = {
                "competition_id": cid,
                "season": season,
                "home_team": home,
                "away_team": away,
                "kickoff_utc": kickoff,
                "settlement_scope": "90m_including_stoppage",
                "freeze_utc": observed,
                "accessed_at_utc": observed,
                "source_observed_at_utc": observed,
                "surface_observed_at_utc": {"one_x_two": observed, "asian_handicap": observed, "over_under": observed},
                "source_url": detail_url,
                "provider_name": "BetCity NL",
                "provider_group": "kambi",
                "one_x_two": {"home": one["home"], "draw": one["draw"], "away": one["away"]},
                "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
                "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
                "source_adapter": {
                    "schema_version": "V5.5.32-kambi-active-domain-capture-r1",
                    "accepted_market_adapter": "V5.5.11-kambi-v523-adapter-r1",
                    "parent_raw_evidence_path": str(raw_path.relative_to(ROOT)),
                    "parent_raw_response_sha256": raw_digest,
                    "source_display_names": {"home": source_home, "away": source_away},
                    "canonical_identity": {"home": home, "away": away},
                    "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
                    "identity_registry_sha256": registry_sha,
                    "identity_time_crosscheck": row["identity_crosscheck"],
                    "identity_time_crosscheck_role": "identity_and_kickoff_only_no_market_surface_splicing",
                    "one_x_two_offer_id": one["offer_id"],
                    "asian_handicap_offer_id": ah["offer_id"],
                    "over_under_offer_id": ou["offer_id"],
                    "asian_handicap_candidate_count": extracted["candidate_counts"]["asian_handicap"],
                    "over_under_candidate_count": extracted["candidate_counts"]["over_under"],
                    "kambi_integer_scaling": {"odds_divisor": 1000, "line_divisor": 1000},
                },
                "observation_semantics": {
                    "retrospective_backfill": False,
                    "source_observed_at_utc": "fresh Kambi event-detail direct observation",
                    "surface_observed_at_utc": "same Kambi event-detail response for 1X2/AH/OU",
                },
                "promotion_semantics": {
                    "single_provider_pit_evidence": True,
                    "independent_provider_consensus": False,
                    "promotion_sample_eligible": False,
                },
            }
            snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
            v = validate(snapshot)
            if not v.get("passed") or not v.get("formal_pit_eligible"):
                raise ValueError(f"V5.2.3 failed: {v.get('errors')}")
            out = formal_path(snapshot)
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable Kambi PIT collision: {out}")
                row["status"] = "ALREADY_PRESENT_IDENTICAL"
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                row["status"] = "VALID_KAMBI_ACTIVE_DOMAIN_PIT_WRITTEN"
                receipt["formal_snapshot_count_written"] += 1
            receipt["formal_snapshot_count_available"] += 1
            row.update({
                "formal_snapshot_path": str(out.relative_to(ROOT)),
                "detail_observed_at_utc": observed,
                "one_x_two": snapshot["one_x_two"],
                "asian_handicap": snapshot["asian_handicap"],
                "over_under": snapshot["over_under"],
                "v523_validation": v,
                "promotion_sample_eligible": False,
            })
        except Exception as exc:
            receipt["detail_or_market_fail_count"] += 1
            row["status"] = "DETAIL_OR_MARKET_FAIL_CLOSED"
            row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["events"].append(row)

    if receipt["formal_snapshot_count_available"]:
        receipt["status"] = "PASS_KAMBI_ACTIVE_DOMAIN_PIT"
    elif receipt["crosschecked_event_count"]:
        receipt["status"] = "CROSSCHECKED_EVENTS_BUT_NO_VALID_KAMBI_PIT"
    receipt["policy"] = (
        "Kambi market surfaces come only from the same immutable Kambi event-detail response. "
        "Marathonbet is used solely for exact fixture identity and kickoff crosscheck. No fuzzy matching, line copying, probability reconstruction or market splicing is allowed."
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "target_group_event_count": receipt["target_group_event_count"],
        "crosschecked_event_count": receipt["crosschecked_event_count"],
        "formal_snapshot_count_available": receipt["formal_snapshot_count_available"],
        "identity_unresolved_count": receipt["identity_unresolved_count"],
        "crosscheck_missing_count": receipt["crosscheck_missing_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
