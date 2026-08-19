#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N3_AJAX_CONFIG_RESULT_V1R1"
PAGES = {
    "EPL": "https://footiqo.com/database/leagues/england-premier-league/",
    "LL": "https://footiqo.com/database/leagues/spain-laliga/",
    "BL": "https://footiqo.com/database/leagues/germany-bundesliga/",
    "SA": "https://footiqo.com/database/leagues/italy-serie-a/",
    "L1": "https://footiqo.com/database/leagues/france-ligue-1/",
}
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
OUT = Path("football-data/research/c072n3_ajax_config_result.json")
ASSET_HINTS = ("wpdatatable", "datatable", "wpdt", "buttons")

AJAX_URL_RE = re.compile(r"(?:https?:)?//[^\"'\s<>]*admin-ajax\.php|/wp-admin/admin-ajax\.php", re.I)
TABLE_DOM_RE = re.compile(r"(?:wpDataTableID-|table_)(\d+)", re.I)
TABLE_NUM_RE = re.compile(r"(?:tableId|table_id|wpdatatable_id|wpDataTableId)[\"'\s:=]+(\d+)", re.I)
ACTION_RE = re.compile(r"(?:action)[\"'\s:=]+[\"']([A-Za-z0-9_\-]{3,80})[\"']", re.I)
KEY_RE = re.compile(r"[\"']([A-Za-z_][A-Za-z0-9_]{2,50})[\"']\s*:")


def same_site_asset(base_url: str, src: str) -> str | None:
    if not src:
        return None
    full = urljoin(base_url, src)
    p = urlparse(full)
    if p.netloc not in {"footiqo.com", "www.footiqo.com"}:
        return None
    low = full.lower()
    if not any(h in low for h in ASSET_HINTS):
        return None
    return full


def scan_text(text: str) -> dict:
    ajax_urls = sorted(set(AJAX_URL_RE.findall(text)))
    table_ids = sorted({int(x) for x in TABLE_DOM_RE.findall(text)} | {int(x) for x in TABLE_NUM_RE.findall(text)})
    actions = sorted(set(ACTION_RE.findall(text)))
    low = text.lower()
    keys = sorted({k for k in KEY_RE.findall(text) if any(t in k.lower() for t in ("ajax", "table", "draw", "start", "length", "filter", "server", "action"))})
    return {
        "ajax_urls": ajax_urls,
        "table_ids": table_ids,
        "actions": actions,
        "transport_keys": keys,
        "wpdatatables_fingerprint": bool("wpdatatable" in low or "wpdatatables" in low),
        "datatables_fingerprint": bool("datatable" in low),
        "server_side_marker": bool(
            re.search(r"serverSide\s*[\"']?\s*[:=]\s*(?:true|1)", text, re.I)
            or "server-side" in low
            or "serverside" in low
        ),
        "ajax_marker": bool("ajax" in low or "admin-ajax.php" in low),
        "table_tools_marker": bool("buttons-csv" in low or "buttons-excel" in low or "dt-buttons" in low or "tabletools" in low),
    }


def merge_scan(dst: dict, src: dict) -> None:
    for k in ("ajax_urls", "table_ids", "actions", "transport_keys"):
        dst[k] = sorted(set(dst.get(k, [])) | set(src.get(k, [])))
    for k in ("wpdatatables_fingerprint", "datatables_fingerprint", "server_side_marker", "ajax_marker", "table_tools_marker"):
        dst[k] = bool(dst.get(k, False) or src.get(k, False))


