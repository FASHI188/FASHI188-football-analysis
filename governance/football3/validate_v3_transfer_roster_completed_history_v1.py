#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CONTRACT_PATH = Path(__file__).with_name("v3_transfer_roster_completed_history_contract_v1.json")
API_ROOT = "https://api.github.com/repos/dcaribou/transfermarkt-datasets"
RAW_ROOT = "https://raw.githubusercontent.com/dcaribou/transfermarkt-datasets"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIR_MD5_RE = re.compile(r"^([0-9a-f]{32})\.dir$")


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "PREREGISTERED_ZERO_LABEL_TRANSPORT_AUDIT":
        raise RuntimeError("contract not preregistered")
    rule = value["user_data_rule"]
    if rule["development_training_tuning_backtest_audit_inventory"] != "COMPLETED_MATCHES_ONLY":
        raise RuntimeError("completed-only rule missing")
    if rule["unplayed_matches_in_research"] != "FORBIDDEN":
        raise RuntimeError("future-match prohibition missing")
    return value


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": "Football3-transfer-roster-completed-history-audit/1.0",
            "Accept": "application/vnd.github+json",
        },
    )


def github_json(url: str) -> Any:
    with urllib.request.urlopen(_request(url), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def github_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=60) as response:
        return response.read().decode("utf-8")


def parse_utc(value: str) -> datetime:
    token = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(token)
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def query_snapshot_commits(contract: dict[str, Any], json_fetch: Callable[[str], Any]) -> list[dict[str, Any]]:
    src = contract["source"]
    gates = contract["transport_gates"]
    query = urllib.parse.urlencode(
        {
            "path": src["descriptor_path"],
            "since": src["snapshot_window_start"],
            "until": src["snapshot_window_end"],
            "per_page": gates["maximum_commit_query_page_size"],
        }
    )
    rows = json_fetch(f"{API_ROOT}/commits?{query}")
    if not isinstance(rows, list):
        raise RuntimeError("commit query did not return a list")
    if len(rows) >= int(gates["maximum_commit_query_page_size"]):
        raise RuntimeError("commit query pagination required")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sha = row.get("sha")
        commit = row.get("commit") if isinstance(row.get("commit"), dict) else {}
        author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
        message = commit.get("message")
        date = author.get("date")
        if not (isinstance(sha, str) and SHA_RE.fullmatch(sha)):
            continue
        if message != src["snapshot_update_message"]:
            continue
        if not isinstance(date, str):
            continue
        dt = parse_utc(date)
        out.append({"sha": sha, "observed_at": dt.isoformat().replace("+00:00", "Z"), "message": message})
    out.sort(key=lambda item: (item["observed_at"], item["sha"]))
    return out


