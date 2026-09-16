#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class AdapterError(RuntimeError):
    pass


def require(ok: bool, msg: str) -> None:
    if not ok:
        raise AdapterError(msg)


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


def load_config(path: Path) -> dict[str, Any]:
    c = json.loads(path.read_text(encoding="utf-8"))
    require(c["status"] == "DESIGN_LOCKED_ADAPTER_CANDIDATE_NOT_ENABLED", "CONFIG_STATUS")
    a = c["activation"]
    require(a["enabled"] is False, "ADAPTER_MUST_NOT_ENABLE_REPLAY")
    require(a["replay_coverage_start_at"] is None, "COVERAGE_START_MUST_REMAIN_NULL")
    require(a["coverage_guarantee_active"] is False and a["pre_enablement_coverage_claimed"] is False, "PRE_ENABLE_COVERAGE_FORBIDDEN")
    x = c["adapter_contract"]
    require(x["runtime_ledger_write_allowed"] is False, "RUNTIME_LEDGER_WRITE_FORBIDDEN")
    require(x["result_fields_read"] == 0, "RESULT_READ_FORBIDDEN")
    require(x["retroactive_coverage_fabrication"] is False, "RETROACTIVE_COVERAGE_FORBIDDEN")
    require(x["formal_v2_changed"] is False and x["current_changed"] is False and x["production_changed"] is False, "FORMAL_BOUNDARY")
    require(x["candidate_weight"] == 0 and x["matrix_delta"] == 0, "WEIGHT_BOUNDARY")
    return c


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        x = json.loads(line)
        require(isinstance(x, dict), f"PROJECTION_ROW_NOT_OBJECT:{i}")
        out.append(x)
    return out


def validate_bindings(c: dict[str, Any], source_receipt: dict[str, Any], foundation_receipt: dict[str, Any], projection: list[dict[str, Any]]) -> None:
    sb = c["source_binding"]
    fb = c["foundation_binding"]
    require(source_receipt.get("status") == sb["receipt_status"], "SOURCE_RECEIPT_STATUS")
    require(source_receipt.get("replay_enabled") is False, "SOURCE_REPLAY_ALREADY_ENABLED")
    require(source_receipt.get("replay_coverage_start_at") is None, "SOURCE_COVERAGE_ALREADY_STARTED")
    require(source_receipt.get("coverage_guarantee_active") is False, "SOURCE_COVERAGE_GUARANTEE_ALREADY_ACTIVE")
    require(source_receipt.get("result_fields_read") == 0, "SOURCE_RESULT_READ")
    require(source_receipt.get("secret_required") is False, "SOURCE_SECRET_ROUTE")
    require(source_receipt.get("formal_v2_changed") is False and source_receipt.get("current_changed") is False and source_receipt.get("production_changed") is False, "SOURCE_FORMAL_BOUNDARY")
    require(source_receipt.get("fixture_projection_sha256") == sb["fixture_projection_sha256"], "SOURCE_RECEIPT_PROJECTION_SHA")
    require(len(projection) == int(sb["future_fixture_n"]), f"PROJECTION_N:{len(projection)}")
    require(sha(projection) == sb["fixture_projection_sha256"], "PROJECTION_SHA")
    counts: dict[str, int] = {}
    ids: set[str] = set()
    allowed = set(c["adapter_contract"]["competitions"])
    for row in projection:
        for k in ("fixture_id", "competition", "season", "kickoff"):
            require(str(row.get(k) or "").strip(), f"PROJECTION_FIELD_MISSING:{k}")
        fid = str(row["fixture_id"])
        require(fid not in ids, f"DUPLICATE_FIXTURE_ID:{fid}")
        ids.add(fid)
        comp = str(row["competition"])
        require(comp in allowed, f"UNSUPPORTED_COMPETITION:{comp}")
        dt(str(row["kickoff"]))
        counts[comp] = counts.get(comp, 0) + 1
    require(counts == {k: int(v) for k, v in sb["future_fixture_n_by_competition"].items()}, f"COMPETITION_COUNTS:{counts}")
    require(foundation_receipt.get("status") == fb["receipt_status"], "FOUNDATION_RECEIPT_STATUS")
    require(foundation_receipt.get("activation_enabled") is False, "FOUNDATION_ALREADY_ENABLED")
    require(foundation_receipt.get("replay_coverage_start_at") is None, "FOUNDATION_COVERAGE_ALREADY_STARTED")
    require(foundation_receipt.get("coverage_guarantee_active") is False, "FOUNDATION_COVERAGE_GUARANTEE_ALREADY_ACTIVE")
    require(foundation_receipt.get("deterministic_state_replay") is True, "FOUNDATION_DETERMINISM")
    require(foundation_receipt.get("reservation_duplicate_suppressed") is True, "FOUNDATION_RESERVATION_DEDUPE")
    require(foundation_receipt.get("completed_duplicate_suppressed") is True, "FOUNDATION_COMPLETED_DEDUPE")
    require(foundation_receipt.get("result_fields_read") == 0 and foundation_receipt.get("retroactive_fabrication") is False, "FOUNDATION_SAFETY")
    require(foundation_receipt.get("formal_v2_changed") is False and foundation_receipt.get("current_changed") is False and foundation_receipt.get("production_changed") is False, "FOUNDATION_FORMAL_BOUNDARY")


