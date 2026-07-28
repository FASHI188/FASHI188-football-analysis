#!/usr/bin/env python3
"""V6.47.8 capacity-isolation ablation for direct-total and conditional-margin tracks.

V6.47.7 showed opposite behaviour across the two factorized tracks: richer context
slightly improved exact-score proper score / 1X2 Brier, but worsened total-goal log loss
and total Top-1. This audit isolates model capacity instead of forcing the same context
level into both tracks.

Because V6.47.7 results are already known, this is explicitly POST-HOC retrospective
ablation. It may nominate a configuration for a NEW prospective epoch, but fixed1000
cannot be treated as independent promotion evidence for that nominated configuration.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import evaluate_direct_total_margin_matrix_v6477 as core

OUT = ROOT / "manifests" / "v6_total_margin_capacity_ablation_v6478_status.json"
LEVELS = ("comp", "strength", "full")


def mixed_predict(model: core.OnlineModel, r: dict[str, Any], total_level: str, margin_level: str) -> dict[str, Any]:
    cid = str(r["competition_id"])
    feat = model.features(r)
    pt = model.total_probs(cid, feat, total_level)
    matrix: dict[tuple[int, int], float] = {}
    result = {d: 0.0 for d in core.DIRECTIONS}
    parity_errors = 0
    for t in range(core.TOTAL_EXACT_MAX + 1):
        pd = model.margin_probs(cid, feat, t, margin_level)
        for d, q in pd.items():
            if (t + int(d)) % 2 != 0:
                parity_errors += 1
                continue
            h = (t + int(d)) // 2; a = (t - int(d)) // 2
            if h < 0 or a < 0:
                parity_errors += 1
                continue
            p = pt[t] * q
            matrix[(h, a)] = matrix.get((h, a), 0.0) + p
            result[core.result_direction(h, a)] += p
    tail_result = model.margin_probs(cid, feat, core.TOTAL_TAIL, margin_level)
    for d in core.DIRECTIONS:
        result[d] += pt[core.TOTAL_TAIL] * tail_result[d]
    total_report = core.report_total_probs(pt)
    rec_t = defaultdict(float)
    for (h, a), p in matrix.items():
        rec_t[h + a] += p
    return {
        "features": {"strength_bin": feat[0], "home_recent_total_bin": feat[1], "away_recent_total_bin": feat[2]},
        "internal_total": pt,
        "total": total_report,
        "matrix": matrix,
        "tail15plus": pt[core.TOTAL_TAIL],
        "tail15plus_result": tail_result,
        "result": result,
        "audit": {
            "probability_sum": sum(matrix.values()) + pt[core.TOTAL_TAIL],
            "probability_sum_residual": abs(sum(matrix.values()) + pt[core.TOTAL_TAIL] - 1.0),
            "internal_total_reconstruction_residual": max(abs(rec_t[t] - pt[t]) for t in range(core.TOTAL_EXACT_MAX + 1)),
            "parity_mapping_errors": parity_errors,
            "result_sum_residual": abs(sum(result.values()) - 1.0),
        },
    }


def main() -> int:
    benchmark = json.loads(core.BENCHMARK.read_text(encoding="utf-8"))
    bench_keys = {(str(r["competition_id"]), str(r["date"]), str(r["home_team"]), str(r["away_team"])) for r in benchmark.get("rows", [])}
    rows, source_meta = core.read_rows()
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_comp[str(r["competition_id"])].append(r)
    selected_seasons = benchmark.get("source_meta", {}).get("selected_seasons", {})
    model = core.OnlineModel()
    pred_rows: dict[tuple[str, str], list[dict[str, Any]]] = {(tl, ml): [] for tl in LEVELS for ml in LEVELS}

    for cid in core.base.TARGET_COMPETITIONS:
        comp_rows = sorted(by_comp.get(cid, []), key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        seasons = set(str(x) for x in selected_seasons.get(cid, []))
        cutoff_dates = [str(r["date"]) for r in comp_rows if str(r["season"]) in seasons]
        cutoff = min(cutoff_dates) if cutoff_dates else None
        days: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in comp_rows:
            days[str(r["date"])[:10]].append(r)
        for day in sorted(days):
            day_rows = days[day]
            frozen = [(r, model.features(r)) for r in day_rows]
            for r, _ in frozen:
                if cutoff and str(r["date"]) < cutoff:
                    continue
                if core.identity_key(r) not in bench_keys:
                    continue
                common = {"competition_id": cid, "date": r["date"], "home_team": r["home_team"], "away_team": r["away_team"], "hg": r["hg"], "ag": r["ag"]}
                for tl in LEVELS:
                    for ml in LEVELS:
                        pred_rows[(tl, ml)].append({**common, "prediction": mixed_predict(model, r, tl, ml)})
            model.update_batch(frozen)

    metrics: dict[str, Any] = {}
    for tl in LEVELS:
        for ml in LEVELS:
            name = f"total={tl}|margin={ml}"
            metrics[name] = core.evaluate_predictions(pred_rows[(tl, ml)], name)

    ref = metrics["total=comp|margin=comp"]
    admissible = []
    for name, m in metrics.items():
        audits = m.get("matrix_audit") or {}
        gates = {
            "coverage": m.get("count") == 1000,
            "probability_conservation": audits.get("max_probability_sum_residual", 1.0) <= 1e-12,
            "total_reconstruction": audits.get("max_internal_total_reconstruction_residual", 1.0) <= 1e-12,
            "parity": audits.get("parity_mapping_error_count", 1) == 0,
            "total_log_nonworse": m.get("total_log_loss", math.inf) <= ref.get("total_log_loss", -math.inf) + 1e-12,
            "total_rps_nonworse": m.get("total_rps", math.inf) <= ref.get("total_rps", -math.inf) + 1e-12,
            "total_top1_nonworse": m.get("total_top1_accuracy", -1.0) >= ref.get("total_top1_accuracy", 2.0) - 1e-12,
            "score_log_nonworse": m.get("exact_score_log_loss_non_tail", math.inf) <= ref.get("exact_score_log_loss_non_tail", -math.inf) + 1e-12,
            "score_top1_nonworse": m.get("exact_score_top1_accuracy", -1.0) >= ref.get("exact_score_top1_accuracy", 2.0) - 1e-12,
        }
        metrics[name]["dominance_gate_vs_comp_comp"] = gates
        if name != "total=comp|margin=comp" and all(gates.values()):
            admissible.append(name)

    # Post-hoc nomination only: among configurations that do not worsen any required
    # reference metric, minimize exact-score log loss, then result Brier, then maximize
    # exact-score Top3. This nomination is NOT independent OOS evidence.
    nominated = None
    if admissible:
        nominated = min(admissible, key=lambda n: (
            metrics[n]["exact_score_log_loss_non_tail"],
            metrics[n]["result_brier"],
            -metrics[n]["exact_score_top3_accuracy"],
            n,
        ))

    payload = {
        "schema_version": "V6.47.8-total-margin-capacity-isolation-r1",
        "generated_at_utc": core.now(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_RESEARCH_ABLATION",
        "classification": "POSTHOC_FIXED1000_CAPACITY_ABLATION_FORMAL_WEIGHT_0",
        "reason": "V6.47.7 richer context improved score proper score but worsened total-goal log loss/Top1; isolate total and margin capacity.",
        "levels": list(LEVELS),
        "reference": "total=comp|margin=comp",
        "metrics": metrics,
        "admissible_nonworse_configurations": admissible,
        "nominated_forward_configuration": nominated,
        "decision": "FREEZE_NEW_FORWARD_CHALLENGE_ONLY" if nominated else "NO_CONFIGURATION_DOMINATES_REFERENCE",
        "governance": {
            "fixed1000_is_no_longer_pristine_for_configuration_nomination": True,
            "nomination_is_posthoc_research_only": True,
            "nomination_cannot_be_called_oos_promotion": True,
            "fresh_postfreeze_forward_required": True,
            "formal_weight": 0,
            "automatic_promotion": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
        "data_meta": source_meta,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"admissible": admissible, "nominated": nominated, "metrics": {k: {x: v for x, v in m.items() if x in {"total_top1_accuracy","total_log_loss","total_rps","exact_score_top1_accuracy","exact_score_top3_accuracy","exact_score_log_loss_non_tail","result_top1_accuracy","result_brier"}} for k, m in metrics.items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
