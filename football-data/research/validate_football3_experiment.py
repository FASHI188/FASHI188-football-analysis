from __future__ import annotations

import argparse
import ast
import json
import math
from pathlib import Path
from typing import Mapping

from football3_core import (
    DEFAULT_MIN_DOMAIN_WIN_FRACTION,
    DEFAULT_MIN_FOLD_WIN_FRACTION,
    DELTA_DEFINITION,
    Football3ContractError,
    MASTER_PREDICTION_CUTOFF,
    POWER_PLAN_SCHEMA,
    validate_confirmation_power_plan,
)

ROOT_SHA = "e3e73c998020beef585cc459a69ea5b73b44ddb3"
REQUIRED_METRICS = {"LogLoss", "Brier", "RPS"}
REQUIRED_CALIBRATION = {"Top1ECE", "ClasswiseECE"}
REQUIRED_EVALUATOR_KWARGS = {"identity_sha256", "fold_ids", "domain_ids", "scored_dates_utc", "cluster_ids", "temporal_manifest", "contract"}
REQUIRED_RUNNER_CALLS = {"evaluate_frozen_experiment", "load_labels_with_frozen_manifest", "validate_sealed_run_receipts"}
FORBIDDEN_DIRECT_IO_CALLS = {
    "open", "read", "read_bytes", "read_text", "readline", "readlines",
    "read_csv", "read_json", "read_parquet", "read_excel", "load", "loads",
    "ZipFile", "extract", "extractall", "urlopen", "request", "get", "post",
}


class PreflightError(RuntimeError):
    pass


def fail(msg: str) -> None:
    raise PreflightError(msg)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{name} must be numeric")
    v = float(value)
    if not math.isfinite(v):
        fail(f"{name} must be finite")
    return v


def _strict_fraction(value: object, name: str, floor: float) -> float:
    v = _finite_number(value, name)
    if not (floor <= v <= 1.0):
        fail(f"{name} must be in [{floor},1]")
    return v


def validate_contract(c: Mapping[str, object]) -> None:
    if c.get("schema_version") != 3:
        fail("football3 remediation requires schema_version=3")
    if c.get("project_id") != "football3":
        fail("project_id must be football3")
    root = c.get("scientific_root", {})
    if not isinstance(root, Mapping) or root.get("experiment") != "C072-C" or root.get("sha") != ROOT_SHA:
        fail("scientific root mismatch")
    locked = c.get("scientific_result_lock", {})
    if locked.get("C072_N20") != "PILOT_NO_SIGNAL_PARK":
        fail("C072-N20 historical verdict must remain PILOT_NO_SIGNAL_PARK")
    if locked.get("formal_weight") != 0:
        fail("formal_weight must remain zero")

    cutoff = c.get("prediction_cutoff", {})
    if not isinstance(cutoff, Mapping):
        fail("prediction_cutoff missing")
    norm = lambda x: "".join(str(x).casefold().split())
    if any(norm(cutoff.get(k)) != norm(MASTER_PREDICTION_CUTOFF) for k in ("master", "baseline", "candidate")):
        fail("master/baseline/candidate cutoff must all be T-15m")
    if cutoff.get("pit_bound_to_real_source_timestamp") is not True:
        fail("PIT must bind real source timestamps")

    q = c.get("scientific_question", {})
    if not isinstance(q, Mapping) or q.get("primary_target") != "P(T=0,1,2,3,4,5,6,7+)" or q.get("direct_draw_optimization") is not False:
        fail("football3 primary target/direct-draw rules violated")
    if c.get("method_shopping", {}).get("same_viewed_oos_rescue_allowed") is not False:
        fail("same-viewed-OOS rescue must be forbidden")

    data = c.get("data_plan", {})
    if not isinstance(data, Mapping):
        fail("data_plan missing")
    if data.get("identity_kind") != "global_match_identity":
        fail("scientific identity must be source-independent global_match_identity")
    if data.get("identity_lock_before_labels") is not True or data.get("target_decode_after_identity_guard") is not True:
        fail("identity must be locked and checked before target decode")
    if not isinstance(data.get("identity_count"), int) or isinstance(data.get("identity_count"), bool) or int(data.get("identity_count")) <= 0:
        fail("identity_count must be positive integer")
    if not isinstance(data.get("ordered_identity_sha256"), str) or len(str(data.get("ordered_identity_sha256"))) != 64:
        fail("ordered global identity digest required")
    if data.get("global_consumption_fail_closed") is not True:
        fail("global consumption audit must fail closed")
    if data.get("fresh_on_unresolved_identity") is not False:
        fail("UNRESOLVED identity may not be fresh/confirmation")

    metrics = c.get("metrics", {})
    if not REQUIRED_METRICS.issubset(set(metrics.get("proper_scores", []))):
        fail("LogLoss/Brier/RPS are required")
    if metrics.get("top1_primary") is not False:
        fail("Top1 cannot be primary")
    cal = metrics.get("calibration", {})
    if cal.get("required") is not True or not REQUIRED_CALIBRATION.issubset(set(cal.get("metrics", []))):
        fail("Top1ECE/ClasswiseECE required")

    eq = c.get("candidate_equivalence", {})
    for name in ("max_abs_floor", "mean_abs_floor"):
        v = _finite_number(eq.get(name), f"candidate_equivalence.{name}")
        if v <= 0:
            fail(f"candidate_equivalence.{name} must be >0")
    if eq.get("fail_closed") is not True:
        fail("candidate equivalence must fail closed")

    gates = c.get("success_gates", {})
    primary = gates.get("primary", {})
    if primary.get("metric") != "LogLoss" or primary.get("strict_improvement") is not True:
        fail("LogLoss strict improvement is required")
    if primary.get("iid_bootstrap_ci_high_strictly_below_zero") is not True:
        fail("iid LogLoss CI high must be strictly below zero")
    if primary.get("dependency_bootstrap_ci_high_strictly_below_zero") is not True:
        fail("dependency LogLoss CI high must be strictly below zero")
    temporal = gates.get("temporal_consistency", {})
    _strict_fraction(temporal.get("minimum_fold_win_fraction"), "minimum_fold_win_fraction", DEFAULT_MIN_FOLD_WIN_FRACTION)
    domain = gates.get("domain_consistency", {})
    _strict_fraction(domain.get("minimum_win_fraction"), "minimum_domain_win_fraction", DEFAULT_MIN_DOMAIN_WIN_FRACTION)

    dep = c.get("dependency_bootstrap", {})
    if dep.get("method") not in {"competition_season_cluster", "time_block"}:
        fail("dependency bootstrap method must be frozen")
    if not isinstance(dep.get("minimum_clusters"), int) or isinstance(dep.get("minimum_clusters"), bool) or dep.get("minimum_clusters") < 8:
        fail("dependency bootstrap minimum_clusters must be >=8")
    if not isinstance(dep.get("resamples"), int) or isinstance(dep.get("resamples"), bool) or dep.get("resamples") < 1000:
        fail("dependency bootstrap resamples must be >=1000")

    oos = c.get("oos_design", {})
    if oos.get("temporal") is not True or oos.get("shuffle") is not False:
        fail("OOS must be temporal and unshuffled")
    if not isinstance(oos.get("temporal_manifest_sha256"), str) or len(str(oos.get("temporal_manifest_sha256"))) != 64:
        fail("immutable temporal manifest SHA required")
    if oos.get("evaluator_binds_identity_fold_date") is not True:
        fail("evaluator must bind identity/fold/date")

    sealed = c.get("sealed", {})
    if sealed.get("reader_required") is not True or sealed.get("self_report_counts_forbidden") is not True:
        fail("sealed access must use attested reader receipts")
    pools = sealed.get("pools")
    if not isinstance(pools, list):
        fail("sealed pools list required")
    for pool in pools:
        if not isinstance(pool, Mapping) or not pool.get("pool_id") or not pool.get("manifest_sha256"):
            fail("sealed pool manifest binding missing")
        if pool.get("access_authorized") is not False:
            fail("this remediation contract may not authorize sealed access")

    confirmation = c.get("confirmation", {})
    if confirmation.get("delta_definition") != DELTA_DEFINITION or confirmation.get("required_direction") != "negative":
        fail("confirmation delta direction must be signed candidate-baseline and negative")
    min_n = confirmation.get("minimum_n")
    if not isinstance(min_n, int) or isinstance(min_n, bool) or min_n < 500:
        fail("minimum confirmation n must be >=500")
    if confirmation.get("cluster_aware_design_effect") is not True:
        fail("confirmation planning must be cluster aware")

    runtime = c.get("runtime_guards", {})
    for key in ("no_real_target_access", "no_training", "no_scientific_scoring", "no_provider_requests", "no_secret_access"):
        if runtime.get(key) is not True:
            fail(f"runtime guard missing: {key}")


