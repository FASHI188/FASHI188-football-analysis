#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N6_NONCE_ONE_ROW_PREFLIGHT_V1"
PAGE = "https://footiqo.com/database/leagues/england-premier-league/"
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
TABLE_ID = 545
DOM_ID = "table_11"
NONCE_DOM = "wdtNonceFrontendServerSide_545"
NONCE_FIELD = "wdtNonce"
ACTION = "get_wdtable"
OUT = Path("football-data/research/c072n6_preflight_result.json")
FORBIDDEN_FIELD_RE = re.compile(
    r"(?:^|[_\-])(fthg|ftag|ftr|hthg|htag|htr|score|result|homegoals|awaygoals|1hhg|1hag|1hr|2hhg|2hag|2hr)(?:$|[_\-])",
    re.I,
)


def norm(x: str) -> str:
    return re.sub(r"\s+", " ", str(x).strip())


def find_table_and_headers(html: str) -> tuple[object | None, list[str]]:
    marker = html.find(HEADING)
    if marker < 0:
        return None, []
    soup = BeautifulSoup(html[marker:], "html.parser")
    for t in soup.find_all("table"):
        if str(t.get("data-wpdatatable_id", "")) != str(TABLE_ID):
            continue
        if str(t.get("id", "")) != DOM_ID:
            continue
        headers = [norm(x.get_text(" ", strip=True)) for x in t.find_all("th")]
        if not headers:
            first = t.find("tr")
            if first:
                headers = [norm(x.get_text(" ", strip=True)) for x in first.find_all(["th", "td"])]
        if {"O15", "U15", "O25", "U25", "O35", "U35"}.issubset(set(headers)):
            return t, headers
    return None, []


def build_payload(headers: list[str]) -> dict[str, str]:
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
                continue
    return None, None


def safe_row_shape(data) -> dict:
    out = {"container_type": type(data).__name__, "returned_row_count": 0}
    if not isinstance(data, list):
        return out
    out["returned_row_count"] = len(data)
    if not data:
        return out
    first = data[0]
    if isinstance(first, dict):
        keys = sorted(str(k) for k in first.keys())
        out["first_row_type"] = "object"
        out["first_row_field_names"] = keys
        out["first_row_field_count"] = len(keys)
        out["forbidden_field_names"] = [k for k in keys if FORBIDDEN_FIELD_RE.search(k)]
    elif isinstance(first, list):
        out["first_row_type"] = "array"
        out["first_row_array_length"] = len(first)
        out["forbidden_field_names"] = []
    else:
        out["first_row_type"] = type(first).__name__
        out["forbidden_field_names"] = []
    return out


def persist(result: dict) -> int:
    # Final hard scrub: never allow any field whose name could contain the runtime nonce value.
    assert "nonce_value" not in json.dumps(result, ensure_ascii=False).lower()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


