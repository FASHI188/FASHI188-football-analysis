#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

PAGE = "https://footiqo.com/database/leagues/england-premier-league/"
TOKEN = "wdtNonceFrontendServerSide_545"
RESULT = Path("football-data/research/c072n5_protocol_result.json")


def classify(text: str) -> dict:
    esc = re.escape(TOKEN)
    patterns = [
        ("QUOTED_OBJECT_PROPERTY_STRING", rf'["\']{esc}["\']\s*:\s*["\']([^"\']+)["\']'),
        ("UNQUOTED_OBJECT_PROPERTY_STRING", rf'\b{esc}\b\s*:\s*["\']([^"\']+)["\']'),
        ("VARIABLE_QUOTED_SCALAR", rf'\b{esc}\b\s*=\s*["\']([^"\']+)["\']'),
        ("VARIABLE_OBJECT", rf'\b{esc}\b\s*=\s*\{{([^{{}}]{{0,1200}})\}}'),
        ("QUOTED_OBJECT_PROPERTY_OBJECT", rf'["\']{esc}["\']\s*:\s*\{{([^{{}}]{{0,1200}})\}}'),
        ("UNQUOTED_OBJECT_PROPERTY_OBJECT", rf'\b{esc}\b\s*:\s*\{{([^{{}}]{{0,1200}})\}}'),
    ]
    for name, pat in patterns:
        m = re.search(pat, text, re.I | re.S)
        if not m:
            continue
        payload = m.group(1)
        if name.endswith("STRING") or name == "VARIABLE_QUOTED_SCALAR":
            return {
                "syntax_class": name,
                "nonempty_value_present": bool(payload),
                "object_child_key_names": [],
                "matched_payload_span_length": len(payload),
                "wdtNonce_child_nonempty_scalar_present": False,
            }
        keys = sorted(set(re.findall(r'["\']?([A-Za-z_$][A-Za-z0-9_$]{1,60})["\']?\s*:', payload)))
        child = bool(re.search(r'["\']?wdtNonce["\']?\s*:\s*["\'][^"\']+["\']', payload, re.I))
        return {
            "syntax_class": name,
            "nonempty_value_present": bool(payload.strip()),
            "object_child_key_names": keys,
            "matched_payload_span_length": len(payload),
            "wdtNonce_child_nonempty_scalar_present": child,
        }

    # Last-resort structural classification: token exists, persist only punctuation following token.
    m = re.search(rf'(?:["\']?{esc}["\']?)(\s*[:=]\s*)(.)', text, re.I | re.S)
    return {
        "syntax_class": "UNRESOLVED_OTHER" if TOKEN in text else "TOKEN_NOT_FOUND",
        "nonempty_value_present": False,
        "object_child_key_names": [],
        "matched_payload_span_length": 0,
        "wdtNonce_child_nonempty_scalar_present": False,
        "following_operator": m.group(1).strip() if m else None,
        "following_value_delimiter_class": (
            "QUOTE" if m and m.group(2) in {'\"', "'"} else
            "OBJECT" if m and m.group(2) == '{' else
            "ARRAY" if m and m.group(2) == '[' else
            "OTHER" if m else None
        ),
    }


def main() -> int:
    x = json.loads(RESULT.read_text(encoding="utf-8"))
    assert x["football_table_data_endpoint_requests_made"] == 0
    r = requests.get(PAGE, timeout=45, headers={"User-Agent":"Mozilla/5.0 football3-research"})
    r.raise_for_status()
    c = classify(r.text)
    x["implementation_corrections"] = [
        "C072N5_IMPLEMENTATION_CORRECTION_01",
        "C072N5_IMPLEMENTATION_CORRECTION_02",
        "C072N5_IMPLEMENTATION_CORRECTION_03",
    ]
    x["table_545_nonce_syntax"] = c

    syntax_resolved = c["syntax_class"] not in {"UNRESOLVED_OTHER", "TOKEN_NOT_FOUND"}
    usable_public_value = bool(c["nonempty_value_present"] or c["wdtNonce_child_nonempty_scalar_present"])
    request_key_resolved = "wdtNonce" in x.get("get_wdtable_nonce_payload_key_names", [])
    relation_resolved = bool(x.get("server_side_nonce_family_same_component_as_get_wdtable", False))

    if syntax_resolved and usable_public_value:
        x["table_545_specific_server_nonce_value_present"] = True
        x["table_545_serverSide_resolved"] = True
        x["nonce_requirement_classification"] = "TABLE_545_FRONTEND_SERVER_SIDE_NONCE_REQUIRED"

    gates = x.get("gates", {})
    gates["table_545_server_ajax_status_resolved"] = bool(syntax_resolved and usable_public_value)
    gates["nonce_requirement_resolved"] = bool(syntax_resolved and usable_public_value and request_key_resolved and relation_resolved)
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
