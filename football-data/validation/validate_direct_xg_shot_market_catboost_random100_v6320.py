#!/usr/bin/env python3
"""V6.32.0 direct nonlinear 1X2 random100 challenge.

Goal
----
Try to break 65% Top-1 on a fixed, unfiltered random sample of 100 matches without
post-hoc match selection or seed shopping.

Information used
----------------
- de-vigged Football-Data closing 1X2 probabilities (retrospective research only);
- current formal 1X2 probabilities from the strict-PIT score matrix;
- immutable V6.18.9 pre-match Understat xG/xGA/npxG/npxGA/PPDA/deep/xPTS state;
- strict date-before-match rolling shots and shots-on-target state;
- two fixed-speed Elo states, recent result form and rest-day state.

Model discipline
----------------
- CatBoost hyperparameters are selected only from rolling historical validation:
  train 2022/23 -> validate 2023/24, then train 2022/23+2023/24 -> validate 2024/25.
- 2025/26 is used only after model selection.
- The random100 seed is fixed at 632100 and every sampled row is scored.
- No confidence filtering, league dropping, threshold tuning or seed replacement is allowed.
- Historical closing odds lack original quote timestamps, and 2025/26 has been viewed in older
  research. Therefore this is DEVELOPMENT RESEARCH ONLY and can never auto-promote CURRENT.
"""
from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_market_residual_fusion_v620 as marketmod  # noqa: E402
import v6_total_shot_feature_offset_v6253 as shotmod  # noqa: E402
import v6_total_xg_ordinal_residual_v62513 as xgcore  # noqa: E402
import v6_understat_xg_residual_v624 as xgmod  # noqa: E402
from platform_core import (  # noqa: E402
    canonical_team_name,
    derive_score_marginals,
    normalize_team_token,
    read_processed_matches,
)

OUT = ROOT / "manifests" / "v6_direct_xg_shot_market_catboost_random100_v6320_status.json"
SEED = 632100
TARGET = 100
TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"
DOMAINS = tuple(sorted(xgmod.UNDERSTAT_LEAGUES))
EPS = 1e-12

# Small, predeclared grid. No test-season tuning.
DEPTHS = (4, 6)
L2S = (10.0, 30.0)
DRAW_WEIGHTS = (0.9, 1.1, 1.3)
MARKET_BLEND_WEIGHTS = (0.0, 0.25, 0.50, 0.75)
ITERATIONS = 350
LEARNING_RATE = 0.04


def _token(cid: str, name: str) -> str:
    try:
        name = canonical_team_name(cid, name)
    except Exception:
        pass
    return normalize_team_token(name)