def main() -> int:
    result = {
        "schema": SCHEMA,
        "project_line": "football3",
        "table_id": TABLE_ID,
        "table_dom_id": DOM_ID,
        "action": ACTION,
        "nonce_dom_name": NONCE_DOM,
        "nonce_request_field_name": NONCE_FIELD,
        "nonce_element_unique": False,
        "nonce_nonempty": False,
        "nonce_sent": False,
        "nonce_persisted_or_logged": 0,
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

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        page = s.get(PAGE, timeout=45, allow_redirects=True)
    except Exception:
        result["terminal"] = "C072N6_ACCESS_BLOCKED"
        result["page_request_error"] = True
        return persist(result)

    result["page_status_code"] = page.status_code
    result["page_bytes"] = len(page.content)
    if not (200 <= page.status_code < 300):
        result["terminal"] = "C072N6_ACCESS_BLOCKED"
        return persist(result)

    soup = BeautifulSoup(page.text, "html.parser")
    table, headers = find_table_and_headers(page.text)
    result["table_resolved"] = table is not None
    result["selected_header_count"] = len(headers)
    result["selected_headers"] = headers
    if table is None or not headers:
        result["terminal"] = "C072N6_PROTOCOL_DRIFT_STOP"
        return persist(result)

    nonce_nodes = [
        el for el in soup.find_all("input")
        if str(el.get("id", "")) == NONCE_DOM and str(el.get("name", "")) == NONCE_DOM
    ]
    result["nonce_element_count"] = len(nonce_nodes)
    result["nonce_element_unique"] = len(nonce_nodes) == 1
    if len(nonce_nodes) != 1:
        result["terminal"] = "C072N6_PROTOCOL_DRIFT_STOP"
        return persist(result)

    node = nonce_nodes[0]
    result["nonce_input_type"] = str(node.get("type", ""))
    raw_nonce = node.get("value")
    nonce = str(raw_nonce) if raw_nonce is not None else ""
    result["nonce_nonempty"] = bool(nonce)
    if str(node.get("type", "")).lower() != "hidden" or not nonce:
        result["terminal"] = "C072N6_PROTOCOL_DRIFT_STOP"
        return persist(result)

    body = build_payload(headers)
    body[NONCE_FIELD] = nonce
    query = {"action": ACTION, "table_id": str(TABLE_ID)}
    request_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://footiqo.com",
        "Referer": PAGE,
    }

    # Binding: this is the only table-data request in N6.
    result["football_table_data_requests_made"] = 1
    result["nonce_sent"] = True
    try:
        rr = s.post(AJAX, params=query, data=body, headers=request_headers, timeout=45, allow_redirects=True)
    except Exception:
        # Do not persist exception text because request objects may contain sensitive form data.
        result["terminal"] = "C072N6_ACCESS_BLOCKED"
        result["table_request_error"] = True
        return persist(result)
    finally:
        # Remove runtime secret-like value from memory as early as possible.
        body[NONCE_FIELD] = "<redacted>"
        nonce = ""
        raw_nonce = None

    result["response_status_code"] = rr.status_code
    result["response_content_type"] = rr.headers.get("content-type", "")
    result["response_bytes"] = len(rr.content)
    if not (200 <= rr.status_code < 300):
        result["terminal"] = "C072N6_ACCESS_BLOCKED"
        return persist(result)

    try:
        x = rr.json()
    except Exception:
        result["json_valid"] = False
        result["terminal"] = "C072N6_NONCE_PREFLIGHT_STRUCTURE_FAIL"
        return persist(result)

    result["json_valid"] = isinstance(x, dict)
    if not isinstance(x, dict):
        result["terminal"] = "C072N6_NONCE_PREFLIGHT_STRUCTURE_FAIL"
        return persist(result)

    result["json_top_level_keys"] = sorted(str(k) for k in x.keys())
    total_key, total = total_from_json(x)
    result["total_count_key"] = total_key
    result["records_total"] = total
    try:
        result["records_filtered"] = int(x.get("recordsFiltered")) if x.get("recordsFiltered") is not None else None
    except (TypeError, ValueError):
        result["records_filtered"] = None
    try:
        result["draw"] = int(x.get("draw")) if x.get("draw") is not None else None
    except (TypeError, ValueError):
        result["draw"] = None

    data = x.get("data", x.get("aaData", []))
    shape = safe_row_shape(data)
    result["response_data_shape"] = shape
    returned = int(shape.get("returned_row_count", 0))
    forbidden = shape.get("forbidden_field_names", [])

    gates = {
        "page_http_2xx": 200 <= page.status_code < 300,
        "unique_nonempty_nonce_hidden_input": result["nonce_element_unique"] and result["nonce_nonempty"],
        "exactly_one_request_and_nonce_sent": result["football_table_data_requests_made"] == 1 and result["nonce_sent"],
        "response_http_2xx": 200 <= rr.status_code < 300,
        "valid_json_object": result["json_valid"],
        "records_total_ge_1000": total is not None and total >= 1000,
        "exactly_one_returned_row": returned == 1,
        "no_forbidden_schema_field_names": len(forbidden) == 0,
        "nonce_not_persisted_or_logged": result["nonce_persisted_or_logged"] == 0,
        "no_football_row_values_persisted": result["football_row_values_persisted"] == 0,
        "zero_target_materialization": result["target_result_values_materialized"] == 0,
        "zero_model": result["model_fit"] == 0 and result["model_score"] == 0,
    }
    result["gates"] = gates
    result["terminal"] = "C072N6_NONCE_PREFLIGHT_PASS" if all(gates.values()) else "C072N6_NONCE_PREFLIGHT_STRUCTURE_FAIL"
    return persist(result)


if __name__ == "__main__":
    raise SystemExit(main())
