#!/usr/bin/env python3
"""Research-only cross-season test of Asian-handicap information for 1X2 accuracy.

Purpose
-------
Test whether legacy Asian-handicap (AH) lines/odds add out-of-sample 1X2 Top-1
accuracy beyond the repository's best available legacy closing 1X2 market anchor.

This is deliberately a diagnostic, not a formal CURRENT probability module:
- legacy prices do not carry original quote timestamps;
- no result from this script may change formal/runtime probability weights;
- older season is used for training/validation only;
- the immediately following season is an untouched test set.

The implementation is empirical rather than a claim to reproduce Hegarty & Whelan
exactly. It tests the economically relevant proposition: whether AH line + side price
contains incremental information for home/draw/away classification.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import canonical_team_name, load_aliases, load_registry, parse_match_date

OUT = ROOT / "manifests" / "v6_1x2_asian_handicap_increment_v6112_status.json"
DIRECTIONS = ("home", "draw", "away")
CALENDAR_YEAR_DOMAINS = {
    "SWE_Allsvenskan",
    "NOR_Eliteserien",
    "JPN_J1",
    "KOR_KLeague1",
    "BRA_SerieA",
    "ARG_Primera",
    "USA_MLS",
}

# 1X2 legacy closing first, then legacy pre-close fallback. These are retrospective
# references only because original quote timestamps are unavailable for the full history.
ONE_X_TWO_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
    ("PSH", "PSD", "PSA", "Pinnacle"),
    ("B365H", "B365D", "B365A", "Bet365"),
    ("AvgH", "AvgD", "AvgA", "Average"),
)

# football-data.co.uk has used several generations of AH column names. The line is
# match-level; side odds may come from the preferred sharp/closing pair when present.
AH_SPECS = (
    ("AHCh", ("PCAHH", "PCAHA"), "Pinnacle_closing"),
    ("AHCh", ("B365CAHH", "B365CAHA"), "Bet365_closing"),
    ("AHCh", ("AvgCAHH", "AvgCAHA"), "Average_closing"),
    ("AHCh", ("MaxCAHH", "MaxCAHA"), "Maximum_closing"),
    ("AHh", ("PAHH", "PAHA"), "Pinnacle"),
    ("AHh", ("B365AHH", "B365AHA"), "Bet365"),
    ("AHh", ("AvgAHH", "AvgAHA"), "Average"),
    ("AHh", ("MaxAHH", "MaxAHA"), "Maximum"),
    ("BbAHh", ("BbAvAHH", "BbAvAHA"), "Betbrain_average"),
    ("BbAHh", ("BbMxAHH", "BbMxAHA"), "Betbrain_maximum"),
)


def _float(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _odds(value: Any) -> float | None:
    x = _float(value)
    return x if x is not None and x > 1.0 else None


def _devig_three(h: float, d: float, a: float) -> dict[str, float]:
    raw = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
    total = sum(raw.values())
    return {k: raw[k] / total for k in DIRECTIONS}


def _devig_two(home_odds: float, away_odds: float) -> tuple[float, float]:
    h = 1.0 / home_odds
    a = 1.0 / away_odds
    total = h + a
    return h / total, a / total


def _extract_1x2(raw: dict[str, str]) -> tuple[dict[str, float], str] | None:
    for hc, dc, ac, label in ONE_X_TWO_TRIPLETS:
        h, d, a = _odds(raw.get(hc)), _odds(raw.get(dc)), _odds(raw.get(ac))
        if h is not None and d is not None and a is not None:
            return _devig_three(h, d, a), label
    return None


def _extract_ah(raw: dict[str, str]) -> tuple[float, float, float, str] | None:
    for line_col, (home_col, away_col), label in AH_SPECS:
        line = _float(raw.get(line_col))
        oh, oa = _odds(raw.get(home_col)), _odds(raw.get(away_col))
        if line is None or oh is None or oa is None:
            continue
        # Defensive sanity checks. AH football lines outside +/-6 are treated as malformed.
        if abs(line) > 6.0:
            continue
        ph, pa = _devig_two(oh, oa)
        return line, ph, pa, label
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


def _read_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aliases = load_aliases()
    registry = load_registry()
    competitions = [str(item["competition_id"]) for item in registry["competitions"]]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    x12_sources = Counter()
    ah_sources = Counter()
    raw_by_comp = Counter()
    matched_by_comp = Counter()

    for cid in competitions:
        older, newer = _target_seasons(cid)
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for raw0 in reader:
                    raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in {older, newer}:
                        continue
                    raw_by_comp[cid] += 1
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):
                        continue
                    actual = _actual(raw)
                    x12 = _extract_1x2(raw)
                    ah = _extract_ah(raw)
                    if actual is None or x12 is None or ah is None:
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
                    market, x12_source = x12
                    line, ah_home_cover, ah_away_cover, ah_source = ah
                    order = sorted(DIRECTIONS, key=lambda k: market[k], reverse=True)
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "bucket": "older" if season == older else "newer",
                        "date": dt.isoformat(),
                        "home_team": home,
                        "away_team": away,
                        "actual": actual,
                        "market_home": market["home"],
                        "market_draw": market["draw"],
                        "market_away": market["away"],
                        "market_max": market[order[0]],
                        "market_margin": market[order[0]] - market[order[1]],
                        "market_entropy": _entropy(market),
                        "ah_line_home": line,
                        "ah_home_cover": ah_home_cover,
                        "ah_away_cover": ah_away_cover,
                        "ah_price_logit": math.log(max(ah_home_cover, 1e-9) / max(ah_away_cover, 1e-9)),
                        "x12_source": x12_source,
                        "ah_source": ah_source,
                    })
                    x12_sources[x12_source] += 1
                    ah_sources[ah_source] += 1
                    matched_by_comp[cid] += 1

    rows.sort(key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    meta = {
        "raw_candidate_rows_by_competition": dict(raw_by_comp),
        "matched_rows_by_competition": dict(matched_by_comp),
        "x12_source_counts": dict(x12_sources),
        "ah_source_counts": dict(ah_sources),
    }
    return rows, meta


def _market_probs(row: dict[str, Any]) -> dict[str, float]:
    return {k: float(row[f"market_{k}"]) for k in DIRECTIONS}


def _pick(p: dict[str, float]) -> str:
    return max(DIRECTIONS, key=lambda k: p[k])


def _metrics(rows: list[dict[str, Any]], prob_fn: Callable[[dict[str, Any]], dict[str, float]]) -> dict[str, Any]:
    hits = 0
    brier = 0.0
    logloss = 0.0
    pick_counts = Counter()
    for row in rows:
        p = prob_fn(row)
        pick = _pick(p)
        pick_counts[pick] += 1
        actual = row["actual"]
        hits += int(pick == actual)
        brier += sum((p[k] - (1.0 if k == actual else 0.0)) ** 2 for k in DIRECTIONS)
        logloss += -math.log(max(p[actual], 1e-15))
    n = len(rows)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "brier": brier / n if n else None,
        "log_loss": logloss / n if n else None,
        "pick_counts": dict(pick_counts),
    }


def _feature(row: dict[str, Any], family: str, competitions: list[str]) -> list[float]:
    line = float(row["ah_line_home"])
    cover = float(row["ah_home_cover"])
    logit = float(row["ah_price_logit"])
    base_ah = [
        line,
        abs(line),
        line * line,
        cover,
        logit,
        line * logit,
    ]
    if family == "ah_only":
        values = base_ah
    elif family == "combined":
        values = [
            float(row["market_home"]),
            float(row["market_draw"]),
            float(row["market_away"]),
            float(row["market_max"]),
            float(row["market_margin"]),
            float(row["market_entropy"]),
        ] + base_ah
    else:
        raise ValueError(f"unknown family: {family}")
    cid = row["competition_id"]
    values.extend(1.0 if cid == comp else 0.0 for comp in competitions)
    return values


def _chronological_split_older(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["bucket"] == "older":
            grouped[row["competition_id"]].append(row)
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for cid, items in grouped.items():
        items.sort(key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        if len(items) < 20:
            continue
        cut = max(1, min(len(items) - 1, int(len(items) * 0.80)))
        train.extend(items[:cut])
        validation.extend(items[cut:])
    return train, validation


def _fit_model(train: list[dict[str, Any]], family: str, c_value: float, competitions: list[str]):
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for V6.11.2 research validation") from exc

    x = [_feature(r, family, competitions) for r in train]
    y = [r["actual"] for r in train]
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=c_value, max_iter=3000, solver="lbfgs"),
    )
    model.fit(x, y)
    return model


def _prob_fn(model, family: str, competitions: list[str]):
    classes = list(model.classes_)
    index = {label: i for i, label in enumerate(classes)}

    def predict(row: dict[str, Any]) -> dict[str, float]:
        arr = model.predict_proba([_feature(row, family, competitions)])[0]
        return {k: float(arr[index[k]]) for k in DIRECTIONS}

    return predict


def _select_family(train, validation, family: str, competitions: list[str]) -> dict[str, Any]:
    candidates = []
    for c_value in (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0):
        model = _fit_model(train, family, c_value, competitions)
        fn = _prob_fn(model, family, competitions)
        met = _metrics(validation, fn)
        candidates.append({"C": c_value, **met})
    candidates.sort(key=lambda x: (-x["accuracy"], x["brier"], x["log_loss"], x["C"]))
    return {"family": family, "selected_C": candidates[0]["C"], "leaderboard": candidates}


def _paired(rows: list[dict[str, Any]], a_fn, b_fn) -> dict[str, int]:
    out = Counter()
    for row in rows:
        a = _pick(a_fn(row)) == row["actual"]
        b = _pick(b_fn(row)) == row["actual"]
        if a and b:
            out["both_correct"] += 1
        elif a:
            out["a_only_correct"] += 1
        elif b:
            out["b_only_correct"] += 1
        else:
            out["both_wrong"] += 1
    return dict(out)


def _selective(rows: list[dict[str, Any]], fn, thresholds=(0.56, 0.58, 0.60)) -> dict[str, Any]:
    result = {}
    for threshold in thresholds:
        chosen = []
        for row in rows:
            p = fn(row)
            pick = _pick(p)
            if p[pick] >= threshold:
                chosen.append((row, pick))
        hits = sum(1 for row, pick in chosen if pick == row["actual"])
        result[f"p_ge_{threshold:.2f}"] = {
            "count": len(chosen),
            "coverage": len(chosen) / len(rows) if rows else 0.0,
            "hits": hits,
            "accuracy": hits / len(chosen) if chosen else None,
        }
    return result


def main() -> int:
    rows, source_meta = _read_rows()
    older = [r for r in rows if r["bucket"] == "older"]
    newer = [r for r in rows if r["bucket"] == "newer"]
    if len(older) < 500 or len(newer) < 500:
        raise RuntimeError(f"insufficient AH-matched rows: older={len(older)} newer={len(newer)}")

    train, validation = _chronological_split_older(rows)
    if len(train) < 400 or len(validation) < 100:
        raise RuntimeError(f"insufficient older chronological split: train={len(train)} validation={len(validation)}")

    competitions = sorted({r["competition_id"] for r in older})
    family_selections = {
        family: _select_family(train, validation, family, competitions)
        for family in ("ah_only", "combined")
    }

    models = {}
    fns = {}
    for family, selection in family_selections.items():
        model = _fit_model(older, family, float(selection["selected_C"]), competitions)
        models[family] = model
        fns[family] = _prob_fn(model, family, competitions)

    market_fn = _market_probs
    newer_market = _metrics(newer, market_fn)
    newer_ah = _metrics(newer, fns["ah_only"])
    newer_combined = _metrics(newer, fns["combined"])

    by_comp = {}
    for cid in sorted({r["competition_id"] for r in newer}):
        subset = [r for r in newer if r["competition_id"] == cid]
        if len(subset) < 10:
            continue
        m = _metrics(subset, market_fn)
        a = _metrics(subset, fns["ah_only"])
        c = _metrics(subset, fns["combined"])
        by_comp[cid] = {
            "count": len(subset),
            "market_accuracy": m["accuracy"],
            "ah_only_accuracy": a["accuracy"],
            "combined_accuracy": c["accuracy"],
            "combined_vs_market_uplift_pp": (c["accuracy"] - m["accuracy"]) * 100.0,
        }

    payload = {
        "schema_version": "V6.11.2-asian-handicap-incremental-1x2-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "market_data_classification": "RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "method_scope": "empirical AH incremental-information test; not an exact paper-method replication",
        "sample": {
            "matched_total": len(rows),
            "older_season_rows": len(older),
            "newer_season_rows": len(newer),
            "older_train_rows": len(train),
            "older_validation_rows": len(validation),
            "competitions": competitions,
            **source_meta,
        },
        "selection": family_selections,
        "newer_season_test": {
            "market_1x2": newer_market,
            "ah_only": newer_ah,
            "combined_1x2_plus_ah": newer_combined,
            "ah_only_vs_market_uplift_pp": (newer_ah["accuracy"] - newer_market["accuracy"]) * 100.0,
            "combined_vs_market_uplift_pp": (newer_combined["accuracy"] - newer_market["accuracy"]) * 100.0,
            "paired_combined_vs_market": _paired(newer, fns["combined"], market_fn),
            "selective_market": _selective(newer, market_fn),
            "selective_combined": _selective(newer, fns["combined"]),
        },
        "by_competition": by_comp,
        "governance": {
            "research_only": True,
            "newer_season_never_used_for_model_or_hyperparameter_selection": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
            "promotion_requires_separate_forward_and_timestamped_market_validation": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
