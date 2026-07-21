#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "direct_kambi_pit_probe_v558_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi"
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.8; +https://github.com/FASHI188/FASHI188-football-analysis)"

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

# Candidate public offering endpoints documented in historical public front-end use.
# Reachability is re-tested at runtime; a historical endpoint is never assumed current.
CANDIDATES = [
    {
        "operator": "BetCity NL",
        "provider_group": "kambi",
        "url": "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json",
        "params": {"lang": "nl_NL", "market": "NL", "client_id": 2, "channel_id": 1, "useCombined": "true", "useCombinedLive": "true"},
        "evidence_basis": "public front-end offering endpoint observed in 2024",
    },
    {
        "operator": "Unibet US NJ legacy public offering",
        "provider_group": "kambi",
        "url": "https://eu-offering.kambicdn.org/offering/v2018/ubusnj/listView/football.json",
        "params": {"lang": "en_US", "market": "US-NJ", "client_id": 2, "channel_id": 1, "useCombined": "true", "useCombinedLive": "true"},
        "evidence_basis": "public Unibet/Kambi offering endpoint documented in prior front-end network traffic",
    },
    {
        "operator": "888 legacy public offering",
        "provider_group": "kambi",
        "url": "https://eu-offering.kambicdn.org/offering/v2018/888/listView/football.json",
        "params": {"lang": "en_GB", "market": "GB", "client_id": 2, "channel_id": 1, "useCombined": "true"},
        "evidence_basis": "public 888/Kambi offering endpoint documented in prior front-end network traffic",
    },
]


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def fetch(candidate: dict) -> tuple[dict, bytes, str, int, str]:
    params = dict(candidate["params"])
    params["ncid"] = int(time.time() * 1000)
    url = f"{candidate['url']}?{urlencode(params)}"
    observed = now_utc()
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=30) as resp:  # nosec - fixed public offering endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    return data, raw, url, status, content_type


def event_info(wrapper: dict) -> dict:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return event if isinstance(event, dict) else {}


def event_names(wrapper: dict) -> tuple[str, str, str]:
    event = event_info(wrapper)
    home = str(event.get("homeName") or event.get("home") or "")
    away = str(event.get("awayName") or event.get("away") or "")
    name = str(event.get("name") or "")
    if (not home or not away) and name:
        for sep in (" - ", " vs ", " v ", " @ "):
            if sep in name:
                left, right = name.split(sep, 1)
                home = home or left
                away = away or right
                break
    return home, away, name


def best_match(events: list[dict], target: dict) -> tuple[dict | None, dict]:
    best = None
    best_score = -1.0
    meta: dict = {}
    for wrapper in events:
        if not isinstance(wrapper, dict):
            continue
        home, away, name = event_names(wrapper)
        score_normal = (similarity(home, target["home_team"]) + similarity(away, target["away_team"])) / 2.0
        score_reversed = (similarity(away, target["home_team"]) + similarity(home, target["away_team"])) / 2.0
        score = max(score_normal, score_reversed)
        if score > best_score:
            best_score = score
            best = wrapper
            event = event_info(wrapper)
            meta = {
                "best_similarity": round(max(score, 0.0), 4),
                "provider_home": home,
                "provider_away": away,
                "provider_name": name,
                "event_id": event.get("id"),
                "provider_start": event.get("start"),
                "reversed_name_order": score_reversed > score_normal,
            }
    return (best if best_score >= 0.72 else None), meta


def summarize_offer(offer: dict) -> dict:
    criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
    offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
    outcomes = offer.get("outcomes") if isinstance(offer.get("outcomes"), list) else []
    return {
        "id": offer.get("id"),
        "criterion_label": criterion.get("label"),
        "criterion_english_label": criterion.get("englishLabel"),
        "bet_offer_type_name": offer_type.get("name"),
        "bet_offer_type_id": offer_type.get("id"),
        "outcomes": [
            {
                "id": outcome.get("id"),
                "label": outcome.get("label"),
                "type": outcome.get("type"),
                "line": outcome.get("line"),
                "odds": outcome.get("odds"),
                "odds_fractional": outcome.get("oddsFractional"),
                "status": outcome.get("status"),
            }
            for outcome in outcomes[:20]
            if isinstance(outcome, dict)
        ],
    }


