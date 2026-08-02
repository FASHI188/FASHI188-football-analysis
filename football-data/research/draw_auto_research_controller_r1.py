#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import time
from typing import Any

from draw_auto_research_engine_r1 import (
    build_outer_folds,
    candidate_catalog,
    evaluate_candidate,
    load_rows,
    validate_candidate_result,
)
from draw_auto_research_math_r1 import canonical_json_sha256

HERE = pathlib.Path(__file__).resolve().parent
SPEC_PATH = HERE / "draw_auto_research_spec_r1.json"
IDENTITY_PATH = HERE / "draw_auto_research_identity_r1.json"
AUTH_PATH = HERE / "draw_composite_run_authorization_r1.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_jsonl(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def initial_checkpoint(spec: dict[str, Any], authorization: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "DRAW-AUTO-RESEARCH-CHECKPOINT-R1.4",
        "status": "RUNNING",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
        "user_authorization_record": "rec0WJJzXiuDvAqSb",
        "authorization_digest": canonical_json_sha256(authorization),
        "frozen_code_head": authorization["frozen_code_head"],
        "spec_digest": canonical_json_sha256(spec),
        "identity_digest": canonical_json_sha256(identity),
        "next_candidate_index": 0,
        "completed_candidates": [],
        "failed_candidates": [],
        "duplicate_prediction_candidates": [],
        "prediction_fingerprints": [],
        "batch_index": 0,
        "consecutive_stagnant_batches": 0,
        "best_ranking_score": None,
        "top5": [],
        "eligible_challenger": None,
        "cumulative_runtime_seconds": 0.0,
        "stop_reason": None,
        "safety_failure": None,
        "terminal_failure": None,
        "formal_weight": 0,
        "repository_writeback": 0,
        "provider_requests": 0,
        "api_football_requests": 0,
        "new_data_collection": False,
        "active_batch_candidate_ids": [],
        "active_batch_records": [],
        "active_batch_start_best_score": None,
        "active_batch_started_at": None,
    }


