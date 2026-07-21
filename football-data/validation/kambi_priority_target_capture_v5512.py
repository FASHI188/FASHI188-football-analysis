#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from kambi_v523_adapter_v5511 import build_snapshot
from prospective_market_snapshot_v523 import validate

MANIFEST = ROOT / "manifests" / "kambi_priority_target_capture_v5512_status.json"
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "priority_targets"
FORMAL_ROOT = ROOT / "evidence" / "markets_prospective"
LIST_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
DETAIL_PREFIX = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/betoffer/event"
PARAMS = {
    "lang": "nl_NL",
    "market": "NL",
    "client_id": 2,
    "channel_id": 1,
    "useCombined": "true",
}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.12; +https://github.com/FASHI188/FASHI188-football-analysis)"
KICKOFF_TOLERANCE_SECONDS = 300
TEAM_SIMILARITY_MIN = 0.82

TARGETS = [
    {
        "competition_id": "POR_PrimeiraLiga",
        "season": "2026/27",
        "home_team": "Estoril Praia",
        "away_team": "FC Famalicão",
        "kickoff_utc": "2026-08-07T19:15:00+00:00",
    },
    {
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "home_team": "Deportivo Alavés",
        "away_team": "Getafe CF",
        "kickoff_utc": "2026-08-15T17:30:00+00:00",
    },
    {
        "competition_id": "FRA_Ligue1",
        "season": "2026/27",
        "home_team": "Olympique de Marseille",
        "away_team": "RC Strasbourg Alsace",
        "kickoff_utc": "2026-08-21T18:45:00+00:00",
    },
    {
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "home_team": "FC Bayern München",
        "away_team": "VfB Stuttgart",
        "kickoff_utc": "2026-08-28T18:30:00+00:00",
    },
]


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dt(value: str) -> datetime:
    token = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(token)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {value}")
    return parsed.astimezone(timezone.utc)


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"\b(fc|cf|vfb|rc|olympique|deportivo)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def similarity(a: object, b: object) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def get_json(url: str, params: dict[str, object], timeout: int = 35) -> tuple[dict, bytes, str, int, str, str]:
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
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response is not JSON object")
    return payload, raw, full_url, status, content_type, observed


def event_info(wrapper: dict) -> dict:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return event if isinstance(event, dict) else {}


def match_target(events: list[dict], target: dict) -> tuple[dict | None, dict]:
    ranked = []
    target_start = dt(target["kickoff_utc"])
    for wrapper in events:
        if not isinstance(wrapper, dict):
            continue
        event = event_info(wrapper)
        home = str(event.get("homeName") or "")
        away = str(event.get("awayName") or "")
        start = event.get("start")
        if not start:
            continue
        try:
            skew = abs((dt(str(start)) - target_start).total_seconds())
        except Exception:
            continue
        hs = similarity(home, target["home_team"])
        aws = similarity(away, target["away_team"])
        state = str(event.get("state") or "")
        ranked.append((hs + aws, -skew, hs, aws, skew, state, wrapper))
    if not ranked:
        return None, {"status": "NO_EVENTS_WITH_VALID_TIME"}
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _, _, hs, aws, skew, state, wrapper = ranked[0]
    event = event_info(wrapper)
    meta = {
        "best_home_similarity": round(hs, 4),
        "best_away_similarity": round(aws, 4),
        "kickoff_skew_seconds": skew,
        "provider_home": event.get("homeName"),
        "provider_away": event.get("awayName"),
        "provider_start": event.get("start"),
        "provider_state": state,
        "event_id": event.get("id"),
    }
    eligible = hs >= TEAM_SIMILARITY_MIN and aws >= TEAM_SIMILARITY_MIN and skew <= KICKOFF_TOLERANCE_SECONDS and state == "NOT_STARTED"
    meta["identity_gate_pass"] = eligible
    return (wrapper if eligible else None), meta


