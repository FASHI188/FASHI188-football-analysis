#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import ssl
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

class ArquivoTextSearchError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise ArquivoTextSearchError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_iso(v: str) -> dt.datetime:
    x = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    req(x.tzinfo is not None, "TIMESTAMP_TZ_REQUIRED")
    return x.astimezone(dt.timezone.utc)

def parse_capture(v: str) -> dt.datetime | None:
    s = (v or "").strip()
    if len(s) == 14 and s.isdigit():
        return dt.datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    if len(s) >= 10:
        try:
            return parse_iso(s if "T" in s else s[:10] + "T00:00:00+00:00")
        except Exception:
            return None
    return None

def compact_ts(v: str) -> str:
    return parse_iso(v).strftime("%Y%m%d%H%M%S")

def host_ok(url: str, allowed_host: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "").lower() == allowed_host.lower()

def official_domain_ok(url: str, suffix: str) -> bool:
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    s = suffix.lower()
    return h == s or h.endswith("." + s)

def fold_text(s: str) -> str:
    x = unicodedata.normalize("NFKD", s or "")
    x = "".join(ch for ch in x if not unicodedata.combining(ch))
    return " ".join(x.casefold().split())

def title_identity_ok(title: str) -> bool:
    t = fold_text(title)
    return "serie a tim" in t and "designazion" in t and "10" in t and "giornata" in t

