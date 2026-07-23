#!/usr/bin/env python3
"""V6.5.2 near-term Kambi PIT capture for active 2026 leagues.

This is a research evidence feeder for the already-frozen V6.5.1 market-first forward epoch. It does
NOT change V6.5.1's 1-72 hour prediction window, threshold, probabilities, weights or promotion gates.

Target competitions are only groups proven present in the current Kambi listView receipt:
BRA Série A, Argentina Liga Profesional, MLS, Allsvenskan, Eliteserien and K-League 1.

Team identity is derived from the latest validated 17-domain weekly team snapshots. Only exact
normalized names, explicit registered aliases, or unique legal-suffix-safe variants may resolve a
provider team. No historical strength roster and no fuzzy cross-club substitution is allowed.

The feeder fetches only NOT_STARTED events with kickoff <=96h from observation. A Kambi event-detail
response must independently contain complete full-time 1X2/AH/OU; all three surfaces use the same
HTTP observation timestamp and must pass the existing V5.2.3 PIT contract. The resulting snapshot is
single-provider evidence and remains promotion-ineligible until separate independent-provider gates
pass.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
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

TEAM_ROOT = ROOT / "evidence" / "team_configuration_weekly"
TEAM_STATUS = ROOT / "manifests" / "v6_team_configuration_weekly_v660_status.json"
GLOBAL_ALIASES = ROOT / "config" / "team_aliases.json"
SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "active_leagues"
OUT = ROOT / "manifests" / "v6_active_kambi_market_capture_v652_status.json"
LIST_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
DETAIL_PREFIX = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/betoffer/event"
PARAMS = {"lang": "nl_NL", "market": "NL", "client_id": 2, "channel_id": 1, "useCombined": "true", "useCombinedLive": "true"}
USER_AGENT = "Mozilla/5.0 (compatible; football-active-pit-research/6.5.2; +https://github.com/FASHI188/FASHI188-football-analysis)"
MAX_CAPTURE_LEAD = timedelta(hours=96)
V651_MIN_LEAD = timedelta(hours=1)
V651_MAX_LEAD = timedelta(hours=72)
MAX_TEAM_IDENTITY_AGE = timedelta(days=8)
SUFFIXES = {"fc", "sc", "cf", "afc", "ac", "bk", "fk", "sk"}

GROUP_MAP = {
    "Brasileirao Serie A": "BRA_SerieA",
    "Liga Profesional Argentina": "ARG_Primera",
    "MLS": "USA_MLS",
    "Allsvenskan": "SWE_Allsvenskan",
    "Eliteserien": "NOR_Eliteserien",
    "K-League 1": "KOR_KLeague1",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def safe(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_") or "unknown"


def variants(value: Any) -> set[str]:
    base = norm(value)
    if not base:
        return set()
    out = {base}
    tokens = base.split()
    while tokens and tokens[-1] in SUFFIXES:
        tokens = tokens[:-1]
        if tokens:
            out.add(" ".join(tokens))
    return out


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_json(url: str, params: dict[str, Any], timeout: int = 35) -> tuple[dict[str, Any], bytes, str, int, str, datetime]:
    query = dict(params)
    query["ncid"] = int(time.time() * 1000)
    full = f"{url}?{urlencode(query)}"
    req = Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as response:  # nosec - fixed public Kambi endpoints only
        raw = response.read()
        status = int(getattr(response, "status", 200))
        content_type = str(response.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Kambi response is not a JSON object")
    return payload, raw, full, status, content_type, utcnow()


def event_payload(wrapper: dict[str, Any]) -> dict[str, Any]:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return event if isinstance(event, dict) else {}


def group_name(event: dict[str, Any]) -> str:
    group = event.get("group")
    if isinstance(group, dict):
        return str(group.get("englishName") or group.get("name") or "")
    return str(group or "")


def latest_team_rows() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    manifest = json.loads(TEAM_STATUS.read_text(encoding="utf-8"))
    if not str(manifest.get("status") or "").startswith(("PASS", "WARN")):
        raise ValueError(f"weekly team status unusable: {manifest.get('status')}")
    generated = parse_dt(manifest.get("generated_at_utc"))
    if utcnow() - generated > MAX_TEAM_IDENTITY_AGE:
        raise ValueError("weekly team identity ledger is stale")
    expected = manifest.get("domain_team_counts") or {}
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for path in TEAM_ROOT.glob("*.json") if TEAM_ROOT.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = payload.get("snapshots") if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("competition_id") or "")
            team = str(row.get("team_name") or "").strip()
            if cid not in set(GROUP_MAP.values()) or not team:
                continue
            try:
                observed = parse_dt(row.get("observed_at_utc"))
            except Exception:
                continue
            key = (cid, team)
            previous = latest.get(key)
            if previous is None or observed > previous[0]:
                latest[key] = (observed, row)
    rows = {key: item[1] for key, item in latest.items()}
    counts = Counter(cid for cid, _ in rows)
    for cid in GROUP_MAP.values():
        if int(counts.get(cid, 0)) != int(expected.get(cid) or -1):
            raise ValueError(f"current-team identity count mismatch {cid}: {counts.get(cid,0)} != {expected.get(cid)}")
    return rows, manifest


def build_identity_maps(rows: dict[tuple[str, str], dict[str, Any]]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    explicit = json.loads(GLOBAL_ALIASES.read_text(encoding="utf-8"))
    explicit_comps = explicit.get("competitions") or {}
    maps: dict[str, dict[str, str]] = {}
    seasons: dict[str, str] = {}
    for cid in GROUP_MAP.values():
        canonical = sorted(team for comp, team in rows if comp == cid)
        canonical_set = set(canonical)
        season_values = {str(rows[(cid, team)].get("season") or "").strip() for team in canonical}
        season_values.discard("")
        if len(season_values) != 1:
            raise ValueError(f"current season ambiguous for {cid}: {sorted(season_values)}")
        seasons[cid] = next(iter(season_values))
        aliases: dict[str, str] = {}
        for team in canonical:
            for token in variants(team):
                previous = aliases.get(token)
                if previous is not None and previous != team:
                    aliases[token] = "__AMBIGUOUS__"
                else:
                    aliases[token] = team
        for source, target in (explicit_comps.get(cid) or {}).items():
            target_name = str(target or "").strip()
            if target_name not in canonical_set:
                continue
            token = norm(source)
            if not token:
                continue
            previous = aliases.get(token)
            if previous is not None and previous != target_name:
                aliases[token] = "__AMBIGUOUS__"
            else:
                aliases[token] = target_name
        maps[cid] = aliases
    return maps, seasons


def resolve(aliases: dict[str, str], source_name: str) -> tuple[str | None, str]:
    exact = aliases.get(norm(source_name))
    if exact and exact != "__AMBIGUOUS__":
        return exact, "EXACT_OR_REGISTERED_ALIAS"
    matches = {aliases[token] for token in variants(source_name) if aliases.get(token) not in {None, "__AMBIGUOUS__"}}
    return (next(iter(matches)), "UNIQUE_SUFFIX_SAFE") if len(matches) == 1 else (None, "UNRESOLVED_FAIL_CLOSED")


def raw_path(cid: str, home: str, away: str, event_id: int, observed: datetime) -> Path:
    token = observed.strftime("%Y%m%dT%H%M%SZ")
    return RAW_ROOT / f"{safe(cid)}__{safe(home)}__{safe(away)}__{event_id}__{token}.json"


def snapshot_path(snapshot: dict[str, Any]) -> Path:
    token = parse_dt(snapshot["freeze_utc"]).strftime("%Y%m%dT%H%M%SZ")
    return SNAPSHOT_ROOT / f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__kambi_active__{token}.json"


def lead_bucket(hours: float) -> str:
    if hours <= 0:
        return "STARTED_OR_INVALID"
    if hours < 1:
        return "LT_1H"
    if hours <= 72:
        return "H1_72_V651_ELIGIBLE"
    if hours <= 96:
        return "H72_96_CAPTURE_ONLY"
    return "GT_96H_NOT_CAPTURED"


def main() -> int:
    generated = utcnow()
    receipt: dict[str, Any] = {
        "schema_version": "V6.5.2-active-kambi-market-capture-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "FAIL_CLOSED",
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "target_groups": GROUP_MAP,
        "capture_window_hours": [0, 96],
        "v651_prediction_window_hours_unchanged": [1, 72],
        "events": [],
        "formal_snapshot_count_written": 0,
        "v651_timing_eligible_snapshot_count": 0,
        "identity_unresolved_count": 0,
        "detail_or_market_fail_count": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "current_rule_change": False,
    }
    try:
        identity_rows, team_manifest = latest_team_rows()
        aliases_by_cid, seasons = build_identity_maps(identity_rows)
        receipt["identity_source"] = {
            "path": str(TEAM_STATUS.relative_to(ROOT)),
            "sha256": file_sha(TEAM_STATUS),
            "generated_at_utc": team_manifest.get("generated_at_utc"),
            "team_counts": {cid: sum(1 for comp, _ in identity_rows if comp == cid) for cid in GROUP_MAP.values()},
            "season_by_competition": seasons,
            "historical_strength_identity_used": False,
            "fuzzy_cross_club_substitution": False,
        }
        listing, raw, list_url, list_status, content_type, list_observed = fetch_json(LIST_URL, PARAMS)
        events = [row for row in listing.get("events", []) if isinstance(row, dict)]
        receipt["list_view"] = {
            "observed_at_utc": list_observed.isoformat(),
            "request_url": list_url,
            "http_status": list_status,
            "content_type": content_type,
            "event_count": len(events),
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        }
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0

    group_counts: Counter[str] = Counter()
    lead_counts: Counter[str] = Counter()
    unresolved_names: defaultdict[str, Counter[str]] = defaultdict(Counter)
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)

    for wrapper in events:
        event = event_payload(wrapper)
        group = group_name(event)
        cid = GROUP_MAP.get(group)
        if cid is None:
            continue
        group_counts[cid] += 1
        source_home = str(event.get("homeName") or "").strip()
        source_away = str(event.get("awayName") or "").strip()
        row: dict[str, Any] = {
            "competition_id": cid,
            "group_name": group,
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
            kickoff = parse_dt(event.get("start"))
        except Exception as exc:
            row["status"] = "KICKOFF_INVALID"
            row["error"] = f"{type(exc).__name__}: {exc}"
            receipt["events"].append(row)
            continue
        lead = kickoff - list_observed
        hours = lead.total_seconds() / 3600.0
        bucket = lead_bucket(hours)
        lead_counts[bucket] += 1
        row["lead_hours_at_list_observation"] = hours
        row["lead_bucket"] = bucket
        if lead <= timedelta(0) or lead > MAX_CAPTURE_LEAD:
            row["status"] = "OUTSIDE_ACTIVE_CAPTURE_WINDOW"
            receipt["events"].append(row)
            continue
        home, home_method = resolve(aliases_by_cid[cid], source_home)
        away, away_method = resolve(aliases_by_cid[cid], source_away)
        row["identity_resolution"] = {
            "home": {"canonical": home, "method": home_method},
            "away": {"canonical": away, "method": away_method},
        }
        if home is None or away is None or home == away:
            receipt["identity_unresolved_count"] += 1
            if home is None:
                unresolved_names[cid][source_home] += 1
            if away is None:
                unresolved_names[cid][source_away] += 1
            row["status"] = "CURRENT_TEAM_IDENTITY_UNRESOLVED"
            receipt["events"].append(row)
            continue
        try:
            event_id = int(event.get("id"))
            detail, detail_raw, detail_url, detail_status, detail_content_type, observed = fetch_json(
                f"{DETAIL_PREFIX}/{event_id}.json",
                {"lang": "nl_NL", "market": "NL", "client_id": 2, "channel_id": 1, "includeParticipants": "true", "range_start": 0, "range_size": 0},
            )
            if observed >= kickoff:
                raise ValueError("detail observation is not pre-kickoff")
            digest = hashlib.sha256(detail_raw).hexdigest()
            rpath = raw_path(cid, home, away, event_id, observed)
            envelope = {
                "schema_version": "V6.5.2-active-kambi-raw-envelope-r1",
                "provider_name": "BetCity NL",
                "provider_group": "kambi",
                "observed_at_utc": observed.isoformat(),
                "event_id": event_id,
                "request_url": detail_url,
                "http_status": detail_status,
                "content_type": detail_content_type,
                "payload_sha256": digest,
                "list_event_identity": {
                    "homeName": source_home,
                    "awayName": source_away,
                    "start": event.get("start"),
                    "state": event.get("state"),
                    "group": event.get("group"),
                },
                "payload": detail,
            }
            rpath.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
            extracted = extract(envelope, home_team=source_home, away_team=source_away)
            one, ah, ou = extracted["one_x_two"], extracted["asian_handicap"], extracted["over_under"]
            snapshot: dict[str, Any] = {
                "competition_id": cid,
                "season": seasons[cid],
                "home_team": home,
                "away_team": away,
                "kickoff_utc": kickoff.replace(microsecond=0).isoformat(),
                "settlement_scope": "90m_including_stoppage",
                "freeze_utc": observed.isoformat(),
                "accessed_at_utc": observed.isoformat(),
                "source_observed_at_utc": observed.isoformat(),
                "surface_observed_at_utc": {"one_x_two": observed.isoformat(), "asian_handicap": observed.isoformat(), "over_under": observed.isoformat()},
                "source_url": detail_url,
                "provider_name": "BetCity NL",
                "provider_group": "kambi",
                "one_x_two": {"home": one["home"], "draw": one["draw"], "away": one["away"]},
                "asian_handicap": {"line": ah["line"], "home": ah["home"], "away": ah["away"]},
                "over_under": {"line": ou["line"], "over": ou["over"], "under": ou["under"]},
                "source_adapter": {
                    "schema_version": "V6.5.2-active-kambi-capture-r1",
                    "accepted_market_adapter": "V5.5.11-kambi-v523-adapter-r1",
                    "parent_raw_evidence_path": str(rpath.relative_to(ROOT)),
                    "parent_raw_response_sha256": digest,
                    "source_display_names": {"home": source_home, "away": source_away},
                    "canonical_identity": {"home": home, "away": away},
                    "identity_methods": {"home": home_method, "away": away_method},
                    "current_team_identity_status_path": str(TEAM_STATUS.relative_to(ROOT)),
                    "current_team_identity_status_sha256": file_sha(TEAM_STATUS),
                    "one_x_two_offer_id": one["offer_id"],
                    "asian_handicap_offer_id": ah["offer_id"],
                    "over_under_offer_id": ou["offer_id"],
                    "asian_handicap_candidate_count": extracted["candidate_counts"]["asian_handicap"],
                    "over_under_candidate_count": extracted["candidate_counts"]["over_under"],
                    "no_cross_source_market_splicing": True,
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
            validation = validate(snapshot)
            if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                raise ValueError(f"V5.2.3 failed: {validation.get('errors')}")
            spath = snapshot_path(snapshot)
            if spath.exists():
                existing = json.loads(spath.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable active Kambi PIT collision: {spath}")
                row["status"] = "ALREADY_PRESENT_IDENTICAL"
            else:
                spath.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["formal_snapshot_count_written"] += 1
                row["status"] = "VALID_ACTIVE_KAMBI_PIT_WRITTEN"
            detail_lead = kickoff - observed
            row.update({
                "canonical_home": home,
                "canonical_away": away,
                "formal_snapshot_path": str(spath.relative_to(ROOT)),
                "detail_observed_at_utc": observed.isoformat(),
                "lead_hours_at_detail_observation": detail_lead.total_seconds() / 3600.0,
                "v651_timing_eligible": bool(V651_MIN_LEAD <= detail_lead <= V651_MAX_LEAD),
                "one_x_two": snapshot["one_x_two"],
                "asian_handicap": snapshot["asian_handicap"],
                "over_under": snapshot["over_under"],
                "v523_validation": validation,
                "promotion_sample_eligible": False,
            })
            if row["v651_timing_eligible"]:
                receipt["v651_timing_eligible_snapshot_count"] += 1
        except Exception as exc:
            receipt["detail_or_market_fail_count"] += 1
            row["status"] = "DETAIL_OR_MARKET_FAIL_CLOSED"
            row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["events"].append(row)
        time.sleep(0.05)

    receipt["target_group_event_counts"] = dict(sorted(group_counts.items()))
    receipt["lead_bucket_counts"] = dict(sorted(lead_counts.items()))
    receipt["unresolved_source_names"] = {cid: dict(rows.most_common()) for cid, rows in sorted(unresolved_names.items())}
    if receipt["formal_snapshot_count_written"] > 0:
        receipt["status"] = "PASS_ACTIVE_KAMBI_PIT" if receipt["identity_unresolved_count"] == 0 else "WARN_PARTIAL_IDENTITY"
    else:
        receipt["status"] = "WARN_NO_VALID_ACTIVE_SNAPSHOTS"
    receipt["governance"] = {
        "current_weekly_team_identity_only": True,
        "historical_strength_identity_used": False,
        "fuzzy_cross_club_substitution": False,
        "single_kambi_event_detail_for_all_market_surfaces": True,
        "cross_source_market_splicing": False,
        "capture_window_does_not_change_v651_prediction_window": True,
        "v651_prediction_window_hours": [1, 72],
        "promotion_sample_eligible": False,
        "formal_weight_change": False,
        "runtime_probability_change": False,
        "current_rule_change": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())