def validate_authorization(spec: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    if not AUTH_PATH.is_file():
        raise ValueError("authorization file missing")
    authorization = read_json(AUTH_PATH)
    if authorization.get("status") != "AUTHORIZED_VIEWED_DEVELOPMENT_AUTO_RESEARCH":
        raise ValueError("authorization status mismatch")
    if authorization.get("user_authorization_record") != "rec0WJJzXiuDvAqSb":
        raise ValueError("authorization record mismatch")
    if authorization.get("data_status") != "VIEWED_DEVELOPMENT_DATA" or authorization.get("formal_weight") != 0:
        raise ValueError("authorization boundary mismatch")
    if int(authorization.get("maximum_candidates", 0)) != int(spec["budget"]["maximum_candidates"]):
        raise ValueError("authorization candidate budget mismatch")
    if int(authorization.get("maximum_cumulative_seconds", 0)) != int(spec["budget"]["maximum_cumulative_seconds"]):
        raise ValueError("authorization time budget mismatch")
    if authorization.get("spec_canonical_sha256") != canonical_json_sha256(spec):
        raise ValueError("authorization spec binding mismatch")
    if authorization.get("identity_canonical_sha256") != canonical_json_sha256(identity):
        raise ValueError("authorization identity binding mismatch")
    return authorization


def load_checkpoint(state_dir: pathlib.Path, spec: dict[str, Any], authorization: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    if (state_dir / "run_failure_receipt.json").is_file():
        raise ValueError("terminal run failure receipt already present")
    path = state_dir / "checkpoint.json"
    if not path.exists():
        return initial_checkpoint(spec, authorization, identity)
    checkpoint = read_json(path)
    expected = {
        "authorization_digest": canonical_json_sha256(authorization),
        "spec_digest": canonical_json_sha256(spec),
        "identity_digest": canonical_json_sha256(identity),
        "user_authorization_record": "rec0WJJzXiuDvAqSb",
        "formal_weight": 0,
        "repository_writeback": 0,
    }
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(f"checkpoint identity mismatch: {key}")
    return checkpoint


def candidate_summary(result: dict[str, Any], result_path: pathlib.Path) -> dict[str, Any]:
    return {
        "candidate_id": result["candidate"]["candidate_id"],
        "candidate_sha256": result["candidate"]["candidate_sha256"],
        "profile": result["candidate"]["profile"],
        "basis_variant": result["candidate"]["basis_variant"],
        "status": result["status"],
        "ranking_score": result["ranking_score"],
        "pooled_candidate_metrics": result["pooled_candidate_metrics"],
        "pooled_baseline_metrics": result["pooled_baseline_metrics"],
        "pooled_delta": result["pooled_delta"],
        "challenger_gate": result["challenger_gate"],
        "prediction_fingerprint": result["candidate_prediction_fingerprint"],
        "runtime_seconds": result["runtime_seconds"],
        "result_file": result_path.name,
        "result_sha256": sha256_file(result_path),
        "recorded_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
    }


def all_summaries(state_dir: pathlib.Path, checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for candidate_id in checkpoint["completed_candidates"]:
        path = state_dir / "results" / f"{candidate_id}.json"
        if not path.is_file():
            raise ValueError(f"completed candidate result missing: {candidate_id}")
        summaries.append(candidate_summary(read_json(path), path))
    summaries.sort(key=lambda item: (-float(item["ranking_score"]), item["candidate_id"]))
    return summaries


def update_rankings(state_dir: pathlib.Path, checkpoint: dict[str, Any]) -> None:
    summaries = all_summaries(state_dir, checkpoint)
    checkpoint["top5"] = summaries[:5]
    eligible = [item for item in summaries if item.get("challenger_gate", {}).get("status") == "PASS"]
    checkpoint["eligible_challenger"] = eligible[0] if eligible else None
    checkpoint["best_ranking_score"] = summaries[0]["ranking_score"] if summaries else None


def batch_markdown(checkpoint: dict[str, Any], batch_records: list[dict[str, Any]], spec: dict[str, Any]) -> str:
    lines = [
        f"# 平局自动研究阶段总结：Batch {checkpoint['batch_index']}", "",
        "- 数据口径：`VIEWED_DEVELOPMENT_DATA`",
        "- 比较基线：`INDEPENDENT_ELO_HISTORICAL_DRAW_BASELINE_R1`（独立研究基线，不是正式模型）",
        f"- 本批记录：{len(batch_records)}",
        f"- 累计完成：{len(checkpoint['completed_candidates'])}",
        f"- 累计失败：{len(checkpoint['failed_candidates'])}",
        f"- 等价预测淘汰：{len(checkpoint['duplicate_prediction_candidates'])}",
        f"- 累计运行：{checkpoint['cumulative_runtime_seconds']:.1f} 秒",
        f"- 连续未达最小改善批次：{checkpoint['consecutive_stagnant_batches']}",
        f"- 剩余候选预算：{spec['budget']['maximum_candidates'] - checkpoint['next_candidate_index']}",
        f"- 剩余时间预算：{max(0.0, spec['budget']['maximum_cumulative_seconds'] - checkpoint['cumulative_runtime_seconds']):.1f} 秒",
        "", "## 当前Top 5", "",
        "| Rank | Candidate | Profile | Basis | Gate | Ranking | Draw F1 | Accuracy | RPS |",
        "|---:|---|---|---|---|---:|---:|---:|---:|",
    ]
    for rank, item in enumerate(checkpoint["top5"], start=1):
        scored = item["pooled_candidate_metrics"]
        lines.append(f"| {rank} | {item['candidate_id']} | {item['profile']} | {item['basis_variant']} | {item['challenger_gate']['status']} | {item['ranking_score']:.6f} | {scored['Draw F1']:.6f} | {scored['Accuracy']:.6f} | {scored['RPS']:.6f} |")
    lines += ["", "## 当前唯一推荐challenger", "", (f"`{checkpoint['eligible_challenger']['candidate_id']}`" if checkpoint["eligible_challenger"] else "`NO_CHALLENGER`"), "", "本阶段不构成盲测、正式效果PASS或正式模型推广。", ""]
    return "\n".join(lines)


def final_markdown(checkpoint: dict[str, Any], spec: dict[str, Any]) -> str:
    lines = [
        "# 平局自动研究最终报告", "", "## 边界", "",
        "- 全部数据均为`VIEWED_DEVELOPMENT_DATA`。",
        "- 基线是独立的`INDEPENDENT_ELO_HISTORICAL_DRAW_BASELINE_R1`，不是当前正式模型。",
        "- 本报告不能声称改善正式模型，也不是未来盲测。",
        "- `formal_weight=0`，正式模型、正式数据、配置、CURRENT均未修改。", "",
        "## 停止状态", "",
        f"- 状态：`{checkpoint['status']}`",
        f"- 停止原因：`{checkpoint['stop_reason']}`",
        f"- 完成候选：{len(checkpoint['completed_candidates'])}",
        f"- 失败候选：{len(checkpoint['failed_candidates'])}",
        f"- 等价预测淘汰：{len(checkpoint['duplicate_prediction_candidates'])}",
        f"- 累计运行：{checkpoint['cumulative_runtime_seconds']:.1f} 秒", "",
        "## Top 5", "",
        "| Rank | Candidate | Profile | Basis | Gate | Draw Precision | Draw Recall | Draw F1 | Accuracy | Macro-F1 | Log Loss | Brier | RPS | Draw ECE |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, item in enumerate(checkpoint["top5"], start=1):
        scored = item["pooled_candidate_metrics"]
        lines.append(f"| {rank} | {item['candidate_id']} | {item['profile']} | {item['basis_variant']} | {item['challenger_gate']['status']} | {scored['Draw Precision']:.6f} | {scored['Draw Recall']:.6f} | {scored['Draw F1']:.6f} | {scored['Accuracy']:.6f} | {scored['Macro-F1']:.6f} | {scored['Log Loss']:.6f} | {scored['Brier']:.6f} | {scored['RPS']:.6f} | {scored['Draw ECE']:.6f} |")
    recommendation = checkpoint["eligible_challenger"]["candidate_id"] if checkpoint["eligible_challenger"] else "NO_CHALLENGER"
    worth_future = "是：该候选仅可进入未来真正未查看数据验证。" if recommendation != "NO_CHALLENGER" else "否：当前没有候选通过预注册的改善、概率质量和跨联赛稳定性门。"
    lines += ["", "## 唯一推荐challenger", "", f"`{recommendation}`", "", "## 是否值得等待未来新数据验证", "", worth_future, "", "## 完整记录位置", "", "- `ledger.jsonl`：所有成功、失败及等价预测淘汰。", "- `results/`：逐fold、逐联赛、汇总、校准与gate。", "- `run_failure_receipt.json`：非候选级运行异常（如有）。", ""]
    return "\n".join(lines)


def stop_reason(checkpoint: dict[str, Any], spec: dict[str, Any]) -> str | None:
    if checkpoint.get("safety_failure"):
        return "SAFETY_GATE_FAILURE"
    if checkpoint["next_candidate_index"] >= int(spec["budget"]["maximum_candidates"]):
        return "MAXIMUM_200_CANDIDATES_REACHED"
    if float(checkpoint["cumulative_runtime_seconds"]) >= float(spec["budget"]["maximum_cumulative_seconds"]):
        return "CUMULATIVE_6_HOURS_REACHED"
    if int(checkpoint["consecutive_stagnant_batches"]) >= int(spec["budget"]["maximum_stagnant_batches"]):
        return "THREE_CONSECUTIVE_STAGNANT_BATCHES"
    return None


def build_manifest(state_dir: pathlib.Path, checkpoint: dict[str, Any] | None, spec: dict[str, Any], identity: dict[str, Any]) -> None:
    files: dict[str, str] = {}
    for path in sorted(item for item in state_dir.rglob("*") if item.is_file() and not item.is_symlink()):
        relative = path.relative_to(state_dir).as_posix()
        if relative != "manifest.json":
            files[relative] = sha256_file(path)
    atomic_json(state_dir / "manifest.json", {
        "schema_version": "DRAW-AUTO-RESEARCH-ARTIFACT-MANIFEST-R1.4",
        "generated_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
        "checkpoint_status": checkpoint.get("status") if checkpoint else "FAILED_WITHOUT_CHECKPOINT",
        "stop_reason": checkpoint.get("stop_reason") if checkpoint else "CONTROLLER_FAILURE_BEFORE_CHECKPOINT",
        "authorization_digest": checkpoint.get("authorization_digest") if checkpoint else None,
        "frozen_code_head": checkpoint.get("frozen_code_head") if checkpoint else None,
        "spec_digest": canonical_json_sha256(spec),
        "identity_digest": canonical_json_sha256(identity),
        "files": files,
        "repository_writeback": 0,
        "formal_weight": 0,
        "provider_requests": 0,
        "api_football_requests": 0,
    })


def _save_checkpoint(state_dir: pathlib.Path, checkpoint: dict[str, Any], base_runtime: float, started: float) -> None:
    checkpoint["cumulative_runtime_seconds"] = base_runtime + (time.monotonic() - started)
    checkpoint["updated_at"] = utc_now()
    atomic_json(state_dir / "checkpoint.json", checkpoint)


def mark_safety_failure(state_dir: pathlib.Path, checkpoint: dict[str, Any], spec: dict[str, Any], identity: dict[str, Any], *, candidate_id: str | None, error: str, base_runtime: float, started: float) -> int:
    record = {
        "record_type": "SAFETY_FAILURE",
        "candidate_id": candidate_id,
        "status": "FAILED_SAFETY",
        "error": error,
        "recorded_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
    }
    append_jsonl(state_dir / "ledger.jsonl", record)
    checkpoint["safety_failure"] = record
    checkpoint["status"] = "FAILED_SAFETY"
    checkpoint["stop_reason"] = "SAFETY_GATE_FAILURE"
    checkpoint["active_batch_records"].append(record)
    _save_checkpoint(state_dir, checkpoint, base_runtime, started)
    (state_dir / "final_report.md").write_text(final_markdown(checkpoint, spec), encoding="utf-8", newline="\n")
    build_manifest(state_dir, checkpoint, spec, identity)
    return 2


def run_batch(state_dir: pathlib.Path, run_id: str, run_attempt: str, max_run_seconds: int) -> int:
    started = time.monotonic()
    spec = read_json(SPEC_PATH)
    identity = read_json(IDENTITY_PATH)
    authorization = validate_authorization(spec, identity)
    state_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint(state_dir, spec, authorization, identity)
    base_runtime = float(checkpoint["cumulative_runtime_seconds"])
    if checkpoint["status"] != "RUNNING":
        build_manifest(state_dir, checkpoint, spec, identity)
        return 0
    checkpoint["last_run_id"] = str(run_id)
    checkpoint["last_run_attempt"] = str(run_attempt)
    catalog = candidate_catalog()
    by_id = {item["candidate_id"]: item for item in catalog}
    attempted_ids = set(checkpoint["completed_candidates"]) | {item["candidate_id"] for item in checkpoint["failed_candidates"]} | set(checkpoint["duplicate_prediction_candidates"])
    active = list(checkpoint.get("active_batch_candidate_ids") or [])
    if not active:
        remaining = [item["candidate_id"] for item in catalog if item["candidate_id"] not in attempted_ids]
        if not remaining:
            checkpoint["status"] = "STOPPED"
            checkpoint["stop_reason"] = "MAXIMUM_200_CANDIDATES_REACHED"
            _save_checkpoint(state_dir, checkpoint, base_runtime, started)
            (state_dir / "final_report.md").write_text(final_markdown(checkpoint, spec), encoding="utf-8", newline="\n")
            build_manifest(state_dir, checkpoint, spec, identity)
            return 0
        active = remaining[: int(spec["budget"]["batch_size"])]
        checkpoint["active_batch_candidate_ids"] = active
        checkpoint["active_batch_records"] = []
        checkpoint["active_batch_start_best_score"] = checkpoint["best_ranking_score"]
        checkpoint["active_batch_started_at"] = utc_now()
        _save_checkpoint(state_dir, checkpoint, base_runtime, started)
    rows = load_rows(spec)
    folds = build_outer_folds(rows)
    seen = set(checkpoint.get("prediction_fingerprints") or [])
    for candidate_id in active:
        if candidate_id in attempted_ids:
            continue
        if time.monotonic() - started >= max_run_seconds or base_runtime + (time.monotonic() - started) >= float(spec["budget"]["maximum_cumulative_seconds"]):
            break
        candidate = by_id[candidate_id]
        result_path = state_dir / "results" / f"{candidate_id}.json"
        try:
            result = evaluate_candidate(candidate, folds, seen_prediction_fingerprints=seen, challenger_gate=spec["challenger_gate"])
            if not result["prediction_fingerprint_unique"]:
                record = {"record_type": "EQUIVALENT_PREDICTION", "candidate_id": candidate_id, "candidate_sha256": candidate["candidate_sha256"], "prediction_fingerprint": result["candidate_prediction_fingerprint"], "recorded_at": utc_now(), "data_status": "VIEWED_DEVELOPMENT_DATA"}
                append_jsonl(state_dir / "ledger.jsonl", record)
                checkpoint["duplicate_prediction_candidates"].append(candidate_id)
            else:
                try:
                    validate_candidate_result(result)
                except Exception as safety_exc:
                    return mark_safety_failure(state_dir, checkpoint, spec, identity, candidate_id=candidate_id, error=f"candidate result safety validation failed: {safety_exc}", base_runtime=base_runtime, started=started)
                atomic_json(result_path, result)
                record = {"record_type": "CANDIDATE", **candidate_summary(result, result_path)}
                append_jsonl(state_dir / "ledger.jsonl", record)
                checkpoint["completed_candidates"].append(candidate_id)
                seen.add(result["candidate_prediction_fingerprint"])
                checkpoint["prediction_fingerprints"] = sorted(seen)
        except Exception as exc:
            record = {"record_type": "CANDIDATE_FAILURE", "candidate_id": candidate_id, "candidate_sha256": candidate["candidate_sha256"], "status": "FAILED", "error": str(exc), "recorded_at": utc_now(), "data_status": "VIEWED_DEVELOPMENT_DATA"}
            append_jsonl(state_dir / "ledger.jsonl", record)
            checkpoint["failed_candidates"].append({"candidate_id": candidate_id, "error": str(exc)})
        checkpoint["active_batch_records"].append(record)
        checkpoint["next_candidate_index"] += 1
        update_rankings(state_dir, checkpoint)
        _save_checkpoint(state_dir, checkpoint, base_runtime, started)
        attempted_ids.add(candidate_id)
    active_complete = all(candidate_id in attempted_ids for candidate_id in active)
    if active_complete:
        checkpoint["batch_index"] += 1
        before_best = checkpoint.get("active_batch_start_best_score")
        current_best = checkpoint["best_ranking_score"]
        improved = current_best is not None and (before_best is None or float(current_best) - float(before_best) >= float(spec["budget"]["minimum_batch_improvement"]))
        checkpoint["consecutive_stagnant_batches"] = 0 if improved else int(checkpoint["consecutive_stagnant_batches"]) + 1
        summary_path = state_dir / "summaries" / f"batch_{checkpoint['batch_index']:02d}.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(batch_markdown(checkpoint, list(checkpoint["active_batch_records"]), spec), encoding="utf-8", newline="\n")
        checkpoint["active_batch_candidate_ids"] = []
        checkpoint["active_batch_records"] = []
        checkpoint["active_batch_start_best_score"] = None
        checkpoint["active_batch_started_at"] = None
    reason = stop_reason(checkpoint, spec)
    if reason:
        checkpoint["status"] = "FAILED_SAFETY" if reason == "SAFETY_GATE_FAILURE" else "STOPPED"
        checkpoint["stop_reason"] = reason
    atomic_json(state_dir / "top5.json", {"generated_at": utc_now(), "top5": checkpoint["top5"], "eligible_challenger": checkpoint["eligible_challenger"] or "NO_CHALLENGER"})
    _save_checkpoint(state_dir, checkpoint, base_runtime, started)
    if checkpoint["status"] != "RUNNING":
        (state_dir / "final_report.md").write_text(final_markdown(checkpoint, spec), encoding="utf-8", newline="\n")
    build_manifest(state_dir, checkpoint, spec, identity)
    return 2 if checkpoint["status"] == "FAILED_SAFETY" else 0


def probe(state_dir: pathlib.Path) -> int:
    failure_path = state_dir / "run_failure_receipt.json"
    if failure_path.is_file():
        receipt = read_json(failure_path)
        print(json.dumps({"status": receipt.get("status", "FAILED"), "stop_reason": receipt.get("stop_reason"), "should_continue": False}))
        return 0
    checkpoint_path = state_dir / "checkpoint.json"
    if not checkpoint_path.exists():
        print(json.dumps({"status": "NEW", "should_continue": True}))
        return 0
    checkpoint = read_json(checkpoint_path)
    print(json.dumps({"status": checkpoint.get("status"), "stop_reason": checkpoint.get("stop_reason"), "should_continue": checkpoint.get("status") == "RUNNING"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--mode", choices=("run-batch", "probe"), default="run-batch")
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--max-run-seconds", type=int, default=19800)
    args = parser.parse_args()
    if args.mode == "probe":
        return probe(args.state_dir)
    return run_batch(args.state_dir, args.run_id, args.run_attempt, args.max_run_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
