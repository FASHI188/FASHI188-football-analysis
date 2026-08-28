#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
Q0DIR = ROOT / "experiments" / "r43q0_sharp_market_score_base"
R0DIR = ROOT / "experiments" / "r43r0_strong_shrink_football_residual"
for p in (Q0DIR, R0DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run_r43q0 as q0  # noqa: E402
import run_r43r0 as r0  # noqa: E402

MARKET_LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
FOOTBALL_LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
OUT = HERE / "results" / "summary_r43s0_draw_probability_path_audit.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def iso(x: str) -> datetime:
    dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def correct_folds(rows: list[dict], k: int) -> list[list[dict]]:
    groups = []
    cur_key = None
    cur = []
    for row in rows:
        key = row["kickoff_utc"]
        if cur_key is None or key == cur_key:
            cur.append(row); cur_key = key
        else:
            groups.append(cur); cur = [row]; cur_key = key
    if cur:
        groups.append(cur)
    if len(groups) < k:
        raise RuntimeError(f"insufficient kickoff groups {len(groups)}")
    total = sum(len(g) for g in groups)
    folds = []; acc = []; cumulative = 0
    for g in groups:
        boundary = total * (len(folds) + 1) / k
        if len(folds) < k - 1 and acc and cumulative + len(g) > boundary:
            folds.append(acc); acc = []
        acc.extend(g); cumulative += len(g)
    if acc:
        folds.append(acc)
    if len(folds) != k or any(not x for x in folds):
        raise RuntimeError(f"bad fold sizes {[len(x) for x in folds]}")
    return folds


def build_q0_rows() -> list[dict]:
    ledger = load(MARKET_LEDGER)
    preds = {}; settled = {}
    for e in ledger.get("events", []):
        mid = str(e.get("match_id"))
        if e.get("event_type") == "MARKET_PREDICTION_FROZEN":
            preds[mid] = e
        elif e.get("event_type") == "RESULT_SETTLED":
            settled[mid] = e
    rows = []
    for mid, se in settled.items():
        pe = preds.get(mid)
        if pe is None:
            continue
        pld = pe["payload"]; fx = pld["fixture_identity"]; surf = pld["frozen_surfaces"]
        kickoff = iso(fx["kickoff_at"]); frozen = iso(pe["event_timestamp_utc"])
        if not frozen < kickoff:
            continue
        y = str(se["payload"]["result"]["actual_result"])
        if y not in q0.CLASSES:
            continue
        market = q0.devig_1x2(surf["one_x_two_odds"])
        lh, la, fit_obj = q0.infer_lambdas(surf["asian_handicap"], surf["over_under"], market)
        matrix = q0.score_matrix(lh, la)
        raw = q0.matrix_1x2(matrix)
        rows.append({
            "match_id": mid, "kickoff_utc": kickoff.isoformat(), "y": y,
            "market": market, "latent_raw": raw, "matrix_raw": matrix,
            "lambda_home": lh, "lambda_away": la, "fit_objective": fit_obj,
        })
    rows.sort(key=lambda x: (x["kickoff_utc"], x["match_id"]))
    seed = rows[:q0.SEED_SETTLED]
    scored = rows[q0.SEED_SETTLED:]
    history = list(seed)
    out = []
    for fold in correct_folds(scored, q0.FOLDS):
        ab = q0.fit_draw_cal(history)
        for row in fold:
            sharp, matrix_sharp = q0.apply_draw_cal(row, ab)
            row["sharp"] = sharp
            row["matrix_sharp"] = matrix_sharp
            row["draw_cal_intercept"] = ab[0]
            row["draw_cal_slope"] = ab[1]
        out.extend(fold); history.extend(fold)
    return out


def add_residual(qrows: list[dict]) -> tuple[list[dict], dict]:
    ml = load(MARKET_LEDGER); fl = load(FOOTBALL_LEDGER)
    mpred = {}; settled = {}
    for e in ml.get("events", []):
        mid = str(e.get("match_id"))
        if e.get("event_type") == "MARKET_PREDICTION_FROZEN": mpred[mid] = e
        elif e.get("event_type") == "RESULT_SETTLED": settled[mid] = e
    fby = {}
    for e in fl.get("events", []):
        if e.get("event_type") == "PREDICTION_FROZEN":
            fby.setdefault(r0.identity(e["payload"]), e)
    matched = []
    for mid, se in settled.items():
        me = mpred.get(mid)
        if me is None: continue
        fe = fby.get(r0.identity(me["payload"]))
        if fe is None: continue
        kickoff = iso(me["payload"]["fixture_identity"]["kickoff_at"])
        if not (iso(me["event_timestamp_utc"]) < kickoff and iso(fe["event_timestamp_utc"]) < kickoff):
            continue
        y = str(se["payload"]["result"]["actual_result"])
        if y not in r0.CLASSES: continue
        matched.append({
            "match_id": mid, "kickoff_utc": kickoff.replace(microsecond=0).isoformat(), "y": y,
            "market": r0.probs(me["payload"]["prediction"]["probabilities"]),
            "football": r0.probs(fe["payload"]["prediction"]["formal_probabilities"]),
        })
    matched.sort(key=lambda x: (x["kickoff_utc"], x["match_id"]))
    seed = matched[:r0.SEED_N]; scored = matched[r0.SEED_N:]
    history = list(seed); betas = []
    for fold in r0.chronological_folds(scored, r0.FOLDS):
        beta = r0.fit_beta(history); betas.append(beta)
        for row in fold:
            row["residual"] = r0.residual_prob(row["market"], row["football"], beta)
            row["beta"] = beta
        history.extend(fold)
    by_mid = {r["match_id"]: r for r in scored}
    common = []
    for row in qrows:
        rr = by_mid.get(row["match_id"])
        if rr is not None:
            row["football"] = rr["football"]
            row["residual"] = rr["residual"]
            row["residual_beta"] = rr["beta"]
            common.append(row)
    return common, {"matched_total": len(matched), "scored_residual": len(scored), "common_q0_scored": len(common), "betas": betas}


def draw_rank(p: dict[str, float]) -> int:
    d = float(p["draw"])
    return 1 + sum(1 for k in ("home", "away") if float(p[k]) > d + 1e-15)


def stage_summary(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"n": 0}
    ranks = Counter(); ranks_draw = Counter()
    probs_all = []; probs_draw = []; gaps = []; gaps_draw = []; top_draw = 0; top_draw_actual = 0; actual_draw_n = 0
    for row in rows:
        p = row[key]; rank = draw_rank(p); ranks[str(rank)] += 1
        pd = float(p["draw"]); gap = max(float(p["home"]), float(p["away"])) - pd
        probs_all.append(pd); gaps.append(gap)
        if r0.top1(p) == "draw": top_draw += 1
        if row["y"] == "draw":
            actual_draw_n += 1; ranks_draw[str(rank)] += 1; probs_draw.append(pd); gaps_draw.append(gap)
            if r0.top1(p) == "draw": top_draw_actual += 1
    return {
        "n": len(rows), "actual_draw_n": actual_draw_n,
        "mean_draw_probability": float(np.mean(probs_all)),
        "draw_top1_count": top_draw, "draw_top1_rate": top_draw / len(rows),
        "draw_rank_counts": dict(ranks),
        "mean_draw_gap_to_best_non_draw": float(np.mean(gaps)),
        "actual_draw_only": {
            "n": actual_draw_n,
            "mean_draw_probability": float(np.mean(probs_draw)) if probs_draw else None,
            "draw_top1_count": top_draw_actual,
            "draw_rank_counts": dict(ranks_draw),
            "mean_draw_gap_to_best_non_draw": float(np.mean(gaps_draw)) if gaps_draw else None,
            "min_draw_gap_to_best_non_draw": float(np.min(gaps_draw)) if gaps_draw else None,
            "max_draw_probability": float(np.max(probs_draw)) if probs_draw else None,
        },
    }


def transition(rows: list[dict], a: str, b: str) -> dict:
    all_inc = all_dec = 0; draw_inc = draw_dec = 0; activated = deactivated = 0; rank_better = rank_worse = 0; draw_n = 0
    for row in rows:
        pa = float(row[a]["draw"]); pb = float(row[b]["draw"])
        if pb > pa + 1e-15: all_inc += 1
        elif pb < pa - 1e-15: all_dec += 1
        if row["y"] == "draw":
            draw_n += 1
            if pb > pa + 1e-15: draw_inc += 1
            elif pb < pa - 1e-15: draw_dec += 1
            ra, rb = draw_rank(row[a]), draw_rank(row[b])
            if rb < ra: rank_better += 1
            elif rb > ra: rank_worse += 1
            ta = r0.top1(row[a]) == "draw"; tb = r0.top1(row[b]) == "draw"
            if (not ta) and tb: activated += 1
            elif ta and (not tb): deactivated += 1
    return {
        "n": len(rows), "actual_draw_n": draw_n,
        "all_draw_probability_increased": all_inc, "all_draw_probability_decreased": all_dec,
        "actual_draw_probability_increased": draw_inc, "actual_draw_probability_decreased": draw_dec,
        "actual_draw_rank_improved": rank_better, "actual_draw_rank_worsened": rank_worse,
        "actual_draw_top1_activated": activated, "actual_draw_top1_deactivated": deactivated,
    }


def run() -> dict:
    qrows = build_q0_rows()
    common, residual_meta = add_residual(qrows)
    stage = {
        "direct_market": stage_summary(qrows, "market"),
        "ah_ou_score_matrix_raw": stage_summary(qrows, "latent_raw"),
        "draw_calibrated_score_matrix": stage_summary(qrows, "sharp"),
        "common_direct_market": stage_summary(common, "market"),
        "common_draw_calibrated_score_matrix": stage_summary(common, "sharp"),
        "common_pure_football": stage_summary(common, "football"),
        "common_market_plus_football_residual": stage_summary(common, "residual"),
    }
    transitions = {
        "market_to_raw_score_matrix": transition(qrows, "market", "latent_raw"),
        "raw_score_matrix_to_draw_calibrated": transition(qrows, "latent_raw", "sharp"),
        "market_to_draw_calibrated": transition(qrows, "market", "sharp"),
        "common_market_to_football_residual": transition(common, "market", "residual"),
    }
    diagnosis = []
    if stage["direct_market"]["draw_top1_count"] == 0:
        diagnosis.append("DRAW_IS_ALREADY_ABSENT_FROM_DIRECT_MARKET_ARGMAX")
    if stage["ah_ou_score_matrix_raw"]["draw_top1_count"] == 0:
        diagnosis.append("AH_OU_LATENT_SCORE_MATRIX_DOES_NOT_ACTIVATE_DRAW_TOP1")
    if stage["draw_calibrated_score_matrix"]["draw_top1_count"] == 0:
        diagnosis.append("ROLLING_DRAW_CALIBRATION_DOES_NOT_CROSS_TOP1_BOUNDARY")
    if common and stage["common_market_plus_football_residual"]["draw_top1_count"] == 0:
        diagnosis.append("STRONG_SHRINK_FOOTBALL_RESIDUAL_DOES_NOT_CROSS_TOP1_BOUNDARY")
    root = diagnosis[0] if diagnosis else "DRAW_TOP1_PRESENT_BEFORE_FINAL_STAGE"

    result = {
        "schema_version": "football3-r43s0-draw-probability-path-audit-v1",
        "status": "COMPLETE", "classification": "POSTVIEW_DIAGNOSTIC_ONLY", "formal_weight": 0,
        "question": "At which fixed stage does draw probability fail to become natural Top1?",
        "governance": {
            "probabilities_modified_by_audit": False, "parameter_search": False, "threshold_search": False,
            "draw_count_forced": False, "outcomes_used_only_for_diagnostic_stratification": True,
            "prematch_frozen_sources_only": True, "main_merge": False, "publication": False,
        },
        "coverage": {"q0_scored_n": len(qrows), **residual_meta},
        "stages": stage, "transitions": transitions,
        "diagnosis": {"root_stage": root, "findings": diagnosis},
        "action": "PROCEED_TO_PREREGISTERED_DYNAMIC_BIVARIATE_RESIDUAL_STATE_SPACE_WITHOUT_RETUNING_PRIOR_STAGES",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def verify():
    x = load(OUT); g = x["governance"]
    assert x["status"] == "COMPLETE" and x["formal_weight"] == 0
    assert g["probabilities_modified_by_audit"] is False and g["parameter_search"] is False
    assert g["threshold_search"] is False and g["draw_count_forced"] is False
    assert g["prematch_frozen_sources_only"] is True
    print("R43S0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
