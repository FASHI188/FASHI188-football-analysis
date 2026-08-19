#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PAGE = "https://footiqo.com/database/leagues/england-premier-league/"
TOKEN = "wdtNonceFrontendServerSide_545"
RESULT = Path("football-data/research/c072n5_protocol_result.json")


def main() -> int:
    x = json.loads(RESULT.read_text(encoding="utf-8"))
    assert x["football_table_data_endpoint_requests_made"] == 0
    r = requests.get(PAGE, timeout=45, headers={"User-Agent": "Mozilla/5.0 football3-research"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    carriers = []
    for el in soup.find_all(True):
        matching_attrs = []
        for k, v in el.attrs.items():
            vals = v if isinstance(v, list) else [v]
            if any(str(z) == TOKEN for z in vals):
                matching_attrs.append(str(k))
        if not matching_attrs:
            continue
        value = el.get("value")
        data_attrs = {k: v for k, v in el.attrs.items() if str(k).startswith("data-")}
        carriers.append({
            "tag": el.name,
            "input_type": str(el.get("type")) if el.get("type") is not None else None,
            "attribute_names": sorted(str(k) for k in el.attrs.keys()),
            "matching_attribute_names": sorted(matching_attrs),
            "value_attribute_present": "value" in el.attrs,
            "value_attribute_nonempty": bool(str(value)) if value is not None else False,
            "data_attribute_names": sorted(str(k) for k in data_attrs.keys()),
            "any_data_attribute_nonempty": any(bool(str(v)) for v in data_attrs.values()),
        })

    unique_dom_value = (
        len(carriers) == 1
        and carriers[0]["tag"] == "input"
        and carriers[0]["value_attribute_present"]
        and carriers[0]["value_attribute_nonempty"]
    )
    x["implementation_corrections"] = [
        "C072N5_IMPLEMENTATION_CORRECTION_01",
        "C072N5_IMPLEMENTATION_CORRECTION_02",
        "C072N5_IMPLEMENTATION_CORRECTION_03",
        "C072N5_IMPLEMENTATION_CORRECTION_04",
    ]
    x["table_545_nonce_dom_carrier_count"] = len(carriers)
    x["table_545_nonce_dom_carriers"] = carriers
    x["table_545_nonce_extraction_class"] = "DOM_VALUE" if unique_dom_value else "DOM_NOT_RESOLVED"

    request_key_resolved = "wdtNonce" in x.get("get_wdtable_nonce_payload_key_names", [])
    relation_resolved = bool(x.get("server_side_nonce_family_same_component_as_get_wdtable", False))
    if unique_dom_value:
        x["table_545_specific_server_nonce_value_present"] = True
        x["table_545_serverSide_resolved"] = True
        x["nonce_requirement_classification"] = "TABLE_545_FRONTEND_SERVER_SIDE_NONCE_REQUIRED_DOM_VALUE"

    gates = x.get("gates", {})
    gates["table_545_server_ajax_status_resolved"] = bool(unique_dom_value)
    gates["nonce_requirement_resolved"] = bool(unique_dom_value and request_key_resolved and relation_resolved)
    x["gates"] = gates
    x["terminal"] = "C072N5_PROTOCOL_RECONSTRUCTION_PASS" if gates and all(gates.values()) else "C072N5_PROTOCOL_DETAIL_NOT_ESTABLISHED"
    x["football_table_data_endpoint_requests_made"] = 0
    x["football_row_values_persisted"] = 0
    x["target_result_values_materialized"] = 0
    RESULT.write_text(json.dumps(x, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(x, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
