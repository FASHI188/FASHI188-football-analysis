#!/usr/bin/env python3
"""Research-only cross-season test of a two-beta FL-GLM draw-bias extension.

Inspired by the future-work extension proposed in Goto, Takeishi & Yairi (2026):
use one power constant for decisive outcomes (home/away) and a separate constant
for draws, then normalise the powered inverse odds.

Unlike the original single-beta FL-GLM, this extension can change 1X2 Top-1
ordering and therefore can be tested against the user's primary KPI: hit rate.

Historical prices are retrospective references only and cannot alter CURRENT.
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

OUT = ROOT / "manifests" / "v6_1x2_two_beta_draw_v6114_status.json"
DIRECTIONS = ("home", "draw", "away")
CALENDAR_YEAR_DOMAINS = {
    "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1", "KOR_KLeague1",
    "BRA_SerieA", "ARG_Primera", "USA_MLS",
}
ODDS_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
    ("PSH", "PSD", "PSA", "Pinnacle"),
    ("B365H", "B365D", "B365A", "Bet365"),
    ("AvgH", "AvgD", "AvgA", "Average"),
)


def _odds(x: Any) -> float | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def _target_seasons(cid: str):
    return ("2024", "2025") if cid in CALENDAR_YEAR_DOMAINS else ("2024/25", "2025/26")


def _actual(raw):
    try:
        h = int(float(raw.get("FTHG", "")))
        a = int(float(raw.get("FTAG", "")))
    except (TypeError, ValueError):
        return None
    return "home" if h > a else "away" if h < a else "draw"


def _extract(raw):
    for hc, dc, ac, label in ODDS_TRIPLETS:
        h, d, a = _odds(raw.get(hc)), _odds(raw.get(dc)), _odds(raw.get(ac))
        if h is not None and d is not None and a is not None:
            return h, d, a, label
    return None


def _load_rows():
    aliases = load_aliases()
    rows = []
    seen = set()
    sources = Counter()
    for item in load_registry()["competitions"]:
        cid = str(item["competition_id"])
        older, newer = _target_seasons(cid)
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw0 in csv.DictReader(handle):
                    raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in {older, newer} or not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):
                        continue
                    actual = _actual(raw)
                    extracted = _extract(raw)
                    if actual is None or extracted is None:
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
                    oh, od, oa, provider = extracted
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "bucket": "older" if season == older else "newer",
                        "date": dt.isoformat(),
                        "actual": actual,
                        "odds_home": oh,
                        "odds_draw": od,
                        "odds_away": oa,
                        "provider": provider,
                    })
                    sources[provider] += 1
    rows.sort(key=lambda r: (r["competition_id"], r["date"]))
    return rows, dict(sources)


def _probs(r, beta_decisive=1.0, beta_draw=1.0):
    qh = (1.0 / r["odds_home"]) ** beta_decisive
    qd = (1.0 / r["odds_draw"]) ** beta_draw
    qa = (1.0 / r["odds_away"]) ** beta_decisive
    s = qh + qd + qa
    return {"home": qh / s, "draw": qd / s, "away": qa / s}


def _metrics(rows, beta_fn):
    hits = 0
    brier = 0.0
    logloss = 0.0
    picks = Counter()
    for r in rows:
        bd, br = beta_fn(r)
        p = _probs(r, bd, br)
        pick = max(DIRECTIONS, key=lambda k: p[k])
        picks[pick] += 1
        hits += int(pick == r["actual"])
        brier += sum((p[k] - (1.0 if k == r["actual"] else 0.0)) ** 2 for k in DIRECTIONS)
        logloss += -math.log(max(p[r["actual"]], 1e-15))
    n = len(rows)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "brier": brier / n if n else None,
        "log_loss": logloss / n if n else None,
        "pick_counts": dict(picks),
    }


def _logloss(rows, bd, br):
    total = 0.0
    for r in rows:
        p = _probs(r, bd, br)
        total += -math.log(max(p[r["actual"]], 1e-15))
    return total / len(rows)


def _fit_grid(rows):
    best = None
    # Broad coarse grid followed by a local fine grid around the best point.
    for bd_i in range(70, 161, 5):
        bd = bd_i / 100.0
        for br_i in range(60, 151, 5):
            br = br_i / 100.0
            loss = _logloss(rows, bd, br)
            cand = (loss, bd, br)
            if best is None or cand < best:
                best = cand
    _, cbd, cbr = best
    fine = None
    for bd_i in range(round((cbd - 0.08) * 100), round((cbd + 0.08) * 100) + 1):
        bd = bd_i / 100.0
        if bd <= 0:
            continue
        for br_i in range(round((cbr - 0.08) * 100), round((cbr + 0.08) * 100) + 1):
            br = br_i / 100.0
            if br <= 0:
                continue
            loss = _logloss(rows, bd, br)
            cand = (loss, bd, br)
            if fine is None or cand < fine:
                fine = cand
    loss, bd, br = fine
    return {"beta_decisive": bd, "beta_draw": br, "train_log_loss": loss}


def _chronological_split(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["bucket"] == "older":
            groups[r["competition_id"]].append(r)
    train, validation = [], []
    for cid, items in groups.items():
        items.sort(key=lambda r: r["date"])
        if len(items) < 20:
            continue
        cut = max(1, min(len(items) - 1, int(0.8 * len(items))))
        train.extend(items[:cut])
        validation.extend(items[cut:])
    return train, validation


def _provider_fit(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["provider"]].append(r)
    global_fit = _fit_grid(rows)
    fits = {}
    for provider, items in grouped.items():
        fits[provider] = _fit_grid(items) if len(items) >= 200 else dict(global_fit)
    return global_fit, fits


def _selective(rows, beta_fn):
    out = {}
    for t in (0.56, 0.58, 0.60):
        selected = []
        for r in rows:
            bd, br = beta_fn(r)
            p = _probs(r, bd, br)
            pick = max(DIRECTIONS, key=lambda k: p[k])
            if p[pick] >= t:
                selected.append((r, pick))
        hits = sum(1 for r, pick in selected if pick == r["actual"])
        out[f"p_ge_{t:.2f}"] = {
            "count": len(selected),
            "coverage": len(selected) / len(rows) if rows else 0.0,
            "hits": hits,
            "accuracy": hits / len(selected) if selected else None,
        }
    return out


def main():
    rows, source_counts = _load_rows()
    older = [r for r in rows if r["bucket"] == "older"]
    newer = [r for r in rows if r["bucket"] == "newer"]
    if len(older) < 1000 or len(newer) < 1000:
        raise RuntimeError(f"insufficient market rows older={len(older)} newer={len(newer)}")
    train, validation = _chronological_split(rows)

    train_global = _fit_grid(train)
    train_global_fn = lambda r: (train_global["beta_decisive"], train_global["beta_draw"])
    val_global = _metrics(validation, train_global_fn)

    global_base, provider_fits_train = _provider_fit(train)
    provider_fn_train = lambda r: (
        provider_fits_train.get(r["provider"], global_base)["beta_decisive"],
        provider_fits_train.get(r["provider"], global_base)["beta_draw"],
    )
    val_provider = _metrics(validation, provider_fn_train)
    val_market = _metrics(validation, lambda r: (1.0, 1.0))

    selected_family = "provider_two_beta" if val_provider["accuracy"] > val_global["accuracy"] else "global_two_beta"

    full_global = _fit_grid(older)
    global_fn = lambda r: (full_global["beta_decisive"], full_global["beta_draw"])
    global_test = _metrics(newer, global_fn)

    provider_global, provider_fits = _provider_fit(older)
    provider_fn = lambda r: (
        provider_fits.get(r["provider"], provider_global)["beta_decisive"],
        provider_fits.get(r["provider"], provider_global)["beta_draw"],
    )
    provider_test = _metrics(newer, provider_fn)
    market_test = _metrics(newer, lambda r: (1.0, 1.0))

    selected_fn = provider_fn if selected_family == "provider_two_beta" else global_fn
    selected_test = provider_test if selected_family == "provider_two_beta" else global_test

    by_comp = {}
    for cid in sorted({r["competition_id"] for r in newer}):
        subset = [r for r in newer if r["competition_id"] == cid]
        if len(subset) < 10:
            continue
        m = _metrics(subset, lambda r: (1.0, 1.0))
        c = _metrics(subset, selected_fn)
        by_comp[cid] = {
            "count": len(subset),
            "market_accuracy": m["accuracy"],
            "two_beta_accuracy": c["accuracy"],
            "uplift_pp": (c["accuracy"] - m["accuracy"]) * 100.0,
        }

    payload = {
        "schema_version": "V6.11.4-two-beta-draw-bias-1x2-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "market_data_classification": "RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "sample": {
            "total": len(rows),
            "older": len(older),
            "newer": len(newer),
            "older_train": len(train),
            "older_validation": len(validation),
            "provider_counts": source_counts,
        },
        "older_validation": {
            "market": val_market,
            "global_two_beta_fit": train_global,
            "global_two_beta": val_global,
            "provider_two_beta_fits": provider_fits_train,
            "provider_two_beta": val_provider,
            "selected_family": selected_family,
        },
        "newer_season_test": {
            "market": market_test,
            "global_fit": full_global,
            "global_two_beta": global_test,
            "provider_fits": provider_fits,
            "provider_two_beta": provider_test,
            "selected_family": selected_family,
            "selected": selected_test,
            "selected_vs_market_uplift_pp": (selected_test["accuracy"] - market_test["accuracy"]) * 100.0,
            "market_selective": _selective(newer, lambda r: (1.0, 1.0)),
            "selected_selective": _selective(newer, selected_fn),
        },
        "by_competition": by_comp,
        "governance": {
            "research_only": True,
            "newer_season_never_used_for_fit_or_family_selection": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
