#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

API_VERSION = "2026-03-10"
ARTIFACT_PREFIX = "football-v520-r44a-forward-pit-"
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def fail(message: str) -> None:
    raise RuntimeError(message)


def api_json(url: str, token: str | None) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def api_bytes(url: str, token: str | None) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def list_artifacts(repository: str, branch: str | None, token: str | None) -> list[dict[str, Any]]:
    owner, repo = repository.split("/", 1)
    out: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        data = api_json(f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts?per_page=100&page={page}", token)
        rows = data.get("artifacts") or []
        for row in rows:
            if row.get("expired"):
                continue
            name = str(row.get("name") or "")
            if not name.startswith(ARTIFACT_PREFIX):
                continue
            wr = row.get("workflow_run") or {}
            if branch and wr.get("head_branch") != branch:
                continue
            out.append(row)
        if len(rows) < 100:
            break
        page += 1
    return out


def read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        return zf.read(name)
    except KeyError as exc:
        fail(f"ARTIFACT_MEMBER_MISSING:{name}")
        raise AssertionError from exc


def evaluate_artifact(meta: dict[str, Any], zip_raw: bytes, contract: dict[str, Any]) -> dict[str, Any]:
    digest = str(meta.get("digest") or "")
    zip_digest = sha256(zip_raw)
    digest_ok = digest == f"sha256:{zip_digest}" if digest else False
    with zipfile.ZipFile(io.BytesIO(zip_raw)) as zf:
        receipt_raw = read_member(zf, "exact_market/exact_payload_market_receipt_r44a.json")
        receipt_sha = read_member(zf, "exact_market/exact_payload_market_receipt_r44a.sha256").decode("ascii").strip()
        structured_raw = read_member(zf, "exact_market/structured_visible_quotes_r44a.json")
        structured_sha = read_member(zf, "exact_market/structured_visible_quotes_r44a.sha256").decode("ascii").strip()
    if receipt_sha != sha256(receipt_raw):
        fail(f"RECEIPT_SHA_MISMATCH:{meta.get('id')}")
    if structured_sha != sha256(structured_raw):
        fail(f"STRUCTURED_SHA_MISMATCH:{meta.get('id')}")
    r = json.loads(receipt_raw)
    s = json.loads(structured_raw)

    fixture = contract["fixture"]
    if r.get("fixture") != fixture or s.get("fixture") != fixture:
        fail(f"FIXTURE_IDENTITY_MISMATCH:{meta.get('id')}")
    if r.get("formal_market_snapshot") is not False or r.get("formal_execution_ready") is not False:
        fail(f"FORMAL_BOUNDARY_INVALID:{meta.get('id')}")
    if s.get("formal_market_snapshot") is not False or s.get("formal_execution_ready") is not False:
        fail(f"STRUCTURED_FORMAL_BOUNDARY_INVALID:{meta.get('id')}")
    for key in ("target_labels_accessed", "settlement_results_accessed", "model_fits", "candidate_probabilities", "fixed100_consumed", "fixed200_consumed", "ev_calculations", "formal_weight", "formal_model_changes", "formal_data_changes", "formal_config_changes", "CURRENT_changes"):
        if r.get(key) != 0:
            fail(f"HARD_BOUNDARY_NONZERO:{key}:{meta.get('id')}")
    if not r.get("bookmaker_specific_quotes_extracted") or not r.get("structured_visible_prices_extracted"):
        fail(f"STRUCTURED_EXTRACTION_NOT_PROVEN:{meta.get('id')}")
    if r.get("structured_quote_document_sha256") != structured_sha:
        fail(f"STRUCTURED_DOCUMENT_LINK_MISMATCH:{meta.get('id')}")
    if r.get("source_payload_sha256") != s.get("source_payload_sha256"):
        fail(f"SOURCE_PAYLOAD_LINK_MISMATCH:{meta.get('id')}")

    schema = r.get("schema_version")
    run_identity = r.get("run_identity") or {}
    identity_quality = "LEGACY_UNTRUSTED_FOR_TARGET_WINDOW"
    if schema == "V520-R44A-EXACT-PAYLOAD-MARKET-RECEIPT-1.1":
        checked = str(run_identity.get("checked_out_head_sha") or "")
        artifact_head = str((meta.get("workflow_run") or {}).get("head_sha") or "")
        head_ok = bool(SHA_RE.fullmatch(checked)) and checked == artifact_head
        identity_quality = "TRUSTED" if head_ok else "INVALID"
    elif schema != "V520-R44A-EXACT-PAYLOAD-MARKET-RECEIPT-1.0":
        fail(f"UNSUPPORTED_RECEIPT_SCHEMA:{schema}:{meta.get('id')}")

    observed = parse_utc(str(r["collector_first_observed_at_utc"]))
    kickoff = parse_utc(str(fixture["scheduled_kickoff_utc"]))
    hours_to_kickoff = (kickoff - observed).total_seconds() / 3600.0
    cfg = contract["market_source"]["structured_extraction"]
    tolerance = float(cfg["freeze_window_tolerance_minutes"])
    targets = {x["label"]: float(x["hours_to_kickoff"]) for x in cfg["freeze_window_targets"]}
    nearest_label, nearest_hours = min(targets.items(), key=lambda kv: abs(hours_to_kickoff - kv[1]))
    nearest_delta_minutes = abs(hours_to_kickoff - nearest_hours) * 60.0
    matched = nearest_delta_minutes <= tolerance
    target_label = nearest_label if matched else None

    recorded = r.get("freeze_window") or {}
    if bool(recorded.get("matched_target_window")) != matched:
        fail(f"FREEZE_WINDOW_RECOMPUTE_MISMATCH:{meta.get('id')}")
    if recorded.get("target_window_label") != target_label:
        fail(f"FREEZE_WINDOW_LABEL_MISMATCH:{meta.get('id')}")

    eligible = matched and identity_quality == "TRUSTED" and digest_ok
    return {
        "artifact_id": meta.get("id"),
        "artifact_name": meta.get("name"),
        "artifact_digest": digest,
        "artifact_digest_verified": digest_ok,
        "workflow_run_id": (meta.get("workflow_run") or {}).get("id"),
        "workflow_head_sha": (meta.get("workflow_run") or {}).get("head_sha"),
        "receipt_schema_version": schema,
        "identity_quality": identity_quality,
        "run_identity": run_identity,
        "collector_first_observed_at_utc": r["collector_first_observed_at_utc"],
        "hours_to_kickoff": hours_to_kickoff,
        "matched_target_window": matched,
        "target_window_label": target_label,
        "nearest_target_delta_minutes": nearest_delta_minutes,
        "eligible_for_target_window_coverage": eligible,
        "structured_quote_document_sha256": structured_sha,
        "source_payload_sha256": r["source_payload_sha256"],
        "structured_warnings": r.get("structured_extraction_warnings") or [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--repository")
    ap.add_argument("--branch")
    ap.add_argument("--local-artifact-zip", action="append", default=[])
    args = ap.parse_args()

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    cfg = contract["market_source"]["structured_extraction"]
    target_labels = [x["label"] for x in cfg["freeze_window_targets"]]
    observations: list[dict[str, Any]] = []

    if args.repository:
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        current_run = (os.getenv("GITHUB_RUN_ID") or "").strip()
        artifacts: list[dict[str, Any]] = []
        for attempt in range(10):
            artifacts = list_artifacts(args.repository, args.branch, token)
            if not current_run or any(str((x.get("workflow_run") or {}).get("id")) == current_run for x in artifacts):
                break
            if attempt == 9:
                fail("CURRENT_RUN_ARTIFACT_NOT_VISIBLE_AFTER_RETRY")
            time.sleep(3)
        for meta in artifacts:
            zip_raw = api_bytes(str(meta["archive_download_url"]), token)
            observations.append(evaluate_artifact(meta, zip_raw, contract))

    for i, zip_path in enumerate(args.local_artifact_zip, start=1):
        p = Path(zip_path)
        raw = p.read_bytes()
        meta = {
            "id": f"local-{i}",
            "name": p.name,
            "digest": f"sha256:{sha256(raw)}",
            "workflow_run": {"id": None, "head_sha": None, "head_branch": None},
        }
        row = evaluate_artifact(meta, raw, contract)
        row["identity_quality"] = "LOCAL_TEST_ONLY"
        row["eligible_for_target_window_coverage"] = False
        observations.append(row)

    observations.sort(key=lambda x: x["collector_first_observed_at_utc"])
    covered: dict[str, list[dict[str, Any]]] = {label: [] for label in target_labels}
    for row in observations:
        label = row.get("target_window_label")
        if row.get("eligible_for_target_window_coverage") and label in covered:
            covered[label].append(row)

    if len({str(x["artifact_id"]) for x in observations}) != len(observations):
        fail("DUPLICATE_ARTIFACT_ID")
    covered_labels = [label for label in target_labels if covered[label]]
    missing_labels = [label for label in target_labels if not covered[label]]
    complete = not missing_labels
    terminal = "PASS_R44B_ALL_TARGET_WINDOWS_PERSISTED_NOT_FORMAL_SNAPSHOT" if complete else "STOP_R44B_TARGET_WINDOW_COVERAGE_INCOMPLETE"

    report = {
        "schema_version": "V520-R44B-FREEZE-PERSISTENCE-AUDIT-1.0",
        "terminal": terminal,
        "fixture": contract["fixture"],
        "provider_group": contract["market_source"]["provider_group"],
        "target_windows": cfg["freeze_window_targets"],
        "tolerance_minutes": cfg["freeze_window_tolerance_minutes"],
        "artifact_prefix": ARTIFACT_PREFIX,
        "observation_count": len(observations),
        "covered_target_windows": covered_labels,
        "missing_target_windows": missing_labels,
        "target_window_evidence": {label: [{"artifact_id": x["artifact_id"], "workflow_run_id": x["workflow_run_id"], "collector_first_observed_at_utc": x["collector_first_observed_at_utc"], "nearest_target_delta_minutes": x["nearest_target_delta_minutes"], "checked_out_head_sha": (x.get("run_identity") or {}).get("checked_out_head_sha"), "github_run_attempt": (x.get("run_identity") or {}).get("github_run_attempt")} for x in covered[label]] for label in target_labels},
        "observations": observations,
        "freeze_window_persistence_complete": complete,
        "formal_market_snapshot": False,
        "formal_execution_ready": False,
        "source_native_quote_timestamp_proven": False,
        "direct_bookmaker_revalidation_proven": False,
        "reason_not_formal_snapshot": "R44B only proves immutable target-window persistence. Source-native quote freshness and direct bookmaker revalidation remain separate gates, so formal market admission and EV stay unavailable.",
        "target_labels_accessed": 0,
        "settlement_results_accessed": 0,
        "model_fits": 0,
        "candidate_probabilities": 0,
        "fixed100_consumed": 0,
        "fixed200_consumed": 0,
        "ev_calculations": 0,
        "formal_weight": 0,
        "formal_model_changes": 0,
        "formal_data_changes": 0,
        "formal_config_changes": 0,
        "CURRENT_changes": 0,
    }
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    encoded = packed(report) + b"\n"
    (out / "freeze_persistence_audit_r44b.json").write_bytes(encoded)
    (out / "freeze_persistence_audit_r44b.sha256").write_text(sha256(encoded) + "\n", encoding="ascii")
    print(json.dumps({"terminal": terminal, "observation_count": len(observations), "covered": covered_labels, "missing": missing_labels, "report_sha256": sha256(encoded)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R44B_PERSISTENCE_AUDIT_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
