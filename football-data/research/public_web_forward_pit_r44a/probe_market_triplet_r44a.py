#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DECIMAL_RE = re.compile(r"(?<![\d.])(?:1\.[0-9]{2,3}|[2-9]\.[0-9]{2,3}|[1-9][0-9]\.[0-9]{2,3})(?![\d.])")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def visible_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return SPACE_RE.sub(" ", html_lib.unescape(TAG_RE.sub(" ", text))).strip()


def fail(message: str) -> None:
    raise RuntimeError(message)


def find_all(text: str, needle: str) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        pos = text.find(needle, start)
        if pos < 0:
            return out
        out.append(pos)
        start = pos + len(needle)


def extract_role_regions(text: str) -> dict[str, str]:
    low = text.casefold()
    hda_anchor = low.find("media 1 x 2")
    hand_anchors = find_all(low, "media 1 hand. media 2")
    if hda_anchor < 0:
        fail("HDA_SECTION_ANCHOR_MISSING")
    if len(hand_anchors) < 2:
        fail("HANDICAP_SECTION_ANCHORS_LT2")
    asian_anchor = hand_anchors[0]
    ou_anchor = hand_anchors[1]
    if not (hda_anchor < asian_anchor < ou_anchor):
        fail("MARKET_SECTION_ORDER_INVALID")
    ou_end = low.find("media gol nogol", ou_anchor)
    if ou_end < 0:
        ou_end = min(len(text), ou_anchor + 12000)
    return {
        "hda": text[hda_anchor:asian_anchor],
        "asian": text[asian_anchor:ou_anchor],
        "ou": text[ou_anchor:ou_end],
    }