def fetch(url: str, timeout: int = 25, limit: int = 2_000_000) -> tuple[bytes, str, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Football3-Nova-N10-ArquivoTextSearch/1.0",
            "Accept": "application/json,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as r:
        data = r.read(limit + 1)
        req(len(data) <= limit, "RESPONSE_TOO_LARGE")
        return data, r.geturl(), {k.lower(): v for k, v in r.headers.items()}

def build_query(endpoint: str, q: str, floor: str, cutoff: str, max_items: int) -> str:
    params = {
        "q": q,
        "from": compact_ts(floor),
        "to": (parse_iso(cutoff) - dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S"),
        "maxItems": str(max_items),
        "prettyPrint": "false",
    }
    return endpoint + "?" + urllib.parse.urlencode(params)

def response_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("response_items", "responseItems", "items", "results"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return []

def item_timestamp(item: dict[str, Any]) -> tuple[str | None, dt.datetime | None]:
    for key in ("timestamp", "date", "captureDate", "capture_date"):
        value = item.get(key)
        if value is None:
            continue
        raw = str(value)
        parsed = parse_capture(raw)
        if parsed is not None:
            return raw, parsed
    return None, None

def item_url(item: dict[str, Any]) -> str:
    for key in ("originalURL", "originalUrl", "original_url", "url"):
        v = item.get(key)
        if isinstance(v, str) and v:
            return v
    return ""

def item_title(item: dict[str, Any]) -> str:
    for key in ("title", "pageTitle", "page_title"):
        v = item.get(key)
        if isinstance(v, str):
            return v
    return ""

def normalize_candidate(item: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
    original = item_url(item)
    title = item_title(item)
    raw_ts, captured = item_timestamp(item)
    if not original or captured is None:
        return None
    if not official_domain_ok(original, target["official_domain"]):
        return None
    if not title_identity_ok(title):
        return None

    lo = parse_iso(target["official_publication_floor_utc"])
    hi = parse_iso(target["safe_cutoff_utc"])
    if not (lo <= captured < hi):
        return None

    out = {
        "timestamp_raw": raw_ts,
        "capture_at_utc": captured.isoformat(),
        "original_url": original,
        "title": title,
        "digest": item.get("digest"),
        "mime_type": item.get("mimeType") or item.get("mime_type") or item.get("mime"),
        "content_length": item.get("contentLength") or item.get("content_length"),
        "link_to_archive": item.get("linkToArchive") or item.get("link_to_archive"),
        "link_to_original_file": item.get("linkToOriginalFile") or item.get("link_to_original_file"),
    }
    return out

def run(registry: Path, out: Path, timeout: int = 25) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL", "STATUS")
    req(p["exact_base"] == "4a1fc3ace3e51ea7d967fb0f07977ff5af546a38", "EXACT_BASE")

    h = p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False, "ZERO_LABEL")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(h["archive_content_fetch_allowed"] is False, "NO_ARCHIVE_CONTENT")
    req(h["capture_before_publication_floor_rejected"] is True, "PUBLICATION_FLOOR")
    req(h["capture_at_or_after_safe_cutoff_forbidden"] is True, "SAFE_CUTOFF")
    req(h["official_domain_required"] is True, "OFFICIAL_DOMAIN")
    req(h["candidate_weight"] == 0 and h["matrix_delta"] == 0, "ZERO_WEIGHT")

    target = p["target"]
    arq = p["arquivo"]
    req(arq["provider"] == "Arquivo.pt Full-text Search API", "PROVIDER")
    req(arq["body_or_archive_content_fetch_allowed"] is False, "NO_CONTENT")
    req(host_ok(arq["endpoint"], arq["allowed_host"]), "ENDPOINT_HOST")

    receipts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for q in arq["query_variants"]:
        url = build_query(
            arq["endpoint"], q,
            target["official_publication_floor_utc"],
            target["safe_cutoff_utc"],
            int(arq["max_items"]),
        )
        try:
            raw, final, headers = fetch(url, timeout=timeout)
            req(host_ok(final, arq["allowed_host"]), "REDIRECT_OUTSIDE_ARQUIVO")
            payload = json.loads(raw.decode("utf-8"))
            rows = response_items(payload)
            good = []
            for item in rows:
                c = normalize_candidate(item, target)
                if c is not None:
                    c["query_variant"] = q
                    good.append(c)
                    candidates.append(c)
            receipts.append({
                "query_variant": q,
                "query_url": url,
                "final_url": final,
                "response_sha256": sha256_bytes(raw),
                "content_type": headers.get("content-type"),
                "response_item_n": len(rows),
                "eligible_n": len(good),
                "estimated_results": payload.get("estimated_results") if isinstance(payload, dict) else None,
            })
        except Exception as e:
            errors.append({
                "query_variant": q,
                "error": f"{type(e).__name__}:{e}"[:400],
            })

    uniq: dict[tuple[Any, ...], dict[str, Any]] = {}
    for c in candidates:
        key = (c["capture_at_utc"], c["original_url"], c.get("digest"))
        uniq[key] = c
    candidates = sorted(uniq.values(), key=lambda x: x["capture_at_utc"])
    selected = candidates[0] if candidates else None

    classification = "DATA_COVERAGE_FEASIBILITY_PASS" if selected else "STOP_DATA_COVERAGE"
    reason = "ELIGIBLE_ALTERNATE_OFFICIAL_URL_CAPTURE_FOUND" if selected else "NO_ELIGIBLE_FULLTEXT_CAPTURE_IN_FROZEN_WINDOW"

    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "football3-nova-n10-referee-arquivo-textsearch-receipt-v1",
        "status": "N10_REFEREE_ARQUIVO_TEXTSEARCH_AUDIT_COMPLETE",
        "classification": classification,
        "reason": reason,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "provider": arq["provider"],
        "query_receipt_n": len(receipts),
        "query_receipts": receipts,
        "query_errors": errors,
        "eligible_capture_n": len(candidates),
        "eligible_captures": candidates,
        "selected_capture": selected,
        "archive_content_fetched": False,
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
            "IF_PASS_FREEZE_DISCOVERED_OFFICIAL_URL_AND_OPEN_SEPARATE_ZERO_LABEL_METADATA_VALIDATION_BATCH"
            if selected else
            "STOP_ARQUIVO_FULLTEXT_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE; DO_NOT_START_REFEREE_OOF"
        ),
    }
    (out / "arquivo_textsearch_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
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
