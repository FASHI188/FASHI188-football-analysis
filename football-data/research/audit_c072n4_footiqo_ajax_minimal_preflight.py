#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N4_MINIMAL_AJAX_PREFLIGHT_V1"
PAGE = "https://footiqo.com/database/leagues/england-premier-league/"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
OUT = Path("football-data/research/c072n4_preflight_result.json")
AJAX_RE = re.compile(r"https://footiqo\.com/wp-admin/admin-ajax\.php|/wp-admin/admin-ajax\.php", re.I)
ACTION_LITERAL = "get_wdtable"
ASSET_HINTS = ("wpdatatable", "datatable", "wpdt", "buttons")
FORBIDDEN_FIELD_RE = re.compile(
    r"(?:^|[_\-])(fthg|ftag|ftr|hthg|htag|htr|score|result|homegoals|awaygoals|1hhg|1hag|1hr|2hhg|2hag|2hr)(?:$|[_\-])",
    re.I,
)


def norm(x: str) -> str:
    return re.sub(r"\s+", " ", str(x).strip())


def is_odds_header(headers: list[str]) -> bool:
    return {"O15", "U15", "O25", "U25", "O35", "U35"}.issubset(set(headers))


def extract_numeric_ids(table) -> dict:
    attrs = {str(k): str(v) for k, v in table.attrs.items() if k in {
        "id", "class", "data-wpdatatable_id", "data-wpdatatable-id", "data-table-id", "data-table_id"
    }}
    text = " ".join(str(v) for v in attrs.values())
    dom_ids = sorted({int(x) for x in re.findall(r"(?:wpDataTableID-|table_)(\d+)", text, re.I)})
    plugin_ids = []
    for k in ("data-wpdatatable_id", "data-wpdatatable-id", "data-table-id", "data-table_id"):
        v = table.get(k)
        if v is not None and str(v).strip().isdigit():
            plugin_ids.append(int(str(v).strip()))
    return {"attrs": attrs, "dom_ids": dom_ids, "plugin_ids": sorted(set(plugin_ids))}


def visible_seasons(table, headers: list[str]) -> list[str]:
    if "Season" not in headers:
        return []
    idx = headers.index("Season")
    vals = []
    for tr in table.find_all("tr")[1:]:
        cells = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if len(cells) > idx and cells[idx]:
            vals.append(cells[idx])
    return sorted(set(vals))


def permitted_assets(soup: BeautifulSoup) -> list[str]:
    out = []
    for tag in soup.find_all("script", src=True):
        full = urljoin(PAGE, tag.get("src", ""))
        low = full.lower()
        if full.startswith("https://footiqo.com/") and any(h in low for h in ASSET_HINTS):
            out.append(full)
    return sorted(set(out))


def build_datatables_payload(headers: list[str]) -> dict[str, str]:
    body: dict[str, str] = {
        "draw": "1",
        "start": "0",
        "length": "1",
        "search[value]": "",
        "search[regex]": "false",
    }
    for i, h in enumerate(headers):
        body[f"columns[{i}][data]"] = str(i)
        body[f"columns[{i}][name]"] = h
        body[f"columns[{i}][searchable]"] = "true"
        body[f"columns[{i}][orderable]"] = "true"
        body[f"columns[{i}][search][value]"] = ""
        body[f"columns[{i}][search][regex]"] = "false"
    return body


def total_from_json(x: dict) -> tuple[str | None, int | None]:
    for key in ("recordsTotal", "iTotalRecords", "recordsFiltered", "iTotalDisplayRecords"):
        if key in x:
            try:
                return key, int(x[key])
            except (TypeError, ValueError):
                pass
    return None, None


