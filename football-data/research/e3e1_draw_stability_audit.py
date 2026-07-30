#!/usr/bin/env python3
"""E3e-1: stability audit for the frozen E3e-0 pure 90-minute H/D/A models.

No new model, feature, threshold, class weight or hyperparameter. The six E3e-0
rolling-OOF variants are reproduced and sliced by league, season, league-season,
modeled-only rows, fixed B100 and contiguous chronological blocks.
Research-only; formal_weight=0; score/total/BTTS are not applicable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import e3d1_direct_td_joint_head as e3d1  # noqa: E402
import e3e0_draw_identifiability as e3e0  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3e1_draw_stability_audit"
GROUPS = ("A_MARKET", "B_TEAM", "C_COMBINED")
KINDS = ("LOGISTIC", "TREE")
BLOCK_SIZE = 500
MIN_CELL_ROWS = 150
MODELED_SEASONS = (2023, 2024, 2025)


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def safe_section(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    if len(rows) < MIN_CELL_ROWS:
        return {"status": "INSUFFICIENT_ROWS", "count": len(rows), "minimum_rows": MIN_CELL_ROWS}
    draws = sum(row["actual_outcome"] == "draw" for row in rows)
    if draws == 0 or draws == len(rows):
        return {"status": "INSUFFICIENT_CLASSES", "count": len(rows), "draws": draws}
    draw = e3e0.draw_diagnostics(rows, seed=seed)
    hda = e3e0.hda_metrics(rows, "e3e_probs")
    market = e3e0.hda_metrics(rows, "market_probs")
    return {
        "status": "EVALUATED", "count": len(rows), "draw": draw,
        "hda": hda, "market": market,
        "proper_score_delta_vs_market": {
            "logloss": float(hda["logloss"]) - float(market["logloss"]),
            "brier": float(hda["brier"]) - float(market["brier"]),
            "rps": float(hda["rps"]) - float(market["rps"]),
        },
    }


def split_sections(rows: list[dict[str, Any]], getter, seed: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(getter(row))].append(row)
    return {
        key: safe_section(subset, seed + index)
        for index, (key, subset) in enumerate(sorted(grouped.items()))
    }


def chronological_blocks(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (
        str(row["date"]), str(row["competition_id"]), str(row["match_key"])
    ))
    output = {}
    for start in range(0, len(ordered), BLOCK_SIZE):
        subset = ordered[start:start + BLOCK_SIZE]
        item = safe_section(subset, seed + start // BLOCK_SIZE)
        item["date_start"] = str(subset[0]["date"]) if subset else None
        item["date_end"] = str(subset[-1]["date"]) if subset else None
        output[f"block_{start // BLOCK_SIZE + 1:02d}"] = item
    return output


def pass_count(sections: dict[str, Any]) -> dict[str, Any]:
    evaluated = [item for item in sections.values() if item.get("status") == "EVALUATED"]
    passed = [
        item for item in evaluated
        if item["draw"]["identifiability_gate"]["identifiable_from_current_features"]
    ]
    return {
        "evaluated": len(evaluated), "passed": len(passed),
        "pass_rate": len(passed) / len(evaluated) if evaluated else None,
    }


def model_audit(predicted: list[dict[str, Any]], fixed_rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    per_league = split_sections(predicted, lambda row: row["competition_id"], seed + 100)
    per_season = split_sections(predicted, lambda row: int(row["season_start_year"]), seed + 200)
    per_league_season = split_sections(
        predicted,
        lambda row: f"{row['competition_id']}__{int(row['season_start_year'])}",
        seed + 300,
    )
    blocks = chronological_blocks(predicted, seed + 400)
    modeled_only = [row for row in predicted if row["e3e_status"] == "MODELED"]
    full = e3e0.model_section(predicted, seed)
    modeled = (
        e3e0.model_section(modeled_only, seed + 500)
        if len(modeled_only) >= MIN_CELL_ROWS else
        {"status": "INSUFFICIENT_ROWS", "count": len(modeled_only)}
    )
    fixed = e3e0.model_section(fixed_rows, seed + 600)
    league_summary = pass_count(per_league)
    season_summary = pass_count({
        key: value for key, value in per_season.items() if int(key) in MODELED_SEASONS
    })
    block_summary = pass_count(blocks)
    b100_pass = bool(fixed["draw_identifiability"]["identifiability_gate"]["identifiable_from_current_features"])
    cross_league = league_summary["evaluated"] == len(b100.BIG5) and league_summary["passed"] >= 3
    cross_season = season_summary["evaluated"] == len(MODELED_SEASONS) and season_summary["passed"] >= 2
    robust = bool(cross_league and cross_season and b100_pass)
    return {
        "full_oof": full, "modeled_only": modeled, "b100": fixed,
        "per_league": per_league, "per_season": per_season,
        "per_league_season": per_league_season,
        "chronological_blocks": blocks,
        "stability": {
            "league": league_summary, "modeled_season": season_summary,
            "chronological_blocks": block_summary,
            "cross_league_signal": cross_league,
            "cross_season_signal": cross_season,
            "b100_confirmation": b100_pass,
            "robustness_established": robust,
            "criteria": {
                "domain_pass": "PR-AUC bootstrap lower 95% and at least one preregistered candidate Precision Wilson lower 95% both exceed local prevalence",
                "cross_league": "at least 3 of 5 evaluated leagues pass",
                "cross_season": "at least 2 of modeled seasons 2023-2025 pass",
                "robustness": "cross-league AND cross-season AND fixed B100 confirmation",
            },
        },
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# E3e-1 Pure H/D/A Draw-Signal Stability Audit", "",
        "Research-only; formal_weight=0; no new model, feature, class weight, threshold, score, total or BTTS task.", "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Fixed OOF: {report['sample']['count']}",
        f"- Actual draws: {report['sample']['actual_draws']} ({report['sample']['draw_rate']:.4%})",
        f"- Fixed B100: {report['b100']['count']}", "",
        "## Stability summary", "",
        "| Model | Full PR-AUC | League pass | Season pass | Block pass | B100 | Robust |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for key, item in report["models"].items():
        full = item["audit"]["full_oof"]["draw_identifiability"]
        s = item["audit"]["stability"]
        lines.append(
            f"| {key} | {full['pr_auc']:.4%} | {s['league']['passed']}/{s['league']['evaluated']} | "
            f"{s['modeled_season']['passed']}/{s['modeled_season']['evaluated']} | "
            f"{s['chronological_blocks']['passed']}/{s['chronological_blocks']['evaluated']} | "
            f"{s['b100_confirmation']} | {s['robustness_established']} |"
        )
    best = report["verdict"]["best_full_model"]
    lines.extend(["", f"## Best full model: `{best}`", "", "### Per league", "",
        "| League | N | Base | PR-AUC | 95% lower | Top10 P/R | Pass | HDA Acc | Draw F1 | LL delta vs market |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ])
    for label, item in report["models"][best]["audit"]["per_league"].items():
        if item["status"] != "EVALUATED":
            lines.append(f"| {label} | {item['count']} | — | — | — | — | {item['status']} | — | — | — |")
            continue
        d, h = item["draw"], item["hda"]
        t10 = d["top_candidates"]["top_10pct"]
        lines.append(
            f"| {label} | {item['count']} | {d['prevalence']:.4%} | {d['pr_auc']:.4%} | "
            f"{d['pr_auc_bootstrap_95']['lower_95']:.4%} | {t10['precision']:.2%}/{t10['recall']:.2%} | "
            f"{d['identifiability_gate']['identifiable_from_current_features']} | {h['accuracy']:.4%} | "
            f"{h['draw_f1']:.4%} | {item['proper_score_delta_vs_market']['logloss']:+.6f} |"
        )
    lines.extend(["", "### Per season", "",
        "| Season | N | Base | PR-AUC | 95% lower | Top10 P/R | Pass | HDA Acc | Draw F1 | LL delta vs market |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ])
    for label, item in report["models"][best]["audit"]["per_season"].items():
        if item["status"] != "EVALUATED":
            lines.append(f"| {label} | {item['count']} | — | — | — | — | {item['status']} | — | — | — |")
            continue
        d, h = item["draw"], item["hda"]
        t10 = d["top_candidates"]["top_10pct"]
        lines.append(
            f"| {label} | {item['count']} | {d['prevalence']:.4%} | {d['pr_auc']:.4%} | "
            f"{d['pr_auc_bootstrap_95']['lower_95']:.4%} | {t10['precision']:.2%}/{t10['recall']:.2%} | "
            f"{d['identifiability_gate']['identifiable_from_current_features']} | {h['accuracy']:.4%} | "
            f"{h['draw_f1']:.4%} | {item['proper_score_delta_vs_market']['logloss']:+.6f} |"
        )
    v = report["verdict"]
    lines.extend(["", "## Verdict", "",
        f"- Cross-league signal: {v['cross_league_signal']}.",
        f"- Cross-season signal: {v['cross_season_signal']}.",
        f"- Fixed B100 confirmation: {v['b100_confirmation']}.",
        f"- Robust draw identifiability established: {v['robustness_established']}.",
        f"- Stop condition: `{v['stop_condition']}`.",
        "- No threshold is activated; no model is promoted; formal_weight remains 0.", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        base_rows, lineage = e3d1.build_records()
        evaluated, e3d1_folds = e3d1.expanding_oos(base_rows)
        if len(evaluated) != 6251:
            raise RuntimeError(f"fixed sample contract failed: {len(evaluated)} != 6251")
        actual_draws = sum(row["actual_outcome"] == "draw" for row in evaluated)
        by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in evaluated:
            by_competition[row["competition_id"]].append(row)
        fixed_source, selection = e3a.fixed_b100(by_competition)
        expected_b100 = b100.TARGET_PER_LEAGUE * len(b100.BIG5)
        if len(fixed_source) != expected_b100:
            raise RuntimeError(f"B100 contract failed: {len(fixed_source)}")
        models = {}
        for group_index, group in enumerate(GROUPS):
            for kind_index, kind in enumerate(KINDS):
                key = f"{group}__{kind}"
                predicted, folds = e3e0.rolling_oof(evaluated, group, kind)
                by_key = {row["match_key"]: row for row in predicted}
                fixed_rows = [by_key[row["match_key"]] for row in fixed_source]
                models[key] = {
                    "feature_group": group, "model_type": kind, "folds": folds,
                    "audit": model_audit(predicted, fixed_rows, e3e0.SEED + group_index * 1000 + kind_index * 100),
                }
        best_key = max(models, key=lambda key: models[key]["audit"]["full_oof"]["draw_identifiability"]["pr_auc"])
        stability = models[best_key]["audit"]["stability"]
        robust = bool(stability["robustness_established"])
        report = {
            "schema_version": "1.0", "research_status": "PASS",
            "repository_head": repository_head(),
            "experiment": "E3E1_PURE_HDA_DRAW_STABILITY_AUDIT",
            "scope": "90_minutes_including_stoppage",
            "sample": {"count": len(evaluated), "actual_draws": actual_draws,
                "draw_rate": actual_draws / len(evaluated), "fixed_sample_expected": 6251},
            "b100": {"count": len(fixed_source), "selection": selection},
            "models": models,
            "lineage": {**lineage, "e3d1_folds": e3d1_folds,
                "e3e0_model_and_feature_contract_reused": True},
            "verdict": {
                "best_full_model": best_key,
                "cross_league_signal": stability["cross_league_signal"],
                "cross_season_signal": stability["cross_season_signal"],
                "b100_confirmation": stability["b100_confirmation"],
                "robustness_established": robust,
                "stop_condition": (
                    "ROBUSTNESS_CONFIRMED; MAY DESIGN A SEPARATE OOS CANDIDATE WITHOUT ACTIVATING THRESHOLDS"
                    if robust else
                    "ROBUSTNESS_NOT_CONFIRMED; DO NOT TUNE THRESHOLDS; MOVE TO NEW PIT FEATURE RESEARCH"
                ),
            },
            "audit": {"new_model_family": False, "new_features": False,
                "new_hyperparameters": False, "class_weights_used": False,
                "manual_draw_threshold_used": False,
                "score_total_btts_output_or_gate_used": False,
                "target_season_used_for_training": False, "rolling_oof": True,
                "fixed_b100": True},
            "promotion": {"automatic_promotion": False, "formal_weight": 0,
                "status": "STABILITY_DIAGNOSTIC_ONLY"},
            "formal_mutation": {"model": 0, "data": 0, "config": 0,
                "current": 0, "formal_weight": 0},
            "failures": [],
        }
    except Exception as exc:
        report = {
            "schema_version": "1.0", "research_status": "FAIL",
            "repository_head": repository_head(),
            "experiment": "E3E1_PURE_HDA_DRAW_STABILITY_AUDIT",
            "failures": [{"error": f"{type(exc).__name__}: {exc}"}],
            "promotion": {"automatic_promotion": False, "formal_weight": 0,
                "status": "STABILITY_DIAGNOSTIC_ONLY"},
            "formal_mutation": {"model": 0, "data": 0, "config": 0,
                "current": 0, "formal_weight": 0},
        }
    json_path = output_dir / "e3e1_draw_stability_audit.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["research_status"] == "PASS":
        (output_dir / "e3e1_draw_stability_audit.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "research_status": report["research_status"],
            "repository_head": report.get("repository_head"),
            "sample": report.get("sample"), "verdict": report.get("verdict"),
            "stability": {key: value["audit"]["stability"] for key, value in report.get("models", {}).items()},
            "failures": report.get("failures")}, ensure_ascii=False, indent=2))
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
