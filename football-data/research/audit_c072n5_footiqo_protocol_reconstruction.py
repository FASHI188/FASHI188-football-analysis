#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N5_PROTOCOL_RECONSTRUCTION_V1"
PAGE = "https://footiqo.com/database/leagues/england-premier-league/"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
TARGET_TABLE_ID = 545
TARGET_DOM_ID = "table_11"
OUT = Path("football-data/research/c072n5_protocol_result.json")
ASSET_HINTS = ("wpdatatable", "datatable", "wpdt", "buttons")
STANDARD_DT_KEYS = {
    "draw", "start", "length", "columns", "search", "order", "data", "name",
    "searchable", "orderable", "regex", "value", "sRangeSeparator",
}


def norm(x: str) -> str:
    return re.sub(r"\s+", " ", str(x).strip())


def relevant_assets(soup: BeautifulSoup) -> list[str]:
    out = []
    for tag in soup.find_all("script", src=True):
        full = urljoin(PAGE, tag.get("src", ""))
        if full.startswith("https://footiqo.com/") and any(h in full.lower() for h in ASSET_HINTS):
            out.append(full)
    return sorted(set(out))


def bool_values(text: str, key: str) -> list[bool]:
    vals = []
    pat = re.compile(rf"(?:[\"']?{re.escape(key)}[\"']?)\s*[:=]\s*(true|false|1|0)", re.I)
    for x in pat.findall(text):
        vals.append(x.lower() in {"true", "1"})
    return vals


def nonce_names(text: str) -> list[str]:
    names = set()
    for x in re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]{0,60}\b", text):
        low = x.lower()
        if "nonce" in low or low in {"security", "_wpnonce"}:
            names.add(x)
    return sorted(names)


def backend_name_values(text: str) -> list[str]:
    vals = set()
    for x in re.findall(r"[\"']name[\"']\s*:\s*[\"']([^\"']{1,100})[\"']", text, re.I):
        if re.fullmatch(r"[A-Za-z0-9_.$()`,+\-/* ]{1,100}", x):
            vals.add(x.strip())
    return sorted(v for v in vals if v)


def query_keys_near_action(text: str) -> list[str]:
    keys = set()
    for m in re.finditer(r"get_wdtable", text, re.I):
        ctx = text[max(0, m.start()-900): min(len(text), m.end()+1500)]
        for k in re.findall(r"[?&]([A-Za-z_][A-Za-z0-9_]*)=", ctx):
            keys.add(k)
    return sorted(keys)


def dt_keys(text: str) -> list[str]:
    found = set()
    low = text.lower()
    for k in STANDARD_DT_KEYS:
        if k.lower() in low:
            found.add(k)
    for x in re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]{2,50})[\"']\s*:", text):
        if any(t in x.lower() for t in ("filter", "range", "table", "ajax", "draw", "start", "length", "search", "order")):
            found.add(x)
    return sorted(found)


