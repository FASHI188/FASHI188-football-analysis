from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = "football3-v3-c3-score-matrix-prereg-contract-v1"
EXPECTED_BASE = "241cd942df2c32b92962a6a7e232381426a21d53"
EXPECTED_BRANCH = "football3/v3-c3-score-matrix-prereg-v1"
CONTRACT_REL = Path("governance/football3/v3_c3_score_matrix_prereg_contract_v1.json")

class PreregError(RuntimeError):
    pass


def _prob_map(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    if type(matrix) is not list or not matrix:
        raise PreregError("matrix must be a non-empty list")
    out: dict[tuple[int, int], float] = {}
    for cell in matrix:
        if type(cell) is not dict:
            raise PreregError("matrix cell must be object")
        try:
            h = int(cell["home_goals"])
            a = int(cell["away_goals"])
            p = float(cell["probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PreregError("invalid score cell") from exc
        if h < 0 or a < 0 or (h, a) in out or not math.isfinite(p) or p < 0:
            raise PreregError("invalid score matrix")
        out[(h, a)] = p
    total = math.fsum(out.values())
    if not math.isfinite(total) or total <= 0:
        raise PreregError("nonpositive matrix mass")
    return {k: v / total for k, v in out.items()}


def formal_v2_mix(v1_matrix: list[dict[str, Any]], xg_matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    v1 = _prob_map(v1_matrix)
    xg = _prob_map(xg_matrix)
    if set(v1) != set(xg):
        raise PreregError("component support mismatch")
    q = {k: 0.25 * v1[k] + 0.75 * xg[k] for k in v1}
    z = math.fsum(q.values())
    return {k: q[k] / z for k in q}


def total_marginals(matrix: dict[tuple[int, int], float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for (h, a), p in matrix.items():
        out[h + a] = out.get(h + a, 0.0) + p
    return out


def conditional_component_disagreement(
    v1_matrix: list[dict[str, Any]],
    xg_matrix: list[dict[str, Any]],
) -> dict[int, float | None]:
    v1 = _prob_map(v1_matrix)
    xg = _prob_map(xg_matrix)
    if set(v1) != set(xg):
        raise PreregError("component support mismatch")
    totals = sorted({h + a for h, a in v1})
    out: dict[int, float | None] = {}
    for t in totals:
        keys = [k for k in v1 if sum(k) == t]
        mv1 = math.fsum(v1[k] for k in keys)
        mxg = math.fsum(xg[k] for k in keys)
        if mv1 <= 0 or mxg <= 0:
            out[t] = None
            continue
        ev1 = math.fsum((h - a) * v1[(h, a)] for h, a in keys) / mv1
        exg = math.fsum((h - a) * xg[(h, a)] for h, a in keys) / mxg
        signal = exg - ev1
        out[t] = signal if math.isfinite(signal) else None
    return out


def conditional_tilt(
    v1_matrix: list[dict[str, Any]],
    xg_matrix: list[dict[str, Any]],
    beta: float,
) -> dict[tuple[int, int], float]:
    if not math.isfinite(beta):
        raise PreregError("beta must be finite")
    q = formal_v2_mix(v1_matrix, xg_matrix)
    if beta == 0.0:
        return copy.copy(q)
    signals = conditional_component_disagreement(v1_matrix, xg_matrix)
    pt = total_marginals(q)
    out: dict[tuple[int, int], float] = {}
    for t, mass in pt.items():
        keys = [k for k in q if sum(k) == t]
        signal = signals[t]
        if signal is None or len(keys) <= 1 or mass <= 0:
            for k in keys:
                out[k] = q[k]
            continue
        cond = {k: q[k] / mass for k in keys}
        logs = {k: beta * signal * (k[0] - k[1]) for k in keys}
        shift = max(logs.values())
        raw = {k: cond[k] * math.exp(logs[k] - shift) for k in keys}
        z = math.fsum(raw.values())
        if not math.isfinite(z) or z <= 0:
            for k in keys:
                out[k] = q[k]
            continue
        for k in keys:
            out[k] = mass * raw[k] / z
    if set(out) != set(q):
        raise PreregError("candidate support drift")
    for t, mass in pt.items():
        got = math.fsum(p for k, p in out.items() if sum(k) == t)
        if abs(got - mass) > 5e-12:
            raise PreregError("P(T) invariant violated")
    return out


def _git_blob_sha(repo_root: Path, rel: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", f"HEAD:{rel}"], text=True
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise PreregError(f"cannot resolve blob: {rel}") from exc


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != SCHEMA:
        raise PreregError("schema mismatch")
    if contract.get("project_id") != "football3" or contract.get("status") != "ZERO_LABEL_PREREG_FROZEN":
        raise PreregError("identity/status mismatch")
    if contract.get("exact_base") != EXPECTED_BASE or contract.get("branch") != EXPECTED_BRANCH:
        raise PreregError("base/branch mismatch")
    gate = contract["data_gate"]
    for key in ("target_labels_read_in_this_batch", "training_in_this_batch", "tuning_in_this_batch", "market_data_loading", "new_provider_loading"):
        if gate.get(key) is not False:
            raise PreregError(f"zero-label gate violated: {key}")
    fam = contract["candidate_family"]
    if fam.get("parameters") != ["beta"] or fam.get("parameter_count") != 1 or fam.get("candidate_grid") is not None:
        raise PreregError("candidate family is not single-parameter locked")
    if fam.get("draw_bonus") is not False or fam.get("score_bonus") is not False or fam.get("thresholds") != []:
        raise PreregError("bonus/threshold leakage")
    inactive = contract["inactive_until_scientific_pass"]
    if inactive != {"status": "NOT_AVAILABLE", "weight": 0, "matrix_delta": 0, "data_ready": False}:
        raise PreregError("inactive contract drift")
    if contract["development_and_confirmation"].get("known_consumed_groups_may_be_fresh_confirmation") is not False:
        raise PreregError("consumed identity reuse allowed")
    forbidden = contract["forbidden_changes"]
    if not all(forbidden.values()):
        raise PreregError("formal forbidden surface unlocked")


def validate_repo(repo_root: Path, contract: dict[str, Any]) -> None:
    pins = contract["formal_baseline"]
    for path_key, sha_key in (
        ("entry_path", "entry_blob_sha"),
        ("pointer_path", "pointer_blob_sha"),
        ("formal_test_path", "formal_test_blob_sha"),
        ("gateway_path", "gateway_blob_sha"),
    ):
        got = _git_blob_sha(repo_root, pins[path_key])
        if got != pins[sha_key]:
            raise PreregError(f"immutable blob drift: {pins[path_key]} {got}")
    source = (repo_root / pins["entry_path"]).read_text(encoding="utf-8")
    required = (
        "FUSION_WEIGHT = 0.75",
        "state.predict_batch(batch, include_matrix=True)",
        "blend_active_predictions",
        "0.25",
        "0.75",
    )
    if not all(token in source for token in required):
        raise PreregError("Formal V2 component-matrix feasibility surface missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    contract = json.loads((root / CONTRACT_REL).read_text(encoding="utf-8"))
    validate_contract(contract)
    validate_repo(root, contract)
    print("C3_ZERO_LABEL_PREREG_VALIDATION_PASS")
    print("target_labels_read=false training=false tuning=false")
    print("candidate_status=NOT_AVAILABLE weight=0 matrix_delta=0 data_ready=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
