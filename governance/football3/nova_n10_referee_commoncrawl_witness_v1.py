#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

class CommonCrawlWitnessError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise CommonCrawlWitnessError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_iso(v: str) -> dt.datetime:
    x = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    req(x.tzinfo is not None, "TIMESTAMP_TZ_REQUIRED")
    return x.astimezone(dt.timezone.utc)

def parse_cc_timestamp(v: str) -> dt.datetime:
    return dt.datetime.strptime(v, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)

def host_ok(url: str, allowed: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "").lower() == allowed.lower()

def official_domain_ok(url: str, suffix: str) -> bool:
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    s = suffix.lower()
    return h == s or h.endswith("." + s)

def fetch(url: str, timeout: int = 25, limit: int = 2_000_000) -> tuple[bytes, str, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Football3-Nova-N10-CommonCrawlWitness/1.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as r:
        data = r.read(limit + 1)
        req(len(data) <= limit, "RESPONSE_TOO_LARGE")
        return data, r.geturl(), {k.lower(): v for k, v in r.headers.items()}

def collection_interval(row: dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    return parse_iso(str(row["from"])), parse_iso(str(row["to"]))

def collection_intersects(row: dict[str, Any], floor: str, cutoff: str) -> bool:
    start, end = collection_interval(row)
    lo, hi = parse_iso(floor), parse_iso(cutoff)
    return start < hi and end >= lo

def select_collections(rows: list[dict[str, Any]], floor: str, cutoff: str, allowed_host: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not all(k in row for k in ("id", "cdx-api", "from", "to")):
            continue
        if not host_ok(str(row["cdx-api"]), allowed_host):
            continue
        try:
            if collection_intersects(row, floor, cutoff):
                out.append(row)
        except Exception:
            continue
    return sorted(out, key=lambda x: str(x["from"]))

def build_query(endpoint: str, target_url: str) -> str:
    req(host_ok(endpoint, "index.commoncrawl.org"), "INDEX_HOST")
    qs = urllib.parse.urlencode({
        "url": target_url,
        "output": "json",
        "filter": "status:200",
    })
    return endpoint + ("&" if "?" in endpoint else "?") + qs

def parse_cdxj(raw: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            x = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(x, dict):
            out.append(x)
    return out

def eligible(rows: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    lo = parse_iso(target["official_publication_floor_utc"])
    hi = parse_iso(target["safe_cutoff_utc"])
    out = []
    for row in rows:
        ts = str(row.get("timestamp", ""))
        url = str(row.get("url", ""))
        status = str(row.get("status", ""))
        if len(ts) != 14 or not ts.isdigit():
            continue
        t = parse_cc_timestamp(ts)
        if not (lo <= t < hi):
            continue
        if status != "200":
            continue
        if not official_domain_ok(url, target["official_domain"]):
            continue
        out.append({
            "timestamp": ts,
            "url": url,
            "status": status,
            "digest": row.get("digest"),
            "mime": row.get("mime"),
            "mime_detected": row.get("mime-detected"),
            "length": row.get("length"),
            "offset": row.get("offset"),
            "filename": row.get("filename"),
        })
    return sorted(out, key=lambda x: x["timestamp"])

def run(registry: Path, out: Path, timeout: int = 25) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL", "STATUS")
    req(p["exact_base"] == "01ed3ebf13b26f00e0bdc856ae3ab53ab6a38401", "EXACT_BASE")

    h = p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False, "ZERO_LABEL")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(h["warc_content_fetch_allowed"] is False, "NO_WARC_CONTENT")
    req(h["capture_before_publication_floor_rejected"] is True, "PUBLICATION_FLOOR")
    req(h["capture_at_or_after_safe_cutoff_forbidden"] is True, "CUTOFF")
    req(h["candidate_weight"] == 0 and h["matrix_delta"] == 0, "ZERO_WEIGHT")

    target = p["target"]
    cc = p["common_crawl"]
    raw, final, headers = fetch(cc["collinfo_url"], timeout=timeout)
    req(host_ok(final, cc["allowed_host"]), "COLLINFO_REDIRECT")
    collections = json.loads(raw.decode("utf-8"))
    req(isinstance(collections, list), "COLLINFO_SHAPE")
    selected = select_collections(
        collections,
        target["official_publication_floor_utc"],
        target["safe_cutoff_utc"],
        cc["allowed_host"],
    )

    query_receipts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for collection in selected:
        for variant in target["url_variants"]:
            q = build_query(str(collection["cdx-api"]), variant)
            try:
                qraw, qfinal, qheaders = fetch(q, timeout=timeout, limit=1_000_000)
                req(host_ok(qfinal, cc["allowed_host"]), "QUERY_REDIRECT")
                rows = parse_cdxj(qraw)
                good = eligible(rows, target)
                for g in good:
                    g["collection_id"] = collection["id"]
                candidates.extend(good)
                query_receipts.append({
                    "collection_id": collection["id"],
                    "collection_from": collection["from"],
                    "collection_to": collection["to"],
                    "target_variant": variant,
                    "query_url": q,
                    "final_url": qfinal,
                    "response_sha256": sha256_bytes(qraw),
                    "row_n": len(rows),
                    "eligible_n": len(good),
                    "content_type": qheaders.get("content-type"),
                })
            except Exception as e:
                errors.append({
                    "collection_id": collection["id"],
                    "target_variant": variant,
                    "error": f"{type(e).__name__}:{e}"[:400],
                })

    uniq: dict[tuple[Any, ...], dict[str, Any]] = {}
    for c in candidates:
        key = (c.get("timestamp"), c.get("url"), c.get("digest"), c.get("collection_id"))
        uniq[key] = c
    candidates = sorted(uniq.values(), key=lambda x: x["timestamp"])
    selected_capture = candidates[0] if candidates else None

    if selected_capture is not None:
        req(parse_iso(target["official_publication_floor_utc"]) <= parse_cc_timestamp(selected_capture["timestamp"]), "CAPTURE_BEFORE_PUBLICATION")
        req(parse_cc_timestamp(selected_capture["timestamp"]) < parse_iso(target["safe_cutoff_utc"]), "CAPTURE_AFTER_CUTOFF")

    classification = "DATA_COVERAGE_FEASIBILITY_PASS" if selected_capture else "STOP_DATA_COVERAGE"
    reason = (
        "ELIGIBLE_COMMON_CRAWL_CAPTURE_FOUND"
        if selected_capture else
        ("NO_COMMON_CRAWL_COLLECTION_OVERLAPS_PIT_WINDOW" if not selected else "NO_ELIGIBLE_CAPTURE_IN_OVERLAPPING_COLLECTIONS")
    )

    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "football3-nova-n10-referee-commoncrawl-witness-receipt-v1",
        "status": "N10_REFEREE_COMMONCRAWL_WITNESS_AUDIT_COMPLETE",
        "classification": classification,
        "reason": reason,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "collinfo_sha256": sha256_bytes(raw),
        "collinfo_final_url": final,
        "collinfo_content_type": headers.get("content-type"),
        "selected_collection_n": len(selected),
        "selected_collections": [
            {"id": x["id"], "from": x["from"], "to": x["to"], "cdx-api": x["cdx-api"]}
            for x in selected
        ],
        "query_receipt_n": len(query_receipts),
        "query_receipts": query_receipts,
        "query_errors": errors,
        "eligible_capture_n": len(candidates),
        "eligible_captures": candidates,
        "selected_capture": selected_capture,
        "warc_content_fetched": False,
        "full_big5_data_ready": False,
        "referee_oof_allowed": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "next_step": (
            "IF_PASS_FREEZE_CAPTURE_METADATA_THEN_SEPARATELY_DESIGN_CONTENT_VALIDATION_WITHOUT_RESULTS"
            if selected_capture else
            "STOP_COMMON_CRAWL_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_PUBLICATION_METADATA_SOURCE; DO_NOT_START_REFEREE_OOF"
        ),
    }
    (out / "commoncrawl_witness_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True))
    return receipt

def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--registry", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--timeout", type=int, default=25)
    x = a.parse_args()
    run(x.registry, x.out, x.timeout)

if __name__ == "__main__":
    main()
