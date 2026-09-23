#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

class SkyWitnessError(RuntimeError):
    pass

HEAD_CLOSE_RE = re.compile(br"</head\s*>", re.I)
JSONLD_DATE_RE = re.compile(
    r'["\']datePublished["\']\s*:\s*["\']([^"\']+)["\']',
    re.I,
)

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyWitnessError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def host_ok(url: str, suffix: str) -> bool:
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    s = suffix.lower()
    return h == s or h.endswith("." + s)

def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()

class HeadMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.title_candidates: list[str] = []
        self.timestamp_candidates: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        d = {str(k).lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            content = normalize_text(d.get("content", ""))
            if not content:
                return
            key = (
                d.get("property")
                or d.get("name")
                or d.get("itemprop")
                or ""
            ).strip().casefold()
            if key in {"og:title", "twitter:title"}:
                self.title_candidates.append(content)
            if key in {
                "article:published_time",
                "datepublished",
                "date:published",
                "publication_date",
            }:
                self.timestamp_candidates.append({
                    "source": f"meta:{key}",
                    "value": content,
                })

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
            title = normalize_text(" ".join(self.title_parts))
            if title:
                self.title_candidates.append(title)

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

def fetch_head_only(
    url: str,
    *,
    timeout: int,
    max_head_bytes: int,
    domain_suffix: str,
) -> tuple[bytes, str, dict[str, str]]:
    req(host_ok(url, domain_suffix), "REQUEST_OUTSIDE_SOURCE_DOMAIN")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Football3-Nova-N10-SkyPublicationWitness/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=ssl.create_default_context(),
    ) as response:
        final = response.geturl()
        req(host_ok(final, domain_suffix), "REDIRECT_OUTSIDE_SOURCE_DOMAIN")
        buf = bytearray()
        while len(buf) < max_head_bytes:
            chunk = response.read(min(8192, max_head_bytes - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
            match = HEAD_CLOSE_RE.search(buf)
            if match:
                return (
                    bytes(buf[: match.end()]),
                    final,
                    {k.lower(): v for k, v in response.headers.items()},
                )
    raise SkyWitnessError("HEAD_BOUNDARY_NOT_FOUND_WITHIN_LIMIT")

def extract_metadata(head_bytes: bytes) -> dict[str, Any]:
    text = head_bytes.decode("utf-8", "replace")
    parser = HeadMetadataParser()
    parser.feed(text)
    for value in JSONLD_DATE_RE.findall(text):
        parser.timestamp_candidates.append({
            "source": "jsonld:datePublished",
            "value": normalize_text(value),
        })

    titles = []
    seen_titles = set()
    for value in parser.title_candidates:
        clean = normalize_text(value)
        if clean and clean not in seen_titles:
            seen_titles.add(clean)
            titles.append(clean)

    timestamps = []
    seen_ts = set()
    for item in parser.timestamp_candidates:
        key = (item["source"], item["value"])
        if key not in seen_ts:
            seen_ts.add(key)
            timestamps.append(item)

    return {
        "title_candidates": titles,
        "timestamp_candidates": timestamps,
    }

def parse_timestamp(value: str, timezone_name: str) -> dt.datetime | None:
    raw = normalize_text(value)
    if not raw:
        return None
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.insert(0, raw[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = dt.datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed.astimezone(ZoneInfo(timezone_name))
    return None

def local_naive(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)

def title_identity_pass(titles: list[str], required_terms: list[str]) -> bool:
    terms = [normalize_text(x).casefold() for x in required_terms]
    for title in titles:
        folded = normalize_text(title).casefold()
        if all(term in folded for term in terms):
            return True
    return False

def audit_sample(
    sample: dict[str, Any],
    source: dict[str, Any],
    acquisition: dict[str, Any],
) -> dict[str, Any]:
    raw, final, headers = fetch_head_only(
        sample["url"],
        timeout=int(acquisition["request_timeout_seconds"]),
        max_head_bytes=int(acquisition["max_head_bytes"]),
        domain_suffix=source["domain_suffix"],
    )
    metadata = extract_metadata(raw)
    identity_ok = title_identity_pass(
        metadata["title_candidates"],
        sample["required_title_terms"],
    )

    expected = local_naive(sample["expected_display_local"])
    first_fixture = local_naive(sample["first_fixture_local"])
    parsed_candidates = []
    exact_minute_candidates = []
    pre_fixture_candidates = []

    for item in metadata["timestamp_candidates"]:
        parsed = parse_timestamp(item["value"], sample["timezone"])
        record = {
            **item,
            "parsed_local": parsed.isoformat() if parsed is not None else None,
            "exact_expected_minute": False,
            "before_first_fixture": False,
        }
        if parsed is not None:
            parsed_naive = parsed.replace(tzinfo=None)
            record["exact_expected_minute"] = (
                parsed_naive.replace(second=0, microsecond=0) ==
                expected.replace(second=0, microsecond=0)
            )
            record["before_first_fixture"] = parsed_naive < first_fixture
            if record["exact_expected_minute"]:
                exact_minute_candidates.append(record)
            if record["before_first_fixture"]:
                pre_fixture_candidates.append(record)
        parsed_candidates.append(record)

    timestamp_pass = bool(exact_minute_candidates)
    pre_fixture_pass = any(
        x["exact_expected_minute"] and x["before_first_fixture"]
        for x in parsed_candidates
    )
    sample_pass = identity_ok and timestamp_pass and pre_fixture_pass

    return {
        "round": sample["round"],
        "source_url": sample["url"],
        "final_url": final,
        "head_sha256": sha256_bytes(raw),
        "head_bytes": len(raw),
        "content_type": headers.get("content-type"),
        "title_candidates": metadata["title_candidates"],
        "title_identity_pass": identity_ok,
        "expected_display_local": sample["expected_display_local"],
        "first_fixture_local": sample["first_fixture_local"],
        "timezone": sample["timezone"],
        "timestamp_candidates": parsed_candidates,
        "timestamp_exact_minute_pass": timestamp_pass,
        "publication_before_first_fixture_pass": pre_fixture_pass,
        "sample_pass": sample_pass,
        "html_body_read": False,
        "article_body_read": False,
        "referee_assignment_body_parsed": False,
    }

def run(registry: Path, out: Path) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY", "STATUS")
    req(
        p["exact_base"] == "ab3c8ec17390c2542ff62eb14ad8590c53eb5f8e",
        "EXACT_BASE",
    )
    req(p["parent"]["gdelt_source_family_closed"] is True, "GDELT_NOT_CLOSED")
    req(
        p["parent"]["canonical_aia_ledger_sha256"] ==
        "0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b",
        "LEDGER_SHA",
    )
    req([x["round"] for x in p["samples"]] == [1, 10, 19, 28, 38], "FROZEN_ROUNDS")

    source = p["source"]
    acquisition = p["acquisition_contract"]
    metadata_contract = p["metadata_contract"]
    hard = p["hard_rules"]

    req(source["html_scope"] == "HEAD_ONLY_THROUGH_FIRST_CLOSING_HEAD", "HEAD_ONLY")
    req(source["article_body_read_allowed"] is False, "NO_ARTICLE_BODY_SOURCE")
    req(
        source["page_content_assignment_parsing_allowed"] is False,
        "NO_ASSIGNMENT_BODY_SOURCE",
    )
    req(acquisition["stop_at_first_closing_head"] is True, "HEAD_STOP")
    req(acquisition["persist_raw_head"] is False, "NO_RAW_HEAD")
    req(acquisition["no_body_fallback"] is True, "NO_BODY_FALLBACK")
    req(metadata_contract["exact_minute_required"] is True, "MINUTE_REQUIRED")
    req(
        metadata_contract["publication_before_first_fixture_required"] is True,
        "PRE_FIXTURE_REQUIRED",
    )
    req(
        metadata_contract["independent_immutable_archive_witness"] is False,
        "NOT_IMMUTABLE",
    )
    req(metadata_contract["formal_available_at_proven"] is False, "NO_FORMAL_AVAILABLE_AT")

    req(hard["result_labels_read"] is False and hard["score_values_read"] is False, "ZERO_LABEL")
    req(
        hard["match_payload_read"] is False and
        hard["standings_payload_read"] is False and
        hard["player_stats_payload_read"] is False,
        "NO_SPORT_PAYLOAD",
    )
    req(hard["article_body_read"] is False, "NO_ARTICLE_BODY")
    req(hard["referee_assignment_body_parsed"] is False, "NO_ASSIGNMENT_BODY")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False, "NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(hard["candidate_weight"] == 0 and hard["matrix_delta"] == 0, "ZERO_WEIGHT")

    reports = []
    errors = []
    for sample in p["samples"]:
        try:
            reports.append(audit_sample(sample, source, acquisition))
        except Exception as exc:
            errors.append({
                "round": sample["round"],
                "source_url": sample["url"],
                "error": f"{type(exc).__name__}:{exc}"[:500],
            })

    passed_rounds = sorted(x["round"] for x in reports if x["sample_pass"])
    all_pass = len(passed_rounds) == len(p["samples"]) and not errors
    classification = (
        p["decision_contract"]["positive_classification"]
        if all_pass else p["decision_contract"]["fail_classification"]
    )
    reason = (
        "ALL_FROZEN_SKY_SAMPLES_PRESERVE_PRE_FIXTURE_MINUTE_LEVEL_PUBLICATION_METADATA"
        if all_pass else
        "SKY_PUBLICATION_METADATA_SAMPLE_CONTRACT_NOT_FULLY_SATISFIED"
    )

    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "football3-nova-n10-referee-sky-publication-witness-receipt-v1",
        "status": "N10_REFEREE_SKY_PUBLICATION_WITNESS_FEASIBILITY_COMPLETE",
        "classification": classification,
        "reason": reason,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "sample_n": len(p["samples"]),
        "report_n": len(reports),
        "error_n": len(errors),
        "reports": reports,
        "errors": errors,
        "passed_round_n": len(passed_rounds),
        "passed_rounds": passed_rounds,
        "all_samples_pass": all_pass,
        "source_family": source["source_family"],
        "source_declared_publication_timestamp": True if reports else False,
        "independent_contemporaneous_publisher": True,
        "independent_immutable_archive_witness": False,
        "formal_available_at_proven": False,
        "fixture_level_binding_complete": False,
        "html_body_read": False,
        "article_body_read": False,
        "referee_assignment_body_parsed": False,
        "match_payload_read": False,
        "standings_payload_read": False,
        "player_stats_payload_read": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "full_big5_data_ready": False,
        "referee_oof_allowed": False,
        "next_step": (
            p["decision_contract"]["next_if_positive"]
            if all_pass else p["decision_contract"]["next_if_fail"]
        ),
    }
    (out / "sky_publication_witness_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.registry, args.out)

if __name__ == "__main__":
    main()
