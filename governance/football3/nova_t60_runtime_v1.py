#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class RuntimeErrorT60(RuntimeError):
    pass


def require(ok: bool, msg: str) -> None:
    if not ok:
        raise RuntimeErrorT60(msg)


def canon(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(v: Any) -> str:
    return hashlib.sha256(canon(v)).hexdigest()


def dt(v: str) -> datetime:
    x = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def iso(x: datetime) -> str:
    return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_config(path: Path) -> dict[str, Any]:
    c = load_json(path)
    require(c["status"] == "T60_RUNTIME_ENABLE_AUTHORIZED", "CONFIG_STATUS")
    require(c["activation"]["enabled"] is True, "ACTIVATION_DISABLED")
    require(c["activation"]["replay_coverage_start_at"] is None, "COVERAGE_START_MUST_BE_RUNTIME_ASSIGNED")
    require(c["activation"]["coverage_start_assignment"] == "FIRST_DEFAULT_BRANCH_RUNTIME_RUN_ONLY", "COVERAGE_START_ASSIGNMENT")
    expected_safety = {
        "result_fields_read": 0,
        "retroactive_coverage_fabrication": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
    }
    require(c["safety"] == expected_safety, "SAFETY_BOUNDARY")
    return c


def source_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"score", "result", "winner", "outcome", "home_goals", "away_goals", "final_score", "points", "postmatch", "xg_result"}
    out = {k: v for k, v in row.items() if k.lower() not in forbidden}
    for key in ("fixture_id", "competition", "season", "kickoff", "home_team", "away_team"):
        require(str(out.get(key) or "").strip(), f"SOURCE_FIELD_MISSING:{key}")
    return out


def revise(registry: dict[str, Any], row: dict[str, Any], observed_at: str, seal_minutes: int) -> str:
    row = source_safe_row(row)
    fid = str(row["fixture_id"])
    kickoff = iso(dt(str(row["kickoff"])))
    observed = iso(dt(observed_at))
    old = registry.get(fid)
    if old is None:
        cutoff = dt(kickoff) - timedelta(minutes=seal_minutes)
        registry[fid] = {
            "fixture_id": fid,
            "competition": row["competition"],
            "season": row["season"],
            "registered_at": observed,
            "kickoff_revisions": [{
                "revision_no": 1,
                "kickoff": kickoff,
                "observed_at": observed,
                "late_revision_risk": dt(observed) > cutoff,
            }],
            "latest_pre_cutoff_snapshot": None,
            "terminal_capture": None,
        }
        return "inserted"
    require(old["competition"] == row["competition"] and old["season"] == row["season"], f"IDENTITY_DRIFT:{fid}")
    last = old["kickoff_revisions"][-1]
    require(dt(observed) >= dt(last["observed_at"]), f"OBSERVED_AT_NOT_MONOTONIC:{fid}")
    if last["kickoff"] == kickoff:
        return "unchanged"
    cutoff = dt(kickoff) - timedelta(minutes=seal_minutes)
    old["kickoff_revisions"].append({
        "revision_no": int(last["revision_no"]) + 1,
        "kickoff": kickoff,
        "observed_at": observed,
        "late_revision_risk": dt(observed) > cutoff,
    })
    old["latest_pre_cutoff_snapshot"] = None
    return "revised"


def update_snapshot(regrow: dict[str, Any], source_row: dict[str, Any], source_observed_at: str, seal_minutes: int) -> None:
    revision = regrow["kickoff_revisions"][-1]
    cutoff = dt(revision["kickoff"]) - timedelta(minutes=seal_minutes)
    observed = dt(source_observed_at)
    if observed > cutoff:
        return
    safe = source_safe_row(source_row)
    snap = {
        "fixture_id": regrow["fixture_id"],
        "kickoff_revision_no": int(revision["revision_no"]),
        "observed_at": iso(observed),
        "available_at": iso(observed),
        "source": "T60_FIXTURE_ROUTE",
        "payload": safe,
    }
    prev = regrow.get("latest_pre_cutoff_snapshot")
    if prev is None or (snap["available_at"], snap["observed_at"]) >= (prev["available_at"], prev["observed_at"]):
        regrow["latest_pre_cutoff_snapshot"] = snap


