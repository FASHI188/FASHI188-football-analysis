#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

PAGE = "https://footiqo.com/database/leagues/england-premier-league/"
RESULT = Path("football-data/research/c072n5_protocol_result.json")
ASSET_HINTS = ("wpdatatable", "datatable", "wpdt", "buttons")
TARGET_NONCE_VAR = "wdtNonceFrontendServerSide_545"


def assets(soup: BeautifulSoup) -> list[str]:
    out=[]
    for tag in soup.find_all("script", src=True):
        u=urljoin(PAGE, tag.get("src", ""))
        if u.startswith("https://footiqo.com/") and any(h in u.lower() for h in ASSET_HINTS):
            out.append(u)
    return sorted(set(out))


def main() -> int:
    x=json.loads(RESULT.read_text(encoding="utf-8"))
    assert x["football_table_data_endpoint_requests_made"] == 0
    s=requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 football3-research","Accept-Language":"en-US,en;q=0.9"})
    r=s.get(PAGE, timeout=45)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    components=[("page_html", r.text)]
    for u in assets(soup):
        try:
            rr=s.get(u,timeout=45)
            if 200 <= rr.status_code < 300:
                components.append((u, rr.text))
        except Exception:
            pass
    all_code="\n".join(text for _,text in components)

    # Never persist the public nonce value. Accept JS assignment or object/property form.
    nonce_assignment = bool(re.search(
        rf"[\"']?{re.escape(TARGET_NONCE_VAR)}[\"']?\s*[:=]\s*[\"'][^\"']+[\"']",
        all_code,
    ))

    # Establish the literal request-field name from site-hosted code, without source snippets.
    request_field_names=set()
    if re.search(r"name\s*:\s*[\"']wdtNonce[\"']", all_code, re.I):
        request_field_names.add("wdtNonce")
    if re.search(r"[\"']wdtNonce[\"']\s*:\s*", all_code, re.I):
        request_field_names.add("wdtNonce")
    if re.search(r"\bwdtNonce\b\s*[:=]", all_code, re.I):
        request_field_names.add("wdtNonce")

    server_nonce_family_same_component_as_action=False
    relation_components=[]
    for name,text in components:
        if "get_wdtable" in text and "wdtNonceFrontendServerSide_" in text:
            server_nonce_family_same_component_as_action=True
            relation_components.append(name)

    # Diagnostic minimum token-distance only; no snippets or values.
    action_positions=[m.start() for m in re.finditer(r"get_wdtable", all_code, re.I)]
    nonce_positions=[m.start() for m in re.finditer(r"wdtNonce", all_code, re.I)]
    min_distance=None
    if action_positions and nonce_positions:
        min_distance=min(abs(a-b) for a in action_positions for b in nonce_positions)

    x["implementation_corrections"]=["C072N5_IMPLEMENTATION_CORRECTION_01","C072N5_IMPLEMENTATION_CORRECTION_02"]
    x["table_545_specific_server_nonce_variable_name"]=TARGET_NONCE_VAR
    x["table_545_specific_server_nonce_value_present"]=nonce_assignment
    x["get_wdtable_nonce_payload_key_names"]=sorted(request_field_names)
    x["server_side_nonce_family_same_component_as_get_wdtable"]=server_nonce_family_same_component_as_action
    x["server_nonce_action_relation_component_count"]=len(relation_components)
    x["minimum_character_distance_get_wdtable_to_wdtNonce"]=min_distance

    if nonce_assignment:
        x["table_545_serverSide_resolved"]=True
        x["nonce_requirement_classification"]="TABLE_545_FRONTEND_SERVER_SIDE_NONCE_REQUIRED"

    nonce_transport_resolved = nonce_assignment and bool(request_field_names) and server_nonce_family_same_component_as_action
    gates=x.get("gates",{})
    gates["table_545_server_ajax_status_resolved"] = bool(nonce_assignment)
    gates["nonce_requirement_resolved"] = bool(nonce_transport_resolved)
    x["gates"]=gates
    x["terminal"]="C072N5_PROTOCOL_RECONSTRUCTION_PASS" if gates and all(gates.values()) else "C072N5_PROTOCOL_DETAIL_NOT_ESTABLISHED"
    x["football_table_data_endpoint_requests_made"]=0
    x["target_result_values_materialized"]=0
    x["football_row_values_persisted"]=0
    RESULT.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
