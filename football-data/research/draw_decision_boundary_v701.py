#!/usr/bin/env python3
"""Research-only E1 draw decision-boundary upper-bound study."""
from __future__ import annotations

import argparse, json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
FOOTBALL_DIR = HERE.parent
for p in (FOOTBALL_DIR / "engine", FOOTBALL_DIR / "validation", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from full_1x2_diagnostic_v700 import (  # noqa: E402
    CLASSES, CLASS_INDEX, EXCLUDED_COMPETITIONS, _competition_records,
    _diagnostics, _git_head,
)
from platform_core import ROOT, load_registry  # noqa: E402

OUT = ROOT.parent / "artifacts" / "research" / "draw_decision_boundary_e1"
TARGETS = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50)
EPS = 1e-15


def div(a: float, b: float) -> float:
    return a / b if b else 0.0


def ha(record: dict[str, Any]) -> str:
    return "home" if float(record["p_home"]) >= float(record["p_away"]) else "away"


def pred(record: dict[str, Any], family: str, value: float) -> str:
    h, d, a = (float(record[f"p_{x}"]) for x in CLASSES)
    best = max(h, a)
    if family == "p_draw_threshold":
        use_draw = d >= value
    elif family == "draw_margin_threshold":
        use_draw = d - best >= value
    elif family == "draw_logit_bias":
        use_draw = math.log(max(EPS, d)) + value >= math.log(max(EPS, best))
    else:
        raise ValueError(family)
    return "draw" if use_draw else ha(record)


