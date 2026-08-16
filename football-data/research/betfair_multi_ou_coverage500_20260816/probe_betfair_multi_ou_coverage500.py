#!/usr/bin/env python3
"""Zero-label Betfair Exchange multi-OU coverage probe, readonly web-API edition.

Research purpose only. No login, no account token, no wagering, no paid/historical
API, no settlement labels, no model fit, no formal asset mutation.

V1 attempted public HTML from GitHub Actions and was blocked by HTTP 403 before any
coverage observation. V2 keeps the preregistered 500-event coverage gates unchanged
and uses Betfair's public read-only web APIs used by the exchange frontend:
  scan-inbf.betfair.com.au / navigation/v2/graph/bynode
  ero.betfair.com.au       / exchange/readonly/v1/byevent
  ero.betfair.com.au       / exchange/readonly/v1/bymarket
The `_ak` value below is the public web-app key, not a user credential.

Selection is result-blind: discover soccer EVENT nodes, resolve event openDate and
market structure, keep only future events, sort by (openDate,eventId), then take the
first target=500. Market coverage cannot affect sample membership.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import json
import math
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

AK = "nzIFcwyWhrlwYMrh"  # Betfair public web key; not a user/account secret.
NAV_URL = "https://scan-inbf.betfair.com.au/www/sports/navigation/v2/graph/bynode"
BYEVENT_URL = "https://ero.betfair.com.au/www/sports/exchange/readonly/v1/byevent"
BYMARKET_URL = "https://ero.betfair.com.au/www/sports/exchange/readonly/v1/bymarket"
LINES = ("1.5", "2.5", "3.5", "4.5")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/142.0 Safari/537.36"
CTX = ssl.create_default_context()
DEFAULT_TYPES = (
    "MARKET_STATE,MARKET_RATES,MARKET_DESCRIPTION,EVENT,RUNNER_DESCRIPTION,"
    "RUNNER_STATE,RUNNER_EXCHANGE_PRICES_BEST,RUNNER_METADATA,MARKET_LINE_RANGE_INFO"
)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    if not s:
        return None
    try:
        x = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if x.tzinfo is None:
        x = x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)


def get_json(base: str, params: dict[str, Any], timeout: float = 30.0, retries: int = 4) -> tuple[Any | None, dict[str, Any]]:
    encoded = {}
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            encoded[k] = ",".join(str(x) for x in v)
        else:
            encoded[k] = str(v)
    url = base + "?" + urllib.parse.urlencode(encoded, safe=",:")
    last: dict[str, Any] = {}
    for attempt in range(retries + 1):
        observed = now()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-AU,en;q=0.9",
                "Origin": "https://www.betfair.com.au",
                "Referer": "https://www.betfair.com.au/",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                raw = r.read()
                meta = {
                    "ok": True,
                    "http_status": int(getattr(r, "status", 200)),
                    "observed_at_utc": observed.isoformat(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "url_host": urllib.parse.urlsplit(url).netloc,
                }
                return json.loads(raw.decode("utf-8")), meta
        except urllib.error.HTTPError as e:
            last = {"ok": False, "error": f"HTTPError:{e.code}", "observed_at_utc": observed.isoformat(), "url_host": urllib.parse.urlsplit(url).netloc}
            if e.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                return None, last
            time.sleep((0.65 * (2 ** attempt)) + random.random() * 0.2)
        except Exception as e:
            last = {"ok": False, "error": f"{type(e).__name__}:{e}", "observed_at_utc": observed.isoformat(), "url_host": urllib.parse.urlsplit(url).netloc}
            if attempt >= retries:
                return None, last
            time.sleep((0.65 * (2 ** attempt)) + random.random() * 0.2)
    return None, last


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for x in value.values():
            yield from iter_dicts(x)
    elif isinstance(value, list):
        for x in value:
            yield from iter_dicts(x)


def all_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                out.append(k)
            out.extend(all_strings(v))
    elif isinstance(value, list):
        for x in value:
            out.extend(all_strings(x))
    return out


def node_type(node: dict[str, Any]) -> str:
    return str(node.get("nodeType") or node.get("type") or "").upper()


def node_id(node: dict[str, Any]) -> str:
    return str(node.get("nodeId") or node.get("id") or "")


def discover_event_ids(max_results: int) -> tuple[list[str], dict[str, Any]]:
    payload, meta = get_json(
        NAV_URL,
        {
            "nodeIds": ["EVENT_TYPE:1"],
            "attachments": ["MENU", "EVENT", "MARKET"],
            "maxInDistance": 0,
            "maxOutDistance": 4,
            "maxResults": max_results,
            "currencyCode": "AUD",
            "locale": "en",
            "_ak": AK,
            "alt": "json",
        },
        timeout=45,
    )
    if payload is None:
        return [], {"navigation_fetch": meta, "node_count": 0, "event_node_count": 0}
    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    ids: set[str] = set()
    for n in nodes if isinstance(nodes, list) else []:
        if not isinstance(n, dict) or node_type(n) != "EVENT":
            continue
        nid = node_id(n)
        if nid.upper().startswith("EVENT:"):
            eid = nid.split(":", 1)[1]
        else:
            eid = str(n.get("eventId") or nid)
        if eid.isdigit():
            ids.add(eid)
    return sorted(ids, key=lambda x: int(x)), {
        "navigation_fetch": meta,
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "event_node_count": len(ids),
    }


def event_nodes_from_byevent(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for et in payload.get("eventTypes", []) or []:
        if isinstance(et, dict):
            for ev in et.get("eventNodes", []) or []:
                if isinstance(ev, dict):
                    out.append(ev)
    return out


def event_identity(ev: dict[str, Any], fallback_id: str) -> tuple[str, str, str | None, str | None]:
    event = ev.get("event") if isinstance(ev.get("event"), dict) else {}
    eid = str(ev.get("eventId") or event.get("eventId") or fallback_id)
    name = str(event.get("eventName") or ev.get("eventName") or ev.get("name") or "").strip()
    country = str(event.get("countryCode") or ev.get("countryCode") or "").strip() or None
    open_date = event.get("openDate") or ev.get("openDate") or event.get("startTime") or ev.get("startTime")
    return eid, name, str(open_date) if open_date else None, country


def market_nodes(ev: dict[str, Any]) -> list[dict[str, Any]]:
    xs = ev.get("marketNodes") or []
    return [x for x in xs if isinstance(x, dict)] if isinstance(xs, list) else []


def market_id(m: dict[str, Any]) -> str:
    return str(m.get("marketId") or m.get("id") or "")


def market_signature(m: dict[str, Any]) -> str:
    return " | ".join(all_strings(m)).lower()


def classify_market(m: dict[str, Any]) -> str | None:
    s = market_signature(m)
    compact = s.replace("_", " ").replace("-", " ")
    if "match odds" in compact or "match odds" in s or "match_odds" in s:
        return "MATCH_ODDS"
    for line in LINES:
        digits = line.replace(".", "")
        tests = (
            f"over/under {line}", f"over under {line}", f"over under {line} goals",
            f"over_under_{digits}", f"over under {digits}", f"overunder{digits}",
        )
        if any(t in s or t in compact for t in tests):
            return f"OU_{line}"
    return None


def resolve_event(eid: str) -> dict[str, Any]:
    payload, meta = get_json(
        BYEVENT_URL,
        {
            "eventIds": [eid],
            "types": DEFAULT_TYPES,
            "currencyCode": "AUD",
            "locale": "en",
            "rollupLimit": 25,
            "rollupModel": "STAKE",
            "_ak": AK,
            "alt": "json",
        },
        timeout=35,
    )
    row: dict[str, Any] = {"event_id": eid, "structure_fetch": meta}
    if payload is None:
        row["structure_status"] = "FETCH_FAIL"
        return row
    evs = event_nodes_from_byevent(payload)
    if not evs:
        row["structure_status"] = "NO_EVENT_NODE"
        return row
    ev = next((x for x in evs if str(x.get("eventId") or "") == eid), evs[0])
    rid, name, open_date, country = event_identity(ev, eid)
    row.update({"event_id": rid, "event_name": name, "open_date_utc": open_date, "country_code": country})
    selected: dict[str, str] = {}
    type_counts = Counter()
    for m in market_nodes(ev):
        key = classify_market(m)
        if key:
            mid = market_id(m)
            if mid:
                selected.setdefault(key, mid)
                type_counts[key] += 1
    row["market_ids"] = selected
    row["market_match_counts"] = dict(type_counts)
    row["structure_market_count"] = len(market_nodes(ev))
    row["structure_status"] = "PASS"
    return row


def best_prices_from_market(m: dict[str, Any]) -> dict[str, Any]:
    # The API shape can vary slightly; collect price/size pairs anywhere inside each runner.
    runners = m.get("runners") if isinstance(m.get("runners"), list) else []
    runner_summaries = []
    any_back = any_lay = False
    for r in runners:
        if not isinstance(r, dict):
            continue
        rstrings = all_strings(r)
        name = str(r.get("runnerName") or r.get("name") or (r.get("description") or {}).get("runnerName") if isinstance(r.get("description"), dict) else "")
        backs: list[float] = []
        lays: list[float] = []
        for d in iter_dicts(r):
            for key, value in d.items():
                lk = str(key).lower()
                if lk in {"back", "availabletoback", "bestback"} and isinstance(value, list):
                    for q in value:
                        if isinstance(q, dict):
                            p = q.get("price") or q.get("odds")
                            if isinstance(p, (int, float)) and math.isfinite(float(p)):
                                backs.append(float(p))
                if lk in {"lay", "availabletolay", "bestlay"} and isinstance(value, list):
                    for q in value:
                        if isinstance(q, dict):
                            p = q.get("price") or q.get("odds")
                            if isinstance(p, (int, float)) and math.isfinite(float(p)):
                                lays.append(float(p))
        any_back = any_back or bool(backs)
        any_lay = any_lay or bool(lays)
        runner_summaries.append({
            "name": name or None,
            "best_back": max(backs) if backs else None,
            "best_lay": min(lays) if lays else None,
            "back_prices_found": len(backs),
            "lay_prices_found": len(lays),
        })
    return {"runner_count": len(runners), "has_back": any_back, "has_lay": any_lay, "runners": runner_summaries}


def price_event(row: dict[str, Any]) -> dict[str, Any]:
    mids = [str(x) for x in (row.get("market_ids") or {}).values() if x]
    out = dict(row)
    if not mids:
        out["price_status"] = "NO_SELECTED_MARKETS"
        out["prices"] = {}
        return out
    payload, meta = get_json(
        BYMARKET_URL,
        {
            "marketIds": mids,
            "types": DEFAULT_TYPES,
            "currencyCode": "AUD",
            "locale": "en",
            "rollupLimit": 25,
            "rollupModel": "STAKE",
            "_ak": AK,
            "alt": "json",
        },
        timeout=35,
    )
    out["price_fetch"] = meta
    if payload is None:
        out["price_status"] = "FETCH_FAIL"
        out["prices"] = {}
        return out
    market_map: dict[str, dict[str, Any]] = {}
    for d in iter_dicts(payload):
        mid = d.get("marketId") if isinstance(d, dict) else None
        if mid and isinstance(d.get("runners"), list):
            market_map[str(mid)] = d
    prices = {}
    for key, mid in (out.get("market_ids") or {}).items():
        m = market_map.get(str(mid))
        prices[key] = best_prices_from_market(m) if m else {"runner_count": 0, "has_back": False, "has_lay": False, "runners": []}
    out["prices"] = prices
    out["price_status"] = "PASS"
    return out


def rate(n: int, d: int) -> float | None:
    return n / d if d else None


def future(row: dict[str, Any], floor: dt.datetime) -> bool:
    d = iso_datetime(row.get("open_date_utc"))
    return bool(d and d > floor + dt.timedelta(minutes=5))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "sample_index", "event_id", "event_name", "country_code", "open_date_utc",
        "structure_observed_at_utc", "price_observed_at_utc", "match_odds_present",
        "ou_1_5", "ou_2_5", "ou_3_5", "ou_4_5", "all_four_ou_present",
        "match_odds_plus_four_ou", "all_four_priceable_back_lay", "labels_read",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, r in enumerate(rows):
            mids = r.get("market_ids") or {}
            prices = r.get("prices") or {}
            w.writerow({
                "sample_index": i,
                "event_id": r.get("event_id"),
                "event_name": r.get("event_name"),
                "country_code": r.get("country_code"),
                "open_date_utc": r.get("open_date_utc"),
                "structure_observed_at_utc": (r.get("structure_fetch") or {}).get("observed_at_utc"),
                "price_observed_at_utc": (r.get("price_fetch") or {}).get("observed_at_utc"),
                "match_odds_present": "MATCH_ODDS" in mids,
                "ou_1_5": "OU_1.5" in mids,
                "ou_2_5": "OU_2.5" in mids,
                "ou_3_5": "OU_3.5" in mids,
                "ou_4_5": "OU_4.5" in mids,
                "all_four_ou_present": all(f"OU_{x}" in mids for x in LINES),
                "match_odds_plus_four_ou": "MATCH_ODDS" in mids and all(f"OU_{x}" in mids for x in LINES),
                "all_four_priceable_back_lay": all(bool((prices.get(f"OU_{x}") or {}).get("has_back")) and bool((prices.get(f"OU_{x}") or {}).get("has_lay")) for x in LINES),
                "labels_read": False,
            })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=500)
    # Retain V1 CLI args so the already-frozen workflow can call V2 unchanged.
    ap.add_argument("--max-listing-pages", type=int, default=80)
    ap.add_argument("--listing-workers", type=int, default=16)
    ap.add_argument("--event-workers", type=int, default=24)
    ap.add_argument("--max-event-fetch", type=int, default=900)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()

    started = now()
    # Ask for substantially more nodes than events because MARKET/MENU nodes share the cap.
    nav_max = max(5000, int(args.max_event_fetch) * 12)
    event_ids, nav_audit = discover_event_ids(nav_max)
    structure_ids = event_ids[: int(args.max_event_fetch)]

    resolved: list[dict[str, Any]] = []
    errors = Counter()
    workers = max(1, min(8, int(args.event_workers)))  # keep public endpoint pressure conservative
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(resolve_event, eid): eid for eid in structure_ids}
        for fut in cf.as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                r = {"event_id": futs[fut], "structure_status": "UNCAUGHT_FAIL", "error": f"{type(e).__name__}:{e}"}
            resolved.append(r)
            if r.get("structure_status") != "PASS":
                errors[str((r.get("structure_fetch") or {}).get("error") or r.get("error") or r.get("structure_status"))] += 1

    future_rows = [r for r in resolved if r.get("structure_status") == "PASS" and future(r, started)]
    future_rows.sort(key=lambda r: (iso_datetime(r.get("open_date_utc")) or dt.datetime.max.replace(tzinfo=dt.timezone.utc), int(str(r.get("event_id") or "0"))))
    sample = future_rows[: int(args.target)]

    # Price selected markets only after membership is frozen, so price availability cannot select rows.
    priced: list[dict[str, Any]] = []
    price_errors = Counter()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(price_event, r): str(r.get("event_id")) for r in sample}
        for fut in cf.as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                r = next((dict(x) for x in sample if str(x.get("event_id")) == futs[fut]), {"event_id": futs[fut]})
                r["price_status"] = "UNCAUGHT_FAIL"
                r["error"] = f"{type(e).__name__}:{e}"
            priced.append(r)
            if r.get("price_status") not in {"PASS", "NO_SELECTED_MARKETS"}:
                price_errors[str((r.get("price_fetch") or {}).get("error") or r.get("error") or r.get("price_status"))] += 1
    priced.sort(key=lambda r: (iso_datetime(r.get("open_date_utc")) or dt.datetime.max.replace(tzinfo=dt.timezone.utc), int(str(r.get("event_id") or "0"))))

    n = len(priced)
    per_line: dict[str, Any] = {}
    for line in LINES:
        key = f"OU_{line}"
        present = sum(key in (r.get("market_ids") or {}) for r in priced)
        priceable = sum(bool(((r.get("prices") or {}).get(key) or {}).get("has_back")) and bool(((r.get("prices") or {}).get(key) or {}).get("has_lay")) for r in priced)
        per_line[line] = {
            "present": present,
            "present_rate": rate(present, n),
            "back_lay_priceable": priceable,
            "back_lay_priceable_rate": rate(priceable, n),
        }

    match_odds = sum("MATCH_ODDS" in (r.get("market_ids") or {}) for r in priced)
    four = sum(all(f"OU_{x}" in (r.get("market_ids") or {}) for x in LINES) for r in priced)
    full = sum("MATCH_ODDS" in (r.get("market_ids") or {}) and all(f"OU_{x}" in (r.get("market_ids") or {}) for x in LINES) for r in priced)
    priceable_four = sum(all(bool(((r.get("prices") or {}).get(f"OU_{x}") or {}).get("has_back")) and bool(((r.get("prices") or {}).get(f"OU_{x}") or {}).get("has_lay")) for x in LINES) for r in priced)

    # Freeze exactly the same V1 gates; do not gate-shop after the HTML transport failure.
    prereg = {
        "target_future_events": int(args.target),
        "minimum_full_usable_events": 350,
        "minimum_full_usable_rate": 0.70,
        "minimum_each_ou_presence_rate": 0.80,
        "minimum_match_odds_presence_rate": 0.80,
        "minimum_exact_sample_count": int(args.target),
    }
    gates = {
        "exact_500_or_target_reached": n == int(args.target),
        "minimum_full_usable_events": full >= prereg["minimum_full_usable_events"],
        "minimum_full_usable_rate": bool(n) and full / n >= prereg["minimum_full_usable_rate"],
        "minimum_each_ou_presence_rate": bool(n) and all(per_line[x]["present_rate"] is not None and per_line[x]["present_rate"] >= prereg["minimum_each_ou_presence_rate"] for x in LINES),
        "minimum_match_odds_presence_rate": bool(n) and match_odds / n >= prereg["minimum_match_odds_presence_rate"],
    }
    all_pass = all(gates.values())

    result = {
        "schema_version": "BETFAIR-MULTI-OU-COVERAGE500-20260816-R2-READONLY-WEB-API",
        "status": "PASS_COVERAGE_GATE" if all_pass else "FAIL_COVERAGE_GATE",
        "classification": "ZERO_LABEL_PUBLIC_WEB_COVERAGE_PROBE_FORMAL_WEIGHT_0",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": now().isoformat(),
        "source": {
            "site": "Betfair Exchange Australia public read-only web APIs",
            "transport": "scan-inbf navigation + ero byevent/bymarket",
            "login_used": False,
            "credentials_used": False,
            "paid_api_used": False,
            "historical_data_purchase_used": False,
            "public_web_key_used": True,
            "capture_timestamp_semantics": "researcher_request_observed_at_not_provider_origin_quote_timestamp",
        },
        "v1_transport_failure_context": {
            "html_runner_listing_requests": 160,
            "html_runner_403": 160,
            "scientific_coverage_inference_from_v1": "NONE",
            "coverage_gate_unchanged_for_v2": True,
        },
        "discovery": {
            **nav_audit,
            "unique_event_ids_discovered": len(event_ids),
            "event_structures_attempted": len(resolved),
            "event_structures_pass": sum(r.get("structure_status") == "PASS" for r in resolved),
            "future_eligible_before_sampling": len(future_rows),
            "structure_error_counts": dict(errors.most_common()),
            "price_error_counts": dict(price_errors.most_common()),
        },
        "sample_contract": {
            "target": int(args.target),
            "actual": n,
            "selection": "future event structures only; deterministic ascending openDate,eventId; no coverage/price/result filtering",
            "result_used_for_selection": False,
            "labels_read": False,
            "membership_frozen_before_price_fetch": True,
        },
        "coverage": {
            "match_odds_present": match_odds,
            "match_odds_present_rate": rate(match_odds, n),
            "per_ou_line": per_line,
            "all_four_ou_present": four,
            "all_four_ou_present_rate": rate(four, n),
            "match_odds_plus_four_ou": full,
            "match_odds_plus_four_ou_rate": rate(full, n),
            "all_four_back_lay_priceable": priceable_four,
            "all_four_back_lay_priceable_rate": rate(priceable_four, n),
        },
        "preregistered_coverage_gate": prereg,
        "gate_results": gates,
        "all_gates_pass": all_pass,
        "decision": "START_FORWARD_MULTI_OU_FREEZE_ACCUMULATION" if all_pass else "DO_NOT_START_FORWARD_MULTI_OU_FROM_THIS_SOURCE_YET",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "model_fit_performed": False,
            "settlement_labels_read": False,
            "prediction_claim_allowed": False,
            "formal_probability_mutation": False,
            "current_rule_change": False,
            "betting_action_performed": False,
        },
        "sample_rows": priced,
    }

    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_csv, priced)
    print(json.dumps({
        "status": result["status"],
        "navigation_fetch": nav_audit.get("navigation_fetch"),
        "event_ids_discovered": len(event_ids),
        "future_eligible": len(future_rows),
        "sample_count": n,
        "match_odds_plus_four_ou": full,
        "match_odds_plus_four_ou_rate": rate(full, n),
        "all_four_back_lay_priceable": priceable_four,
        "gate_results": gates,
        "decision": result["decision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
