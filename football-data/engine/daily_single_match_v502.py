#!/usr/bin/env python3
"""Offline-first daily single-match orchestration for CURRENT V5.0.2.

This module does not create probabilities. It prepares a fail-closed input for the
existing formal runner, invokes that runner, validates its artifacts, and renders
the fixed A-H report. It performs no network access and never reads API keys.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

CURRENT_VERSION = "V5.0.2"
SETTLEMENT = "90_minutes_including_stoppage"
ENGINE_DIR = Path(__file__).resolve().parent
FORMAL_RUNNER = ENGINE_DIR / "run_formal_prediction_v460.py"
VALID_EXECUTION_MODES = {"live_user_supplied", "offline_repository_snapshot_demo"}
TARGET_RESULT_KEYS = {
    "actual_result", "target_result", "final_score", "postmatch_result",
    "actual_home_goals", "actual_away_goals", "target_home_goals", "target_away_goals",
}
STATE_ORDER = {
    "通过": 0, "部分通过": 1, "警告": 2, "降级": 3, "未启用": 4,
    "不可用": 5, "弃权": 6, "失败": 7, "不适用": 0,
}
DIRECTION_ZH = {"home": "主胜", "draw": "平局", "away": "客胜"}


class DailyFlowError(RuntimeError):
    def __init__(self, failure_class: str, message: str):
        self.failure_class = failure_class
        super().__init__(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyFlowError("INPUT_INVALID", f"无法读取JSON：{path}") from exc
    if not isinstance(value, dict):
        raise DailyFlowError("INPUT_INVALID", "输入JSON必须是对象")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DailyFlowError("INPUT_INVALID", f"{field}不是有效ISO时间") from exc
    if parsed.tzinfo is None:
        raise DailyFlowError("INPUT_INVALID", f"{field}必须含时区")
    return parsed.astimezone(timezone.utc)


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "LOCAL_UNCOMMITTED"


def _assert_no_target_result_leakage(payload: Mapping[str, Any]) -> None:
    leaked = sorted(key for key in payload if str(key).lower() in TARGET_RESULT_KEYS)
    if leaked:
        raise DailyFlowError("TARGET_RESULT_LEAKAGE_BLOCKED", "目标比赛输入包含赛后字段：" + ", ".join(leaked))
    if payload.get("postmatch") is not None or payload.get("audit_result") is not None:
        raise DailyFlowError("TARGET_RESULT_LEAKAGE_BLOCKED", "目标比赛不得携带赛后对象")


def _require_identity_evidence(payload: Mapping[str, Any], freeze: datetime) -> dict[str, Any]:
    evidence = payload.get("identity_evidence")
    if not isinstance(evidence, dict):
        raise DailyFlowError("IDENTITY_EVIDENCE_MISSING", "缺少identity_evidence")
    status = str(evidence.get("status") or "")
    mode = str(payload.get("execution_mode") or "")
    allowed = {"verified"} if mode == "live_user_supplied" else {"demonstration_only"}
    if status not in allowed:
        raise DailyFlowError("IDENTITY_EVIDENCE_INVALID", f"身份状态不允许：{status}")
    source_name = str(evidence.get("source_name") or "").strip()
    source_url = str(evidence.get("source_url") or "").strip()
    observed = _parse_utc(evidence.get("observed_at_utc"), "identity_evidence.observed_at_utc")
    if not source_name or not source_url or observed > freeze:
        raise DailyFlowError("IDENTITY_EVIDENCE_INVALID", "身份来源缺失或晚于冻结时点")
    return dict(evidence)


def _derive_repository_freshness(payload: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("repository_snapshot")
    if not isinstance(snapshot, dict):
        raise DailyFlowError("REPOSITORY_SNAPSHOT_MISSING", "离线演示缺少repository_snapshot")
    freeze = _parse_utc(payload.get("freeze_time_utc"), "freeze_time_utc")
    observed = _parse_utc(snapshot.get("observed_at_utc"), "repository_snapshot.observed_at_utc")
    if observed > freeze:
        raise DailyFlowError("REPOSITORY_SNAPSHOT_AFTER_FREEZE", "仓库快照晚于冻结时点")
    source_name = str(snapshot.get("source_name") or "").strip()
    source_url = str(snapshot.get("source_url") or "").strip()
    if not source_name or not source_url:
        raise DailyFlowError("REPOSITORY_SNAPSHOT_MISSING", "仓库快照来源不完整")
    from football_v460_engine import current_season_history
    from platform_core import read_processed_matches
    competition_id = str(payload.get("competition_id") or "").strip()
    season = str(payload.get("season") or "").strip()
    _, history = current_season_history(read_processed_matches(competition_id), freeze, season=season)
    if not history:
        raise DailyFlowError("CURRENT_SEASON_HISTORY_EMPTY", "冻结时点前无本季历史比赛")
    return {
        "source_name": source_name,
        "source_url": source_url,
        "observed_at_utc": observed.isoformat(),
        "expected_history_matches": len(history),
        "latest_history_match_date": history[-1].date.date().isoformat(),
        "availability_semantics": "repository_snapshot_observed_before_freeze",
        "source_available_at_utc": None,
        "availability_evidence": "REPOSITORY_SNAPSHOT_OBSERVED_AT",
    }


def normalize_input(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(payload))
    _assert_no_target_result_leakage(value)
    mode = str(value.get("execution_mode") or "").strip()
    if mode not in VALID_EXECUTION_MODES:
        raise DailyFlowError("INPUT_INVALID", f"execution_mode不允许：{mode}")
    if value.get("settlement") != SETTLEMENT:
        raise DailyFlowError("INPUT_INVALID", f"settlement必须为{SETTLEMENT}")
    if not str(value.get("season") or "").strip():
        raise DailyFlowError("INPUT_INVALID", "必须显式提供season")
    kickoff = _parse_utc(value.get("kickoff_utc"), "kickoff_utc")
    freeze = _parse_utc(value.get("freeze_time_utc"), "freeze_time_utc")
    if freeze >= kickoff:
        raise DailyFlowError("INPUT_INVALID", "冻结时点必须早于开球")
    value["identity_evidence"] = _require_identity_evidence(value, freeze)
    value.setdefault("market_snapshot", None)
    value.setdefault("lineup_evidence", {"status": "unavailable"})
    value.setdefault("neutral_venue", False)
    value.setdefault("two_legged", False)
    if mode == "offline_repository_snapshot_demo":
        value["data_freshness_evidence"] = _derive_repository_freshness(value)
    elif not isinstance(value.get("data_freshness_evidence"), dict):
        raise DailyFlowError("DATA_FRESHNESS_EVIDENCE_MISSING", "live_user_supplied必须提供冻结时点前的数据完整性证据")
    return value


def _worst_state(*states: str) -> str:
    valid = [state for state in states if state]
    return max(valid, key=lambda item: STATE_ORDER.get(item, 99)) if valid else "不可用"


def _total_interval(distribution: Mapping[str, Any], mass: float = 0.80) -> dict[str, Any]:
    bins = [(index, str(index)) for index in range(7)] + [(7, "7+")]
    probs = [float(distribution.get(label, 0.0)) for _, label in bins]
    best: tuple[int, int, float] | None = None
    for left in range(len(probs)):
        running = 0.0
        for right in range(left, len(probs)):
            running += probs[right]
            if running + 1e-12 >= mass:
                candidate = (left, right, running)
                if best is None or (right - left, -running, left) < (best[1] - best[0], -best[2], best[0]):
                    best = candidate
                break
    if best is None:
        best = (0, 7, sum(probs))
    left, right, probability = best
    return {"lower": bins[left][1], "upper": bins[right][1], "probability": probability, "method": "minimum_contiguous_interval_at_least_80pct"}


def _top_scores(calculation: Mapping[str, Any], count: int = 3) -> list[dict[str, Any]]:
    rows = (calculation.get("model_audit") or {}).get("top_scores")
    if isinstance(rows, list) and rows:
        return [dict(row) for row in rows[:count]]
    matrix = (calculation.get("probabilities") or {}).get("score_matrix") or []
    normalized = [
        {"score": f"{int(cell['home_goals'])}-{int(cell['away_goals'])}", "probability": float(cell["probability"])}
        for cell in matrix if isinstance(cell, dict)
    ]
    return sorted(normalized, key=lambda item: (-item["probability"], item["score"]))[:count]


def render_report(normalized_input: Mapping[str, Any], context: Mapping[str, Any], calculation: Mapping[str, Any], validation: Mapping[str, Any], *, exact_head: str) -> dict[str, Any]:
    identity = context["match_identity"]
    probabilities = calculation.get("probabilities") or {}
    one_x_two = {key: float((probabilities.get("one_x_two") or {})[key]) for key in ("home", "draw", "away")}
    totals = {key: float((probabilities.get("total_goals") or {})[key]) for key in ("0", "1", "2", "3", "4", "5", "6", "7+")}
    top = _top_scores(calculation, 3)
    conclusions = calculation.get("conclusions") or {}
    model_audit = calculation.get("model_audit") or {}
    context_states = context.get("module_states") or {}
    calc_states = calculation.get("module_states") or {}
    demo = normalized_input.get("execution_mode") == "offline_repository_snapshot_demo"
    direction_key = max(one_x_two, key=one_x_two.get)
    direction = DIRECTION_ZH[direction_key]
    confidence = str(conclusions.get("confidence_grade") or "D")
    market_status = context.get("market_assessment") or {}
    lineup_status = context.get("lineup_assessment") or {}
    team_status = context.get("team_features") or {}
    freshness = calculation.get("data_freshness_audit") or {}
    probability_sum = float((model_audit.get("audit") or {}).get("probability_sum", 0.0))
    top3 = sum(float(row.get("probability", 0.0)) for row in top)
    gap = float(top[0]["probability"]) - float(top[1]["probability"]) if len(top) >= 2 else None
    exact_gate = bool(conclusions.get("exact_gate", False))
    total_rank = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    total_interval = _total_interval(totals)
    tail_5_plus = totals["5"] + totals["6"] + totals["7+"]
    identity_status = str((normalized_input.get("identity_evidence") or {}).get("status"))
    formal_direction = "弃权" if demo or validation.get("status") != "通过" else direction
    price_decision = "价格不可用" if not market_status.get("ev_gate") else "No Bet"
    final_line = f"{formal_direction}；可信等级{confidence}；{price_decision}。"
    modules = [
        {"模块": "赛事、口径与时点", "注册状态": "正式治理", "本场状态": context_states.get("competition_identity_and_time", "不可用"), "输入证据": f"{identity['competition_id']} / {identity['freeze_time_utc']}", "运行结果或降级原因": "90分钟含补时；身份状态=" + identity_status},
        {"模块": "数据质量与来源裁决", "注册状态": "正式治理", "本场状态": calc_states.get("data_freshness", "不可用"), "输入证据": str(freshness.get("source_name") or "无"), "运行结果或降级原因": f"历史场次={freshness.get('engine_history_matches')}，最新={freshness.get('engine_latest_history_match_date')}"},
        {"模块": "同步市场", "注册状态": "条件正式", "本场状态": context_states.get("synchronized_market", "不可用"), "输入证据": f"source_count={market_status.get('source_count', 0)}", "运行结果或降级原因": "完整、同步、可成交价格缺失" if not market_status.get("ev_gate") else "市场门通过"},
        {"模块": "球队、阵容与任务", "注册状态": "正式输入治理", "本场状态": _worst_state(context_states.get("team_dynamic_features", "不可用"), context_states.get("lineup_and_task", "不可用")), "输入证据": f"team_snapshot={team_status.get('snapshot_sha256')}；lineup={lineup_status.get('evidence_status')}", "运行结果或降级原因": "球队样本可用；阵容未核验" if lineup_status.get("status") != "通过" else "球队与阵容证据通过"},
        {"模块": "直接总进球主轨", "注册状态": "正式中心", "本场状态": calc_states.get("direct_total_goals", "不可用"), "输入证据": str(model_audit.get("parameter_source") or "正式参数"), "运行结果或降级原因": f"P(T)守恒={sum(totals.values()):.12f}"},
        {"模块": "条件净胜球", "注册状态": "正式中心", "本场状态": calc_states.get("conditional_goal_difference", "不可用"), "输入证据": "Beta-Binomial条件分配", "运行结果或降级原因": "条件轨已运行并映射统一矩阵"},
        {"模块": "统一比分矩阵", "注册状态": "正式中心输出", "本场状态": calc_states.get("unified_score_matrix", "不可用"), "输入证据": str((model_audit.get("audit") or {}).get("engine_sha256") or "无"), "运行结果或降级原因": f"概率和={probability_sum:.12f}；Top1={top[0]['score'] if top else None}"},
        {"模块": "市场协调", "注册状态": "条件启用", "本场状态": calc_states.get("market_coordination", "未启用"), "输入证据": "无完整同步多线市场", "运行结果或降级原因": "未启用，不凭空补市场约束"},
        {"模块": "价格、EV与No Bet", "注册状态": "条件正式", "本场状态": calc_states.get("price_ev_no_bet", "降级"), "输入证据": f"ev_gate={bool(market_status.get('ev_gate'))}", "运行结果或降级原因": price_decision},
    ]
    counterevidence = []
    if demo:
        counterevidence.append("赛程身份仅为离线确定性演示，未完成真实赛程核验")
    if lineup_status.get("status") != "通过":
        counterevidence.append("官方首发/预计首发证据不可用")
    if not market_status.get("ev_gate"):
        counterevidence.append("缺少同步、完整、可成交市场快照")
    counterevidence.append("正式引擎自身历史OOF存在平局识别偏弱风险")
    return {
        "schema_version": "DAILY-SINGLE-MATCH-V502-1.0", "current_version": CURRENT_VERSION,
        "engine_rule_version": calculation.get("rule_version"), "exact_head": exact_head,
        "execution_mode": normalized_input.get("execution_mode"), "provider_network_used": False,
        "external_request_attempts": 0, "new_model_or_weight": False,
        "A. 比赛与冻结时点": {
            "赛事": identity.get("competition_name_zh"), "competition_id": identity.get("competition_id"),
            "赛季": identity.get("season"), "主队": identity.get("home_team"), "客队": identity.get("away_team"),
            "开球时间_UTC": identity.get("kickoff_utc"), "冻结时点_UTC": identity.get("freeze_time_utc"),
            "结算口径": SETTLEMENT, "场地": identity.get("venue"), "中立场": identity.get("neutral_venue"),
            "两回合": identity.get("two_legged"), "身份证据状态": identity_status,
            "context_hash": context.get("context_hash"),
        },
        "B. 模块状态表": modules,
        "C. 数据及市场输入": {
            "本季历史场次": model_audit.get("history_matches"), "最新历史比赛日": model_audit.get("latest_history_match_date"),
            "球队有效样本_ESS": (model_audit.get("team_sample") or {}).get("ess"),
            "主队主场原始样本": (model_audit.get("team_sample") or {}).get("home_raw_matches"),
            "客队客场原始样本": (model_audit.get("team_sample") or {}).get("away_raw_matches"),
            "数据完整性审计": freshness, "市场审计": market_status, "阵容审计": lineup_status,
            "任务状态": context.get("task_state"),
        },
        "D. 计算与审计": {
            "正式runner": "run_formal_prediction_v460.py", "正式引擎状态": calculation.get("formal_status"),
            "验证状态": validation.get("status"), "验证错误": validation.get("errors", []),
            "验证警告": validation.get("warnings", []), "统一矩阵概率和": probability_sum,
            "1X2概率和": sum(one_x_two.values()), "总进球概率和": sum(totals.values()),
            "尾部聚合概率": (model_audit.get("audit") or {}).get("tail_aggregation_probability"),
            "OOF矩阵校准": calc_states.get("oof_matrix_calibration", "不可用"),
            "参数来源": model_audit.get("parameter_source"), "Top1与Top2比分差": gap,
            "Top3累计": top3, "EXACT门": exact_gate,
        },
        "E. 反证及失效条件": {
            "最大反证": counterevidence,
            "强制重算条件": [
                "比赛身份、开球时间、主客场或赛制变化", "新增已完成比赛使本季历史计数或最新日期变化",
                "官方首发、核心伤停、换帅或任务状态发生重大变化", "盘口跨档或出现完整同步可成交市场快照",
                "引擎、参数、校准器或CURRENT哈希变化",
            ],
        },
        "F. 三块正式结论": {
            "① 赛果": {
                "90分钟概率": {"主胜": one_x_two["home"], "平局": one_x_two["draw"], "客胜": one_x_two["away"]},
                "模型Top1": direction, "正式方向": formal_direction, "可信等级": confidence,
                "最大反证": counterevidence[0] if counterevidence else None,
                "公平价_压力价_最低可接受价": "价格不可用",
            },
            "② 总进球": {
                "0-7+分布": totals, "主选": total_rank[0][0], "次选": total_rank[1][0],
                "主区间": total_interval, "单一尾部_P5+": tail_5_plus, "大小球方向": "No Bet",
            },
            "③ 比分": {
                "唯一Top1": top[0] if top else None, "次比分": top[1] if len(top) > 1 else None,
                "Top3": top, "Top3累计": top3, "第一第二差距": gap,
                "EXACT门控": "未通过，仅作模型中心比分" if not exact_gate else "通过",
            },
        },
        "G. EV或No Bet": {"结论": "No Bet" if market_status.get("ev_gate") else "价格不可用 / No Bet", "原因": "缺少完整同步可成交价格，且不得用历史收盘价替代当前冻结价"},
        "H. 最终一句话": final_line,
    }


def report_to_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# 单场预测报告｜CURRENT V5.0.2", "", f"运行HEAD：`{report['exact_head']}`", f"执行模式：`{report['execution_mode']}`", ""]
    sections = ("A. 比赛与冻结时点", "B. 模块状态表", "C. 数据及市场输入", "D. 计算与审计", "E. 反证及失效条件", "F. 三块正式结论", "G. EV或No Bet", "H. 最终一句话")
    for section in sections:
        lines.append(f"## {section}")
        value = report[section]
        if section == "B. 模块状态表":
            lines.extend(["", "| 模块 | 注册状态 | 本场状态 | 输入证据 | 运行结果或降级原因 |", "|---|---|---|---|---|"])
            for row in value:
                cells = [str(row[key]).replace("|", "\\|") for key in ("模块", "注册状态", "本场状态", "输入证据", "运行结果或降级原因")]
                lines.append("| " + " | ".join(cells) + " |")
        elif section == "H. 最终一句话":
            lines.extend(["", f"**{value}**"])
        else:
            lines.extend(["", "```json", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), "```"])
        lines.append("")
    lines.extend(["---", "本报告由既有正式引擎生成；新日常入口未新增模型、未修改正式权重、未访问外部Provider。"])
    return "\n".join(lines) + "\n"


def run_flow(input_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DailyFlowError("OUTPUT_NOT_EMPTY", "output-dir必须为空，避免覆盖证据")
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = normalize_input(_read_json(input_path))
    normalized_path = output_dir / "normalized_input.json"
    _write_json(normalized_path, normalized)
    paths = {"context": output_dir / "context.json", "calculation": output_dir / "calculation.json", "validation": output_dir / "validation.json"}
    command = [
        sys.executable, str(FORMAL_RUNNER), "--input", str(normalized_path),
        "--context-output", str(paths["context"]), "--calculation-output", str(paths["calculation"]),
        "--validation-output", str(paths["validation"]), "--print-summary",
    ]
    process = subprocess.run(command, text=True, capture_output=True, check=False)
    (output_dir / "formal_runner.stdout.txt").write_text(process.stdout, encoding="utf-8")
    (output_dir / "formal_runner.stderr.txt").write_text(process.stderr, encoding="utf-8")
    if process.returncode != 0:
        receipt = {"schema_version": "DAILY-SINGLE-MATCH-RECEIPT-1.0", "status": "FAILED", "failure_class": "FORMAL_RUNNER_FAILED", "returncode": process.returncode, "exact_head": _git_head(), "provider_network_used": False, "external_request_attempts": 0}
        _write_json(output_dir / "receipt.json", receipt)
        raise DailyFlowError("FORMAL_RUNNER_FAILED", process.stdout.strip() or process.stderr.strip())
    context = _read_json(paths["context"])
    calculation = _read_json(paths["calculation"])
    validation = _read_json(paths["validation"])
    if validation.get("status") != "通过":
        raise DailyFlowError("FORMAL_VALIDATION_FAILED", str(validation.get("errors")))
    head = _git_head()
    report = render_report(normalized, context, calculation, validation, exact_head=head)
    _write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(report_to_markdown(report), encoding="utf-8")
    artifact_paths = [normalized_path, paths["context"], paths["calculation"], paths["validation"], output_dir / "report.json", output_dir / "report.md"]
    receipt = {
        "schema_version": "DAILY-SINGLE-MATCH-RECEIPT-1.0", "status": "PASS", "failure_class": None,
        "current_version": CURRENT_VERSION, "exact_head": head, "context_hash": context.get("context_hash"),
        "validation_status": validation.get("status"), "execution_mode": normalized.get("execution_mode"),
        "provider_network_used": False, "external_request_attempts": 0, "api_football_key_accessed": False,
        "model_training": 0, "formal_model_weight_change": 0,
        "files": {path.name: _sha256_file(path) for path in artifact_paths},
    }
    _write_json(output_dir / "receipt.json", receipt)
    return {"receipt": receipt, "report": report}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = run_flow(args.input, args.output_dir)
    except DailyFlowError as exc:
        print(f"DAILY_FLOW_ERROR[{exc.failure_class}]: {exc}", file=sys.stderr)
        return 2
    if args.print_summary:
        report = result["report"]
        print(json.dumps({
            "status": result["receipt"]["status"], "exact_head": result["receipt"]["exact_head"],
            "validation_status": result["receipt"]["validation_status"], "final_line": report["H. 最终一句话"],
            "provider_network_used": False, "external_request_attempts": 0,
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
