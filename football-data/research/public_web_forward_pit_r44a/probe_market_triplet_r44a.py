#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as html_lib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DECIMAL_RE = re.compile(r"(?<![\d.])(?:1\.[0-9]{2,3}|[2-9]\.[0-9]{2,3}|[1-9][0-9]\.[0-9]{2,3})(?![\d.])")
BOX_RE_TEMPLATE = r'<div[^>]*class="[^"]*\bboxQuote1x2\b[^"]*"[^>]*id="box_bm_{fixture_market_id}_(\d+)"[^>]*>'


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def visible_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return SPACE_RE.sub(" ", html_lib.unescape(TAG_RE.sub(" ", text))).strip()


def fail(message: str) -> None:
    raise RuntimeError(message)


def parse_price(value: str, identity: str) -> float:
    try:
        out = float(value.strip())
    except Exception as exc:
        raise RuntimeError(f"PRICE_PARSE_FAILED:{identity}") from exc
    if not (1.0 < out < 1000.0):
        fail(f"PRICE_RANGE_INVALID:{identity}:{out}")
    return out


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
    return {"hda": text[hda_anchor:asian_anchor], "asian": text[asian_anchor:ou_anchor], "ou": text[ou_anchor:ou_end]}


def audit_roles(regions: dict[str, str], required_ou_lines: list[str]) -> list[dict[str, Any]]:
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
    missing = [x for x in required_ou_lines if x not in ou]
    if not any(x in ou for x in date_tokens) or missing or len(ou_decimals) < 12:
        fail("OU_MULTILINE_BILATERAL_COVERAGE_FAILED:" + ",".join(missing))
    return [
        {"role": "hda", "role_gate": True, "region_sha256": sha256(hda.encode("utf-8")), "price_like_count": len(hda_decimals)},
        {"role": "asian", "role_gate": True, "contains_handicap_0_0": True, "region_sha256": sha256(asian.encode("utf-8")), "price_like_count": len(asian_decimals)},
        {"role": "ou", "role_gate": True, "required_half_goal_lines": required_ou_lines, "missing_half_goal_lines": [], "region_sha256": sha256(ou.encode("utf-8")), "price_like_count": len(ou_decimals)},
    ]


def html_section(raw_html: str, section_id: str, next_section_id: str) -> str:
    start = raw_html.find(f'id="{section_id}"')
    if start < 0:
        fail(f"HTML_SECTION_MISSING:{section_id}")
    end = raw_html.find(f'id="{next_section_id}"', start + 1)
    if end < 0 or end <= start:
        fail(f"HTML_SECTION_BOUNDARY_MISSING:{section_id}:{next_section_id}")
    return raw_html[start:end]


def id_text(section: str, identity: str) -> str:
    m = re.search(rf'<(?:span|a)[^>]*\bid="{re.escape(identity)}"[^>]*>(.*?)</(?:span|a)>', section, flags=re.I | re.S)
    if not m:
        fail(f"STRUCTURED_ID_MISSING:{identity}")
    text = TAG_RE.sub(" ", m.group(1))
    return SPACE_RE.sub(" ", html_lib.unescape(text)).strip()


def bookmaker_blocks(section: str, fixture_market_id: str) -> list[tuple[str, str]]:
    pat = re.compile(BOX_RE_TEMPLATE.format(fixture_market_id=re.escape(fixture_market_id)), flags=re.I)
    matches = list(pat.finditer(section))
    rows: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        rows.append((match.group(1), section[match.start():end]))
    return rows


def bookmaker_meta(block: str, bookmaker_id: str) -> dict[str, Any]:
    img = re.search(r'<img[^>]*class="[^"]*\bbookLogo\b[^"]*"[^>]*alt="([^"]+)"', block, flags=re.I)
    if not img:
        img = re.search(r'<img[^>]*alt="([^"]+)"[^>]*class="[^"]*\bbookLogo\b[^"]*"', block, flags=re.I)
    if not img:
        fail(f"BOOKMAKER_NAME_MISSING:{bookmaker_id}")
    name = html_lib.unescape(img.group(1))
    name = re.sub(r"^logo\s+100x20\s+", "", name, flags=re.I).strip()
    return {"bookmaker_id": bookmaker_id, "bookmaker_name": name}