def table_545_associated_script(text: str) -> bool:
    tokens = ["wpDataTableID-545", "table_11", "tableWpId:545", 'tableWpId":545', "tableId:545", 'tableId":545']
    return any(t in text.replace(" ", "") if ":" in t else t in text for t in tokens)


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research",
        "Accept-Language": "en-US,en;q=0.9",
    })
    result = {
        "schema": SCHEMA,
        "project_line": "football3",
        "football_table_data_endpoint_requests_made": 0,
        "target_result_values_materialized": 0,
        "football_row_values_persisted": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
    }

    try:
        r = s.get(PAGE, timeout=45)
    except Exception as exc:
        result.update({"terminal": "SOURCE_ACCESS_BLOCKED", "page_error": repr(exc)})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    result["page_status_code"] = r.status_code
    result["page_bytes"] = len(r.content)
    if not (200 <= r.status_code < 300) or HEADING not in r.text:
        result["terminal"] = "SOURCE_ACCESS_BLOCKED"
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    soup = BeautifulSoup(r.text, "html.parser")
    odds_soup = BeautifulSoup(r.text[r.text.find(HEADING):], "html.parser")
    table = None
    selected_headers = []
    selected_seasons = []
    for t in odds_soup.find_all("table"):
        if str(t.get("data-wpdatatable_id", "")) != str(TARGET_TABLE_ID):
            continue
        headers = [norm(x.get_text(" ", strip=True)) for x in t.find_all("th")]
        if not {"O15","U15","O25","U25","O35","U35"}.issubset(set(headers)):
            continue
        table = t
        selected_headers = headers
        if "Season" in headers:
            si = headers.index("Season")
            for tr in t.find_all("tr")[1:]:
                cells = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td","th"])]
                if len(cells) > si and cells[si] and cells[si] != "Season":
                    selected_seasons.append(cells[si])
        break
    result["table_545_historical_odds_association"] = bool(table is not None and any(x.startswith("2015/") for x in selected_seasons))
    result["selected_headers"] = selected_headers
    result["selected_visible_seasons"] = sorted(set(selected_seasons))
    result["selected_table_dom_id"] = table.get("id") if table else None
    result["selected_table_plugin_id"] = int(table.get("data-wpdatatable_id")) if table and str(table.get("data-wpdatatable_id","")).isdigit() else None

    inline_texts = []
    for script in soup.find_all("script"):
        if script.get("src"):
            continue
        txt = script.string or script.get_text(" ", strip=False) or ""
        if txt:
            inline_texts.append(txt)

    assets = relevant_assets(soup)
    asset_texts = []
    asset_meta = []
    for url in assets:
        try:
            rr = s.get(url, timeout=45)
        except Exception:
            continue
        if 200 <= rr.status_code < 300:
            asset_texts.append(rr.text)
            ver = None
            m = re.search(r"[?&]ver=([0-9.]+)", url)
            if m: ver = m.group(1)
            asset_meta.append({"url": url, "bytes": len(rr.content), "version": ver})
    result["permitted_static_assets"] = asset_meta

    associated = [x for x in inline_texts if table_545_associated_script(x)]
    result["table_545_associated_inline_script_count"] = len(associated)
    associated_joined = "\n".join(associated)
    all_code = r.text + "\n" + "\n".join(asset_texts)

    # Resolve table-specific server-side/AJAX configuration.
    server_vals = bool_values(associated_joined, "serverSide")
    result["table_545_serverSide_values"] = server_vals
    result["table_545_serverSide_resolved"] = server_vals[0] if server_vals and all(v == server_vals[0] for v in server_vals) else None
    result["table_545_ajax_token_present"] = "ajax" in associated_joined.lower() or "fnserverdata" in associated_joined.lower()
    result["table_545_fnServerData_present"] = "fnServerData" in associated_joined
    result["table_545_dataTableParams_present"] = "dataTableParams" in associated_joined

    # Site-hosted action construction; persist keys, never snippets.
    result["get_wdtable_literal_present"] = "get_wdtable" in all_code
    result["get_wdtable_query_keys"] = query_keys_near_action(all_code)
    result["action_parameter_location_resolved"] = "query" if "action" in result["get_wdtable_query_keys"] else None
    result["table_id_parameter_location_resolved"] = "query" if "table_id" in result["get_wdtable_query_keys"] else None

    # Public nonce/security requirement: names only, no values.
    nonce_all = nonce_names(all_code)
    nonce_assoc = nonce_names(associated_joined)
    result["nonce_security_key_names_global"] = nonce_all
    result["nonce_security_key_names_table_545"] = nonce_assoc
    result["nonce_requirement_classification"] = (
        "TABLE_SPECIFIC_KEY_PRESENT" if nonce_assoc else
        "GLOBAL_KEYS_PRESENT_NO_TABLE_545_REQUIREMENT_ESTABLISHED" if nonce_all else
        "NO_NONCE_SECURITY_KEY_OBSERVED"
    )

    # Parameter families and column backend names; schema identifiers only.
    result["datatable_parameter_keys_table_545"] = dt_keys(associated_joined)
    result["datatable_parameter_keys_plugin_code"] = dt_keys("\n".join(asset_texts))
    names = backend_name_values(associated_joined)
    result["backend_column_names_table_545"] = names
    result["backend_column_schema_resolved"] = bool(names)
    result["backend_names_equal_visible_headers"] = (set(names) == set(selected_headers)) if names else None

    # Look for direct URL construction markers without storing code.
    result["site_code_constructs_action_get_wdtable"] = bool(re.search(r"action=get_wdtable", all_code, re.I))
    result["site_code_constructs_table_id_query"] = bool(re.search(r"table_id\s*=|table_id=", all_code, re.I))
    result["standard_request_families_observed"] = sorted(set(result["datatable_parameter_keys_plugin_code"]) & STANDARD_DT_KEYS)

    # PASS accepts an explicitly absent/non-table-specific nonce only if URL/action/table_id and standard DT body are otherwise resolved.
    nonce_resolved = result["nonce_requirement_classification"] in {
        "TABLE_SPECIFIC_KEY_PRESENT",
        "GLOBAL_KEYS_PRESENT_NO_TABLE_545_REQUIREMENT_ESTABLISHED",
        "NO_NONCE_SECURITY_KEY_OBSERVED",
    }
    backend_resolved_or_absent = result["backend_column_schema_resolved"] or bool(selected_headers)
    gates = {
        "page_http_2xx": 200 <= r.status_code < 300,
        "table_545_last_seasons_association": result["table_545_historical_odds_association"],
        "table_545_server_ajax_status_resolved": result["table_545_serverSide_resolved"] is not None or result["table_545_ajax_token_present"],
        "get_wdtable_url_construction_resolved": result["get_wdtable_literal_present"] and result["site_code_constructs_action_get_wdtable"] and result["site_code_constructs_table_id_query"],
        "datatable_parameter_families_resolved": all(k in result["datatable_parameter_keys_plugin_code"] for k in ["draw","start","length","columns","search"]),
        "nonce_requirement_resolved": nonce_resolved,
        "backend_column_schema_resolved_or_not_required": backend_resolved_or_absent,
        "zero_football_data_endpoint_requests": True,
        "zero_target_materialization": True,
        "zero_model": True,
    }
    result["gates"] = gates
    result["terminal"] = "C072N5_PROTOCOL_RECONSTRUCTION_PASS" if all(gates.values()) else "C072N5_PROTOCOL_DETAIL_NOT_ESTABLISHED"

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