def metrics(records: list[dict[str, Any]], predictor: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    cm = {x: {y: 0 for y in CLASSES} for x in CLASSES}
    pc, ac = Counter(), Counter()
    for r in records:
        actual, forecast = str(r["actual_outcome"]), predictor(r)
        cm[actual][forecast] += 1; pc[forecast] += 1; ac[actual] += 1
    per = {}
    for x in CLASSES:
        p, r = div(cm[x][x], pc[x]), div(cm[x][x], ac[x])
        per[x] = {"precision": p, "recall": r, "f1": div(2*p*r, p+r),
                  "predicted": pc[x], "actual": ac[x], "true_positive": cm[x][x]}
    n = len(records)
    return {
        "count": n,
        "one_x_two_accuracy": sum(cm[x][x] for x in CLASSES) / n,
        "balanced_accuracy": mean(per[x]["recall"] for x in CLASSES),
        "macro_f1": mean(per[x]["f1"] for x in CLASSES),
        "predicted_counts": {x: pc[x] for x in CLASSES},
        "actual_counts": {x: ac[x] for x in CLASSES},
        "predicted_shares": {x: pc[x] / n for x in CLASSES},
        "confusion_matrix_actual_rows_predicted_columns": cm,
        "per_class": per,
    }


def configs() -> list[dict[str, Any]]:
    return (
        [{"family": "p_draw_threshold", "parameter": round(.18 + .0025*i, 6)} for i in range(89)] +
        [{"family": "draw_margin_threshold", "parameter": round(-.35 + .005*i, 6)} for i in range(81)] +
        [{"family": "draw_logit_bias", "parameter": round(.025*i, 6)} for i in range(61)]
    )


def evaluate(records: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    family, value = str(cfg["family"]), float(cfg["parameter"])
    return {"family": family, "parameter": value,
            **metrics(records, lambda r: pred(r, family, value))}


def argmax_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return metrics(records, lambda r: max(CLASSES,
        key=lambda x: (float(r[f"p_{x}"]), -CLASS_INDEX[x])))


def choose(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda x: (float(x[key]), float(x["one_x_two_accuracy"]),
        float(x["per_class"]["draw"]["f1"]), -float(x["predicted_shares"]["draw"])))


def compact(x: dict[str, Any] | None) -> dict[str, Any] | None:
    if x is None:
        return None
    return {"family": x["family"], "parameter": x["parameter"],
            "one_x_two_accuracy": x["one_x_two_accuracy"],
            "balanced_accuracy": x["balanced_accuracy"], "macro_f1": x["macro_f1"],
            "draw": x["per_class"]["draw"],
            "predicted_draw_share": x["predicted_shares"]["draw"],
            "delta_accuracy_vs_argmax": x.get("delta_accuracy_vs_argmax")}


def scan(records: list[dict[str, Any]], grid: list[dict[str, Any]]) -> dict[str, Any]:
    base = argmax_metrics(records)
    rows = [evaluate(records, c) for c in grid]
    for x in rows:
        x["delta_accuracy_vs_argmax"] = x["one_x_two_accuracy"] - base["one_x_two_accuracy"]
        x["delta_macro_f1_vs_argmax"] = x["macro_f1"] - base["macro_f1"]
    constrained = {}
    for target in TARGETS:
        ok = [x for x in rows if x["per_class"]["draw"]["recall"] >= target]
        constrained[f"draw_recall_at_least_{target:.2f}"] = max(ok,
            key=lambda x: (x["one_x_two_accuracy"], x["per_class"]["draw"]["precision"], x["macro_f1"])) if ok else None
    frontier, best_acc = [], -1.0
    ordered = sorted(rows, key=lambda x: (x["per_class"]["draw"]["recall"], -x["one_x_two_accuracy"]))
    for x in reversed(ordered):
        if x["one_x_two_accuracy"] > best_acc + 1e-15:
            frontier.append(x); best_acc = x["one_x_two_accuracy"]
    frontier.reverse()
    proper = _diagnostics(records)
    return {
        "argmax_baseline": base,
        "proper_probability_scores": {
            "note": "Decision-only rules do not alter probabilities; LogLoss/Brier/RPS/ECE are invariant.",
            "mean_one_x_two_log_loss": proper["mean_one_x_two_log_loss"],
            "mean_one_x_two_brier": proper["mean_one_x_two_brier"],
            "mean_one_x_two_rps": proper["mean_one_x_two_rps"],
            "one_x_two_ece": proper["one_x_two_ece"],
        },
        "best_points": {
            "maximum_accuracy": choose(rows, "one_x_two_accuracy"),
            "maximum_macro_f1": choose(rows, "macro_f1"),
            "maximum_balanced_accuracy": choose(rows, "balanced_accuracy"),
            "maximum_draw_f1": max(rows, key=lambda x: (x["per_class"]["draw"]["f1"], x["one_x_two_accuracy"])),
            "constrained_accuracy": constrained,
        },
        "pareto_accuracy_vs_draw_recall": frontier,
        "all_rule_results": rows,
    }


def rolling(by_comp: dict[str, list[dict[str, Any]]], grid: list[dict[str, Any]]) -> dict[str, Any]:
    chosen_pairs, base_pairs, folds, skipped = [], [], [], []
    for cid, records in sorted(by_comp.items()):
        seasons = sorted({str(r["season"]) for r in records},
            key=lambda s: min(str(r["date"]) for r in records if str(r["season"]) == s))
        if len(seasons) < 2:
            skipped.append({"competition_id": cid, "reason": "fewer_than_two_oos_seasons"}); continue
        for i in range(1, len(seasons)):
            prior, test_season = seasons[:i], seasons[i]
            tune = [r for r in records if str(r["season"]) in prior]
            test = [r for r in records if str(r["season"]) == test_season]
            selected = choose([evaluate(tune, c) for c in grid], "macro_f1")
            if not selected or not test:
                continue
            family, value = selected["family"], selected["parameter"]
            tm = metrics(test, lambda r: pred(r, family, value)); bm = argmax_metrics(test)
            folds.append({"competition_id": cid, "test_season": test_season,
                "prior_oos_seasons": prior, "tuning_count": len(tune), "test_count": len(test),
                "selected_rule": {"family": family, "parameter": value},
                "test_metrics": tm, "test_argmax_baseline": bm})
            for r in test:
                chosen_pairs.append((r, pred(r, family, value)))
                base_pairs.append((r, max(CLASSES, key=lambda x: (float(r[f"p_{x}"]), -CLASS_INDEX[x]))))
    def pair_metrics(pairs: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
        lookup = {id(r): p for r, p in pairs}; rs = [r for r, _ in pairs]
        return metrics(rs, lambda r: lookup[id(r)])
    selected, base = pair_metrics(chosen_pairs), pair_metrics(base_pairs)
    delta = {}
    if selected.get("count"):
        for k in ("one_x_two_accuracy", "balanced_accuracy", "macro_f1"):
            delta[k] = selected[k] - base[k]
        for k in ("precision", "recall", "f1"):
            delta[f"draw_{k}"] = selected["per_class"]["draw"][k] - base["per_class"]["draw"][k]
    return {"selection_policy": "per-competition rolling OOS; maximize Macro-F1 on earlier OOS seasons",
            "first_oos_season_per_competition_is_tuning_only": True,
            "selected_policy_aggregate": selected, "same_match_argmax_baseline": base,
            "delta_selected_minus_argmax": delta, "folds": folds, "skipped": skipped}


def markdown(report: dict[str, Any]) -> str:
    s, r = report["retrospective_oracle_upper_bound"], report["rolling_oos_decision_policy"]
    b, p = s["argmax_baseline"], s["best_points"]
    return "\n".join([
        "# E1 Draw Decision-Boundary Upper-Bound Study", "",
        "Research-only. Original probabilities are unchanged; proper probability scores remain invariant.", "",
        f"- Repository HEAD: `{report['repository_head']}`", f"- 90-minute OOS records: {b['count']}",
        f"- Argmax accuracy: {b['one_x_two_accuracy']:.6f}", f"- Argmax Draw recall: {b['per_class']['draw']['recall']:.6f}", "",
        "## Retrospective oracle points", "",
        f"- Maximum accuracy: `{json.dumps(compact(p['maximum_accuracy']), ensure_ascii=False)}`",
        f"- Maximum Macro-F1: `{json.dumps(compact(p['maximum_macro_f1']), ensure_ascii=False)}`",
        f"- Maximum Draw F1: `{json.dumps(compact(p['maximum_draw_f1']), ensure_ascii=False)}`", "",
        "These are retrospective upper bounds, not executable OOS estimates.", "",
        "## Rolling OOS policy", "",
        f"- Selected: `{json.dumps(r['selected_policy_aggregate'], ensure_ascii=False)}`",
        f"- Argmax: `{json.dumps(r['same_match_argmax_baseline'], ensure_ascii=False)}`",
        f"- Delta: `{json.dumps(r['delta_selected_minus_argmax'], ensure_ascii=False)}`", "",
        "## Governance", "", "- Research/challenge status only.",
        "- No formal model, probability, weight, config, data, or CURRENT mutation.",
        "- Codex review and explicit user approval required before formal use.", ""
    ])


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--competition"); ap.add_argument("--output-dir", default=str(OUT)); ap.add_argument("--print-summary", action="store_true"); a = ap.parse_args()
    registered = [x["competition_id"] for x in load_registry()["competitions"]]
    if a.competition:
        if a.competition not in registered: raise SystemExit(f"unknown competition: {a.competition}")
        if a.competition in EXCLUDED_COMPETITIONS: raise SystemExit(f"excluded: {a.competition}")
        ids = [a.competition]
    else:
        ids = [x for x in registered if x not in EXCLUDED_COMPETITIONS]
    all_records, by_comp, failures, counts = [], defaultdict(list), [], {}
    for cid in ids:
        try:
            records, _, _ = _competition_records(cid)
            records = [{**r, "competition_id": cid} for r in records]
            all_records.extend(records); by_comp[cid].extend(records); counts[cid] = len(records)
        except Exception as exc:
            failures.append({"competition_id": cid, "error": f"{type(exc).__name__}: {exc}"})
    grid = configs(); upper = scan(all_records, grid); roll = rolling(by_comp, grid)
    report = {
        "schema_version": "E1-draw-decision-boundary-v1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repository_head": _git_head(), "status": "PASS" if not failures else "PARTIAL",
        "scope": {"research_only": True, "outcome_scope": "90_minutes_including_stoppage",
            "included_competitions": ids, "excluded_competitions": EXCLUDED_COMPETITIONS,
            "probability_model_mutation": False, "formal_weight_change": False,
            "formal_config_change": False, "data_change": False, "current_rule_change": False,
            "training_new_model": False},
        "method": {"probability_source": "D0 time-ordered OOS reconstruction",
            "configuration_count": len(grid), "retrospective_scan_status": "ORACLE_UPPER_BOUND_ONLY",
            "rolling_selection": "earlier OOS seasons only", "proper_scores": "invariant"},
        "competition_record_counts": counts,
        "retrospective_oracle_upper_bound": upper,
        "rolling_oos_decision_policy": roll,
        "failures": failures,
        "governance": {"candidate_status": "RESEARCH_CHALLENGE_ONLY",
            "formal_model_promotion": False, "codex_review_required": True, "user_approval_required": True},
    }
    out = Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / "draw_decision_boundary_v701.json", out / "draw_decision_boundary_v701.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mp.write_text(markdown(report), encoding="utf-8")
    if a.print_summary:
        print(json.dumps({"status": report["status"], "repository_head": report["repository_head"],
            "record_count": len(all_records), "configuration_count": len(grid), "failures": failures,
            "argmax_baseline": upper["argmax_baseline"],
            "best_points": {k: ({t: compact(v) for t, v in x.items()} if k == "constrained_accuracy" else compact(x)) for k, x in upper["best_points"].items()},
            "rolling_oos_decision_policy": roll, "json_report": str(jp), "markdown_report": str(mp)}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
