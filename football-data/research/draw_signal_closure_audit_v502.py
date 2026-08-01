#!/usr/bin/env python3
"""Read-only closure audit for existing draw/1X2 research.

No model training, no provider access, no secret access, no formal-asset writes.
The script inventories prior experiments, profiles repository-existing fields, and
issues a fail-closed decision on whether a genuinely new PIT-safe feature exists.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

FORMAL_WEIGHT = 0
MIN_COVERAGE = 0.70

REQUIRED_VERSION_PREFIXES = (
    "V4.7.0", "V6.4.3", "V6.7.0", "V6.7.1", "V6.7.2", "V6.7.3", "V6.7.4",
    "V6.10.", "V6.11.", "V6.32", "V6.33", "V6.34", "V6.50.7", "V6.50.8",
    "V6.50.9", "V6.51.0", "V6.51.1", "V6.51.2",
)

EXPERIMENT_SPECS = [
    dict(id="V4.7.0-DRAW-STRUCTURE", version="V4.7.0", patterns=["draw_structure_diagnostics_v470"], family="league/team draw structure diagnostics", method="descriptive draw-rate and structural diagnostics", source="processed historical match results; repository-derived pre-match history windows", pit="MIXED: rolling history can be PIT-safe; retrospective market fields not used as formal evidence"),
    dict(id="V4.7.0-DRAW-DISCRIMINATION", version="V4.7.0", patterns=["draw_discrimination_diagnostics_v470"], family="draw discrimination / sharpness", method="domain draw probability discrimination diagnostics including AUC and dispersion", source="formal rolling OOF draw probabilities and completed-match labels", pit="PIT_SAFE_IF_OOF_RECEIPTS_VALID"),
    dict(id="V4.7.0-RESIDUAL-SCREEN", version="V4.7.0", patterns=["draw_residual_challenger_screen_v470"], family="outcome residual draw screen", method="screened residual draw challenger", source="existing formal probabilities plus time-ordered labels", pit="PIT_SAFE_IF_STRICT_OOF; target-period reuse risk must be checked"),
    dict(id="V4.7.0-RESIDUAL-OOF", version="V4.7.0", patterns=["draw_residual_rolling_oof_v470"], family="rolling OOF residual draw model", method="rolling-origin residual draw classifier", source="existing OOF probabilities, lagged team/domain history", pit="PIT_SAFE_ROLLING_ORIGIN"),
    dict(id="V4.7.0-ONLINE-RESIDUAL", version="V4.7.0", patterns=["draw_online_residual_rolling_oof_v470"], family="online residual draw model", method="online/expanding residual update after completed matches", source="existing OOF probabilities and lagged outcomes", pit="PIT_SAFE_IF_SAME_DAY_BATCHING"),
    dict(id="V4.7.0-D-CONDITIONAL-TOTAL", version="V4.7.0", patterns=["conditional_draw_by_total_rolling_oof_v470"], family="conditional D|Total", method="draw probability conditioned on direct total-goal distribution", source="unified score/total-goal OOF outputs", pit="PIT_SAFE_OOF"),
    dict(id="V4.7.0-REPAIR-VERDICT", version="V4.7.0", patterns=["draw_repair_research_verdict_v470"], family="V4.7.0 draw repair registry", method="governance synthesis of prior challengers", source="prior V4.7.0 receipts", pit="DERIVED_AUDIT_ONLY"),
    dict(id="V6.4.3-MULTIMARKET", version="V6.4.3", patterns=["v6_multimarket_draw_side_v643"], family="1X2 + AH + OU multimarket classification", method="multimarket draw-side classifier", source="historical 1X2, Asian handicap and totals", pit="RETROSPECTIVE_REFERENCE_ONLY: quote timestamps not proven"),
    dict(id="V6.7.0-DRAW-OVERRIDE", version="V6.7.0", patterns=["v6_draw_residual_override_v670"], family="generic draw override", method="logistic residual override on non-draw market picks", source="historical market probabilities and derived residual features", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.7.1-DYNAMIC-PROPENSITY", version="V6.7.1", patterns=["v6_dynamic_draw_propensity_v671"], family="dynamic team/league draw propensity", method="lagged team and league draw propensity model", source="completed matches before prediction date", pit="PIT_SAFE_IF_STRICT_LAGGING"),
    dict(id="V6.7.2-ZM-SKELLAM", version="V6.7.2", patterns=["v6_zero_modified_skellam_draw_v672"], family="zero-modified Skellam", method="zero-modified score-difference model", source="lagged team goal rates and score model", pit="PIT_SAFE_IF_ROLLING"),
    dict(id="V6.7.3-OUTCOME-CALIBRATION", version="V6.7.3", patterns=["v6_draw_probability_calibration_v673"], family="outcome-only draw calibration", method="calibration of baseline draw probability", source="existing baseline probabilities and outcomes", pit="PIT_SAFE_ONLY_WITH_NESTED_TIME_SPLIT"),
    dict(id="V6.7.4-REGISTRY", version="V6.7.4", patterns=["v6_draw_resolution_registry_v674"], family="draw experiment registry", method="registry and comparative verdict", source="V6.7.0-V6.7.3 receipts", pit="DERIVED_AUDIT_ONLY"),
    dict(id="V6.10.0-RESIDUAL-ML", version="V6.10.0", patterns=["v6_1x2_market_residual_ml_v6100"], family="market residual ML", method="logistic/HGB-style residual multiclass model", source="historical market plus repository features", pit="RETROSPECTIVE_REFERENCE_ONLY for market quote timing"),
    dict(id="V6.10.1-MULTIBOOK", version="V6.10.1", patterns=["v6_1x2_multibook_consensus_v6101"], family="bookmaker disagreement/dispersion", method="multi-book consensus and dispersion diagnostics", source="historical bookmaker odds", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.10.2-MARKET-FORM", version="V6.10.2", patterns=["v6_1x2_market_form_residual_v6102"], family="market + Elo/form/rest residual", method="residual classifier with form/rest/team context", source="historical market plus lagged team context", pit="MIXED: lagged context PIT-safe; market timing retrospective"),
    dict(id="V6.10.3-SELECTIVE-MARKET", version="V6.10.3", patterns=["v6_1x2_selective_market_v6103"], family="selective market decision", method="confidence/coverage selection", source="historical devigged 1X2", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.10.4-COMP-FILTER", version="V6.10.4", patterns=["v6_1x2_selective_competition_filter_v6104"], family="competition selective filter", method="competition-specific abstention filter", source="historical market and competition reliability", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.10.5-MOVEMENT", version="V6.10.5", patterns=["v6_1x2_market_movement_v6105"], family="opening-to-closing movement", method="market movement features", source="opening and closing odds columns", pit="RETROSPECTIVE_REFERENCE_ONLY: no original quote timestamps"),
    dict(id="V6.10.6-CALIBRATION", version="V6.10.6", patterns=["v6_1x2_market_calibration_v6106"], family="multiclass market calibration", method="multiclass probability calibration", source="devigged historical 1X2", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.10.7-SELECTIVE-MULTISEASON", version="V6.10.7", patterns=["v6_1x2_market_selective_multiseason_v6107"], family="multiseason selective H/D/A", method="time-ordered selective calibration", source="historical 1X2 across seasons", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.10.8-CROSSSEASON-CAL", version="V6.10.8", patterns=["v6_1x2_market_calibration_crossseason_v6108"], family="cross-season multiclass calibration", method="cross-season calibration and evaluation", source="historical 1X2 across seasons", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.11.1-METHOD-REGISTRY", version="V6.11.1", patterns=["v6_1x2_research_method_registry_v6111"], family="1X2 method registry", method="research registry / duplicate-route control", source="prior 1X2 receipts", pit="DERIVED_AUDIT_ONLY"),
    dict(id="V6.11.3-OU-OVERRIDE", version="V6.11.3", patterns=["v6_1x2_ou_draw_override_v6113"], family="1X2 + OU draw override", method="totals-conditioned draw override", source="historical 1X2 and totals", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.11.4-TWO-BETA", version="V6.11.4", patterns=["v6_1x2_two_beta_draw_v6114"], family="two-beta draw decomposition", method="draw-vs-decisive plus conditional H-vs-A", source="historical devigged 1X2", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.11.5-BOOK-ROUTING", version="V6.11.5", patterns=["v6_1x2_bookmaker_routing_v6115"], family="bookmaker routing", method="route/select bookmaker inputs", source="historical bookmaker odds", pit="RETROSPECTIVE_REFERENCE_ONLY"),
    dict(id="V6.11.6-LAGGED-SHOT", version="V6.11.6", patterns=["v6_1x2_lagged_shot_quality_v6116"], family="lagged shot quality", method="lagged shot/xG-quality features", source="prior-match shots and goals, lagged only", pit="PIT_SAFE_IF_LAGGED; coverage must be checked"),
    dict(id="V6.11.7-LINEUP", version="V6.11.7", patterns=["v6_1x2_pit_lineup_increment_v6117_status"], family="PIT lineup increment", method="lineup increment challenger", source="lineup snapshots matched before kickoff", pit="PIT_CLAIMED; matching coverage/robustness required"),
    dict(id="V6.11.7B-LINEUP", version="V6.11.7b", patterns=["v6_1x2_pit_lineup_increment_v6117b"], family="PIT lineup increment revision", method="lineup feature revision", source="lineup snapshots", pit="PIT_CLAIMED"),
    dict(id="V6.11.7C-LINEUP", version="V6.11.7c", patterns=["v6_1x2_pit_lineup_increment_v6117c"], family="PIT lineup increment revision", method="lineup feature revision", source="lineup snapshots", pit="PIT_CLAIMED"),
    dict(id="V6.11.8-LINEUP-ROBUST", version="V6.11.8", patterns=["v6_1x2_pit_lineup_robustness_v6118"], family="lineup robustness", method="lineup robustness / missingness evaluation", source="lineup snapshots", pit="PIT_CLAIMED"),
    dict(id="V6.11.9-LINEUP-VALUE", version="V6.11.9", patterns=["v6_1x2_pit_lineup_valuation_v6119"], family="lineup valuation", method="player/lineup value aggregation", source="pre-match lineup and valuation snapshots", pit="PIT_CLAIMED; external availability evidence must be audited"),
    dict(id="V6.32.0-CATBOOST", version="V6.32.0", patterns=["v6_direct_xg_shot_market_catboost_random100_v6320"], family="market+xG+shot CatBoost", method="CatBoost direct multiclass random100 research", source="market, xG and shot features", pit="MIXED; random split/sample and market timing prevent promotion"),
    dict(id="V6.32.1-OVERLAP", version="V6.32.1", patterns=["v6_direct_random100_information_overlap_v6321"], family="information overlap audit", method="feature overlap / leakage audit", source="V6.32.0 features", pit="AUDIT_ONLY"),
    dict(id="V6.33.0-PLAYER-CORE", version="V6.33.0", patterns=["v6_player_core_strength_random100_v6330"], family="player core strength", method="player-core strength random100 challenger", source="player/lineup strength artifacts", pit="PIT_UNPROVEN_OR_LOW_COVERAGE"),
    dict(id="V6.34.0-MLP", version="V6.34.0", patterns=["v6_mlp_1x2_random100_v6340"], family="MLP existing-feature model", method="multilayer perceptron direct H/D/A", source="existing market/team/context features", pit="MIXED; random100 is not strict future validation"),
    dict(id="V6.50.7-NET-GAIN", version="V6.50.7", patterns=["v6_draw_residual_net_gain_v6507"], family="direct H/A-to-D net-gain override", method="logistic draw residual utility rule", source="frozen selector rows and existing features", pit="TIME_ORDERED_STYLE_BUT_REPEATED_TARGET_SET"),
    dict(id="V6.50.8-TRIAGE", version="V6.50.8", patterns=["v6_draw_triage_decision_v6508"], family="draw-vs-decisive triage plus error risk", method="three-model draw/correct/error triage", source="frozen selector rows and existing features", pit="TIME_ORDERED_STYLE_BUT_REPEATED_TARGET_SET"),
    dict(id="V6.50.9-RISK-VETO", version="V6.50.9", patterns=["v6_draw_risk_operating_point_v6509"], family="H/A risk abstention", method="P_BASE_ERROR threshold veto; no draw reclassification", source="frozen selector rows", pit="TIME_ORDERED_STYLE_BUT_REPEATED_TARGET_SET"),
    dict(id="V6.51.0-REENTRY", version="V6.51.0", patterns=["v6_selective_draw_reentry_v6510", "v6_draw_problem_resolution_v6510"], family="selective draw re-entry", method="draw re-entry inside veto pool", source="V6.50.9 veto pool", pit="REPEATED_2025_RESEARCH_SET"),
    dict(id="V6.51.1-DISCRETE-NB", version="V6.51.1", patterns=["v6_draw_discrete_nb_v6511"], family="class-separating discrete NB", method="discrete Naive Bayes draw classifier", source="rank/market/context bins", pit="REPEATED_2025_RESEARCH_SET"),
    dict(id="V6.51.2-RANK-NB", version="V6.51.2", patterns=["v6_draw_rank_attack_nb_v6512", "v6_draw_problem_resolution_v6512"], family="NB rank attack/defence", method="rank attack/defence discrete Naive Bayes", source="team rank attack/defence and market/context bins", pit="REPEATED_2025_RESEARCH_SET"),
]

POSTMATCH_COLUMNS = {
    "fthg", "ftag", "ftr", "hthg", "htag", "htr", "hc", "ac", "hf", "af",
    "hr", "ar", "hs", "as", "hst", "ast", "hy", "ay", "full_time_home_goals",
    "full_time_away_goals", "home_goals", "away_goals", "result", "actual", "label",
}
STRUCTURAL_COLUMNS = {
    "league_id", "competition_id", "season", "stage", "source_code", "date", "time",
    "hometeam", "awayteam", "home_team", "away_team", "div",
}
PIT_UNPROVEN_CONTEXT = {"referee"}
MARKET_HINTS = (
    "b365", "avg", "max", "ps", "wh", "vc", "bw", "iw", "odds", "price",
    "ah", "<2.5", ">2.5", "home_price", "draw_price", "away_price",
)

TESTED_FAMILY_ALIASES = {
    "identity_time": STRUCTURAL_COLUMNS | {"kickoff", "kickoff_utc", "month", "weekday", "season_phase"},
    "market_1x2": {"avgh", "avgd", "avga", "b365h", "b365d", "b365a", "psh", "psd", "psa", "whh", "whd", "wha", "vch", "vcd", "vca", "bwh", "bwd", "bwa", "iwh", "iwd", "iwa"},
    "market_closing": {"avgch", "avgcd", "avgca", "b365ch", "b365cd", "b365ca", "psch", "pscd", "psca", "whch", "whcd", "whca", "vcch", "vccd", "vcca", "bwch", "bwcd", "bwca", "iwch", "iwcd", "iwca"},
    "totals": {"avg<2.5", "avg>2.5", "avgc<2.5", "avgc>2.5", "b365<2.5", "b365>2.5", "b365c<2.5", "b365c>2.5", "max<2.5", "max>2.5", "maxc<2.5", "maxc>2.5", "p<2.5", "p>2.5", "pc<2.5", "pc>2.5"},
    "asian_handicap": {"ahch", "ahh", "avgahh", "avgaha", "avgcahh", "avgcaha", "b365ahh", "b365aha", "b365cahh", "b365caha", "pahh", "paha", "pcahh", "pcaha"},
    "lagged_match_stats": {"hs", "as", "hst", "ast", "hc", "ac", "hf", "af", "hy", "ay", "hr", "ar"},
    "score_result": POSTMATCH_COLUMNS,
    "referee": {"referee"},
}

METRIC_ALIASES = {
    "accuracy": ("accuracy", "acc", "top1_accuracy", "full_1x2_accuracy", "combined_accuracy", "final_accuracy", "selected_accuracy"),
    "macro_f1": ("macro_f1", "macro-f1", "macro_f1_score"),
    "draw_precision": ("draw_precision", "precision_draw", "draw_prec"),
    "draw_recall": ("draw_recall", "recall_draw"),
    "draw_f1": ("draw_f1", "f1_draw", "draw_f1_score"),
    "log_loss": ("log_loss", "logloss", "cross_entropy"),
    "brier": ("brier", "brier_score"),
    "rps": ("rps", "ranked_probability_score"),
    "coverage": ("coverage", "market_coverage", "coverage_on_market_population", "selected_coverage"),
    "executed": ("executed", "executed_n", "executed_count", "selected_n", "count"),
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def recursive_items(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, val in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, val
            yield from recursive_items(val, path)
    elif isinstance(value, list):
        for i, val in enumerate(value):
            path = f"{prefix}[{i}]"
            yield path, val
            yield from recursive_items(val, path)


def norm_key(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def collect_metric_evidence(objects: list[tuple[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {key: [] for key in METRIC_ALIASES}
    for file_path, obj in objects:
        for path, value in recursive_items(obj):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            npath = norm_key(path)
            leaf = npath.split("_")[-1] if npath else ""
            for metric, aliases in METRIC_ALIASES.items():
                if any(alias in npath or leaf == alias for alias in aliases):
                    evidence[metric].append({"file": file_path, "json_path": path, "value": float(value)})
    return evidence


def choose_metric(metric: str, evidence: list[dict[str, Any]]) -> float | None:
    if not evidence:
        return None
    def rank(item: dict[str, Any]) -> tuple[int, int, str]:
        text = (item["file"] + " " + item["json_path"]).lower()
        score = 0
        if any(tok in text for tok in ("holdout", "test", "future", "full_1x2", "overall", "aggregate")):
            score -= 20
        if any(tok in text for tok in ("validation", "train", "best", "selected", "veto", "coverage")):
            score += 10
        if metric == "accuracy" and "selected" in text:
            score += 20
        return score, len(text), text
    return float(sorted(evidence, key=rank)[0]["value"])


def find_period_evidence(objects: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tokens = ("train", "validation", "test", "holdout", "fit", "development", "benchmark", "fold", "cutoff", "period", "season")
    for file_path, obj in objects:
        for path, value in recursive_items(obj):
            n = norm_key(path)
            if any(tok in n for tok in tokens) and isinstance(value, (str, int, float, list, dict)):
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)[:1000] if isinstance(value, (dict, list)) else str(value)[:1000]
                out.append({"file": file_path, "json_path": path, "value": rendered})
    return out[:120]


def find_status(objects: list[tuple[str, Any]]) -> tuple[str | None, str | None]:
    statuses: list[str] = []
    reasons: list[str] = []
    for _, obj in objects:
        for path, value in recursive_items(obj):
            n = norm_key(path)
            if isinstance(value, str):
                if n.endswith("status") or "decision" in n or "verdict" in n or "classification" in n:
                    statuses.append(value)
                if any(tok in n for tok in ("reason", "result", "interpretation", "diagnosis", "failure", "rejection")):
                    reasons.append(value)
    status = next((s for s in statuses if any(tok in s.upper() for tok in ("REJECT", "FAIL", "PASS", "INACTIVE", "NOT_SUPPORTED"))), statuses[0] if statuses else None)
    reason = max(reasons, key=len) if reasons else None
    return status, reason


def repeated_test_flag(objects: list[tuple[str, Any]]) -> tuple[bool | None, list[str]]:
    evidence = []
    flag: bool | None = None
    for file_path, obj in objects:
        raw = json.dumps(obj, ensure_ascii=False).lower()
        if any(tok in raw for tok in ("repeated research set", "repeatedly inspected", "not_a_final_blind_holdout", "not an untouched", "researcher_reuse")):
            flag = True
            evidence.append(file_path)
        if any(tok in raw for tok in ("untouched final holdout", "pristine holdout", "blind holdout")) and flag is None:
            flag = False
            evidence.append(file_path)
    return flag, sorted(set(evidence))


def experiment_files(root: Path, spec: dict[str, Any]) -> list[Path]:
    candidates = list((root / "football-data" / "manifests").glob("**/*")) + list((root / "football-data" / "validation").glob("*.py"))
    return sorted({path for path in candidates if path.is_file() and any(pattern.lower() in path.name.lower() for pattern in spec["patterns"])})


def build_ledger(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ledger = []
    audited_files = []
    for spec in EXPERIMENT_SPECS:
        files = experiment_files(root, spec)
        json_objects = []
        script_texts = []
        for path in files:
            rel = path.relative_to(root).as_posix()
            audited_files.append({"path": rel, "sha256": sha256_file(path), "size": path.stat().st_size})
            if path.suffix.lower() == ".json":
                try:
                    json_objects.append((rel, json_load(path)))
                except Exception as exc:
                    json_objects.append((rel, {"parse_error": f"{type(exc).__name__}: {exc}"}))
            elif path.suffix.lower() in {".py", ".md", ".txt"}:
                script_texts.append((rel, path.read_text(encoding="utf-8", errors="replace")))
        metrics_evidence = collect_metric_evidence(json_objects)
        status, reason = find_status(json_objects)
        repeated, repeated_evidence = repeated_test_flag(json_objects)
        confusion = []
        for file_path, obj in json_objects:
            for path, value in recursive_items(obj):
                if "confusion" in norm_key(path) and isinstance(value, (dict, list)):
                    confusion.append({"file": file_path, "json_path": path, "value": value})
        text_join = "\n".join(text for _, text in script_texts).lower()
        feature_tokens = sorted(set(re.findall(r"['\"]([A-Za-z][A-Za-z0-9_<>.+/-]{1,50})['\"]", text_join)))
        row = dict(spec)
        row.update({
            "files": [path.relative_to(root).as_posix() for path in files],
            "file_count": len(files),
            "file_sha256": {path.relative_to(root).as_posix(): sha256_file(path) for path in files},
            "train_validation_test_evidence": find_period_evidence(json_objects),
            "test_set_repeatedly_viewed": repeated,
            "test_reuse_evidence": repeated_evidence,
            "metrics": {metric: choose_metric(metric, ev) for metric, ev in metrics_evidence.items()},
            "metric_evidence": metrics_evidence,
            "confusion_matrix_evidence": confusion,
            "status": status,
            "rejection_or_result": reason,
            "script_feature_tokens": feature_tokens[:400],
            "formal_weight": 0,
        })
        ledger.append(row)
    audited_files = sorted({item["path"]: item for item in audited_files}.values(), key=lambda x: x["path"])
    return ledger, audited_files


def classify_column(column: str) -> tuple[str, str]:
    c = column.strip().lower()
    if c in POSTMATCH_COLUMNS:
        return "POSTMATCH_FORBIDDEN", "match result or in-match/postmatch statistic"
    if c in STRUCTURAL_COLUMNS:
        return "PIT_SAFE_STRUCTURAL", "identity/schedule field; not a new draw signal"
    if c in PIT_UNPROVEN_CONTEXT:
        return "PIT_UNPROVEN_CONTEXT", "repository has no observed_at proving pre-match availability"
    if any(hint in c for hint in MARKET_HINTS):
        return "RETROSPECTIVE_MARKET_REFERENCE", "odds/line without original quote timestamp"
    if re.fullmatch(r"[ha](c|f|r|s|st|y)", c):
        return "POSTMATCH_FORBIDDEN", "in-match statistic"
    return "UNKNOWN_PIT_STATUS", "no repository-wide timestamp contract proves prediction-time availability"


def extract_script_tokens(paths: list[Path]) -> set[str]:
    tokens: set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip().lower()
                if 1 < len(value) <= 80 and re.fullmatch(r"[a-z0-9_<>.+/ -]+", value):
                    tokens.add(value)
    return tokens


def profile_fields(root: Path) -> dict[str, Any]:
    files = sorted((root / "football-data" / "processed").glob("**/*.csv"))
    field_stats: dict[str, dict[str, Any]] = {}
    total_rows = 0
    file_manifest = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        sha = sha256_file(path)
        row_count = 0
        present_counts: Counter[str] = Counter()
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                columns = reader.fieldnames or []
                for row in reader:
                    row_count += 1
                    for col in columns:
                        if str(row.get(col) or "").strip():
                            present_counts[col] += 1
        except Exception as exc:
            file_manifest.append({"path": rel, "sha256": sha, "error": f"{type(exc).__name__}: {exc}"})
            continue
        total_rows += row_count
        competition = path.parent.name
        file_manifest.append({"path": rel, "sha256": sha, "rows": row_count, "columns": columns})
        for col in columns:
            stat = field_stats.setdefault(col, {"files": 0, "rows_in_files": 0, "nonempty": 0, "competitions": set(), "sample_paths": []})
            stat["files"] += 1
            stat["rows_in_files"] += row_count
            stat["nonempty"] += present_counts[col]
            stat["competitions"].add(competition)
            if len(stat["sample_paths"]) < 5:
                stat["sample_paths"].append(rel)
    scripts = sorted((root / "football-data" / "validation").glob("*.py")) + sorted((root / "football-data" / "research").glob("*.py"))
    tested_tokens = extract_script_tokens(scripts)
    for aliases in TESTED_FAMILY_ALIASES.values():
        tested_tokens.update(alias.lower() for alias in aliases)
    fields = []
    for col, stat in sorted(field_stats.items(), key=lambda kv: kv[0].lower()):
        cls, reason = classify_column(col)
        coverage = stat["nonempty"] / stat["rows_in_files"] if stat["rows_in_files"] else 0.0
        normalized = col.lower().strip()
        exact_tested = normalized in tested_tokens
        alias_families = [family for family, aliases in TESTED_FAMILY_ALIASES.items() if normalized in aliases]
        substantive = cls not in {"PIT_SAFE_STRUCTURAL", "POSTMATCH_FORBIDDEN"}
        pit_safe = cls == "PIT_SAFE_PREDICTION_TIME"
        untested = not exact_tested and not alias_families
        fields.append({
            "field": col,
            "classification": cls,
            "classification_reason": reason,
            "files": stat["files"],
            "rows_in_files": stat["rows_in_files"],
            "nonempty": stat["nonempty"],
            "coverage_within_present_files": coverage,
            "competition_count": len(stat["competitions"]),
            "competitions": sorted(stat["competitions"]),
            "sample_paths": stat["sample_paths"],
            "previously_tested_exact_or_alias": bool(exact_tested or alias_families),
            "tested_alias_families": alias_families,
            "substantive_predictive_candidate": substantive,
            "pit_safe": pit_safe,
            "untested": untested,
            "qualifies_existing_pit_safe_untested": pit_safe and substantive and untested and coverage >= MIN_COVERAGE,
        })
    untested = [row for row in fields if row["qualifies_existing_pit_safe_untested"]]
    return {
        "processed_csv_count": len(files),
        "processed_total_rows_scanned": total_rows,
        "data_file_manifest": file_manifest,
        "field_count": len(fields),
        "fields": fields,
        "tested_feature_token_count": len(tested_tokens),
        "tested_feature_tokens_sha256": canonical_sha(sorted(tested_tokens)),
        "EXISTING_PIT_SAFE_UNTESTED_FEATURES": untested,
    }


def markdown_ledger(ledger: list[dict[str, Any]]) -> str:
    lines = ["# 现有数据平局信号闭合审计｜实验总账", "", "仅研究态；formal_weight=0；未训练新模型。", "", "| 实验 | 特征家族 | PIT状态 | 方法 | Accuracy | Macro-F1 | Draw P/R/F1 | LogLoss | Brier | RPS | 状态 |", "|---|---|---|---|---:|---:|---|---:|---:|---:|---|"]
    for row in ledger:
        m = row["metrics"]
        draw = "/".join("—" if m[k] is None else f"{m[k]:.4f}" for k in ("draw_precision", "draw_recall", "draw_f1"))
        vals = [row["id"], row["family"], row["pit"], row["method"],
                "—" if m["accuracy"] is None else f"{m['accuracy']:.4f}",
                "—" if m["macro_f1"] is None else f"{m['macro_f1']:.4f}", draw,
                "—" if m["log_loss"] is None else f"{m['log_loss']:.4f}",
                "—" if m["brier"] is None else f"{m['brier']:.4f}",
                "—" if m["rps"] is None else f"{m['rps']:.4f}", row.get("status") or "未记录"]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|") for v in vals) + " |")
    lines += ["", "## 审计解释", "", "- `—`表示原manifest未保存该指标，不能事后猜测。", "- selected accuracy与完整三分类accuracy严格分开。", "- 2025重复研究集不能称为未查看最终盲测。", ""]
    return "\n".join(lines)


def markdown_features(feature_audit: dict[str, Any], decision: str) -> str:
    lines = ["# 现有字段差集", "", f"裁决：`{decision}`", "", f"扫描CSV：{feature_audit['processed_csv_count']}；行数：{feature_audit['processed_total_rows_scanned']}；唯一字段：{feature_audit['field_count']}。", "", "## EXISTING_PIT_SAFE_UNTESTED_FEATURES", ""]
    features = feature_audit["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]
    if not features:
        lines.append("空集。")
    else:
        lines += ["| 字段 | 覆盖率 | 联赛数 | PIT证明 | 未使用证明 |", "|---|---:|---:|---|---|"]
        for row in features:
            lines.append(f"| {row['field']} | {row['coverage_within_present_files']:.2%} | {row['competition_count']} | {row['classification_reason']} | exact/alias未命中 |")
    lines += ["", "## 排除规则", "", "- 比分、赛果、射门、角球、犯规、牌等为赛中/赛后字段。", "- 历史赔率和盘口缺原始报价时间戳，只能作为回顾性市场参考。", "- 联赛、赛季、日期、时间、主客队等是身份/切分字段，不是新的平局信号。", "- Referee等上下文字段缺`observed_at`，无法证明在统一预测冻结时点前可用。", "- 同义字段按特征家族去重，不能改名后重新计作新信息。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    root = Path(git("rev-parse", "--show-toplevel"))
    head = git("rev-parse", "HEAD")
    ledger, audited_files = build_ledger(root)
    found_versions = {row["version"] for row in ledger if row["file_count"] > 0}
    missing_required = [prefix for prefix in REQUIRED_VERSION_PREFIXES if not any(version.startswith(prefix) for version in found_versions)]
    if missing_required:
        raise SystemExit(f"required experiment families missing: {missing_required}")
    feature_audit = profile_fields(root)
    has_new = bool(feature_audit["EXISTING_PIT_SAFE_UNTESTED_FEATURES"])
    decision = "PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET" if has_new else "EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING"
    prereg = None
    if has_new:
        prereg = {
            "status": "PRE_REGISTERED_NOT_RUN",
            "features": [row["field"] for row in feature_audit["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]],
            "model": "multinomial_logistic_with_nested_time_ordered_calibration",
            "hyperparameter_grid": {"l2": [0.1, 1.0, 10.0, 100.0], "class_weight_draw": [1.0, 1.15, 1.3]},
            "outer_folds": "expanding-window by season; final latest season uniquely untouched",
            "inner_selection": "training-only rolling validation; no random split",
            "primary_metric": "paired improvement in draw_F1 and macro_F1 subject to noninferiority gates",
            "promotion_gates": {
                "accuracy_noninferiority": "delta >= -0.005",
                "log_loss_noninferiority": "delta <= +0.005",
                "brier_noninferiority": "delta <= +0.005",
                "rps_noninferiority": "delta <= +0.003",
                "draw_f1_improvement": "> 0 with paired bootstrap 95% CI lower bound >= 0",
                "macro_f1_improvement": "> 0 with paired bootstrap 95% CI lower bound >= 0",
                "minimum_draw_predictions_per_outer_fold": 20,
                "minimum_coverage_if_abstaining": 0.50,
            },
            "formal_weight": 0,
        }
    audit = {
        "schema_version": "DRAW-SIGNAL-CLOSURE-AUDIT-V502-1.0",
        "head": head,
        "base_main": "605abf2d9f98c46f063106c7bd47193b96e588e4",
        "formal_weight": 0,
        "provider_network_used": False,
        "external_request_attempts": 0,
        "api_football_key_accessed": False,
        "model_training": 0,
        "decision": decision,
        "required_version_prefixes": REQUIRED_VERSION_PREFIXES,
        "missing_required_version_prefixes": missing_required,
        "experiment_count": len(ledger),
        "experiment_ledger": ledger,
        "audited_files": audited_files,
        "feature_difference": feature_audit,
        "preregistration": prereg,
        "known_codex_baseline": {
            "strict_time_ordered_matches": 4786,
            "actual_draw_rate": 0.2566,
            "mean_predicted_draw_probability": 0.2479,
            "mean_domain_draw_auc": 0.52218,
            "draw_probability_std": 0.01711,
            "full_1x2_count": 5110,
            "full_1x2_hits": 2645,
            "full_1x2_accuracy": 0.517613,
            "full_predicted_home_draw_away": [3532, 40, 1538],
            "selector_executed": 1611,
            "selector_accuracy": 0.671012,
            "selector_predicted_home_draw_away": [1252, 0, 359],
            "risk_veto_executed": 1252,
            "risk_veto_accuracy": 0.707668,
            "risk_veto_market_coverage": 0.245010,
            "risk_veto_draw_predictions": 0,
            "baseline_status": "USER_SUPPLIED_CODEX_READ_ONLY_CHECK_TO_BE_RECONCILED_WITH_REPOSITORY_RECEIPTS",
        },
    }
    audit["audit_sha256"] = canonical_sha(audit)
    (out / "experiment_ledger.json").write_text(json.dumps({"head": head, "formal_weight": 0, "rows": ledger}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "experiment_ledger.md").write_text(markdown_ledger(ledger) + "\n", encoding="utf-8")
    (out / "feature_difference.json").write_text(json.dumps(feature_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "feature_difference.md").write_text(markdown_features(feature_audit, decision) + "\n", encoding="utf-8")
    (out / "decision.json").write_text(json.dumps({"decision": decision, "formal_weight": 0, "preregistration": prereg}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "closure_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "audited_files.json").write_text(json.dumps(audited_files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "data_manifest.json").write_text(json.dumps(feature_audit["data_file_manifest"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({
        "checkout_head": head, "formal_weight": 0, "provider_network_used": False,
        "external_request_attempts": 0, "api_football_key_accessed": False,
        "model_training": 0, "decision": decision, "audit_sha256": audit["audit_sha256"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"head": head, "experiments": len(ledger), "fields": feature_audit["field_count"], "new_features": len(feature_audit["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]), "decision": decision}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
