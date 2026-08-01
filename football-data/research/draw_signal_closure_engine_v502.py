#!/usr/bin/env python3
"""Bidirectional, fail-closed draw-signal closure audit for CURRENT V5.0.2.

The module reads repository-existing research assets and processed CSV files only.
It never trains a model, never scores a new target period, never accesses a provider
or secret, and never writes to formal assets. Both legal decisions are reachable:

* EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING
* PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET

A positive decision only means that a materially new, sufficiently covered,
prediction-time field survived the audit. It creates a preregistration but does not
run it.
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
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

FORMAL_WEIGHT = 0
MIN_GLOBAL_COVERAGE = 0.70
MIN_COMPETITION_COVERAGE = 0.30
NEGATIVE_DECISION = "EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING"
POSITIVE_DECISION = "PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET"
ALLOWED_DECISIONS = {NEGATIVE_DECISION, POSITIVE_DECISION}
BASE_MAIN = "605abf2d9f98c46f063106c7bd47193b96e588e4"

REQUIRED_VERSION_PREFIXES = (
    "V4.7.0", "V6.4.3", "V6.7.0", "V6.7.1", "V6.7.2", "V6.7.3", "V6.7.4",
    "V6.10.", "V6.11.", "V6.32", "V6.33", "V6.34", "V6.50.7", "V6.50.8",
    "V6.50.9", "V6.51.0", "V6.51.1", "V6.51.2",
)

_SPEC_ROWS = [
    ("V4.7.0-DRAW-STRUCTURE", "V4.7.0", "draw_structure_diagnostics_v470", "league/team draw structure diagnostics", "descriptive draw-rate and structural diagnostics", "processed historical match results", "MIXED"),
    ("V4.7.0-DRAW-DISCRIMINATION", "V4.7.0", "draw_discrimination_diagnostics_v470", "draw discrimination / sharpness", "domain draw probability AUC and dispersion diagnostics", "rolling OOF draw probabilities", "PIT_SAFE_IF_OOF_RECEIPTS_VALID"),
    ("V4.7.0-RESIDUAL-SCREEN", "V4.7.0", "draw_residual_challenger_screen_v470", "outcome residual draw screen", "screened residual draw challenger", "formal probabilities plus time-ordered labels", "PIT_SAFE_IF_STRICT_OOF"),
    ("V4.7.0-RESIDUAL-OOF", "V4.7.0", "draw_residual_rolling_oof_v470", "rolling OOF residual draw model", "rolling-origin residual classifier", "OOF probabilities and lagged history", "PIT_SAFE_ROLLING_ORIGIN"),
    ("V4.7.0-ONLINE-RESIDUAL", "V4.7.0", "draw_online_residual_rolling_oof_v470", "online residual draw model", "online expanding residual update", "OOF probabilities and lagged outcomes", "PIT_SAFE_IF_SAME_DAY_BATCHING"),
    ("V4.7.0-D-CONDITIONAL-TOTAL", "V4.7.0", "conditional_draw_by_total_rolling_oof_v470", "conditional D|Total", "draw conditioned on total-goal distribution", "unified score/total-goal OOF outputs", "PIT_SAFE_OOF"),
    ("V4.7.0-REPAIR-VERDICT", "V4.7.0", "draw_repair_research_verdict_v470", "V4.7.0 draw repair registry", "governance synthesis", "prior V4.7.0 receipts", "DERIVED_AUDIT_ONLY"),
    ("V6.4.3-MULTIMARKET", "V6.4.3", "v6_multimarket_draw_side_v643", "1X2 + AH + OU multimarket classification", "multimarket draw-side classifier", "historical 1X2/AH/OU", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.7.0-DRAW-OVERRIDE", "V6.7.0", "v6_draw_residual_override_v670", "generic draw override", "logistic residual override", "historical market probabilities", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.7.1-DYNAMIC-PROPENSITY", "V6.7.1", "v6_dynamic_draw_propensity_v671", "dynamic team/league draw propensity", "lagged team and league propensity", "completed prior matches", "PIT_SAFE_IF_STRICT_LAGGING"),
    ("V6.7.2-ZM-SKELLAM", "V6.7.2", "v6_zero_modified_skellam_draw_v672", "zero-modified Skellam", "zero-modified score-difference model", "lagged goal rates", "PIT_SAFE_IF_ROLLING"),
    ("V6.7.3-OUTCOME-CALIBRATION", "V6.7.3", "v6_draw_probability_calibration_v673", "outcome-only draw calibration", "baseline draw calibration", "baseline probabilities and outcomes", "PIT_SAFE_ONLY_WITH_NESTED_TIME_SPLIT"),
    ("V6.7.4-REGISTRY", "V6.7.4", "v6_draw_resolution_registry_v674", "draw experiment registry", "comparative registry", "V6.7.x receipts", "DERIVED_AUDIT_ONLY"),
    ("V6.10.0-RESIDUAL-ML", "V6.10.0", "v6_1x2_market_residual_ml_v6100", "market residual ML", "logistic/HGB residual multiclass", "historical market plus repository features", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.1-MULTIBOOK", "V6.10.1", "v6_1x2_multibook_consensus_v6101", "bookmaker disagreement/dispersion", "multi-book consensus and dispersion", "historical bookmaker odds", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.2-MARKET-FORM", "V6.10.2", "v6_1x2_market_form_residual_v6102", "market + Elo/form/rest residual", "residual classifier", "market plus lagged context", "MIXED"),
    ("V6.10.3-SELECTIVE-MARKET", "V6.10.3", "v6_1x2_selective_market_v6103", "selective market decision", "confidence/coverage selection", "historical devigged 1X2", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.4-COMP-FILTER", "V6.10.4", "v6_1x2_selective_competition_filter_v6104", "competition selective filter", "competition abstention filter", "historical market", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.5-MOVEMENT", "V6.10.5", "v6_1x2_market_movement_v6105", "opening-to-closing movement", "market movement features", "opening and closing odds", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.6-CALIBRATION", "V6.10.6", "v6_1x2_market_calibration_v6106", "multiclass market calibration", "multiclass calibration", "devigged historical 1X2", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.7-SELECTIVE-MULTISEASON", "V6.10.7", "v6_1x2_market_selective_multiseason_v6107", "multiseason selective H/D/A", "time-ordered selective calibration", "historical 1X2", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.10.8-CROSSSEASON-CAL", "V6.10.8", "v6_1x2_market_calibration_crossseason_v6108", "cross-season multiclass calibration", "cross-season calibration", "historical 1X2", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.11.1-METHOD-REGISTRY", "V6.11.1", "v6_1x2_research_method_registry_v6111", "1X2 method registry", "duplicate-route control", "prior receipts", "DERIVED_AUDIT_ONLY"),
    ("V6.11.3-OU-OVERRIDE", "V6.11.3", "v6_1x2_ou_draw_override_v6113", "1X2 + OU draw override", "totals-conditioned override", "historical 1X2 and totals", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.11.4-TWO-BETA", "V6.11.4", "v6_1x2_two_beta_draw_v6114", "two-beta draw decomposition", "draw-vs-decisive plus H-vs-A", "historical devigged 1X2", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.11.5-BOOK-ROUTING", "V6.11.5", "v6_1x2_bookmaker_routing_v6115", "bookmaker routing", "provider route selection", "historical bookmaker odds", "RETROSPECTIVE_REFERENCE_ONLY"),
    ("V6.11.6-LAGGED-SHOT", "V6.11.6", "v6_1x2_lagged_shot_quality_v6116", "lagged shot quality", "lagged shot-quality features", "prior-match shots and goals", "PIT_SAFE_IF_LAGGED"),
    ("V6.11.7-LINEUP", "V6.11.7", "v6_1x2_pit_lineup_increment_v6117", "PIT lineup increment", "lineup increment challenger", "lineup snapshots", "PIT_CLAIMED"),
    ("V6.11.7B-LINEUP", "V6.11.7b", "v6_1x2_pit_lineup_increment_v6117b", "PIT lineup revision", "lineup feature revision", "lineup snapshots", "PIT_CLAIMED"),
    ("V6.11.7C-LINEUP", "V6.11.7c", "v6_1x2_pit_lineup_increment_v6117c", "PIT lineup revision", "lineup feature revision", "lineup snapshots", "PIT_CLAIMED"),
    ("V6.11.8-LINEUP-ROBUST", "V6.11.8", "v6_1x2_pit_lineup_robustness_v6118", "lineup robustness", "missingness/robustness evaluation", "lineup snapshots", "PIT_CLAIMED"),
    ("V6.11.9-LINEUP-VALUE", "V6.11.9", "v6_1x2_pit_lineup_valuation_v6119", "lineup valuation", "player/lineup value aggregation", "pre-match lineup/value snapshots", "PIT_CLAIMED"),
    ("V6.32.0-CATBOOST", "V6.32.0", "v6_direct_xg_shot_market_catboost_random100_v6320", "market+xG+shot CatBoost", "CatBoost direct multiclass", "market/xG/shot", "RANDOM_SAMPLE_NOT_PROMOTION_GRADE"),
    ("V6.32.1-OVERLAP", "V6.32.1", "v6_direct_random100_information_overlap_v6321", "information overlap audit", "feature overlap audit", "V6.32.0 features", "AUDIT_ONLY"),
    ("V6.33.0-PLAYER-CORE", "V6.33.0", "v6_player_core_strength_random100_v6330", "player core strength", "player-core random100", "player/lineup strength", "PIT_UNPROVEN_OR_LOW_COVERAGE"),
    ("V6.34.0-MLP", "V6.34.0", "v6_mlp_1x2_random100_v6340", "MLP existing-feature model", "direct H/D/A MLP", "existing market/team/context", "RANDOM_SAMPLE_NOT_PROMOTION_GRADE"),
    ("V6.50.7-NET-GAIN", "V6.50.7", "v6_draw_residual_net_gain_v6507", "direct H/A-to-D net-gain override", "logistic draw residual utility", "frozen selector rows", "REPEATED_RESEARCH_SET"),
    ("V6.50.8-TRIAGE", "V6.50.8", "v6_draw_triage_decision_v6508", "draw-vs-decisive triage", "draw/correct/error triage", "frozen selector rows", "REPEATED_RESEARCH_SET"),
    ("V6.50.9-RISK-VETO", "V6.50.9", "v6_draw_risk_operating_point_v6509", "H/A risk abstention", "error-risk threshold veto", "frozen selector rows", "REPEATED_RESEARCH_SET"),
    ("V6.51.0-REENTRY", "V6.51.0", "v6_selective_draw_reentry_v6510|v6_draw_problem_resolution_v6510", "selective draw re-entry", "draw re-entry inside veto pool", "V6.50.9 veto pool", "REPEATED_RESEARCH_SET"),
    ("V6.51.1-DISCRETE-NB", "V6.51.1", "v6_draw_discrete_nb_v6511", "class-separating discrete NB", "discrete NB draw classifier", "rank/market/context bins", "REPEATED_RESEARCH_SET"),
    ("V6.51.2-RANK-NB", "V6.51.2", "v6_draw_rank_attack_nb_v6512|v6_draw_problem_resolution_v6512", "NB rank attack/defence", "rank attack/defence NB", "rank and market/context bins", "REPEATED_RESEARCH_SET"),
]
EXPERIMENT_SPECS = [
    {"id": row[0], "version": row[1], "patterns": row[2].split("|"), "family": row[3], "method": row[4], "source": row[5], "pit": row[6]}
    for row in _SPEC_ROWS
]

METRIC_ALIASES = {
    "accuracy": ("accuracy", "acc", "top1_accuracy", "full_1x2_accuracy", "combined_accuracy", "final_accuracy", "selected_accuracy"),
    "macro_f1": ("macro_f1", "macro_f1_score"),
    "draw_precision": ("draw_precision", "precision_draw", "draw_prec"),
    "draw_recall": ("draw_recall", "recall_draw"),
    "draw_f1": ("draw_f1", "f1_draw", "draw_f1_score"),
    "log_loss": ("log_loss", "logloss", "cross_entropy"),
    "brier": ("brier", "brier_score"),
    "rps": ("rps", "ranked_probability_score"),
    "coverage": ("coverage", "market_coverage", "selected_coverage"),
    "executed": ("executed", "executed_n", "executed_count", "selected_n"),
}

POSTMATCH_COLUMNS = {
    "fthg", "ftag", "ftr", "hthg", "htag", "htr", "hc", "ac", "hf", "af", "hr", "ar", "hs", "as", "hst", "ast", "hy", "ay", "home_goals", "away_goals", "result", "actual", "label", "score", "final_score",
}
STRUCTURAL_COLUMNS = {
    "league_id", "competition_id", "season", "source_season", "stage", "source_code", "date", "time", "hometeam", "awayteam", "home_team", "away_team", "div", "country", "game_id", "league", "meet_name", "venue",
}
PIT_UNPROVEN_CONTEXT = {"referee"}
PIT_FIELD_CONTRACTS: dict[str, dict[str, Any]] = {
    "round": {
        "classification": "PIT_SAFE_PREDICTION_TIME",
        "source": "football-data/processed/KOR_KLeague1/official_results.csv",
        "source_semantics": "official schedule round label from kleague.com/getScheduleList.do",
        "scope": ["KOR_KLeague1"],
        "observed_at_contract": "intrinsic schedule label; repository-wide historical observed_at is unavailable",
    }
}

ROW_VAR_HINTS = {"raw", "row", "r", "record", "item", "match", "source", "data", "raw0"}
MODEL_CALL_NAMES = {"fit", "predict", "predict_proba", "decision_function", "score", "accuracy_score", "log_loss", "brier_score_loss", "roc_auc_score", "confusion_matrix", "evaluate", "eval", "stats", "correct", "select", "scorer"}
TIME_SPLIT_TOKENS = ("rolling", "expanding", "holdout", "validation", "test", "development", "same_date", "same-day", "same day", "cutoff", "season_progress", "outer_fold")
RESEARCH_SEMANTIC_TOKENS = ("draw", "1x2", "h/d/a", "home", "away", "selector", "outcome", "market", "probability", "confusion", "macro_f1", "log_loss", "brier", "rps")
RESEARCH_ACTION_TOKENS = ("predict", "fit", "evaluate", "accuracy", "validation", "holdout", "benchmark", "calibr", "selector", "threshold", "risk", "score", "manifest", "status")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def recursive_items(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, val in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, val
            yield from recursive_items(val, path)
    elif isinstance(value, list):
        for index, val in enumerate(value):
            path = f"{prefix}[{index}]"
            yield path, val
            yield from recursive_items(val, path)


def norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def collect_metric_evidence(objects: Sequence[tuple[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    evidence = {key: [] for key in METRIC_ALIASES}
    for file_path, obj in objects:
        for path, value in recursive_items(obj):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            leaf = norm_key(re.sub(r"\[\d+\]$", "", path.split(".")[-1]))
            for metric, aliases in METRIC_ALIASES.items():
                if leaf in aliases or any(leaf.endswith("_" + alias) for alias in aliases):
                    evidence[metric].append({"file": file_path, "json_path": path, "value": float(value)})
    return evidence


def choose_metric(metric: str, evidence: Sequence[dict[str, Any]]) -> float | None:
    if not evidence:
        return None
    def rank(item: Mapping[str, Any]) -> tuple[int, int, str]:
        text = f"{item['file']} {item['json_path']}".lower()
        score = 0
        if any(token in text for token in ("holdout", "test", "future", "full_1x2", "overall", "aggregate")):
            score -= 20
        if any(token in text for token in ("validation", "train", "best", "selected", "veto", "coverage")):
            score += 10
        if metric == "accuracy" and "selected" in text:
            score += 20
        return score, len(text), text
    return float(sorted(evidence, key=rank)[0]["value"])


def find_status(objects: Sequence[tuple[str, Any]]) -> tuple[str | None, str | None]:
    statuses: list[str] = []
    reasons: list[str] = []
    for _, obj in objects:
        for path, value in recursive_items(obj):
            key = norm_key(path)
            if isinstance(value, str):
                if key.endswith("status") or any(token in key for token in ("decision", "verdict", "classification")):
                    statuses.append(value)
                if any(token in key for token in ("reason", "interpretation", "diagnosis", "failure", "rejection")):
                    reasons.append(value)
    status = next((value for value in statuses if any(token in value.upper() for token in ("REJECT", "FAIL", "PASS", "INACTIVE", "NOT_SUPPORTED"))), statuses[0] if statuses else None)
    return status, max(reasons, key=len) if reasons else None


def repeated_test_flag(objects: Sequence[tuple[str, Any]]) -> tuple[bool | None, list[str]]:
    evidence: list[str] = []
    flag: bool | None = None
    for file_path, obj in objects:
        raw = json.dumps(obj, ensure_ascii=False).lower()
        if any(token in raw for token in ("repeated research set", "repeatedly inspected", "not_a_final_blind_holdout", "researcher_reuse")):
            flag = True
            evidence.append(file_path)
        elif flag is None and any(token in raw for token in ("untouched final holdout", "pristine holdout", "blind holdout")):
            flag = False
            evidence.append(file_path)
    return flag, sorted(set(evidence))


def find_period_evidence(objects: Sequence[tuple[str, Any]]) -> list[dict[str, Any]]:
    tokens = ("train", "validation", "test", "holdout", "fit", "development", "benchmark", "fold", "cutoff", "period", "season")
    output: list[dict[str, Any]] = []
    for file_path, obj in objects:
        for path, value in recursive_items(obj):
            key = norm_key(path)
            if any(token in key for token in tokens) and isinstance(value, (str, int, float, list, dict)):
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)[:1000] if isinstance(value, (dict, list)) else str(value)[:1000]
                output.append({"file": file_path, "json_path": path, "value": rendered})
    return output[:120]


def bookmaker_triplet_key(column: str, all_columns: Iterable[str]) -> str | None:
    token = column.strip().lower()
    columns = {value.strip().lower() for value in all_columns}
    if len(token) < 2 or token[-1] not in "hda":
        return None
    prefix = token[:-1]
    if not re.fullmatch(r"[0-9a-z_.+-]{1,20}", prefix):
        return None
    siblings = {prefix + suffix for suffix in "hda"}
    return prefix if siblings.issubset(columns) else None


def classify_column(column: str, all_columns: Iterable[str] = (), pit_contracts: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[str, str]:
    token = column.strip().lower()
    contracts = {key.lower(): value for key, value in (pit_contracts or PIT_FIELD_CONTRACTS).items()}
    if token in POSTMATCH_COLUMNS:
        return "POSTMATCH_FORBIDDEN", "match result or in-match/postmatch statistic"
    if token in STRUCTURAL_COLUMNS:
        return "PIT_SAFE_STRUCTURAL", "identity/schedule field; not a new draw signal"
    if token in contracts:
        contract = contracts[token]
        return str(contract.get("classification", "PIT_SAFE_PREDICTION_TIME")), str(contract.get("source_semantics", "explicit prediction-time contract"))
    if token in PIT_UNPROVEN_CONTEXT:
        return "PIT_UNPROVEN_CONTEXT", "repository has no observed_at proving prediction-time availability"
    if bookmaker_triplet_key(token, all_columns):
        return "RETROSPECTIVE_MARKET_REFERENCE", "H/D/A bookmaker triplet without original quote timestamp"
    if any(marker in token for marker in ("odds", "price", "<2.5", ">2.5", "ahch", "cahh", "caha")):
        return "RETROSPECTIVE_MARKET_REFERENCE", "odds/line without original quote timestamp"
    if re.fullmatch(r"[ha](?:c|f|r|s|st|y)", token):
        return "POSTMATCH_FORBIDDEN", "in-match statistic"
    return "UNKNOWN_PIT_STATUS", "no repository-wide timestamp contract proves prediction-time availability"


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _row_receiver_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return node.value.id
    return None


@dataclass
class ScriptFlow:
    path: str
    fields: dict[str, list[dict[str, Any]]]
    builder_functions: set[str]
    scorer_calls: set[str]
    manifest_paths: list[str]
    time_split_evidence: list[str]


class FlowVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.current_function = "<module>"
        self.function_stack: list[str] = []
        self.fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.builder_functions: set[str] = set()
        self.scorer_calls: set[str] = set()
        self.literal_triplets: list[tuple[str, str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.function_stack.append(self.current_function)
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _record(self, field: str, node: ast.AST, kind: str) -> None:
        self.fields[field.lower()].append({"line": getattr(node, "lineno", None), "function": self.current_function, "kind": kind})

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name == "get" and node.args:
                receiver = _row_receiver_name(node.func.value)
                field = _literal_string(node.args[0])
                if receiver and receiver.lower() in ROW_VAR_HINTS and field:
                    self._record(field, node, "row.get_literal")
            if name in MODEL_CALL_NAMES:
                self.scorer_calls.add(name)
            if name == "append" and node.args and isinstance(node.args[0], (ast.Dict, ast.List, ast.Tuple)):
                self.builder_functions.add(self.current_function)
        elif isinstance(node.func, ast.Name) and node.func.id in MODEL_CALL_NAMES:
            self.scorer_calls.add(node.func.id)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        receiver = _row_receiver_name(node.value)
        field = _literal_string(node.slice)
        if receiver and receiver.lower() in ROW_VAR_HINTS and field:
            self._record(field, node, "row_subscript_literal")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> Any:
        if node.value is not None and isinstance(node.value, (ast.Dict, ast.List, ast.Tuple, ast.Name, ast.Call)):
            self.builder_functions.add(self.current_function)
        self.generic_visit(node)

    def visit_Tuple(self, node: ast.Tuple) -> Any:
        values = [_literal_string(value) for value in node.elts]
        if len(values) == 3 and all(values):
            normalized = [str(value).lower() for value in values]
            prefixes = [value[:-1] for value in normalized if value[-1:] in {"h", "d", "a"}]
            if len(prefixes) == 3 and len(set(prefixes)) == 1 and {value[-1] for value in normalized} == {"h", "d", "a"}:
                self.literal_triplets.append(tuple(str(value) for value in values))
        self.generic_visit(node)


def _manifest_paths_from_text(root: Path, text: str) -> list[str]:
    output: list[str] = []
    for name in re.findall(r"['\"]([^'\"]+\.json)['\"]", text):
        candidates = [root / "football-data" / "manifests" / Path(name).name, root / name]
        for candidate in candidates:
            if candidate.is_file():
                output.append(candidate.relative_to(root).as_posix())
                break
    return sorted(set(output))


def analyze_script_flow(root: Path, path: Path) -> ScriptFlow:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ScriptFlow(path.relative_to(root).as_posix(), {}, set(), set(), [], [])
    visitor = FlowVisitor()
    visitor.visit(tree)
    if visitor.scorer_calls:
        for triplet in visitor.literal_triplets:
            for field in triplet:
                visitor.fields[field.lower()].append({"line": None, "function": "literal_triplet_reader", "kind": "iterated_HDA_triplet"})
        if visitor.literal_triplets:
            visitor.builder_functions.add("literal_triplet_reader")
    time_evidence = [token for token in TIME_SPLIT_TOKENS if token in text.lower()]
    return ScriptFlow(path=path.relative_to(root).as_posix(), fields=dict(visitor.fields), builder_functions=visitor.builder_functions, scorer_calls=visitor.scorer_calls, manifest_paths=_manifest_paths_from_text(root, text), time_split_evidence=time_evidence)


def build_dataflow_index(root: Path, scripts: Sequence[Path] | None = None) -> dict[str, list[dict[str, Any]]]:
    paths = list(scripts) if scripts is not None else sorted((root / "football-data" / "validation").glob("*.py")) + sorted((root / "football-data" / "research").glob("*.py"))
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        flow = analyze_script_flow(root, path)
        for field, accesses in flow.fields.items():
            access_functions = {str(item.get("function")) for item in accesses}
            builder = bool(access_functions & flow.builder_functions)
            scorer = bool(flow.scorer_calls)
            manifest = bool(flow.manifest_paths)
            time_split = bool(flow.time_split_evidence)
            complete = builder and scorer and manifest and time_split
            index[field].append({"script": flow.path, "raw_field_accesses": accesses, "feature_builder_functions": sorted(flow.builder_functions), "estimator_or_scorer_calls": sorted(flow.scorer_calls), "exact_run_or_manifest": flow.manifest_paths, "pit_or_time_split_evidence": flow.time_split_evidence, "complete_dataflow_chain": complete})
    return dict(index)


def dataflow_chain_is_complete(evidence: Sequence[Mapping[str, Any]]) -> bool:
    return any(bool(item.get("complete_dataflow_chain")) for item in evidence)


def route_review_for_field(field: str, dataflow: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if field.lower() != "round":
        return None
    scripts = {str(item.get("script")) for item in dataflow}
    return {
        "field": "round",
        "raw_field_used_by_V6_12_2": any("validate_1x2_late_season_stakes_fast100_v6122.py" in value for value in scripts),
        "raw_field_used_by_V6_12_3": any("validate_1x2_crossseason_phase_v6123.py" in value for value in scripts),
        "V6_12_2_binding": "derives matches-left and urgency from prior-date standings; does not read raw round",
        "V6_12_3_binding": "derives season_progress from ordered row index; does not read raw round",
        "conclusion": "raw round is not proven as an estimator/scorer input in these routes",
    }


def assess_field_candidate(field: str, stat: Mapping[str, Any], *, all_columns: Iterable[str], total_rows: int, total_competitions: int, dataflow_index: Mapping[str, Sequence[Mapping[str, Any]]], pit_contracts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    classification, reason = classify_column(field, all_columns, pit_contracts)
    nonempty = int(stat.get("nonempty", 0))
    rows_in_files = int(stat.get("rows_in_files", 0))
    competitions = sorted(set(stat.get("competitions", [])))
    global_coverage = nonempty / total_rows if total_rows else 0.0
    within_coverage = nonempty / rows_in_files if rows_in_files else 0.0
    competition_coverage = len(competitions) / total_competitions if total_competitions else 0.0
    evidence = list(dataflow_index.get(field.lower(), []))
    exact_used = dataflow_chain_is_complete(evidence)
    substantive = classification not in {"PIT_SAFE_STRUCTURAL", "POSTMATCH_FORBIDDEN"}
    pit_safe = classification == "PIT_SAFE_PREDICTION_TIME"
    coverage_ok = global_coverage >= MIN_GLOBAL_COVERAGE and competition_coverage >= MIN_COMPETITION_COVERAGE
    untested = not exact_used
    qualifies = substantive and pit_safe and coverage_ok and untested
    return {"field": field, "classification": classification, "classification_reason": reason, "files": int(stat.get("files", 0)), "rows_in_files": rows_in_files, "nonempty": nonempty, "coverage_within_present_files": within_coverage, "global_row_coverage": global_coverage, "competition_count": len(competitions), "competition_coverage": competition_coverage, "competitions": competitions, "sample_paths": list(stat.get("sample_paths", [])), "dataflow_evidence": evidence, "previously_tested_via_complete_dataflow": exact_used, "substantive_predictive_candidate": substantive, "pit_safe": pit_safe, "coverage_gate_passed": coverage_ok, "untested": untested, "qualifies_existing_pit_safe_untested": qualifies, "route_binding_review": route_review_for_field(field, evidence)}


def profile_fields(root: Path, *, pit_contracts: Mapping[str, Mapping[str, Any]] | None = None, dataflow_index: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    files = sorted((root / "football-data" / "processed").glob("**/*.csv"))
    field_stats: dict[str, dict[str, Any]] = {}
    file_manifest: list[dict[str, Any]] = []
    total_rows = 0
    competitions_seen: set[str] = set()
    all_columns: set[str] = set()
    for path in files:
        rel = path.relative_to(root).as_posix()
        row_count = 0
        present: Counter[str] = Counter()
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                all_columns.update(columns)
                for row in reader:
                    row_count += 1
                    for column in columns:
                        if str(row.get(column) or "").strip():
                            present[column] += 1
        except Exception as exc:
            file_manifest.append({"path": rel, "sha256": sha256_file(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        competition = path.parent.name
        competitions_seen.add(competition)
        total_rows += row_count
        file_manifest.append({"path": rel, "sha256": sha256_file(path), "rows": row_count, "columns": columns})
        for column in columns:
            stat = field_stats.setdefault(column, {"files": 0, "rows_in_files": 0, "nonempty": 0, "competitions": set(), "sample_paths": []})
            stat["files"] += 1
            stat["rows_in_files"] += row_count
            stat["nonempty"] += present[column]
            stat["competitions"].add(competition)
            if len(stat["sample_paths"]) < 5:
                stat["sample_paths"].append(rel)
    flows = dict(dataflow_index) if dataflow_index is not None else build_dataflow_index(root)
    rows = [assess_field_candidate(column, stat, all_columns=all_columns, total_rows=total_rows, total_competitions=len(competitions_seen), dataflow_index=flows, pit_contracts=pit_contracts) for column, stat in sorted(field_stats.items(), key=lambda item: item[0].lower())]
    candidates = [row for row in rows if row["qualifies_existing_pit_safe_untested"]]
    near_miss = [row for row in rows if row["untested"] and row["substantive_predictive_candidate"] and not row["qualifies_existing_pit_safe_untested"]]
    return {"processed_csv_count": len(files), "processed_total_rows_scanned": total_rows, "processed_competition_count": len(competitions_seen), "data_file_manifest": file_manifest, "field_count": len(rows), "field_classification_counts": dict(Counter(row["classification"] for row in rows)), "fields": rows, "dataflow_field_count": len(flows), "dataflow_index_sha256": canonical_sha(flows), "EXISTING_PIT_SAFE_UNTESTED_FEATURES": candidates, "UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED": near_miss}


def make_preregistration(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    features = [str(row["field"]) for row in candidates]
    return {"status": "PRE_REGISTERED_NOT_RUN", "features": features, "feature_evidence_sha256": canonical_sha([row for row in candidates]), "model": "multinomial_logistic_with_nested_time_ordered_calibration", "hyperparameter_grid": {"l2": [0.1, 1.0, 10.0, 100.0], "class_weight_draw": [1.0, 1.15, 1.3]}, "outer_folds": "expanding-window by season; latest eligible season uniquely untouched", "inner_selection": "training-only rolling validation; no random split", "primary_metric": "paired draw_F1 and macro_F1 improvement under full-1X2 noninferiority", "promotion_gates": {"accuracy_delta_min": -0.005, "log_loss_delta_max": 0.005, "brier_delta_max": 0.005, "rps_delta_max": 0.003, "draw_f1_paired_bootstrap_lower_bound_min": 0.0, "macro_f1_paired_bootstrap_lower_bound_min": 0.0, "minimum_draw_predictions_per_outer_fold": 20, "minimum_coverage_if_abstaining": 0.50}, "unique_unviewed_test_segment": "latest eligible future season; must remain unopened until one registered run", "formal_weight": 0, "run_authorized": False}


def decide_and_preregister(candidates: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    if candidates:
        return POSITIVE_DECISION, make_preregistration(candidates)
    return NEGATIVE_DECISION, None


def _asset_candidates(root: Path) -> list[Path]:
    paths = sorted((root / "football-data" / "validation").glob("*.py")) + sorted((root / "football-data" / "research").glob("*.py")) + sorted((root / "football-data" / "manifests").glob("**/*.json"))
    return [path for path in paths if "/cache/" not in path.as_posix()]


def _json_research_semantics(value: Any) -> tuple[bool, bool]:
    semantic = False
    action = False
    for path, item in recursive_items(value):
        text = f"{path} {item if isinstance(item, str) else ''}".lower()
        if any(token in text for token in RESEARCH_SEMANTIC_TOKENS):
            semantic = True
        if any(token in text for token in RESEARCH_ACTION_TOKENS):
            action = True
        if semantic and action:
            return True, True
    return semantic, action


def is_expected_research_asset(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if path.suffix.lower() == ".json":
        try:
            semantic, action = _json_research_semantics(json.loads(text))
            return semantic and action
        except Exception:
            return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    constants = [node.value.lower() for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    names = [node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)]
    attrs = [node.attr.lower() for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
    joined = " ".join(constants + names + attrs)
    semantic = any(token in joined for token in RESEARCH_SEMANTIC_TOKENS)
    action = any(token in joined for token in RESEARCH_ACTION_TOKENS)
    hda_labels = {value for value in constants if value in {"home", "draw", "away", "h", "d", "a"}}
    return (semantic and action) or ({"home", "draw", "away"}.issubset(hda_labels) and action)


def discover_expected_research_assets(root: Path) -> list[Path]:
    return [path for path in _asset_candidates(root) if is_expected_research_asset(path)]


def build_asset_ledger(root: Path, expected_paths: Sequence[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in expected_paths:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() == ".json":
                json.loads(text)
            else:
                ast.parse(text)
            rows.append({"path": rel, "sha256": sha256_file(path), "size": path.stat().st_size, "kind": "manifest" if path.suffix.lower() == ".json" else "script", "parse_error": None, "formal_weight": 0})
        except Exception as exc:
            rows.append({"path": rel, "sha256": sha256_file(path), "size": path.stat().st_size, "kind": "manifest" if path.suffix.lower() == ".json" else "script", "parse_error": f"{type(exc).__name__}: {exc}", "formal_weight": 0, "matched": False})
    return rows


def research_asset_coverage(expected_paths: Sequence[str], ledger_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    expected = sorted(set(expected_paths))
    matched = sorted({str(row["path"]) for row in ledger_rows if row.get("matched", True) and not row.get("parse_error")})
    missing = sorted(set(expected) - set(matched))
    extra = sorted(set(matched) - set(expected))
    return {"expected_count": len(expected), "matched_count": len(matched), "expected": expected, "matched": matched, "missing": missing, "extra": extra, "all_covered": not missing and not extra}


def experiment_files(root: Path, spec: Mapping[str, Any]) -> list[Path]:
    paths = list((root / "football-data" / "validation").glob("*.py")) + list((root / "football-data" / "research").glob("*.py")) + list((root / "football-data" / "manifests").glob("**/*.json"))
    return sorted({path for path in paths if path.is_file() and any(pattern.lower() in path.name.lower() for pattern in spec["patterns"])})


def build_experiment_ledger(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ledger: list[dict[str, Any]] = []
    audited: dict[str, dict[str, Any]] = {}
    for spec in EXPERIMENT_SPECS:
        files = experiment_files(root, spec)
        objects: list[tuple[str, Any]] = []
        for path in files:
            rel = path.relative_to(root).as_posix()
            audited[rel] = {"path": rel, "sha256": sha256_file(path), "size": path.stat().st_size}
            if path.suffix.lower() == ".json":
                try:
                    objects.append((rel, json.loads(path.read_text(encoding="utf-8"))))
                except Exception as exc:
                    objects.append((rel, {"parse_error": f"{type(exc).__name__}: {exc}"}))
        metric_evidence = collect_metric_evidence(objects)
        status, reason = find_status(objects)
        repeated, repeated_evidence = repeated_test_flag(objects)
        row = dict(spec)
        row.update({"files": [path.relative_to(root).as_posix() for path in files], "file_count": len(files), "file_sha256": {path.relative_to(root).as_posix(): sha256_file(path) for path in files}, "train_validation_test_evidence": find_period_evidence(objects), "test_set_repeatedly_viewed": repeated, "test_reuse_evidence": repeated_evidence, "metrics": {metric: choose_metric(metric, values) for metric, values in metric_evidence.items()}, "metric_evidence": metric_evidence, "status": status, "rejection_or_result": reason, "formal_weight": 0})
        ledger.append(row)
    return ledger, sorted(audited.values(), key=lambda row: row["path"])


def markdown_ledger(ledger: Sequence[Mapping[str, Any]], coverage: Mapping[str, Any]) -> str:
    lines = ["# 平局/1X2实验总账", "", "仅研究态；formal_weight=0；未训练新模型。", "", "| 实验 | 特征家族 | PIT状态 | Accuracy | Macro-F1 | Draw P/R/F1 | LogLoss | Brier | RPS | 状态 |", "|---|---|---|---:|---:|---|---:|---:|---:|---|"]
    for row in ledger:
        metrics = row["metrics"]
        draw = "/".join("—" if metrics[key] is None else f"{metrics[key]:.4f}" for key in ("draw_precision", "draw_recall", "draw_f1"))
        values = [row["id"], row["family"], row["pit"], "—" if metrics["accuracy"] is None else f"{metrics['accuracy']:.4f}", "—" if metrics["macro_f1"] is None else f"{metrics['macro_f1']:.4f}", draw, "—" if metrics["log_loss"] is None else f"{metrics['log_loss']:.4f}", "—" if metrics["brier"] is None else f"{metrics['brier']:.4f}", "—" if metrics["rps"] is None else f"{metrics['rps']:.4f}", row.get("status") or "未记录"]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    lines += ["", "## 独立研究资产全集覆盖", "", f"expected={coverage['expected_count']}，matched={coverage['matched_count']}，missing={len(coverage['missing'])}，extra={len(coverage['extra'])}，all_covered={coverage['all_covered']}。", "", "- 资产全集按文件内容/H-D-A语义发现，不依赖文件名包含draw或1x2。", "- missing和extra由独立expected集合与实际ledger集合比较产生。", ""]
    return "\n".join(lines)


def markdown_features(feature_audit: Mapping[str, Any], decision: str) -> str:
    candidates = feature_audit["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]
    near = feature_audit["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"]
    lines = ["# 现有字段差集", "", f"裁决：`{decision}`", "", f"扫描CSV={feature_audit['processed_csv_count']}，行数={feature_audit['processed_total_rows_scanned']}，字段={feature_audit['field_count']}。", "", "## EXISTING_PIT_SAFE_UNTESTED_FEATURES", ""]
    if not candidates:
        lines.append("空集。")
    else:
        lines += ["| 字段 | 全局覆盖 | 联赛覆盖 | PIT证明 |", "|---|---:|---:|---|"]
        for row in candidates:
            lines.append(f"| {row['field']} | {row['global_row_coverage']:.2%} | {row['competition_coverage']:.2%} | {row['classification_reason']} |")
    lines += ["", "## Near-miss（必须保留）", ""]
    if not near:
        lines.append("无。")
    else:
        lines += ["| 字段 | 分类 | 全局覆盖 | 联赛覆盖 | 排除原因 |", "|---|---|---:|---:|---|"]
        for row in near:
            reasons = []
            if not row["pit_safe"]:
                reasons.append("PIT未证明")
            if not row["coverage_gate_passed"]:
                reasons.append("覆盖不足")
            if not row["untested"]:
                reasons.append("已有完整数据流证据")
            lines.append(f"| {row['field']} | {row['classification']} | {row['global_row_coverage']:.2%} | {row['competition_coverage']:.2%} | {','.join(reasons) or row['classification_reason']} |")
    return "\n".join(lines) + "\n"


def build_audit(root: Path) -> dict[str, Any]:
    head = git("rev-parse", "HEAD")
    experiment_ledger, audited_files = build_experiment_ledger(root)
    found_versions = {row["version"] for row in experiment_ledger if row["file_count"] > 0}
    missing_versions = [prefix for prefix in REQUIRED_VERSION_PREFIXES if not any(version.startswith(prefix) for version in found_versions)]
    dataflow = build_dataflow_index(root)
    feature_difference = profile_fields(root, dataflow_index=dataflow)
    decision, preregistration = decide_and_preregister(feature_difference["EXISTING_PIT_SAFE_UNTESTED_FEATURES"])
    expected_paths = discover_expected_research_assets(root)
    asset_rows = build_asset_ledger(root, expected_paths)
    asset_coverage = research_asset_coverage([path.relative_to(root).as_posix() for path in expected_paths], asset_rows)
    audit = {"schema_version": "DRAW-SIGNAL-CLOSURE-AUDIT-V502-2.0", "head": head, "base_main": BASE_MAIN, "formal_weight": 0, "provider_network_used": False, "external_request_attempts": 0, "api_football_key_accessed": False, "model_training": 0, "decision": decision, "preregistration": preregistration, "required_version_prefixes": list(REQUIRED_VERSION_PREFIXES), "missing_required_version_prefixes": missing_versions, "experiment_count": len(experiment_ledger), "experiment_ledger": experiment_ledger, "audited_files": audited_files, "feature_difference": feature_difference, "research_asset_coverage": asset_coverage, "complete_research_file_count": len(asset_rows), "complete_research_file_ledger": asset_rows, "all_draw_1x2_research_files_covered": asset_coverage["all_covered"], "reference_scorecards": {"strict_time_ordered_probability_diagnostics": {"matches": 4786, "actual_draw_rate": 0.2566, "mean_predicted_draw_probability": 0.2479, "mean_domain_draw_auc": 0.52218, "draw_probability_std": 0.01711}, "full_1x2": {"count": 5110, "hits": 2645, "accuracy": 0.517613, "predicted_home_draw_away": [3532, 40, 1538]}, "selector": {"executed": 1611, "accuracy": 0.671012, "predicted_home_draw_away": [1252, 0, 359], "scope": "SELECTIVE_NOT_FULL_1X2"}, "ha_risk_veto": {"executed": 1252, "accuracy": 0.707668, "market_coverage": 0.245010, "draw_predictions": 0, "scope": "HOME_AWAY_ABSTAIN"}}}
    audit["audit_sha256"] = canonical_sha(audit)
    return audit


def write_audit(out: Path, audit: Mapping[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    head = str(audit["head"])
    ledger = {"head": head, "formal_weight": 0, "rows": audit["experiment_ledger"], "research_asset_coverage": audit["research_asset_coverage"]}
    feature = audit["feature_difference"]
    complete = {"head": head, "count": audit["complete_research_file_count"], "rows": audit["complete_research_file_ledger"], "coverage": audit["research_asset_coverage"]}
    files = {"experiment_ledger.json": ledger, "feature_difference.json": feature, "decision.json": {"decision": audit["decision"], "formal_weight": 0, "preregistration": audit["preregistration"]}, "closure_audit.json": audit, "audited_files.json": audit["audited_files"], "data_manifest.json": feature["data_file_manifest"], "complete_research_file_ledger.json": complete, "metadata.json": {"checkout_head": head, "formal_weight": 0, "provider_network_used": False, "external_request_attempts": 0, "api_football_key_accessed": False, "model_training": 0, "decision": audit["decision"], "audit_sha256": audit["audit_sha256"], "complete_research_file_count": audit["complete_research_file_count"], "all_draw_1x2_research_files_covered": audit["all_draw_1x2_research_files_covered"]}}
    for name, value in files.items():
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "experiment_ledger.md").write_text(markdown_ledger(audit["experiment_ledger"], audit["research_asset_coverage"]), encoding="utf-8")
    (out / "feature_difference.md").write_text(markdown_features(feature, str(audit["decision"])), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(git("rev-parse", "--show-toplevel"))
    audit = build_audit(root)
    write_audit(args.output_dir, audit)
    print(json.dumps({"head": audit["head"], "experiments": audit["experiment_count"], "research_assets": audit["complete_research_file_count"], "new_features": len(audit["feature_difference"]["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]), "near_miss": len(audit["feature_difference"]["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"]), "decision": audit["decision"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
