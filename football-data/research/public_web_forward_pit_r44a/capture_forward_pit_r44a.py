#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as html_lib
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
PLAYER_HREF = re.compile(r"^/en/players/(\d+)/[^/]+/overview$")
USER_AGENT = "FASHI188-V520-R44A-Forward-PIT/1.1"

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

def fail(message: str) -> None:
    raise RuntimeError(message)

def visible_text(dom: str) -> str:
    return SPACE_RE.sub(" ", html_lib.unescape(TAG_RE.sub(" ", dom))).strip()

def find_browser() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    fail("HEADLESS_BROWSER_NOT_FOUND")
    raise AssertionError

def validate_https(url: str, allowed_hosts: set[str]) -> urllib.parse.SplitResult:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in allowed_hosts or p.username or p.password or p.port:
        fail("URL_OUTSIDE_ALLOWLIST")
    return p

class AllowlistedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str]) -> None:
        super().__init__()
        self.hosts = hosts
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_https(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_public(url: str, hosts: set[str], timeout: int, max_bytes: int) -> tuple[bytes | None, dict[str, Any]]:
    validate_https(url, hosts)
    requested = utc_now()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/xml,text/plain;q=0.9,*/*;q=0.1", "Cache-Control": "no-cache"},
        method="GET",
    )
    opener = urllib.request.build_opener(AllowlistedRedirect(hosts))
    try:
        with opener.open(req, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            final_url = response.geturl()
            validate_https(final_url, hosts)
            raw = response.read(max_bytes + 1)
            ctype = str(response.headers.get("Content-Type") or "")
        observed = utc_now()
        if len(raw) > max_bytes:
            return None, {"url": url, "final_url": final_url, "status": status, "error": "BODY_TOO_LARGE", "requested_at_utc": iso(requested), "observed_at_utc": iso(observed)}
        return raw, {
            "url": url,
            "final_url": final_url,
            "status": status,
            "content_type": ctype,
            "bytes": len(raw),
            "sha256": sha256(raw),
            "requested_at_utc": iso(requested),
            "observed_at_utc": iso(observed),
        }
    except urllib.error.HTTPError as exc:
        return None, {"url": url, "status": int(exc.code), "error": f"HTTP_{exc.code}", "requested_at_utc": iso(requested), "observed_at_utc": iso(utc_now())}
    except Exception as exc:
        return None, {"url": url, "status": None, "error": type(exc).__name__, "requested_at_utc": iso(requested), "observed_at_utc": iso(utc_now())}

def render_public(browser: str, url: str, allowed_hosts: set[str], budget_ms: int, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    validate_https(url, allowed_hosts)
    requested = utc_now()
    proc = subprocess.run(
        [
            browser, "--headless=new", "--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox",
            "--window-size=1440,1400", f"--virtual-time-budget={budget_ms}", "--dump-dom", url,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=False,
    )
    observed = utc_now()
    if proc.returncode != 0:
        fail("BROWSER_RENDER_FAILED")
    raw = proc.stdout
    if not raw or len(raw) > max_bytes:
        fail("RENDERED_DOM_SIZE_INVALID")
    return raw, {
        "requested_at_utc": iso(requested),
        "observed_at_utc": iso(observed),
        "dom_bytes": len(raw),
        "dom_sha256": sha256(raw),
        "stderr_tail": proc.stderr.decode("utf-8", errors="replace")[-1000:],
    }

class LineupDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.testids: dict[str, int] = {}
        self.starters: list[dict[str, str]] = []
        self._starter: dict[str, str] | None = None
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        tid = a.get("data-testid")
        if tid:
            self.testids[tid] = self.testids.get(tid, 0) + 1
        if tag == "a" and tid == "lineupsPlayer":
            m = PLAYER_HREF.fullmatch(a.get("href", ""))
            if not m:
                fail("LINEUPS_PLAYER_HREF_CONTRACT_FAILED")
            self._starter = {"player_id": m.group(1), "name": ""}
        elif tag == "img" and self._starter is not None and not self._starter["name"]:
            if a.get("alt", "").strip():
                self._starter["name"] = a["alt"].strip()
    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._starter is not None:
            if not self._starter["name"]:
                fail("STARTER_NAME_MISSING")
            self.starters.append(self._starter)
            self._starter = None

def classify_lineup(dom_raw: bytes) -> dict[str, Any]:
    p = LineupDOMParser()
    p.feed(dom_raw.decode("utf-8", errors="replace"))
    p.close()
    c = p.testids
    confirmed = c.get("lineupsFormations", 0) == 1 and c.get("teamFormation", 0) == 2 and c.get("lineupsPlayer", 0) == 22 and c.get("lineupsSubs", 0) >= 1 and c.get("squads", 0) == 0
    pre = c.get("lineupsFormations", 0) == 0 and c.get("teamFormation", 0) == 0 and c.get("lineupsPlayer", 0) == 0 and c.get("lineupsSubs", 0) == 0 and c.get("squads", 0) >= 1
    state = "CONFIRMED_XI" if confirmed else ("PRE_ANNOUNCEMENT_SQUADS" if pre else "UNKNOWN_FAIL_CLOSED")
    if state == "CONFIRMED_XI":
        if len(p.starters) != 22 or len({x["player_id"] for x in p.starters}) != 22:
            fail("CONFIRMED_XI_STARTER_CONTRACT_FAILED")
    elif p.starters:
        fail("NON_CONFIRMED_STATE_HAS_STARTERS")
    return {
        "state": state,
        "source_native_counts": {k: c.get(k, 0) for k in ("lineupsFormations", "teamFormation", "lineupsPlayer", "lineupsSubs", "squads", "squadsLists")},
        "starter_count": len(p.starters),
        "home_starters": p.starters[:11] if state == "CONFIRMED_XI" else [],
        "away_starters": p.starters[11:] if state == "CONFIRMED_XI" else [],
    }

def normalize_candidate(url: str, hosts: set[str]) -> str | None:
    url = html_lib.unescape(url).rstrip(").,;")
    try:
        p = validate_https(url, hosts)
    except Exception:
        return None
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))

def extract_links(raw: bytes, hosts: set[str]) -> list[str]:
    text = raw.decode("utf-8", errors="replace")
    found: set[str] = set()
    for match in URL_RE.findall(text):
        u = normalize_candidate(match, hosts)
        if u:
            found.add(u)
    for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", text, flags=re.I):
        try:
            u = urllib.parse.urljoin("https://www.infobetting.com/", html_lib.unescape(href))
            u = normalize_candidate(u, hosts)
            if u:
                found.add(u)
        except Exception:
            pass
    return sorted(found)

def market_identity(text: str) -> dict[str, Any]:
    low = text.casefold()
    home = "arsenal" in low
    away = "coventry" in low
    hda = any(t in low for t in ("1x2", "match result", "draw"))
    asian = "asian" in low and any(t in low for t in ("handicap", "ah"))
    ou = ("under" in low and "over" in low) or "under-over" in low
    lines = sorted({m.group(1) for m in re.finditer(r"(?<!\d)([0-9]+\.5)(?!\d)", text)})
    decimals = re.findall(r"(?<![\d.])(?:1\.[0-9]{2,3}|[2-9]\.[0-9]{2,3}|[1-9][0-9]\.[0-9]{2,3})(?![\d.])", text)
    return {
        "home_token": home, "away_token": away, "hda_section": hda, "asian_section": asian,
        "ou_section": ou, "half_goal_lines": lines, "decimal_like_price_count": len(decimals),
        "feasibility_gate": home and away and hda and asian and ou and len(lines) >= 4 and len(decimals) >= 6,
    }

def validate_contract(c: dict[str, Any]) -> None:
    if c.get("schema_version") != "V520-R44A-PUBLIC-WEB-FORWARD-PIT-1.1":
        fail("CONTRACT_SCHEMA_MISMATCH")
    if c.get("status") != "ZERO_LABEL_EXACT_FIXTURE_MULTI_MARKET_COVERAGE_ONLY":
        fail("CONTRACT_STATUS_MISMATCH")
    if c.get("research_only") is not True or c.get("formal_weight") != 0:
        fail("RESEARCH_BOUNDARY_INVALID")
    b = c.get("hard_boundaries") or {}
    zero = ("target_labels", "settlement_results", "model_fits", "candidate_probabilities", "fixed_sample_consumption", "ev_calculations")
    if any(b.get(k) != 0 for k in zero):
        fail("HARD_BOUNDARY_NONZERO")
    if any(b.get(k) is not False for k in ("formal_model_mutation", "formal_data_mutation", "formal_config_mutation", "current_rule_mutation", "main_mutation")):
        fail("MUTATION_BOUNDARY_INVALID")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    cp = Path(args.contract)
    out = Path(args.output)
    c = json.loads(cp.read_text(encoding="utf-8"))
    validate_contract(c)
    out.mkdir(parents=True, exist_ok=False)
    raw_dir = out / "raw"
    raw_dir.mkdir()

    fixture = c["fixture"]
    kickoff = parse_utc(fixture["scheduled_kickoff_utc"])
    started = utc_now()
    if started >= kickoff:
        fail("FIXTURE_ALREADY_KICKED_OFF")

    browser = find_browser()
    line = c["lineup_source"]
    line_hosts = set(line["allowed_hosts"])
    line_dom, line_meta = render_public(browser, line["url"], line_hosts, int(c["capture_rules"]["browser_virtual_time_budget_ms"]), int(c["capture_rules"]["max_dom_bytes"]))
    if parse_utc(line_meta["observed_at_utc"]) >= kickoff:
        fail("LINEUP_OBSERVED_AFTER_KICKOFF")
    line_text = visible_text(line_dom.decode("utf-8", errors="replace"))
    if str(fixture["fixture_id"]) not in line_dom.decode("utf-8", errors="replace") or "arsenal" not in line_text.casefold() or "coventry" not in line_text.casefold():
        fail("LINEUP_FIXTURE_IDENTITY_FAILED")
    lineup = classify_lineup(line_dom)
    (raw_dir / f"lineup_dom__{line_meta['dom_sha256']}.html").write_bytes(line_dom)

    market = c["market_source"]
    hosts = set(market["allowed_hosts"])
    timeout = int(c["capture_rules"]["http_timeout_seconds"])
    max_http = int(c["capture_rules"]["max_http_bytes"])
    max_req = int(c["capture_rules"]["max_market_requests"])
    queue = list(market.get("discovery_urls") or []) + list(market["candidate_urls"])
    seen: set[str] = set()
    fetch_rows: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None

    while queue and len(fetch_rows) < max_req and selected is None:
        url = queue.pop(0)
        u = normalize_candidate(url, hosts)
        if not u or u in seen:
            continue
        seen.add(u)
        raw, meta = fetch_public(u, hosts, timeout, max_http)
        fetch_rows.append(meta)
        if raw is None:
            continue
        digest = meta["sha256"]
        suffix = ".xml" if "xml" in meta.get("content_type", "").casefold() else ".html"
        raw_path = raw_dir / f"market_discovery_{len(fetch_rows):02d}__{digest}{suffix}"
        raw_path.write_bytes(raw)
        text = visible_text(raw.decode("utf-8", errors="replace"))
        ident = market_identity(text)
        meta["market_identity"] = ident
        if ident["feasibility_gate"]:
            selected = {"url": meta.get("final_url", u), "raw": raw, "meta": meta, "identity": ident, "raw_path": raw_path.relative_to(out).as_posix()}
            break
        for link in extract_links(raw, hosts):
            low = link.casefold()
            if ("arsenal" in low and "coventry" in low) or "sitemap" in low:
                if link not in seen and link not in queue:
                    queue.append(link)

    terminal = "STOP_R44A_MARKET_DISCOVERY_NO_MATCH"
    market_receipt: dict[str, Any] = {
        "provider_group": market["provider_group"],
        "source_class": "PUBLIC_AGGREGATOR_SINGLE_PROVIDER_GROUP",
        "independent_market_count_claimed": 1,
        "source_native_quote_timestamp": None,
        "provable_available_at_semantics": "collector_first_observed_at_utc",
        "formal_market_snapshot": False,
        "discovery_requests": fetch_rows,
    }
    if selected is not None:
        terminal = "PASS_R44A_SOURCE_FEASIBILITY_NOT_FORMAL_MARKET_SNAPSHOT"
        observed = parse_utc(selected["meta"]["observed_at_utc"])
        if observed >= kickoff:
            fail("MARKET_OBSERVED_AFTER_KICKOFF")
        market_receipt.update({
            "selected_url": selected["url"],
            "raw_path": selected["raw_path"],
            "collector_first_observed_at_utc": selected["meta"]["observed_at_utc"],
            "retrieved_at_utc": selected["meta"]["observed_at_utc"],
            "payload_sha256": selected["meta"]["sha256"],
            "identity": selected["identity"],
            "structured_complete_prices_extracted": False,
            "reason_not_formal_snapshot": "Stage-1 feasibility only: sections/lines/prices detected but exact structured H/D/A, AH pair and OU pair extraction is not yet frozen.",
        })

    receipt = {
        "schema_version": "V520-R44A-RECEIPT-1.1",
        "terminal": terminal,
        "current_rule_family": "V5.2.0",
        "contract_sha256": sha256(cp.read_bytes()),
        "run_started_at_utc": iso(started),
        "run_finished_at_utc": iso(utc_now()),
        "fixture": fixture,
        "lineup_evidence": {
            "source_identity": line["source_identity"],
            "source_url": line["url"],
            "source_published_at_utc": None,
            "collector_first_observed_at_utc": line_meta["observed_at_utc"],
            "retrieved_at_utc": line_meta["observed_at_utc"],
            "payload_sha256": line_meta["dom_sha256"],
            "state": lineup["state"],
            "source_native_counts": lineup["source_native_counts"],
            "starter_count": lineup["starter_count"],
            "home_starters": lineup["home_starters"],
            "away_starters": lineup["away_starters"],
            "missingness_semantics": "PRE_ANNOUNCEMENT_SQUADS means official XI not yet source-native announced; UNKNOWN_FAIL_CLOSED is not interpreted.",
        },
        "market_evidence": market_receipt,
        "hard_boundary_receipt": {
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
        },
    }
    raw = packed(receipt) + b"\n"
    (out / "receipt_r44a.json").write_bytes(raw)
    (out / "receipt_r44a.sha256").write_text(sha256(raw) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal,
        "lineup_state": lineup["state"],
        "lineup_observed_at_utc": line_meta["observed_at_utc"],
        "market_selected_url": market_receipt.get("selected_url"),
        "market_request_count": len(fetch_rows),
        "market_identity": market_receipt.get("identity"),
        "receipt_sha256": sha256(raw),
    }, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R44A_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
