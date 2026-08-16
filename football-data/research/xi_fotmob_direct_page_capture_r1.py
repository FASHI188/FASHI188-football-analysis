#!/usr/bin/env python3
"""Zero-label direct FotMob target-page capture probe.

Purpose:
- decouple the FotMob context surface from market-fixture discovery;
- bind only to the already frozen 2026/27 Premier League MW1 fixture manifest;
- preserve raw target match pages with source URL, observation timestamps, and SHA-256;
- inventory availability/lineup-related page markers without claiming a confirmed XI.

This is an infrastructure/capability probe only. It does not read result labels,
train, score, change probabilities, or mutate formal assets.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "research" / "eng_pl_2026_27_mw1_fotmob_match_pages_freeze_20260816.json"
OUT = ROOT / "research" / "artifacts" / "xi_fotmob_direct_page_capture_r1"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
AVAIL_MARKERS = ("injur", "suspend", "unavail", "absence", "absent", "doubt")
LINEUP_MARKERS = ("lineup", "formation", "starting", "starter")
PREDICT_MARKERS = ("predict", "expected", "probable", "projected")
ACTUAL_MARKERS = ("confirmed", "actual lineup", "starting lineup")


class ProbeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ProbeError(f"{path}: expected JSON object")
    return obj


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", html_lib.unescape(str(value or ""))).casefold()
    chars: list[str] = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        chars.append(ch if ch.isalnum() else " ")
    return " ".join("".join(chars).split())


def fetch(url: str) -> dict[str, Any]:
    observed = utc_now()
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        },
    )
    with urlopen(req, timeout=30) as resp:  # nosec B310 - frozen HTTPS FotMob URLs only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        final_url = str(resp.geturl())
        content_type = str(resp.headers.get("content-type") or "")
    retrieved = utc_now()
    if status != 200:
        raise ProbeError(f"HTTP {status}: {url}")
    return {
        "requested_url": url,
        "final_url": final_url,
        "observed_at_utc": observed,
        "retrieved_at_utc": retrieved,
        "content_type": content_type,
        "raw": raw,
    }


def extract_next_data(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    text = raw.decode("utf-8", errors="replace")
    # Legacy/pages-router Next.js surface used by the prior repository collector.
    m = re.search(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m:
        try:
            obj = json.loads(html_lib.unescape(m.group(1)))
            if isinstance(obj, dict):
                return obj, "__NEXT_DATA__"
        except Exception:
            return None, "__NEXT_DATA___PARSE_ERROR"
    # Modern app-router pages can expose React Flight payloads instead. We do not
    # decode them here; presence is recorded so a later extractor can be versioned.
    if "self.__next_f.push" in text.casefold():
        return None, "NEXT_FLIGHT_PRESENT"
    return None, None


def marker_counts(text: str, markers: tuple[str, ...]) -> dict[str, int]:
    low = text.casefold()
    return {m: low.count(m) for m in markers}


def main() -> int:
    manifest = load_json(MANIFEST)
    if manifest.get("research_only") is not True or manifest.get("label_access") is not False:
        raise ProbeError("manifest must keep research_only=true and label_access=false")
    rows = manifest.get("fixtures")
    if not isinstance(rows, list) or len(rows) != 10:
        raise ProbeError("manifest must contain exactly 10 target fixtures")
    if any(not isinstance(x, dict) or x.get("identity_verified") is not True for x in rows):
        raise ProbeError("all target page identities must be pre-verified")

    OUT.mkdir(parents=True, exist_ok=True)
    raw_dir = OUT / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    page_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for row in rows:
        match_id = str(row.get("match_id") or "")
        url = str(row.get("page_url") or "")
        home = str(row.get("home_team") or "")
        away = str(row.get("away_team") or "")
        if not match_id or not url.startswith("https://www.fotmob.com/"):
            errors.append(f"INVALID_MANIFEST_ROW:{match_id or 'unknown'}")
            continue
        try:
            item = fetch(url)
            raw = item.pop("raw")
            raw_path = raw_dir / f"{match_id}.html"
            raw_path.write_bytes(raw)
            decoded = html_lib.unescape(raw.decode("utf-8", errors="replace"))
            normalized = norm(decoded)
            home_ok = norm(home) in normalized
            away_ok = norm(away) in normalized
            next_obj, next_surface = extract_next_data(raw)
            next_sha = None
            if next_obj is not None:
                next_raw = json.dumps(next_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                next_sha = sha256(next_raw)
            page = {
                "match_id": match_id,
                "kickoff_at_utc": row.get("kickoff_at_utc"),
                "home_team": home,
                "away_team": away,
                **item,
                "raw_path": str(raw_path.relative_to(ROOT)),
                "raw_sha256": sha256(raw),
                "raw_bytes": len(raw),
                "identity_home_token_present": home_ok,
                "identity_away_token_present": away_ok,
                "next_surface": next_surface,
                "next_data_parsed": next_obj is not None,
                "next_data_canonical_sha256": next_sha,
                "availability_marker_counts": marker_counts(decoded, AVAIL_MARKERS),
                "lineup_marker_counts": marker_counts(decoded, LINEUP_MARKERS),
                "prediction_marker_counts": marker_counts(decoded, PREDICT_MARKERS),
                "actual_xi_marker_counts": marker_counts(decoded, ACTUAL_MARKERS),
                "confirmed_xi_claim": False,
            }
            if not (home_ok and away_ok):
                errors.append(f"TARGET_IDENTITY_TEXT_MISSING:{match_id}:home={home_ok}:away={away_ok}")
            page_rows.append(page)
        except Exception as exc:
            errors.append(f"FETCH_OR_PARSE:{match_id}:{type(exc).__name__}:{exc}")

    fetched = len(page_rows)
    all_identity = fetched == 10 and all(
        x["identity_home_token_present"] and x["identity_away_token_present"] for x in page_rows
    )
    next_surface_count = sum(1 for x in page_rows if x.get("next_surface"))
    next_parsed_count = sum(1 for x in page_rows if x.get("next_data_parsed"))
    pages_with_availability_markers = sum(
        1 for x in page_rows if sum((x.get("availability_marker_counts") or {}).values()) > 0
    )
    pages_with_lineup_markers = sum(
        1 for x in page_rows if sum((x.get("lineup_marker_counts") or {}).values()) > 0
    )

    status: dict[str, Any] = {
        "schema_version": "xi-fotmob-direct-page-capture-r1",
        "research_only": True,
        "label_access": False,
        "scientific_claim": "NONE",
        "formal_weight": 0,
        "generated_at_utc": utc_now(),
        "manifest_path": str(MANIFEST.relative_to(ROOT)),
        "target_fixture_count": 10,
        "fetched_page_count": fetched,
        "identity_bound_page_count": sum(
            1 for x in page_rows if x["identity_home_token_present"] and x["identity_away_token_present"]
        ),
        "next_surface_page_count": next_surface_count,
        "next_data_parsed_page_count": next_parsed_count,
        "pages_with_availability_markers": pages_with_availability_markers,
        "pages_with_lineup_markers": pages_with_lineup_markers,
        "errors": errors,
        "confirmed_xi_claim": False,
        "page_rows": page_rows,
        "verdict": (
            "PASS_DIRECT_FOTMOB_PAGE_CAPTURE_INFRASTRUCTURE_ONLY"
            if fetched == 10 and all_identity and not errors
            else "STOP_DIRECT_FOTMOB_PAGE_CAPTURE"
        ),
        "interpretation": (
            "PASS means only that all frozen target match pages can be fetched and identity-bound before kickoff. Marker counts are structural inventory, not player availability truth or confirmed-XI evidence."
        ),
        "next_if_pass": (
            "Version the raw-page context extractor. Keep predicted XI separate from actual confirmed XI; actual XI requires exactly 11 unique starters per team and must only be frozen near lineup release."
        ),
    }
    (OUT / "capture_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "verdict": status["verdict"],
        "fetched_page_count": fetched,
        "identity_bound_page_count": status["identity_bound_page_count"],
        "next_surface_page_count": next_surface_count,
        "next_data_parsed_page_count": next_parsed_count,
        "pages_with_availability_markers": pages_with_availability_markers,
        "pages_with_lineup_markers": pages_with_lineup_markers,
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0 if status["verdict"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
