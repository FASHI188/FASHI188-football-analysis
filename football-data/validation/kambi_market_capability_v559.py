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
MANIFEST = ROOT / "manifests" / "kambi_market_capability_v559_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "kambi" / "capability_samples"
ENDPOINT = "https://eu-offering-api.kambicdn.com/offering/v2018/betcitynl/listView/football.json"
PARAMS = {
    "lang": "nl_NL",
    "market": "NL",
    "client_id": 2,
    "channel_id": 1,
    "useCombined": "true",
    "useCombinedLive": "true",
}
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.9; +https://github.com/FASHI188/FASHI188-football-analysis)"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


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
        raise ValueError("Kambi response lacks events list")
    return data, raw, url, status, content_type, observed


def offer_text(offer: dict) -> str:
    criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
    offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
    parts = [
        criterion.get("label"),
        criterion.get("englishLabel"),
        offer_type.get("name"),
    ]
    return " | ".join(str(x) for x in parts if x)


def outcomes(offer: dict) -> list[dict]:
    value = offer.get("outcomes")
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def outcome_tokens(offer: dict) -> set[str]:
    toks: set[str] = set()
    for outcome in outcomes(offer):
        for field in ("type", "label", "englishLabel"):
            value = outcome.get(field)
            if value is not None:
                toks.add(norm(value))
    return toks


def has_line(offer: dict) -> bool:
    return any(o.get("line") is not None for o in outcomes(offer))


def is_1x2(offer: dict) -> bool:
    outs = outcomes(offer)
    if len(outs) != 3:
        return False
    text = norm(offer_text(offer))
    toks = outcome_tokens(offer)
    canonical = {"1", "x", "2"}.issubset(toks)
    home_draw_away = any("draw" in t or "gelijk" in t for t in toks) and len(toks) >= 3
    semantic = any(key in text for key in ("match odds", "match result", "full time result", "wedstrijdresultaat", "1x2"))
    return canonical or home_draw_away or semantic


def is_total(offer: dict) -> bool:
    outs = outcomes(offer)
    if len(outs) != 2 or not has_line(offer):
        return False
    text = norm(offer_text(offer))
    toks = outcome_tokens(offer)
    over_under = any(t in {"over", "under", "o", "u"} or "over" in t or "under" in t for t in toks)
    semantic = any(key in text for key in ("total goals", "over under", "totaal", "goals over under"))
    return over_under or semantic


def is_asian_handicap(offer: dict) -> bool:
    outs = outcomes(offer)
    if len(outs) != 2 or not has_line(offer):
        return False
    text = norm(offer_text(offer))
    return "asian handicap" in text or "aziatische handicap" in text