def row_shape(data) -> dict:
    meta = {"container_type": type(data).__name__, "returned_row_count": 0}
    if not isinstance(data, list):
        return meta
    meta["returned_row_count"] = len(data)
    if not data:
        return meta
    first = data[0]
    if isinstance(first, dict):
        keys = sorted(str(k) for k in first.keys())
        meta["first_row_type"] = "object"
        meta["first_row_field_names"] = keys
        meta["first_row_field_count"] = len(keys)
        meta["forbidden_field_names"] = [k for k in keys if FORBIDDEN_FIELD_RE.search(k)]
    elif isinstance(first, list):
        meta["first_row_type"] = "array"
        meta["first_row_array_length"] = len(first)
        meta["forbidden_field_names"] = []
    else:
        meta["first_row_type"] = type(first).__name__
        meta["forbidden_field_names"] = []
    return meta


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research",
        "Accept-Language": "en-US,en;q=0.9",
    })

    result = {
        "schema": SCHEMA,
        "project_line": "football3",
        "football_table_data_requests_made": 0,
        "football_row_values_persisted": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
    }

    try:
        page_r = s.get(PAGE, timeout=45, allow_redirects=True)
    except Exception as exc:
        result.update({"terminal": "C072N4_ACCESS_OR_PREMIUM_BLOCKED", "page_error": repr(exc)})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    result["page_status_code"] = page_r.status_code
    result["page_bytes"] = len(page_r.content)
    if not (200 <= page_r.status_code < 300) or HEADING not in page_r.text:
        result["terminal"] = "C072N4_ACCESS_OR_PREMIUM_BLOCKED"
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    full_soup = BeautifulSoup(page_r.text, "html.parser")
    marker = page_r.text.find(HEADING)
    odds_soup = BeautifulSoup(page_r.text[marker:], "html.parser")
    matching = []
    for table in odds_soup.find_all("table"):
        headers = [norm(x.get_text(" ", strip=True)) for x in table.find_all("th")]
        if not headers:
            first = table.find("tr")
            if first:
                headers = [norm(x.get_text(" ", strip=True)) for x in first.find_all(["th", "td"])]
        if not is_odds_header(headers):
            continue
        ids = extract_numeric_ids(table)
        seasons = visible_seasons(table, headers)
        matching.append({"table": table, "headers": headers, "seasons": seasons, **ids})

    result["matching_odds_table_count"] = len(matching)
    result["matching_table_metadata"] = [
        {"headers": x["headers"], "seasons": x["seasons"], "attrs": x["attrs"], "dom_ids": x["dom_ids"], "plugin_ids": x["plugin_ids"]}
        for x in matching
    ]

    historical = [x for x in matching if any(s.startswith("2015/") or s == "2015/2016" for s in x["seasons"])]
    if len(historical) != 1:
        result.update({"terminal": "C072N4_PROTOCOL_NOT_RESOLVED", "protocol_reason": "last_seasons_table_not_unique"})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    selected = historical[0]
    result["selected_headers"] = selected["headers"]
    result["selected_visible_seasons"] = selected["seasons"]
    result["selected_table_attrs"] = selected["attrs"]
    result["selected_dom_ids"] = selected["dom_ids"]
    result["selected_plugin_ids"] = selected["plugin_ids"]

    # Resolve table ID from the table's own explicit data attribute first.
    table_id = selected["plugin_ids"][0] if len(selected["plugin_ids"]) == 1 else None
    if table_id is None:
        # Directly-associated inline initialization tokens only: scripts that mention one selected DOM id.
        associated_ids = set()
        dom_tokens = [f"wpDataTableID-{x}" for x in selected["dom_ids"]] + [f"table_{x}" for x in selected["dom_ids"]]
        for script in full_soup.find_all("script"):
            txt = script.string or script.get_text(" ", strip=False) or ""
            if not any(tok in txt for tok in dom_tokens):
                continue
            associated_ids.update(int(x) for x in re.findall(r"(?:tableWpId|tableId|table_id)[\"'\s:=]+(\d+)", txt, re.I))
        result["associated_inline_table_ids"] = sorted(associated_ids)
        if len(associated_ids) == 1:
            table_id = next(iter(associated_ids))

    ajax_urls = sorted(set(AJAX_RE.findall(page_r.text)))
    result["ajax_urls"] = ajax_urls
    ajax_url = "https://footiqo.com/wp-admin/admin-ajax.php" if ajax_urls else None

    action_found = False
    action_sources = []
    # Inline Footiqo-hosted page code.
    if ACTION_LITERAL in page_r.text:
        action_found = True
        action_sources.append("page_html")
    assets = permitted_assets(full_soup)
    result["permitted_static_asset_count"] = len(assets)
    for url in assets:
        try:
            rr = s.get(url, timeout=45, allow_redirects=True)
        except Exception:
            continue
        if 200 <= rr.status_code < 300 and ACTION_LITERAL in rr.text:
            action_found = True
            action_sources.append(url)

    result["resolved_action"] = ACTION_LITERAL if action_found else None
    result["action_sources"] = sorted(set(action_sources))
    result["resolved_table_id"] = table_id
    result["resolved_ajax_url"] = ajax_url

    if not (table_id is not None and ajax_url and action_found and selected["headers"]):
        result.update({"terminal": "C072N4_PROTOCOL_NOT_RESOLVED", "protocol_reason": "missing_table_id_ajax_url_or_site_hosted_action"})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    # Binding rule: exactly one football table-data request, length=1.
    params = {"action": ACTION_LITERAL, "table_id": str(table_id)}
    body = build_datatables_payload(selected["headers"])
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://footiqo.com",
        "Referer": PAGE,
    }
    result["football_table_data_requests_made"] = 1
    try:
        rr = s.post(ajax_url, params=params, data=body, headers=headers, timeout=45, allow_redirects=True)
    except Exception as exc:
        result.update({"terminal": "C072N4_ACCESS_OR_PREMIUM_BLOCKED", "request_error": repr(exc)})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    result["response_status_code"] = rr.status_code
    result["response_content_type"] = rr.headers.get("content-type", "")
    result["response_bytes"] = len(rr.content)
    if not (200 <= rr.status_code < 300):
        result["terminal"] = "C072N4_ACCESS_OR_PREMIUM_BLOCKED"
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    try:
        x = rr.json()
    except Exception:
        result["terminal"] = "C072N4_PREFLIGHT_STRUCTURE_FAIL"
        result["json_valid"] = False
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    result["json_valid"] = isinstance(x, dict)
    if not isinstance(x, dict):
        result["terminal"] = "C072N4_PREFLIGHT_STRUCTURE_FAIL"
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    result["json_top_level_keys"] = sorted(str(k) for k in x.keys())
    total_key, total = total_from_json(x)
    result["total_count_key"] = total_key
    result["records_total"] = total
    if "draw" in x:
        try:
            result["draw"] = int(x["draw"])
        except (TypeError, ValueError):
            result["draw"] = None
    data = x.get("data", x.get("aaData", []))
    shape = row_shape(data)
    result["response_data_shape"] = shape

    forbidden = shape.get("forbidden_field_names", [])
    returned_n = int(shape.get("returned_row_count", 0))
    gates = {
        "page_and_assets_accessible": True,
        "unique_last_seasons_odds_table_resolved": True,
        "site_hosted_action_and_ajax_url_resolved": True,
        "exactly_one_data_request": result["football_table_data_requests_made"] == 1,
        "response_http_2xx": 200 <= rr.status_code < 300,
        "valid_json": True,
        "records_total_ge_1000": total is not None and total >= 1000,
        "returned_rows_between_1_and_1": returned_n == 1,
        "no_forbidden_response_field_names": len(forbidden) == 0,
        "no_row_values_persisted": result["football_row_values_persisted"] == 0,
        "zero_target_materialization": True,
        "zero_model": True,
    }
    result["gates"] = gates
    result["terminal"] = "C072N4_MINIMAL_AJAX_PREFLIGHT_PASS" if all(gates.values()) else "C072N4_PREFLIGHT_STRUCTURE_FAIL"

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
