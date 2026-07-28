#!/usr/bin/env python3
"""V6.50.6 strict 2025-era full-season retrospective replay diagnostic.

Purpose
-------
Evaluate the current research architecture on every available completed match from the
2025-era season in each of the 17 formal domains, without waiting for new fixtures.

Target seasons
--------------
* European cross-year competitions: 2025/26.
* Calendar-year competitions: 2025.

Tracks
------
1) F06 raw matrix: time-ordered replay of total=comp | margin=full.
2) F05 1X2 selector analogue: legacy closing 1X2 reference + frozen V6.47.5
   hierarchical reliability threshold/model. The selector never mutates probabilities.
3) V6.50.3 analogue: on rows with a real legacy OU2.5 price pair, KL-project the raw
   F06 total P(T) to the de-vigged OU2.5 under/over group masses.
4) V6.50.0 analogue: keep raw F06 total, constrain result marginal to legacy closing 1X2.
5) V6.50.5 analogue: constrain result to legacy closing 1X2 and total to the OU-KL total.

Leakage controls
----------------
* All match-result-dependent state is updated only after all matches on the same calendar
  day have been predicted.
* No 2025 target result is used to tune a threshold, weight, bin, smoothing constant,
  market transformation, or model configuration.
* The V6.47.5 selector threshold/reliability model is already frozen from development data
  strictly before its recent-season benchmark cutoffs.
* Legacy market prices have no original quote timestamps; therefore all market-based
  results here are RETROSPECTIVE_REFERENCE_ONLY, never formal snapshot/promotion evidence.
* Historical OU is fixed 2.5 when present. This is a diagnostic analogue of V6.50.3, not
  an assertion that the historical main OU line matched the modern live Kambi line.

Research only. formal_weight=0. CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import audit_total_margin_capacity_ablation_v6478 as ablation
import evaluate_direct_total_margin_matrix_v6477 as core
import v6_total_margin_forward_v6479 as matrix_forward
import v6_kl_joint_projection_v6500 as kl_result
import v6_ou_kl_direct_total_v6503 as oukl
import v6_ou_result_joint_matrix_v6505 as joint
from diagnose_1x2_market_anchor_v697 import _extract_odds

SELECTOR_FREEZE = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_freeze.json"
OUT = ROOT / "manifests" / "v6_fullseason_2025_replay_v6506_status.json"

CALENDAR_YEAR = {
    "ARG_Primera",
    "BRA_SerieA",
    "JPN_J1",
    "KOR_KLeague1",
    "NOR_Eliteserien",
    "SWE_Allsvenskan",
    "USA_MLS",
}
TARGET_SEASONS = {
    cid: ("2025" if cid in CALENDAR_YEAR else "2025/26")
    for cid in core.base.TARGET_COMPETITIONS
}
DIRECTIONS = ("home", "draw", "away")
EPS = 1e-15

# Historical Football-Data style OU2.5 fields, in provider preference order.
OU25_PAIRS = (
    ("P>2.5", "P<2.5", "Pinnacle_legacy_ou25"),
    ("B365>2.5", "B365<2.5", "Bet365_legacy_ou25"),
    ("Avg>2.5", "Avg<2.5", "Average_legacy_ou25"),
    ("BbAv>2.5", "BbAv<2.5", "Average_legacy_ou25_oldschema"),
    ("Max>2.5", "Max<2.5", "Maximum_legacy_ou25"),
    ("BbMx>2.5", "BbMx<2.5", "Maximum_legacy_ou25_oldschema"),
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _float_odds(v: Any) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def extract_ou25(raw: dict[str, str]) -> dict[str, Any] | None:
    for over_key, under_key, provider in OU25_PAIRS:
        over = _float_odds(raw.get(over_key))
        under = _float_odds(raw.get(under_key))
        if over is not None and under is not None:
            return {
                "line": 2.5,
                "over": over,
                "under": under,
                "provider": provider,
                "over_field": over_key,
                "under_field": under_key,
            }
    return None


def load_raw_row_cache(target_rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, int], dict[str, str]], dict[str, Any]]:
    paths = sorted({str(r["source_file"]) for r in target_rows})
    out: dict[tuple[str, int], dict[str, str]] = {}
    missing_paths: list[str] = []
    bad_indices = 0
    for rel in paths:
        path = ROOT / rel
        if not path.exists():
            missing_paths.append(rel)
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [
                {str(k): "" if v is None else str(v) for k, v in raw.items() if k}
                for raw in csv.DictReader(handle)
            ]
        for r in target_rows:
            if str(r["source_file"]) != rel:
                continue
            idx = int(r["row_index"])
            if idx < 0 or idx >= len(rows):
                bad_indices += 1
                continue
            out[(rel, idx)] = rows[idx]
    return out, {
        "source_file_count": len(paths),
        "missing_source_files": missing_paths,
        "bad_row_indices": bad_indices,
        "raw_rows_resolved": len(out),
    }


def serialise_prediction(p: dict[str, Any]) -> dict[str, Any]:
    return matrix_forward.serialise_prediction(p)


def result_actual(hg: int, ag: int) -> str:
    return core.result_direction(hg, ag)


def total_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    top1 = top2 = 0
    ll = rps = 0.0
    pred_modes: Counter[str] = Counter()
    actuals: Counter[str] = Counter()
    for r in rows:
        p = r[key]
        actual = core.total_cat(int(r["hg"]), int(r["ag"]))
        tk = core.topk(p, 2)
        top1 += int(bool(tk) and tk[0] == actual)
        top2 += int(actual in tk)
        ll -= math.log(max(EPS, float(p[actual])))
        rps += core.rps_total(p, actual)
        if tk:
            pred_modes[str(tk[0])] += 1
        actuals[actual] += 1
    return {
        "count": n,
        "top1_accuracy": top1 / n,
        "top2_accuracy": top2 / n,
        "log_loss": ll / n,
        "rps": rps / n,
        "mode_counts": dict(sorted(pred_modes.items())),
        "actual_total_counts": dict(sorted(actuals.items())),
    }


def x12_metrics(rows: list[dict[str, Any]], key: str = "probabilities") -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    hits = 0
    ll = brier = rps = 0.0
    by_pick: Counter[str] = Counter()
    by_actual: Counter[str] = Counter()
    for r in rows:
        p = {d: float(r[key][d]) for d in DIRECTIONS}
        actual = str(r["actual"])
        pick = max(DIRECTIONS, key=lambda d: p[d])
        hits += int(pick == actual)
        ll -= math.log(max(EPS, p[actual]))
        brier += sum((p[d] - (1.0 if d == actual else 0.0)) ** 2 for d in DIRECTIONS)
        # Ordered home-draw-away RPS with two cut points.
        cp1 = p["home"]
        cy1 = 1.0 if actual == "home" else 0.0
        cp2 = p["home"] + p["draw"]
        cy2 = 0.0 if actual == "away" else 1.0
        rps += ((cp1 - cy1) ** 2 + (cp2 - cy2) ** 2) / 2.0
        by_pick[pick] += 1
        by_actual[actual] += 1
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n,
        "log_loss": ll / n,
        "brier": brier / n,
        "rps": rps / n,
        "pick_counts": dict(sorted(by_pick.items())),
        "actual_counts": dict(sorted(by_actual.items())),
    }


def surface_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    total1 = total2 = result1 = score1 = score3 = 0
    tll = trps = rb = sll = 0.0
    sll_n = 0
    total_modes: Counter[str] = Counter()
    score_modes: Counter[str] = Counter()
    for r in rows:
        p = r[key]
        hg = int(r["hg"]); ag = int(r["ag"])
        tc = core.total_cat(hg, ag)
        tt = {k: float((p.get("total") or {})[k]) for k in core.REPORT_TOTAL_STATES}
        tk = core.topk(tt, 2)
        total1 += int(tk and tk[0] == tc)
        total2 += int(tc in tk)
        tll -= math.log(max(EPS, tt[tc]))
        trps += core.rps_total(tt, tc)
        if tk:
            total_modes[str(tk[0])] += 1

        actual_res = result_actual(hg, ag)
        rp = {d: float((p.get("result") or {})[d]) for d in DIRECTIONS}
        result1 += int(max(DIRECTIONS, key=lambda d: rp[d]) == actual_res)
        rb += sum((rp[d] - (1.0 if d == actual_res else 0.0)) ** 2 for d in DIRECTIONS)

        sm = {str(k): float(v) for k, v in (p.get("score_matrix") or {}).items()}
        top_scores = sorted(sm.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        actual_score = f"{hg}-{ag}"
        if top_scores:
            score_modes[top_scores[0][0]] += 1
            score1 += int(top_scores[0][0] == actual_score)
            score3 += int(any(k == actual_score for k, _ in top_scores))
        if hg + ag <= core.TOTAL_EXACT_MAX:
            sll -= math.log(max(EPS, sm.get(actual_score, 0.0)))
            sll_n += 1

    return {
        "count": n,
        "total_top1_accuracy": total1 / n,
        "total_top2_accuracy": total2 / n,
        "total_log_loss": tll / n,
        "total_rps": trps / n,
        "result_top1_accuracy": result1 / n,
        "result_brier": rb / n,
        "exact_score_top1_accuracy": score1 / n,
        "exact_score_top3_accuracy": score3 / n,
        "exact_score_log_loss_non_tail": sll / sll_n if sll_n else None,
        "exact_score_log_loss_n": sll_n,
        "total_mode_counts": dict(sorted(total_modes.items())),
        "score_top1_counts": dict(sorted(score_modes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unique_score_top1": len(score_modes),
        "one_one_top1_count": int(score_modes.get("1-1", 0)),
        "one_one_top1_rate": float(score_modes.get("1-1", 0)) / n,
    }


def replay_f06(all_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in all_rows:
        by_comp[str(r["competition_id"])].append(r)

    model = core.OnlineModel()
    out: list[dict[str, Any]] = []
    training_rows = 0
    target_seen = Counter()
    for cid in core.base.TARGET_COMPETITIONS:
        target_season = TARGET_SEASONS[cid]
        comp_rows = sorted(by_comp.get(cid, []), key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        days: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in comp_rows:
            days[str(r["date"])[:10]].append(r)
        for day in sorted(days):
            day_rows = days[day]
            frozen = [(r, model.features(r)) for r in day_rows]
            for r, _ in frozen:
                if str(r["season"]) == target_season:
                    pred = ablation.mixed_predict(model, r, "comp", "full")
                    out.append({
                        "competition_id": cid,
                        "season": str(r["season"]),
                        "date": str(r["date"]),
                        "home_team": str(r["home_team"]),
                        "away_team": str(r["away_team"]),
                        "source_file": str(r["source_file"]),
                        "row_index": int(r["row_index"]),
                        "hg": int(r["hg"]),
                        "ag": int(r["ag"]),
                        "prediction": pred,
                    })
                    target_seen[cid] += 1
                else:
                    training_rows += 1
            model.update_batch(frozen)
    out.sort(key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    return out, {"training_or_non_target_rows_replayed": training_rows, "target_counts_by_competition": dict(sorted(target_seen.items()))}


def selector_decision(q: dict[str, float], cid: str, freeze: dict[str, Any]) -> dict[str, Any]:
    model = freeze.get("model") or {}
    trained = set(str(k) for k in (model.get("competition_cutoff_dates") or {}).keys())
    pick = max(DIRECTIONS, key=lambda d: q[d])
    pmax = float(q[pick])
    threshold = float(freeze.get("selector_threshold") or 0.55)
    if cid not in trained:
        return {"pick": pick, "pmax": pmax, "reliability_score": None, "level": "UNSEEN_DOMAIN_ABSTAIN", "support": 0, "selected": False}
    edges = [float(x) for x in (model.get("pmax_edges") or [])]
    b = len(edges) - 2
    for i in range(len(edges) - 1):
        if edges[i] <= pmax < edges[i + 1]:
            b = i
            break
    rel_model = model.get("reliability_model") or {}
    comp = (rel_model.get("competition") or {}).get(f"{cid}|{pick}|{b}")
    min_n = int(model.get("minimum_competition_cell_n") or 20)
    if isinstance(comp, dict) and int(comp.get("n") or 0) >= min_n:
        score = float(comp["posterior_mean"]); level = "COMPETITION_SHRUNK_CELL"; support = int(comp.get("n") or 0)
    else:
        glob = (rel_model.get("global") or {}).get(f"{pick}|{b}") or {"posterior_mean": 0.5, "n": 0}
        score = float(glob["posterior_mean"]); level = "GLOBAL_FALLBACK_CELL"; support = int(glob.get("n") or 0)
    return {"pick": pick, "pmax": pmax, "reliability_score": score, "level": level, "support": support, "selected": bool(score >= threshold)}


def by_comp_metrics(rows: list[dict[str, Any]], metric_fn, *args) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r["competition_id"])].append(r)
    return {cid: metric_fn(rs, *args) for cid, rs in sorted(by.items())}


def main() -> int:
    selector_freeze = json.loads(SELECTOR_FREEZE.read_text(encoding="utf-8"))
    if selector_freeze.get("status") != "FROZEN" or float(selector_freeze.get("selector_threshold") or 0) != 0.55:
        raise RuntimeError("unexpected selector freeze/threshold")

    all_rows, source_meta = core.read_rows()
    target_rows, replay_meta = replay_f06(all_rows)
    target_expected_domains = set(core.base.TARGET_COMPETITIONS)
    target_actual_domains = set(str(r["competition_id"]) for r in target_rows)
    raw_cache, raw_meta = load_raw_row_cache(target_rows)

    # Base F06 evaluation on every target-season row with a valid 90m result.
    base_eval_rows = [
        {"competition_id": r["competition_id"], "hg": r["hg"], "ag": r["ag"], "prediction": r["prediction"]}
        for r in target_rows
    ]
    base_metrics = core.evaluate_predictions(base_eval_rows, "V6.50.6_2025_raw_F06_comp_full")
    base_modes = Counter()
    base_score_modes = Counter()
    for r in target_rows:
        pred = r["prediction"]
        tk = core.topk(pred["total"], 1)
        if tk:
            base_modes[str(tk[0])] += 1
        sk = core.topk(pred["matrix"], 1)
        if sk:
            base_score_modes[f"{sk[0][0]}-{sk[0][1]}"] += 1

    market_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    ou_rows: list[dict[str, Any]] = []
    missing_raw = market_missing = ou_missing = projection_fail = joint_fail = result_kl_fail = 0
    market_provider_counts = Counter(); ou_provider_counts = Counter(); selector_levels = Counter(); unseen_domains = Counter()

    for r in target_rows:
        raw = raw_cache.get((str(r["source_file"]), int(r["row_index"])))
        if raw is None:
            missing_raw += 1
            continue
        extracted = _extract_odds(raw)
        if extracted is None:
            market_missing += 1
            continue
        q, provider = extracted
        q = {d: float(q[d]) for d in DIRECTIONS}
        s = sum(q.values())
        if not math.isfinite(s) or s <= 0:
            market_missing += 1
            continue
        q = {d: q[d] / s for d in DIRECTIONS}
        actual = result_actual(int(r["hg"]), int(r["ag"]))
        dec = selector_decision(q, str(r["competition_id"]), selector_freeze)
        mr = {
            **{k: r[k] for k in ("competition_id", "season", "date", "home_team", "away_team", "hg", "ag")},
            "actual": actual,
            "probabilities": q,
            "market_provider": provider,
            "selector": dec,
        }
        market_rows.append(mr)
        market_provider_counts[provider] += 1
        selector_levels[str(dec["level"])] += 1
        if str(dec["level"]) == "UNSEEN_DOMAIN_ABSTAIN":
            unseen_domains[str(r["competition_id"])] += 1
        if bool(dec["selected"]):
            selected_rows.append(mr)

        ou = extract_ou25(raw)
        if ou is None:
            ou_missing += 1
            continue
        try:
            p_over, p_under = oukl.devig(float(ou["over"]), float(ou["under"]))
            prior_surface = serialise_prediction(r["prediction"])
            total_candidate, total_audit = oukl.project(prior_surface["total"], 2.5, p_over, p_under)
            coherent_ref = kl_result.project(prior_surface, q)
            unified = joint.project(prior_surface, total_candidate, q)
        except Exception:
            projection_fail += 1
            continue
        if not bool((coherent_ref.get("audit") or {}).get("converged")):
            result_kl_fail += 1
            continue
        if not bool((unified.get("audit") or {}).get("converged")):
            joint_fail += 1
            continue
        ou_provider_counts[str(ou["provider"])] += 1
        ou_rows.append({
            **{k: r[k] for k in ("competition_id", "season", "date", "home_team", "away_team", "hg", "ag")},
            "actual": actual,
            "market_probabilities": q,
            "ou": ou,
            "ou_devig": {"over": p_over, "under": p_under},
            "raw_total": prior_surface["total"],
            "ou_total": total_candidate,
            "raw_surface": prior_surface,
            "result_only_kl_surface": coherent_ref,
            "ou_result_joint_surface": unified,
            "ou_projection_audit": total_audit,
        })

    market_metrics = x12_metrics(market_rows)
    selector_metrics = x12_metrics(selected_rows)
    selector_metrics.update({
        "population_with_market": len(market_rows),
        "coverage_on_market_population": len(selected_rows) / len(market_rows) if market_rows else 0.0,
        "threshold": 0.55,
        "trained_domain_count": len((selector_freeze.get("model") or {}).get("competition_cutoff_dates") or {}),
        "selector_level_counts": dict(sorted(selector_levels.items())),
        "unseen_domain_abstain_counts": dict(sorted(unseen_domains.items())),
    })

    total_same_ref = total_metrics(ou_rows, "raw_total")
    total_same_new = total_metrics(ou_rows, "ou_total")
    raw_surface_same = surface_metrics(ou_rows, "raw_surface")
    coherent_ref_same = surface_metrics(ou_rows, "result_only_kl_surface")
    unified_same = surface_metrics(ou_rows, "ou_result_joint_surface")

    by_comp_base: dict[str, Any] = {}
    for cid in core.base.TARGET_COMPETITIONS:
        rs = [x for x in target_rows if x["competition_id"] == cid]
        ers = [{"hg": x["hg"], "ag": x["ag"], "prediction": x["prediction"]} for x in rs]
        by_comp_base[cid] = core.evaluate_predictions(ers, cid) if ers else {"count": 0}

    by_comp_market = by_comp_metrics(market_rows, x12_metrics)
    by_comp_selector = by_comp_metrics(selected_rows, x12_metrics)
    by_comp_ou_raw = by_comp_metrics(ou_rows, total_metrics, "raw_total")
    by_comp_ou_new = by_comp_metrics(ou_rows, total_metrics, "ou_total")
    by_comp_joint = by_comp_metrics(ou_rows, surface_metrics, "ou_result_joint_surface")

    mode_changed = sum(
        int(core.topk(r["raw_total"], 1)[0] != core.topk(r["ou_total"], 1)[0])
        for r in ou_rows if core.topk(r["raw_total"], 1) and core.topk(r["ou_total"], 1)
    )
    score_changed = sum(
        int((r["result_only_kl_surface"].get("score_top10") or [{}])[0].get("score") != (r["ou_result_joint_surface"].get("score_top10") or [{}])[0].get("score"))
        for r in ou_rows
    )
    max_ou_resid = max((float((r["ou_projection_audit"] or {}).get("max_constraint_residual") or 0.0) for r in ou_rows), default=0.0)
    max_joint_total_resid = max((float((r["ou_result_joint_surface"].get("audit") or {}).get("max_total_constraint_residual") or 0.0) for r in ou_rows), default=0.0)
    max_joint_result_resid = max((float((r["ou_result_joint_surface"].get("audit") or {}).get("max_result_constraint_residual") or 0.0) for r in ou_rows), default=0.0)

    payload = {
        "schema_version": "V6.50.6-fullseason-2025-replay-status-r1",
        "generated_at_utc": now(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_RETROSPECTIVE_DIAGNOSTIC" if target_actual_domains == target_expected_domains else "PARTIAL_DOMAIN_COVERAGE",
        "classification": "STRICT_TIME_ORDERED_2025_FULLSEASON_RETROSPECTIVE_RESEARCH_FORMAL_WEIGHT_0",
        "target_contract": {
            "calendar_year_domains_use": "2025",
            "cross_year_domains_use": "2025/26",
            "target_seasons_by_competition": TARGET_SEASONS,
            "all_17_domains_required_for_base_replay": True,
            "same_day_policy": "predict_all_then_update_all",
            "current_selector_threshold_frozen": 0.55,
            "legacy_market_timestamp_classification": "RETROSPECTIVE_REFERENCE_ONLY",
            "historical_ou_surface": "fixed OU2.5 when a real legacy pre-match/closing price pair exists",
            "historical_ou_is_exact_live_v6503_equivalent": False,
        },
        "data": {
            "core_read_rows_meta": source_meta,
            "replay_meta": replay_meta,
            "raw_row_resolution": raw_meta,
            "base_target_count": len(target_rows),
            "target_domain_count": len(target_actual_domains),
            "missing_target_domains": sorted(target_expected_domains - target_actual_domains),
            "market_1x2_count": len(market_rows),
            "selector_selected_count": len(selected_rows),
            "ou25_joint_count": len(ou_rows),
            "missing_raw_count": missing_raw,
            "missing_1x2_count_after_raw_resolution": market_missing,
            "missing_ou25_count_on_market_rows": ou_missing,
            "ou_projection_failures": projection_fail,
            "result_kl_failures": result_kl_fail,
            "joint_projection_failures": joint_fail,
            "market_provider_counts": dict(sorted(market_provider_counts.items())),
            "ou_provider_counts": dict(sorted(ou_provider_counts.items())),
        },
        "raw_f06_fullseason": {
            "metrics": base_metrics,
            "total_mode_counts": dict(sorted(base_modes.items())),
            "score_top1_counts": dict(sorted(base_score_modes.items(), key=lambda kv: (-kv[1], kv[0]))),
            "unique_score_top1": len(base_score_modes),
            "by_competition": by_comp_base,
        },
        "f05_market_and_selector": {
            "market_all_available": market_metrics,
            "selector_selected": selector_metrics,
            "by_competition_market": by_comp_market,
            "by_competition_selector_selected": by_comp_selector,
        },
        "ou_kl_total_same_subset": {
            "count": len(ou_rows),
            "raw_f06": total_same_ref,
            "ou_kl_candidate": total_same_new,
            "mode_changed_count": mode_changed,
            "mode_changed_rate": mode_changed / len(ou_rows) if ou_rows else 0.0,
            "top1_accuracy_delta": (total_same_new.get("top1_accuracy", 0.0) - total_same_ref.get("top1_accuracy", 0.0)) if ou_rows else None,
            "log_loss_delta_candidate_minus_raw": (total_same_new.get("log_loss", 0.0) - total_same_ref.get("log_loss", 0.0)) if ou_rows else None,
            "rps_delta_candidate_minus_raw": (total_same_new.get("rps", 0.0) - total_same_ref.get("rps", 0.0)) if ou_rows else None,
            "max_constraint_residual": max_ou_resid,
            "by_competition_raw": by_comp_ou_raw,
            "by_competition_candidate": by_comp_ou_new,
        },
        "joint_matrix_same_subset": {
            "count": len(ou_rows),
            "raw_f06": raw_surface_same,
            "result_only_kl_reference": coherent_ref_same,
            "ou_result_joint_candidate": unified_same,
            "score_top1_changed_vs_result_only_kl_count": score_changed,
            "score_top1_changed_vs_result_only_kl_rate": score_changed / len(ou_rows) if ou_rows else 0.0,
            "max_total_constraint_residual": max_joint_total_resid,
            "max_result_constraint_residual": max_joint_result_resid,
            "by_competition_candidate": by_comp_joint,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "formal_probability_change": False,
            "formal_threshold_change": False,
            "no_target_result_used_for_parameter_selection": True,
            "historical_market_has_no_original_quote_timestamp": True,
            "market_based_results_are_retrospective_reference_only": True,
            "cannot_promote_from_this_replay": True,
            "can_reject_or_diagnose_current_research_paths": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "base_n": len(target_rows),
        "market_n": len(market_rows),
        "selected_n": len(selected_rows),
        "ou_joint_n": len(ou_rows),
        "raw_f06_total_top1": base_metrics.get("total_top1_accuracy"),
        "raw_f06_result_top1": base_metrics.get("result_top1_accuracy"),
        "market_1x2_accuracy": market_metrics.get("accuracy"),
        "selector_accuracy": selector_metrics.get("accuracy"),
        "selector_coverage": selector_metrics.get("coverage_on_market_population"),
        "ou_raw_total_top1": total_same_ref.get("top1_accuracy"),
        "ou_candidate_total_top1": total_same_new.get("top1_accuracy"),
        "ou_candidate_total_logloss": total_same_new.get("log_loss"),
        "ou_mode_changed": mode_changed,
        "raw_score_top1": raw_surface_same.get("exact_score_top1_accuracy"),
        "result_only_score_top1": coherent_ref_same.get("exact_score_top1_accuracy"),
        "joint_score_top1": unified_same.get("exact_score_top1_accuracy"),
        "joint_score_unique": unified_same.get("unique_score_top1"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