def write_raw(target: dict, event: dict, detail: dict, observed: str, request_url: str, raw_sha256: str) -> tuple[Path, dict]:
    event_id = int(event.get("id"))
    envelope = {
        "schema_version": "V5.5.12-kambi-priority-target-raw-envelope-r1",
        "observed_at_utc": observed,
        "operator": "BetCity NL",
        "provider_group": "kambi",
        "event_id": event_id,
        "payload_sha256": raw_sha256,
        "request_url": request_url,
        "list_event_identity": {
            "id": event_id,
            "homeName": event.get("homeName"),
            "awayName": event.get("awayName"),
            "start": event.get("start"),
            "state": event.get("state"),
        },
        "target": target,
        "payload": detail,
        "formal_evidence": False,
        "research_probe_only": False,
        "role": "raw_direct_provider_parent_for_v523_snapshot",
    }
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = RAW_ROOT / f"{safe(target['competition_id'])}__{safe(target['home_team'])}__{safe(target['away_team'])}__{event_id}__{token}.json"
    if path.exists():
        raise FileExistsError(f"immutable raw target envelope already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, envelope


def formal_path(snapshot: dict) -> Path:
    token = snapshot["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return FORMAL_ROOT / (
        f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__kambi__{token}.json"
    )


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.12-kambi-priority-target-capture-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "BetCity NL",
        "provider_group": "kambi",
        "status": "NO_TARGETS_CAPTURED",
        "target_count": len(TARGETS),
        "targets": [],
        "formal_snapshot_count_written": 0,
        "raw_target_envelope_count_written": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "consensus_change": False,
        "policy": "Write a formal V5.2.3 PIT snapshot only when exact target identity, pre-kickoff state, complete direct Kambi Full Time 1X2/AH/OU extraction, immutable parent raw evidence and V5.2.3 validator all pass. No cross-source surface splicing. Kambi remains one provider_group regardless of frontend skin.",
    }

    try:
        listing, list_raw, list_request_url, list_status, list_content_type, list_observed = get_json(
            LIST_URL,
            {**PARAMS, "useCombinedLive": "true"},
        )
        events = [x for x in listing.get("events", []) if isinstance(x, dict)]
        manifest["list_view"] = {
            "observed_at_utc": list_observed,
            "request_url": list_request_url,
            "http_status": list_status,
            "content_type": list_content_type,
            "event_count": len(events),
            "raw_response_sha256": hashlib.sha256(list_raw).hexdigest(),
        }
    except Exception as exc:
        manifest["status"] = "BLOCKED_LISTVIEW"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    for target in TARGETS:
        wrapper, match_meta = match_target(events, target)
        row = {
            "target": target,
            "match": match_meta,
            "capture_status": "TARGET_NOT_AVAILABLE_OR_IDENTITY_GATE_FAILED",
            "formal_snapshot_written": False,
        }
        if wrapper is None:
            manifest["targets"].append(row)
            continue
        event = event_info(wrapper)
        event_id = int(event.get("id"))
        try:
            detail, detail_raw, detail_url, detail_status, detail_content_type, detail_observed = get_json(
                f"{DETAIL_PREFIX}/{event_id}.json",
                {**PARAMS, "includeParticipants": "true", "range_start": 0, "range_size": 0},
            )
            raw_sha = hashlib.sha256(detail_raw).hexdigest()
            raw_path, envelope = write_raw(target, event, detail, detail_observed, detail_url, raw_sha)
            manifest["raw_target_envelope_count_written"] += 1

            # The raw envelope itself carries the listView identity that passed above.
            identity = envelope["list_event_identity"]
            if not (
                similarity(identity.get("homeName"), target["home_team"]) >= TEAM_SIMILARITY_MIN
                and similarity(identity.get("awayName"), target["away_team"]) >= TEAM_SIMILARITY_MIN
                and abs((dt(identity["start"]) - dt(target["kickoff_utc"])).total_seconds()) <= KICKOFF_TOLERANCE_SECONDS
                and identity.get("state") == "NOT_STARTED"
            ):
                raise ValueError("post-fetch identity gate failed")

            snapshot = build_snapshot(
                envelope,
                competition_id=target["competition_id"],
                season=target["season"],
                home_team=target["home_team"],
                away_team=target["away_team"],
                kickoff_utc=target["kickoff_utc"],
                observed_at_utc=detail_observed,
            )
            validation = validate(snapshot)
            if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                raise ValueError(f"V5.2.3 validation failed: {validation.get('errors')}")
            snapshot["source_adapter"]["parent_raw_evidence_path"] = str(raw_path.relative_to(ROOT))
            snapshot["source_adapter"]["parent_raw_response_sha256"] = raw_sha
            # Added metadata changes the canonical hash, so re-hash and revalidate.
            snapshot["raw_snapshot_sha256"] = canonical_snapshot_hash(snapshot)
            validation = validate(snapshot)
            if not validation.get("passed"):
                raise ValueError(f"V5.2.3 validation failed after parent linkage: {validation.get('errors')}")
            out = formal_path(snapshot)
            if out.exists():
                raise FileExistsError(f"immutable formal PIT snapshot already exists: {out}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["formal_snapshot_count_written"] += 1
            row.update({
                "capture_status": "VALID_PIT_SNAPSHOT_WRITTEN",
                "formal_snapshot_written": True,
                "raw_evidence_path": str(raw_path.relative_to(ROOT)),
                "formal_snapshot_path": str(out.relative_to(ROOT)),
                "detail_http_status": detail_status,
                "detail_content_type": detail_content_type,
                "detail_observed_at_utc": detail_observed,
                "one_x_two": snapshot["one_x_two"],
                "asian_handicap": snapshot["asian_handicap"],
                "over_under": snapshot["over_under"],
                "v523_validation": validation,
            })
        except Exception as exc:
            row["capture_status"] = "MATCHED_BUT_CAPTURE_FAILED_CLOSED"
            row["error"] = f"{type(exc).__name__}: {exc}"
        manifest["targets"].append(row)

    if manifest["formal_snapshot_count_written"]:
        manifest["status"] = "FORMAL_PIT_SNAPSHOTS_WRITTEN"
    elif manifest["raw_target_envelope_count_written"]:
        manifest["status"] = "TARGET_RAW_CAPTURED_FORMAL_GATE_NOT_PASSED"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def canonical_snapshot_hash(snapshot: dict) -> str:
    from prospective_market_snapshot_v523 import canonical_sha256
    return canonical_sha256(snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
