#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, re, ssl, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Any

class CollectorError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise CollectorError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())

UA = "Football3-Nova-ZeroLabel-ArchiveCollector/1.0 (+public official archive metadata only)"
DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.]([0-2]?\d|3[01])(?!\d)")
DMY_RE = re.compile(r"(?<!\d)([0-2]?\d|3[01])[/.-](0?[1-9]|1[0-2])[/.-](20\d{2})(?!\d)")
TAG_RE = re.compile(r"<[^>]+>", re.S)
SPACE_RE = re.compile(r"\s+")
META_PUB_RE = re.compile(
    r'''(?:property|name)\s*=\s*["'](?:article:published_time|datePublished|datepublished)["'][^>]*content\s*=\s*["']([^"']+)["']|
        content\s*=\s*["']([^"']+)["'][^>]*(?:property|name)\s*=\s*["'](?:article:published_time|datePublished|datepublished)["']''',
    re.I | re.X,
)
TIME_RE = re.compile(r"<time\b[^>]*datetime\s*=\s*[\"']([^\"']+)[\"']", re.I)
JSONLD_DATE_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I)
TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)

def fetch(url: str, timeout: int = 25) -> tuple[bytes, dict[str, str], str]:
    parsed = urllib.parse.urlparse(url)
    req(parsed.scheme == "https", "HTTPS_ONLY:" + url)
    rq = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(rq, timeout=timeout, context=ctx) as r:
        body = r.read()
        headers = {k.lower(): v for k, v in r.headers.items()}
        final = r.geturl()
    return body, headers, final