def summarize_offer(offer: dict) -> dict:
    criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
    offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
    return {
        "id": offer.get("id"),
        "criterion_label": criterion.get("label"),
        "criterion_english_label": criterion.get("englishLabel"),
        "bet_offer_type_name": offer_type.get("name"),
        "bet_offer_type_id": offer_type.get("id"),
        "outcome_count": len(outcomes(offer)),
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


def event_meta(wrapper: dict) -> dict:
    event = wrapper.get("event") if isinstance(wrapper.get("event"), dict) else {}
    return {
        "id": event.get("id"),
        "name": event.get("name"),
        "homeName": event.get("homeName"),
        "awayName": event.get("awayName"),
        "start": event.get("start"),
        "group": event.get("group"),
        "groupId": event.get("groupId"),
        "path": event.get("path"),
    }


def write_sample(wrapper: dict, observed: str) -> tuple[str, str]:
    payload_bytes = json.dumps(wrapper, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload_bytes).hexdigest()
    event_id = event_meta(wrapper).get("id") or "unknown"
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = EVIDENCE_ROOT / f"event_{event_id}__{token}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": "V5.5.9-kambi-market-capability-sample-r1",
        "observed_at_utc": observed,
        "operator": "BetCity NL",
        "provider_group": "kambi",
        "event_wrapper_sha256": digest,
        "event_wrapper": wrapper,
        "formal_evidence": False,
        "research_probe_only": True,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.9-kambi-market-capability-r1",
        "generated_at_utc": now_utc(),
        "operator": "BetCity NL",
        "provider_group": "kambi",
        "status": "BLOCKED",
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Capability diagnostics only. Structural market recognition does not authorize formal PIT ingestion until exact mapping, two-sided completeness, identity and timestamp gates pass V5.2.3.",
    }

    try:
        data, raw, url, http_status, content_type, observed = fetch()
        events = [x for x in data.get("events", []) if isinstance(x, dict)]
        criterion_labels: Counter[str] = Counter()
        english_labels: Counter[str] = Counter()
        type_names: Counter[str] = Counter()
        outcome_types: Counter[str] = Counter()
        one_x_two_events = 0
        total_events = 0
        asian_events = 0
        all_three_events = 0
        samples: list[dict] = []

        for wrapper in events:
            offers = [x for x in wrapper.get("betOffers", []) if isinstance(x, dict)] if isinstance(wrapper.get("betOffers"), list) else []
            has_1x2 = False
            has_total_market = False
            has_asian = False
            classified: dict[str, list[dict]] = {"one_x_two": [], "asian_handicap": [], "over_under": []}
            for offer in offers:
                criterion = offer.get("criterion") if isinstance(offer.get("criterion"), dict) else {}
                offer_type = offer.get("betOfferType") if isinstance(offer.get("betOfferType"), dict) else {}
                if criterion.get("label"):
                    criterion_labels[str(criterion.get("label"))] += 1
                if criterion.get("englishLabel"):
                    english_labels[str(criterion.get("englishLabel"))] += 1
                if offer_type.get("name"):
                    type_names[str(offer_type.get("name"))] += 1
                for o in outcomes(offer):
                    if o.get("type") is not None:
                        outcome_types[str(o.get("type"))] += 1
                if is_1x2(offer):
                    has_1x2 = True
                    if len(classified["one_x_two"]) < 2:
                        classified["one_x_two"].append(summarize_offer(offer))
                if is_total(offer):
                    has_total_market = True
                    if len(classified["over_under"]) < 4:
                        classified["over_under"].append(summarize_offer(offer))
                if is_asian_handicap(offer):
                    has_asian = True
                    if len(classified["asian_handicap"]) < 4:
                        classified["asian_handicap"].append(summarize_offer(offer))

            one_x_two_events += int(has_1x2)
            total_events += int(has_total_market)
            asian_events += int(has_asian)
            if has_1x2 and has_total_market and has_asian:
                all_three_events += 1
                if len(samples) < 5:
                    path, digest = write_sample(wrapper, observed)
                    samples.append({
                        "event": event_meta(wrapper),
                        "raw_sample_path": path,
                        "event_wrapper_sha256": digest,
                        "classified_markets": classified,
                    })

        manifest.update({
            "status": "PASS_CAPABILITY_SCAN",
            "observed_at_utc": observed,
            "request_url": url,
            "http_status": http_status,
            "content_type": content_type,
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
            "event_count": len(events),
            "events_with_recognized_1x2": one_x_two_events,
            "events_with_recognized_over_under": total_events,
            "events_with_recognized_asian_handicap": asian_events,
            "events_with_all_three_recognized": all_three_events,
            "top_criterion_labels": criterion_labels.most_common(40),
            "top_criterion_english_labels": english_labels.most_common(40),
            "top_bet_offer_type_names": type_names.most_common(30),
            "top_outcome_types": outcome_types.most_common(30),
            "full_surface_samples": samples,
            "next_gate": "If all-three samples exist, manually audit the exact Kambi field semantics and add regression tests before wiring target fixtures into V5.2.3. If Asian count is zero, inspect labels/types for alternate Kambi handicap encoding rather than guessing.",
        })
    except Exception as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
