#!/usr/bin/env python3
"""Build a robust one-off audit receipt from formal context/calculation outputs.

V4.6.4 reporting utility.  It fixes canonical 7+ aggregation and adds total-goal
peak-strength diagnostics.  It never changes model probabilities.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_receipt_utils_v464 import total_goals_0_7plus, total_peak_diagnostics
from platform_core import settle_home_handicap, settle_over_total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--calculation", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--home-handicap", type=float)
    parser.add_argument("--away-handicap", type=float)
    parser.add_argument("--over-lines", default="2.5,3.5")
    args = parser.parse_args()

    ctx = json.loads(Path(args.context).read_text(encoding="utf-8"))
    calc = json.loads(Path(args.calculation).read_text(encoding="utf-8"))
    val = json.loads(Path(args.validation).read_text(encoding="utf-8"))
    matrix = calc["probabilities"]["score_matrix"]

    def settle(line, fn):
        out = {"win": 0.0, "push": 0.0, "loss": 0.0}
        for cell in matrix:
            s = fn(int(cell["home_goals"]), int(cell["away_goals"]), line)
            p = float(cell["probability"])
            for key in out:
                out[key] += p * s[key]
        return out

    totals = total_goals_0_7plus(calc["probabilities"].get("total_goals"), matrix)
    peak = total_peak_diagnostics(totals)
    top = calc.get("model_audit", {}).get("top_scores", []) or calc.get("top_scores", []) or []
    if not top:
        ranked = sorted(matrix, key=lambda c: float(c["probability"]), reverse=True)
        top = [{"score": f"{c['home_goals']}-{c['away_goals']}", "probability": float(c["probability"])} for c in ranked[:10]]

    identity = ctx.get("match_identity", {})
    receipt = {
        "schema_version": "oneoff-prematch-audit-v464",
        "competition_id": identity.get("competition_id"),
        "home_team": identity.get("home_team"),
        "away_team": identity.get("away_team"),
        "kickoff_utc": identity.get("kickoff_utc"),
        "freeze_time_utc": identity.get("freeze_time_utc"),
        "postmatch_target_result_used_in_model": False,
        "validation_status": val.get("status"),
        "module_states": calc.get("module_states"),
        "context_module_states": ctx.get("module_states"),
        "summary": {
            "history_matches": calc.get("model_audit", {}).get("history_matches"),
            "latest_history_match_date": calc.get("model_audit", {}).get("latest_history_match_date"),
            "one_x_two": calc["probabilities"].get("one_x_two"),
            "total_goals_0_7plus": totals,
            "total_peak_diagnostics": peak,
            "btts_yes": calc["probabilities"].get("btts_yes"),
            "top_scores": top[:10],
            "top3_cumulative": sum(float(x["probability"]) for x in top[:3]),
            "top1_top2_gap": float(top[0]["probability"]) - float(top[1]["probability"]) if len(top) > 1 else None,
            "probability_sum": calc.get("model_audit", {}).get("audit", {}).get("probability_sum"),
            "engine_sha256": calc.get("model_audit", {}).get("audit", {}).get("engine_sha256"),
            "parameter_source": calc.get("model_audit", {}).get("parameter_source"),
            "nb_dispersion_k": calc.get("model_audit", {}).get("nb_dispersion_k"),
            "team_sample": calc.get("model_audit", {}).get("team_sample"),
            "confidence_grade": calc.get("conclusions", {}).get("confidence_grade"),
            "price_status": calc.get("conclusions", {}).get("price_status"),
            "lineup_status": ctx.get("lineup_assessment", {}).get("status"),
            "market_status": ctx.get("market_assessment", {}).get("status"),
        },
    }

    if args.home_handicap is not None:
        receipt["summary"]["home_handicap"] = {"line": args.home_handicap, **settle(args.home_handicap, settle_home_handicap)}
    if args.away_handicap is not None:
        receipt["summary"]["away_handicap"] = {
            "line": args.away_handicap,
            **settle(args.away_handicap, lambda hg, ag, line: settle_home_handicap(ag, hg, line)),
        }
    for raw in [item.strip() for item in args.over_lines.split(",") if item.strip()]:
        line = float(raw)
        receipt["summary"][f"over_{str(line).replace('.', '_')}"] = {"line": line, **settle(line, settle_over_total)}

    Path(args.output).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
