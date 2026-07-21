#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "kambi_event_detail_capability_v5510_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "event_detail_samples"
LIST_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
BASE_PREFIX = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl"
COMMON = {
    "lang": "nl_NL",
    "market": "NL",
    "client_id": 2,
    "channel_id": 1,
    "useCombined": "true",
}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.10; +https://github.com/FASHI188/FASHI188-football-analysis)"
MAX_EVENT_DETAIL_REQUESTS = 24


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def get_json(url: str, params: dict[str, object], timeout: int = 30) -> tuple[dict, bytes, str, int, str]:
    query = dict(params)
    query["ncid"] = int(time.time() * 1000)
    full_url = f"{url}?{urlencode(query)}"
    req = Request(full_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed public Kambi endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response is not JSON object")
    return data, raw, full_url, status, content_type


def event_info(wrapper: dict) -> dict:
    value = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else wrapper
    return value if isinstance(value, dict) else {}


def outcomes(offer: dict) -> list[dict]:
    value = offer.get("outcomes")
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def offer_text(offer: dict) -> str:
    criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
    offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
    return " | ".join(str(x) for x in (criterion.get("label"), criterion.get("englishLabel"), offer_type.get("name")) if x)


def has_line(offer: dict) -> bool:
    return any(o.get("line") is not None for o in outcomes(offer))


def is_1x2(offer: dict) -> bool:
    outs = outcomes(offer)
    types = {str(o.get("type") or "") for o in outs}
    text = norm(offer_text(offer))
    return len(outs) == 3 and ({"OT_ONE", "OT_CROSS", "OT_TWO"}.issubset(types) or "full time" in text or "wedstrijd" in text)


def is_total(offer: dict) -> bool:
    outs = outcomes(offer)
    types = {str(o.get("type") or "") for o in outs}
    text = norm(offer_text(offer))
    return len(outs) == 2 and has_line(offer) and ({"OT_OVER", "OT_UNDER"}.issubset(types) or "total goals" in text or "over onder" in text)


def is_asian_handicap(offer: dict) -> bool:
    outs = outcomes(offer)
    text = norm(offer_text(offer))
    types = {str(o.get("type") or "") for o in outs}
    explicit = "asian handicap" in text or "aziatische handicap" in text
    structural = len(outs) == 2 and has_line(offer) and any("HANDICAP" in t.upper() for t in types)
    return len(outs) == 2 and has_line(offer) and (explicit or structural)


def summarize_offer(offer: dict) -> dict:
    criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
    offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
    return {
        "id": offer.get("id"),
        "eventId": offer.get("eventId"),
        "criterion_label": criterion.get("label"),
        "criterion_english_label": criterion.get("englishLabel"),
        "bet_offer_type_name": offer_type.get("name"),
        "bet_offer_type_id": offer_type.get("id"),
        "outcomes": [
            {
                "id": o.get("id"),
                "label": o.get("label"),
                "englishLabel": o.get("englishLabel"),
                "type": o.get("type"),
                "line": o.get("line"),
                "odds": o.get("odds"),
                "status": o.get("status"),
            }
            for o in outcomes(offer)
        ],
    }


def pick_event_ids(events: list[dict]) -> list[tuple[int, dict]]:
    picked: list[tuple[int, dict]] = []
    seen: set[int] = set()
    for wrapper in events:
        if not isinstance(wrapper, dict):
            continue
        event = event_info(wrapper)
        raw_id = event.get("id")
        try:
            event_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if event_id in seen:
            continue
        seen.add(event_id)
        picked.append((event_id, {
            "id": event_id,
            "name": event.get("name"),
            "homeName": event.get("homeName"),
            "awayName": event.get("awayName"),
            "start": event.get("start"),
            "state": event.get("state"),
            "liveBetOffers": event.get("liveBetOffers"),
        }))
        if len(picked) >= MAX_EVENT_DETAIL_REQUESTS:
            break
    return picked


def write_sample(event_id: int, payload: dict, observed: str) -> tuple[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = EVIDENCE_ROOT / f"event_{event_id}__{token}.json"
    envelope = {
        "schema_version": "V5.5.10-kambi-event-detail-sample-r1",
        "observed_at_utc": observed,
        "operator": "BetCity NL",
        "provider_group": "kambi",
        "event_id": event_id,
        "payload_sha256": digest,
        "payload": payload,
        "formal_evidence": False,
        "research_probe_only": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.10-kambi-event-detail-capability-r1",
        "generated_at_utc": now_utc(),
        "operator": "BetCity NL",
        "provider_group": "kambi",
        "status": "BLOCKED",
        "max_event_detail_requests": MAX_EVENT_DETAIL_REQUESTS,
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Research capability probe only. Event-detail market presence is not formal PIT eligibility. Exact market mapping, complete two-sided prices, event identity and synchronized timestamp must pass V5.2.3 before ingestion.",
    }

    try:
        list_data, list_raw, list_request_url, list_http_status, list_content_type = get_json(
            LIST_URL,
            {**COMMON, "useCombinedLive": "true"},
        )
        events = [x for x in list_data.get("events", []) if isinstance(x, dict)]
        if not events:
            raise ValueError("listView returned no events")

        request_results: list[dict] = []
        label_counts: Counter[str] = Counter()
        english_counts: Counter[str] = Counter()
        type_name_counts: Counter[str] = Counter()
        outcome_type_counts: Counter[str] = Counter()
        recognized_1x2 = 0
        recognized_total = 0
        recognized_asian = 0
        all_three = 0
        full_samples: list[dict] = []

        for event_id, list_meta in pick_event_ids(events):
            detail_url = f"{BASE_PREFIX}/betoffer/event/{event_id}.json"
            observed = now_utc()
            row: dict = {"event_id": event_id, "list_meta": list_meta, "observed_at_utc": observed, "status": "FAIL"}
            try:
                detail, raw, request_url, http_status, content_type = get_json(
                    detail_url,
                    {**COMMON, "includeParticipants": "true", "range_start": 0, "range_size": 0},
                )
                offers = [x for x in detail.get("betOffers", []) if isinstance(x, dict)]
                has_1x2 = any(is_1x2(o) for o in offers)
                has_total_market = any(is_total(o) for o in offers)
                has_asian = any(is_asian_handicap(o) for o in offers)
                recognized_1x2 += int(has_1x2)
                recognized_total += int(has_total_market)
                recognized_asian += int(has_asian)
                all_three += int(has_1x2 and has_total_market and has_asian)

                for offer in offers:
                    criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
                    offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
                    if criterion.get("label"):
                        label_counts[str(criterion.get("label"))] += 1
                    if criterion.get("englishLabel"):
                        english_counts[str(criterion.get("englishLabel"))] += 1
                    if offer_type.get("name"):
                        type_name_counts[str(offer_type.get("name"))] += 1
                    for outcome in outcomes(offer):
                        if outcome.get("type") is not None:
                            outcome_type_counts[str(outcome.get("type"))] += 1

                row.update({
                    "status": "PASS",
                    "request_url": request_url,
                    "http_status": http_status,
                    "content_type": content_type,
                    "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                    "bet_offer_count": len(offers),
                    "recognized_1x2": has_1x2,
                    "recognized_over_under": has_total_market,
                    "recognized_asian_handicap": has_asian,
                })
                if has_1x2 and has_total_market and has_asian and len(full_samples) < 5:
                    path, digest = write_sample(event_id, detail, observed)
                    full_samples.append({
                        "event_id": event_id,
                        "list_meta": list_meta,
                        "sample_path": path,
                        "payload_sha256": digest,
                        "one_x_two": [summarize_offer(o) for o in offers if is_1x2(o)][:3],
                        "asian_handicap": [summarize_offer(o) for o in offers if is_asian_handicap(o)][:8],
                        "over_under": [summarize_offer(o) for o in offers if is_total(o)][:8],
                    })
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            request_results.append(row)

        manifest.update({
            "status": "PASS_EVENT_DETAIL_SCAN" if request_results else "NO_EVENT_DETAILS_TESTED",
            "list_view": {
                "request_url": list_request_url,
                "http_status": list_http_status,
                "content_type": list_content_type,
                "raw_response_sha256": hashlib.sha256(list_raw).hexdigest(),
                "event_count": len(events),
            },
            "event_detail_requests": request_results,
            "successful_event_detail_count": sum(1 for r in request_results if r.get("status") == "PASS"),
            "events_with_recognized_1x2": recognized_1x2,
            "events_with_recognized_over_under": recognized_total,
            "events_with_recognized_asian_handicap": recognized_asian,
            "events_with_all_three_recognized": all_three,
            "top_criterion_labels": label_counts.most_common(60),
            "top_criterion_english_labels": english_counts.most_common(60),
            "top_bet_offer_type_names": type_name_counts.most_common(40),
            "top_outcome_types": outcome_type_counts.most_common(40),
            "full_surface_samples": full_samples,
            "next_gate": "If Asian handicap is recognized, audit exact line sign, outcome/team mapping and integer-odds normalization on frozen samples before adding a V5.2.3 Kambi adapter. If zero, inspect labels and outcome types before changing classifier; never infer AH from generic three-way handicap.",
        })
    except Exception as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
