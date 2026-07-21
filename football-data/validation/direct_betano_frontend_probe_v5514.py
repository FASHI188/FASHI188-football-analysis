#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "direct_betano_frontend_probe_v5514_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "betano"
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.14; +https://github.com/FASHI188/FASHI188-football-analysis)"

# No proxy, VPN, credential, cookie injection, CAPTCHA bypass or geo-evasion is permitted.
# These are low-frequency reads of first-party frontend endpoints only.
CANDIDATES = [
    {
        "label": "betano_global_football_en",
        "base": "https://www.betano.com",
        "listing_path": "/api/sport/football?req=la,s,stnf,c,mb",
    },
    {
        "label": "betano_global_futebol_pt",
        "base": "https://www.betano.com",
        "listing_path": "/api/sport/futebol?req=la,s,stnf,c,mb",
    },
    {
        "label": "betano_mobile_football_en",
        "base": "https://m.betano.com",
        "listing_path": "/api/sport/football?req=la,s,stnf,c,mb",
    },
    {
        "label": "betano_mobile_futebol_pt",
        "base": "https://m.betano.com",
        "listing_path": "/api/sport/futebol?req=la,s,stnf,c,mb",
    },
    {
        "label": "betano_brazil_current_official",
        "base": "https://betano.bet.br",
        "listing_path": "/api/sport/futebol?req=la,s,stnf,c,mb",
    },
]


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def fetch(url: str, timeout: int = 30) -> tuple[object, bytes, int, str, str]:
    observed = now_utc()
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed first-party Betano endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        content_type = str(resp.headers.get("Content-Type") or "")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    return data, raw, status, content_type, observed


def find_events(data: object) -> list[dict]:
    if not isinstance(data, dict):
        return []
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    found: list[dict] = []
    blocks = root.get("blocks") if isinstance(root, dict) else None
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            events = block.get("events")
            if isinstance(events, list):
                found.extend(x for x in events if isinstance(x, dict))
    # Some deployments may return events directly or nested in coupons.
    direct = root.get("events") if isinstance(root, dict) else None
    if isinstance(direct, list):
        found.extend(x for x in direct if isinstance(x, dict))
    dedup: dict[str, dict] = {}
    for event in found:
        key = str(event.get("id") or event.get("eventId") or event.get("url") or id(event))
        dedup[key] = event
    return list(dedup.values())


def listing_structure(data: object) -> dict:
    if not isinstance(data, dict):
        return {"json_type": type(data).__name__, "dict_keys": []}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    result = {
        "json_type": "dict",
        "top_level_keys": sorted(str(k) for k in data.keys()),
        "data_keys": sorted(str(k) for k in root.keys()) if isinstance(root, dict) else [],
        "event_count": len(find_events(data)),
    }
    if isinstance(root, dict) and isinstance(root.get("regionGroups"), list):
        result["region_group_count"] = len(root["regionGroups"])
    if isinstance(root, dict) and isinstance(root.get("blocks"), list):
        result["block_count"] = len(root["blocks"])
    return result


def candidate_detail_url(base: str, event: dict) -> str | None:
    path = event.get("url")
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    # Historical first-party frontend semantics: detail is /api + event['url'].
    # This is only attempted when the listing itself returned that event URL.
    suffix = "&" if "?" in path else "?"
    return f"{base}/api{path}{suffix}req=la,t,l,s,stnf,c,mb,mbl"


def summarize_detail(data: object) -> dict:
    if not isinstance(data, dict):
        return {"json_type": type(data).__name__}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    event = root.get("event") if isinstance(root, dict) and isinstance(root.get("event"), dict) else None
    markets = event.get("markets") if isinstance(event, dict) and isinstance(event.get("markets"), list) else []
    market_names = []
    selection_counts = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        if market.get("name") is not None:
            market_names.append(str(market.get("name")))
        selections = market.get("selections") if isinstance(market.get("selections"), list) else []
        selection_counts.append(len(selections))
    return {
        "json_type": "dict",
        "has_event": isinstance(event, dict),
        "market_count": len(markets),
        "market_names_sample": market_names[:40],
        "selection_count_sample": selection_counts[:40],
    }