def _revision(kickoff: str, observed_at: str, revision_no: int, seal_offset_minutes: int) -> dict[str, Any]:
    k, o = dt(kickoff), dt(observed_at)
    return {
        "revision_no": revision_no,
        "kickoff": iso(k),
        "observed_at": iso(o),
        "late_revision_risk": o > k - timedelta(minutes=seal_offset_minutes),
    }


def apply_projection(registry: dict[str, Any], projection: list[dict[str, Any]], *, observed_at: str, seal_offset_minutes: int) -> dict[str, int]:
    o = iso(dt(observed_at))
    stats = {"inserted": 0, "unchanged": 0, "revised": 0}
    for row in sorted(projection, key=lambda x: str(x["fixture_id"])):
        fid = str(row["fixture_id"])
        comp = str(row["competition"])
        season = str(row["season"])
        kickoff = iso(dt(str(row["kickoff"])))
        old = registry.get(fid)
        if old is None:
            registry[fid] = {
                "fixture_id": fid,
                "competition": comp,
                "season": season,
                "registered_at": o,
                "kickoff_revisions": [_revision(kickoff, o, 1, seal_offset_minutes)],
            }
            stats["inserted"] += 1
            continue
        require(old["competition"] == comp and old["season"] == season, f"FIXTURE_IDENTITY_DRIFT:{fid}")
        last = old["kickoff_revisions"][-1]
        require(dt(o) >= dt(last["observed_at"]), f"OBSERVED_AT_NOT_MONOTONIC:{fid}")
        if last["kickoff"] == kickoff:
            stats["unchanged"] += 1
            continue
        old["kickoff_revisions"].append(_revision(kickoff, o, int(last["revision_no"]) + 1, seal_offset_minutes))
        stats["revised"] += 1
    return stats


def build_capture_plan(registry: dict[str, Any], *, seal_offset_minutes: int, replay_enabled: bool, coverage_start: str | None) -> list[dict[str, Any]]:
    require(replay_enabled is False, "DRY_RUN_CANNOT_ENABLE_REPLAY")
    require(coverage_start is None, "DRY_RUN_COVERAGE_START_MUST_BE_NULL")
    rows = []
    for fid in sorted(registry):
        x = registry[fid]
        r = x["kickoff_revisions"][-1]
        k = dt(r["kickoff"])
        rows.append({
            "fixture_id": fid,
            "competition": x["competition"],
            "season": x["season"],
            "kickoff": iso(k),
            "cutoff": iso(k - timedelta(minutes=seal_offset_minutes)),
            "kickoff_revision_no": int(r["revision_no"]),
            "scheduled": False,
            "status": "PENDING_ACTIVATION",
            "reason": "REPLAY_NOT_ENABLED_COVERAGE_START_NULL",
        })
    return rows


