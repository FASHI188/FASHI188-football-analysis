#!/usr/bin/env python3
"""Audit the V5.1 R18 real-fixture candidate queue.

The queue is not a capture ledger. Official fixture identity alone cannot increment a
sample. A candidate becomes R14-eligible only after fixture-specific UTC provenance and
an executable synchronized 1X2/AH/OU snapshot with original quote timestamps exist.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = ROOT / "forward" / "queues" / "v510_first_forward_batch_r18.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_first_forward_batch_r18_status.json"


class QueueError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise QueueError(f"missing queue: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise QueueError("queue root must be object")
    return value


def http_url(value: Any) -> bool:
    return str(value or "").startswith(("http://", "https://"))


def audit(queue: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    candidates = queue.get("candidates")
    if not isinstance(candidates, list):
        failures.append("candidates must be list")
        candidates = []
    target = int(queue.get("target_unique_fixtures") or 0)
    if len(candidates) != target:
        failures.append(f"candidate count {len(candidates)} != target {target}")
    if int(queue.get("sample_count") or 0) != 0:
        failures.append("candidate queue sample_count must remain zero")
    if int(queue.get("capture_event_count") or 0) != 0:
        failures.append("candidate queue capture_event_count must remain zero")
    if int(queue.get("prediction_receipt_count") or 0) != 0:
        failures.append("candidate queue prediction_receipt_count must remain zero")

    ids: set[str] = set()
    match_ids: set[str] = set()
    identities: set[tuple[str, str, str]] = set()
    eligible = 0
    blocked_utc = 0
    blocked_market = 0
    for index, row in enumerate(candidates, start=1):
        if not isinstance(row, dict):
            failures.append(f"candidate[{index}] must be object")
            continue
        required = (
            "candidate_id", "competition_id", "official_match_id", "home_team",
            "away_team", "fixture_date_source_label", "kickoff_time_source_label",
            "kickoff_time_source_basis", "kickoff_at_utc", "official_fixture_url",
            "official_schedule_url", "official_fixture_identity_status",
            "kickoff_utc_status", "market_status", "context_status",
            "r14_capture_eligible",
        )
        missing = [field for field in required if field not in row]
        if missing:
            failures.append(f"candidate[{index}] missing {missing}")
            continue
        candidate_id = str(row["candidate_id"])
        match_id = str(row["official_match_id"])
        identity = (
            str(row["competition_id"]),
            str(row["home_team"]).casefold().strip(),
            str(row["away_team"]).casefold().strip(),
        )
        if candidate_id in ids:
            failures.append(f"duplicate candidate_id {candidate_id}")
        ids.add(candidate_id)
        if match_id in match_ids:
            failures.append(f"duplicate official_match_id {match_id}")
        match_ids.add(match_id)
        if identity in identities:
            failures.append(f"duplicate fixture identity {identity}")
        identities.add(identity)
        if not str(row["home_team"]).strip() or not str(row["away_team"]).strip():
            failures.append(f"candidate[{index}] empty team")
        if str(row["home_team"]).casefold().strip() == str(row["away_team"]).casefold().strip():
            failures.append(f"candidate[{index}] identical teams")
        if not http_url(row["official_fixture_url"]) or not http_url(row["official_schedule_url"]):
            failures.append(f"candidate[{index}] official URL missing")
        if row["official_fixture_identity_status"] != "CONFIRMED":
            failures.append(f"candidate[{index}] identity not confirmed")

        utc_ready = row["kickoff_at_utc"] is not None and str(row["kickoff_utc_status"]).startswith("PASS_")
        market_ready = str(row["market_status"]).startswith("PASS_")
        capture_eligible = row["r14_capture_eligible"] is True
        if capture_eligible and not (utc_ready and market_ready):
            failures.append(f"candidate[{index}] illegally marked R14 eligible")
        if capture_eligible:
            eligible += 1
        if not utc_ready:
            blocked_utc += 1
        if not market_ready:
            blocked_market += 1

    infrastructure_pass = not failures
    if infrastructure_pass and eligible == target and target > 0:
        status = "PASS_R18_FIRST_BATCH_ALL_CANDIDATES_R14_ELIGIBLE"
    elif infrastructure_pass:
        status = "PASS_R18_REAL_FIXTURE_QUEUE_READY_CAPTURE_GATES_PENDING"
    else:
        status = "FAIL_R18_REAL_FIXTURE_QUEUE_AUDIT"
    return {
        "schema_version": "V5.1.0-first-forward-batch-r18-status",
        "status": status,
        "infrastructure_pass": infrastructure_pass,
        "counts": {
            "target_unique_fixtures": target,
            "candidate_unique_fixtures": len(identities),
            "official_identity_confirmed": sum(
                isinstance(row, dict) and row.get("official_fixture_identity_status") == "CONFIRMED"
                for row in candidates
            ),
            "r14_capture_eligible": eligible,
            "blocked_pending_utc": blocked_utc,
            "blocked_pending_market_original_timestamps": blocked_market,
            "sample_count": int(queue.get("sample_count") or 0),
            "capture_event_count": int(queue.get("capture_event_count") or 0),
            "prediction_receipt_count": int(queue.get("prediction_receipt_count") or 0),
        },
        "failures": failures,
        "ruling": {
            "candidate_queue_is_not_capture_ledger": True,
            "official_identity_alone_increments_sample": False,
            "ambiguous_schedule_label_may_be_inferred_to_utc": False,
            "aggregator_odds_without_original_quote_time_allowed": False,
            "current_match_probability_allowed": False,
            "formal_weight": 0,
        },
    }


def self_test(queue: dict[str, Any]) -> None:
    receipt = audit(queue)
    assert receipt["infrastructure_pass"] is True
    assert receipt["counts"]["candidate_unique_fixtures"] == 20
    assert receipt["counts"]["r14_capture_eligible"] == 0
    broken = json.loads(json.dumps(queue))
    broken["candidates"][0]["r14_capture_eligible"] = True
    failure = audit(broken)
    assert failure["infrastructure_pass"] is False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    queue = load_json(args.queue)
    if args.self_test:
        self_test(queue)
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = audit(queue)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
