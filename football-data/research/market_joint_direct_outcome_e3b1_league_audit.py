#!/usr/bin/env python3
"""Build a read-only per-league audit receipt for E3b-1 results.

This script does not train, tune, select matches, or mutate formal assets. It
reads the frozen E3b-1 JSON result, verifies that the recorded repository HEAD
matches the checked-out commit, and expands the existing league metrics into a
human-readable audit receipt.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any

METRICS = (
    "accuracy",
    "balanced_accuracy",
    "macro_f1",
    "draw_precision",
    "draw_recall",
    "draw_f1",
    "logloss",
    "brier",
    "rps",
    "confidence_ece_10bin",
)
OUTCOMES = ("home", "draw", "away")


def git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def predicted_counts(metrics: dict[str, Any]) -> dict[str, int]:
    per_class = metrics.get("per_class", {})
    return {
        outcome: int(per_class.get(outcome, {}).get("predicted_count", 0))
        for outcome in OUTCOMES
    }


def metric_delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    return {
        key: float(candidate[key]) - float(baseline[key])
        for key in METRICS
    }


def finite_metrics(metrics: dict[str, Any]) -> bool:
    return all(math.isfinite(float(metrics[key])) for key in METRICS)


def league_receipt(
    report: dict[str, Any], section_name: str, competition_id: str
) -> dict[str, Any]:
    section = report[section_name]
    source = section["per_league"][competition_id]
    market = source["market"]
    champion = source["champion"]
    candidate = source["e3b1"]
    join = report.get("coverage", {}).get("by_competition", {}).get(competition_id, {})
    return {
        "competition_id": competition_id,
        "competition_zh": source.get("competition_zh", competition_id),
        "evaluated_count": int(source["count"]),
        "champion_oos_count": int(join.get("champion_oos", source["count"])),
        "market_joined_count": int(join.get("market_joined", source["count"])),
        "market_join_coverage": float(join.get("coverage", 1.0)),
        "market": market,
        "champion": champion,
        "e3b1": candidate,
        "predicted_counts": {
            "market": predicted_counts(market),
            "champion": predicted_counts(champion),
            "e3b1": predicted_counts(candidate),
        },
        "delta_e3b1_minus_market": metric_delta(candidate, market),
        "delta_e3b1_minus_champion": metric_delta(candidate, champion),
        "finite_metric_audit": {
            "market": finite_metrics(market),
            "champion": finite_metrics(champion),
            "e3b1": finite_metrics(candidate),
        },
    }


def pct(value: float) -> str:
    return f"{value:.4%}"


def metric_row(label: str, metrics: dict[str, Any], counts: dict[str, int]) -> str:
    return (
        f"| {label} | {pct(metrics['accuracy'])} | {pct(metrics['balanced_accuracy'])} | "
        f"{pct(metrics['macro_f1'])} | {pct(metrics['draw_precision'])} | "
        f"{pct(metrics['draw_recall'])} | {pct(metrics['draw_f1'])} | "
        f"{metrics['logloss']:.6f} | {metrics['brier']:.6f} | "
        f"{metrics['rps']:.6f} | {metrics['confidence_ece_10bin']:.6f} | "
        f"{counts['home']}/{counts['draw']}/{counts['away']} |"
    )


def markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# E3b-1 准确HEAD与逐联赛审计回执",
        "",
        f"- 预期HEAD：`{receipt['expected_head']}`",
        f"- 实际checkout HEAD：`{receipt['actual_head']}`",
        f"- 原始报告记录HEAD：`{receipt['source_report_head']}`",
        f"- HEAD一致性：**{receipt['head_identity_status']}**",
        f"- 原始实验状态：**{receipt['source_research_status']}**",
        f"- 正式权重：**{receipt['formal_weight']}**",
        f"- 晋级裁决：**{receipt['promotion_verdict']}**",
        "",
        "本回执只展开原始E3b-1结果；没有重新训练、调参、重选B100或修改正式资产。",
        "",
    ]
    for section_name, title in (("full_oos", "五大联赛滚动OOS"), ("b100", "固定B100")):
        lines.extend((f"## {title}", ""))
        for league in receipt["sections"][section_name]["leagues"]:
            lines.extend((
                f"### {league['competition_zh']}（{league['competition_id']}）",
                "",
                f"- 评估样本：{league['evaluated_count']}",
                f"- 市场联合覆盖：{league['market_joined_count']}/{league['champion_oos_count']} "
                f"= {pct(league['market_join_coverage'])}",
                "",
                "| 模型 | Accuracy | Balanced Acc. | Macro-F1 | Draw P | Draw R | Draw F1 | LogLoss | Brier | RPS | ECE | 预测H/D/A |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
                metric_row("市场", league["market"], league["predicted_counts"]["market"]),
                metric_row("Champion", league["champion"], league["predicted_counts"]["champion"]),
                metric_row("E3b-1", league["e3b1"], league["predicted_counts"]["e3b1"]),
                "",
                f"- E3b-1相对市场：Accuracy {league['delta_e3b1_minus_market']['accuracy']:+.4%}；"
                f"Draw F1 {league['delta_e3b1_minus_market']['draw_f1']:+.4%}；"
                f"LogLoss {league['delta_e3b1_minus_market']['logloss']:+.6f}。",
                f"- E3b-1相对Champion：Accuracy {league['delta_e3b1_minus_champion']['accuracy']:+.4%}；"
                f"Draw F1 {league['delta_e3b1_minus_champion']['draw_f1']:+.4%}；"
                f"LogLoss {league['delta_e3b1_minus_champion']['logloss']:+.6f}。",
                "",
            ))
    lines.extend((
        "## 固定裁决",
        "",
        "- 此回执的PASS只表示准确HEAD、概率指标和逐联赛审计链完整。",
        "- 不表示E3b-1效果晋级，不表示平局问题已经解决。",
        "- E3b-1继续保持挑战层、formal_weight=0。",
        "- 在单一统一比分矩阵完成E3b-2最小KL/IPF协调并通过审计前，不得正式使用。",
        "",
    ))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="artifacts/research/market_joint_direct_outcome_e3b1/market_joint_direct_outcome_e3b1.json",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/research/market_joint_direct_outcome_e3b1",
    )
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    report = json.loads(source.read_text(encoding="utf-8"))
    actual_head = git_head(root)
    source_head = str(report.get("repository_head") or "")
    head_ok = actual_head == args.expected_head == source_head

    competition_ids = list(report["full_oos"]["per_league"].keys())
    sections = {}
    for section_name in ("full_oos", "b100"):
        leagues = [league_receipt(report, section_name, cid) for cid in competition_ids]
        sections[section_name] = {
            "count": int(report[section_name]["count"]),
            "leagues": leagues,
            "all_metrics_finite": all(
                all(item["finite_metric_audit"].values()) for item in leagues
            ),
        }

    audit_pass = (
        head_ok
        and report.get("research_status") == "PASS"
        and all(section["all_metrics_finite"] for section in sections.values())
    )
    receipt = {
        "schema_version": "1.0",
        "audit_status": "PASS" if audit_pass else "FAIL",
        "expected_head": args.expected_head,
        "actual_head": actual_head,
        "source_report_head": source_head,
        "head_identity_status": "PASS" if head_ok else "FAIL",
        "source_research_status": report.get("research_status"),
        "formal_weight": 0,
        "promotion_verdict": "NOT_PROMOTED_CHALLENGE_LAYER_ONLY",
        "sections": sections,
        "formal_mutation": {
            "model": 0,
            "data": 0,
            "config": 0,
            "current": 0,
            "formal_weight": 0,
        },
    }
    (output_dir / "market_joint_direct_outcome_e3b1_league_audit.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "market_joint_direct_outcome_e3b1_league_audit.md").write_text(
        markdown(receipt), encoding="utf-8"
    )
    print(json.dumps({
        "audit_status": receipt["audit_status"],
        "head_identity_status": receipt["head_identity_status"],
        "expected_head": receipt["expected_head"],
        "actual_head": receipt["actual_head"],
        "source_report_head": receipt["source_report_head"],
        "promotion_verdict": receipt["promotion_verdict"],
    }, ensure_ascii=False, indent=2))
    return 0 if audit_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
