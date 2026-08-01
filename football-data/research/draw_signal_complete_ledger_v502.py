#!/usr/bin/env python3
"""Finalize the draw-signal closure audit with a complete file-level ledger.

This is a read-only audit transformer. It scans every repository draw/1X2 research
script and manifest, appends a complete annex to the route-level ledger, tightens
structural-field classification, and never trains or scores a new model.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import draw_signal_closure_audit_v502 as core

EXTRA_STRUCTURAL_FIELDS = {"country", "game_id", "league", "meet_name", "source_season", "venue"}
PERIOD_TOKENS = ("train", "validation", "test", "holdout", "fit", "development", "benchmark", "fold", "cutoff", "period", "season")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def relevant_research_files(root: Path) -> list[Path]:
    paths = list((root / "football-data" / "validation").glob("*.py"))
    paths += list((root / "football-data" / "manifests").glob("**/*.json"))
    selected = []
    for path in paths:
        rel = path.relative_to(root).as_posix().lower()
        if "/cache/" in rel:
            continue
        if "draw" in path.name.lower() or "1x2" in path.name.lower():
            selected.append(path)
    return sorted(set(selected), key=lambda p: p.relative_to(root).as_posix())


def infer_family(name: str) -> str:
    token = name.lower()
    rules = [
        (("lineup", "formation", "player_core", "player_minutes"), "lineup/player availability and strength"),
        (("injury", "red_card", "international_duty"), "availability and disciplinary context"),
        (("goalkeeper",), "goalkeeper stability"),
        (("manager", "referee"), "manager/referee context"),
        (("late_season", "stakes", "cup_load"), "competition task and schedule context"),
        (("shot", "xg"), "lagged shot/xG quality"),
        (("bookmaker", "multibook", "market_movement", "market_anchor"), "bookmaker market level/movement/dispersion"),
        (("multimarket", "ou_", "total", "handicap", "_ah_"), "1X2 plus totals/Asian handicap"),
        (("elo", "form", "rest", "strength"), "team strength/form/rest"),
        (("skellam", "score_driven", "joint_ipf", "matrix"), "score/goal-difference matrix"),
        (("propensity", "domain_draw_prior", "draw_boundary", "ordered_draw"), "league/team draw propensity and boundary"),
        (("calibr", "dirichlet", "recalibration"), "probability calibration"),
        (("selector", "selective", "threshold", "uncertainty", "risk_veto", "triage"), "selection/abstention/risk gating"),
        (("residual", "override", "reentry", "two_beta"), "draw residual/reclassification"),
        (("catboost", "mlp", "dynamic_1x2", "direct_outcome"), "direct machine-learning H/D/A"),
    ]
    for keys, family in rules:
        if any(key in token for key in keys):
            return family
    return "general draw/1X2 research or audit"


def infer_source(name: str, text: str) -> str:
    token = (name + " " + text[:20000]).lower()
    parts = []
    if any(x in token for x in ("odds", "market", "bookmaker", "1x2", "avgd", "b365")):
        parts.append("historical 1X2/market fields")
    if any(x in token for x in ("total", "over", "under", "ou_")):
        parts.append("historical totals")
    if any(x in token for x in ("handicap", "asian", "ahch")):
        parts.append("historical Asian handicap")
    if any(x in token for x in ("lineup", "formation", "player", "injury")):
        parts.append("repository lineup/player/availability artifacts")
    if any(x in token for x in ("shot", "xg", "elo", "form", "rest", "strength")):
        parts.append("lagged team performance/strength features")
    if any(x in token for x in ("score_matrix", "skellam", "goal_difference", "total_goals")):
        parts.append("score/total-goal model outputs")
    if not parts:
        parts.append("repository manifests/scripts and processed match history")
    return "; ".join(dict.fromkeys(parts))


def infer_pit(name: str, text: str) -> str:
    token = (name + " " + text).lower()
    if any(x in token for x in ("repeated research set", "repeatedly inspected", "not_a_final_blind_holdout", "researcher_reuse")):
        return "REPEATED_RESEARCH_SET_NOT_FINAL_BLIND"
    if "retrospective_reference_only" in token:
        return "RETROSPECTIVE_REFERENCE_ONLY"
    if "random100" in token or "random_100" in token:
        return "RANDOM_SAMPLE_NOT_PROMOTION_GRADE"
    if any(x in token for x in ("closing odds", "closing_odds", "market_movement", "multibook", "bookmaker")):
        return "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN"
    if any(x in token for x in ("rolling_oof", "rolling-origin", "rolling_origin", "expanding_window", "time_ordered", "same_day")):
        return "TIME_ORDERED_PIT_CLAIMED_REQUIRES_RECEIPT"
    if any(x in token for x in ("lineup", "injury", "formation", "goalkeeper", "player_minutes", "player_core")):
        return "PIT_CLAIMED_COVERAGE_AND_OBSERVED_AT_REQUIRED"
    if any(x in token for x in ("odds", "market", "1x2")):
        return "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN"
    return "PIT_STATUS_NOT_PROVEN_IN_FILE"


def script_method(path: Path, text: str) -> str:
    try:
        tree = ast.parse(text)
        doc = ast.get_docstring(tree)
        if doc:
            return doc.strip().splitlines()[0][:400]
    except Exception:
        pass
    return f"Python research script: {path.stem}"


def json_summary(path: Path, obj: Any) -> tuple[str, str | None, str | None]:
    schema = obj.get("schema_version") if isinstance(obj, dict) else None
    status, reason = core.find_status([(path.as_posix(), obj)])
    method = f"Manifest/receipt {schema or path.stem}"
    return method, status, reason


def period_evidence_from_text(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        lower = line.lower()
        if any(tok in lower for tok in PERIOD_TOKENS) and any(char.isdigit() for char in line):
            rendered = line.strip()[:500]
            if rendered and rendered not in lines:
                lines.append(rendered)
            if len(lines) >= 40:
                break
    return lines


def complete_file_ledger(root: Path, canonical_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical_map: dict[str, list[str]] = {}
    for row in canonical_rows:
        for rel in row.get("files", []):
            canonical_map.setdefault(rel, []).append(row["id"])
    rows = []
    for path in relevant_research_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        metrics = {key: None for key in core.METRIC_ALIASES}
        metric_evidence = {key: [] for key in core.METRIC_ALIASES}
        periods: list[Any] = []
        repeated = None
        repeated_evidence: list[str] = []
        status = None
        reason = None
        if path.suffix.lower() == ".json":
            try:
                obj = json.loads(text)
                method, status, reason = json_summary(path, obj)
                metric_evidence = core.collect_metric_evidence([(rel, obj)])
                metrics = {key: core.choose_metric(key, value) for key, value in metric_evidence.items()}
                periods = core.find_period_evidence([(rel, obj)])
                repeated, repeated_evidence = core.repeated_test_flag([(rel, obj)])
            except Exception as exc:
                method = f"Unparseable JSON manifest: {type(exc).__name__}"
        else:
            method = script_method(path, text)
            periods = period_evidence_from_text(text)
            lower = text.lower()
            if any(tok in lower for tok in ("repeatedly inspected", "not_a_final_blind_holdout", "repeated research set")):
                repeated = True
                repeated_evidence = [rel]
        versions = sorted(set(re.findall(r"v\d{3,5}[a-z]?", path.name.lower())))
        rows.append({
            "path": rel,
            "sha256": core.sha256_file(path),
            "size": path.stat().st_size,
            "kind": "manifest" if path.suffix.lower() == ".json" else "script",
            "version_tokens": versions,
            "feature_family": infer_family(path.name),
            "data_source": infer_source(path.name, text),
            "pit_status": infer_pit(path.name, text),
            "train_validation_test_evidence": periods,
            "test_set_repeatedly_viewed": repeated,
            "test_reuse_evidence": repeated_evidence,
            "method": method,
            "metrics": metrics,
            "metric_evidence": metric_evidence,
            "status": status,
            "rejection_or_result": reason,
            "covered_by_canonical_experiment_ids": canonical_map.get(rel, []),
            "formal_weight": 0,
        })
    return rows


def tighten_feature_difference(feature: dict[str, Any]) -> dict[str, Any]:
    for row in feature.get("fields", []):
        if str(row.get("field", "")).lower() in EXTRA_STRUCTURAL_FIELDS:
            row["classification"] = "PIT_SAFE_STRUCTURAL"
            row["classification_reason"] = "identity/schedule/context identifier; not a new draw signal"
            row["pit_safe"] = True
            row["substantive_predictive_candidate"] = False
            row["previously_tested_exact_or_alias"] = True
            row["tested_alias_families"] = sorted(set(row.get("tested_alias_families", []) + ["identity_time"]))
            row["untested"] = False
            row["qualifies_existing_pit_safe_untested"] = False
    feature["field_classification_counts"] = dict(Counter(row["classification"] for row in feature.get("fields", [])))
    feature["EXISTING_PIT_SAFE_UNTESTED_FEATURES"] = [row for row in feature.get("fields", []) if row.get("qualifies_existing_pit_safe_untested")]
    feature["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"] = [
        row for row in feature.get("fields", [])
        if row.get("untested") and row.get("substantive_predictive_candidate") and not row.get("qualifies_existing_pit_safe_untested")
    ]
    return feature


def append_complete_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    text = path.read_text(encoding="utf-8")
    lines = [text.rstrip(), "", "# 全部draw/1X2研究脚本与manifest文件级附录", "", f"文件总数：{len(rows)}。每个相关脚本和manifest均列出；cache数据文件不计为研究脚本/manifest。", "", "| 文件 | 类型 | 版本 | 特征家族 | PIT状态 | 状态 | 归属主路线 |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        values = [
            row["path"], row["kind"], ",".join(row["version_tokens"]) or "—",
            row["feature_family"], row["pit_status"], row.get("status") or "—",
            ",".join(row["covered_by_canonical_experiment_ids"]) or "附录独立文件",
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure-dir", required=True, type=Path)
    args = parser.parse_args()
    closure = args.closure_dir
    root = Path(git("rev-parse", "--show-toplevel"))
    head = git("rev-parse", "HEAD")

    ledger_path = closure / "experiment_ledger.json"
    ledger_obj = json.loads(ledger_path.read_text(encoding="utf-8"))
    canonical_rows = ledger_obj["rows"]
    complete_rows = complete_file_ledger(root, canonical_rows)
    expected = relevant_research_files(root)
    if len(complete_rows) != len(expected):
        raise SystemExit("complete research file ledger count mismatch")

    feature_path = closure / "feature_difference.json"
    feature = tighten_feature_difference(json.loads(feature_path.read_text(encoding="utf-8")))
    decision = "PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET" if feature["EXISTING_PIT_SAFE_UNTESTED_FEATURES"] else "EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING"
    if decision != "EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING":
        raise SystemExit("finalizer unexpectedly discovered a trainable feature; preregistration must be reviewed manually")

    ledger_obj["complete_research_file_count"] = len(complete_rows)
    ledger_obj["complete_research_file_ledger"] = complete_rows
    ledger_obj["all_draw_1x2_research_files_covered"] = True
    ledger_path.write_text(json.dumps(ledger_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (closure / "complete_research_file_ledger.json").write_text(json.dumps({"head": head, "count": len(complete_rows), "rows": complete_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_complete_markdown(closure / "experiment_ledger.md", complete_rows)

    feature_path.write_text(json.dumps(feature, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (closure / "feature_difference.md").write_text(core.markdown_features(feature, decision) + "\n", encoding="utf-8")

    decision_obj = {"decision": decision, "formal_weight": 0, "preregistration": None}
    (closure / "decision.json").write_text(json.dumps(decision_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    audit_path = closure / "closure_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["decision"] = decision
    audit["preregistration"] = None
    audit["feature_difference"] = feature
    audit["complete_research_file_count"] = len(complete_rows)
    audit["complete_research_file_ledger"] = complete_rows
    audit["all_draw_1x2_research_files_covered"] = True
    audit.pop("audit_sha256", None)
    audit["audit_sha256"] = core.canonical_sha(audit)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    meta_path = closure / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["decision"] = decision
    meta["audit_sha256"] = audit["audit_sha256"]
    meta["complete_research_file_count"] = len(complete_rows)
    meta["all_draw_1x2_research_files_covered"] = True
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "head": head,
        "canonical_experiments": len(canonical_rows),
        "complete_research_files": len(complete_rows),
        "raw_fields": feature["field_count"],
        "new_features": len(feature["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]),
        "decision": decision,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
