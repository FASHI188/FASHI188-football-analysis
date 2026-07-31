#!/usr/bin/env python3
"""Production workflow controls, digest normalization, evidence gates, and real fault tests."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

from e3g0d_common import DAILY_CAP, E3Error, packed, sha

DIGEST_RE = re.compile(r"[0-9a-fA-F]{64}")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,240}")
LIVE_MODES = {"build-plan", "odds", "injuries", "lineup-window"}
MODES = {"self-test", "preflight", "status-check", *LIVE_MODES}
SCHEDULES = {
    "5 0 * * *": ("build-plan", "10", "1"),
    "10 */3 * * *": ("odds", "10", "2"),
    "20 */4 * * *": ("injuries", "10", "2"),
    "*/15 10-22 * * *": ("lineup-window", "10", "6"),
}


def normalize_artifact_digest(value: Any, failure_class: str = "ARTIFACT_DIGEST_INVALID") -> str:
    """Accept upload-artifact raw hex or canonical sha256 form; return canonical lower-case form."""
    text = str(value or "").strip()
    if text.lower().startswith("sha256:"):
        text = text[7:]
    elif ":" in text:
        raise E3Error(failure_class, "unsupported Artifact digest algorithm")
    if not DIGEST_RE.fullmatch(text):
        raise E3Error(failure_class, "Artifact digest must contain exactly 64 hexadecimal digits")
    return f"sha256:{text.lower()}"


def digest_hex(value: Any, failure_class: str = "ARTIFACT_DIGEST_INVALID") -> str:
    return normalize_artifact_digest(value, failure_class).split(":", 1)[1]


def _value(env: Mapping[str, Any], name: str, pattern: str, *, empty: bool = False) -> str:
    raw = str(env.get(name, "") or "")
    if any(char in raw for char in "\r\n\0"):
        raise E3Error("WORKFLOW_CONTROL_INVALID", f"invalid {name}")
    if not raw:
        if empty:
            return ""
        raise E3Error("WORKFLOW_CONTROL_INVALID", f"missing {name}")
    if not re.fullmatch(pattern, raw):
        raise E3Error("WORKFLOW_CONTROL_INVALID", f"invalid {name}")
    return raw


def resolve_controls(env: Mapping[str, Any], now: dt.datetime | None = None) -> dict[str, str]:
    """The single production parser for workflow inputs and scheduled controls."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    event = _value(env, "EVENT_NAME", r"workflow_dispatch|schedule")
    head = _value(env, "EXPECTED_HEAD", r"[0-9a-f]{40}")
    run_id = _value(env, "GITHUB_RUN_ID", r"[1-9][0-9]*")
    attempt = _value(env, "GITHUB_RUN_ATTEMPT", r"[1-9][0-9]*")
    today = now.date().isoformat()

    if event == "workflow_dispatch":
        mode = _value(env, "INPUT_MODE", r"self-test|preflight|status-check|build-plan|odds|injuries|lineup-window")
        target = _value(env, "INPUT_TARGET", r"\d{4}-\d{2}-\d{2}", empty=True) or today
        try:
            dt.date.fromisoformat(target)
        except ValueError as exc:
            raise E3Error("WORKFLOW_CONTROL_INVALID", "invalid target date") from exc
        league = _value(env, "INPUT_LEAGUE", r"39")
        season = _value(env, "INPUT_SEASON", r"2026")
        limit = _value(env, "INPUT_LIMIT", r"[1-9]|1[0-9]|20")
        maximum = _value(env, "INPUT_MAX", r"[1-9]|1[0-9]|20")
        plan_id = _value(env, "INPUT_PLAN_ID", r"[1-9][0-9]*", empty=True)
        plan_sha = _value(env, "INPUT_PLAN_SHA", r"[0-9a-fA-F]{64}", empty=True).lower()
        dry = _value(env, "INPUT_DRY", r"true|false")
        no_network = _value(env, "INPUT_NETWORK", r"true|false")
        upload = _value(env, "INPUT_UPLOAD", r"true|false")
        allow = _value(env, "INPUT_ALLOW", r"true|false")
    else:
        cron = _value(env, "CRON_VALUE", r"[0-9*/ ,\-]+")
        if cron not in SCHEDULES:
            raise E3Error("WORKFLOW_CONTROL_INVALID", "unknown schedule")
        mode, limit, maximum = SCHEDULES[cron]
        target, league, season = today, "39", "2026"
        plan_id = plan_sha = ""
        dry, no_network, upload, allow = "false", "false", "true", "true"

    live = mode in LIVE_MODES
    if event == "workflow_dispatch" and live and (int(limit) > 1 or int(maximum) > 3):
        raise E3Error("WORKFLOW_CONTROL_INVALID", "trial envelope exceeded")
    ref = _value(env, "REF_VALUE", r"refs/heads/[A-Za-z0-9._/-]+")
    enabled = str(env.get("API_FOOTBALL_COLLECTOR_ENABLED", "false")) == "true"
    schedule_enabled = str(env.get("API_FOOTBALL_SCHEDULE_ENABLED", "false")) == "true"
    ok = (
        live
        and upload == "true"
        and ref == "refs/heads/main"
        and enabled
        and dry == "false"
        and no_network == "false"
        and (event == "workflow_dispatch" or (schedule_enabled and allow == "true"))
    )
    expires = (now + dt.timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "request_day": today,
        "mode": mode,
        "target": target,
        "league": league,
        "season": season,
        "limit": limit,
        "max": maximum,
        "plan_id": plan_id,
        "plan_sha": plan_sha,
        "ok": str(ok).lower(),
        "blocked_live": str(live and not ok).lower(),
        "expires": expires,
        "evidence_head": head,
        "run_id": run_id,
        "run_attempt": attempt,
    }