def select_monthly_latest(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in commits:
        dt = parse_utc(item["observed_at"])
        grouped[f"{dt.year:04d}-{dt.month:02d}"].append(item)
    selected = []
    for month in sorted(grouped):
        selected.append(max(grouped[month], key=lambda item: (item["observed_at"], item["sha"])))
    return selected


def parse_dvc_descriptor(text: str) -> dict[str, Any]:
    md5_match = re.search(r"(?m)^- md5:\s*([^\s]+)\s*$", text)
    size_match = re.search(r"(?m)^\s*size:\s*(\d+)\s*$", text)
    nfiles_match = re.search(r"(?m)^\s*nfiles:\s*(\d+)\s*$", text)
    path_match = re.search(r"(?m)^\s*path:\s*([^\s]+)\s*$", text)
    if not all((md5_match, size_match, nfiles_match, path_match)):
        raise ValueError("descriptor fields missing")
    raw_md5 = md5_match.group(1)
    parsed = DIR_MD5_RE.fullmatch(raw_md5)
    if not parsed:
        raise ValueError("descriptor md5 is not a .dir hash")
    size = int(size_match.group(1))
    nfiles = int(nfiles_match.group(1))
    path = path_match.group(1)
    if size <= 0 or nfiles <= 0 or path != "transfermarkt-api":
        raise ValueError("descriptor shape invalid")
    return {"dir_md5": parsed.group(1), "size": size, "nfiles": nfiles, "path": path}


def audit(
    *,
    contract_path: Path = CONTRACT_PATH,
    json_fetch: Callable[[str], Any] = github_json,
    text_fetch: Callable[[str], str] = github_text,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    src = contract["source"]
    gates = contract["transport_gates"]
    receipt: dict[str, Any] = {
        "schema_version": "football3-v3-transfer-roster-completed-history-receipt-v1",
        "phase": "DESCRIPTOR_ONLY_HISTORICAL_SNAPSHOT_TRANSPORT_AUDIT",
        "decision": None,
        "research_axis": contract["research_axis"],
        "target_population": "COMPLETED_MATCHES_ONLY",
        "future_matches_allowed": False,
        "existing_frozen_future_receipts_used": False,
        "stage6_1335_queue_used": False,
        "labels_opened": 0,
        "target_match_rows_read": 0,
        "target_result_or_goal_values_read": 0,
        "referenced_dvc_data_objects_downloaded": 0,
        "games_payload_downloaded": False,
        "appearances_payload_downloaded": False,
        "lineups_payload_downloaded": False,
        "training": False,
        "tuning": False,
        "stage6_touched": False,
        "formal_weight": 0,
        "matrix_delta": 0,
        "data_ready": False,
        "selected_snapshots": [],
    }

    license_text = text_fetch(f"{RAW_ROOT}/{src['license_ref']}/LICENSE")
    receipt["license"] = {
        "ref": src["license_ref"],
        "required_marker": src["license_required_marker"],
        "marker_present": src["license_required_marker"] in license_text,
    }
    if not receipt["license"]["marker_present"]:
        receipt["decision"] = "STOP_SOURCE_LICENSE_NOT_CC0"
        return receipt

    commits = query_snapshot_commits(contract, json_fetch)
    selected = select_monthly_latest(commits)
    receipt["snapshot_commit_count"] = len(commits)
    receipt["selected_month_count"] = len(selected)
    receipt["selected_months"] = [item["observed_at"][:7] for item in selected]

    if len(commits) < int(gates["minimum_source_update_commits"]):
        receipt["decision"] = "STOP_HISTORICAL_SNAPSHOT_UPDATE_COUNT"
        return receipt
    if len(selected) < int(gates["minimum_distinct_months"]):
        receipt["decision"] = "STOP_HISTORICAL_SNAPSHOT_MONTH_COVERAGE"
        return receipt
    if not selected:
        receipt["decision"] = "STOP_HISTORICAL_SNAPSHOT_MONTH_COVERAGE"
        return receipt

    first_dt = parse_utc(selected[0]["observed_at"])
    last_dt = parse_utc(selected[-1]["observed_at"])
    span_days = (last_dt - first_dt).days
    receipt["calendar_span_days"] = span_days
    if span_days < int(gates["minimum_calendar_span_days"]):
        receipt["decision"] = "STOP_HISTORICAL_SNAPSHOT_SPAN"
        return receipt
    if selected[0]["observed_at"][:7] > gates["required_first_selected_month_at_or_before"]:
        receipt["decision"] = "STOP_HISTORICAL_SNAPSHOT_START_BOUND"
        return receipt
    if selected[-1]["observed_at"][:7] < gates["required_last_selected_month_at_or_after"]:
        receipt["decision"] = "STOP_HISTORICAL_SNAPSHOT_END_BOUND"
        return receipt

    dir_hashes: set[str] = set()
    for item in selected:
        text = text_fetch(f"{RAW_ROOT}/{item['sha']}/{src['descriptor_path']}")
        try:
            descriptor = parse_dvc_descriptor(text)
        except ValueError as exc:
            receipt["decision"] = "STOP_DVC_DESCRIPTOR_INVALID"
            receipt["descriptor_error"] = {"sha": item["sha"], "reason": str(exc)}
            return receipt
        dir_hashes.add(descriptor["dir_md5"])
        receipt["selected_snapshots"].append({**item, **descriptor})

    receipt["distinct_descriptor_dir_hashes"] = len(dir_hashes)
    if len(dir_hashes) < int(gates["minimum_distinct_descriptor_dir_hashes"]):
        receipt["decision"] = "STOP_DVC_DESCRIPTOR_LINEAGE_NOT_DISTINCT"
        return receipt

    receipt["decision"] = "PASS_HISTORICAL_SNAPSHOT_TRANSPORT_NEXT_EXACT_HASH_OBJECT_AUDIT"
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit()
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"decision": result["decision"], "snapshot_commit_count": result.get("snapshot_commit_count"), "selected_month_count": result.get("selected_month_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