def _actual(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def _entropy(p: list[float]) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def _side_logit(p: list[float]) -> float:
    side = float(p[0]) / max(EPS, float(p[0]) + float(p[2]))
    side = min(1.0 - 1e-8, max(1e-8, side))
    return math.log(side / (1.0 - side))


def _draw_logit(p: list[float]) -> float:
    d = min(1.0 - 1e-8, max(1e-8, float(p[1])))
    return math.log(d / (1.0 - d))


def _margin(p: list[float]) -> float:
    s = sorted((float(x) for x in p), reverse=True)
    return s[0] - s[1]


def _geometric_pool(model: list[float], market: list[float], market_weight: float) -> list[float]:
    if market_weight <= 0.0:
        return [float(x) for x in model]
    logs = [
        (1.0 - market_weight) * math.log(max(EPS, float(a)))
        + market_weight * math.log(max(EPS, float(b)))
        for a, b in zip(model, market)
    ]
    m = max(logs)
    raw = [math.exp(x - m) for x in logs]
    z = sum(raw)
    return [x / z for x in raw]


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter()
    for row in rows:
        p = [float(x) for x in row[key]]
        y = int(row["y"])
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        predicted[str(pick)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    return {
        "count": n,
        "hits": hits,
        "top1": hits / n if n else None,
        "brier": brier / n if n else None,
        "logloss": logloss / n if n else None,
        "rps": rps / n if n else None,
        "predicted_counts": dict(predicted),
    }


def _market_lookup(cid: str) -> tuple[dict[tuple[str, str, str, str], list[float]], dict[str, Any]]:
    directory = ROOT / "processed" / cid
    lookup: dict[tuple[str, str, str, str], list[float]] = {}
    stats = Counter()
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                stats["rows"] += 1
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season not in TRAIN_SEASONS and season != TEST_SEASON:
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                    market, family = marketmod._closing_market(raw)
                except Exception:
                    stats["parse_rejected"] += 1
                    continue
                if market is None:
                    stats["missing_market"] += 1
                    continue
                home = _token(cid, str(raw.get("HomeTeam") or ""))
                away = _token(cid, str(raw.get("AwayTeam") or ""))
                if not home or not away:
                    stats["identity_rejected"] += 1
                    continue
                key = (season, date, home, away)
                if key in lookup:
                    stats["duplicate_key"] += 1
                    continue
                lookup[key] = [float(market[k]) for k in ("home", "draw", "away")]
                stats[f"family_{family}"] += 1
                stats["attached"] += 1
    return lookup, dict(stats)


def _context_map(cid: str) -> dict[tuple[str, str, str, str], list[float]]:
    """Same-day-safe dual-Elo + recent form + rest context from settled prior matches."""
    matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    by_season: dict[str, list[Any]] = defaultdict(list)
    for m in matches:
        if str(m.season) in TRAIN_SEASONS or str(m.season) == TEST_SEASON:
            by_season[str(m.season)].append(m)

    ordered = sorted(by_season, key=lambda s: min(m.date for m in by_season[s]))
    rating_slow: dict[str, float] = defaultdict(lambda: 1500.0)
    rating_fast: dict[str, float] = defaultdict(lambda: 1500.0)
    form_pts: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    form_gd: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    last_date: dict[str, datetime] = {}
    out: dict[tuple[str, str, str, str], list[float]] = {}

    for season_index, season in enumerate(ordered):
        if season_index > 0:
            for team in list(rating_slow):
                rating_slow[team] = 1500.0 + 0.85 * (rating_slow[team] - 1500.0)
            for team in list(rating_fast):
                rating_fast[team] = 1500.0 + 0.70 * (rating_fast[team] - 1500.0)
            form_pts = defaultdict(lambda: deque(maxlen=10))
            form_gd = defaultdict(lambda: deque(maxlen=10))
            last_date = {}

        by_date: dict[datetime, list[Any]] = defaultdict(list)
        for m in by_season[season]:
            by_date[m.date].append(m)
        for date in sorted(by_date):
            day = sorted(by_date[date], key=lambda m: (m.home_team, m.away_team))
            pending = []
            for m in day:
                h = _token(cid, m.home_team)
                a = _token(cid, m.away_team)
                hp = list(form_pts[h]); ap = list(form_pts[a])
                hg = list(form_gd[h]); ag = list(form_gd[a])

                def avg_last(values: list[float], n: int) -> float:
                    z = values[-n:]
                    return sum(z) / len(z) if z else 0.0

                hr = min(21.0, max(0.0, (date - last_date[h]).total_seconds() / 86400.0)) if h in last_date else 7.0
                ar = min(21.0, max(0.0, (date - last_date[a]).total_seconds() / 86400.0)) if a in last_date else 7.0
                key = (season, date.date().isoformat(), h, a)
                out[key] = [
                    (rating_slow[h] - rating_slow[a]) / 200.0,
                    (rating_fast[h] - rating_fast[a]) / 200.0,
                    avg_last(hp, 5) - avg_last(ap, 5),
                    avg_last(hg, 5) - avg_last(ag, 5),
                    avg_last(hp, 10) - avg_last(ap, 10),
                    avg_last(hg, 10) - avg_last(ag, 10),
                    hr / 7.0,
                    ar / 7.0,
                    (hr - ar) / 7.0,
                ]
                pending.append((m, h, a))

            # Update only after every same-date prediction has been frozen.
            for m, h, a in pending:
                y = 1.0 if int(m.home_goals) > int(m.away_goals) else 0.5 if int(m.home_goals) == int(m.away_goals) else 0.0
                for ratings, k in ((rating_slow, 10.0), (rating_fast, 30.0)):
                    expected = 1.0 / (1.0 + 10.0 ** (-(ratings[h] - ratings[a] + 60.0) / 400.0))
                    delta = k * (y - expected)
                    ratings[h] += delta
                    ratings[a] -= delta
                if y == 1.0:
                    hpts, apts = 3.0, 0.0
                elif y == 0.5:
                    hpts = apts = 1.0
                else:
                    hpts, apts = 0.0, 3.0
                gd = float(int(m.home_goals) - int(m.away_goals))
                form_pts[h].append(hpts); form_pts[a].append(apts)
                form_gd[h].append(gd); form_gd[a].append(-gd)
                last_date[h] = date; last_date[a] = date
    return out


def _build_rows() -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    panel, panel_status = xgcore._load_panel()
    strict_rows, strict_audit = xgcore._strict_rows_with_xg()
    market_by_domain = {}
    market_audit = {}
    stats_by_domain = {}
    context_by_domain = {}
    for cid in DOMAINS:
        market_by_domain[cid], market_audit[cid] = _market_lookup(cid)
        stats_by_domain[cid], _ = shotmod._read_stat_rows(cid)
        context_by_domain[cid] = _context_map(cid)

    feature_names = [
        "market_home", "market_draw", "market_away", "market_side_logit", "market_draw_logit", "market_margin", "market_entropy",
        "formal_home", "formal_draw", "formal_away", "formal_side_logit", "formal_draw_logit", "formal_margin", "formal_entropy",
        "market_minus_formal_home", "market_minus_formal_draw", "market_minus_formal_away",
    ]
    xg_names = [
        "xg_edge_diff", "xg_edge_sum", "xg_edge_absdiff", "npxg_edge_diff", "npxg_edge_sum",
        "xpts_diff", "ppda_diff", "oppda_diff", "deep_edge_diff", "deep_edge_sum", "xg_min_history_scaled",
    ]
    shot_names = [
        "home_shot_chance", "away_shot_chance", "home_sot_chance", "away_sot_chance",
        "total_shot_chance", "total_sot_chance", "home_accuracy", "away_accuracy",
    ]
    ctx_names = [
        "elo_slow_diff", "elo_fast_diff", "form_pts5_diff", "form_gd5_diff", "form_pts10_diff", "form_gd10_diff",
        "home_rest_scaled", "away_rest_scaled", "rest_diff_scaled",
    ]
    hist_names = ["panel_home_n_scaled", "panel_away_n_scaled", "panel_min_n_scaled"]
    league_names = [f"league_{cid}" for cid in DOMAINS]
    feature_names.extend(xg_names + shot_names + ctx_names + hist_names + league_names)

    rows = []
    misses = Counter()
    for row in strict_rows:
        cid = str(row["competition_id"])
        if cid not in DOMAINS:
            continue
        season = str(row["season"])
        date = str(row["date"])
        home = str(row["home_team"])
        away = str(row["away_team"])
        htok = _token(cid, home); atok = _token(cid, away)
        market = market_by_domain[cid].get((season, date, htok, atok))
        if market is None:
            misses["market"] += 1
            continue
        pkey = (cid, season, date, home, away)
        p = panel.get(pkey)
        if p is None:
            misses["xg_panel"] += 1
            continue
        cutoff = datetime.fromisoformat(date + "T00:00:00+00:00")
        shot_history = shotmod._stat_history(stats_by_domain[cid], season, cutoff)
        shot_x = shotmod._shot_features(shot_history, home, away)
        if shot_x is None:
            misses["shot"] += 1
            continue
        context = context_by_domain[cid].get((season, date, htok, atok))
        if context is None:
            misses["context"] += 1
            continue

        formal_m = derive_score_marginals(row["formal_matrix"])["1x2"]
        formal = [float(formal_m[k]) for k in ("home", "draw", "away")]
        xg_x = [float(v) for v in xgmod._xg_features(p["home_prematch_state"], p["away_prematch_state"])[1:]]
        hn = int(p["home_prematch_state"]["n"]); an = int(p["away_prematch_state"]["n"])
        league_onehot = [1.0 if cid == d else 0.0 for d in DOMAINS]
        x = [
            *market, _side_logit(market), _draw_logit(market), _margin(market), _entropy(market),
            *formal, _side_logit(formal), _draw_logit(formal), _margin(formal), _entropy(formal),
            market[0] - formal[0], market[1] - formal[1], market[2] - formal[2],
            *xg_x,
            *[float(v) for v in shot_x],
            *[float(v) for v in context],
            hn / 20.0, an / 20.0, min(hn, an) / 20.0,
            *league_onehot,
        ]
        if len(x) != len(feature_names):
            raise RuntimeError(f"feature length mismatch {len(x)} != {len(feature_names)}")
        rows.append({
            "competition_id": cid,
            "season": season,
            "date": date,
            "home_team": home,
            "away_team": away,
            "y": _actual(int(row["home_goals"]), int(row["away_goals"])),
            "actual_score": [int(row["home_goals"]), int(row["away_goals"])],
            "market": market,
            "formal": formal,
            "x": x,
        })

    audit = {
        "panel_sha256": panel_status["panel_sha256"],
        "strict_rows": len(strict_rows),
        "eligible_rows": len(rows),
        "feature_count": len(feature_names),
        "misses": dict(misses),
        "market_audit": market_audit,
        "strict_audit": strict_audit,
    }
    return rows, audit, feature_names


def _fit(train: list[dict[str, Any]], depth: int, l2: float, draw_weight: float) -> CatBoostClassifier:
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=ITERATIONS,
        depth=int(depth),
        learning_rate=LEARNING_RATE,
        l2_leaf_reg=float(l2),
        random_seed=SEED,
        random_strength=0.5,
        bootstrap_type="Bayesian",
        bagging_temperature=0.5,
        allow_writing_files=False,
        verbose=False,
        thread_count=2,
    )
    weights = [float(draw_weight) if int(r["y"]) == 1 else 1.0 for r in train]
    model.fit([r["x"] for r in train], [int(r["y"]) for r in train], sample_weight=weights)
    return model


def _apply(model: CatBoostClassifier, rows: list[dict[str, Any]], blend: float, out_key: str) -> None:
    probs = model.predict_proba([r["x"] for r in rows])
    for row, q in zip(rows, probs):
        model_p = [float(v) for v in q]
        row[out_key] = _geometric_pool(model_p, row["market"], float(blend))


def _validation_select(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    folds = [
        (("2022/23",), "2023/24"),
        (("2022/23", "2023/24"), "2024/25"),
    ]
    leaderboard = []
    for depth in DEPTHS:
        for l2 in L2S:
            for draw_weight in DRAW_WEIGHTS:
                fold_cache = []
                for train_seasons, valid_season in folds:
                    train = [r for r in rows if r["season"] in train_seasons]
                    valid = [dict(r) for r in rows if r["season"] == valid_season]
                    model = _fit(train, depth, l2, draw_weight)
                    raw = model.predict_proba([r["x"] for r in valid])
                    fold_cache.append((valid, [[float(v) for v in q] for q in raw]))
                for blend in MARKET_BLEND_WEIGHTS:
                    fold_summaries = []
                    for valid, raw in fold_cache:
                        for row, q in zip(valid, raw):
                            row["candidate"] = _geometric_pool(q, row["market"], blend)
                        cm = _metrics(valid, "candidate")
                        mm = _metrics(valid, "market")
                        fold_summaries.append({"candidate": cm, "market": mm})
                    mean_top1 = sum(f["candidate"]["top1"] for f in fold_summaries) / len(fold_summaries)
                    mean_logloss = sum(f["candidate"]["logloss"] for f in fold_summaries) / len(fold_summaries)
                    mean_rps = sum(f["candidate"]["rps"] for f in fold_summaries) / len(fold_summaries)
                    mean_market_logloss = sum(f["market"]["logloss"] for f in fold_summaries) / len(fold_summaries)
                    mean_market_rps = sum(f["market"]["rps"] for f in fold_summaries) / len(fold_summaries)
                    leaderboard.append({
                        "depth": depth,
                        "l2": l2,
                        "draw_weight": draw_weight,
                        "market_blend": blend,
                        "mean_top1": mean_top1,
                        "mean_logloss": mean_logloss,
                        "mean_rps": mean_rps,
                        "mean_market_logloss": mean_market_logloss,
                        "mean_market_rps": mean_market_rps,
                        "proper_guard": bool(mean_logloss <= mean_market_logloss + 0.01 and mean_rps <= mean_market_rps + 0.01),
                        "folds": fold_summaries,
                    })
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(eligible, key=lambda x: (-x["mean_top1"], x["mean_logloss"], x["mean_rps"], x["depth"], x["l2"], x["draw_weight"], x["market_blend"]))
    leaderboard.sort(key=lambda x: (-x["mean_top1"], x["mean_logloss"], x["mean_rps"]))
    return selected, leaderboard[:20]


def main() -> int:
    rows, data_audit, feature_names = _build_rows()
    counts = Counter(r["season"] for r in rows)
    if any(counts[s] < 700 for s in TRAIN_SEASONS + (TEST_SEASON,)):
        raise RuntimeError(f"insufficient rows by season: {dict(counts)}")

    selected, leaderboard = _validation_select(rows)
    final_train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    test = [dict(r) for r in rows if r["season"] == TEST_SEASON]
    model = _fit(final_train, int(selected["depth"]), float(selected["l2"]), float(selected["draw_weight"]))
    _apply(model, test, float(selected["market_blend"]), "candidate")

    ordered = sorted(test, key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    random.Random(SEED).shuffle(ordered)
    sample = ordered[:TARGET]
    if len(sample) != TARGET:
        raise RuntimeError(f"random100 sample incomplete: {len(sample)}")

    sample_metrics = {
        "market": _metrics(sample, "market"),
        "formal": _metrics(sample, "formal"),
        "candidate": _metrics(sample, "candidate"),
    }
    full_metrics = {
        "market": _metrics(test, "market"),
        "formal": _metrics(test, "formal"),
        "candidate": _metrics(test, "candidate"),
    }

    by_comp = {}
    for cid in DOMAINS:
        rs = [r for r in sample if r["competition_id"] == cid]
        if rs:
            by_comp[cid] = {
                "count": len(rs),
                "market_top1": _metrics(rs, "market")["top1"],
                "formal_top1": _metrics(rs, "formal")["top1"],
                "candidate_top1": _metrics(rs, "candidate")["top1"],
            }

    importance = model.get_feature_importance()
    feature_importance = sorted(
        ({"feature": name, "importance": float(value)} for name, value in zip(feature_names, importance)),
        key=lambda x: x["importance"], reverse=True,
    )[:20]

    target_hit = sample_metrics["candidate"]["top1"] >= 0.65 - 1e-12
    payload = {
        "schema_version": "V6.32.0-direct-xg-shot-market-catboost-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "DEVELOPMENT_RESEARCH_FIXED_UNFILTERED_RANDOM100_NO_PROMOTION",
        "target": "1X2_TOP1_AT_LEAST_65_PERCENT_ON_ALL_100_RANDOM_MATCHES",
        "sample_contract": {
            "test_season": TEST_SEASON,
            "eligible_domains": list(DOMAINS),
            "seed": SEED,
            "sample_count": TARGET,
            "all_sampled_matches_scored": True,
            "confidence_filtering": False,
            "posthoc_league_dropping": False,
            "seed_replacement": False,
        },
        "data_audit": data_audit,
        "rows_by_season": dict(sorted(counts.items())),
        "model": {
            "library": "catboost",
            "iterations": ITERATIONS,
            "learning_rate": LEARNING_RATE,
            "selection_folds": ["2022/23->2023/24", "2022/23+2023/24->2024/25"],
            "grid": {
                "depth": list(DEPTHS),
                "l2_leaf_reg": list(L2S),
                "draw_weight": list(DRAW_WEIGHTS),
                "market_blend": list(MARKET_BLEND_WEIGHTS),
            },
            "selected": {k: selected[k] for k in ("depth", "l2", "draw_weight", "market_blend", "mean_top1", "mean_logloss", "mean_rps", "proper_guard")},
            "validation_leaderboard_top20": leaderboard,
            "feature_count": len(feature_names),
            "feature_importance_top20": feature_importance,
        },
        "full_2025_26_development_metrics": full_metrics,
        "random100": {
            "metrics": sample_metrics,
            "by_competition": by_comp,
            "candidate_vs_market_top1_pp": (sample_metrics["candidate"]["top1"] - sample_metrics["market"]["top1"]) * 100.0,
            "candidate_vs_formal_top1_pp": (sample_metrics["candidate"]["top1"] - sample_metrics["formal"]["top1"]) * 100.0,
            "target_65_reached": target_hit,
            "sample_rows": [
                {
                    "competition_id": r["competition_id"], "date": r["date"], "home_team": r["home_team"], "away_team": r["away_team"],
                    "actual_score": r["actual_score"], "actual_result": r["y"],
                    "market_pick": max(range(3), key=lambda i: r["market"][i]),
                    "formal_pick": max(range(3), key=lambda i: r["formal"][i]),
                    "candidate_pick": max(range(3), key=lambda i: r["candidate"][i]),
                    "candidate_probability": r["candidate"],
                }
                for r in sample
            ],
        },
        "decision": "TARGET_65_REACHED" if target_hit else "TARGET_65_NOT_REACHED",
        "governance": {
            "research_only": True,
            "historical_closing_market_has_no_original_quote_timestamp": True,
            "development_test_season_previously_viewed_in_older_research": True,
            "random100_cannot_promote_current": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "rows_by_season": payload["rows_by_season"],
        "selected": payload["model"]["selected"],
        "full_2025_26": payload["full_2025_26_development_metrics"],
        "random100": {k: v for k, v in payload["random100"].items() if k != "sample_rows"},
        "decision": payload["decision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
