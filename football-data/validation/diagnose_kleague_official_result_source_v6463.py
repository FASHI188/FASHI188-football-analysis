#!/usr/bin/env python3
"""V6.46.3 diagnose K League official result HTML for a resolver fallback.

Research engineering only. This script does NOT settle results. It inspects the official
K LEAGUE attendance-detail page structure and tries conservative pagination/page-size
parameter variants so we can identify a stable official score surface instead of silently
falling back to a third-party scoreboard.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_kleague_official_result_source_diag_v6463_status.json"
BASE = "https://www.kleague.com/record/audienceDetail.do"
TARGETS = [
    ("2026/07/25", "김천", "대전"),
    ("2026/07/25", "포항", "전북"),
    ("2026/07/26", "안양", "강원"),
    ("2026/07/26", "광주", "제주"),
    ("2026/07/26", "인천", "부천"),
    ("2026/07/26", "서울", "울산"),
]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(url: str, data: bytes | None = None) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FASHI188-football-audit/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        ctype = response.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(ctype)
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return int(response.status), response.geturl(), text


def textify(raw: str) -> str:
    raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def actual_table_matches(raw: str) -> list[dict[str, object]]:
    rows = [textify(x) for x in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, flags=re.I | re.S)]
    out = []
    for date, home, away in TARGETS:
        candidates = []
        for row in rows:
            if date in row and home in row and away in row:
                score = re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)", row)
                candidates.append({
                    "row_text": row[:1200],
                    "score": [int(score.group(1)), int(score.group(2))] if score else None,
                })
        out.append({"date": date, "home": home, "away": away, "row_candidates": candidates})
    return out


def script_contexts(raw: str) -> list[str]:
    out = []
    for token in ("goToPageAudience", "pageBegin", "pageEnd", "audienceDetail", "300건", "300"):
        pos = 0
        while True:
            idx = raw.find(token, pos)
            if idx < 0:
                break
            start, stop = max(0, idx - 500), min(len(raw), idx + 900)
            snippet = re.sub(r"\s+", " ", html.unescape(raw[start:stop])).strip()
            if snippet not in out:
                out.append(snippet)
            pos = idx + len(token)
            if len(out) >= 30:
                break
        if len(out) >= 30:
            break
    return out[:30]


def page_meta(raw: str) -> dict[str, object]:
    forms = []
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", raw, flags=re.I | re.S):
        attrs, body = m.group(1), m.group(2)
        action = re.search(r"action=[\"']([^\"']*)", attrs, flags=re.I)
        method = re.search(r"method=[\"']([^\"']*)", attrs, flags=re.I)
        inputs = []
        for im in re.finditer(r"<(?:input|select)\b([^>]*)>", body, flags=re.I | re.S):
            ia = im.group(1)
            name = re.search(r"name=[\"']([^\"']+)", ia, flags=re.I)
            value = re.search(r"value=[\"']([^\"']*)", ia, flags=re.I)
            if name:
                inputs.append({"name": name.group(1), "value": value.group(1) if value else None})
        forms.append({"action": action.group(1) if action else None, "method": (method.group(1) if method else "GET").upper(), "inputs": inputs})
    hrefs = sorted(set(html.unescape(x) for x in re.findall(r"href=[\"']([^\"']+)", raw, flags=re.I)))
    page_hrefs = [h for h in hrefs if any(t in h.lower() for t in ("page", "audience"))][:100]
    scripts = sorted(set(re.findall(r"(?:fn_|go|move|search|page)[A-Za-z0-9_]{1,40}", raw, flags=re.I)))[:100]
    return {
        "forms": forms[:20],
        "page_hrefs": page_hrefs,
        "script_identifiers": scripts,
        "script_contexts": script_contexts(raw),
    }


def attempt(label: str, method: str, params: dict[str, str] | None) -> dict[str, object]:
    try:
        if method == "GET":
            url = BASE if not params else BASE + "?" + urllib.parse.urlencode(params)
            status, final_url, raw = fetch(url)
        else:
            url = BASE
            body = urllib.parse.urlencode(params or {}).encode("utf-8")
            status, final_url, raw = fetch(url, body)
        found = actual_table_matches(raw)
        found_count = sum(bool(x["row_candidates"]) for x in found)
        return {
            "label": label,
            "method": method,
            "params": params,
            "status": status,
            "final_url": final_url,
            "bytes": len(raw.encode("utf-8", errors="ignore")),
            "target_table_rows": found_count,
            "matches": [x for x in found if x["row_candidates"]],
            "raw": raw,
        }
    except Exception as exc:
        return {"label": label, "method": method, "params": params, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    variants: list[tuple[str, str, dict[str, str] | None]] = [("base", "GET", None)]
    for param in ("page", "pageNo", "pageIndex", "currentPage", "pageNum", "pageBegin"):
        for n in range(1, 9):
            variants.append((f"get_{param}_{n}", "GET", {param: str(n), "year": "2026", "leagueId": "1"}))
    for param in ("page", "pageNo", "pageIndex", "currentPage", "pageNum", "pageBegin"):
        for n in range(1, 6):
            variants.append((f"post_{param}_{n}", "POST", {param: str(n), "year": "2026", "leagueId": "1"}))
    for param in ("pageSize", "dataCount", "recordCountPerPage", "perPage", "listSize", "rowPerPage"):
        variants.append((f"get_{param}_300", "GET", {param: "300", "year": "2026", "leagueId": "1"}))
        variants.append((f"post_{param}_300", "POST", {param: "300", "year": "2026", "leagueId": "1"}))

    attempts = []
    raw_base = ""
    for label, method, params in variants:
        item = attempt(label, method, params)
        raw = item.pop("raw", "")
        if label == "base":
            raw_base = str(raw)
        attempts.append(item)

    best = sorted(attempts, key=lambda x: int(x.get("target_table_rows") or 0), reverse=True)[:30]
    payload = {
        "schema_version": "V6.46.3-kleague-official-result-source-diag-r2",
        "generated_at_utc": now(),
        "status": "PASS_DIAGNOSTIC",
        "official_source": BASE,
        "base_page_meta": page_meta(raw_base) if raw_base else {},
        "best_attempts": best,
        "attempt_count": len(attempts),
        "governance": {
            "settlement_written": False,
            "third_party_result_fallback_used": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
