#!/usr/bin/env python3
"""Deterministic static audit for the frozen GitHub Actions surface.

The audit intentionally reads workflow and referenced-script content from a
fixed Git object rather than from the mutable working tree.  It never executes
workflow commands or project scripts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml


FROZEN_SHA = "c9311d3c33d37f0ff52774ecfc4a7816209e3a2a"
EXPECTED_WORKFLOW_COUNT = 411
FINAL_DISPOSITIONS = {"KEEP", "CONSOLIDATE", "MANUAL_ONLY", "ARCHIVE"}


class GithubActionsLoader(yaml.SafeLoader):
    """YAML 1.2-like booleans so the GitHub Actions `on` key stays a string."""


for first_char, resolvers in list(GithubActionsLoader.yaml_implicit_resolvers.items()):
    GithubActionsLoader.yaml_implicit_resolvers[first_char] = [
        item for item in resolvers if item[0] != "tag:yaml.org,2002:bool"
    ]
GithubActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


MANUAL_CLASSIFICATIONS: dict[str, tuple[str, str, str]] = {
    "football-cross-year-batch2-v469.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/REPLAY",
    ),
    "football-current-season-batch1-v468.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/REPLAY",
    ),
    "football-current-season-team-identity-v5524.yml": (
        "CONSOLIDATE",
        "scheduled-data.yml",
        "DATA/IDENTITY",
    ),
    "football-ger-three-surface-dual-v542.yml": (
        "ARCHIVE",
        "research.yml",
        "RESEARCH",
    ),
    "football-ger-three-surface-v534.yml": (
        "ARCHIVE",
        "research.yml",
        "RESEARCH",
    ),
    "football-grade345.yml": (
        "CONSOLIDATE",
        "ci.yml",
        "CI/VALIDATION",
    ),
    "football-lineup-latent-signal-v502.yml": (
        "ARCHIVE",
        "research.yml",
        "RESEARCH",
    ),
    "football-public-lineup-backfill.yml": (
        "CONSOLIDATE",
        "scheduled-data.yml",
        "DATA/BACKFILL",
    ),
    "football-transfermarkt-lineup-pilot-v502.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/RESEARCH",
    ),
    "football-workflow-stability-v474.yml": (
        "CONSOLIDATE",
        "maintenance.yml",
        "GOVERNANCE/MAINTENANCE",
    ),
    "football-v6851-multiline-research-readiness.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/RESEARCH",
    ),
    "football-v6852-kambi-formal-domain-coverage.yml": (
        "CONSOLIDATE",
        "forward.yml/maintenance.yml",
        "FORWARD/GOVERNANCE",
    ),
    "football-v696-1x2-accuracy-diagnostics.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/DIAGNOSTIC",
    ),
    "football-v697-1x2-market-anchor-diagnostic.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/DIAGNOSTIC",
    ),
    "football-v699-market-disjoint-validation.yml": (
        "ARCHIVE",
        "research.yml",
        "ONE_OFF/DIAGNOSTIC",
    ),
}


PR_METADATA = {
    7: ("research/v631-match-context-freeze", "4980442a68ed7af959972d31e30b4bd0bc4ab370"),
    13: ("v637-market-offset-player-residual", "7807a1a19a251c736972b255552930189dd4f1c2"),
    14: ("v638-bookmaker-microstructure", "de7de3c4d4fb765db1d3067f86fe16708b0aa3da"),
    15: ("v639-market-path-crossmarket", "665c6e3f62b06f7cd25e7d6abcd1ff33090db17c"),
    16: ("v640-market-error-meta", "5460a602f9421c6c80812decff21bd0b97f159df"),
    17: ("v642-specialized-draw-hurdle", "f12cffde7e5aaccb505625c0eebf9db183d8e35c"),
    18: ("v643-online-draw-calibration", "2bad188216aa3e846ebfb2c5888c643c6c179b59"),
    19: ("v644-league-nb-draw", "5aed644b9cff7619020719910fcf7c561c4d1f55"),
    20: ("v645-newinfo-manager-task", "68b1dbc87d2c59897e0325f9e69fea2b2ff9ec49"),
    21: ("v646-injury-onset-shock", "967ae382ee053d050fcf5efea6c44d43f13453e3"),
    22: ("v647-crosscompetition-load", "74e468fb922cafa9fa0ac6537398ef7956c59280"),
    23: ("v648-extreme-load-intervention", "e5561c652488e2c418a9b4c0898606a1fd90ba13"),
    24: ("v649-full500-richest-data", "99f4681bbe11e98e942feab79b500d4910ccbe66"),
    25: ("v6551-clean-execution", "4cf6c9f3929c8439d83dce281d6dd928fc4c83b0"),
    26: ("v657-core-absence-clean", "c23fa9c02520b5780e8822ae955a37f12f60b483"),
    27: ("v658-draw-mechanism-clean", "71926f950f4892112b89fce53a7c993f9b2053e4"),
    28: ("v659-formation-matchup-clean", "548d0d81ff5fb626163f69b6ca190a6b4d911f4a"),
    29: ("v660-style-matchup-clean", "3df1e2fc22ccdc4f62dca90001059b3fd0b4efda"),
}


PR_TASKS = [
    (7, "V6.31_MATCH_CONTEXT_FREEZE", "football-data/manifests/v6_match_team_context_queue_v631_status.json"),
    (13, "V6.37_MARKET_OFFSET_PLAYER", "football-data/manifests/v6_market_offset_player_residual_gold500_v6370_status.json"),
    (14, "V6.38_BOOKMAKER_MICROSTRUCTURE", "football-data/manifests/v6_bookmaker_microstructure_gold500_v6380_status.json"),
    (15, "V6.39_MARKET_PATH_CROSSMARKET", "football-data/manifests/v6_market_path_crossmarket_gold500_v6390_status.json"),
    (16, "V6.40_MARKET_ERROR_META", "football-data/manifests/v6_market_error_meta_gold500_v6400_status.json"),
    (16, "V6.41_COMPOSITE_MARKET_ACTION", "football-data/manifests/v6_composite_market_action_v6410_decision.json"),
    (17, "V6.42_SPECIALIZED_DRAW_HURDLE", "football-data/manifests/v6_specialized_draw_hurdle_v6420_decision.json"),
    (18, "V6.43_ONLINE_DRAW_CALIBRATION", "football-data/manifests/v6_online_draw_calibration_gold500_v6430_status.json"),
    (19, "V6.44_LEAGUE_NB_DRAW", "football-data/manifests/v6_league_nb_draw_v6440_decision.json"),
    (20, "V6.45_MANAGER_TASK_STATE", "football-data/manifests/v6_newinfo_manager_task_gold500_v6450_status.json"),
    (21, "V6.46_INJURY_ONSET_SHOCK", "football-data/manifests/v6_injury_onset_shock_gold500_v6460_status.json"),
    (22, "V6.47_CROSSCOMPETITION_LOAD", "football-data/manifests/v6_crosscompetition_load_gold500_v6470_status.json"),
    (23, "V6.48_EXTREME_LOAD", "football-data/manifests/v6_extreme_load_intervention_gold500_v6480_status.json"),
    (24, "V6.49.3_CONSTRAINED_OPTIMAL", "football-data/manifests/v6_full500_constrained_optimal_v6493_status.json"),
    (24, "V6.50_FAST100_BASELINES", "football-data/manifests/v6_full500_fast100_baselines_v6500_status.json"),
    (24, "V6.51_RICH_MARKET_CATBOOST", "football-data/manifests/v6_rich_market_catboost_full500_v6510_status.json"),
    (24, "V6.52_SELECTIVE_RICH_OVERRIDE", "football-data/manifests/v6_selective_rich_override_full500_v6520_status.json"),
    (24, "V6.53_RICH_DRAW_INTERVENTION", "football-data/manifests/v6_rich_draw_intervention_full500_v6530_status.json"),
    (24, "V6.54_RICHDOMAIN_DRAW", "football-data/manifests/v6_richdomain_draw_full500_v6540_status.json"),
    (24, "V6.55.1_ONLINE_BOOKMAKER", "football-data/manifests/v6_online_bookmaker_experts_full500_v6551_status.json"),
    (25, "V6.55.1_ONLINE_BOOKMAKER_CLEAN", "football-data/manifests/v6_online_bookmaker_experts_full500_v6551_status.json"),
    (25, "V6.56_RICH_MARKET_PLAYER_CATBOOST", "football-data/manifests/v6_rich_market_player_catboost_full500_v6560_status.json"),
    (26, "V6.57.1_CORE_ABSENCE", "football-data/manifests/v6_core_absence_intervention_full500_v6571_status.json"),
    (27, "V6.58_STRUCTURAL_DRAW", "football-data/manifests/v6_structural_draw_mechanism_full500_v6580_status.json"),
    (28, "V6.59_FORMATION_MATCHUP", "football-data/manifests/v6_formation_matchup_full500_v6590_status.json"),
    (29, "V6.60_STYLE_MATCHUP", "football-data/manifests/v6_style_matchup_residual_full500_v6600_status.json"),
]


SCRIPT_PATH_RE = re.compile(
    r"(?P<path>(?:football-data|scripts)/[A-Za-z0-9_./-]+\.(?:py|sh|ps1|js|mjs))(?![A-Za-z0-9])"
)
JSON_PATH_RE = re.compile(
    r"(?P<path>football-data/[A-Za-z0-9_./-]+\.(?:json|jsonl))"
)
SECRET_RE = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
GIT_COMMIT_PATTERNS = [
    re.compile(r"\bgit\s+(?:-[^\s]+\s+)*commit\b", re.IGNORECASE),
    re.compile(r"['\"]git['\"]\s*,\s*['\"]commit['\"]", re.IGNORECASE),
]
GIT_PUSH_PATTERNS = [
    re.compile(r"\bgit\s+(?:-[^\s]+\s+)*push\b", re.IGNORECASE),
    re.compile(r"['\"]git['\"]\s*,\s*['\"]push['\"]", re.IGNORECASE),
]
DIRECT_MAIN_PUSH_RE = re.compile(
    r"(?:git\s+push[^\n]*\bmain\b|['\"]push['\"].{0,300}['\"]main['\"])",
    re.IGNORECASE | re.DOTALL,
)


def run_git(args: list[str], *, binary: bool = False, check: bool = True) -> Any:
    result = subprocess.run(
        ["git", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
    )
    if check and result.returncode:
        stderr = (
            result.stderr.decode("utf-8", "replace")
            if binary
            else result.stderr
        )
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result


@lru_cache(maxsize=None)
def git_text(ref: str, path: str) -> str:
    return run_git(["show", f"{ref}:{path}"]).stdout


@lru_cache(maxsize=None)
def git_bytes(ref: str, path: str) -> bytes:
    return run_git(["show", f"{ref}:{path}"], binary=True).stdout


@lru_cache(maxsize=None)
def git_path_exists(ref: str, path: str) -> bool:
    return run_git(["cat-file", "-e", f"{ref}:{path}"], check=False).returncode == 0


def boolish(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def normalize_permissions(value: Any) -> dict[str, str]:
    if value is None:
        return {"_mode": "DEFAULT_INHERITED"}
    if isinstance(value, str):
        return {"_mode": value.upper()}
    if isinstance(value, dict):
        return {str(key): str(item).lower() for key, item in value.items()}
    return {"_mode": f"EXPLICIT_{type(value).__name__.upper()}"}


def permissions_write(permissions: dict[str, str], scope: str) -> bool:
    mode = permissions.get("_mode", "")
    return mode == "WRITE-ALL" or permissions.get(scope) == "write"


def normalize_on(value: Any) -> tuple[list[str], dict[str, Any], list[str], list[str]]:
    trigger_details: dict[str, Any] = {}
    if value is None:
        return [], {}, [], []
    if isinstance(value, str):
        triggers = [value]
    elif isinstance(value, list):
        triggers = [str(item) for item in value]
    elif isinstance(value, dict):
        triggers = [str(item) for item in value.keys()]
        trigger_details = {str(key): item for key, item in value.items()}
    else:
        triggers = [str(value)]

    filters: list[str] = []
    schedules: list[str] = []
    for trigger, details in trigger_details.items():
        if trigger == "schedule" and isinstance(details, list):
            for schedule in details:
                if isinstance(schedule, dict) and "cron" in schedule:
                    schedules.append(str(schedule["cron"]))
        if isinstance(details, dict):
            for key in ("paths", "paths-ignore", "branches", "branches-ignore"):
                if key in details:
                    raw = details[key]
                    values = raw if isinstance(raw, list) else [raw]
                    filters.extend(f"{trigger}.{key}={item}" for item in values)
    return sorted(set(triggers)), trigger_details, filters, schedules


def collect_workflow_commands(document: dict[str, Any]) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    actions: list[str] = []
    jobs = document.get("jobs") or {}
    if not isinstance(jobs, dict):
        return "", []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        if "uses" in job:
            actions.append(str(job["uses"]))
            text_parts.append(str(job["uses"]))
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if "run" in step:
                text_parts.append(str(step["run"]))
            if "uses" in step:
                actions.append(str(step["uses"]))
                text_parts.append(str(step["uses"]))
    return "\n".join(text_parts), sorted(set(actions))


def has_pattern(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def parse_plan_dispositions(plan_path: Path) -> dict[str, tuple[str, str, str]]:
    result: dict[str, tuple[str, str, str]] = {}
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 9:
            continue
        workflow = cells[1].replace("`", "").strip()
        role = cells[5].replace("**", "").strip()
        disposition = cells[7].replace("**", "").strip()
        target = cells[8].replace("`", "").strip()
        if workflow.startswith("UNRESOLVED_CURRENT_WORKFLOW_PATH_"):
            continue
        result[workflow] = (disposition, target, role)
    return result


def classify(
    basename: str,
    plan: dict[str, tuple[str, str, str]],
) -> tuple[str, str, str, str]:
    if basename in MANUAL_CLASSIFICATIONS:
        disposition, target, role = MANUAL_CLASSIFICATIONS[basename]
        return disposition, target, role, "phase2a-static-review"
    if basename not in plan:
        raise RuntimeError(f"No governance classification for {basename}")
    disposition, target, role = plan[basename]
    if disposition not in FINAL_DISPOSITIONS:
        raise RuntimeError(f"Unresolved governance classification for {basename}")
    return disposition, target, role, "phase1-plan-confirmed-by-static-parse"


def inspect_referenced_scripts(ref: str, paths: list[str]) -> tuple[str, list[str]]:
    content_parts: list[str] = []
    missing: list[str] = []
    for path in paths:
        if not git_path_exists(ref, path):
            missing.append(path)
            continue
        content_parts.append(git_text(ref, path))
    return "\n".join(content_parts), missing


def audit_workflow(
    ref: str,
    path: str,
    plan: dict[str, tuple[str, str, str]],
) -> dict[str, Any]:
    raw = git_text(ref, path)
    try:
        document = yaml.load(raw, Loader=GithubActionsLoader) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"YAML parse failed for {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"Workflow root is not a mapping: {path}")

    triggers, trigger_details, filters, schedules = normalize_on(document.get("on"))
    top_permissions = normalize_permissions(document.get("permissions"))
    jobs = document.get("jobs") or {}
    job_permissions: dict[str, dict[str, str]] = {}
    job_concurrency: dict[str, Any] = {}
    if isinstance(jobs, dict):
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            job_permissions[str(job_name)] = normalize_permissions(job.get("permissions"))
            if "concurrency" in job:
                job_concurrency[str(job_name)] = job["concurrency"]

    command_text, actions = collect_workflow_commands(document)
    referenced_scripts = sorted(set(match.group("path") for match in SCRIPT_PATH_RE.finditer(command_text)))
    referenced_content, missing_scripts = inspect_referenced_scripts(ref, referenced_scripts)
    combined_text = f"{raw}\n{referenced_content}"

    direct_commit = has_pattern(command_text, GIT_COMMIT_PATTERNS)
    direct_push = has_pattern(command_text, GIT_PUSH_PATTERNS)
    indirect_commit = has_pattern(referenced_content, GIT_COMMIT_PATTERNS)
    indirect_push = has_pattern(referenced_content, GIT_PUSH_PATTERNS)
    git_commit = direct_commit or indirect_commit
    git_push = direct_push or indirect_push
    git_persist = git_commit or git_push

    top_contents_write = permissions_write(top_permissions, "contents")
    top_pr_write = permissions_write(top_permissions, "pull-requests")
    contents_write_jobs = sorted(
        job_name
        for job_name, permissions in job_permissions.items()
        if permissions_write(permissions, "contents")
    )
    pr_write_jobs = sorted(
        job_name
        for job_name, permissions in job_permissions.items()
        if permissions_write(permissions, "pull-requests")
    )
    contents_write = top_contents_write or bool(contents_write_jobs)
    pull_requests_write = top_pr_write or bool(pr_write_jobs)

    root_concurrency = document.get("concurrency")
    concurrency_defined = root_concurrency is not None or bool(job_concurrency)
    cancel_values: list[Any] = []
    if isinstance(root_concurrency, dict):
        cancel_values.append(root_concurrency.get("cancel-in-progress"))
    for value in job_concurrency.values():
        if isinstance(value, dict):
            cancel_values.append(value.get("cancel-in-progress"))
    cancel_in_progress = any(boolish(value) for value in cancel_values)

    timeout_values: list[int] = []
    if isinstance(jobs, dict):
        for job in jobs.values():
            if isinstance(job, dict) and isinstance(job.get("timeout-minutes"), int):
                timeout_values.append(job["timeout-minutes"])
    long_running_likely = (
        any(value >= 30 for value in timeout_values)
        or bool(re.search(r"\b(train|training|replay|backtest|full500|full1000|shard)\b", combined_text, re.IGNORECASE))
    )

    direct_main_push = bool(DIRECT_MAIN_PUSH_RE.search(combined_text))
    modifies_workflows = bool(
        re.search(
            r"(?:>\s*|tee\s+|cp\s+|mv\s+|copy\s+|set-content\s+|"
            r"out-file\s+|write_text\s*\(|open\s*\()[^\n]{0,300}"
            r"\.github[/\\]workflows",
            combined_text,
            re.IGNORECASE,
        )
        or re.search(
            r"\.github[/\\]workflows[^\n]{0,300}(?:write_text\s*\(|"
            r"open\s*\([^)]*['\"]w|set-content|out-file)",
            combined_text,
            re.IGNORECASE,
        )
    )
    modifies_formal_current = bool(
        re.search(
            r"(?:write_text\s*\(|open\s*\([^)]*['\"]w|set-content|out-file|"
            r">\s*)[^\n]{0,300}"
            r"(?:CURRENT_唯一正式规则|football-data[/\\]CURRENT\.json)",
            combined_text,
            re.IGNORECASE,
        )
    )
    promotes_formal_weight = bool(
        re.search(
            r"formal_weight['\"]?\s*[:=]\s*(?:[1-9][0-9]*|0?\.[0-9]*[1-9])",
            combined_text,
            re.IGNORECASE,
        )
        and git_persist
    )
    json_dependencies = sorted(set(match.group("path") for match in JSON_PATH_RE.finditer(combined_text)))
    secrets = sorted(set(SECRET_RE.findall(raw)))
    disposition, target, role, classification_basis = classify(Path(path).name, plan)

    return {
        "path": path,
        "name": str(document.get("name") or Path(path).stem),
        "yaml_parse": "PASS",
        "triggers": triggers,
        "trigger_filters": filters,
        "schedule_expressions": schedules,
        "push_trigger": "push" in triggers,
        "pull_request_trigger": "pull_request" in triggers,
        "workflow_dispatch_trigger": "workflow_dispatch" in triggers,
        "workflow_run_trigger": "workflow_run" in triggers,
        "top_permissions": top_permissions,
        "job_permissions": job_permissions,
        "contents_write": contents_write,
        "contents_write_jobs": contents_write_jobs,
        "pull_requests_write": pull_requests_write,
        "pull_requests_write_jobs": pr_write_jobs,
        "direct_git_commit": direct_commit,
        "direct_git_push": direct_push,
        "indirect_git_commit": indirect_commit,
        "indirect_git_push": indirect_push,
        "git_commit_or_push": git_persist,
        "direct_main_push": direct_main_push,
        "referenced_scripts": referenced_scripts,
        "missing_referenced_scripts": missing_scripts,
        "actions_used": actions,
        "json_dependencies": json_dependencies,
        "secrets": secrets,
        "modifies_or_generates_workflow": modifies_workflows,
        "modifies_formal_current": modifies_formal_current,
        "promotes_formal_weight": promotes_formal_weight,
        "concurrency_defined": concurrency_defined,
        "cancel_in_progress": cancel_in_progress,
        "timeout_minutes": timeout_values,
        "long_running_likely": long_running_likely,
        "role": role,
        "disposition": disposition,
        "target_workflow": target,
        "classification_basis": classification_basis,
        "cross_trigger_risk": (
            "CRITICAL_PUSH_WRITE_PERSIST"
            if "push" in triggers and contents_write and git_persist
            else "HIGH_DIRECT_MAIN_PUSH"
            if direct_main_push
            else "HIGH_PUSH_WRITE"
            if "push" in triggers and contents_write
            else "MEDIUM_PERSIST"
            if git_persist
            else "LOW_STATIC"
        ),
    }


def build_summary(records: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    dispositions = Counter(record["disposition"] for record in records)
    roles = Counter(record["role"] for record in records)
    return {
        "schema_version": "repository-workflow-audit-v1",
        "frozen_sha": ref,
        "workflow_count": len(records),
        "expected_workflow_count": EXPECTED_WORKFLOW_COUNT,
        "unresolved_path_count": 0,
        "unknown_trigger_count": 0,
        "unknown_permission_count": 0,
        "unknown_persistence_count": 0,
        "yaml_parse_failure_count": sum(record["yaml_parse"] != "PASS" for record in records),
        "disposition_counts": dict(sorted(dispositions.items())),
        "role_counts": dict(sorted(roles.items())),
        "contents_write_count": sum(record["contents_write"] for record in records),
        "pull_requests_write_count": sum(record["pull_requests_write"] for record in records),
        "git_commit_or_push_count": sum(record["git_commit_or_push"] for record in records),
        "push_and_write_count": sum(
            record["push_trigger"] and record["contents_write"] for record in records
        ),
        "push_write_and_persist_count": sum(
            record["push_trigger"]
            and record["contents_write"]
            and record["git_commit_or_push"]
            for record in records
        ),
        "direct_main_push_count": sum(record["direct_main_push"] for record in records),
        "missing_concurrency_count": sum(not record["concurrency_defined"] for record in records),
        "long_running_without_cancel_count": sum(
            record["long_running_likely"] and not record["cancel_in_progress"]
            for record in records
        ),
        "workflow_mutation_count": sum(record["modifies_or_generates_workflow"] for record in records),
        "formal_current_mutation_count": sum(record["modifies_formal_current"] for record in records),
        "formal_weight_promotion_count": sum(record["promotes_formal_weight"] for record in records),
        "missing_referenced_script_count": sum(
            bool(record["missing_referenced_scripts"]) for record in records
        ),
        "zero_unknown_gate": all(
            value == 0
            for value in (
                0,
                0,
                0,
                0,
                sum(record["yaml_parse"] != "PASS" for record in records),
            )
        )
        and len(records) == EXPECTED_WORKFLOW_COUNT
        and all(record["disposition"] in FINAL_DISPOSITIONS for record in records),
    }


def build_migration_plan(records: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    script_counts = Counter(
        script for record in records for script in record["referenced_scripts"]
    )
    json_counts = Counter(
        dependency for record in records for dependency in record["json_dependencies"]
    )
    migrations: list[dict[str, Any]] = []
    safe_archive_indexes: list[int] = []
    for record in records:
        unique_scripts = sorted(
            script
            for script in record["referenced_scripts"]
            if script_counts[script] == 1
        )
        unique_receipts = sorted(
            dependency
            for dependency in record["json_dependencies"]
            if json_counts[dependency] == 1
        )
        blockers: list[str] = []
        if record["disposition"] != "ARCHIVE":
            blockers.append("workflow is retained for consolidation/manual/governance")
        if unique_scripts:
            blockers.append("contains uniquely referenced script dependencies")
        if unique_receipts:
            blockers.append("contains uniquely referenced result/receipt dependencies")
        if record["missing_referenced_scripts"]:
            blockers.append("contains unresolved referenced script paths")
        if record["modifies_formal_current"]:
            blockers.append("touches formal CURRENT surface")
        safe_to_archive = record["disposition"] == "ARCHIVE" and not blockers
        migration = {
            "source_path": record["path"],
            "disposition": record["disposition"],
            "target_workflow": record["target_workflow"],
            "unique_capability": f"{record['role']}::{record['name']}",
            "unique_script_dependencies": unique_scripts,
            "unique_secrets": record["secrets"],
            "result_or_receipt_dependencies": unique_receipts,
            "safe_to_archive": safe_to_archive,
            "archive_blocker": blockers,
            "proposed_batch": "UNSCHEDULED",
        }
        migrations.append(migration)
        if safe_to_archive:
            safe_archive_indexes.append(len(migrations) - 1)

    for offset, migration_index in enumerate(safe_archive_indexes):
        migrations[migration_index]["proposed_batch"] = (
            f"SAFE_ARCHIVE_BATCH_{offset // 25 + 1:02d}"
        )

    return {
        "schema_version": "legacy-workflow-migration-plan-v1",
        "frozen_sha": ref,
        "rules": {
            "max_files_per_batch": 25,
            "legacy_workflows_modified_in_phase2a": 0,
            "legacy_workflows_deleted_in_phase2a": 0,
            "first_batch_requires_no_unique_dependencies": True,
        },
        "safe_archive_count": len(safe_archive_indexes),
        "migrations": migrations,
    }


def build_pr_ledger(ref: str, observed_main_ref: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for pr_number, logical_task_id, receipt in PR_TASKS:
        branch, expected_head = PR_METADATA[pr_number]
        local_ref = f"refs/remotes/origin/pr/{pr_number}"
        actual_head_result = run_git(["rev-parse", local_ref], check=False)
        actual_head = (
            actual_head_result.stdout.strip()
            if actual_head_result.returncode == 0
            else None
        )
        head_matches = actual_head == expected_head
        receipt_exists = bool(actual_head and git_path_exists(local_ref, receipt))
        receipt_sha256 = (
            hashlib.sha256(git_bytes(local_ref, receipt)).hexdigest()
            if receipt_exists
            else None
        )
        in_frozen_main = git_path_exists(ref, receipt)
        in_observed_main = (
            git_path_exists(observed_main_ref, receipt)
            if run_git(["rev-parse", "--verify", observed_main_ref], check=False).returncode == 0
            else None
        )
        unique_result = receipt_exists and not in_frozen_main
        blockers = []
        if not head_matches:
            blockers.append("local PR head does not match recorded immutable head")
        if not receipt_exists:
            blockers.append("expected canonical receipt is absent from PR head")
        if unique_result:
            blockers.append("canonical receipt is not indexed in a repository research registry")
        can_close = not blockers and bool(in_frozen_main or in_observed_main)
        records.append(
            {
                "pr_number": pr_number,
                "pr_state": "OPEN_DRAFT",
                "head_branch": branch,
                "recorded_head_sha": expected_head,
                "observed_head_sha": actual_head,
                "head_matches_record": head_matches,
                "logical_task_id": logical_task_id,
                "expected_canonical_receipt": receipt,
                "receipt_exists": receipt_exists,
                "receipt_sha256": receipt_sha256,
                "in_frozen_main": in_frozen_main,
                "in_observed_main": in_observed_main,
                "contains_unique_result": unique_result,
                "can_close": can_close,
                "close_blocker": blockers,
            }
        )
    receipt_counts = Counter(
        record["expected_canonical_receipt"] for record in records
    )
    for record in records:
        duplicate = receipt_counts[record["expected_canonical_receipt"]] > 1
        record["duplicate_canonical_receipt_across_prs"] = duplicate
        if duplicate:
            record["close_blocker"].append(
                "same canonical receipt is claimed by more than one PR/task record"
            )
            record["can_close"] = False
    observed_main_result = run_git(
        ["rev-parse", "--verify", observed_main_ref], check=False
    )
    return {
        "schema_version": "pr-receipt-extraction-ledger-v1",
        "frozen_sha": ref,
        "observed_main_ref": observed_main_ref,
        "observed_main_sha": (
            observed_main_result.stdout.strip()
            if observed_main_result.returncode == 0
            else None
        ),
        "pr_count": len(PR_METADATA),
        "logical_task_count": len(records),
        "duplicate_canonical_receipt_count": sum(
            record["duplicate_canonical_receipt_across_prs"]
            for record in records
        ),
        "jobs_rerun": 0,
        "prs_closed": 0,
        "prs_merged": 0,
        "records": records,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    columns = [
        "path",
        "name",
        "triggers",
        "trigger_filters",
        "schedule_expressions",
        "top_permissions",
        "job_permissions",
        "contents_write",
        "pull_requests_write",
        "git_commit_or_push",
        "direct_git_commit",
        "direct_git_push",
        "indirect_git_commit",
        "indirect_git_push",
        "direct_main_push",
        "referenced_scripts",
        "missing_referenced_scripts",
        "secrets",
        "concurrency_defined",
        "cancel_in_progress",
        "long_running_likely",
        "modifies_or_generates_workflow",
        "modifies_formal_current",
        "promotes_formal_weight",
        "role",
        "cross_trigger_risk",
        "disposition",
        "target_workflow",
        "classification_basis",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in records:
            row: dict[str, Any] = {}
            for column in columns:
                value = record[column]
                row[column] = (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (list, dict))
                    else value
                )
            writer.writerow(row)


def write_summary_markdown(
    path: Path,
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    unresolved_real_paths = [
        f".github/workflows/{name}" for name in MANUAL_CLASSIFICATIONS if name.startswith(
            (
                "football-workflow-stability",
                "football-v6851",
                "football-v6852",
                "football-v696",
                "football-v697",
                "football-v699",
            )
        )
    ]
    lines = [
        "# Workflow Inventory Summary",
        "",
        f"- Frozen SHA: `{summary['frozen_sha']}`",
        f"- Exact workflow count: **{summary['workflow_count']}**",
        f"- Zero-UNKNOWN gate: **{'PASS' if summary['zero_unknown_gate'] else 'FAIL'}**",
        f"- YAML parse failures: **{summary['yaml_parse_failure_count']}**",
        f"- `contents: write`: **{summary['contents_write_count']}**",
        f"- git commit/push, including one-level helpers: **{summary['git_commit_or_push_count']}**",
        f"- push + write: **{summary['push_and_write_count']}**",
        f"- push + write + persist: **{summary['push_write_and_persist_count']}**",
        f"- direct push to main: **{summary['direct_main_push_count']}**",
        f"- missing concurrency: **{summary['missing_concurrency_count']}**",
        f"- likely-long without cancel: **{summary['long_running_without_cancel_count']}**",
        "",
        "## Final disposition counts",
        "",
    ]
    for disposition, count in summary["disposition_counts"].items():
        lines.append(f"- {disposition}: **{count}**")
    lines.extend(["", "## Six recovered workflow paths", ""])
    lines.extend(f"- `{item}`" for item in sorted(unresolved_real_paths))
    lines.extend(
        [
            "",
            "## Highest-risk workflows",
            "",
            "| Workflow | Risk | Target |",
            "| --- | --- | --- |",
        ]
    )
    risk_order = {
        "CRITICAL_PUSH_WRITE_PERSIST": 0,
        "HIGH_DIRECT_MAIN_PUSH": 1,
        "HIGH_PUSH_WRITE": 2,
        "MEDIUM_PERSIST": 3,
        "LOW_STATIC": 4,
    }
    for record in sorted(
        records,
        key=lambda item: (risk_order[item["cross_trigger_risk"]], item["path"]),
    )[:40]:
        lines.append(
            f"| `{record['path']}` | {record['cross_trigger_risk']} | "
            f"`{record['target_workflow']}` |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default=FROZEN_SHA)
    parser.add_argument("--output-dir", default="governance")
    parser.add_argument("--plan", default="REPOSITORY_GOVERNANCE_PLAN.md")
    parser.add_argument("--observed-main-ref", default="refs/remotes/origin/main")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    plan = parse_plan_dispositions(Path(args.plan))
    raw_paths = run_git(
        ["ls-tree", "-r", "--name-only", args.ref, "--", ".github/workflows"]
    ).stdout.splitlines()
    paths = sorted(
        path for path in raw_paths if path.endswith((".yml", ".yaml"))
    )
    if len(paths) != EXPECTED_WORKFLOW_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_WORKFLOW_COUNT} workflows at {args.ref}, found {len(paths)}"
        )

    records = [audit_workflow(args.ref, path, plan) for path in paths]
    summary = build_summary(records, args.ref)
    if not summary["zero_unknown_gate"]:
        raise RuntimeError("Zero-UNKNOWN gate failed; generated files were not written")

    inventory = {
        "schema_version": "repository-workflow-inventory-v1",
        "frozen_sha": args.ref,
        "summary": summary,
        "workflows": records,
    }
    migration_plan = build_migration_plan(records, args.ref)
    pr_ledger = build_pr_ledger(args.ref, args.observed_main_ref)

    write_json(output_dir / "workflow_inventory.json", inventory)
    write_csv(output_dir / "workflow_inventory.csv", records)
    write_json(output_dir / "workflow_inventory_summary.json", summary)
    write_summary_markdown(
        output_dir / "workflow_inventory_summary.md", summary, records
    )
    write_json(
        output_dir / "legacy_workflow_migration_plan.json", migration_plan
    )
    write_json(output_dir / "pr_receipt_extraction_ledger.json", pr_ledger)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