def historical_odds_table_ids(html: str) -> list[int]:
    marker = html.find(HEADING)
    if marker < 0:
        return []
    soup = BeautifulSoup(html[marker:], "html.parser")
    found: set[int] = set()
    for table in soup.find_all("table"):
        headers = [re.sub(r"\s+", " ", th.get_text(" ", strip=True)) for th in table.find_all("th")]
        if not {"O15", "U15", "O25", "U25", "O35", "U35"}.issubset(set(headers)):
            continue
        token_text = " ".join([
            table.get("id", ""),
            " ".join(table.get("class", [])),
            str(table.get("data-wpdatatable_id", "")),
            str(table.get("data-wpdatatable-id", "")),
        ])
        found.update(int(x) for x in TABLE_DOM_RE.findall(token_text))
        found.update(int(x) for x in TABLE_NUM_RE.findall(token_text))
        for attr in ("data-wpdatatable_id", "data-wpdatatable-id"):
            val = table.get(attr)
            if val and str(val).isdigit():
                found.add(int(val))
    return sorted(found)


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research",
        "Accept-Language": "en-US,en;q=0.9",
    })

    page_results = {}
    global_assets: set[str] = set()
    blocked = []

    for code, url in PAGES.items():
        try:
            r = s.get(url, timeout=45, allow_redirects=True)
            ok = 200 <= r.status_code < 300
            if not ok:
                blocked.append(code)
                page_results[code] = {"status_code": r.status_code, "bytes": len(r.content), "asset_urls": []}
                continue
            html = r.text
            soup = BeautifulSoup(html, "html.parser")
            assets = []
            for tag in soup.find_all("script", src=True):
                full = same_site_asset(url, tag.get("src", ""))
                if full:
                    assets.append(full); global_assets.add(full)
            scan = scan_text(html)
            page_results[code] = {
                "status_code": r.status_code,
                "bytes": len(r.content),
                "asset_urls": sorted(set(assets)),
                "historical_odds_table_ids": historical_odds_table_ids(html),
                **scan,
            }
        except Exception as exc:
            blocked.append(code)
            page_results[code] = {"error": repr(exc), "asset_urls": [], "historical_odds_table_ids": []}

    asset_results = {}
    for asset in sorted(global_assets):
        try:
            r = s.get(asset, timeout=45, allow_redirects=True)
            if not (200 <= r.status_code < 300):
                asset_results[asset] = {"status_code": r.status_code, "bytes": len(r.content)}
                continue
            asset_results[asset] = {"status_code": r.status_code, "bytes": len(r.content), **scan_text(r.text)}
        except Exception as exc:
            asset_results[asset] = {"error": repr(exc)}

    global_scan = {
        "ajax_urls": [], "table_ids": [], "actions": [], "transport_keys": [],
        "wpdatatables_fingerprint": False, "datatables_fingerprint": False,
        "server_side_marker": False, "ajax_marker": False, "table_tools_marker": False,
    }
    for v in asset_results.values():
        if v.get("status_code") == 200:
            merge_scan(global_scan, v)
    for v in page_results.values():
        if v.get("status_code") == 200:
            merge_scan(v, global_scan)

    all_ajax = sorted({x for v in page_results.values() for x in v.get("ajax_urls", [])})
    all_actions = sorted({x for v in page_results.values() for x in v.get("actions", [])})
    all_keys = sorted({x for v in page_results.values() for x in v.get("transport_keys", [])})
    page_table_ok = sum(1 for v in page_results.values() if v.get("table_ids"))
    page_odds_table_ok = sum(1 for v in page_results.values() if v.get("historical_odds_table_ids"))
    page_fp_ok = sum(1 for v in page_results.values() if v.get("wpdatatables_fingerprint") or v.get("datatables_fingerprint"))
    page_server_ok = sum(1 for v in page_results.values() if v.get("server_side_marker") or v.get("ajax_marker"))

    if blocked:
        terminal = "SOURCE_ACCESS_BLOCKED"
        gates = {}
    else:
        plausible_action_or_key = bool(
            any("table" in x.lower() or "wdt" in x.lower() or "ajax" in x.lower() for x in all_actions + all_keys)
        )
        gates = {
            "all_five_pages_http2xx": len(page_results) == 5 and all(v.get("status_code") == 200 for v in page_results.values()),
            "wpdatatables_fingerprint_all_five": page_fp_ok == 5,
            "public_ajax_url_identified": bool(all_ajax),
            "table_identifier_all_five": page_table_ok == 5,
            "historical_odds_table_identifier_all_five": page_odds_table_ok == 5,
            "server_ajax_evidence_ge_four": page_server_ok >= 4,
            "plausible_data_action_or_config_key": plausible_action_or_key,
            "no_football_data_endpoint_invoked": True,
            "zero_target_materialization": True,
            "zero_model": True,
        }
        terminal = "C072N3_PUBLIC_AJAX_CONFIG_PASS" if all(gates.values()) else "C072N3_AJAX_CONFIG_NOT_ESTABLISHED"

    summary = {
        "schema": SCHEMA,
        "project_line": "football3",
        "terminal": terminal,
        "page_results": page_results,
        "static_asset_results": asset_results,
        "combined_ajax_urls": all_ajax,
        "combined_actions": all_actions,
        "combined_transport_keys": all_keys,
        "gates": gates,
        "football_data_endpoint_requests_made": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
