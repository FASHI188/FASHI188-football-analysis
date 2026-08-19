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
    code=[r.text]
    for u in assets(soup):
        try:
            rr=s.get(u,timeout=45)
            if 200 <= rr.status_code < 300:
                code.append(rr.text)
        except Exception:
            pass
    all_code="\n".join(code)

    # Never persist the public nonce value. Only establish that a non-empty assignment exists.
    nonce_assignment = bool(re.search(rf"\b{re.escape(TARGET_NONCE_VAR)}\b\s*=\s*[\"'][^\"']+[\"']", all_code))
    nonce_payload_keys=set()
    server_nonce_family_near_action=False
    for m in re.finditer(r"get_wdtable", all_code, re.I):
        ctx=all_code[max(0,m.start()-1400):min(len(all_code),m.end()+2200)]
        if "wdtNonceFrontendServerSide_" in ctx:
            server_nonce_family_near_action=True
        for key in re.findall(r"[\"']([A-Za-z_$][A-Za-z0-9_$]{1,60})[\"']\s*[:=]", ctx):
            if "nonce" in key.lower() or key.lower() == "security":
                nonce_payload_keys.add(key)
        # Also catch form-array pushes: name:'wdtNonce'
        for key in re.findall(r"name\s*:\s*[\"']([^\"']+)[\"']", ctx, re.I):
            if "nonce" in key.lower() or key.lower() == "security":
                nonce_payload_keys.add(key)

    x["implementation_correction"]="C072N5_IMPLEMENTATION_CORRECTION_01"
    x["table_545_specific_server_nonce_variable_name"]=TARGET_NONCE_VAR
    x["table_545_specific_server_nonce_value_present"]=nonce_assignment
    x["get_wdtable_nonce_payload_key_names"]=sorted(nonce_payload_keys)
    x["server_side_nonce_family_near_get_wdtable"]=server_nonce_family_near_action

    # Exact table-ID-specific server-side nonce variable is direct server-side transport evidence.
    if nonce_assignment:
        x["table_545_serverSide_resolved"]=True
        x["nonce_requirement_classification"]="TABLE_545_FRONTEND_SERVER_SIDE_NONCE_REQUIRED"

    nonce_transport_resolved = nonce_assignment and (bool(nonce_payload_keys) or server_nonce_family_near_action)
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
