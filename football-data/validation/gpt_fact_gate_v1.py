#!/usr/bin/env python3
"""Fail-closed fact gate for GPT-facing football project reports.

The gate separates live/recomputed facts from reported text, inference, and
unknowns. It creates deterministic JSON and Markdown from a structured evidence
bundle and refuses stale, contradictory, or identity-mismatched evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "GPT-FACT-EVIDENCE-BUNDLE-V1"
REPORT_SCHEMA_VERSION = "GPT-FACT-REPORT-V1"
VERIFICATION_SCHEMA_VERSION = "GPT-FACT-VERIFICATION-V1"
CLASSIFICATIONS = frozenset({"VERIFIED", "REPORTED_NOT_VERIFIED", "INFERRED", "UNKNOWN"})
TRUSTED_PROVENANCE = frozenset({"LIVE_API", "LOCAL_RECOMPUTATION", "SIGNED_ARTIFACT"})
ALL_PROVENANCE = TRUSTED_PROVENANCE | {"REPORTED_TEXT"}
CLAIM_TYPES = frozenset({
    "EXACT_HEAD",
    "PR_STATE",
    "WORKFLOW_EXECUTION",
    "JOB_EXECUTION",
    "ARTIFACT_IDENTITY",
    "SCIENTIFIC_GATE",
    "METRIC_VALUE",
    "HOLDOUT_ACCESS",
    "FORMAL_BOUNDARY",
    "REPORTED_TEXT",
    "INFERENCE",
    "UNKNOWN_FACT",
})
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
CERTAINTY_TERMS = (
    "已证明", "已确认", "确定有效", "问题已解决", "正式通过", "验收通过",
    "proven", "confirmed", "definitely", "solved", "acceptance pass",
)


class GateError(ValueError):
    """Raised when evidence cannot support a safe report."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require(mapping: Mapping[str, Any], field: str, context: str) -> Any:
    if field not in mapping:
        raise GateError(f"{context} missing required field: {field}")
    value = mapping[field]
    if value is None or value == "" or value == [] or value == {}:
        raise GateError(f"{context} has empty required field: {field}")
    return value


