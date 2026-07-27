#!/usr/bin/env python3
"""V6.44.0 league-specific class-separation Naive Bayes draw challenge.

Research-inspired by 2025/2026 draw-specific Bayesian-classifier work, but this is
NOT a reproduction of that paper. The executable experiment here uses only our
strict PIT feature library and a transparent supervised discretizer.

Design
------
- one DRAW-vs-DECISIVE Naive Bayes model per league;
- continuous features are discretized using 1-2 quantile candidate cut-points
  selected on TRAIN only to maximize mutual information with DRAW / NOT-DRAW;
- if NOT-DRAW, closing market chooses the stronger home/away side;
- feature-set and posterior threshold are selected on 2023/24 only;
- the chosen policy is evaluated once on untouched 2024/25 historical holdout;
- A_FAST100 opens only if holdout uplift >= 0.5 percentage points;
- B300/C100 labels are never read; CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_market_error_meta_gold500_v6400 as v640  # noqa: E402

OUT = ROOT / "manifests" / "v6_league_nb_draw_gold500_v6440_status.json"
EPS = 1e-12
LAPLACE = 1.0
THRESHOLDS = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55)
HOLDOUT_REQUIRED_UPLIFT_PP = 0.50
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_PP = 3.0

# Stable V6.32 base-feature indices. Kept intentionally compact for Naive Bayes.
FEATURE_SETS: dict[str, tuple[tuple[str, int], ...]] = {
    "market_balance": (
        ("market_draw", 1), ("market_side_logit", 3), ("market_draw_logit", 4),
        ("market_margin", 5), ("market_entropy", 6),
        ("formal_draw", 8), ("formal_side_logit", 10), ("formal_draw_logit", 11),
        ("formal_margin", 12), ("formal_entropy", 13), ("market_minus_formal_draw", 15),
    ),
    "market_xg": (
        ("market_draw", 1), ("market_side_logit", 3), ("market_draw_logit", 4),
        ("market_margin", 5), ("market_entropy", 6),
        ("formal_draw", 8), ("formal_side_logit", 10), ("formal_margin", 12),
        ("market_minus_formal_draw", 15),
        ("xg_edge_diff", 17), ("xg_edge_absdiff", 19), ("npxg_edge_diff", 20),
        ("xpts_diff", 22), ("ppda_diff", 23), ("deep_edge_diff", 25),
        ("total_shot_chance", 32), ("total_sot_chance", 33),
    ),
    "market_context": (
        ("market_draw", 1), ("market_side_logit", 3), ("market_draw_logit", 4),
        ("market_margin", 5), ("market_entropy", 6),
        ("formal_draw", 8), ("formal_side_logit", 10), ("formal_margin", 12),
        ("market_minus_formal_draw", 15),
        ("elo_slow_diff", 36), ("elo_fast_diff", 37),
        ("form_pts5_diff", 38), ("form_gd5_diff", 39),
        ("form_pts10_diff", 40), ("form_gd10_diff", 41), ("rest_diff_scaled", 44),
    ),
}


def xvalue(row: dict[str, Any], index: int) -> float:
    return float(row["base_features"][index])


def mutual_information(values: list[float], labels: list[int], cuts: tuple[float, ...]) -> float:
    bins = [int(np.searchsorted(cuts, v, side="right")) for v in values]
    n = len(labels)
    if n == 0:
        return -1.0
    cb = Counter(bins); cc = Counter(labels); joint = Counter(zip(bins, labels))
    score = 0.0
    for (b, c), k in joint.items():
        pbc = k / n; pb = cb[b] / n; pc = cc[c] / n
        score += pbc * math.log(max(EPS, pbc / max(EPS, pb * pc)))
    return score


def choose_cuts(values: list[float], labels: list[int]) -> tuple[float, ...]:
    arr = np.asarray(values, dtype=float)
    if len(arr) < 20 or float(np.nanstd(arr)) < 1e-10:
        return tuple()
    candidates = sorted(set(float(x) for x in np.quantile(arr, [0.2, 0.4, 0.6, 0.8])))
    min_bin = max(5, int(round(0.05 * len(arr))))
    best: tuple[float, int, tuple[float, ...]] | None = None
    for size in (1, 2):
        for cuts in itertools.combinations(candidates, size):
            bins = [int(np.searchsorted(cuts, v, side="right")) for v in values]
            counts = Counter(bins)
            if min(counts.values()) < min_bin or len(counts) != size + 1:
                continue
            mi = mutual_information(values, labels, tuple(cuts))
            key = (mi, -size, tuple(-c for c in cuts))
            if best is None or key > (best[0], -best[1], tuple(-c for c in best[2])):
                best = (mi, size, tuple(cuts))
    return best[2] if best is not None else tuple()


class LeagueNB:
    def __init__(self, feature_spec: tuple[tuple[str, int], ...]):
        self.feature_spec = feature_spec
        self.cuts: list[tuple[float, ...]] = []
        self.class_prior: dict[int, float] = {}
        self.cond: list[dict[int, dict[int, float]]] = []

    def fit(self, rows: list[dict[str, Any]]) -> "LeagueNB":
        labels = [1 if int(r["y"]) == 1 else 0 for r in rows]
        if len(rows) < 100 or len(set(labels)) != 2:
            raise RuntimeError(f"league NB insufficient rows/classes n={len(rows)} y={Counter(labels)}")
        counts = Counter(labels)
        self.class_prior = {c: (counts[c] + LAPLACE) / (len(rows) + 2 * LAPLACE) for c in (0, 1)}
        self.cuts = []
        self.cond = []
        for _name, idx in self.feature_spec:
            vals = [xvalue(r, idx) for r in rows]
            cuts = choose_cuts(vals, labels)
            self.cuts.append(cuts)
            nbins = len(cuts) + 1
            table: dict[int, dict[int, float]] = {}
            for c in (0, 1):
                binc = Counter(
                    int(np.searchsorted(cuts, v, side="right"))
                    for v, y in zip(vals, labels) if y == c
                )
                denom = counts[c] + LAPLACE * nbins
                table[c] = {b: (binc[b] + LAPLACE) / denom for b in range(nbins)}
            self.cond.append(table)
        return self

    def predict_draw(self, row: dict[str, Any]) -> float:
        logs = {c: math.log(max(EPS, self.class_prior[c])) for c in (0, 1)}
        for j, (_name, idx) in enumerate(self.feature_spec):
            b = int(np.searchsorted(self.cuts[j], xvalue(row, idx), side="right"))
            for c in (0, 1):
                logs[c] += math.log(max(EPS, self.cond[j][c].get(b, EPS)))
        m = max(logs.values())
        e0 = math.exp(logs[0] - m); e1 = math.exp(logs[1] - m)
        return e1 / (e0 + e1)

    def audit(self) -> dict[str, Any]:
        return {
            "feature_count": len(self.feature_spec),
            "features": [name for name, _ in self.feature_spec],
            "cut_counts": {name: len(cuts) for (name, _), cuts in zip(self.feature_spec, self.cuts)},
            "class_prior": self.class_prior,
        }


def fit_league_models(rows: list[dict[str, Any]], feature_set: str) -> dict[str, LeagueNB]:
    models = {}
    spec = FEATURE_SETS[feature_set]
    for cid in v640.DOMAINS:
        rs = [r for r in rows if str(r["competition_id"]) == cid]
        models[cid] = LeagueNB(spec).fit(rs)
    return models


def predict(rows: list[dict[str, Any]], models: dict[str, LeagueNB]) -> list[float]:
    return [models[str(r["competition_id"])].predict_draw(r) for r in rows]


def market_pick(row: dict[str, Any]) -> int:
    return max(range(3), key=lambda i: float(row["market"][i]))


def decisive_side(row: dict[str, Any]) -> int:
    return 0 if float(row["market"][0]) >= float(row["market"][2]) else 2


def score(rows: list[dict[str, Any]], pdraw: list[float], threshold: float) -> dict[str, Any]:
    mh = ch = draws = draw_hits = overrides = wins = losses = neutral = 0
    predicted = Counter(); actual = Counter(); by_comp: dict[str, Counter] = defaultdict(Counter); detail = []
    for r, pd in zip(rows, pdraw):
        y = int(r["y"]); mp = market_pick(r); cp = 1 if float(pd) >= threshold else decisive_side(r)
        mok = mp == y; cok = cp == y
        mh += int(mok); ch += int(cok); predicted[str(cp)] += 1; actual[str(y)] += 1
        cid = str(r["competition_id"]); bc = by_comp[cid]
        bc["count"] += 1; bc["market_hits"] += int(mok); bc["candidate_hits"] += int(cok)
        bc["draw_calls"] += int(cp == 1); bc["draw_hits"] += int(cp == 1 and y == 1)
        if cp == 1:
            draws += 1; draw_hits += int(y == 1)
        if cp != mp:
            overrides += 1
            if (not mok) and cok: wins += 1
            elif mok: losses += 1
            else: neutral += 1
        detail.append({"p_draw": float(pd), "market_pick": mp, "candidate_pick": cp, "market_correct": mok, "candidate_correct": cok})
    n = len(rows)
    return {
        "count": n, "market_hits": mh, "candidate_hits": ch,
        "market_top1": mh / n, "candidate_top1": ch / n, "uplift_pp": (ch - mh) * 100.0 / n,
        "draw_call_count": draws, "draw_hit_count": draw_hits,
        "draw_precision": draw_hits / draws if draws else None,
        "override_count": overrides, "override_wins": wins, "override_losses": losses,
        "override_neutral": neutral, "override_net": wins - losses,
        "predicted_counts": dict(predicted), "actual_counts": dict(actual),
        "by_competition": {
            cid: {
                "count": int(c["count"]), "market_hits": int(c["market_hits"]),
                "candidate_hits": int(c["candidate_hits"]),
                "uplift_pp": (c["candidate_hits"] - c["market_hits"]) * 100.0 / max(1, c["count"]),
                "draw_calls": int(c["draw_calls"]), "draw_hits": int(c["draw_hits"]),
            } for cid, c in sorted(by_comp.items())
        },
        "detail": detail,
    }


def strip_score(s: dict[str, Any]) -> dict[str, Any]:
    x = dict(s); x.pop("detail", None); return x


def select_on_2023(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train = [r for r in rows if r["season"] == "2022/23"]
    val = [r for r in rows if r["season"] == "2023/24"]
    board = []
    for feature_set in FEATURE_SETS:
        models = fit_league_models(train, feature_set)
        p = predict(val, models)
        for threshold in THRESHOLDS:
            s = strip_score(score(val, p, threshold))
            board.append({
                "feature_set": feature_set, "threshold": threshold, "selection": s,
                "model_audit": {cid: model.audit() for cid, model in models.items()},
            })
    board.sort(key=lambda x: (
        -float(x["selection"]["candidate_top1"]), -float(x["selection"]["uplift_pp"]),
        -float(x["selection"]["draw_precision"] or 0.0), float(x["selection"]["draw_call_count"]),
        list(FEATURE_SETS).index(x["feature_set"]), float(x["threshold"]),
    ))
    return board[0], board


def holdout_2024(rows: list[dict[str, Any]], selected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    train = [r for r in rows if r["season"] in ("2022/23", "2023/24")]
    val = [r for r in rows if r["season"] == "2024/25"]
    models = fit_league_models(train, str(selected["feature_set"]))
    p = predict(val, models)
    return strip_score(score(val, p, float(selected["threshold"]))), {cid: m.audit() for cid, m in models.items()}


def main() -> int:
    historical, build_audit = v640.build_historical_rows()
    selected, board = select_on_2023(historical)
    holdout, holdout_model_audit = holdout_2024(historical, selected)
    historical_gate = float(holdout["uplift_pp"]) >= HOLDOUT_REQUIRED_UPLIFT_PP
    payload: dict[str, Any] = {
        "schema_version": "V6.44.0-league-nb-draw-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_LEAGUE_SPECIFIC_CLASS_SEPARATION_NAIVE_BAYES_DRAW",
        "research_note": "Inspired by draw-specific NB/discretization literature; not a reproduction of the published model.",
        "governance_contract": {
            "selection_season": "2023/24", "historical_holdout_season": "2024/25",
            "A_FAST100_opened_only_after_holdout_gate": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "probability_vector_changed": False, "A100_parameter_tuning": False,
            "CURRENT_unchanged": True,
        },
        "architecture": {
            "model": "one custom categorical Naive Bayes per league",
            "discretization": "TRAIN-only 20/40/60/80% candidate quantiles; choose 1-2 cuts maximizing mutual information with draw/not-draw subject to >=5% bin support",
            "laplace_smoothing": LAPLACE,
            "non_draw_decision": "closing market stronger home/away side",
            "feature_sets": {k: [name for name, _ in v] for k, v in FEATURE_SETS.items()},
            "threshold_grid": list(THRESHOLDS), "holdout_required_uplift_pp": HOLDOUT_REQUIRED_UPLIFT_PP,
        },
        "build_audit": build_audit,
        "selection_2023_24": {"selected": selected, "leaderboard": board},
        "historical_holdout_2024_25": holdout,
        "holdout_model_audit": holdout_model_audit,
        "historical_gate_passed": historical_gate,
    }
    if not historical_gate:
        payload["fast100"] = {"opened": False, "reason": "2024/25 holdout gate failed; A_FAST100 labels not read"}
        payload["decision"] = "HISTORICAL_HOLDOUT_FAILED_A100_NOT_OPENED"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": payload["status"], "decision": payload["decision"],
            "selected": {"feature_set": selected["feature_set"], "threshold": selected["threshold"], "selection": selected["selection"]},
            "holdout": holdout, "historical_gate_passed": historical_gate,
        }, ensure_ascii=False, indent=2))
        return 0

    final_train = [r for r in historical if r["season"] in v640.TRAIN_SEASONS]
    models = fit_league_models(final_train, str(selected["feature_set"]))
    fast = v640.build_fast_rows(); v640.attach_fast_labels(fast)
    p = predict(fast, models)
    fs = score(fast, p, float(selected["threshold"]))
    fast_gate = int(fs["candidate_hits"]) >= FAST_REQUIRED_HITS and float(fs["uplift_pp"]) >= FAST_REQUIRED_UPLIFT_PP
    changed = []
    for row, item in zip(fast, fs["detail"]):
        if int(item["candidate_pick"]) != int(item["market_pick"]):
            changed.append({
                "gold_index": int(row["gold_index"]), "competition_id": row["competition_id"],
                "date": row["date"], "home_team": row["home_team"], "away_team": row["away_team"],
                "actual_result": int(row["y"]), "p_draw": float(item["p_draw"]),
                "market_pick": int(item["market_pick"]), "candidate_pick": int(item["candidate_pick"]),
                "market_correct": bool(item["market_correct"]), "candidate_correct": bool(item["candidate_correct"]),
            })
    fast_public = strip_score(fs)
    payload["fast100"] = {
        "opened": True, **fast_public,
        "required_hits": FAST_REQUIRED_HITS, "required_uplift_pp": FAST_REQUIRED_UPLIFT_PP,
        "gate_passed": bool(fast_gate), "changed_pick_audit": changed,
    }
    payload["decision"] = "OPEN_CONFIRM300" if fast_gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "selected": {"feature_set": selected["feature_set"], "threshold": selected["threshold"], "selection": selected["selection"]},
        "holdout": holdout,
        "fast100": {k: payload["fast100"][k] for k in ("market_hits", "candidate_hits", "market_top1", "candidate_top1", "uplift_pp", "draw_call_count", "draw_hit_count", "draw_precision", "override_net", "predicted_counts", "actual_counts", "gate_passed")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
