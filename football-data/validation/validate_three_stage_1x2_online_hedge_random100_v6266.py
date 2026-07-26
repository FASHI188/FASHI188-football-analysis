#!/usr/bin/env python3
"""V6.26.6 random100: leakage-safe online 1X2 expert aggregation.

Experts: (A) formal football-model 1X2 marginal, (B) de-vigged market 1X2 marginal.
For each competition, expert weights are updated only from previously settled eligible matches.
All matches on the same date are predicted before any update from that date.

Hedge rule (K=2): eta_n=sqrt(2*ln(K)/n), weights proportional to exp(-eta_n*cum_loss).
Loss is multiclass Brier/2 in [0,1]. The aggregated probability is the convex mixture of the two
expert distributions. No blend coefficient is fitted on the random100 sample.

Evaluation uses the exact fixed-seed random100 candidate order from V6.26.4. Total-goals head is
unchanged; exact score is reconciled last. Research only, formal weight zero.
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
import validate_decoupled_1x2_total_fusion_v6191 as dec  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
import validate_three_stage_random100_v6264 as r100  # noqa: E402
from football_v460_engine import load_config, predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import derive_score_marginals  # noqa: E402

OUT = ROOT / "manifests" / "v6_three_stage_1x2_online_hedge_random100_v6266_status.json"
K = 2
EPS = 1e-15


def avg(rows: list[dict[str, Any]], key: str) -> float | None:
    return sum(float(r[key]) for r in rows) / len(rows) if rows else None


def hedge_weights(n: int, losses: list[float]) -> tuple[list[float], float | None]:
    if n <= 0:
        return [0.5, 0.5], None
    eta = math.sqrt(2.0 * math.log(K) / n)
    logits = [-eta * float(x) for x in losses]
    m = max(logits)
    raw = [math.exp(x - m) for x in logits]
    z = sum(raw)
    return [x / z for x in raw], eta


def mix(a: list[float], b: list[float], w: list[float]) -> list[float]:
    q = [w[0] * float(x) + w[1] * float(y) for x, y in zip(a, b)]
    z = sum(q)
    return [x / z for x in q]


def norm_brier(p: list[float], actual: int) -> float:
    return 0.5 * sum((float(p[i]) - (1.0 if i == actual else 0.0)) ** 2 for i in range(3))


def main() -> int:
    cfg = load_config()
    candidates, packs = r100._enumerate_candidates(cfg)
    order = list(candidates)
    random.Random(r100.SEED).shuffle(order)
    frozen_order = order[: min(len(order), r100.ATTEMPT_POOL)]
    wanted = set(frozen_order)
    rank = {key: i for i, key in enumerate(frozen_order)}

    produced: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    failures = Counter()
    ledger_audit: dict[str, Any] = {}
    max_one = max_total = max_mass = 0.0

    # One reliability ledger per competition; it carries forward across seasons.
    for cid in dec.COMPS:
        n_settled = 0
        cumulative_losses = [0.0, 0.0]  # formal, market
        sample_weight_sum = [0.0, 0.0]
        sample_weight_count = 0

        for season in dec.SEASONS:
            pack = packs.get((season, cid))
            if not pack:
                continue
            bydate = defaultdict(list)
            for m in pack["matches"]:
                bydate[m.date].append(m)
            hist = []
            candidate_ids = pack["candidate_ids"]

            for dt in sorted(bydate):
                day = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
                pending_updates: list[tuple[list[float], list[float], int]] = []

                for m in day:
                    key = (season, cid, m.date.isoformat(), m.home_team, m.away_team)
                    if key not in candidate_ids:
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
                    formal_one = arch.one_vec(prior)
                    market_one = [float(x) for x in mk["one_x_two"]]
                    w, eta = hedge_weights(n_settled, cumulative_losses)
                    hedge_one = mix(formal_one, market_one, w)
                    actual_result = arch.result_index(m.home_goals, m.away_goals)
                    pending_updates.append((formal_one, market_one, actual_result))

                    if key not in wanted:
                        continue
                    marg = derive_score_marginals(prior)
                    total_dict = ou.project(marg["total_goals"], float(mk["p_over25"]))
                    if total_dict is None:
                        failures["total_projection"] += 1
                        continue
                    target_total = [float(total_dict[k]) for k in ou.TOTAL_KEYS]
                    try:
                        matrix, audit = core.reconcile(prior, hedge_one, target_total)
                    except Exception:
                        matrix, audit = None, {"converged": False}
                    if matrix is None or not audit.get("converged"):
                        failures["reconciliation"] += 1
                        continue

                    final_one = core.one_x_two_vector(matrix)
                    formal_total = arch.total_vec(prior)
                    new_total = core.total_goals_vector(matrix)
                    ti = min(7, m.home_goals + m.away_goals)
                    max_one = max(max_one, max(abs(a-b) for a,b in zip(final_one, hedge_one)))
                    max_total = max(max_total, max(abs(a-b) for a,b in zip(new_total, target_total)))
                    max_mass = max(max_mass, abs(sum(float(c["probability"]) for c in matrix)-1.0))
                    sample_weight_sum[0] += w[0]
                    sample_weight_sum[1] += w[1]
                    sample_weight_count += 1

                    produced[key] = {
                        "date": m.date.isoformat(), "competition_id": cid, "season": season,
                        "home": m.home_team, "away": m.away_team, "actual_score": [m.home_goals, m.away_goals],
                        "ledger_n_before": n_settled, "eta": eta,
                        "formal_weight": w[0], "market_weight": w[1],
                        "formal_1x2_top1": int(max(range(3), key=lambda i: formal_one[i]) == actual_result),
                        "market_1x2_top1": int(max(range(3), key=lambda i: market_one[i]) == actual_result),
                        "hedge_1x2_top1": int(max(range(3), key=lambda i: final_one[i]) == actual_result),
                        "formal_1x2_brier": arch.brier3(formal_one, actual_result),
                        "market_1x2_brier": arch.brier3(market_one, actual_result),
                        "hedge_1x2_brier": arch.brier3(final_one, actual_result),
                        "formal_1x2_logloss": arch.logloss3(formal_one, actual_result),
                        "market_1x2_logloss": arch.logloss3(market_one, actual_result),
                        "hedge_1x2_logloss": arch.logloss3(final_one, actual_result),
                        "formal_total_top1": int(max(range(8), key=lambda i: formal_total[i]) == ti),
                        "hedge_total_top1": int(max(range(8), key=lambda i: new_total[i]) == ti),
                        "formal_total_rps": arch.rps8(formal_total, ti),
                        "hedge_total_rps": arch.rps8(new_total, ti),
                        "formal_score_top1": arch.score_topk(prior, 1, m.home_goals, m.away_goals),
                        "hedge_score_top1": arch.score_topk(matrix, 1, m.home_goals, m.away_goals),
                        "formal_score_top3": arch.score_topk(prior, 3, m.home_goals, m.away_goals),
                        "hedge_score_top3": arch.score_topk(matrix, 3, m.home_goals, m.away_goals),
                    }

                # Update expert reliability only after every prediction on this date is frozen.
                for formal_one, market_one, actual_result in pending_updates:
                    cumulative_losses[0] += norm_brier(formal_one, actual_result)
                    cumulative_losses[1] += norm_brier(market_one, actual_result)
                    n_settled += 1
                for m in day:
                    hist.append(m)

        final_w, final_eta = hedge_weights(n_settled, cumulative_losses)
        ledger_audit[cid] = {
            "settled_eligible_matches": n_settled,
            "cumulative_losses": {"formal": cumulative_losses[0], "market": cumulative_losses[1]},
            "next_weights": {"formal": final_w[0], "market": final_w[1]},
            "next_eta": final_eta,
            "sample_mean_weights": {
                "formal": sample_weight_sum[0] / sample_weight_count if sample_weight_count else None,
                "market": sample_weight_sum[1] / sample_weight_count if sample_weight_count else None,
            },
        }

    rows = sorted(produced.values(), key=lambda r: rank[(r["season"], r["competition_id"], r["date"], r["home"], r["away"])])[:r100.TARGET]
    summary = {"count": len(rows)}
    for prefix in ("formal", "market", "hedge"):
        summary[f"{prefix}_1x2_top1"] = avg(rows, f"{prefix}_1x2_top1")
        summary[f"{prefix}_1x2_brier"] = avg(rows, f"{prefix}_1x2_brier")
        summary[f"{prefix}_1x2_logloss"] = avg(rows, f"{prefix}_1x2_logloss")
    for prefix in ("formal", "hedge"):
        summary[f"{prefix}_total_top1"] = avg(rows, f"{prefix}_total_top1")
        summary[f"{prefix}_total_rps"] = avg(rows, f"{prefix}_total_rps")
        summary[f"{prefix}_score_top1"] = avg(rows, f"{prefix}_score_top1")
        summary[f"{prefix}_score_top3"] = avg(rows, f"{prefix}_score_top3")
    summary["hedge_vs_formal_1x2_pp"] = ((summary["hedge_1x2_top1"] or 0)-(summary["formal_1x2_top1"] or 0))*100
    summary["hedge_vs_market_1x2_pp"] = ((summary["hedge_1x2_top1"] or 0)-(summary["market_1x2_top1"] or 0))*100
    summary["hedge_vs_formal_total_pp"] = ((summary["hedge_total_top1"] or 0)-(summary["formal_total_top1"] or 0))*100
    summary["hedge_vs_formal_score1_pp"] = ((summary["hedge_score_top1"] or 0)-(summary["formal_score_top1"] or 0))*100
    summary["sample_mean_formal_weight"] = avg(rows, "formal_weight")
    summary["sample_mean_market_weight"] = avg(rows, "market_weight")

    report = {
        "schema_version": "V6.26.6-three-stage-online-formal-market-hedge-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(rows)==r100.TARGET else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_FIXED_SEED_RANDOM100_LEAKAGE_SAFE_ONLINE_EXPERT_AGGREGATION",
        "seed": r100.SEED, "target": r100.TARGET, "candidate_population": len(candidates),
        "failures": dict(failures),
        "audit": {"max_1x2_residual": max_one, "max_total_residual": max_total, "max_mass_residual": max_mass,
                  "same_day_predict_before_update": True, "asian_handicap_primary_target": False},
        "summary": summary, "competition_ledgers": ledger_audit, "sample": rows,
        "governance": {"research_only": True, "formal_weight": 0, "current_rule_change": False,
                       "expert_weights_use_only_prior_settled_matches": True, "no_test_fitted_blend_weight": True,
                       "random100_is_diagnostic_only": True},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failures": report["failures"], "audit": report["audit"], "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if len(rows)==r100.TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