def write_sample(label: str, observed: str, kind: str, data: object) -> tuple[str, str]:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    path = EVIDENCE_ROOT / f"{safe(label)}__{kind}__{token}__{digest[:12]}.json"
    envelope = {
        "schema_version": "V5.5.14-betano-frontend-probe-sample-r1",
        "provider_name": "Betano",
        "provider_group": "kaizen_betano",
        "candidate_label": label,
        "observed_at_utc": observed,
        "kind": kind,
        "payload_sha256": digest,
        "payload": data,
        "formal_evidence": False,
        "research_probe_only": True,
        "access_policy": "Anonymous direct request only; no proxy, geo-evasion, credential, CAPTCHA bypass or anti-bot circumvention.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.14-direct-betano-frontend-probe-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "Betano",
        "provider_group": "kaizen_betano",
        "status": "NO_VALID_FRONTEND_JSON",
        "candidate_count": len(CANDIDATES),
        "candidates": [],
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "consensus_change": False,
        "policy": "Low-frequency first-party frontend capability probe only. Stop on blocking/invalid JSON. Do not use proxies or bypass geo/access controls. Even valid JSON remains research-only until exact fixture identity, synchronized Full Time 1X2/AH/OU mapping and V5.2.3 validation pass.",
    }

    valid_listing = 0
    valid_detail = 0
    for candidate in CANDIDATES:
        url = candidate["base"] + candidate["listing_path"]
        row: dict = {
            "label": candidate["label"],
            "listing_url": url,
            "status": "FAIL",
        }
        try:
            data, raw, http_status, content_type, observed = fetch(url)
            structure = listing_structure(data)
            events = find_events(data)
            if not isinstance(data, dict):
                raise ValueError("listing JSON is not an object")
            valid_listing += 1
            sample_path, digest = write_sample(candidate["label"], observed, "listing", data)
            row.update({
                "status": "PASS_LISTING_JSON",
                "observed_at_utc": observed,
                "http_status": http_status,
                "content_type": content_type,
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "listing_structure": structure,
                "listing_sample_path": sample_path,
                "listing_payload_sha256": digest,
            })
            if events:
                first = events[0]
                row["first_event_summary"] = {
                    "id": first.get("id") or first.get("eventId"),
                    "shortName": first.get("shortName"),
                    "startTime": first.get("startTime"),
                    "url": first.get("url"),
                }
                detail_url = candidate_detail_url(candidate["base"], first)
                if detail_url:
                    try:
                        detail, detail_raw, detail_status, detail_content_type, detail_observed = fetch(detail_url)
                        detail_structure = summarize_detail(detail)
                        detail_path, detail_digest = write_sample(candidate["label"], detail_observed, "detail", detail)
                        valid_detail += 1
                        row["detail_probe"] = {
                            "status": "PASS_DETAIL_JSON",
                            "url": detail_url,
                            "observed_at_utc": detail_observed,
                            "http_status": detail_status,
                            "content_type": detail_content_type,
                            "raw_response_sha256": hashlib.sha256(detail_raw).hexdigest(),
                            "payload_sha256": detail_digest,
                            "sample_path": detail_path,
                            "structure": detail_structure,
                        }
                    except Exception as exc:
                        row["detail_probe"] = {"status": "FAIL", "url": detail_url, "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        manifest["candidates"].append(row)

    manifest["valid_listing_json_count"] = valid_listing
    manifest["valid_detail_json_count"] = valid_detail
    if valid_detail:
        manifest["status"] = "PASS_BETANO_DETAIL_JSON_REACHABLE_MAPPING_REQUIRED"
    elif valid_listing:
        manifest["status"] = "PASS_BETANO_LISTING_JSON_REACHABLE_DETAIL_NOT_VALIDATED"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