def execute(config_path: Path, source_dir: Path, foundation_dir: Path, out_dir: Path, implementation_head: str) -> dict[str, Any]:
    c = load_config(config_path)
    source_receipt = load_json(source_dir / "receipt.json")
    foundation_receipt = load_json(foundation_dir / "simulation_receipt.json")
    projection = load_jsonl(source_dir / "future_fixture_projection.jsonl")
    validate_bindings(c, source_receipt, foundation_receipt, projection)
    observed_at = str(source_receipt["observed_at"])
    seal = int(c["foundation_binding"]["seal_offset_minutes"])

    registry: dict[str, Any] = {}
    first = apply_projection(registry, projection, observed_at=observed_at, seal_offset_minutes=seal)
    second = apply_projection(registry, projection, observed_at=observed_at, seal_offset_minutes=seal)
    require(first == {"inserted": len(projection), "unchanged": 0, "revised": 0}, f"FIRST_APPLY:{first}")
    require(second == {"inserted": 0, "unchanged": len(projection), "revised": 0}, f"IDEMPOTENCE:{second}")

    simulation_registry = copy.deepcopy(registry)
    candidates = sorted(projection, key=lambda x: (str(x["kickoff"]), str(x["fixture_id"])), reverse=True)
    require(bool(candidates), "NO_REVISION_SIMULATION_CANDIDATE")
    chosen = copy.deepcopy(candidates[0])
    chosen["kickoff"] = iso(dt(str(chosen["kickoff"])) + timedelta(minutes=15))
    simulated_observed = iso(dt(observed_at) + timedelta(minutes=1))
    revision_stats = apply_projection(simulation_registry, [chosen], observed_at=simulated_observed, seal_offset_minutes=seal)
    require(revision_stats == {"inserted": 0, "unchanged": 0, "revised": 1}, f"REVISION_APPEND:{revision_stats}")
    require(len(simulation_registry) == len(registry), "REVISION_CHANGED_REGISTRY_SIZE")
    require(len(simulation_registry[str(chosen["fixture_id"])]["kickoff_revisions"]) == 2, "REVISION_NOT_APPENDED")

    plan = build_capture_plan(registry, seal_offset_minutes=seal, replay_enabled=c["activation"]["enabled"], coverage_start=c["activation"]["replay_coverage_start_at"])
    require(len(plan) == len(projection), "CAPTURE_PLAN_N")
    require(sum(1 for x in plan if x["scheduled"]) == 0, "PRE_ENABLE_CAPTURE_SCHEDULED")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fixture_registry_dry_run.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "capture_plan_dry_run.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in plan), encoding="utf-8")
    receipt = {
        "schema_version": "football3-nova-t60-adapter-candidate-receipt-v1",
        "status": "T60_ADAPTER_CANDIDATE_DRY_RUN_PASS",
        "implementation_head": implementation_head,
        "source_run_id": c["source_binding"]["run_id"],
        "source_artifact_id": c["source_binding"]["artifact_id"],
        "source_artifact_digest": c["source_binding"]["artifact_digest"],
        "source_projection_sha256": c["source_binding"]["fixture_projection_sha256"],
        "foundation_head_sha": c["foundation_binding"]["head_sha"],
        "foundation_run_id": c["foundation_binding"]["run_id"],
        "foundation_artifact_id": c["foundation_binding"]["artifact_id"],
        "foundation_artifact_digest": c["foundation_binding"]["artifact_digest"],
        "fixture_n": len(projection),
        "fixture_n_by_competition": c["source_binding"]["future_fixture_n_by_competition"],
        "first_apply_inserted_n": first["inserted"],
        "second_apply_unchanged_n": second["unchanged"],
        "same_projection_idempotent": True,
        "simulated_kickoff_revision_fixture_id": str(chosen["fixture_id"]),
        "simulated_kickoff_revision_append_only": True,
        "fixture_registry_sha256": sha(registry),
        "capture_plan_sha256": sha(plan),
        "capture_plan_n": len(plan),
        "active_capture_scheduled_n": 0,
        "capture_plan_status": "PENDING_ACTIVATION",
        "runtime_ledger_written": False,
        "replay_enabled": False,
        "replay_coverage_start_at": None,
        "coverage_guarantee_active": False,
        "pre_enablement_coverage_claimed": False,
        "result_fields_read": 0,
        "retroactive_coverage_fabrication": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "allowed_next_phase": "CANDIDATE_FROZEN_ACTIVATION_PRECHECK"
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--foundation-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--implementation-head", required=True)
    a = ap.parse_args()
    print(json.dumps(execute(a.config, a.source_dir, a.foundation_dir, a.out_dir, a.implementation_head), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
