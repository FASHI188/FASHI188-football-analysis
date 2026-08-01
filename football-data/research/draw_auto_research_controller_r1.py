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
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_jsonl(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def initial_checkpoint(spec: dict[str, Any], authorization: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "DRAW-AUTO-RESEARCH-CHECKPOINT-R1.0",
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
        "batch_index": 0,
        "consecutive_stagnant_batches": 0,
        "best_ranking_score": None,
        "top5": [],
        "cumulative_runtime_seconds": 0.0,
        "stop_reason": None,
        "safety_failure": None,
        "formal_weight": 0,
        "repository_writeback": 0,
        "provider_requests": 0,
        "api_football_requests": 0,
        "new_data_collection": False,
        "active_batch_candidate_ids": [],
        "active_batch_records": [],
        "active_batch_start_best_score": None,
        "active_batch_started_at": None
    }


def validate_authorization(spec: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    if not AUTH_PATH.is_file():
        raise ValueError("authorization file missing")
    authorization = read_json(AUTH_PATH)
    if authorization.get("status") != "AUTHORIZED_VIEWED_DEVELOPMENT_AUTO_RESEARCH":
        raise ValueError("authorization status mismatch")
    if authorization.get("user_authorization_record") != "rec0WJJzXiuDvAqSb":
        raise ValueError("authorization record mismatch")
    if authorization.get("data_status") != "VIEWED_DEVELOPMENT_DATA":
        raise ValueError("authorization data status mismatch")
    if authorization.get("formal_weight") != 0:
        raise ValueError("authorization formal weight mismatch")
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
        "status": result["status"],
        "ranking_score": result["ranking_score"],
        "pooled_candidate_metrics": result["pooled_candidate_metrics"],
        "pooled_baseline_metrics": result["pooled_baseline_metrics"],
        "pooled_delta": result["pooled_delta"],
        "runtime_seconds": result["runtime_seconds"],
        "result_file": result_path.name,
        "result_sha256": sha256_file(result_path),
        "recorded_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
    }


def compute_top5(state_dir: pathlib.Path, checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for candidate_id in checkpoint["completed_candidates"]:
        path = state_dir / "results" / f"{candidate_id}.json"
        if path.is_file():
            summaries.append(candidate_summary(read_json(path), path))
    summaries.sort(key=lambda item: (-float(item["ranking_score"]), item["candidate_id"]))
    return summaries[:5]


def batch_markdown(checkpoint: dict[str, Any], batch_records: list[dict[str, Any]], spec: dict[str, Any]) -> str:
    lines = [
        f"# 平局自动研究阶段总结：Batch {checkpoint['batch_index']}", "",
        "- 数据口径：`VIEWED_DEVELOPMENT_DATA`",
        f"- 本批候选：{len(batch_records)}",
        f"- 累计完成：{len(checkpoint['completed_candidates'])}",
        f"- 累计失败：{len(checkpoint['failed_candidates'])}",
        f"- 累计运行：{checkpoint['cumulative_runtime_seconds']:.1f} 秒",
        f"- 连续未达最小改善批次：{checkpoint['consecutive_stagnant_batches']}",
        f"- 剩余候选预算：{spec['budget']['maximum_candidates'] - len(checkpoint['completed_candidates']) - len(checkpoint['failed_candidates'])}",
        f"- 剩余时间预算：{max(0.0, spec['budget']['maximum_cumulative_seconds'] - checkpoint['cumulative_runtime_seconds']):.1f} 秒",
        "", "## 本批记录", "",
        "| Candidate | 状态 | Profile | Ranking score | ΔDraw F1 | ΔRPS |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for record in batch_records:
        delta = record.get("pooled_delta") or {}
        lines.append(f"| {record['candidate_id']} | {record['status']} | {record.get('profile','-')} | {float(record.get('ranking_score', -999)):.6f} | {float(delta.get('Draw F1', 0)):.6f} | {float(delta.get('RPS', 0)):.6f} |")
    lines += ["", "## 当前Top 5", "", "| Rank | Candidate | Profile | Ranking score | Draw F1 | Accuracy | RPS |", "|---:|---|---|---:|---:|---:|---:|"]
    for rank, item in enumerate(checkpoint["top5"], start=1):
        scored = item["pooled_candidate_metrics"]
        lines.append(f"| {rank} | {item['candidate_id']} | {item['profile']} | {item['ranking_score']:.6f} | {scored['Draw F1']:.6f} | {scored['Accuracy']:.6f} | {scored['RPS']:.6f} |")
    lines += ["", "本阶段不构成盲测、正式效果PASS或正式模型推广。", ""]
    return "\n".join(lines)


def final_markdown(checkpoint: dict[str, Any], state_dir: pathlib.Path, spec: dict[str, Any]) -> str:
    lines = [
        "# 平局自动研究最终报告", "", "## 边界", "",
        "- 全部数据均标记为`VIEWED_DEVELOPMENT_DATA`。",
        "- 本报告不是未来盲测，不构成正式效果PASS。",
        "- `formal_weight=0`，正式模型、正式数据、配置、CURRENT均未修改。",
        "", "## 停止状态", "",
        f"- 状态：`{checkpoint['status']}`",
        f"- 停止原因：`{checkpoint['stop_reason']}`",
        f"- 完成候选：{len(checkpoint['completed_candidates'])}",
        f"- 失败候选：{len(checkpoint['failed_candidates'])}",
        f"- 累计运行：{checkpoint['cumulative_runtime_seconds']:.1f} 秒",
        "", "## Top 5", "",
        "| Rank | Candidate | Profile | Draw Precision | Draw Recall | Draw F1 | Accuracy | Macro-F1 | Log Loss | Brier | RPS | Draw ECE |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, item in enumerate(checkpoint["top5"], start=1):
        scored = item["pooled_candidate_metrics"]
        lines.append(f"| {rank} | {item['candidate_id']} | {item['profile']} | {scored['Draw Precision']:.6f} | {scored['Draw Recall']:.6f} | {scored['Draw F1']:.6f} | {scored['Accuracy']:.6f} | {scored['Macro-F1']:.6f} | {scored['Log Loss']:.6f} | {scored['Brier']:.6f} | {scored['RPS']:.6f} | {scored['Draw ECE']:.6f} |")
    recommendation = checkpoint["top5"][0]["candidate_id"] if checkpoint["top5"] else "NONE"
    worth_future = "是：仅建议保留唯一推荐challenger，等待未来真正未查看数据验证。" if checkpoint["top5"] else "否：没有完成的候选可进入未来验证队列。"
    lines += ["", "## 唯一推荐challenger", "", f"`{recommendation}`", "", "## 是否值得等待未来新数据验证", "", worth_future, "", "## 完整记录位置", "", "- `ledger.jsonl`：所有成功与失败试验。", "- `results/`：每个候选的逐fold、逐联赛、汇总、校准与安全门结果。", "- `checkpoint.json`：最终预算和停止状态。", ""]
    return "\n".join(lines)


def stop_reason(checkpoint: dict[str, Any], spec: dict[str, Any]) -> str | None:
    attempted = len(checkpoint["completed_candidates"]) + len(checkpoint["failed_candidates"])
    if checkpoint.get("safety_failure"):
        return "SAFETY_GATE_FAILURE"
    if attempted >= int(spec["budget"]["maximum_candidates"]):
        return "MAXIMUM_200_CANDIDATES_REACHED"
    if float(checkpoint["cumulative_runtime_seconds"]) >= float(spec["budget"]["maximum_cumulative_seconds"]):
        return "CUMULATIVE_6_HOURS_REACHED"
    if int(checkpoint["consecutive_stagnant_batches"]) >= int(spec["budget"]["maximum_stagnant_batches"]):
        return "THREE_CONSECUTIVE_STAGNANT_BATCHES"
    return None


def build_manifest(state_dir: pathlib.Path, checkpoint: dict[str, Any], spec: dict[str, Any], identity: dict[str, Any]) -> None:
    files: dict[str, str] = {}
    for path in sorted(item for item in state_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(state_dir).as_posix()
        if relative != "manifest.json":
            files[relative] = sha256_file(path)
    manifest = {
        "schema_version": "DRAW-AUTO-RESEARCH-ARTIFACT-MANIFEST-R1.0",
        "generated_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
        "checkpoint_status": checkpoint["status"],
        "stop_reason": checkpoint["stop_reason"],
        "authorization_digest": checkpoint["authorization_digest"],
        "frozen_code_head": checkpoint["frozen_code_head"],
        "spec_digest": canonical_json_sha256(spec),
        "identity_digest": canonical_json_sha256(identity),
        "files": files,
        "repository_writeback": 0,
        "formal_weight": 0,
        "provider_requests": 0,
        "api_football_requests": 0,
    }
    atomic_json(state_dir / "manifest.json", manifest)


def _save_checkpoint(state_dir: pathlib.Path, checkpoint: dict[str, Any], base_runtime: float, started: float) -> None:
    checkpoint["cumulative_runtime_seconds"] = base_runtime + (time.monotonic() - started)
    checkpoint["updated_at"] = utc_now()
    atomic_json(state_dir / "checkpoint.json", checkpoint)


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
    attempted_ids = set(checkpoint["completed_candidates"]) | {item["candidate_id"] for item in checkpoint["failed_candidates"]}
    active = list(checkpoint.get("active_batch_candidate_ids") or [])
    if not active:
        remaining = [item["candidate_id"] for item in catalog if item["candidate_id"] not in attempted_ids]
        if not remaining:
            checkpoint["status"] = "STOPPED"
            checkpoint["stop_reason"] = "MAXIMUM_200_CANDIDATES_REACHED"
            _save_checkpoint(state_dir, checkpoint, base_runtime, started)
            (state_dir / "final_report.md").write_text(final_markdown(checkpoint, state_dir, spec), encoding="utf-8")
            build_manifest(state_dir, checkpoint, spec, identity)
            return 0
        active = remaining[: int(spec["budget"]["batch_size"])]
        checkpoint["active_batch_candidate_ids"] = active
        checkpoint["active_batch_records"] = []
        checkpoint["active_batch_start_best_score"] = checkpoint["best_ranking_score"]
        checkpoint["active_batch_started_at"] = utc_now()
        _save_checkpoint(state_dir, checkpoint, base_runtime, started)
    try:
        rows = load_rows(spec)
        folds = build_outer_folds(rows)
        if len(folds) != 51:
            raise ValueError("fold safety gate failure")
    except Exception as exc:
        checkpoint["status"] = "FAILED_SAFETY"
        checkpoint["safety_failure"] = f"DATA_OR_FOLD_GATE:{exc}"
        checkpoint["stop_reason"] = "SAFETY_GATE_FAILURE"
        append_jsonl(state_dir / "ledger.jsonl", {"record_type": "GLOBAL_FAILURE", "error": str(exc), "recorded_at": utc_now()})
        _save_checkpoint(state_dir, checkpoint, base_runtime, started)
        (state_dir / "final_report.md").write_text(final_markdown(checkpoint, state_dir, spec), encoding="utf-8")
        build_manifest(state_dir, checkpoint, spec, identity)
        return 2
    attempted_ids = set(checkpoint["completed_candidates"]) | {item["candidate_id"] for item in checkpoint["failed_candidates"]}
    for candidate_id in active:
        if candidate_id in attempted_ids:
            continue
        if time.monotonic() - started >= max_run_seconds:
            break
        if base_runtime + (time.monotonic() - started) >= float(spec["budget"]["maximum_cumulative_seconds"]):
            break
        candidate = by_id[candidate_id]
        result_path = state_dir / "results" / f"{candidate_id}.json"
        try:
            result = evaluate_candidate(candidate, folds)
            validate_candidate_result(result)
            atomic_json(result_path, result)
            record = candidate_summary(result, result_path)
            append_jsonl(state_dir / "ledger.jsonl", {"record_type": "CANDIDATE", **record})
            checkpoint["completed_candidates"].append(candidate_id)
        except Exception as exc:
            record = {
                "record_type": "CANDIDATE_FAILURE", "candidate_id": candidate_id,
                "candidate_sha256": candidate["candidate_sha256"], "profile": candidate["profile"],
                "status": "FAILED", "error": str(exc), "recorded_at": utc_now(),
                "data_status": "VIEWED_DEVELOPMENT_DATA",
            }
            append_jsonl(state_dir / "ledger.jsonl", record)
            checkpoint["failed_candidates"].append({"candidate_id": candidate_id, "error": str(exc)})
        checkpoint["active_batch_records"].append(record)
        checkpoint["next_candidate_index"] = len(checkpoint["completed_candidates"]) + len(checkpoint["failed_candidates"])
        checkpoint["top5"] = compute_top5(state_dir, checkpoint)
        checkpoint["best_ranking_score"] = checkpoint["top5"][0]["ranking_score"] if checkpoint["top5"] else None
        _save_checkpoint(state_dir, checkpoint, base_runtime, started)
        attempted_ids.add(candidate_id)
    active_complete = all(candidate_id in attempted_ids for candidate_id in active)
    if active_complete:
        checkpoint["batch_index"] += 1
        before_best = checkpoint.get("active_batch_start_best_score")
        current_best = checkpoint["best_ranking_score"]
        minimum = float(spec["budget"]["minimum_batch_improvement"])
        improved = current_best is not None and (before_best is None or float(current_best) - float(before_best) >= minimum)
        checkpoint["consecutive_stagnant_batches"] = 0 if improved else int(checkpoint["consecutive_stagnant_batches"]) + 1
        summary_path = state_dir / "summaries" / f"batch_{checkpoint['batch_index']:02d}.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        _save_checkpoint(state_dir, checkpoint, base_runtime, started)
        summary_path.write_text(batch_markdown(checkpoint, list(checkpoint["active_batch_records"]), spec), encoding="utf-8")
        checkpoint["active_batch_candidate_ids"] = []
        checkpoint["active_batch_records"] = []
        checkpoint["active_batch_start_best_score"] = None
        checkpoint["active_batch_started_at"] = None
    _save_checkpoint(state_dir, checkpoint, base_runtime, started)
    reason = stop_reason(checkpoint, spec)
    if reason:
        checkpoint["status"] = "STOPPED" if reason != "SAFETY_GATE_FAILURE" else "FAILED_SAFETY"
        checkpoint["stop_reason"] = reason
    atomic_json(state_dir / "top5.json", {"generated_at": utc_now(), "top5": checkpoint["top5"]})
    _save_checkpoint(state_dir, checkpoint, base_runtime, started)
    if checkpoint["status"] != "RUNNING":
        (state_dir / "final_report.md").write_text(final_markdown(checkpoint, state_dir, spec), encoding="utf-8")
    build_manifest(state_dir, checkpoint, spec, identity)
    return 2 if checkpoint["status"] == "FAILED_SAFETY" else 0


def probe(state_dir: pathlib.Path) -> int:
    if not (state_dir / "checkpoint.json").exists():
        print(json.dumps({"status": "NEW", "should_dispatch_successor": True}))
        return 0
    checkpoint = read_json(state_dir / "checkpoint.json")
    print(json.dumps({"status": checkpoint.get("status"), "should_dispatch_successor": checkpoint.get("status") == "RUNNING"}))
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