def market_summary(wrapper: dict) -> list[dict]:
    offers = wrapper.get("betOffers")
    if not isinstance(offers, list):
        return []
    return [summarize_offer(o) for o in offers if isinstance(o, dict)]


def write_raw(candidate: dict, target: dict, wrapper: dict, request_meta: dict) -> tuple[str, str]:
    raw_bytes = json.dumps(wrapper, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    token = request_meta["observed_at_utc"].replace(":", "").replace("+00:00", "Z")
    safe_operator = re.sub(r"[^A-Za-z0-9_-]+", "_", candidate["operator"]).strip("_")
    path = EVIDENCE_ROOT / f"{target['competition_id']}__{safe_operator}__{token}.json"
    payload = {
        "schema_version": "V5.5.8-direct-kambi-probe-raw-envelope-r1",
        "operator": candidate["operator"],
        "provider_group": "kambi",
        "request": request_meta,
        "target": target,
        "event_wrapper_sha256": digest,
        "event_wrapper": wrapper,
        "formal_evidence": False,
        "research_probe_only": True,
        "independence_policy": "All Kambi-powered operator endpoints remain one provider_group=kambi until operator-specific independent pricing is separately proven.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.8-direct-kambi-pit-probe-r1",
        "generated_at_utc": now_utc(),
        "status": "NO_REACHABLE_PUBLIC_KAMBI_JSON",
        "provider_group": "kambi",
        "candidate_count": len(CANDIDATES),
        "candidates": [],
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": {
            "different_kambi_operator_frontends_count_as_independent_providers": False,
            "historical_endpoint_documentation_is_not_current_evidence": True,
            "raw_reachability_is_not_formal_market_evidence": True,
            "v523_required_before_formal_snapshot": True,
        },
    }

    reachable = 0
    matched_total = 0
    frozen_total = 0
    for candidate in CANDIDATES:
        observed = now_utc()
        result: dict = {
            "operator": candidate["operator"],
            "provider_group": "kambi",
            "endpoint": candidate["url"],
            "evidence_basis": candidate["evidence_basis"],
            "observed_at_utc": observed,
            "status": "FAIL",
            "targets": [],
        }
        try:
            data, raw, url, http_status, content_type = fetch(candidate)
            events = data.get("events")
            if not isinstance(events, list):
                raise ValueError("response.events is not a list")
            reachable += 1
            result.update({
                "status": "PASS_JSON_STRUCTURE",
                "http_status": http_status,
                "content_type": content_type,
                "request_url": url,
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "event_count": len(events),
            })
            for target in TARGETS:
                wrapper, meta = best_match(events, target)
                target_result = {"target": target, "matched": bool(wrapper), "match_meta": meta, "formal_snapshot_eligible": False}
                if wrapper:
                    matched_total += 1
                    request_meta = {"url": url, "http_status": http_status, "content_type": content_type, "observed_at_utc": observed}
                    path, digest = write_raw(candidate, target, wrapper, request_meta)
                    frozen_total += 1
                    target_result.update({
                        "raw_evidence_path": path,
                        "event_wrapper_sha256": digest,
                        "market_summary": market_summary(wrapper),
                        "market_mapping_status": "LABELS_EXPOSED_FORMAL_MAPPING_NOT_VALIDATED",
                        "reason": "Direct Kambi event wrapper frozen. Exact 1X2/AH/OU extraction and completeness must be regression-tested before V5.2.3 ingestion.",
                    })
                result["targets"].append(target_result)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        manifest["candidates"].append(result)

    manifest["reachable_candidate_count"] = reachable
    manifest["matched_target_count_across_candidates"] = matched_total
    manifest["frozen_event_wrapper_count"] = frozen_total
    if frozen_total:
        manifest["status"] = "PASS_DIRECT_KAMBI_EVENTS_FROZEN_MAPPING_REQUIRED"
    elif reachable:
        manifest["status"] = "DIRECT_KAMBI_JSON_REACHABLE_TARGETS_NOT_MATCHED"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