def write_github_outputs(values: Mapping[str, Any], path: str | Path) -> None:
    target = Path(path)
    with target.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = str(value)
            if any(char in text for char in "\r\n\0"):
                raise E3Error("WORKFLOW_CONTROL_INVALID", f"unsafe output {key}")
            handle.write(f"{key}={text}\n")


def reservation_identity(head: str, run_id: str, attempt: str, request_day: str) -> tuple[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise E3Error("WORKFLOW_CONTROL_INVALID", "invalid reservation head")
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
        raise E3Error("WORKFLOW_CONTROL_INVALID", "invalid run identity")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", request_day):
        raise E3Error("WORKFLOW_CONTROL_INVALID", "invalid reservation day")
    reservation_id = f"{head}:{run_id}:{attempt}"
    name = f"football-e3g0d-quota-reservation-{request_day}-{head}-{run_id}-attempt-{attempt}"
    if not SAFE_NAME_RE.fullmatch(name):
        raise E3Error("WORKFLOW_CONTROL_INVALID", "unsafe reservation Artifact name")
    return reservation_id, name


def count_reservations(rows: list[Mapping[str, Any]]) -> int:
    """Conservatively count immutable reservations; final receipts cannot reduce this total."""
    unique: dict[str, tuple[int, str, str]] = {}
    for row in rows:
        rid = str(row.get("reservation_id") or "")
        reserved = row.get("reserved_attempts")
        run_id = str(row.get("workflow_run_id") or "")
        attempt = str(row.get("workflow_run_attempt") or "")
        if not rid or any(char in rid for char in "\r\n\0"):
            raise E3Error("QUOTA_STATE_UNTRUSTED", "reservation identity invalid")
        if not isinstance(reserved, int) or not 1 <= reserved <= 20:
            raise E3Error("QUOTA_STATE_UNTRUSTED", "reservation amount invalid")
        current = (reserved, run_id, attempt)
        if rid in unique and unique[rid] != current:
            raise E3Error("QUOTA_STATE_UNTRUSTED", "conflicting duplicate reservation")
        unique[rid] = current
    total = sum(item[0] for item in unique.values())
    if total > DAILY_CAP:
        raise E3Error("QUOTA_STATE_UNTRUSTED", "reservation total exceeds safety cap")
    return total


def provider_allowed(controls_ok: str, needs_requests: str, reservation_upload: str) -> bool:
    return controls_ok == "true" and needs_requests == "true" and reservation_upload == "success"


def create_reservation(env: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    maximum = int(_value(env, "MAXIMUM", r"[1-9]|1[0-9]|20"))
    used = int(_value(env, "USED", r"0|[1-8]?[0-9]|90"))
    if used + maximum > DAILY_CAP:
        raise E3Error("PROVIDER_QUOTA_RESERVE_REACHED")
    head = _value(env, "HEAD_VALUE", r"[0-9a-f]{40}")
    run = _value(env, "RUN_VALUE", r"[1-9][0-9]*")
    attempt = _value(env, "ATTEMPT_VALUE", r"[1-9][0-9]*")
    request_day = _value(env, "REQUEST_DAY", r"\d{4}-\d{2}-\d{2}")
    reservation_id, name = reservation_identity(head, run, attempt, request_day)
    row = {
        "schema_version": "E3G0D-QUOTA-RESERVATION-1.0",
        "deployment_status": "IMPLEMENTED_NOT_LIVE",
        "provider": "API-Football",
        "collector_mode": _value(env, "MODE_VALUE", r"build-plan|odds|injuries|lineup-window"),
        "request_day_utc": request_day,
        "target_date_utc": _value(env, "TARGET", r"\d{4}-\d{2}-\d{2}"),
        "reservation_id": reservation_id,
        "reserved_attempts": maximum,
        "requests_used_before_reservation": used,
        "workflow_run_id": run,
        "workflow_run_attempt": attempt,
        "run_head": head,
        "evidence_head": head,
        "artifact_name": name,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retention_days": 30,
        "expires_at_utc": _value(env, "EXPIRES", r"[^\r\n\0]+"),
        "final_status": "RESERVED_BEFORE_PROVIDER",
        "append_only": True,
        "formal_weight": 0,
    }
    destination.mkdir(parents=True, exist_ok=True)
    raw = packed(row) + b"\n"
    path = destination / "quota_reservation.json"
    path.write_bytes(raw)
    return {"row": row, "reservation_sha256": sha(raw), "reservation_path": path.as_posix()}


def build_plan_index(env: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    artifact_id = _value(env, "ARTIFACT_ID", r"[1-9][0-9]*")
    artifact_digest = normalize_artifact_digest(env.get("ARTIFACT_DIGEST"), "PLAN_INDEX_MISMATCH")
    plan_sha = _value(env, "PLAN_SHA", r"[0-9a-f]{64}")
    receipt_path = Path(_value(env, "RECEIPT", r"[^\r\n\0]+"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    source = str(((receipt.get("snapshots") or [{}])[0]).get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source):
        raise E3Error("PLAN_INDEX_MISMATCH", "source raw SHA is invalid")
    row = {
        "schema_version": "E3G0D-PLAN-INDEX-1.1",
        "plan_artifact_id": int(artifact_id),
        "plan_artifact_name": _value(env, "ARTIFACT_NAME", r"[A-Za-z0-9._-]{1,240}"),
        "plan_artifact_digest": artifact_digest,
        "plan_sha256": plan_sha,
        "source_raw_response_sha256": source,
        "workflow_run_id": f"{_value(env, 'RUN_VALUE', r'[1-9][0-9]*')}-attempt-{_value(env, 'ATTEMPT_VALUE', r'[1-9][0-9]*')}",
        "workflow_run_attempt": _value(env, "ATTEMPT_VALUE", r"[1-9][0-9]*"),
        "run_head": _value(env, "HEAD_VALUE", r"[0-9a-f]{40}"),
        "evidence_head": _value(env, "HEAD_VALUE", r"[0-9a-f]{40}"),
        "request_day_utc": _value(env, "REQUEST_DAY", r"\d{4}-\d{2}-\d{2}"),
        "target_date_utc": _value(env, "TARGET", r"\d{4}-\d{2}-\d{2}"),
        "competition_id": int(_value(env, "LEAGUE", r"[1-9][0-9]*")),
        "season_id": int(_value(env, "SEASON", r"[0-9]{4}")),
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retention_days": 30,
        "expires_at_utc": _value(env, "EXPIRES", r"[^\r\n\0]+"),
        "append_only": True,
        "formal_weight": 0,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "plan_index_receipt.json").write_bytes(packed(row) + b"\n")
    return row


def prepare_final_evidence(env: Mapping[str, Any], runner_temp: Path) -> dict[str, Any]:
    final_dir = runner_temp / "final-evidence"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True)
    reservation_dir = runner_temp / "reservation"
    if not reservation_dir.is_dir():
        raise E3Error("EVIDENCE_MISSING", "reservation evidence is missing")
    shutil.copytree(reservation_dir, final_dir / "reservation")
    out_dir = runner_temp / "out"
    if out_dir.is_dir():
        shutil.copytree(out_dir, final_dir / "collector-output")

    receipt: dict[str, Any] | None = None
    receipt_raw = str(env.get("RECEIPT") or "")
    if receipt_raw:
        receipt_path = Path(receipt_raw).resolve()
        allowed = out_dir.resolve()
        if receipt_path.is_relative_to(allowed) and receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    actual = int((receipt or {}).get("request_attempts", 0))
    manifests = out_dir / "manifests"
    successful = min(actual, len(list(manifests.rglob("*.manifest.json"))) if manifests.exists() else 0)
    failed = max(0, actual - successful)
    success = str(env.get("COLLECTOR_OUTCOME")) == "success" and (receipt or {}).get("outcome") == "SUCCESS"
    reservation_digest = normalize_artifact_digest(env.get("RESERVATION_ARTIFACT_DIGEST"), "QUOTA_STATE_UNTRUSTED")
    row = {
        "schema_version": "E3G0D-QUOTA-FINAL-RECEIPT-1.0",
        "deployment_status": "IMPLEMENTED_NOT_LIVE",
        "provider": "API-Football",
        "collector_mode": _value(env, "MODE_VALUE", r"build-plan|odds|injuries|lineup-window"),
        "request_day_utc": _value(env, "REQUEST_DAY", r"\d{4}-\d{2}-\d{2}"),
        "target_date_utc": _value(env, "TARGET", r"\d{4}-\d{2}-\d{2}"),
        "reservation_id": _value(env, "RESERVATION_ID", r"[^\r\n\0]+"),
        "reservation_artifact_id": int(_value(env, "RESERVATION_ARTIFACT_ID", r"[1-9][0-9]*")),
        "reservation_artifact_name": _value(env, "RESERVATION_ARTIFACT_NAME", r"[A-Za-z0-9._-]{1,240}"),
        "reservation_artifact_digest": reservation_digest,
        "reservation_sha256": _value(env, "RESERVATION_SHA", r"[0-9a-f]{64}"),
        "reserved_attempts": int(_value(env, "RESERVED", r"[1-9]|1[0-9]|20")),
        "actual_request_attempts": actual,
        "successful_requests": successful,
        "failed_requests": failed,
        "workflow_run_id": _value(env, "RUN_VALUE", r"[1-9][0-9]*"),
        "workflow_run_attempt": _value(env, "ATTEMPT_VALUE", r"[1-9][0-9]*"),
        "run_head": _value(env, "HEAD_VALUE", r"[0-9a-f]{40}"),
        "evidence_head": _value(env, "HEAD_VALUE", r"[0-9a-f]{40}"),
        "final_status": "SUCCESS" if success else ("FAILED" if receipt else "FINAL_RECEIPT_MISSING"),
        "failure_class": (receipt or {}).get("failure_class") or (None if success else "COLLECTOR_FAILED_OR_INTERRUPTED"),
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retention_days": 30,
        "expires_at_utc": _value(env, "EXPIRES", r"[^\r\n\0]+"),
        "append_only": True,
        "formal_weight": 0,
    }
    (final_dir / "quota_final_receipt.json").write_bytes(packed(row) + b"\n")
    return row


def final_gate(env: Mapping[str, Any]) -> tuple[bool, str]:
    mode = str(env.get("MODE_VALUE") or "")
    ok = str(env.get("OK_VALUE") or "")
    blocked = str(env.get("BLOCKED_LIVE") or "")
    needs = str(env.get("NEEDS_REQUESTS") or "")
    if blocked == "true":
        return False, "blocked live execution"
    if ok != "true":
        return (mode == "status-check" or str(env.get("NO_NETWORK_UPLOAD")) == "success", "no-network evidence")
    if needs == "false":
        return (str(env.get("NO_NETWORK_UPLOAD")) == "success", "no-request evidence")
    required = ["RESERVATION_UPLOAD", "EVIDENCE_MANIFEST", "FINAL_RECEIPT_UPLOAD"]
    if any(str(env.get(key)) != "success" for key in required):
        return False, "mandatory evidence upload failed"
    if str(env.get("COLLECTOR_OUTCOME")) != "success":
        return False, "collector failed"
    if mode == "build-plan":
        return (
            str(env.get("PLAN_UPLOAD")) == "success" and str(env.get("PLAN_INDEX_UPLOAD")) == "success",
            "plan evidence",
        )
    return (str(env.get("SNAPSHOT_UPLOAD")) == "success", "snapshot evidence")


def main() -> int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    for name in ("resolve-controls","create-reservation","provider-gate","build-plan-index","final-evidence","final-gate"): sub.add_parser(name)
    args=parser.parse_args()
    try:
        if args.command=="resolve-controls": write_github_outputs(resolve_controls(os.environ),os.environ["GITHUB_OUTPUT"])
        elif args.command=="create-reservation":
            result=create_reservation(os.environ,Path(os.environ["RUNNER_TEMP"])/"reservation")
            write_github_outputs({"reservation_id":result["row"]["reservation_id"],"reservation_sha256":result["reservation_sha256"]},os.environ["GITHUB_OUTPUT"])
        elif args.command=="provider-gate":
            allowed=provider_allowed(str(os.environ.get("CONTROLS_OK") or ""),str(os.environ.get("NEEDS_REQUESTS") or ""),str(os.environ.get("RESERVATION_UPLOAD") or ""))
            write_github_outputs({"allowed":str(allowed).lower()},os.environ["GITHUB_OUTPUT"])
        elif args.command=="build-plan-index": build_plan_index(os.environ,Path(os.environ["RUNNER_TEMP"])/"index")
        elif args.command=="final-evidence": prepare_final_evidence(os.environ,Path(os.environ["RUNNER_TEMP"]))
        elif args.command=="final-gate":
            passed,reason=final_gate(os.environ)
            if not passed: print(f"E3g-0D final gate failed: {reason}",file=sys.stderr); return 2
    except (E3Error,OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        failure=exc.failure_class if isinstance(exc,E3Error) else "VALIDATION_FAILED"
        print(f"E3g-0D runtime error [{failure}]",file=sys.stderr); return 2
    return 0

if __name__=="__main__": raise SystemExit(main())