def audit_roles(regions: dict[str, str]) -> list[dict[str, Any]]:
    date_tokens = ("21/08/2026", "21-08-2026")
    hda = regions["hda"]
    hda_decimals = DECIMAL_RE.findall(hda)
    if not any(x in hda for x in date_tokens) or len(hda_decimals) < 6:
        fail("HDA_COMPLETE_PRICE_COVERAGE_FAILED")

    asian = regions["asian"]
    asian_decimals = DECIMAL_RE.findall(asian)
    if not any(x in asian for x in date_tokens) or "0:0" not in asian or len(asian_decimals) < 4:
        fail("ASIAN_BILATERAL_COVERAGE_FAILED")

    ou = regions["ou"]
    ou_decimals = DECIMAL_RE.findall(ou)
    required = ["0.5", "1.5", "2.5", "3.5", "4.5", "5.5"]
    missing = [x for x in required if x not in ou]
    if not any(x in ou for x in date_tokens) or missing or len(ou_decimals) < 12:
        fail("OU_MULTILINE_BILATERAL_COVERAGE_FAILED:" + ",".join(missing))

    return [
        {
            "role": "hda",
            "role_gate": True,
            "region_sha256": sha256(hda.encode("utf-8")),
            "price_like_count": len(hda_decimals),
            "visible_price_sample": hda_decimals[:9],
        },
        {
            "role": "asian",
            "role_gate": True,
            "contains_handicap_0_0": True,
            "region_sha256": sha256(asian.encode("utf-8")),
            "price_like_count": len(asian_decimals),
            "visible_price_sample": asian_decimals[:8],
        },
        {
            "role": "ou",
            "role_gate": True,
            "required_half_goal_lines": required,
            "missing_half_goal_lines": [],
            "region_sha256": sha256(ou.encode("utf-8")),
            "price_like_count": len(ou_decimals),
            "visible_price_sample": ou_decimals[:18],
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--base-output", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    cp = Path(args.contract)
    base = Path(args.base_output)
    out = Path(args.output)
    c = json.loads(cp.read_text(encoding="utf-8"))
    if c.get("schema_version") != "V520-R44A-PUBLIC-WEB-FORWARD-PIT-1.1":
        fail("CONTRACT_SCHEMA_MISMATCH")
    if c.get("status") != "ZERO_LABEL_EXACT_FIXTURE_MULTI_MARKET_COVERAGE_ONLY":
        fail("CONTRACT_STATUS_MISMATCH")
    if c.get("research_only") is not True or c.get("formal_weight") != 0:
        fail("RESEARCH_BOUNDARY_INVALID")
    exact = (c.get("market_source") or {}).get("exact_fixture_payload") or {}
    if exact.get("required_roles") != ["hda", "asian", "ou"]:
        fail("ROLE_CONTRACT_INVALID")
    if exact.get("synchronization_semantics") != "SAME_HTTP_PAYLOAD_SINGLE_OBSERVATION":
        fail("SYNC_SEMANTICS_INVALID")

    receipt_path = base / "receipt_r44a.json"
    if not receipt_path.is_file():
        fail("BASE_RECEIPT_MISSING")
    base_raw = receipt_path.read_bytes()
    base_receipt = json.loads(base_raw)
    if base_receipt.get("schema_version") != "V520-R44A-RECEIPT-1.1":
        fail("BASE_RECEIPT_SCHEMA_MISMATCH")
    if base_receipt.get("terminal") != "PASS_R44A_SOURCE_FEASIBILITY_NOT_FORMAL_MARKET_SNAPSHOT":
        fail("BASE_SOURCE_FEASIBILITY_NOT_PASS")

    market = base_receipt.get("market_evidence") or {}
    source_url = market.get("selected_url")
    expected_url = exact.get("url")
    if source_url != expected_url:
        fail("EXACT_FIXTURE_URL_MISMATCH")
    raw_rel = market.get("raw_path")
    digest = market.get("payload_sha256")
    if not isinstance(raw_rel, str) or not isinstance(digest, str):
        fail("BASE_RAW_IDENTITY_MISSING")
    market_raw_path = base / raw_rel
    if not market_raw_path.is_file():
        fail("BASE_MARKET_RAW_MISSING")
    raw = market_raw_path.read_bytes()
    if sha256(raw) != digest:
        fail("BASE_MARKET_SHA_MISMATCH")

    text = visible_text(raw)
    low = text.casefold()
    if "arsenal" not in low or "coventry" not in low:
        fail("FIXTURE_IDENTITY_MISSING")
    regions = extract_role_regions(text)
    audits = audit_roles(regions)

    out.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema_version": "V520-R44A-EXACT-PAYLOAD-MARKET-RECEIPT-1.0",
        "terminal": "PASS_R44A_EXACT_FIXTURE_MULTI_MARKET_COVERAGE_NOT_FORMAL_SNAPSHOT",
        "fixture": c["fixture"],
        "provider_group": c["market_source"]["provider_group"],
        "independent_market_count_claimed": 1,
        "source_url": source_url,
        "source_payload_sha256": digest,
        "source_payload_path": str(market_raw_path),
        "collector_first_observed_at_utc": market["collector_first_observed_at_utc"],
        "source_native_quote_timestamp": None,
        "availability_time_semantics": "collector_first_observed_at_utc",
        "synchronization_semantics": "SAME_HTTP_PAYLOAD_SINGLE_OBSERVATION",
        "synchronization_span_seconds": 0.0,
        "market_roles": audits,
        "formal_market_snapshot": False,
        "structured_complete_prices_extracted": False,
        "reason_not_formal_snapshot": "Same-payload role coverage is proven, but source-native quote timestamps, frozen field-level price extraction, executable-price identity, and repeated freeze-window persistence are not yet admitted.",
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
    encoded = packed(receipt) + b"\n"
    (out / "exact_payload_market_receipt_r44a.json").write_bytes(encoded)
    (out / "exact_payload_market_receipt_r44a.sha256").write_text(sha256(encoded) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": receipt["terminal"],
        "source_payload_sha256": digest,
        "roles": [x["role"] for x in audits],
        "synchronization_span_seconds": 0.0,
        "receipt_sha256": sha256(encoded),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R44A_EXACT_PAYLOAD_AUDIT_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
