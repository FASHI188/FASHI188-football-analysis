#!/usr/bin/env python3
"""V6.26.4 fixed-seed random-100 diagnostic for the rebuilt three-stage core.

Sampling contract
-----------------
1. Enumerate legal pre-match rows without looking at outcomes.
2. Shuffle once with a fixed seed.
3. Attempt candidates in that frozen order until 100 successful predictions are produced.
4. No tuning, no re-ranking by result, no same-day leakage.

Candidate architecture
----------------------
Stage 1: independent de-vigged 1X2 market marginal.
Stage 2: formal direct 0-7+ total prior projected only to de-vigged O/U2.5.
Stage 3: V6.26 score reconciliation preserving both accepted marginals.

Asian handicap is intentionally excluded as a primary target/hard constraint.
Historical market rows lack original quote timestamps, so this is retrospective research only.
"""
from __future__ import annotations

import json
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
import validate_joint_market_ipf_crossseason_v6164 as base  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
from football_v460_engine import load_config, predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import derive_score_marginals, read_processed_matches  # noqa: E402

OUT = ROOT / "manifests" / "v6_three_stage_random100_v6264_status.json"
SEED = 6260100
TARGET = 100
ATTEMPT_POOL = 170


def _avg(rows: list[dict[str, Any]], key: str) -> float | None:
    return sum(float(r[key]) for r in rows) / len(rows) if rows else None


def _enumerate_candidates(cfg: dict[str, Any]):
    warmc = int(cfg["validation"]["warmup_competition_matches"])
    warmt = int(cfg["validation"]["warmup_team_matches"])
    candidates: list[tuple[str, str, str, str, str]] = []
    packs: dict[tuple[str, str], dict[str, Any]] = {}

    for season in dec.SEASONS:
        for cid in dec.COMPS:
            lookup = base.market_lookup(cid, season)
            params = ou.params_by_season(cid).get(season)
            if not params:
                continue
            matches = [m for m in read_processed_matches(cid) if str(m.season) == season]
            bydate = defaultdict(list)
            for m in matches:
                bydate[m.date].append(m)
            hist = []
            home_count = Counter()
            away_count = Counter()
            ids = []
            for dt in sorted(bydate):
                day = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
                for m in day:
                    mk = lookup.get((m.date.isoformat(), m.home_team, m.away_team))
                    if (
                        len(hist) >= warmc
                        and home_count[m.home_team] >= warmt
                        and away_count[m.away_team] >= warmt
                        and mk
                    ):
                        key = (season, cid, m.date.isoformat(), m.home_team, m.away_team)
                        candidates.append(key)
                        ids.append(key)
                # Same-day outcomes become history only after every match that day is screened.
                for m in day:
                    hist.append(m)
                    home_count[m.home_team] += 1
                    away_count[m.away_team] += 1
            packs[(season, cid)] = {
                "lookup": lookup,
                "params": params,
                "matches": matches,
                "candidate_ids": set(ids),
                "temperature": ou.calibrator(cid, season),
            }
    return candidates, packs


def _score_metrics(matrix: list[dict[str, Any]], hg: int, ag: int) -> tuple[int, int]:
    return arch.score_topk(matrix, 1, hg, ag), arch.score_topk(matrix, 3, hg, ag)