def gap(regrow: dict[str, Any], now: str, reason: str, model_head: str, current_sha: str, seal_minutes: int) -> dict[str, Any]:
    revision = regrow["kickoff_revisions"][-1]
    kickoff = dt(revision["kickoff"])
    cutoff = kickoff - timedelta(minutes=seal_minutes)
    receipt = {
        "schema_version": "football3-nova-t60-gap-receipt-v1",
        "status": "GAP_RECEIPT",
        "fixture_id": regrow["fixture_id"],
        "competition": regrow["competition"],
        "season": regrow["season"],
        "kickoff": iso(kickoff),
        "cutoff": iso(cutoff),
        "observed_at": iso(dt(now)),
        "reason": reason,
        "model_head": model_head,
        "current_sha": current_sha,
        "result_fields_read": 0,
        "retroactive_fabrication": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    receipt["gap_receipt_id"] = "gap:" + sha(receipt)[:24]
    return receipt


def seal_state(regrow: dict[str, Any], now: str, model_head: str, current_sha: str, seal_minutes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    revision = regrow["kickoff_revisions"][-1]
    kickoff = dt(revision["kickoff"])
    cutoff = kickoff - timedelta(minutes=seal_minutes)
    snap = regrow.get("latest_pre_cutoff_snapshot")
    require(snap is not None, "SNAPSHOT_REQUIRED")
    require(int(snap["kickoff_revision_no"]) == int(revision["revision_no"]), "SNAPSHOT_REVISION_MISMATCH")
    require(dt(snap["available_at"]) <= cutoff, "SNAPSHOT_AFTER_CUTOFF")
    selected = {"fixture_safe_snapshot": snap}
    input_sha = sha(selected)
    core = {
        "schema_version": "football3-nova-t60-sealed-state-v1",
        "fixture_id": regrow["fixture_id"],
        "competition": regrow["competition"],
        "season": regrow["season"],
        "kickoff": iso(kickoff),
        "cutoff": iso(cutoff),
        "observed_at": iso(dt(now)),
        "available_at": snap["available_at"],
        "source": [snap["source"]],
        "input_sha256": input_sha,
        "model_head": model_head,
        "current_sha": current_sha,
        "kickoff_revision_no": int(revision["revision_no"]),
        "payload": selected,
        "result_fields_read": 0,
        "retroactive_fabrication": False,
    }
    state = dict(core)
    state["state_sha256"] = sha(core)
    receipt = {
        "schema_version": "football3-nova-t60-capture-receipt-v1",
        "status": "SEALED_STATE_CAPTURED",
        "fixture_id": state["fixture_id"],
        "competition": state["competition"],
        "season": state["season"],
        "kickoff": state["kickoff"],
        "cutoff": state["cutoff"],
        "observed_at": state["observed_at"],
        "available_at": state["available_at"],
        "source": state["source"],
        "input_sha256": input_sha,
        "state_sha256": state["state_sha256"],
        "model_head": model_head,
        "current_sha": current_sha,
        "result_fields_read": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    receipt["capture_receipt_id"] = "capture:" + sha(receipt)[:24]
    state["capture_receipt_id"] = receipt["capture_receipt_id"]
    return state, receipt


def execute(config_path: Path, source_dir: Path, prior_dir: Path | None, out_dir: Path, now_raw: str, implementation_head: str) -> dict[str, Any]:
    c = load_config(config_path)
    now = iso(dt(now_raw))
    source_receipt = load_json(source_dir / "receipt.json")
    projection = load_jsonl(source_dir / "future_fixture_projection.jsonl")
    require(source_receipt["status"] == "T60_SOURCE_PRECHECK_PASS", "SOURCE_PRECHECK_STATUS")
    require(source_receipt["result_fields_read"] == 0, "SOURCE_RESULT_READ")
    require(source_receipt["secret_required"] is False, "SOURCE_SECRET")
    observed = iso(dt(source_receipt["observed_at"]))
    seal_minutes = int(c["timing"]["seal_offset_minutes"])
    model_head = c["binding"]["model_head"]
    current_sha = c["binding"]["current_sha"]

    if prior_dir and (prior_dir / "runtime_state.json").exists():
        prior = load_json(prior_dir / "runtime_state.json")
        coverage_start = prior["replay_coverage_start_at"]
        registry = prior["fixture_registry"]
        ledger = prior.get("dispatch_ledger", {})
        sealed_states = prior.get("sealed_states", {})
        capture_receipts = prior.get("capture_receipts", {})
        gap_receipts = prior.get("gap_receipts", {})
        first = False
    else:
        coverage_start = now
        registry = {}
        ledger = {}
        sealed_states = {}
        capture_receipts = {}
        gap_receipts = {}
        first = True
    require(dt(coverage_start) <= dt(now), "COVERAGE_START_FROM_FUTURE")

    stats = {"inserted": 0, "unchanged": 0, "revised": 0}
    for raw in projection:
        row = source_safe_row(raw)
        status = revise(registry, row, observed, seal_minutes)
        stats[status] += 1
        update_snapshot(registry[row["fixture_id"]], row, observed, seal_minutes)

    scheduled = 0
    due = 0
    new_captures = 0
    new_gaps = 0
    for fid in sorted(registry):
        row = registry[fid]
        if row.get("terminal_capture"):
            continue
        revision = row["kickoff_revisions"][-1]
        kickoff = dt(revision["kickoff"])
        cutoff = kickoff - timedelta(minutes=seal_minutes)
        if kickoff <= dt(coverage_start):
            continue
        if dt(row["registered_at"]) > cutoff:
            if dt(now) >= cutoff:
                receipt = gap(row, now, "REGISTERED_AFTER_CUTOFF", model_head, current_sha, seal_minutes)
                gap_receipts[fid] = receipt
                row["terminal_capture"] = {"type": "gap", "receipt_id": receipt["gap_receipt_id"]}
                new_gaps += 1
            continue
        scheduled += 1
        if dt(now) < cutoff:
            continue
        due += 1
        if dt(now) >= kickoff:
            receipt = gap(row, now, "CAPTURE_WINDOW_MISSED", model_head, current_sha, seal_minutes)
            gap_receipts[fid] = receipt
            row["terminal_capture"] = {"type": "gap", "receipt_id": receipt["gap_receipt_id"]}
            new_gaps += 1
            continue
        if revision["late_revision_risk"]:
            receipt = gap(row, now, "LATE_KICKOFF_REVISION_RISK", model_head, current_sha, seal_minutes)
            gap_receipts[fid] = receipt
            row["terminal_capture"] = {"type": "gap", "receipt_id": receipt["gap_receipt_id"]}
            new_gaps += 1
            continue
        if row.get("latest_pre_cutoff_snapshot") is None:
            receipt = gap(row, now, "NO_AVAILABLE_AT_CUTOFF_SNAPSHOT", model_head, current_sha, seal_minutes)
            gap_receipts[fid] = receipt
            row["terminal_capture"] = {"type": "gap", "receipt_id": receipt["gap_receipt_id"]}
            new_gaps += 1
            continue
        state, receipt = seal_state(row, now, model_head, current_sha, seal_minutes)
        identity = {key: state[key] for key in ("fixture_id", "cutoff", "model_head", "state_sha256", "input_sha256")}
        ledger_key = sha(identity)
        if ledger_key in ledger:
            row["terminal_capture"] = {"type": "state", "receipt_id": ledger[ledger_key]["capture_receipt_id"]}
            continue
        ledger[ledger_key] = {
            "status": "COMPLETED",
            "dispatch_identity": identity,
            "completed_at": now,
            "capture_receipt_id": receipt["capture_receipt_id"],
        }
        sealed_states[fid] = state
        capture_receipts[fid] = receipt
        row["terminal_capture"] = {"type": "state", "receipt_id": receipt["capture_receipt_id"]}
        new_captures += 1

    state = {
        "schema_version": "football3-nova-t60-runtime-state-v1",
        "status": "T60_RUNTIME_ACTIVE",
        "replay_enabled": True,
        "replay_coverage_start_at": coverage_start,
        "coverage_guarantee_active": True,
        "implementation_head": implementation_head,
        "model_head": model_head,
        "current_sha": current_sha,
        "last_runtime_observed_at": now,
        "last_source_observed_at": observed,
        "fixture_registry": registry,
        "dispatch_ledger": ledger,
        "sealed_states": sealed_states,
        "capture_receipts": capture_receipts,
        "gap_receipts": gap_receipts,
        "result_fields_read": 0,
        "retroactive_coverage_fabrication": False,
    }
    receipt = {
        "schema_version": "football3-nova-t60-runtime-receipt-v1",
        "status": "T60_RUNTIME_ACTIVATED" if first else "T60_RUNTIME_TICK_PASS",
        "runtime_observed_at": now,
        "source_observed_at": observed,
        "replay_coverage_start_at": coverage_start,
        "coverage_start_created_this_run": first,
        "fixture_projection_n": len(projection),
        "registry_n": len(registry),
        "apply_stats": stats,
        "scheduled_future_n": scheduled,
        "due_n": due,
        "new_capture_n": new_captures,
        "new_gap_n": new_gaps,
        "total_capture_n": len(capture_receipts),
        "total_gap_n": len(gap_receipts),
        "runtime_state_sha256": sha(state),
        "result_fields_read": 0,
        "retroactive_coverage_fabrication": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "runtime_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--prior-dir", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--implementation-head", required=True)
    args = ap.parse_args()
    print(json.dumps(execute(args.config, args.source_dir, args.prior_dir, args.out_dir, args.now, args.implementation_head), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
