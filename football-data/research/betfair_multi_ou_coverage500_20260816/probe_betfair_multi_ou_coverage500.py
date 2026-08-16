#!/usr/bin/env python3
"""Read-only Betfair Exchange public-page multi-OU coverage probe.

Research purpose only. This script does NOT log in, place bets, call paid/history APIs,
read settlement labels, train a model, or mutate formal football assets.

It discovers public Betfair football event pages, captures each event page once, and
measures whether the same capture exposes Match Odds plus O/U 1.5, 2.5, 3.5 and 4.5.
The target is a deterministic sample of 500 future events. Capture time is our own
first_observed_at timestamp; it is NOT claimed to be a provider-origin quote timestamp.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE = "https://www.betfair.com"
LISTING_BASES = (
    "https://www.betfair.com/exchange/plus/en/football-betting-1",
    "https://www.betfair.com/exchange/plus/en/soccer-betting-1",
)
LINES = ("1.5", "2.5", "3.5", "4.5")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36 "
    "football-research-readonly-coverage500/1.0"
)
CTX = ssl.create_default_context()
EVENT_HREF_RE = re.compile(
    r'''(?:href|url)\s*[=:]\s*["']([^"']*?/football/[^"'<>\s]+?-betting-\d+[^"']*)["']''',
    re.I,
)
EVENT_ABS_RE = re.compile(
    r'''https?://(?:www\.)?betfair\.com/exchange/plus/(?:en/)?football/[^"'<>\s]+?-betting-\d+''',
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
SPACE_RE = re.compile(r"\s+")
ISO_RE = re.compile(r'''(?:eventDate|openDate|startTime|kickoff(?:At)?)\s*["']?\s*[:=]\s*["'](20\d\d-\d\d-\d\dT[^"']+)["']''', re.I)
HUMAN_DATE_RE = re.compile(r"\((\d{1,2}\s+[A-Z][a-z]+\s+20\d{2},\s+\d{1,2}:\d{2})\)")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
MONEY_RE = re.compile(r"£\s*([0-9][0-9,]*(?:\.\d+)?)")
ODDS_RE = re.compile(r"(?<![\d.])([1-9]\d{0,2}(?:\.\d{1,3})?|1000)(?![\d.])")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def canonical_url(raw: str) -> str | None:
    raw = html.unescape(raw).strip().replace("\\/", "/")
    if not raw:
        return None
    url = urllib.parse.urljoin(BASE, raw)
    p = urllib.parse.urlsplit(url)
    if p.scheme not in {"http", "https"} or "betfair.com" not in p.netloc.lower():
        return None
    path = p.path
    if "-betting-" not in path or "/football/" not in path:
        return None
    return urllib.parse.urlunsplit(("https", "www.betfair.com", path, "", ""))


def fetch(url: str, timeout: float = 30.0) -> tuple[bytes | None, dict[str, Any]]:
    started = utc_now()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            data = r.read()
            meta = {
                "ok": True,
                "http_status": int(getattr(r, "status", 200)),
                "final_url": str(r.geturl()),
                "content_type": str(r.headers.get("Content-Type") or ""),
                "observed_at_utc": started.isoformat(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            return data, meta
    except urllib.error.HTTPError as e:
        return None, {
            "ok": False,
            "error": f"HTTPError:{e.code}",
            "observed_at_utc": started.isoformat(),
        }
    except Exception as e:  # network boundary; summarized, never retried forever
        return None, {
            "ok": False,
            "error": f"{type(e).__name__}:{e}",
            "observed_at_utc": started.isoformat(),
        }


def textify(data: bytes) -> tuple[str, str]:
    raw = data.decode("utf-8", errors="replace")
    cleaned = SCRIPT_RE.sub(" ", raw)
    cleaned = TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned).strip()
    # Some state payloads escape slashes/quotes; keep a normalized raw copy too.
    raw_norm = html.unescape(raw).replace("\\/", "/").replace("\\u002F", "/")
    return raw_norm, cleaned


def discover_event_urls(max_listing_pages: int, listing_workers: int) -> tuple[list[str], dict[str, Any]]:
    urls: list[str] = []
    for base in LISTING_BASES:
        urls.append(base)
        urls.extend(f"{base}/{i}" for i in range(2, max_listing_pages + 1))

    found: set[str] = set()
    stats = Counter()
    errors: Counter[str] = Counter()

    def one(url: str) -> tuple[str, bytes | None, dict[str, Any]]:
        data, meta = fetch(url, timeout=25)
        return url, data, meta

    with cf.ThreadPoolExecutor(max_workers=listing_workers) as ex:
        futures = [ex.submit(one, u) for u in urls]
        for fut in cf.as_completed(futures):
            url, data, meta = fut.result()
            stats["listing_requested"] += 1
            if data is None:
                stats["listing_failed"] += 1
                errors[str(meta.get("error") or "unknown")] += 1
                continue
            stats["listing_ok"] += 1
            raw, _ = textify(data)
            local: set[str] = set()
            for m in EVENT_HREF_RE.finditer(raw):
                u = canonical_url(m.group(1))
                if u:
                    local.add(u)
            for m in EVENT_ABS_RE.finditer(raw):
                u = canonical_url(m.group(0))
                if u:
                    local.add(u)
            found.update(local)
            stats["event_link_occurrences"] += len(local)

    return sorted(found), {
        "stats": dict(stats),
        "errors": dict(errors.most_common()),
        "listing_page_count": len(urls),
    }


def parse_title(raw: str, url: str) -> str:
    m = TITLE_RE.search(raw)
    if m:
        t = SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", m.group(1)))).strip()
        t = re.sub(r"^Best\s+", "", t, flags=re.I)
        t = re.sub(r"\s+Odds\s*&\s*Bets.*$", "", t, flags=re.I)
        if t:
            return t
    slug = urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-betting-\d+$", "", slug)
    return slug.replace("-", " ").strip()


def parse_competition(url: str) -> str:
    parts = [p for p in urllib.parse.urlsplit(url).path.split("/") if p]
    try:
        i = parts.index("football")
        return parts[i + 1] if i + 1 < len(parts) else ""
    except ValueError:
        return ""


def parse_kickoff(raw: str, text: str) -> tuple[str | None, str]:
    m = ISO_RE.search(raw)
    if m:
        value = html.unescape(m.group(1)).replace("Z", "+00:00")
        try:
            d = dt.datetime.fromisoformat(value)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat(), "embedded_iso"
        except Exception:
            pass
    m = HUMAN_DATE_RE.search(text)
    if m:
        try:
            local = dt.datetime.strptime(m.group(1), "%d %B %Y, %H:%M").replace(tzinfo=ZoneInfo("Europe/London"))
            return local.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat(), "human_page_text_europe_london"
        except Exception:
            pass
    return None, "unparsed"


def market_window(text: str, label: str, width: int = 850) -> str:
    low = text.lower()
    idx = low.find(label.lower())
    if idx < 0:
        return ""
    return text[max(0, idx - 100): min(len(text), idx + width)]


def parse_market(text: str, line: str) -> dict[str, Any]:
    labels = (
        f"Over/Under {line} Goals",
        f"Over {line} Goals",
        f"Under {line} Goals",
    )
    found_label = next((x for x in labels if x.lower() in text.lower()), None)
    if not found_label:
        return {"present": False, "price_tokens_found": 0, "money_tokens_found": 0}
    win = market_window(text, found_label)
    odds: list[float] = []
    for m in ODDS_RE.finditer(win):
        try:
            x = float(m.group(1))
        except ValueError:
            continue
        if 1.01 <= x <= 1000.0 and x not in odds:
            odds.append(x)
        if len(odds) >= 12:
            break
    money = [m.group(1) for m in MONEY_RE.finditer(win)][:12]
    return {
        "present": True,
        "price_tokens_found": len(odds),
        "money_tokens_found": len(money),
        "odds_candidates": odds,
        "money_candidates_gbp": money,
        "window_excerpt": win[:500],
    }


def parse_event(url: str) -> dict[str, Any]:
    data, meta = fetch(url, timeout=30)
    row: dict[str, Any] = {
        "url": url,
        "competition_slug": parse_competition(url),
        "fetch": meta,
        "labels_read": False,
        "model_fit_performed": False,
    }
    if data is None:
        row["parse_status"] = "FETCH_FAIL"
        return row
    raw, text = textify(data)
    row["event_name"] = parse_title(raw, url)
    kickoff, kickoff_basis = parse_kickoff(raw, text)
    row["kickoff_utc"] = kickoff
    row["kickoff_basis"] = kickoff_basis
    row["match_odds_present"] = "match odds" in text.lower() or "match_odds" in raw.lower()
    row["markets"] = {line: parse_market(text, line) for line in LINES}
    row["all_four_ou_present"] = all(bool(row["markets"][line]["present"]) for line in LINES)
    row["match_odds_plus_four_ou"] = bool(row["match_odds_present"] and row["all_four_ou_present"])
    row["all_four_have_price_tokens"] = all(int(row["markets"][line]["price_tokens_found"]) >= 2 for line in LINES)
    row["parse_status"] = "PASS"
    return row


def is_future(row: dict[str, Any], observed_floor: dt.datetime) -> bool:
    value = row.get("kickoff_utc")
    if not value:
        return False
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d > observed_floor + dt.timedelta(minutes=5)
    except Exception:
        return False


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "sample_index", "event_name", "competition_slug", "kickoff_utc", "kickoff_basis",
        "url", "observed_at_utc", "http_status", "sha256", "match_odds_present",
        "ou_1_5", "ou_2_5", "ou_3_5", "ou_4_5", "all_four_ou_present",
        "match_odds_plus_four_ou", "all_four_have_price_tokens", "labels_read",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow({
                "sample_index": i,
                "event_name": r.get("event_name"),
                "competition_slug": r.get("competition_slug"),
                "kickoff_utc": r.get("kickoff_utc"),
                "kickoff_basis": r.get("kickoff_basis"),
                "url": r.get("url"),
                "observed_at_utc": (r.get("fetch") or {}).get("observed_at_utc"),
                "http_status": (r.get("fetch") or {}).get("http_status"),
                "sha256": (r.get("fetch") or {}).get("sha256"),
                "match_odds_present": r.get("match_odds_present"),
                "ou_1_5": ((r.get("markets") or {}).get("1.5") or {}).get("present"),
                "ou_2_5": ((r.get("markets") or {}).get("2.5") or {}).get("present"),
                "ou_3_5": ((r.get("markets") or {}).get("3.5") or {}).get("present"),
                "ou_4_5": ((r.get("markets") or {}).get("4.5") or {}).get("present"),
                "all_four_ou_present": r.get("all_four_ou_present"),
                "match_odds_plus_four_ou": r.get("match_odds_plus_four_ou"),
                "all_four_have_price_tokens": r.get("all_four_have_price_tokens"),
                "labels_read": r.get("labels_read"),
            })


def rate(n: int, d: int) -> float | None:
    return (n / d) if d else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=500)
    ap.add_argument("--max-listing-pages", type=int, default=80)
    ap.add_argument("--listing-workers", type=int, default=16)
    ap.add_argument("--event-workers", type=int, default=24)
    ap.add_argument("--max-event-fetch", type=int, default=900)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()

    started = utc_now()
    event_urls, discovery = discover_event_urls(args.max_listing_pages, args.listing_workers)
    candidates = event_urls[: args.max_event_fetch]

    parsed: list[dict[str, Any]] = []
    errors = Counter()
    with cf.ThreadPoolExecutor(max_workers=args.event_workers) as ex:
        futs = {ex.submit(parse_event, u): u for u in candidates}
        for fut in cf.as_completed(futs):
            try:
                row = fut.result()
            except Exception as e:
                row = {"url": futs[fut], "parse_status": "UNCAUGHT_FAIL", "error": f"{type(e).__name__}:{e}"}
            parsed.append(row)
            if row.get("parse_status") != "PASS":
                errors[str((row.get("fetch") or {}).get("error") or row.get("error") or row.get("parse_status"))] += 1

    future_rows = [r for r in parsed if r.get("parse_status") == "PASS" and is_future(r, started)]
    future_rows.sort(key=lambda r: (str(r.get("kickoff_utc") or ""), str(r.get("url") or "")))
    sample = future_rows[: args.target]

    n = len(sample)
    per_line = {}
    for line in LINES:
        present = sum(bool(((r.get("markets") or {}).get(line) or {}).get("present")) for r in sample)
        priceable = sum(int(((r.get("markets") or {}).get(line) or {}).get("price_tokens_found") or 0) >= 2 for r in sample)
        per_line[line] = {
            "present": present,
            "present_rate": rate(present, n),
            "two_plus_price_tokens": priceable,
            "two_plus_price_tokens_rate": rate(priceable, n),
        }

    match_odds = sum(bool(r.get("match_odds_present")) for r in sample)
    four = sum(bool(r.get("all_four_ou_present")) for r in sample)
    full = sum(bool(r.get("match_odds_plus_four_ou")) for r in sample)
    priceable_four = sum(bool(r.get("all_four_have_price_tokens")) for r in sample)
    fetch_ok = sum(bool((r.get("fetch") or {}).get("ok")) for r in parsed)
    kickoff_known = sum(bool(r.get("kickoff_utc")) for r in parsed if r.get("parse_status") == "PASS")
    parsed_pass = sum(r.get("parse_status") == "PASS" for r in parsed)

    prereg = {
        "target_future_events": args.target,
        "minimum_full_usable_events": 350,
        "minimum_full_usable_rate": 0.70,
        "minimum_each_ou_presence_rate": 0.80,
        "minimum_match_odds_presence_rate": 0.80,
        "minimum_exact_sample_count": args.target,
    }
    gates = {
        "exact_500_or_target_reached": n == args.target,
        "minimum_full_usable_events": full >= prereg["minimum_full_usable_events"],
        "minimum_full_usable_rate": bool(n) and full / n >= prereg["minimum_full_usable_rate"],
        "minimum_each_ou_presence_rate": bool(n) and all(per_line[x]["present_rate"] is not None and per_line[x]["present_rate"] >= prereg["minimum_each_ou_presence_rate"] for x in LINES),
        "minimum_match_odds_presence_rate": bool(n) and match_odds / n >= prereg["minimum_match_odds_presence_rate"],
    }
    all_pass = all(gates.values())

    result = {
        "schema_version": "BETFAIR-MULTI-OU-COVERAGE500-20260816-R1",
        "status": "PASS_COVERAGE_GATE" if all_pass else "FAIL_COVERAGE_GATE",
        "classification": "ZERO_LABEL_PUBLIC_WEB_COVERAGE_PROBE_FORMAL_WEIGHT_0",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": utc_now().isoformat(),
        "source": {
            "site": "Betfair Exchange public web",
            "login_used": False,
            "credentials_used": False,
            "paid_api_used": False,
            "historical_data_purchase_used": False,
            "capture_timestamp_semantics": "researcher_capture_observed_at_not_provider_origin_quote_timestamp",
        },
        "discovery": {
            **discovery,
            "unique_event_urls_discovered": len(event_urls),
            "event_pages_attempted": len(parsed),
            "event_pages_fetch_ok": fetch_ok,
            "event_pages_parse_pass": parsed_pass,
            "event_pages_kickoff_known": kickoff_known,
            "future_eligible_before_sampling": len(future_rows),
            "error_counts": dict(errors.most_common()),
        },
        "sample_contract": {
            "target": args.target,
            "actual": n,
            "selection": "future events only; deterministic ascending kickoff_utc then URL; no coverage/result filtering",
            "result_used_for_selection": False,
            "labels_read": False,
            "same_event_single_http_capture_for_market_presence": True,
        },
        "coverage": {
            "match_odds_present": match_odds,
            "match_odds_present_rate": rate(match_odds, n),
            "per_ou_line": per_line,
            "all_four_ou_present": four,
            "all_four_ou_present_rate": rate(four, n),
            "match_odds_plus_four_ou": full,
            "match_odds_plus_four_ou_rate": rate(full, n),
            "all_four_have_two_plus_price_tokens": priceable_four,
            "all_four_have_two_plus_price_tokens_rate": rate(priceable_four, n),
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
        "sample_rows": sample,
    }

    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_csv, sample)
    print(json.dumps({
        "status": result["status"],
        "unique_event_urls_discovered": len(event_urls),
        "future_eligible": len(future_rows),
        "sample_count": n,
        "match_odds_plus_four_ou": full,
        "match_odds_plus_four_ou_rate": rate(full, n),
        "all_four_have_two_plus_price_tokens": priceable_four,
        "gate_results": gates,
        "decision": result["decision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
