#!/usr/bin/env python3
"""V6.43.0 online season-adaptive draw calibration challenge.

V6.42 found a dedicated DRAW-vs-DECISIVE model that helped in 2023/24 but failed
in 2024/25 as draw precision shifted sharply. V6.43 freezes that raw model
architecture and tests only a point-in-time calibration layer for season drift.

For each league, before each matchday:
- start from the training-period league draw rate;
- update a Beta-style posterior draw rate using only earlier completed matches in
  the current validation/test season;
- shift the raw draw-model logit toward the current-season posterior rate;
- make all same-day predictions before applying that day's outcomes.

Calibration parameters are selected on 2023/24 only, then evaluated once on
2024/25. A_FAST100 is opened only if that untouched historical holdout improves by
at least 0.5 percentage points. During A100 evaluation, only chronologically prior
A100 outcomes may update the online state; B300/C100 labels are never read.

Research only. CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_market_error_meta_gold500_v6400 as v640  # noqa: E402
import validate_specialized_draw_hurdle_gold500_v6420 as v642  # noqa: E402

OUT = ROOT / "manifests" / "v6_online_draw_calibration_gold500_v6430_status.json"

# Frozen from the best V6.42 historical configuration. V6.43 does not retune model structure.
RAW_SPEC = "core71"
RAW_DEPTH = 3
RAW_L2 = 50.0
RAW_DRAW_WEIGHT = 1.0

BETAS = (0.5, 1.0, 1.5, 2.0)
PRIOR_STRENGTHS = (20.0, 50.0, 100.0)
THRESHOLDS = (0.30, 0.34, 0.37, 0.40, 0.43)
HIST_HOLDOUT_REQUIRED_UPLIFT_PP = 0.50
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_PP = 3.0
EPS = 1e-8


def logit(p: float) -> float:
    p = min(1.0 - EPS, max(EPS, float(p)))
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def train_draw_rates(rows: list[dict[str, Any]]) -> dict[str, float]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        cid = str(r["competition_id"])
        counts[cid][0] += int(r["y"] == 1)
        counts[cid][1] += 1
    out = {}
    global_draws = sum(x[0] for x in counts.values())
    global_n = sum(x[1] for x in counts.values())
    global_rate = global_draws / max(1, global_n)
    for cid in v640.DOMAINS:
        d, n = counts.get(cid, [0, 0])
        out[cid] = d / n if n else global_rate
    return out


def market_pick(row: dict[str, Any]) -> int:
    return max(range(3), key=lambda i: float(row["market"][i]))


def decisive_side(row: dict[str, Any]) -> int:
    return 0 if float(row["market"][0]) >= float(row["market"][2]) else 2


def online_score(
    rows: list[dict[str, Any]],
    raw_pdraw: list[float],
    base_rates: dict[str, float],
    beta: float,
    prior_strength: float,
    threshold: float,
) -> dict[str, Any]:
    indexed = list(zip(rows, raw_pdraw))
    indexed.sort(key=lambda z: (str(z[0]["date"]), str(z[0]["competition_id"]), str(z[0]["home_team"]), str(z[0]["away_team"])))

    seen_n = Counter()
    seen_draws = Counter()
    market_hits = candidate_hits = 0
    draw_calls = draw_hits = 0
    overrides = wins = losses = neutral = 0
    predicted = Counter(); actual = Counter(); detail = []

    i = 0
    while i < len(indexed):
        date = str(indexed[i][0]["date"])
        j = i
        day = []
        while j < len(indexed) and str(indexed[j][0]["date"]) == date:
            day.append(indexed[j]); j += 1

        pending = []
        for row, raw in day:
            cid = str(row["competition_id"])
            base = float(base_rates[cid])
            posterior = (float(prior_strength) * base + float(seen_draws[cid])) / (float(prior_strength) + float(seen_n[cid]))
            adjusted = sigmoid(logit(float(raw)) + float(beta) * (logit(posterior) - logit(base)))
            mp = market_pick(row)
            cp = 1 if adjusted >= float(threshold) else decisive_side(row)
            y = int(row["y"])
            market_ok = mp == y; candidate_ok = cp == y
            market_hits += int(market_ok); candidate_hits += int(candidate_ok)
            predicted[str(cp)] += 1; actual[str(y)] += 1
            if cp == 1:
                draw_calls += 1; draw_hits += int(y == 1)
            if cp != mp:
                overrides += 1
                if (not market_ok) and candidate_ok:
                    wins += 1
                elif market_ok:
                    losses += 1
                else:
                    neutral += 1
            detail.append({
                "date": date, "competition_id": cid,
                "raw_p_draw": float(raw), "posterior_draw_rate": posterior,
                "adjusted_p_draw": adjusted, "market_pick": mp, "candidate_pick": cp,
                "market_correct": market_ok, "candidate_correct": candidate_ok,
            })
            pending.append((cid, y))

        # Same-date safe: update only after every prediction for the date is frozen.
        for cid, y in pending:
            seen_n[cid] += 1
            seen_draws[cid] += int(y == 1)
        i = j

    n = len(rows)
    return {
        "count": n, "market_hits": market_hits, "candidate_hits": candidate_hits,
        "market_top1": market_hits / n, "candidate_top1": candidate_hits / n,
        "uplift_pp": (candidate_hits - market_hits) * 100.0 / n,
        "draw_call_count": draw_calls, "draw_hit_count": draw_hits,
        "draw_precision": draw_hits / draw_calls if draw_calls else None,
        "override_count": overrides, "override_wins": wins, "override_losses": losses,
        "override_neutral": neutral, "override_net": wins - losses,
        "predicted_counts": dict(predicted), "actual_counts": dict(actual),
        "online_final_seen": {cid: {"n": int(seen_n[cid]), "draws": int(seen_draws[cid])} for cid in v640.DOMAINS},
        "detail": detail,
    }


def strip_score(s: dict[str, Any]) -> dict[str, Any]:
    x = dict(s); x.pop("detail", None); return x


def choose_calibration(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train = [r for r in rows if r["season"] == "2022/23"]
    val = [r for r in rows if r["season"] == "2023/24"]
    model = v642.fit_draw_model(train, RAW_SPEC, RAW_DEPTH, RAW_L2, RAW_DRAW_WEIGHT)
    raw = v642.predict_draw(val, RAW_SPEC, model)
    rates = train_draw_rates(train)
    board = []
    for beta in BETAS:
        for strength in PRIOR_STRENGTHS:
            for threshold in THRESHOLDS:
                s = strip_score(online_score(val, raw, rates, beta, strength, threshold))
                board.append({"beta": beta, "prior_strength": strength, "threshold": threshold, "selection": s})
    board.sort(key=lambda x: (
        -float(x["selection"]["candidate_top1"]),
        -float(x["selection"]["uplift_pp"]),
        -float(x["selection"]["draw_precision"] or 0.0),
        float(x["selection"]["draw_call_count"]),
        float(x["beta"]), float(x["prior_strength"]), float(x["threshold"]),
    ))
    return board[0], board


def historical_holdout(rows: list[dict[str, Any]], selected: dict[str, Any]) -> dict[str, Any]:
    train = [r for r in rows if r["season"] in ("2022/23", "2023/24")]
    holdout = [r for r in rows if r["season"] == "2024/25"]
    model = v642.fit_draw_model(train, RAW_SPEC, RAW_DEPTH, RAW_L2, RAW_DRAW_WEIGHT)
    raw = v642.predict_draw(holdout, RAW_SPEC, model)
    rates = train_draw_rates(train)
    return strip_score(online_score(
        holdout, raw, rates,
        float(selected["beta"]), float(selected["prior_strength"]), float(selected["threshold"]),
    ))


def main() -> int:
    historical, build_audit = v640.build_historical_rows()
    selected, selection_board = choose_calibration(historical)
    holdout = historical_holdout(historical, selected)
    historical_gate = float(holdout["uplift_pp"]) >= HIST_HOLDOUT_REQUIRED_UPLIFT_PP

    payload: dict[str, Any] = {
        "schema_version": "V6.43.0-online-draw-calibration-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_ONLINE_SEASON_ADAPTIVE_DRAW_CALIBRATION",
        "governance_contract": {
            "raw_model_frozen_from_v642": True,
            "raw_model_spec": RAW_SPEC, "raw_model_depth": RAW_DEPTH,
            "raw_model_l2": RAW_L2, "raw_model_draw_weight": RAW_DRAW_WEIGHT,
            "calibration_selected_on_2023_24_only": True,
            "historical_holdout_2024_25_used_for_gate_only": True,
            "same_day_predictions_before_updates": True,
            "probability_vector_changed": False,
            "A_FAST100_opened_only_after_historical_holdout_gate": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "A100_parameter_tuning": False, "CURRENT_unchanged": True,
        },
        "architecture": {
            "online_adjustment": "logit(p_draw_raw) + beta * [logit(current_season_posterior_draw_rate) - logit(training_draw_rate)]",
            "league_specific_state": True,
            "beta_grid": list(BETAS), "prior_strength_grid": list(PRIOR_STRENGTHS),
            "threshold_grid": list(THRESHOLDS),
            "selection_season": "2023/24", "historical_holdout_season": "2024/25",
            "historical_holdout_required_uplift_pp": HIST_HOLDOUT_REQUIRED_UPLIFT_PP,
        },
        "build_audit": build_audit,
        "calibration_selection": {"selected": selected, "leaderboard": selection_board},
        "historical_holdout": holdout,
        "historical_gate_passed": historical_gate,
    }

    if not historical_gate:
        payload["fast100"] = {"opened": False, "reason": "2024/25 historical holdout gate failed; A_FAST100 labels not read"}
        payload["decision"] = "HISTORICAL_HOLDOUT_FAILED_A100_NOT_OPENED"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": payload["status"], "decision": payload["decision"],
            "selected_calibration": {k: selected[k] for k in ("beta", "prior_strength", "threshold")},
            "selection_2023_24": selected["selection"],
            "holdout_2024_25": holdout,
            "historical_gate_passed": historical_gate,
        }, ensure_ascii=False, indent=2))
        return 0

    final_train = [r for r in historical if r["season"] in v640.TRAIN_SEASONS]
    model = v642.fit_draw_model(final_train, RAW_SPEC, RAW_DEPTH, RAW_L2, RAW_DRAW_WEIGHT)
    fast = v640.build_fast_rows()
    v640.attach_fast_labels(fast)
    raw = v642.predict_draw(fast, RAW_SPEC, model)
    rates = train_draw_rates(final_train)
    fast_score = online_score(
        fast, raw, rates,
        float(selected["beta"]), float(selected["prior_strength"]), float(selected["threshold"]),
    )
    fast_gate = int(fast_score["candidate_hits"]) >= FAST_REQUIRED_HITS and float(fast_score["uplift_pp"]) >= FAST_REQUIRED_UPLIFT_PP
    changed = []
    for row, item in zip(sorted(fast, key=lambda r: (str(r["date"]), str(r["competition_id"]), str(r["home_team"]), str(r["away_team"]))), fast_score["detail"]):
        if int(item["candidate_pick"]) != int(item["market_pick"]):
            changed.append({
                "gold_index": int(row["gold_index"]), "competition_id": row["competition_id"],
                "date": row["date"], "home_team": row["home_team"], "away_team": row["away_team"],
                "actual_result": int(row["y"]),
                "market_pick": int(item["market_pick"]), "candidate_pick": int(item["candidate_pick"]),
                "raw_p_draw": float(item["raw_p_draw"]), "posterior_draw_rate": float(item["posterior_draw_rate"]),
                "adjusted_p_draw": float(item["adjusted_p_draw"]),
                "market_correct": bool(item["market_correct"]), "candidate_correct": bool(item["candidate_correct"]),
            })
    fs = strip_score(fast_score)
    payload["fast100"] = {
        "opened": True, **fs,
        "required_hits": FAST_REQUIRED_HITS, "required_uplift_pp": FAST_REQUIRED_UPLIFT_PP,
        "gate_passed": bool(fast_gate), "changed_pick_audit": changed,
        "online_update_scope": "chronologically prior A_FAST100 results only; B/C labels not read",
    }
    payload["decision"] = "OPEN_CONFIRM300" if fast_gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "selected_calibration": {k: selected[k] for k in ("beta", "prior_strength", "threshold")},
        "selection_2023_24": selected["selection"], "holdout_2024_25": holdout,
        "fast100": {k: payload["fast100"][k] for k in ("market_hits", "candidate_hits", "market_top1", "candidate_top1", "uplift_pp", "draw_call_count", "draw_hit_count", "draw_precision", "override_net", "predicted_counts", "actual_counts", "gate_passed")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
