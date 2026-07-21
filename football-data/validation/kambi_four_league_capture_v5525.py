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

REGISTRY = ROOT / "config" / "current_season_team_identity_v5524.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "league_targets"
MANIFEST = ROOT / "manifests" / "kambi_four_league_capture_v5525_status.json"
LIST_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
DETAIL_PREFIX = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/betoffer/event"
PARAMS = {"lang": "nl_NL", "market": "NL", "client_id": 2, "channel_id": 1, "useCombined": "true"}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.25; +https://github.com/FASHI188/FASHI188-football-analysis)"
GROUP_MAP = {
    "Spain - LaLiga": "ESP_LaLiga",
    "France - Ligue 1": "FRA_Ligue1",
    "Germany - Bundesliga": "GER_Bundesliga",
    "Portugal - Primeira Liga": "POR_PrimeiraLiga",
}
SEASON = "2026/27"
KICKOFF_CROSSCHECK_TOLERANCE_SECONDS = 60


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def iso(value: str) -> str:
    return dt(value).replace(microsecond=0).isoformat()


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def fetch_json(url: str, params: dict[str, object], timeout: int = 35) -> tuple[dict, bytes, str, int, str, str]:
    query = dict(params)
    query["ncid"] = int(time.time() * 1000)
    full_url = f"{url}?{urlencode(query)}"
    observed = now_utc()
    req = Request(full_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed public Kambi endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Kambi response is not a JSON object")
    return data, raw, full_url, status, content_type, observed


def load_alias_maps() -> tuple[dict[str, dict[str, str]], str]:
    raw = REGISTRY.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema_version") != "V5.5.24-current-season-team-identity-r1" or data.get("season") != SEASON:
        raise ValueError("unexpected current-season identity registry")
    maps: dict[str, dict[str, str]] = {}
    for cid in GROUP_MAP.values():
        comp = (data.get("competitions") or {}).get(cid)
        if not isinstance(comp, dict):
            raise ValueError(f"identity competition missing: {cid}")
        teams = comp.get("teams") or []
        if len(teams) != int(comp.get("team_count") or -1):
            raise ValueError(f"identity team count mismatch: {cid}")
        aliases: dict[str, str] = {}
        for row in teams:
            canonical = str(row.get("canonical_name") or "")
            for value in [canonical, row.get("official_name"), *(row.get("aliases") or [])]:
                if not value:
                    continue
                token = norm(value)
                previous = aliases.get(token)
                if previous is not None and previous != canonical:
                    raise ValueError(f"ambiguous alias {cid}:{value}:{previous}/{canonical}")
                aliases[token] = canonical
        maps[cid] = aliases
    return maps, hashlib.sha256(raw).hexdigest()


def event_payload(wrapper: dict) -> dict:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return event if isinstance(event, dict) else {}


def group_english_name(event: dict) -> str:
    group = event.get("group")
    if isinstance(group, dict):
        return str(group.get("englishName") or group.get("name") or "")
    return str(group or "")


def resolve(aliases: dict[str, str], source: str) -> str | None:
    return aliases.get(norm(source))


def fresh_marathon_crosscheck(cid: str, home: str, away: str, kickoff: str, batch_start: datetime) -> tuple[dict | None, Path | None]:
    best: tuple[datetime, dict, Path] | None = None
    target_kickoff = dt(kickoff)
    for path in SNAPSHOT_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("provider_group") != "marathonbet":
                continue
            if row.get("competition_id") != cid or row.get("season") != SEASON:
                continue
            if row.get("home_team") != home or row.get("away_team") != away:
                continue
            observed = dt(str(row.get("freeze_utc")))
            if observed < batch_start:
                continue
            kickoff_skew = abs((dt(str(row.get("kickoff_utc"))) - target_kickoff).total_seconds())
            if kickoff_skew > KICKOFF_CROSSCHECK_TOLERANCE_SECONDS:
                continue
            v = validate(row)
            if not v.get("passed") or not v.get("formal_pit_eligible"):
                continue
            if best is None or observed > best[0]:
                best = (observed, row, path)
        except Exception:
            continue
    return (best[1], best[2]) if best else (None, None)


def write_raw(cid: str, home: str, away: str, event_id: int, event: dict, detail: dict, observed: str, url: str, raw: bytes) -> tuple[Path, dict, str]:
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = RAW_ROOT / f"{safe(cid)}__{safe(home)}__{safe(away)}__{event_id}__{token}.json"
    envelope = {
        "schema_version": "V5.5.25-kambi-four-league-raw-envelope-r1",
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
            raise FileExistsError(f"immutable Kambi raw envelope collision: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, envelope, digest


def formal_path(snapshot: dict[str, Any]) -> Path:
    token = snapshot["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return SNAPSHOT_ROOT / f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__kambi__{token}.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-start-utc", required=True)
    args = parser.parse_args()
    batch_start = dt(args.batch_start_utc)
    aliases_by_cid, registry_sha = load_alias_maps()
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.25-kambi-four-league-capture-status-r1",
        "generated_at_utc": now_utc(),
        "batch_start_utc": batch_start.replace(microsecond=0).isoformat(),
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "status": "NO_FORMAL_KAMBI_PIT",
        "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
        "identity_registry_sha256": registry_sha,
        "identity_crosscheck_provider_group": "marathonbet",
        "identity_crosscheck_only_no_market_splicing": True,
        "events": [],
        "target_group_event_count": 0,
        "crosschecked_event_count": 0,
        "formal_snapshot_count_written": 0,
        "identity_unresolved_count": 0,
        "crosscheck_missing_count": 0,
        "detail_or_market_fail_count": 0,
        "promotion_sample_count_change": 0,
        "formal_weight_change": False,
        "probability_change": False,
    }
    try:
        listing, listing_raw, list_url, list_status, list_content_type, list_observed = fetch_json(LIST_URL, {**PARAMS, "useCombinedLive": "true"})
        events = [x for x in listing.get("events", []) if isinstance(x, dict)]
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
        MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0

    for wrapper in events:
        event = event_payload(wrapper)
        group_name = group_english_name(event)
        cid = GROUP_MAP.get(group_name)
        if cid is None:
            continue
        receipt["target_group_event_count"] += 1
        source_home = str(event.get("homeName") or "")
        source_away = str(event.get("awayName") or "")
        row: dict[str, Any] = {
            "competition_id": cid,
            "group_english_name": group_name,
            "event_id": event.get("id"),
            "source_home": source_home,
            "source_away": source_away,
            "provider_start": event.get("start"),
            "provider_state": event.get("state"),
            "status": "FAIL_CLOSED",
        }
        home = resolve(aliases_by_cid[cid], source_home)
        away = resolve(aliases_by_cid[cid], source_away)
        row["canonical_home"] = home
        row["canonical_away"] = away
        if home is None or away is None:
            receipt["identity_unresolved_count"] += 1
            row["status"] = "CURRENT_SEASON_IDENTITY_UNRESOLVED"
            receipt["events"].append(row)
            continue
        if str(event.get("state") or "") != "NOT_STARTED":
            row["status"] = "NOT_PREMATCH"
            receipt["events"].append(row)
            continue
        try:
            kickoff = iso(str(event.get("start")))
        except Exception as exc:
            row["status"] = "KICKOFF_INVALID"
            row["error"] = f"{type(exc).__name__}: {exc}"
            receipt["events"].append(row)
            continue
        marathon, marathon_path = fresh_marathon_crosscheck(cid, home, away, kickoff, batch_start)
        if marathon is None or marathon_path is None:
            receipt["crosscheck_missing_count"] += 1
            row["status"] = "FRESH_MARATHON_IDENTITY_TIME_CROSSCHECK_MISSING"
            receipt["events"].append(row)
            continue
        receipt["crosschecked_event_count"] += 1
        row["identity_crosscheck"] = {
            "provider_group": "marathonbet",
            "snapshot_path": str(marathon_path.relative_to(ROOT)),
            "snapshot_sha256": marathon.get("raw_snapshot_sha256"),
            "kickoff_utc": marathon.get("kickoff_utc"),
            "freeze_utc": marathon.get("freeze_utc"),
            "market_values_copied": False,
        }
        try:
            event_id = int(event.get("id"))
            detail, detail_raw, detail_url, detail_status, detail_content_type, observed = fetch_json(
                f"{DETAIL_PREFIX}/{event_id}.json",
                {**PARAMS, "includeParticipants": "true", "range_start": 0, "range_size": 0},
            )
            if observed < args.batch_start_utc:
                raise ValueError("detail observation precedes batch start")
            raw_path, envelope, raw_digest = write_raw(cid, home, away, event_id, event, detail, observed, detail_url, detail_raw)
            extracted = extract(envelope, home_team=source_home, away_team=source_away)
            one = extracted["one_x_two"]
            ah = extracted["asian_handicap"]
            ou = extracted["over_under"]
            snapshot: dict[str, Any] = {
                "competition_id": cid,
                "season": SEASON,
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
                    "schema_version": "V5.5.25-kambi-four-league-capture-r1",
                    "accepted_market_adapter": "V5.5.11-kambi-v523-adapter-r1",
                    "parent_raw_evidence_path": str(raw_path.relative_to(ROOT)),
                    "parent_raw_response_sha256": raw_digest,
                    "source_display_names": {"home": source_home, "away": source_away},
                    "canonical_identity": {"home": home, "away": away},
                    "current_season_identity_registry_path": str(REGISTRY.relative_to(ROOT)),
                    "current_season_identity_registry_sha256": registry_sha,
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
                receipt["formal_snapshot_count_written"] += 1
                row["status"] = "VALID_KAMBI_PIT_SNAPSHOT_WRITTEN"
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

    if receipt["formal_snapshot_count_written"]:
        receipt["status"] = "PASS_KAMBI_FOUR_LEAGUE_PIT"
    elif receipt["crosschecked_event_count"]:
        receipt["status"] = "CROSSCHECKED_EVENTS_BUT_NO_VALID_KAMBI_PIT"
    receipt["policy"] = "Kambi market surfaces remain fully independent and come only from Kambi event-detail JSON. Fresh Marathonbet snapshots are used only to independently verify canonical match identity and kickoff time for league-wide capture; no price, line, probability or market surface is copied or spliced. All snapshots remain promotion-ineligible until strict synchronized independent-provider consensus passes."
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