def main() -> int:
    cfg = load_config()
    candidates, packs = _enumerate_candidates(cfg)
    order = list(candidates)
    random.Random(SEED).shuffle(order)
    frozen_attempt_order = order[: min(len(order), ATTEMPT_POOL)]
    wanted = set(frozen_attempt_order)
    rank = {key: i for i, key in enumerate(frozen_attempt_order)}

    produced: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    failures = Counter()
    max_one_residual = 0.0
    max_total_residual = 0.0
    max_mass_residual = 0.0

    for (season, cid), pack in packs.items():
        if not wanted.intersection(pack["candidate_ids"]):
            continue
        bydate = defaultdict(list)
        for m in pack["matches"]:
            bydate[m.date].append(m)
        hist = []
        home_count = Counter()
        away_count = Counter()

        for dt in sorted(bydate):
            day = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
            for m in day:
                key = (season, cid, m.date.isoformat(), m.home_team, m.away_team)
                if key not in wanted:
                    continue
                mk = pack["lookup"].get((m.date.isoformat(), m.home_team, m.away_team))
                try:
                    pred = predict_from_history(
                        hist,
                        cid,
                        season,
                        m.home_team,
                        m.away_team,
                        m.date,
                        selected_parameters=pack["params"],
                        use_team_effects=True,
                    )
                except Exception:
                    pred = None
                if not pred:
                    failures["formal_prior"] += 1
                    continue

                prior = temperature_scale_matrix(pred["probabilities"]["score_matrix"], pack["temperature"])
                marg = derive_score_marginals(prior)
                target_total_dict = ou.project(marg["total_goals"], float(mk["p_over25"]))
                if target_total_dict is None:
                    failures["total_projection"] += 1
                    continue

                target_one = [float(x) for x in mk["one_x_two"]]
                target_total = [float(target_total_dict[k]) for k in ou.TOTAL_KEYS]
                try:
                    candidate, audit = core.reconcile(prior, target_one, target_total)
                except Exception:
                    candidate, audit = None, {"converged": False}
                if candidate is None or not audit.get("converged"):
                    failures["reconciliation"] += 1
                    continue

                formal_one = arch.one_vec(prior)
                new_one = core.one_x_two_vector(candidate)
                formal_total = arch.total_vec(prior)
                new_total = core.total_goals_vector(candidate)
                actual_result = arch.result_index(m.home_goals, m.away_goals)
                actual_total = min(7, m.home_goals + m.away_goals)
                formal_s1, formal_s3 = _score_metrics(prior, m.home_goals, m.away_goals)
                new_s1, new_s3 = _score_metrics(candidate, m.home_goals, m.away_goals)

                one_resid = max(abs(a - b) for a, b in zip(new_one, target_one))
                total_resid = max(abs(a - b) for a, b in zip(new_total, target_total))
                mass_resid = abs(sum(float(c["probability"]) for c in candidate) - 1.0)
                max_one_residual = max(max_one_residual, one_resid)
                max_total_residual = max(max_total_residual, total_resid)
                max_mass_residual = max(max_mass_residual, mass_resid)

                produced[key] = {
                    "date": m.date.isoformat(),
                    "competition_id": cid,
                    "season": season,
                    "home": m.home_team,
                    "away": m.away_team,
                    "actual_score": [m.home_goals, m.away_goals],
                    "formal_1x2_top1": int(max(range(3), key=lambda i: formal_one[i]) == actual_result),
                    "new_1x2_top1": int(max(range(3), key=lambda i: new_one[i]) == actual_result),
                    "formal_1x2_brier": arch.brier3(formal_one, actual_result),
                    "new_1x2_brier": arch.brier3(new_one, actual_result),
                    "formal_1x2_logloss": arch.logloss3(formal_one, actual_result),
                    "new_1x2_logloss": arch.logloss3(new_one, actual_result),
                    "formal_total_top1": int(max(range(8), key=lambda i: formal_total[i]) == actual_total),
                    "new_total_top1": int(max(range(8), key=lambda i: new_total[i]) == actual_total),
                    "formal_total_rps": arch.rps8(formal_total, actual_total),
                    "new_total_rps": arch.rps8(new_total, actual_total),
                    "formal_score_top1": formal_s1,
                    "new_score_top1": new_s1,
                    "formal_score_top3": formal_s3,
                    "new_score_top3": new_s3,
                    "iterations": int(audit.get("iterations") or 0),
                    "max_residual": float(audit.get("max_residual") or 0.0),
                }

            # Strict daily PIT: update history only after all same-day predictions are complete.
            for m in day:
                hist.append(m)
                home_count[m.home_team] += 1
                away_count[m.away_team] += 1

    rows = sorted(produced.values(), key=lambda r: rank[(r["season"], r["competition_id"], r["date"], r["home"], r["away"])])[:TARGET]
    by_comp = Counter(r["competition_id"] for r in rows)
    summary = {
        "count": len(rows),
        "formal_1x2_top1": _avg(rows, "formal_1x2_top1"),
        "new_1x2_top1": _avg(rows, "new_1x2_top1"),
        "delta_1x2_top1_pp": ((_avg(rows, "new_1x2_top1") or 0.0) - (_avg(rows, "formal_1x2_top1") or 0.0)) * 100.0,
        "formal_1x2_brier": _avg(rows, "formal_1x2_brier"),
        "new_1x2_brier": _avg(rows, "new_1x2_brier"),
        "formal_1x2_logloss": _avg(rows, "formal_1x2_logloss"),
        "new_1x2_logloss": _avg(rows, "new_1x2_logloss"),
        "formal_total_top1": _avg(rows, "formal_total_top1"),
        "new_total_top1": _avg(rows, "new_total_top1"),
        "delta_total_top1_pp": ((_avg(rows, "new_total_top1") or 0.0) - (_avg(rows, "formal_total_top1") or 0.0)) * 100.0,
        "formal_total_rps": _avg(rows, "formal_total_rps"),
        "new_total_rps": _avg(rows, "new_total_rps"),
        "formal_score_top1": _avg(rows, "formal_score_top1"),
        "new_score_top1": _avg(rows, "new_score_top1"),
        "delta_score_top1_pp": ((_avg(rows, "new_score_top1") or 0.0) - (_avg(rows, "formal_score_top1") or 0.0)) * 100.0,
        "formal_score_top3": _avg(rows, "formal_score_top3"),
        "new_score_top3": _avg(rows, "new_score_top3"),
    }

    report = {
        "schema_version": "V6.26.4-three-stage-fixed-seed-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(rows) == TARGET else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_FIXED_SEED_RANDOM100_DIAGNOSTIC_NO_ORIGINAL_MARKET_TIMESTAMP",
        "seed": SEED,
        "target": TARGET,
        "candidate_population": len(candidates),
        "attempt_pool": min(len(order), ATTEMPT_POOL),
        "failures": dict(failures),
        "competition_sample_counts": dict(sorted(by_comp.items())),
        "audit": {
            "max_1x2_constraint_residual": max_one_residual,
            "max_total_constraint_residual": max_total_residual,
            "max_probability_sum_residual": max_mass_residual,
            "asian_handicap_used_as_primary_target": False,
            "same_day_history_frozen": True,
        },
        "summary": summary,
        "sample": rows,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
            "candidate_enumeration_uses_no_outcomes": True,
            "fixed_seed_before_evaluation": True,
            "random100_is_diagnostic_only": True,
            "historical_market_quotes_lack_original_timestamp": True,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "candidate_population": report["candidate_population"],
        "failures": report["failures"],
        "audit": report["audit"],
        "summary": summary,
    }, ensure_ascii=False, indent=2))
    return 0 if len(rows) == TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
