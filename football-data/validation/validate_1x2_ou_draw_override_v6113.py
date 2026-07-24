#!/usr/bin/env python3
"""Research-only cross-season 1X2 draw-override test using O/U 2.5 market information.

The legacy 1X2 market is a strong Top-1 baseline but almost never selects draw as the
argmax. This diagnostic tests whether an independent total-goals market signal can
identify a subset of balanced, low-scoring matches where overriding the 1X2 market
pick to DRAW improves full-coverage Top-1 accuracy.

Strict diagnostic contract:
- older season only for fitting/rule selection;
- next season untouched for evaluation;
- no post-match features except labels during training/evaluation;
- legacy market prices are RETROSPECTIVE_REFERENCE_ONLY because original quote
  timestamps are not available for complete history;
- formal probability/weight/CURRENT remain unchanged.
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
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import canonical_team_name, load_aliases, load_registry, parse_match_date

OUT = ROOT / "manifests" / "v6_1x2_ou_draw_override_v6113_status.json"
DIRECTIONS = ("home", "draw", "away")
CALENDAR_YEAR_DOMAINS = {
    "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1", "KOR_KLeague1",
    "BRA_SerieA", "ARG_Primera", "USA_MLS",
}

ONE_X_TWO_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
    ("PSH", "PSD", "PSA", "Pinnacle"),
    ("B365H", "B365D", "B365A", "Bet365"),
    ("AvgH", "AvgD", "AvgA", "Average"),
)

OU25_PAIRS = (
    ("PC>2.5", "PC<2.5", "Pinnacle_closing_2.5"),
    ("B365C>2.5", "B365C<2.5", "Bet365_closing_2.5"),
    ("AvgC>2.5", "AvgC<2.5", "Average_closing_2.5"),
    ("MaxC>2.5", "MaxC<2.5", "Maximum_closing_2.5"),
    ("P>2.5", "P<2.5", "Pinnacle_2.5"),
    ("B365>2.5", "B365<2.5", "Bet365_2.5"),
    ("Avg>2.5", "Avg<2.5", "Average_2.5"),
    ("Max>2.5", "Max<2.5", "Maximum_2.5"),
)


def _odds(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def _devig3(h: float, d: float, a: float) -> dict[str, float]:
    q = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
    s = sum(q.values())
    return {k: q[k] / s for k in DIRECTIONS}


def _extract_1x2(raw: dict[str, str]):
    for hc, dc, ac, label in ONE_X_TWO_TRIPLETS:
        h, d, a = _odds(raw.get(hc)), _odds(raw.get(dc)), _odds(raw.get(ac))
        if h is not None and d is not None and a is not None:
            return _devig3(h, d, a), label
    return None


def _extract_ou25(raw: dict[str, str]):
    for oc, uc, label in OU25_PAIRS:
        o, u = _odds(raw.get(oc)), _odds(raw.get(uc))
        if o is None or u is None:
            continue
        qo, qu = 1.0 / o, 1.0 / u
        s = qo + qu
        return qo / s, qu / s, label
    return None


def _actual(raw: dict[str, str]) -> str | None:
    try:
        hg = int(float(str(raw.get("FTHG", "")).strip()))
        ag = int(float(str(raw.get("FTAG", "")).strip()))
    except (TypeError, ValueError):
        return None
    return "home" if hg > ag else "away" if hg < ag else "draw"


def _target_seasons(cid: str) -> tuple[str, str]:
    return ("2024", "2025") if cid in CALENDAR_YEAR_DOMAINS else ("2024/25", "2025/26")


def _entropy(p: dict[str, float]) -> float:
    return -sum(v * math.log(max(v, 1e-15)) for v in p.values())


def _load_rows():
    aliases = load_aliases()
    competitions = [str(x["competition_id"]) for x in load_registry()["competitions"]]
    rows = []
    seen = set()
    x12_sources = Counter()
    ou_sources = Counter()
    matched_by_comp = Counter()
    for cid in competitions:
        older, newer = _target_seasons(cid)
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw0 in csv.DictReader(handle):
                    raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in {older, newer}:
                        continue
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):
                        continue
                    actual = _actual(raw)
                    x12 = _extract_1x2(raw)
                    ou = _extract_ou25(raw)
                    if actual is None or x12 is None or ou is None:
                        continue
                    try:
                        dt = parse_match_date(raw["Date"], season)
                    except Exception:
                        continue
                    home = canonical_team_name(cid, raw["HomeTeam"], aliases)
                    away = canonical_team_name(cid, raw["AwayTeam"], aliases)
                    key = (cid, season, dt.date().isoformat(), home, away)
                    if key in seen:
                        continue
                    seen.add(key)
                    p, xsrc = x12
                    p_over, p_under, osrc = ou
                    order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "bucket": "older" if season == older else "newer",
                        "date": dt.isoformat(),
                        "actual": actual,
                        "market_home": p["home"],
                        "market_draw": p["draw"],
                        "market_away": p["away"],
                        "market_max": p[order[0]],
                        "market_margin": p[order[0]] - p[order[1]],
                        "home_away_gap": abs(p["home"] - p["away"]),
                        "market_entropy": _entropy(p),
                        "p_over25": p_over,
                        "p_under25": p_under,
                        "under_logit": math.log(max(p_under, 1e-9) / max(p_over, 1e-9)),
                    })
                    x12_sources[xsrc] += 1
                    ou_sources[osrc] += 1
                    matched_by_comp[cid] += 1
    rows.sort(key=lambda r: (r["competition_id"], r["date"]))
    return rows, {
        "x12_source_counts": dict(x12_sources),
        "ou25_source_counts": dict(ou_sources),
        "matched_by_competition": dict(matched_by_comp),
    }


def _market_probs(r):
    return {k: float(r[f"market_{k}"]) for k in DIRECTIONS}


def _market_pick(r):
    p = _market_probs(r)
    return max(DIRECTIONS, key=lambda k: p[k])


def _chronological_older_split(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["bucket"] == "older":
            groups[r["competition_id"]].append(r)
    train, validation = [], []
    for cid, items in groups.items():
        items.sort(key=lambda r: r["date"])
        if len(items) < 20:
            continue
        cut = max(1, min(len(items) - 1, int(0.80 * len(items))))
        train.extend(items[:cut])
        validation.extend(items[cut:])
    return train, validation


def _features(r, comps):
    vals = [
        r["market_home"], r["market_draw"], r["market_away"],
        r["market_max"], r["market_margin"], r["home_away_gap"], r["market_entropy"],
        r["p_under25"], r["under_logit"],
        r["market_draw"] * r["p_under25"],
        r["home_away_gap"] * r["p_under25"],
    ]
    vals.extend(1.0 if r["competition_id"] == c else 0.0 for c in comps)
    return vals


def _fit_draw_model(train, comps, c_value):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    x = [_features(r, comps) for r in train]
    y = [1 if r["actual"] == "draw" else 0 for r in train]
    model = make_pipeline(StandardScaler(), LogisticRegression(C=c_value, max_iter=3000, class_weight=None))
    model.fit(x, y)
    return model


def _draw_prob(model, r, comps):
    classes = list(model.classes_)
    idx = classes.index(1)
    return float(model.predict_proba([_features(r, comps)])[0][idx])


def _eval_override(rows, score_fn, threshold):
    hits = 0
    overrides = 0
    correct_overrides = 0
    harmful_overrides = 0
    picks = Counter()
    for r in rows:
        base = _market_pick(r)
        score = score_fn(r)
        pick = "draw" if score >= threshold else base
        if pick == "draw" and base != "draw":
            overrides += 1
            if r["actual"] == "draw":
                correct_overrides += 1
            elif base == r["actual"]:
                harmful_overrides += 1
        picks[pick] += 1
        hits += int(pick == r["actual"])
    n = len(rows)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "threshold": threshold,
        "override_count": overrides,
        "correct_draw_overrides": correct_overrides,
        "harmful_overrides": harmful_overrides,
        "pick_counts": dict(picks),
    }


def _market_metrics(rows):
    hits = sum(1 for r in rows if _market_pick(r) == r["actual"])
    return {
        "count": len(rows),
        "hits": hits,
        "accuracy": hits / len(rows) if rows else None,
        "pick_counts": dict(Counter(_market_pick(r) for r in rows)),
        "actual_counts": dict(Counter(r["actual"] for r in rows)),
    }


def _choose_ml(train, validation, comps):
    candidates = []
    for c in (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0):
        model = _fit_draw_model(train, comps, c)
        scores = {id(r): _draw_prob(model, r, comps) for r in validation}
        for t_i in range(15, 51):
            threshold = t_i / 100.0
            met = _eval_override(validation, lambda r, s=scores: s[id(r)], threshold)
            candidates.append({"C": c, **met})
    candidates.sort(key=lambda x: (-x["accuracy"], x["override_count"], x["C"], x["threshold"]))
    return candidates[0], candidates[:20]


def _simple_score(r, draw_min, gap_max, under_min):
    return 1.0 if (r["market_draw"] >= draw_min and r["home_away_gap"] <= gap_max and r["p_under25"] >= under_min) else 0.0


def _choose_simple(validation):
    candidates = []
    for d_i in range(22, 36, 2):
        draw_min = d_i / 100.0
        for g_i in range(2, 26, 2):
            gap_max = g_i / 100.0
            for u_i in range(44, 71, 3):
                under_min = u_i / 100.0
                met = _eval_override(
                    validation,
                    lambda r, dm=draw_min, gm=gap_max, um=under_min: _simple_score(r, dm, gm, um),
                    0.5,
                )
                candidates.append({"draw_min": draw_min, "gap_max": gap_max, "under_min": under_min, **met})
    candidates.sort(key=lambda x: (-x["accuracy"], x["override_count"], x["draw_min"], x["gap_max"], x["under_min"]))
    return candidates[0], candidates[:20]


def main():
    rows, meta = _load_rows()
    older = [r for r in rows if r["bucket"] == "older"]
    newer = [r for r in rows if r["bucket"] == "newer"]
    if len(older) < 500 or len(newer) < 500:
        raise RuntimeError(f"insufficient 1X2+OU2.5 matched rows older={len(older)} newer={len(newer)}")
    train, validation = _chronological_older_split(rows)
    comps = sorted({r["competition_id"] for r in older})

    baseline_validation = _market_metrics(validation)
    baseline_test = _market_metrics(newer)

    ml_best, ml_leader = _choose_ml(train, validation, comps)
    final_ml = _fit_draw_model(older, comps, float(ml_best["C"]))
    test_ml = _eval_override(newer, lambda r: _draw_prob(final_ml, r, comps), float(ml_best["threshold"]))

    simple_best, simple_leader = _choose_simple(validation)
    test_simple = _eval_override(
        newer,
        lambda r: _simple_score(r, simple_best["draw_min"], simple_best["gap_max"], simple_best["under_min"]),
        0.5,
    )

    selected_family = "ml" if ml_best["accuracy"] >= simple_best["accuracy"] else "simple"
    selected_test = test_ml if selected_family == "ml" else test_simple

    by_comp = {}
    for cid in sorted({r["competition_id"] for r in newer}):
        subset = [r for r in newer if r["competition_id"] == cid]
        if len(subset) < 10:
            continue
        base = _market_metrics(subset)
        if selected_family == "ml":
            cand = _eval_override(subset, lambda r: _draw_prob(final_ml, r, comps), float(ml_best["threshold"]))
        else:
            cand = _eval_override(
                subset,
                lambda r: _simple_score(r, simple_best["draw_min"], simple_best["gap_max"], simple_best["under_min"]),
                0.5,
            )
        by_comp[cid] = {
            "count": len(subset),
            "market_accuracy": base["accuracy"],
            "selected_accuracy": cand["accuracy"],
            "uplift_pp": (cand["accuracy"] - base["accuracy"]) * 100.0,
            "override_count": cand["override_count"],
        }

    payload = {
        "schema_version": "V6.11.3-ou-informed-draw-override-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "market_data_classification": "RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "sample": {
            "matched_total": len(rows),
            "older_rows": len(older),
            "newer_rows": len(newer),
            "older_train": len(train),
            "older_validation": len(validation),
            "competitions": comps,
            **meta,
        },
        "older_validation": {
            "market": baseline_validation,
            "ml_selected_rule": ml_best,
            "ml_leaderboard": ml_leader,
            "simple_selected_rule": simple_best,
            "simple_leaderboard": simple_leader,
            "selected_family": selected_family,
        },
        "newer_season_test": {
            "market": baseline_test,
            "ml_override": test_ml,
            "simple_override": test_simple,
            "selected_family": selected_family,
            "selected": selected_test,
            "selected_vs_market_uplift_pp": (selected_test["accuracy"] - baseline_test["accuracy"]) * 100.0,
        },
        "by_competition": by_comp,
        "governance": {
            "research_only": True,
            "newer_season_never_used_for_rule_selection": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
            "promotion_requires_timestamped_forward_validation": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
