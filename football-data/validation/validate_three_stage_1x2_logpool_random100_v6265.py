#!/usr/bin/env python3
"""V6.26.5 fixed-seed random100: no-tune 1X2 log-opinion-pool ablation.

Purpose: test the first-layer design hypothesis without fitting a blend weight on the test set.
The challenger 1X2 head is the equal log pool
    q_i ∝ sqrt(p_formal_i * p_market_i)
which has no fitted coefficient. Total-goals head is unchanged from V6.26.4 and exact score is
again reconciled last by the V6.26 core. Same fixed candidate seed/order as V6.26.4.

This is retrospective diagnostic research only; historical market rows lack original quote times.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import three_stage_core_v6260 as core  # noqa: E402
import validate_architecture_order_v6190 as arch  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
import validate_three_stage_random100_v6264 as r100  # noqa: E402
from football_v460_engine import load_config, predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import derive_score_marginals  # noqa: E402

OUT = ROOT / "manifests" / "v6_three_stage_1x2_logpool_random100_v6265_status.json"
SEED = r100.SEED
TARGET = r100.TARGET
ATTEMPT_POOL = r100.ATTEMPT_POOL
EPS = 1e-15


def avg(rows: list[dict[str, Any]], key: str) -> float | None:
    return sum(float(r[key]) for r in rows) / len(rows) if rows else None


def log_pool_equal(a: list[float], b: list[float]) -> list[float]:
    raw = [math.sqrt(max(EPS, float(x)) * max(EPS, float(y))) for x, y in zip(a, b)]
    z = sum(raw)
    return [x / z for x in raw]


def main() -> int:
    cfg = load_config()
    candidates, packs = r100._enumerate_candidates(cfg)
    order = list(candidates)
    random.Random(SEED).shuffle(order)
    frozen_order = order[: min(len(order), ATTEMPT_POOL)]
    wanted = set(frozen_order)
    rank = {key: i for i, key in enumerate(frozen_order)}

    produced: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    failures = Counter()
    residuals = {"market": 0.0, "logpool": 0.0, "total": 0.0, "mass": 0.0}

    for (season, cid), pack in packs.items():
        if not wanted.intersection(pack["candidate_ids"]):
            continue
        bydate = defaultdict(list)
        for m in pack["matches"]:
            bydate[m.date].append(m)
        hist = []

        for dt in sorted(bydate):
            day = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
            for m in day:
                key = (season, cid, m.date.isoformat(), m.home_team, m.away_team)
                if key not in wanted:
                    continue
                mk = pack["lookup"].get((m.date.isoformat(), m.home_team, m.away_team))
                try:
                    pred = predict_from_history(
                        hist, cid, season, m.home_team, m.away_team, m.date,
                        selected_parameters=pack["params"], use_team_effects=True,
                    )
                except Exception:
                    pred = None
                if not pred:
                    failures["formal_prior"] += 1
                    continue

                prior = temperature_scale_matrix(pred["probabilities"]["score_matrix"], pack["temperature"])
                marg = derive_score_marginals(prior)
                total_dict = ou.project(marg["total_goals"], float(mk["p_over25"]))
                if total_dict is None:
                    failures["total_projection"] += 1
                    continue

                formal_one = arch.one_vec(prior)
                market_one = [float(x) for x in mk["one_x_two"]]
                logpool_one = log_pool_equal(formal_one, market_one)
                target_total = [float(total_dict[k]) for k in ou.TOTAL_KEYS]

                try:
                    market_matrix, ma = core.reconcile(prior, market_one, target_total)
                    logpool_matrix, la = core.reconcile(prior, logpool_one, target_total)
                except Exception:
                    failures["reconciliation_exception"] += 1
                    continue
                if not ma.get("converged") or not la.get("converged"):
                    failures["reconciliation"] += 1
                    continue

                market_final_one = core.one_x_two_vector(market_matrix)
                logpool_final_one = core.one_x_two_vector(logpool_matrix)
                formal_total = arch.total_vec(prior)
                market_total = core.total_goals_vector(market_matrix)
                logpool_total = core.total_goals_vector(logpool_matrix)
                ri = arch.result_index(m.home_goals, m.away_goals)
                ti = min(7, m.home_goals + m.away_goals)

                residuals["market"] = max(residuals["market"], max(abs(a-b) for a,b in zip(market_final_one, market_one)))
                residuals["logpool"] = max(residuals["logpool"], max(abs(a-b) for a,b in zip(logpool_final_one, logpool_one)))
                residuals["total"] = max(residuals["total"], max(abs(a-b) for a,b in zip(logpool_total, target_total)))
                residuals["mass"] = max(residuals["mass"], abs(sum(float(c["probability"]) for c in logpool_matrix)-1.0))

                produced[key] = {
                    "date": m.date.isoformat(), "competition_id": cid, "season": season,
                    "home": m.home_team, "away": m.away_team, "actual_score": [m.home_goals, m.away_goals],
                    "formal_1x2_top1": int(max(range(3), key=lambda i: formal_one[i]) == ri),
                    "market_1x2_top1": int(max(range(3), key=lambda i: market_final_one[i]) == ri),
                    "logpool_1x2_top1": int(max(range(3), key=lambda i: logpool_final_one[i]) == ri),
                    "formal_1x2_brier": arch.brier3(formal_one, ri),
                    "market_1x2_brier": arch.brier3(market_final_one, ri),
                    "logpool_1x2_brier": arch.brier3(logpool_final_one, ri),
                    "formal_1x2_logloss": arch.logloss3(formal_one, ri),
                    "market_1x2_logloss": arch.logloss3(market_final_one, ri),
                    "logpool_1x2_logloss": arch.logloss3(logpool_final_one, ri),
                    "formal_total_top1": int(max(range(8), key=lambda i: formal_total[i]) == ti),
                    "market_total_top1": int(max(range(8), key=lambda i: market_total[i]) == ti),
                    "logpool_total_top1": int(max(range(8), key=lambda i: logpool_total[i]) == ti),
                    "formal_total_rps": arch.rps8(formal_total, ti),
                    "market_total_rps": arch.rps8(market_total, ti),
                    "logpool_total_rps": arch.rps8(logpool_total, ti),
                    "formal_score_top1": arch.score_topk(prior, 1, m.home_goals, m.away_goals),
                    "market_score_top1": arch.score_topk(market_matrix, 1, m.home_goals, m.away_goals),
                    "logpool_score_top1": arch.score_topk(logpool_matrix, 1, m.home_goals, m.away_goals),
                    "formal_score_top3": arch.score_topk(prior, 3, m.home_goals, m.away_goals),
                    "market_score_top3": arch.score_topk(market_matrix, 3, m.home_goals, m.away_goals),
                    "logpool_score_top3": arch.score_topk(logpool_matrix, 3, m.home_goals, m.away_goals),
                }
            for m in day:
                hist.append(m)

    rows = sorted(produced.values(), key=lambda r: rank[(r["season"], r["competition_id"], r["date"], r["home"], r["away"])])[:TARGET]
    summary = {"count": len(rows)}
    for prefix in ("formal", "market", "logpool"):
        summary[f"{prefix}_1x2_top1"] = avg(rows, f"{prefix}_1x2_top1")
        summary[f"{prefix}_1x2_brier"] = avg(rows, f"{prefix}_1x2_brier")
        summary[f"{prefix}_1x2_logloss"] = avg(rows, f"{prefix}_1x2_logloss")
        summary[f"{prefix}_total_top1"] = avg(rows, f"{prefix}_total_top1")
        summary[f"{prefix}_total_rps"] = avg(rows, f"{prefix}_total_rps")
        summary[f"{prefix}_score_top1"] = avg(rows, f"{prefix}_score_top1")
        summary[f"{prefix}_score_top3"] = avg(rows, f"{prefix}_score_top3")
    summary["logpool_vs_formal_1x2_pp"] = ((summary["logpool_1x2_top1"] or 0)-(summary["formal_1x2_top1"] or 0))*100
    summary["logpool_vs_market_1x2_pp"] = ((summary["logpool_1x2_top1"] or 0)-(summary["market_1x2_top1"] or 0))*100
    summary["logpool_vs_formal_total_pp"] = ((summary["logpool_total_top1"] or 0)-(summary["formal_total_top1"] or 0))*100
    summary["logpool_vs_formal_score1_pp"] = ((summary["logpool_score_top1"] or 0)-(summary["formal_score_top1"] or 0))*100

    report = {
        "schema_version": "V6.26.5-three-stage-equal-logpool-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(rows)==TARGET else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_FIXED_SEED_RANDOM100_NO_TUNE_1X2_ABLATION",
        "seed": SEED, "target": TARGET, "candidate_population": len(candidates),
        "failures": dict(failures), "audit": residuals, "summary": summary, "sample": rows,
        "governance": {
            "research_only": True, "formal_weight": 0, "current_rule_change": False,
            "blend_weight_fitted_on_test": False, "equal_log_pool_fixed_ex_ante": True,
            "random100_is_diagnostic_only": True, "asian_handicap_primary_target": False,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failures": report["failures"], "audit": residuals, "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if len(rows)==TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
