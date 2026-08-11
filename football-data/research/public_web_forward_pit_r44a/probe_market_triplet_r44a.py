#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as html_lib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DECIMAL_RE = re.compile(r"(?<![\d.])(?:1\.[0-9]{2,3}|[2-9]\.[0-9]{2,3}|[1-9][0-9]\.[0-9]{2,3})(?![\d.])")
USER_AGENT = "FASHI188-V520-R44A-MarketTriplet/1.0"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def visible_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return SPACE_RE.sub(" ", html_lib.unescape(TAG_RE.sub(" ", text))).strip()


def fail(message: str) -> None:
    raise RuntimeError(message)


def validate_url(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in {"www.infobetting.com", "infobetting.com"} or p.username or p.password or p.port:
        fail("URL_OUTSIDE_ALLOWLIST")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, timeout: int, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    validate_url(url)
    requested = utc_now()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.1", "Cache-Control": "no-cache"},
    )
    opener = urllib.request.build_opener(SafeRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            final_url = response.geturl()
            validate_url(final_url)
            raw = response.read(max_bytes + 1)
            ctype = str(response.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as exc:
        fail(f"HTTP_{exc.code}")
    except urllib.error.URLError as exc:
        raise RuntimeError("NETWORK_READ_FAILED") from exc
    observed = utc_now()
    if status != 200 or not raw or len(raw) > max_bytes:
        fail("HTTP_BODY_INVALID")
    return raw, {
        "requested_at_utc": iso(requested),
        "observed_at_utc": iso(observed),
        "status": status,
        "final_url": final_url,
        "content_type": ctype,
        "bytes": len(raw),
        "payload_sha256": sha256(raw),
    }


def fixture_region(text: str) -> str:
    low = text.casefold()
    start = low.find("arsenal - coventry")
    if start < 0:
        fail("FIXTURE_TOKEN_MISSING")
    next_markers = ["hull - manchester united", "everton - crystal palace", "ipswich - sunderland"]
    ends = [low.find(m, start + 20) for m in next_markers]
    ends = [x for x in ends if x > start]
    end = min(ends) if ends else min(len(text), start + 6000)
    region = text[start:end]
    if "21-08-2026" not in region and "21/08/2026" not in region:
        fail("FIXTURE_DATE_MISSING")
    return region


def audit_role(role: str, region: str) -> dict[str, Any]:
    decimals = DECIMAL_RE.findall(region)
    if role == "hda":
        if len(decimals) < 3:
            fail("HDA_COMPLETE_PRICE_TRIPLE_NOT_VISIBLE")
        return {
            "role": role,
            "price_like_count": len(decimals),
            "visible_price_sample": decimals[:6],
            "role_gate": True,
        }
    if role == "asian":
        if "0:0" not in region or len(decimals) < 2:
            fail("ASIAN_PAIR_NOT_VISIBLE")
        return {
            "role": role,
            "contains_handicap_0_0": True,
            "price_like_count": len(decimals),
            "visible_price_sample": decimals[:6],
            "role_gate": True,
        }
    if role == "ou":
        required = ["0.5", "1.5", "2.5", "3.5", "4.5", "5.5"]
        missing = [line for line in required if line not in region]
        if missing:
            fail("OU_REQUIRED_LINES_MISSING:" + ",".join(missing))
        if len(decimals) < 12:
            fail("OU_BILATERAL_PRICE_COVERAGE_TOO_LOW")
        return {
            "role": role,
            "required_half_goal_lines": required,
            "missing_half_goal_lines": [],
            "price_like_count": len(decimals),
            "visible_price_sample": decimals[:18],
            "role_gate": True,
        }
    fail("UNKNOWN_MARKET_ROLE")
    raise AssertionError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    cp = Path(args.contract)
    c = json.loads(cp.read_text(encoding="utf-8"))
    if c.get("schema_version") != "V520-R44A-PUBLIC-WEB-FORWARD-PIT-1.0":
        fail("CONTRACT_SCHEMA_MISMATCH")
    if c.get("research_only") is not True or c.get("formal_weight") != 0:
        fail("RESEARCH_BOUNDARY_INVALID")
    triplet = (c.get("market_source") or {}).get("synchronized_triplet") or []
    if [x.get("role") for x in triplet] != ["hda", "asian", "ou"]:
        fail("TRIPLET_ROLE_ORDER_INVALID")
    if len(triplet) != 3:
        fail("TRIPLET_SIZE_INVALID")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    raw_dir = out / "raw"
    raw_dir.mkdir()
    timeout = int(c["capture_rules"]["http_timeout_seconds"])
    max_bytes = int(c["capture_rules"]["max_http_bytes"])
    kickoff = parse_utc(c["fixture"]["scheduled_kickoff_utc"])

    rows: list[dict[str, Any]] = []
    for item in triplet:
        raw, meta = fetch(item["url"], timeout, max_bytes)
        observed = parse_utc(meta["observed_at_utc"])
        if observed >= kickoff:
            fail("MARKET_OBSERVED_AFTER_KICKOFF")
        text = visible_text(raw)
        region = fixture_region(text)
        role_audit = audit_role(item["role"], region)
        raw_name = f"{item['role']}__{meta['payload_sha256']}.html"
        (raw_dir / raw_name).write_bytes(raw)
        rows.append({
            "role": item["role"],
            "source_url": item["url"],
            "raw_path": f"raw/{raw_name}",
            "fixture_region_sha256": sha256(region.encode("utf-8")),
            **meta,
            **role_audit,
        })

    first = min(parse_utc(x["requested_at_utc"]) for x in rows)
    last = max(parse_utc(x["observed_at_utc"]) for x in rows)
    span = (last - first).total_seconds()
    max_span = float(c["capture_rules"].get("market_triplet_max_sync_span_seconds", 15))
    if span > max_span:
        fail("MARKET_TRIPLET_SYNC_SPAN_EXCEEDED")

    receipt = {
        "schema_version": "V520-R44A-MARKET-TRIPLET-RECEIPT-1.0",
        "terminal": "PASS_R44A_SYNCHRONIZED_MARKET_SOURCE_COVERAGE_NOT_FORMAL_SNAPSHOT",
        "fixture": c["fixture"],
        "provider_group": c["market_source"]["provider_group"],
        "independent_market_count_claimed": 1,
        "collector_observation_window_start_utc": iso(first),
        "collector_observation_window_end_utc": iso(last),
        "synchronization_span_seconds": span,
        "synchronization_span_limit_seconds": max_span,
        "source_native_quote_timestamp": None,
        "availability_time_semantics": "collector_first_observed_at_utc per captured page",
        "markets": rows,
        "formal_market_snapshot": False,
        "reason_not_formal_snapshot": "This is a zero-label coverage gate. Exact field-level structured prices, source freshness policy, and repeated freeze-window persistence are not yet admitted into the formal runtime.",
        "target_labels_accessed": 0,
        "settlement_results_accessed": 0,
        "model_fits": 0,
        "candidate_probabilities": 0,
        "fixed100_consumed": 0,
        "fixed200_consumed": 0,
        "ev_calculations": 0,
        "formal_weight": 0,
        "formal_model_changes": 0,
        "formal_data_changes": 0,
        "formal_config_changes": 0,
        "CURRENT_changes": 0,
    }
    raw = packed(receipt) + b"\n"
    (out / "market_triplet_receipt_r44a.json").write_bytes(raw)
    (out / "market_triplet_receipt_r44a.sha256").write_text(sha256(raw) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": receipt["terminal"],
        "sync_span_seconds": span,
        "roles": [x["role"] for x in rows],
        "observed_at": [x["observed_at_utc"] for x in rows],
        "payload_sha256": [x["payload_sha256"] for x in rows],
        "receipt_sha256": sha256(raw),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R44A_MARKET_TRIPLET_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
