#!/usr/bin/env python3
"""E3f-1B: validate and publish the external PIT source admission matrix.

No external records are downloaded or joined. No model is fitted.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_MATRIX = HERE / "e3f1b_external_pit_source_matrix.json"
OUT = ROOT / "artifacts/research/e3f1b_external_pit_source_audit"

REQUIRED_SOURCE_FIELDS = (
    "source_id", "provider", "source_class", "feature_families", "access_mode",
    "license_or_terms", "technical_structure", "historical_big5_coverage",
    "original_pre_match_timestamp", "immutable_snapshot_possible", "pit_notes",
    "status", "decision", "evidence",
)


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def validate(matrix: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field, expected in (
        ("fixed_target_matches", 6251),
        ("fixed_b100", 100),
        ("external_records_ingested", 0),
        ("api_calls_with_credentials", 0),
        ("website_scrapes", 0),
        ("candidate_model_fits", 0),
        ("threshold_tuning_count", 0),
        ("formal_weight", 0),
    ):
        if matrix.get(field) != expected:
            errors.append(f"{field}={matrix.get(field)!r}, expected {expected!r}")
    sources = matrix.get("sources")
    if not isinstance(sources, list) or len(sources) < 8:
        errors.append("at least eight source classes are required")
        sources = []
    ids = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"source[{index}] is not an object")
            continue
        missing = [field for field in REQUIRED_SOURCE_FIELDS if field not in source]
        if missing:
            errors.append(f"source[{index}] missing {missing}")
        ids.append(source.get("source_id"))
        if not isinstance(source.get("feature_families"), list):
            errors.append(f"source[{index}] feature_families must be a list")
        if not isinstance(source.get("evidence"), list):
            errors.append(f"source[{index}] evidence must be a list")
    if len(ids) != len(set(ids)):
        errors.append("duplicate source_id")
    verdict = matrix.get("overall_verdict", {})
    for field in (
        "full_6251_external_source_ready",
        "source_ready_for_immediate_training_join",
        "training_authorized",
        "promotion_candidate",
    ):
        if verdict.get(field) is not False:
            errors.append(f"overall_verdict.{field} must be false")
    if verdict.get("formal_assets_changed") != 0:
        errors.append("formal_assets_changed must be zero")
    return errors


def markdown(report: dict[str, Any]) -> str:
    matrix = report["matrix"]
    lines = [
        "# E3f-1B External PIT Source and Timestamp Audit",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Status: `{report['research_status']}`",
        f"- Sources reviewed: {len(matrix['sources'])}",
        "- External records ingested: 0",
        "- Credentialed API calls: 0",
        "- Website scrapes: 0",
        "- Candidate model fits: 0",
        "- Threshold tuning: 0",
        "- Formal weight: 0",
        "",
        "## Source matrix",
        "",
        "| Source | Class | Status | Decision |",
        "|---|---|---|---|",
    ]
    for source in matrix["sources"]:
        lines.append(
            f"| {source['source_id']} | {source['source_class']} | "
            f"{source['status']} | {source['decision']} |"
        )
    verdict = matrix["overall_verdict"]
    lines += [
        "",
        "## Overall verdict",
        "",
        f"- Full 6,251 source ready: {verdict['full_6251_external_source_ready']}",
        f"- Immediate training join ready: {verdict['source_ready_for_immediate_training_join']}",
        f"- Training authorized: {verdict['training_authorized']}",
        f"- Promotion candidate: {verdict['promotion_candidate']}",
        f"- Formal assets changed: {verdict['formal_assets_changed']}",
        "",
        verdict["recommended_next_action"],
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    matrix_path = Path(args.matrix)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    errors = validate(matrix)
    report = {
        "schema_version": "1.0",
        "research_id": "E3f-1B",
        "research_status": "PASS" if not errors else "FAIL",
        "repository_head": repository_head(),
        "validation_errors": errors,
        "matrix_sha256": __import__("hashlib").sha256(matrix_path.read_bytes()).hexdigest(),
        "matrix": matrix,
        "audit": {
            "external_network_calls_during_workflow": 0,
            "external_records_ingested": 0,
            "candidate_model_fits": 0,
            "threshold_tuning_count": 0,
            "formal_assets_changed": 0,
        },
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "e3f1b_external_pit_source_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "e3f1b_external_pit_source_audit.md").write_text(
        markdown(report), encoding="utf-8"
    )
    if args.print_summary:
        print(json.dumps({
            "status": report["research_status"],
            "head": report["repository_head"],
            "sources": len(matrix.get("sources", [])),
            "full_6251_ready": matrix.get("overall_verdict", {}).get("full_6251_external_source_ready"),
            "training_authorized": matrix.get("overall_verdict", {}).get("training_authorized"),
        }, sort_keys=True))
    return 0 if report["research_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
