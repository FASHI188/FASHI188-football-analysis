#!/usr/bin/env python3
"""Execute queued V5.0.1 formal prediction requests reproducibly.

Each request is a complete ordinary run_formal_prediction_actionable input, including its
question-time freeze and official data-freshness evidence. A queue item is successful only when
the ordinary formal runner passes validation *and* the exact context hash appears in an immutable
prediction_freeze artifact. Failed requests are preserved and are never retried with weaker input.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUESTS = ROOT / "evidence" / "formal_prediction_requests_v501"
RUNS = ROOT / "evidence" / "formal_prediction_runs_v501"
FREEZES = ROOT / "prediction_freezes"
ENGINE = ROOT / "engine" / "run_formal_prediction_actionable.py"
STATUS = ROOT / "manifests" / "formal_prediction_queue_v501_status.json"
CURRENT_RECEIPT = ROOT / "manifests" / "v501_upgrade_status.json"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def matching_freeze(context_hash: str) -> Path | None:
    if not context_hash or not FREEZES.exists():
        return None
    for path in FREEZES.rglob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if str((row.get("match_context") or {}).get("context_hash") or "") == context_hash:
                return path
        except Exception:
            continue
    return None


def main() -> int:
    current = json.loads(CURRENT_RECEIPT.read_text(encoding="utf-8")) if CURRENT_RECEIPT.exists() else {}
    if current.get("status") != "FORMALLY_ACTIVATED_UNIQUE_CURRENT_VERIFIED" or current.get("formal_rule_version") != "V5.0.1":
        raise SystemExit("V5.0.1 formal CURRENT activation receipt missing or invalid")
    RUNS.mkdir(parents=True, exist_ok=True)
    requests = sorted(REQUESTS.glob("*.json")) if REQUESTS.exists() else []
    results = []
    failed_requests = 0
    for request in requests:
        run_dir = RUNS / request.stem
        receipt_path = run_dir / "receipt.json"
        if receipt_path.exists():
            try:
                existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                existing_status = str(existing.get("status") or "")
                results.append({"request": request.name, "status": "ALREADY_PROCESSED", "run_status": existing_status})
                failed_requests += int(existing_status != "PASS")
            except Exception:
                results.append({"request": request.name, "status": "ALREADY_PROCESSED_RECEIPT_UNREADABLE"})
                failed_requests += 1
            continue
        run_dir.mkdir(parents=True, exist_ok=True)
        context = run_dir / "context.json"
        calculation = run_dir / "calculation.json"
        validation = run_dir / "validation.json"
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "engine"), str(ROOT / "validation"), env.get("PYTHONPATH", "")]).strip(os.pathsep)
        cmd = [
            sys.executable, str(ENGINE),
            "--input", str(request),
            "--context-output", str(context),
            "--calculation-output", str(calculation),
            "--validation-output", str(validation),
            "--print-summary",
        ]
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=180)
        context_hash = None
        if context.exists():
            try:
                context_hash = str(json.loads(context.read_text(encoding="utf-8")).get("context_hash") or "")
            except Exception:
                context_hash = None
        freeze_path = matching_freeze(context_hash or "") if proc.returncode == 0 else None
        if proc.returncode != 0:
            run_status = "FAIL_FORMAL_RUN"
        elif freeze_path is None:
            run_status = "FAIL_IMMUTABLE_FREEZE_MISSING"
        else:
            run_status = "PASS"
        failed_requests += int(run_status != "PASS")
        receipt = {
            "schema_version": "V5.0.1-formal-prediction-queue-receipt-r2",
            "processed_at_utc": now(),
            "status": run_status,
            "formal_rule_version": "V5.0.1",
            "request_path": str(request.relative_to(ROOT)),
            "context_path": str(context.relative_to(ROOT)) if context.exists() else None,
            "calculation_path": str(calculation.relative_to(ROOT)) if calculation.exists() else None,
            "validation_path": str(validation.relative_to(ROOT)) if validation.exists() else None,
            "context_hash": context_hash,
            "formal_freeze_path": str(freeze_path.relative_to(ROOT)) if freeze_path else None,
            "formal_freeze_file_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest() if freeze_path else None,
            "return_code": proc.returncode,
            "stdout_tail": proc.stdout[-12000:],
            "stderr_tail": proc.stderr[-12000:],
            "governance": {
                "request_input_is_authoritative": True,
                "no_input_weakening_on_failure": True,
                "validated_prediction_auto_freeze_required": True,
                "formal_probability_change_by_queue": False,
            },
        }
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append({"request": request.name, "status": "PROCESSED", "run_status": run_status, "return_code": proc.returncode, "formal_freeze_present": freeze_path is not None})
    payload = {
        "schema_version": "V5.0.1-formal-prediction-queue-status-r2",
        "generated_at_utc": now(),
        "status": "PASS" if failed_requests == 0 else "WARN_REQUEST_FAILURES",
        "request_count": len(requests),
        "failed_request_count": failed_requests,
        "results": results,
        "current_rule_version": "V5.0.1",
        "governance": {"fail_closed_per_request": True, "immutable_freeze_required_for_pass": True, "automatic_probability_promotion": False},
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if failed_requests == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
