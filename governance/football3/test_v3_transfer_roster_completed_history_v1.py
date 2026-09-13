#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import validate_v3_transfer_roster_completed_history_v1 as mod


def contract_copy(**gate_updates):
    value = json.loads(mod.CONTRACT_PATH.read_text(encoding="utf-8"))
    value["transport_gates"].update(gate_updates)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(value, tmp)
    tmp.close()
    return Path(tmp.name)


def synthetic_commits():
    rows = []
    serial = 1
    for year, month in [(2024, m) for m in range(9, 13)] + [(2025, m) for m in range(1, 7)]:
        for day in (5, 20):
            if year == 2025 and month == 6 and day == 20:
                continue
            sha = f"{serial:040x}"[-40:]
            serial += 1
            rows.append({
                "sha": sha,
                "commit": {
                    "author": {"date": datetime(year, month, day, 5, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")},
                    "message": "🤖 updated `transfermarkt-api` raw data",
                },
            })
    return rows


def descriptor_for(sha):
    md5 = hashlib.md5(sha.encode("utf-8")).hexdigest()
    return f"outs:\n- md5: {md5}.dir\n  size: 123456\n  nfiles: 15\n  hash: md5\n  path: transfermarkt-api\n"


def run_pass_audit(contract_path):
    rows = synthetic_commits()
    by_sha = {row["sha"]: descriptor_for(row["sha"]) for row in rows}

    def json_fetch(url):
        assert "commits?" in url
        return list(reversed(rows))

    def text_fetch(url):
        if url.endswith("/LICENSE"):
            return "Creative Commons Legal Code\nCC0 1.0 Universal\n"
        sha = url.split("/")[-4]
        return by_sha[sha]

    return mod.audit(contract_path=contract_path, json_fetch=json_fetch, text_fetch=text_fetch)


def test_contract_completed_only():
    c = mod.load_contract()
    assert c["user_data_rule"]["development_training_tuning_backtest_audit_inventory"] == "COMPLETED_MATCHES_ONLY"
    assert c["user_data_rule"]["unplayed_matches_in_research"] == "FORBIDDEN"
    assert c["user_data_rule"]["stage6_1335_queue_usage"] == "FORBIDDEN"


def test_parse_descriptor():
    parsed = mod.parse_dvc_descriptor("outs:\n- md5: 0123456789abcdef0123456789abcdef.dir\n  size: 999\n  nfiles: 15\n  hash: md5\n  path: transfermarkt-api\n")
    assert parsed["dir_md5"] == "0123456789abcdef0123456789abcdef"
    assert parsed["nfiles"] == 15


def test_pass_zero_label():
    path = contract_copy(minimum_source_update_commits=18, minimum_distinct_months=8, minimum_distinct_descriptor_dir_hashes=6)
    r = run_pass_audit(path)
    assert r["decision"] == "PASS_HISTORICAL_SNAPSHOT_TRANSPORT_NEXT_EXACT_HASH_OBJECT_AUDIT"
    assert r["target_population"] == "COMPLETED_MATCHES_ONLY"
    assert r["target_match_rows_read"] == 0
    assert r["target_result_or_goal_values_read"] == 0
    assert r["referenced_dvc_data_objects_downloaded"] == 0
    assert r["existing_frozen_future_receipts_used"] is False
    assert r["stage6_1335_queue_used"] is False
    assert r["training"] is False and r["tuning"] is False


def test_license_fail_closed():
    path = contract_copy(minimum_source_update_commits=1, minimum_distinct_months=1, minimum_calendar_span_days=0, minimum_distinct_descriptor_dir_hashes=1)
    def json_fetch(url):
        raise AssertionError("commit query must not occur after license failure")
    def text_fetch(url):
        return "not a permitted marker"
    r = mod.audit(contract_path=path, json_fetch=json_fetch, text_fetch=text_fetch)
    assert r["decision"] == "STOP_SOURCE_LICENSE_NOT_CC0"


def test_descriptor_fail_closed():
    path = contract_copy(minimum_source_update_commits=18, minimum_distinct_months=8, minimum_distinct_descriptor_dir_hashes=6)
    rows = synthetic_commits()
    def json_fetch(url):
        return list(reversed(rows))
    def text_fetch(url):
        if url.endswith("/LICENSE"):
            return "CC0 1.0 Universal"
        return "outs:\n- md5: invalid\n  size: 1\n  nfiles: 1\n  path: transfermarkt-api\n"
    r = mod.audit(contract_path=path, json_fetch=json_fetch, text_fetch=text_fetch)
    assert r["decision"] == "STOP_DVC_DESCRIPTOR_INVALID"
    assert r["referenced_dvc_data_objects_downloaded"] == 0


def main():
    tests = [name for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for name in sorted(tests):
        globals()[name]()
    print(f"PASS {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