def _parse_utc(value: str, context: str) -> datetime:
    if value == "UNPROVEN_NOT_MEASURED":
        raise GateError(f"{context} is not a measured timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError(f"{context} is not ISO-8601: {value}") from exc
    if parsed.tzinfo is None:
        raise GateError(f"{context} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_observed_at(value: Any, *, now: datetime, max_age_hours: float, context: str) -> None:
    observed = _parse_utc(str(value), context)
    now_utc = now.astimezone(timezone.utc)
    age_seconds = (now_utc - observed).total_seconds()
    if age_seconds < -300:
        raise GateError(f"{context} is more than 5 minutes in the future")
    if age_seconds > max_age_hours * 3600:
        raise GateError(f"{context} is stale: age_hours={age_seconds / 3600:.3f}")


def _validate_provenance(value: Any, context: str) -> str:
    provenance = str(value)
    if provenance not in ALL_PROVENANCE:
        raise GateError(f"{context} has unsupported provenance: {provenance}")
    return provenance


def _normalize_digest(value: str) -> str:
    lowered = value.lower()
    return lowered.removeprefix("sha256:")


def _safe_number(value: Any, context: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateError(f"{context} must be numeric")
    if value != value or value in (float("inf"), float("-inf")):
        raise GateError(f"{context} must be finite")
    return value


def _evidence_index(bundle: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    repository = dict(_require(bundle, "repository", "bundle"))
    pull_request = dict(_require(bundle, "pull_request", "bundle"))
    workflow = dict(_require(bundle, "workflow", "bundle"))
    research = dict(_require(bundle, "research", "bundle"))
    formal = dict(_require(bundle, "formal_boundary", "bundle"))
    index = {
        "repository": repository,
        "pull_request": pull_request,
        "workflow": workflow,
        "research": research,
        "formal_boundary": formal,
    }
    jobs = bundle.get("jobs")
    artifacts = bundle.get("artifacts")
    if not isinstance(jobs, list):
        raise GateError("bundle.jobs must be a list")
    if not isinstance(artifacts, list):
        raise GateError("bundle.artifacts must be a list")
    for job in jobs:
        job_id = str(_require(job, "job_id", "job"))
        key = f"job:{job_id}"
        if key in index:
            raise GateError(f"duplicate evidence id: {key}")
        index[key] = dict(job)
    for artifact in artifacts:
        artifact_id = str(_require(artifact, "artifact_id", "artifact"))
        key = f"artifact:{artifact_id}"
        if key in index:
            raise GateError(f"duplicate evidence id: {key}")
        index[key] = dict(artifact)
    for metric in research.get("metrics", []):
        metric_id = str(_require(metric, "metric_id", "metric"))
        key = f"metric:{metric_id}"
        if key in index:
            raise GateError(f"duplicate evidence id: {key}")
        index[key] = dict(metric)
    return index


def validate_bundle(
    bundle: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
) -> dict[str, dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise GateError(f"schema_version must be {SCHEMA_VERSION}")
    if max_age_hours <= 0:
        raise GateError("max_age_hours must be positive")
    _validate_observed_at(
        _require(bundle, "observed_at_utc", "bundle"),
        now=now,
        max_age_hours=max_age_hours,
        context="bundle.observed_at_utc",
    )
    index = _evidence_index(bundle)
    repository = index["repository"]
    exact_head = str(_require(repository, "exact_head", "repository")).lower()
    if not SHA40_RE.fullmatch(exact_head):
        raise GateError("repository.exact_head must be a lowercase 40-character Git SHA")

    dynamic_entries = [("repository", repository), ("pull_request", index["pull_request"]), ("workflow", index["workflow"])]
    dynamic_entries.extend((key, value) for key, value in index.items() if key.startswith(("job:", "artifact:")))
    for key, value in dynamic_entries:
        provenance = _validate_provenance(_require(value, "provenance", key), key)
        if provenance == "REPORTED_TEXT":
            raise GateError(f"{key} cannot use REPORTED_TEXT provenance for identity evidence")
        _validate_observed_at(
            _require(value, "observed_at_utc", key),
            now=now,
            max_age_hours=max_age_hours,
            context=f"{key}.observed_at_utc",
        )
        raw_sha = str(_require(value, "raw_payload_sha256", key)).lower()
        if not SHA256_RE.fullmatch(raw_sha):
            raise GateError(f"{key}.raw_payload_sha256 must be SHA-256")

    pull_request = index["pull_request"]
    if str(_require(pull_request, "head_sha", "pull_request")).lower() != exact_head:
        raise GateError("pull_request.head_sha does not match repository.exact_head")
    state = str(_require(pull_request, "state", "pull_request")).lower()
    if state not in {"open", "closed"}:
        raise GateError(f"unsupported pull_request.state: {state}")
    merged = _require(pull_request, "merged", "pull_request")
    draft = _require(pull_request, "draft", "pull_request")
    if not isinstance(merged, bool) or not isinstance(draft, bool):
        raise GateError("pull_request.merged and draft must be booleans")
    if merged and state != "closed":
        raise GateError("merged pull request must be closed")

    workflow = index["workflow"]
    if str(_require(workflow, "head_sha", "workflow")).lower() != exact_head:
        raise GateError("workflow.head_sha does not match repository.exact_head")
    workflow_status = str(_require(workflow, "status", "workflow"))
    workflow_conclusion = workflow.get("conclusion")
    if workflow_status == "completed" and workflow_conclusion in (None, ""):
        raise GateError("completed workflow requires a conclusion")
    if workflow_status != "completed" and workflow_conclusion not in (None, ""):
        raise GateError("incomplete workflow cannot have a conclusion")

    for key, job in index.items():
        if not key.startswith("job:"):
            continue
        if str(_require(job, "head_sha", key)).lower() != exact_head:
            raise GateError(f"{key}.head_sha does not match repository.exact_head")
        if str(_require(job, "run_id", key)) != str(_require(workflow, "run_id", "workflow")):
            raise GateError(f"{key}.run_id does not match workflow.run_id")
        status = str(_require(job, "status", key))
        conclusion = job.get("conclusion")
        if status == "completed" and conclusion in (None, ""):
            raise GateError(f"{key} completed without conclusion")
        if status != "completed" and conclusion not in (None, ""):
            raise GateError(f"{key} incomplete with conclusion")

    artifact_ids: set[str] = set()
    for key, artifact in index.items():
        if not key.startswith("artifact:"):
            continue
        artifact_ids.add(str(artifact["artifact_id"]))
        if str(_require(artifact, "head_sha", key)).lower() != exact_head:
            raise GateError(f"{key}.head_sha does not match repository.exact_head")
        if str(_require(artifact, "run_id", key)) != str(workflow["run_id"]):
            raise GateError(f"{key}.run_id does not match workflow.run_id")
        digest = str(_require(artifact, "sha256", key)).lower()
        if not SHA256_RE.fullmatch(digest):
            raise GateError(f"{key}.sha256 must be SHA-256")

    research = index["research"]
    research_provenance = _validate_provenance(_require(research, "provenance", "research"), "research")
    evaluation_status = str(_require(research, "evaluation_status", "research"))
    if evaluation_status not in {"NOT_STARTED", "RUNNING", "COMPLETE", "FAILED", "UNPROVEN"}:
        raise GateError(f"unsupported research.evaluation_status: {evaluation_status}")
    scientific_gate = str(_require(research, "scientific_gate", "research"))
    if scientific_gate not in {"NOT_EVALUATED", "PASS", "FAIL", "UNPROVEN"}:
        raise GateError(f"unsupported research.scientific_gate: {scientific_gate}")
    metrics = research.get("metrics", [])
    if not isinstance(metrics, list):
        raise GateError("research.metrics must be a list")
    if scientific_gate in {"PASS", "FAIL"} and evaluation_status != "COMPLETE":
        raise GateError("a final scientific gate requires evaluation_status=COMPLETE")
    if scientific_gate == "PASS" and not metrics:
        raise GateError("scientific_gate=PASS requires metrics")
    if evaluation_status != "COMPLETE" and metrics:
        raise GateError("metrics cannot be final before evaluation_status=COMPLETE")
    if scientific_gate in {"PASS", "FAIL"} and research_provenance not in TRUSTED_PROVENANCE:
        raise GateError("a final scientific gate requires trusted provenance")
    source_artifact_id = research.get("source_artifact_id")
    if research_provenance == "SIGNED_ARTIFACT":
        if str(source_artifact_id) not in artifact_ids:
            raise GateError("research.source_artifact_id is not in artifacts")
        source_sha = str(_require(research, "source_artifact_sha256", "research")).lower()
        artifact_sha = str(index[f"artifact:{source_artifact_id}"]["sha256"]).lower()
        if _normalize_digest(source_sha) != _normalize_digest(artifact_sha):
            raise GateError("research source artifact SHA-256 mismatch")

    for metric in metrics:
        _safe_number(_require(metric, "value", "metric"), f"metric:{metric.get('metric_id')}.value")
        provenance = _validate_provenance(_require(metric, "provenance", "metric"), "metric")
        if provenance not in TRUSTED_PROVENANCE:
            raise GateError("final metric requires trusted provenance")
        metric_artifact = str(_require(metric, "source_artifact_id", "metric"))
        if metric_artifact not in artifact_ids:
            raise GateError(f"metric source artifact missing: {metric_artifact}")

    holdout = dict(_require(research, "holdout", "research"))
    access_count = _require(holdout, "labels_accessed_count", "research.holdout")
    access_authorized = _require(holdout, "access_authorized", "research.holdout")
    holdout_status = str(_require(holdout, "status", "research.holdout"))
    if holdout_status not in {"SEALED", "ACCESSED", "UNPROVEN"}:
        raise GateError(f"unsupported holdout.status: {holdout_status}")
    first_access = str(_require(holdout, "exact_first_access_utc", "research.holdout"))
    if holdout_status == "UNPROVEN":
        if access_count != "UNKNOWN" or access_authorized != "UNKNOWN" or first_access != "UNPROVEN_NOT_MEASURED":
            raise GateError("UNPROVEN holdout must keep count, authorization, and time explicitly unknown")
    else:
        if isinstance(access_count, bool) or not isinstance(access_count, int) or access_count < 0:
            raise GateError("holdout.labels_accessed_count must be a non-negative integer")
        if not isinstance(access_authorized, bool):
            raise GateError("holdout.access_authorized must be boolean")
        if access_count > 0 and not access_authorized:
            raise GateError("holdout labels were accessed without authorization")
        if access_count > 0 and holdout_status != "ACCESSED":
            raise GateError("positive holdout access requires status=ACCESSED")
        if access_count == 0 and holdout_status == "ACCESSED":
            raise GateError("status=ACCESSED contradicts labels_accessed_count=0")
        if access_count > 0 and first_access != "UNPROVEN_NOT_MEASURED":
            _parse_utc(first_access, "research.holdout.exact_first_access_utc")
        if access_count == 0 and first_access != "NOT_APPLICABLE":
            raise GateError("zero holdout access requires exact_first_access_utc=NOT_APPLICABLE")

    formal = index["formal_boundary"]
    formal_provenance = _validate_provenance(_require(formal, "provenance", "formal_boundary"), "formal_boundary")
    for field in ("formal_weight", "model_diff", "formal_data_diff", "config_diff", "current_diff"):
        value = _require(formal, field, "formal_boundary")
        if formal_provenance in TRUSTED_PROVENANCE:
            _safe_number(value, f"formal_boundary.{field}")
        elif value != "UNKNOWN":
            raise GateError(f"untrusted formal_boundary.{field} must be UNKNOWN")

    claims = _require(bundle, "claims", "bundle")
    if not isinstance(claims, list) or not claims:
        raise GateError("bundle.claims must be a non-empty list")
    seen_claim_ids: set[str] = set()
    for claim in claims:
        claim_id = str(_require(claim, "claim_id", "claim"))
        if claim_id in seen_claim_ids:
            raise GateError(f"duplicate claim_id: {claim_id}")
        seen_claim_ids.add(claim_id)
        claim_type = str(_require(claim, "claim_type", f"claim:{claim_id}"))
        classification = str(_require(claim, "classification", f"claim:{claim_id}"))
        if claim_type not in CLAIM_TYPES:
            raise GateError(f"claim:{claim_id} has unsupported claim_type: {claim_type}")
        if classification not in CLASSIFICATIONS:
            raise GateError(f"claim:{claim_id} has unsupported classification: {classification}")
        expected_class = {
            "REPORTED_TEXT": "REPORTED_NOT_VERIFIED",
            "INFERENCE": "INFERRED",
            "UNKNOWN_FACT": "UNKNOWN",
        }.get(claim_type, "VERIFIED")
        if classification != expected_class:
            raise GateError(f"claim:{claim_id} must use classification={expected_class}")
        refs = claim.get("evidence_refs", [])
        if classification != "UNKNOWN" and (not isinstance(refs, list) or not refs):
            raise GateError(f"claim:{claim_id} requires evidence_refs")
        for ref in refs:
            if str(ref) not in index:
                raise GateError(f"claim:{claim_id} references missing evidence: {ref}")
        if classification == "VERIFIED":
            untrusted = [ref for ref in refs if str(index[str(ref)].get("provenance")) not in TRUSTED_PROVENANCE]
            if untrusted:
                raise GateError(f"claim:{claim_id} VERIFIED uses untrusted evidence: {untrusted}")
        if claim_type == "JOB_EXECUTION" and f"job:{_require(claim, 'job_id', f'claim:{claim_id}')}" not in refs:
            raise GateError(f"claim:{claim_id} must reference its job")
        if claim_type == "ARTIFACT_IDENTITY" and f"artifact:{_require(claim, 'artifact_id', f'claim:{claim_id}')}" not in refs:
            raise GateError(f"claim:{claim_id} must reference its artifact")
        if claim_type == "METRIC_VALUE" and f"metric:{_require(claim, 'metric_id', f'claim:{claim_id}')}" not in refs:
            raise GateError(f"claim:{claim_id} must reference its metric")
        if classification in {"REPORTED_NOT_VERIFIED", "INFERRED"}:
            text = str(_require(claim, "text", f"claim:{claim_id}"))
            if classification == "INFERRED":
                _require(claim, "basis", f"claim:{claim_id}")
                lowered = text.lower()
                if any(term in lowered for term in CERTAINTY_TERMS):
                    raise GateError(f"claim:{claim_id} inference contains certainty language")
        if classification == "UNKNOWN":
            _require(claim, "question", f"claim:{claim_id}")
    return index


def _claim_statement(claim: Mapping[str, Any], index: Mapping[str, Mapping[str, Any]]) -> str:
    claim_type = str(claim["claim_type"])
    repository = index["repository"]
    pull_request = index["pull_request"]
    workflow = index["workflow"]
    research = index["research"]
    formal = index["formal_boundary"]
    if claim_type == "EXACT_HEAD":
        return f"准确HEAD为 {repository['exact_head']}。"
    if claim_type == "PR_STATE":
        return (
            f"PR #{pull_request['number']} 状态为 {pull_request['state']}，"
            f"Draft={str(pull_request['draft']).lower()}，merged={str(pull_request['merged']).lower()}。"
        )
    if claim_type == "WORKFLOW_EXECUTION":
        conclusion = workflow.get("conclusion") or "NOT_COMPLETED"
        return f"Workflow run {workflow['run_id']} 状态为 {workflow['status']}，结论为 {conclusion}。"
    if claim_type == "JOB_EXECUTION":
        job = index[f"job:{claim['job_id']}"]
        conclusion = job.get("conclusion") or "NOT_COMPLETED"
        return f"Job {job['job_id']}（{job['name']}）状态为 {job['status']}，结论为 {conclusion}。"
    if claim_type == "ARTIFACT_IDENTITY":
        artifact = index[f"artifact:{claim['artifact_id']}"]
        return (
            f"Artifact {artifact['artifact_id']}（{artifact['name']}）绑定HEAD {artifact['head_sha']}，"
            f"SHA-256为 {_normalize_digest(str(artifact['sha256']))}。"
        )
    if claim_type == "SCIENTIFIC_GATE":
        return (
            f"研究执行状态为 {research['evaluation_status']}，科学效果门为 {research['scientific_gate']}。"
            "该结论与Workflow是否成功分开记录。"
        )
    if claim_type == "METRIC_VALUE":
        metric = index[f"metric:{claim['metric_id']}"]
        return f"指标 {metric['name']}（{metric['scope']}）={metric['value']}。"
    if claim_type == "HOLDOUT_ACCESS":
        holdout = research["holdout"]
        return (
            f"盲样本状态为 {holdout['status']}，标签访问数={holdout['labels_accessed_count']}，"
            f"精确首次访问时间={holdout['exact_first_access_utc']}。"
        )
    if claim_type == "FORMAL_BOUNDARY":
        return (
            "正式边界："
            f"formal_weight={formal['formal_weight']}，model_diff={formal['model_diff']}，"
            f"formal_data_diff={formal['formal_data_diff']}，config_diff={formal['config_diff']}，"
            f"CURRENT_diff={formal['current_diff']}。"
        )
    if claim_type == "REPORTED_TEXT":
        return str(claim["text"])
    if claim_type == "INFERENCE":
        return str(claim["text"])
    if claim_type == "UNKNOWN_FACT":
        return f"尚无法确认：{claim['question']}"
    raise GateError(f"unsupported claim_type: {claim_type}")


def build_report(
    bundle: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    index = validate_bundle(bundle, now=now, max_age_hours=max_age_hours)
    normalized_claims: list[dict[str, Any]] = []
    for claim in bundle["claims"]:
        row = {
            "claim_id": claim["claim_id"],
            "claim_type": claim["claim_type"],
            "classification": claim["classification"],
            "statement": _claim_statement(claim, index),
            "evidence_refs": list(claim.get("evidence_refs", [])),
            "limitations": list(claim.get("limitations", [])),
        }
        if claim["classification"] == "INFERRED":
            row["basis"] = claim["basis"]
        normalized_claims.append(row)
    counts = Counter(row["classification"] for row in normalized_claims)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_bundle_sha256": canonical_sha256(bundle),
        "observed_at_utc": bundle["observed_at_utc"],
        "exact_head": index["repository"]["exact_head"],
        "claim_counts": {name: counts.get(name, 0) for name in sorted(CLASSIFICATIONS)},
        "claims": normalized_claims,
        "execution_success_is_not_scientific_success": True,
        "automatic_authorization_granted": False,
        "cold_conclusion": (
            "本报告只把受可信来源和准确身份绑定支持的内容列为VERIFIED。"
            "转述、推断和未知项不得升级为事实，也不得仅凭Workflow成功或Artifact存在宣称模型有效、平局问题解决、正式晋级或允许合并。"
        ),
    }


def report_markdown(report: Mapping[str, Any]) -> str:
    labels = (
        ("VERIFIED", "已核实事实"),
        ("REPORTED_NOT_VERIFIED", "仅为转述，尚未独立核实"),
        ("INFERRED", "推断"),
        ("UNKNOWN", "未知"),
    )
    lines = [
        "# GPT事实核验报告",
        "",
        f"- 准确HEAD：`{report['exact_head']}`",
        f"- 证据观察时间：`{report['observed_at_utc']}`",
        f"- 源证据SHA-256：`{report['source_bundle_sha256']}`",
        "- 自动授权：`false`",
        "",
    ]
    for classification, title in labels:
        lines.extend([f"## {title}", ""])
        rows = [row for row in report["claims"] if row["classification"] == classification]
        if not rows:
            lines.append("- 无")
        for row in rows:
            lines.append(f"- `{row['claim_id']}`：{row['statement']}")
            if row.get("basis"):
                lines.append(f"  - 推断依据：{row['basis']}")
            if row.get("limitations"):
                lines.append(f"  - 限制：{'；'.join(row['limitations'])}")
        lines.append("")
    lines.extend(["## 冷结论", "", str(report["cold_conclusion"]), ""])
    return "\n".join(lines)


def write_outputs(
    bundle: Mapping[str, Any],
    output_dir: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    report = build_report(bundle, now=now, max_age_hours=max_age_hours)
    report_json_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    report_md_bytes = report_markdown(report).encode("utf-8")
    verification = {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "PASS",
        "exact_head": report["exact_head"],
        "source_bundle_sha256": report["source_bundle_sha256"],
        "report_json_sha256": bytes_sha256(report_json_bytes),
        "report_markdown_sha256": bytes_sha256(report_md_bytes),
        "automatic_authorization_granted": False,
    }
    verification_bytes = (json.dumps(verification, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "gpt_fact_report.json").write_bytes(report_json_bytes)
    (output_dir / "gpt_fact_report.md").write_bytes(report_md_bytes)
    (output_dir / "gpt_fact_verification.json").write_bytes(verification_bytes)
    manifest = {
        "schema_version": "GPT-FACT-ARTIFACT-MANIFEST-V1",
        "exact_head": report["exact_head"],
        "files": {
            "gpt_fact_report.json": bytes_sha256(report_json_bytes),
            "gpt_fact_report.md": bytes_sha256(report_md_bytes),
            "gpt_fact_verification.json": bytes_sha256(verification_bytes),
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verification


def verify_outputs(
    bundle: Mapping[str, Any],
    output_dir: Path,
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    expected_report = build_report(bundle, now=now, max_age_hours=max_age_hours)
    expected_json = (json.dumps(expected_report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    expected_md = report_markdown(expected_report).encode("utf-8")
    report_path = output_dir / "gpt_fact_report.json"
    markdown_path = output_dir / "gpt_fact_report.md"
    verification_path = output_dir / "gpt_fact_verification.json"
    manifest_path = output_dir / "manifest.json"
    for path in (report_path, markdown_path, verification_path, manifest_path):
        if not path.is_file():
            raise GateError(f"required output missing: {path.name}")
    if report_path.read_bytes() != expected_json:
        raise GateError("gpt_fact_report.json differs from deterministic rebuild")
    if markdown_path.read_bytes() != expected_md:
        raise GateError("gpt_fact_report.md differs from deterministic rebuild")
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if verification.get("status") != "PASS":
        raise GateError("verification status is not PASS")
    if verification.get("exact_head") != expected_report["exact_head"]:
        raise GateError("verification exact_head mismatch")
    if verification.get("source_bundle_sha256") != canonical_sha256(bundle):
        raise GateError("verification source bundle SHA mismatch")
    if verification.get("report_json_sha256") != bytes_sha256(expected_json):
        raise GateError("verification report JSON SHA mismatch")
    if verification.get("report_markdown_sha256") != bytes_sha256(expected_md):
        raise GateError("verification report Markdown SHA mismatch")
    if verification.get("automatic_authorization_granted") is not False:
        raise GateError("fact verification may not grant authorization")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_files = {path.name for path in output_dir.iterdir() if path.is_file()}
    if actual_files != {"gpt_fact_report.json", "gpt_fact_report.md", "gpt_fact_verification.json", "manifest.json"}:
        raise GateError(f"artifact file set mismatch: {sorted(actual_files)}")
    if manifest.get("schema_version") != "GPT-FACT-ARTIFACT-MANIFEST-V1":
        raise GateError("manifest schema_version mismatch")
    if manifest.get("exact_head") != expected_report["exact_head"]:
        raise GateError("manifest exact_head mismatch")
    expected_manifest_files = {"gpt_fact_report.json", "gpt_fact_report.md", "gpt_fact_verification.json"}
    if set(manifest.get("files", {})) != expected_manifest_files:
        raise GateError("manifest file registry mismatch")
    for name, digest in manifest["files"].items():
        actual = bytes_sha256((output_dir / name).read_bytes())
        if digest != actual:
            raise GateError(f"manifest SHA mismatch: {name}")
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "status": "PASS",
        "exact_head": expected_report["exact_head"],
        "source_bundle_sha256": canonical_sha256(bundle),
    }


def _github_get(url: str, token: str) -> tuple[dict[str, Any], str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "football-gpt-fact-gate-v1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GateError(f"GitHub request failed: {url}: {exc}") from exc
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise GateError(f"GitHub response is not an object: {url}")
    return payload, bytes_sha256(raw)


def collect_github_bundle(repo: str, pr_number: int, run_id: int, *, token: str = "") -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise GateError("repo must be owner/name")
    base = f"https://api.github.com/repos/{repo}"
    repo_obj, repo_sha = _github_get(base, token)
    pr_obj, pr_sha = _github_get(f"{base}/pulls/{pr_number}", token)
    run_obj, run_sha = _github_get(f"{base}/actions/runs/{run_id}", token)
    jobs_obj, jobs_sha = _github_get(f"{base}/actions/runs/{run_id}/jobs?per_page=100", token)
    artifacts_obj, artifacts_sha = _github_get(f"{base}/actions/runs/{run_id}/artifacts?per_page=100", token)
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    exact_head = str(pr_obj["head"]["sha"]).lower()
    jobs = []
    for item in jobs_obj.get("jobs", []):
        jobs.append({
            "job_id": item["id"],
            "name": item["name"],
            "run_id": run_id,
            "head_sha": exact_head,
            "status": item["status"],
            "conclusion": item.get("conclusion"),
            "observed_at_utc": observed,
            "provenance": "LIVE_API",
            "raw_payload_sha256": canonical_sha256(item),
        })
    artifacts = []
    for item in artifacts_obj.get("artifacts", []):
        digest = item.get("digest")
        if not digest:
            continue
        artifacts.append({
            "artifact_id": item["id"],
            "name": item["name"],
            "run_id": run_id,
            "head_sha": exact_head,
            "sha256": digest,
            "observed_at_utc": observed,
            "provenance": "LIVE_API",
            "raw_payload_sha256": canonical_sha256(item),
        })
    claims: list[dict[str, Any]] = [
        {"claim_id": "exact-head", "claim_type": "EXACT_HEAD", "classification": "VERIFIED", "evidence_refs": ["repository", "pull_request"]},
        {"claim_id": "pr-state", "claim_type": "PR_STATE", "classification": "VERIFIED", "evidence_refs": ["pull_request"]},
        {"claim_id": "workflow-state", "claim_type": "WORKFLOW_EXECUTION", "classification": "VERIFIED", "evidence_refs": ["workflow"]},
    ]
    for job in jobs:
        claims.append({"claim_id": f"job-{job['job_id']}", "claim_type": "JOB_EXECUTION", "classification": "VERIFIED", "job_id": job["job_id"], "evidence_refs": [f"job:{job['job_id']}"]})
    for artifact in artifacts:
        claims.append({"claim_id": f"artifact-{artifact['artifact_id']}", "claim_type": "ARTIFACT_IDENTITY", "classification": "VERIFIED", "artifact_id": artifact["artifact_id"], "evidence_refs": [f"artifact:{artifact['artifact_id']}"]})
    claims.extend([
        {"claim_id": "scientific-result", "claim_type": "UNKNOWN_FACT", "classification": "UNKNOWN", "question": "该run是否通过预注册科学效果门"},
        {"claim_id": "holdout-access", "claim_type": "UNKNOWN_FACT", "classification": "UNKNOWN", "question": "盲样本标签是否被访问"},
    ])
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": observed,
        "repository": {
            "name": repo_obj["full_name"],
            "default_branch": repo_obj["default_branch"],
            "exact_head": exact_head,
            "observed_at_utc": observed,
            "provenance": "LIVE_API",
            "raw_payload_sha256": repo_sha,
        },
        "pull_request": {
            "number": pr_number,
            "state": pr_obj["state"],
            "draft": bool(pr_obj.get("draft")),
            "merged": bool(pr_obj.get("merged")),
            "head_sha": exact_head,
            "observed_at_utc": observed,
            "provenance": "LIVE_API",
            "raw_payload_sha256": pr_sha,
        },
        "workflow": {
            "run_id": run_id,
            "event": run_obj["event"],
            "status": run_obj["status"],
            "conclusion": run_obj.get("conclusion"),
            "head_sha": str(run_obj["head_sha"]).lower(),
            "observed_at_utc": observed,
            "provenance": "LIVE_API",
            "raw_payload_sha256": run_sha,
        },
        "jobs": jobs,
        "artifacts": artifacts,
        "research": {
            "evaluation_status": "UNPROVEN",
            "scientific_gate": "UNPROVEN",
            "metrics": [],
            "holdout": {"status": "UNPROVEN", "labels_accessed_count": "UNKNOWN", "access_authorized": "UNKNOWN", "exact_first_access_utc": "UNPROVEN_NOT_MEASURED"},
            "provenance": "REPORTED_TEXT",
        },
        "formal_boundary": {
            "formal_weight": "UNKNOWN",
            "model_diff": "UNKNOWN",
            "formal_data_diff": "UNKNOWN",
            "config_diff": "UNKNOWN",
            "current_diff": "UNKNOWN",
            "provenance": "REPORTED_TEXT",
        },
        "claims": claims,
        "collection_receipts": {
            "jobs_response_sha256": jobs_sha,
            "artifacts_response_sha256": artifacts_sha,
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError(f"JSON root must be an object: {path}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--max-age-hours", type=float, default=24.0)
    verify = sub.add_parser("verify")
    verify.add_argument("--input", required=True, type=Path)
    verify.add_argument("--output-dir", required=True, type=Path)
    verify.add_argument("--max-age-hours", type=float, default=24.0)
    collect = sub.add_parser("collect-github")
    collect.add_argument("--repo", required=True)
    collect.add_argument("--pr", required=True, type=int)
    collect.add_argument("--run-id", required=True, type=int)
    collect.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "build":
            result = write_outputs(_load_json(args.input), args.output_dir, max_age_hours=args.max_age_hours)
        elif args.command == "verify":
            result = verify_outputs(_load_json(args.input), args.output_dir, max_age_hours=args.max_age_hours)
        else:
            bundle = collect_github_bundle(args.repo, args.pr, args.run_id, token=os.environ.get("GH_TOKEN", ""))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = {"status": "COLLECTED", "exact_head": bundle["repository"]["exact_head"], "output": str(args.output)}
    except (GateError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