def extract_structured_quotes(raw_html: str, cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture_market_id = str(cfg["fixture_market_id"])
    required_lines = [str(x) for x in cfg["ou_required_lines"]]
    asian_line = str(cfg["asian_handicap_line"])
    hda_sec = html_section(raw_html, "tab-1x2", "tab-asian")
    asian_sec = html_section(raw_html, "tab-asian", "tab-underover")
    ou_sec = html_section(raw_html, "tab-underover", "tab-golnogol")

    hda_summary = {
        "average": {
            "home": parse_price(id_text(hda_sec, f"q_{fixture_market_id}_1_med"), "hda.average.home"),
            "draw": parse_price(id_text(hda_sec, f"q_{fixture_market_id}_X_med"), "hda.average.draw"),
            "away": parse_price(id_text(hda_sec, f"q_{fixture_market_id}_2_med"), "hda.average.away"),
        },
        "maximum_displayed": {
            "home": parse_price(id_text(hda_sec, f"q_{fixture_market_id}_1_max"), "hda.max.home"),
            "draw": parse_price(id_text(hda_sec, f"q_{fixture_market_id}_X_max"), "hda.max.draw"),
            "away": parse_price(id_text(hda_sec, f"q_{fixture_market_id}_2_max"), "hda.max.away"),
        },
    }
    hda_books: list[dict[str, Any]] = []
    for bookmaker_id, block in bookmaker_blocks(hda_sec, fixture_market_id):
        ids = {"home": f"q_{fixture_market_id}_1_{bookmaker_id}", "draw": f"q_{fixture_market_id}_X_{bookmaker_id}", "away": f"q_{fixture_market_id}_2_{bookmaker_id}"}
        if not all(f'id="{identity}"' in block for identity in ids.values()):
            continue
        hda_books.append({**bookmaker_meta(block, bookmaker_id), "prices": {k: parse_price(id_text(block, v), f"hda.{bookmaker_id}.{k}") for k, v in ids.items()}})
    if len(hda_books) < int(cfg["hda_min_complete_bookmakers"]):
        fail("HDA_STRUCTURED_BOOKMAKER_COVERAGE_TOO_LOW")

    asian_summary = {
        "average": {
            "home": parse_price(id_text(asian_sec, f"q__{asian_line}_1_med"), "asian.average.home"),
            "away": parse_price(id_text(asian_sec, f"q__{asian_line}_2_med"), "asian.average.away"),
        },
        "maximum_displayed": {
            "home": parse_price(id_text(asian_sec, f"q__{asian_line}_1_max"), "asian.max.home"),
            "away": parse_price(id_text(asian_sec, f"q__{asian_line}_2_max"), "asian.max.away"),
        },
    }
    asian_books: list[dict[str, Any]] = []
    for bookmaker_id, block in bookmaker_blocks(asian_sec, fixture_market_id):
        i1 = f"q_{fixture_market_id}_1_{asian_line}_{bookmaker_id}"
        i2 = f"q_{fixture_market_id}_2_{asian_line}_{bookmaker_id}"
        if f'id="{i1}"' not in block or f'id="{i2}"' not in block:
            continue
        asian_books.append({**bookmaker_meta(block, bookmaker_id), "home": parse_price(id_text(block, i1), f"asian.{bookmaker_id}.home"), "away": parse_price(id_text(block, i2), f"asian.{bookmaker_id}.away")})
    if len(asian_books) < int(cfg["asian_min_complete_bookmakers"]):
        fail("ASIAN_STRUCTURED_BOOKMAKER_COVERAGE_TOO_LOW")

    ou_summary: dict[str, Any] = {}
    for line in required_lines:
        ou_summary[line] = {
            "average": {
                "under": parse_price(id_text(ou_sec, f"q__{line}_1_med"), f"ou.{line}.average.under"),
                "over": parse_price(id_text(ou_sec, f"q__{line}_2_med"), f"ou.{line}.average.over"),
            },
            "maximum_displayed": {
                "under": parse_price(id_text(ou_sec, f"q__{line}_1_max"), f"ou.{line}.max.under"),
                "over": parse_price(id_text(ou_sec, f"q__{line}_2_max"), f"ou.{line}.max.over"),
            },
        }
    ou_books: list[dict[str, Any]] = []
    per_line_counts = {line: 0 for line in required_lines}
    for bookmaker_id, block in bookmaker_blocks(ou_sec, fixture_market_id):
        lines: dict[str, Any] = {}
        for line in required_lines:
            i1 = f"q_{fixture_market_id}_1_{line}_{bookmaker_id}"
            i2 = f"q_{fixture_market_id}_2_{line}_{bookmaker_id}"
            if f'id="{i1}"' in block and f'id="{i2}"' in block:
                lines[line] = {"under": parse_price(id_text(block, i1), f"ou.{bookmaker_id}.{line}.under"), "over": parse_price(id_text(block, i2), f"ou.{bookmaker_id}.{line}.over")}
                per_line_counts[line] += 1
        if lines:
            ou_books.append({**bookmaker_meta(block, bookmaker_id), "lines": lines})
    minimum = int(cfg["ou_min_complete_bookmakers_per_line"])
    missing_structured = [line for line, count in per_line_counts.items() if count < minimum]
    if missing_structured:
        fail("OU_STRUCTURED_BOOKMAKER_COVERAGE_TOO_LOW:" + ",".join(missing_structured))

    hda_row_max = {side: max(row["prices"][side] for row in hda_books) for side in ("home", "draw", "away")}
    asian_row_max = {side: max(row[side] for row in asian_books) for side in ("home", "away")}
    ou_row_max = {line: {side: max(row["lines"][line][side] for row in ou_books if line in row["lines"]) for side in ("under", "over")} for line in required_lines}
    consistency = {
        "hda_displayed_max_equals_visible_row_max": {side: hda_summary["maximum_displayed"][side] == hda_row_max[side] for side in hda_row_max},
        "asian_displayed_max_equals_visible_row_max": {side: asian_summary["maximum_displayed"][side] == asian_row_max[side] for side in asian_row_max},
        "ou_displayed_max_equals_visible_row_max": {line: {side: ou_summary[line]["maximum_displayed"][side] == ou_row_max[line][side] for side in ("under", "over")} for line in required_lines},
    }
    warnings: list[str] = []
    if not all(consistency["hda_displayed_max_equals_visible_row_max"].values()):
        warnings.append("HDA_SOURCE_SUMMARY_MAX_DIFFERS_FROM_VISIBLE_BOOKMAKER_ROWS")
    if not all(consistency["asian_displayed_max_equals_visible_row_max"].values()):
        warnings.append("ASIAN_SOURCE_SUMMARY_MAX_DIFFERS_FROM_VISIBLE_BOOKMAKER_ROWS")
    if not all(v for line in consistency["ou_displayed_max_equals_visible_row_max"].values() for v in line.values()):
        warnings.append("OU_SOURCE_SUMMARY_MAX_DIFFERS_FROM_VISIBLE_BOOKMAKER_ROWS")

    snapshot = {
        "fixture_market_id": fixture_market_id,
        "side_semantics": {"hda": {"1": "home", "X": "draw", "2": "away"}, "asian": {"1": "home", "2": "away"}, "ou": {"1": "under", "2": "over"}},
        "hda": {"summary": hda_summary, "complete_bookmaker_count": len(hda_books), "bookmakers": hda_books},
        "asian": {"line": asian_line, "summary": asian_summary, "complete_bookmaker_count": len(asian_books), "bookmakers": asian_books},
        "ou": {"required_lines": required_lines, "summary": ou_summary, "complete_bookmaker_count_by_line": per_line_counts, "bookmakers": ou_books},
    }
    audit = {"summary_vs_visible_rows": consistency, "warnings": warnings, "bookmaker_rows_are_primary_for_structured_extraction": True}
    return snapshot, audit


def freeze_window(observed: dt.datetime, kickoff: dt.datetime, cfg: dict[str, Any]) -> dict[str, Any]:
    hours = (kickoff - observed).total_seconds() / 3600.0
    targets = cfg.get("freeze_window_targets") or []
    tolerance_minutes = float(cfg.get("freeze_window_tolerance_minutes", 15))
    ranked = sorted(targets, key=lambda x: abs(hours - float(x["hours_to_kickoff"])))
    nearest = ranked[0] if ranked else None
    delta_minutes = abs(hours - float(nearest["hours_to_kickoff"])) * 60.0 if nearest else None
    matched = bool(nearest and delta_minutes is not None and delta_minutes <= tolerance_minutes)
    return {
        "hours_to_kickoff": hours,
        "target_window_label": nearest.get("label") if matched else None,
        "nearest_target_label": nearest.get("label") if nearest else None,
        "nearest_target_delta_minutes": delta_minutes,
        "tolerance_minutes": tolerance_minutes,
        "matched_target_window": matched,
        "persistence_semantics": cfg.get("persistence_semantics"),
    }


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
    structured_cfg = (c.get("market_source") or {}).get("structured_extraction") or {}
    if structured_cfg.get("fixture_market_id") != "1402950":
        fail("STRUCTURED_FIXTURE_MARKET_ID_INVALID")

    receipt_path = base / "receipt_r44a.json"
    if not receipt_path.is_file():
        fail("BASE_RECEIPT_MISSING")
    base_receipt = json.loads(receipt_path.read_bytes())
    if base_receipt.get("schema_version") != "V520-R44A-RECEIPT-1.1":
        fail("BASE_RECEIPT_SCHEMA_MISMATCH")
    if base_receipt.get("terminal") != "PASS_R44A_SOURCE_FEASIBILITY_NOT_FORMAL_MARKET_SNAPSHOT":
        fail("BASE_SOURCE_FEASIBILITY_NOT_PASS")

    market = base_receipt.get("market_evidence") or {}
    source_url = market.get("selected_url")
    if source_url != exact.get("url"):
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
    if "arsenal" not in text.casefold() or "coventry" not in text.casefold():
        fail("FIXTURE_IDENTITY_MISSING")

    required_lines = [str(x) for x in structured_cfg["ou_required_lines"]]
    regions = extract_role_regions(text)
    audits = audit_roles(regions, required_lines)
    raw_html = raw.decode("utf-8", errors="replace")
    structured, structured_audit = extract_structured_quotes(raw_html, structured_cfg)
    observed = parse_utc(market["collector_first_observed_at_utc"])
    kickoff = parse_utc(c["fixture"]["scheduled_kickoff_utc"])
    if observed >= kickoff:
        fail("MARKET_OBSERVED_AFTER_KICKOFF")
    freeze = freeze_window(observed, kickoff, structured_cfg)

    out.mkdir(parents=True, exist_ok=False)
    structured_doc = {
        "schema_version": "V520-R44A-STRUCTURED-VISIBLE-QUOTES-1.0",
        "fixture": c["fixture"],
        "provider_group": c["market_source"]["provider_group"],
        "independent_market_count_claimed": 1,
        "source_url": source_url,
        "source_payload_sha256": digest,
        "collector_first_observed_at_utc": market["collector_first_observed_at_utc"],
        "source_native_quote_timestamp": None,
        "availability_time_semantics": "collector_first_observed_at_utc",
        "quotes": structured,
        "audit": structured_audit,
        "freeze_window": freeze,
        "bookmaker_specific_quote_identity": "VISIBLE_AGGREGATOR_ROW_WITH_BOOKMAKER_ID_AND_NAME",
        "direct_bookmaker_revalidation_performed": False,
        "formal_execution_ready": False,
        "formal_market_snapshot": False,
    }
    structured_encoded = packed(structured_doc) + b"\n"
    structured_name = "structured_visible_quotes_r44a.json"
    (out / structured_name).write_bytes(structured_encoded)
    (out / "structured_visible_quotes_r44a.sha256").write_text(sha256(structured_encoded) + "\n", encoding="ascii")

    warnings = structured_audit["warnings"]
    structured_terminal = "PASS_R44A_STRUCTURED_BOOKMAKER_QUOTES_WITH_SOURCE_SUMMARY_WARNINGS_NOT_FORMAL_SNAPSHOT" if warnings else "PASS_R44A_STRUCTURED_BOOKMAKER_QUOTES_NOT_FORMAL_SNAPSHOT"
    receipt = {
        "schema_version": "V520-R44A-EXACT-PAYLOAD-MARKET-RECEIPT-1.0",
        "terminal": "PASS_R44A_EXACT_FIXTURE_MULTI_MARKET_COVERAGE_NOT_FORMAL_SNAPSHOT",
        "structured_terminal": structured_terminal,
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
        "structured_visible_prices_extracted": True,
        "bookmaker_specific_quotes_extracted": True,
        "structured_quote_document": structured_name,
        "structured_quote_document_sha256": sha256(structured_encoded),
        "structured_extraction_warnings": warnings,
        "freeze_window": freeze,
        "freeze_window_persistence_complete": False,
        "formal_execution_ready": False,
        "reason_not_formal_snapshot": "Bookmaker-specific quotes are structurally frozen from the aggregator payload, but source-native quote timestamps, direct bookmaker revalidation, repeated target freeze-window persistence, and formal runtime admission are not yet proven.",
        "run_identity": {"github_run_id": os.getenv("GITHUB_RUN_ID"), "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"), "github_sha": os.getenv("GITHUB_SHA")},
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
        "structured_terminal": structured_terminal,
        "source_payload_sha256": digest,
        "hda_bookmakers": len(structured["hda"]["bookmakers"]),
        "asian_bookmakers": len(structured["asian"]["bookmakers"]),
        "ou_bookmakers_by_line": structured["ou"]["complete_bookmaker_count_by_line"],
        "warnings": warnings,
        "freeze_window": freeze,
        "structured_quote_sha256": sha256(structured_encoded),
        "receipt_sha256": sha256(encoded),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R44A_STRUCTURED_EXTRACT_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