def textify(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    s = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", s, flags=re.I | re.S)
    return SPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", s))).strip()

def title_of(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    m = H1_RE.search(s) or TITLE_RE.search(s)
    return SPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", m.group(1)))).strip() if m else ""

def normalize_datetime(v: str) -> tuple[str | None, str]:
    v = unescape(v).strip()
    if not v:
        return None, "MISSING"
    z = v.replace("Z", "+00:00")
    try:
        x = dt.datetime.fromisoformat(z)
        if x.tzinfo is None:
            return x.isoformat(), "DATETIME_NO_TZ"
        return x.isoformat(), "DATETIME_TZ"
    except ValueError:
        pass
    try:
        d = dt.date.fromisoformat(v[:10])
        return d.isoformat(), "DATE_ONLY"
    except ValueError:
        pass
    m = DMY_RE.search(v)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat(), "DATE_ONLY"
    m = DATE_RE.search(v)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), "DATE_ONLY"
    return None, "UNPARSED"

def publication_of(raw: bytes) -> tuple[str | None, str, str | None]:
    s = raw.decode("utf-8", "replace")
    vals = []
    for m in META_PUB_RE.finditer(s):
        vals.append((m.group(1) or m.group(2), "META"))
    vals += [(m.group(1), "TIME") for m in TIME_RE.finditer(s)]
    vals += [(m.group(1), "JSONLD") for m in JSONLD_DATE_RE.finditer(s)]
    for v, src in vals:
        norm, precision = normalize_datetime(v)
        if norm:
            return norm, precision, src
    t = textify(raw)
    m = DMY_RE.search(t)
    if m:
        d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return d.isoformat(), "DATE_ONLY", "BODY_TEXT"
    m = DATE_RE.search(t)
    if m:
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.isoformat(), "DATE_ONLY", "BODY_TEXT"
    return None, "MISSING", None

def parse_sitemap(raw: bytes) -> tuple[str, list[str]]:
    root = ET.fromstring(raw)
    kind = root.tag.rsplit("}", 1)[-1]
    locs = []
    for e in root.iter():
        if e.tag.rsplit("}", 1)[-1] == "loc" and e.text:
            locs.append(e.text.strip())
    return kind, locs

def domain_ok(url: str, suffix: str) -> bool:
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    s = suffix.lower()
    return h == s or h.endswith("." + s)

def matches_url(url: str, pats: list[str]) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return any(re.search(p, path, re.I) for p in pats)

def round_no(title: str, text: str, pats: list[str]) -> int | None:
    for src in (title, text[:1500]):
        for p in pats:
            m = re.search(p, src, re.I)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 60:
                    return n
    return None

def in_target(pub: str | None, start: str, end: str) -> bool:
    if not pub:
        return False
    d = pub[:10]
    return start <= d <= end

def discover_urls(cfg: dict[str, Any], timeout: int, max_sitemaps: int = 80) -> tuple[list[str], list[dict[str, Any]]]:
    found = set(cfg.get("seed_urls", []))
    errors = []
    q = list(cfg.get("sitemap_urls", []))
    seen = set()
    while q and len(seen) < max_sitemaps:
        u = q.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            raw, _, _ = fetch(u, timeout)
            kind, locs = parse_sitemap(raw)
            if kind == "sitemapindex":
                for x in locs:
                    if domain_ok(x, cfg["domain_suffix"]) and x not in seen:
                        q.append(x)
            else:
                for x in locs:
                    if domain_ok(x, cfg["domain_suffix"]) and matches_url(x, cfg["url_patterns"]):
                        found.add(x)
        except Exception as e:
            errors.append({"url": u, "stage": "sitemap", "error": type(e).__name__ + ":" + str(e)[:240]})
    return sorted(found), errors

def page_record(cfg: dict[str, Any], url: str, timeout: int, retrieved_at: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        raw, headers, final = fetch(url, timeout)
    except Exception as e:
        return None, {"url": url, "stage": "page", "error": type(e).__name__ + ":" + str(e)[:240]}
    if not domain_ok(final, cfg["domain_suffix"]):
        return None, {"url": url, "stage": "redirect", "error": "REDIRECT_OUTSIDE_OFFICIAL_DOMAIN", "final_url": final}
    txt = textify(raw)
    low = txt.lower()
    if cfg.get("required_text_any") and not any(x.lower() in low for x in cfg["required_text_any"]):
        return None, {"url": url, "stage": "content_filter", "error": "REQUIRED_TEXT_NOT_FOUND"}
    pub, precision, source = publication_of(raw)
    title = title_of(raw)
    rnd = round_no(title, txt, cfg.get("round_patterns", []))
    return {
        "competition": cfg["competition"],
        "source_id": cfg["source_id"],
        "authority": cfg["authority"],
        "source_url": url,
        "final_url": final,
        "retrieved_at": retrieved_at,
        "published_at": pub,
        "publication_precision": precision,
        "publication_evidence": source,
        "round": rnd,
        "title": title,
        "content_sha256": sha256_bytes(raw),
        "content_bytes": len(raw),
        "http_last_modified": headers.get("last-modified"),
        "etag": headers.get("etag"),
        "result_labels_read": 0,
        "score_values_read": 0,
    }, None

def build(registry: Path, out: Path, timeout: int = 25) -> dict[str, Any]:
    p = json.loads(registry.read_text())
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL", "STATUS")
    req(p["exact_base"] == "b201d3476b22f6fd2e80378990d83dfa6320b7da", "EXACT_BASE")
    h = p["hard_rules"]
    req(h["target_result_labels_read"] is False and h["score_values_read"] is False, "NO_LABELS")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["official_public_sources_only"] is True and h["paid_or_secret_source_allowed"] is False, "PUBLIC_ONLY")
    s = p["safety"]
    req(s["result_labels_read"] == 0 and s["score_values_read"] == 0 and not s["training_performed"] and not s["scoring_performed"], "SAFETY")

    out.mkdir(parents=True, exist_ok=True)
    retrieved_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    all_rows = []
    source_reports = []
    gap = list(p["gap_receipts"])
    target = p["target"]
    expected = set(range(1, int(target["expected_rounds"]) + 1))

    for cfg in p["sources"]:
        urls, errs = discover_urls(cfg, timeout)
        rows = []
        for u in urls:
            rec, err = page_record(cfg, u, timeout, retrieved_at)
            if err:
                errs.append(err)
                continue
            if rec and in_target(rec["published_at"], target["season_start"], target["season_end"]):
                rows.append(rec)
        uniq = {}
        for r in rows:
            key = (r["round"], r["content_sha256"])
            uniq[key] = r
        rows = sorted(uniq.values(), key=lambda x: (999 if x["round"] is None else x["round"], x["source_url"]))
        rounds = sorted({r["round"] for r in rows if isinstance(r["round"], int) and r["round"] in expected})
        missing = sorted(expected - set(rounds))
        report = {
            "competition": cfg["competition"],
            "source_id": cfg["source_id"],
            "candidate_url_n": len(urls),
            "accepted_page_n": len(rows),
            "rounds_found": rounds,
            "missing_rounds": missing,
            "round_inventory_complete": len(missing) == 0,
            "publication_missing_n": sum(r["published_at"] is None for r in rows),
            "date_only_n": sum(r["publication_precision"] == "DATE_ONLY" for r in rows),
            "fetch_error_n": len(errs),
            "fetch_errors": errs[:100],
            "fixture_level_inventory_complete": False,
            "classification": "PAGE_INVENTORY_COMPLETE_FIXTURE_BINDING_PENDING" if len(missing) == 0 else "STOP_DATA_COVERAGE",
        }
        source_reports.append(report)
        all_rows.extend(rows)

    inv = out / "official_page_inventory.jsonl"
    inv.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in all_rows))
    (out / "gap_receipts.json").write_text(json.dumps(gap, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    inventory_complete = all(x["round_inventory_complete"] for x in source_reports)
    receipt = {
        "schema_version": "football3-nova-n10-referee-official-archive-receipt-v1",
        "status": "N10_REFEREE_OFFICIAL_ARCHIVE_COLLECTOR_COMPLETE",
        "classification": "STOP_DATA_COVERAGE",
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_file(registry),
        "retrieved_at": retrieved_at,
        "official_page_row_n": len(all_rows),
        "source_reports": source_reports,
        "inventory_competitions": target["inventory_competitions"],
        "page_round_inventory_complete_for_three": inventory_complete,
        "fixture_level_inventory_complete_for_three": False,
        "gap_only_competitions": target["gap_only_competitions"],
        "gap_receipt_n": len(gap),
        "full_big5_data_ready": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "inventory_sha256": sha256_file(inv),
        "next_step": "COMPLETE_FIXTURE_LEVEL_BINDING_FOR_EPL_LA_LIGA_SERIE_A_AND_CONTINUE_BUNDESLIGA_LIGUE1_AVAILABLE_AT_DISCOVERY; DO_NOT_START_REFEREE_OOF",
    }
    (out / "collector_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt

def main():
    a = argparse.ArgumentParser()
    a.add_argument("--registry", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--timeout", type=int, default=25)
    x = a.parse_args()
    build(x.registry, x.out, x.timeout)

if __name__ == "__main__":
    main()
