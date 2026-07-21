#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "direct_marathonbet_html_probe_v5516_status.json"
EVIDENCE_ROOT = ROOT / "evidence" / "direct_provider_probes" / "marathonbet"
USER_AGENT = "Mozilla/5.0 (compatible; football-pit-research/5.5.16; +https://github.com/FASHI188/FASHI188-football-analysis)"

TARGETS = [
    {
        "competition_id": "POR_PrimeiraLiga",
        "home_team": "Estoril Praia",
        "away_team": "FC Famalicão",
        "kickoff_utc": "2026-08-07T19:15:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/Portugal/Primeira%2BLiga%2B-%2B43058",
    },
    {
        "competition_id": "ESP_LaLiga",
        "home_team": "Alaves",
        "away_team": "Getafe",
        "kickoff_utc": "2026-08-15T17:30:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/Spain%2B-%2B8727",
    },
    {
        "competition_id": "FRA_Ligue1",
        "home_team": "Marseille",
        "away_team": "Strasbourg",
        "kickoff_utc": "2026-08-21T18:45:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/France%2B-%2B21532",
    },
    {
        "competition_id": "GER_Bundesliga",
        "home_team": "Bayern Munich",
        "away_team": "Stuttgart",
        "kickoff_utc": "2026-08-28T18:30:00+00:00",
        "url": "https://www.marathonbet.com/en/betting/Football/Germany/Bundesliga%2B-%2B22436",
    },
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        elif tag.lower() in {"br", "p", "div", "tr", "td", "th", "li", "section", "article", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        elif tag.lower() in {"p", "div", "tr", "td", "th", "li", "section", "article", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape(" ".join(self.parts))
        raw = raw.replace("\xa0", " ")
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n", raw)
        return raw.strip()


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def normalize(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").lower()
    value = re.sub(r"[^a-z0-9.+()\-]+", " ", value)
    return " ".join(value.split())


def fetch(url: str, timeout: int = 40) -> tuple[bytes, str, int, dict[str, str], str]:
    observed = now_utc()
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as resp:  # nosec - fixed first-party Marathonbet pages only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        final_url = str(resp.geturl())
        headers = {
            "content_type": str(resp.headers.get("Content-Type") or ""),
            "content_language": str(resp.headers.get("Content-Language") or ""),
            "date": str(resp.headers.get("Date") or ""),
            "server": str(resp.headers.get("Server") or ""),
            "cache_control": str(resp.headers.get("Cache-Control") or ""),
        }
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    return raw, final_url, status, headers, observed


def extract_text(raw: bytes) -> str:
    decoded = raw.decode("utf-8", errors="replace")
    parser = TextExtractor()
    parser.feed(decoded)
    return parser.text()


def context(text: str, home: str, away: str, width: int = 1400) -> str | None:
    low = text.lower()
    positions = [p for p in (low.find(home.lower()), low.find(away.lower())) if p >= 0]
    if not positions:
        return None
    start = max(0, min(positions) - 300)
    return text[start : start + width]


def write_evidence(target: dict, observed: str, final_url: str, status: int, headers: dict[str, str], raw: bytes, text: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(raw).hexdigest()
    text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    token = observed.replace(":", "").replace("+00:00", "Z")
    base = f"{safe(target['competition_id'])}__{token}__{digest[:12]}"
    html_path = EVIDENCE_ROOT / f"{base}.html"
    meta_path = EVIDENCE_ROOT / f"{base}.json"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_bytes(raw)
    meta = {
        "schema_version": "V5.5.16-marathonbet-html-envelope-r1",
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "observed_at_utc": observed,
        "requested_url": target["url"],
        "final_url": final_url,
        "http_status": status,
        "response_headers": headers,
        "raw_html_sha256": digest,
        "extracted_text_sha256": text_digest,
        "target": target,
        "formal_evidence": False,
        "research_probe_only": True,
        "access_policy": "Anonymous direct first-party HTML only; no login, proxy, CAPTCHA bypass, cookie injection or anti-bot circumvention.",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(html_path.relative_to(ROOT)), str(meta_path.relative_to(ROOT)), digest


def main() -> int:
    manifest: dict = {
        "schema_version": "V5.5.16-direct-marathonbet-html-probe-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_VALID_FIRST_PARTY_HTML",
        "targets": [],
        "formal_snapshot_written": False,
        "promotion_consensus_written": False,
        "formal_weight_change": False,
        "probability_change": False,
        "consensus_change": False,
        "policy": "Capability probe only. Direct HTML reachability and visible market text are not formal PIT evidence until exact fixture identity, locale timezone normalization, complete synchronized 1X2/AH/OU parsing, immutable raw parent linkage and V5.2.3 validation all pass.",
    }

    reachable = 0
    exact_team_pair = 0
    triple_market_text = 0
    for target in TARGETS:
        row: dict = {"target": target, "status": "FAIL"}
        try:
            raw, final_url, status, headers, observed = fetch(target["url"])
            text = extract_text(raw)
            low = normalize(text)
            home_present = normalize(target["home_team"]) in low
            away_present = normalize(target["away_team"]) in low
            labels = {
                "match_result": "match result" in low,
                "handicap": "to win match with handicap" in low or "handicap" in low,
                "total_goals": "total goals" in low,
            }
            reachable += 1
            exact_team_pair += int(home_present and away_present)
            triple_market_text += int(all(labels.values()))
            html_path, meta_path, digest = write_evidence(target, observed, final_url, status, headers, raw, text)
            row.update({
                "status": "PASS_HTML",
                "observed_at_utc": observed,
                "http_status": status,
                "final_url": final_url,
                "response_headers": headers,
                "raw_html_bytes": len(raw),
                "raw_html_sha256": digest,
                "raw_html_path": html_path,
                "metadata_path": meta_path,
                "home_present": home_present,
                "away_present": away_present,
                "market_labels": labels,
                "all_three_market_labels_present": all(labels.values()),
                "target_context": context(text, target["home_team"], target["away_team"]),
            })
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        manifest["targets"].append(row)

    manifest["reachable_page_count"] = reachable
    manifest["pages_with_both_target_teams"] = exact_team_pair
    manifest["pages_with_all_three_market_labels"] = triple_market_text
    if reachable:
        manifest["status"] = "PASS_FIRST_PARTY_HTML_REACHABLE"
    if exact_team_pair:
        manifest["status"] = "PASS_TARGET_TEXT_PRESENT_PARSER_REQUIRED"

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