def validate_power_plan_against_raw(plan: Mapping[str, object], per_match_delta, cluster_ids) -> None:
    try:
        validate_confirmation_power_plan(plan, per_match_delta, cluster_ids)
    except Football3ContractError as exc:
        fail(str(exc))
    if plan.get("schema") != POWER_PLAN_SCHEMA:
        fail("power plan schema mismatch")


def _dotted_name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def validate_runner(path: str | Path) -> None:
    """Static auxiliary guard. Runtime identity/time/label/sealed proofs remain mandatory."""
    p = Path(path)
    tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    call_names = {_dotted_name(n.func).split(".")[-1] for n in calls}
    missing_calls = REQUIRED_RUNNER_CALLS - call_names
    if missing_calls:
        fail(f"runner missing canonical production calls: {sorted(missing_calls)}")

    evaluator_calls = [n for n in calls if _dotted_name(n.func).split(".")[-1] == "evaluate_frozen_experiment"]
    for call in evaluator_calls:
        kwargs = {k.arg for k in call.keywords if k.arg}
        missing = REQUIRED_EVALUATOR_KWARGS - kwargs
        if missing:
            fail(f"canonical evaluator missing runtime bindings: {sorted(missing)}")

    label_calls = [n for n in calls if _dotted_name(n.func).split(".")[-1] == "load_labels_with_frozen_manifest"]
    for call in label_calls:
        kwargs = {k.arg for k in call.keywords if k.arg}
        required = {"expected_manifest_sha256", "keys", "target_columns", "expected_rows"}
        if required - kwargs:
            fail(f"label loader missing immutable pre-target bindings: {sorted(required-kwargs)}")

    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "football3_core":
            for alias in n.names:
                if alias.name.startswith("_") or alias.name == "SealedAccessReceipt":
                    fail("runner may not import private receipt attestation or construct receipts")
        if isinstance(n, ast.Call):
            name = _dotted_name(n.func).split(".")[-1]
            if name in FORBIDDEN_DIRECT_IO_CALLS:
                fail(f"direct file/network IO call forbidden in football3 scientific runner: {name}")


def load_contract(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_contract(payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract")
    ap.add_argument("--runner")
    args = ap.parse_args()
    if args.contract:
        load_contract(args.contract)
    if args.runner:
        validate_runner(args.runner)
    print("FOOTBALL3_REMEDIATION_PREFLIGHT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
