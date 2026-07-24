#!/usr/bin/env python3
"""Research-only retrospective 1X2 market-anchor diagnostic.

Uses the V6.9.6 cached strict-PIT model probabilities and attempts to match legacy
pre-match/closing 1X2 odds columns already present in processed CSV files. Because
these legacy prices do not have original quote timestamps, they are explicitly
RETROSPECTIVE_REFERENCE_ONLY and can never satisfy the formal CURRENT snapshot gate.

Goal: test whether market-only or market/model blending materially improves 1X2
Top-1 accuracy on repeated 100-match holdouts and one untouched 100-match audit.
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import canonical_team_name, load_aliases, load_json, parse_match_date

CACHE = ROOT / "validation" / "cache" / "v696_last_complete_season_1x2_rows.json"
OUT = ROOT / "manifests" / "v6_1x2_market_anchor_diagnostic_v697_status.json"
DIRECTIONS = ("home", "draw", "away")
SEED = 20260724
AUDIT_N = 100
DEV_N = 100
TEST_N = 100
REPEATS = 30

# Ordered by preference. All are legacy retrospective references here because the
# repository does not have original quote timestamps for the complete history.
ODDS_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
    ("PSH", "PSD", "PSA", "Pinnacle"),
    ("B365H", "B365D", "B365A", "Bet365"),
    ("AvgH", "AvgD", "AvgA", "Average"),
    ("WHH", "WHD", "WHA", "WilliamHill"),
    ("MaxH", "MaxD", "MaxA", "Maximum"),
)


def _key(cid: str, season: str, date_iso: str, home: str, away: str) -> tuple[str, str, str, str, str]:
    return cid, season, date_iso, home, away


def _float_odds(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if x > 1.0 else None


def _devig(h: float, d: float, a: float) -> dict[str, float]:
    raw = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
    total = sum(raw.values())
    return {k: raw[k] / total for k in DIRECTIONS}


def _extract_odds(raw: dict[str, str]) -> tuple[dict[str, float], str] | None:
    for hc, dc, ac, label in ODDS_TRIPLETS:
        h, d, a = _float_odds(raw.get(hc)), _float_odds(raw.get(dc)), _float_odds(raw.get(ac))
        if h is not None and d is not None and a is not None:
            return _devig(h, d, a), label
    return None


def _load_model_rows() -> list[dict[str, Any]]:
    payload = load_json(CACHE)
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("V6.9.6 row cache missing/empty")
    return rows


def _match_market(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    aliases = load_aliases()
    lookup = {
        _key(r["competition_id"], r["season"], r["date"], r["home_team"], r["away_team"]): r
        for r in rows
    }
    matched: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    providers = Counter()
    for cid in sorted({r["competition_id"] for r in rows}):
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for raw in reader:
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):
                        continue
                    try:
                        date_iso = parse_match_date(str(raw.get("Date")), season).isoformat()
                    except Exception:
                        continue
                    home = canonical_team_name(cid, str(raw.get("HomeTeam")), aliases)
                    away = canonical_team_name(cid, str(raw.get("AwayTeam")), aliases)
                    key = _key(cid, season, date_iso, home, away)
                    base = lookup.get(key)
                    if base is None or key in matched:
                        continue
                    extracted = _extract_odds({str(k): "" if v is None else str(v) for k, v in raw.items() if k})
                    if extracted is None:
                        continue
                    market, provider = extracted
                    item = dict(base)
                    item.update({
                        "market_p_home": market["home"],
                        "market_p_draw": market["draw"],
                        "market_p_away": market["away"],
                        "market_provider_class": provider,
                    })
                    matched[key] = item
                    providers[provider] += 1
    return list(matched.values()), dict(providers)


def _model_probs(r: dict[str, Any]) -> dict[str, float]:
    return {"home": float(r["p_home"]), "draw": float(r["p_draw"]), "away": float(r["p_away"])}


def _market_probs(r: dict[str, Any]) -> dict[str, float]:
    return {
        "home": float(r["market_p_home"]),
        "draw": float(r["market_p_draw"]),
        "away": float(r["market_p_away"]),
    }


def _blend_probs(r: dict[str, Any], market_weight: float) -> dict[str, float]:
    m = _model_probs(r)
    q = _market_probs(r)
    return {k: (1.0 - market_weight) * m[k] + market_weight * q[k] for k in DIRECTIONS}


def _pick_probs(p: dict[str, float]) -> str:
    return max(DIRECTIONS, key=lambda k: p[k])


def _accuracy(rows: list[dict[str, Any]], picker) -> tuple[int, int, float]:
    hits = sum(1 for r in rows if picker(r) == r["actual"])
    n = len(rows)
    return hits, n, hits / n if n else float("nan")


def _fit_blend(dev: list[dict[str, Any]]) -> float:
    best_weight = 0.0
    best_hits = -1
    # Conservative grid; tie-break toward less market dependence to avoid fitting noise.
    for wi in range(0, 21):
        w = wi / 20.0
        hits = sum(1 for r in dev if _pick_probs(_blend_probs(r, w)) == r["actual"])
        if hits > best_hits:
            best_hits = hits
            best_weight = w
    return best_weight


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _direction_counts(rows: list[dict[str, Any]], picker) -> dict[str, int]:
    return dict(Counter(picker(r) for r in rows))


def _fit_selective_gate(rows: list[dict[str, Any]], market_weight: float, target: float) -> dict[str, Any] | None:
    candidates = []
    for min_p_i in range(34, 81, 2):
        min_p = min_p_i / 100.0
        for min_margin_i in range(0, 31, 2):
            min_margin = min_margin_i / 100.0
            selected = []
            for r in rows:
                p = _blend_probs(r, market_weight)
                order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
                if p[order[0]] >= min_p and p[order[0]] - p[order[1]] >= min_margin:
                    selected.append((r, order[0]))
            if len(selected) < 50:
                continue
            hits = sum(1 for r, pick in selected if pick == r["actual"])
            acc = hits / len(selected)
            if acc >= target:
                candidates.append((len(selected) / len(rows), acc, min_p, min_margin, len(selected)))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    coverage, acc, min_p, min_margin, count = candidates[0]
    return {"min_probability": min_p, "min_margin": min_margin, "count": count, "coverage": coverage, "accuracy": acc}


def _eval_gate(rows: list[dict[str, Any]], market_weight: float, gate: dict[str, Any] | None) -> dict[str, Any]:
    if gate is None:
        return {"available": False, "count": 0, "coverage": 0.0, "accuracy": None}
    selected = []
    for r in rows:
        p = _blend_probs(r, market_weight)
        order = sorted(DIRECTIONS, key=lambda k: p[k], reverse=True)
        if p[order[0]] >= gate["min_probability"] and p[order[0]] - p[order[1]] >= gate["min_margin"]:
            selected.append((r, order[0]))
    hits = sum(1 for r, pick in selected if pick == r["actual"])
    return {
        "available": True,
        "count": len(selected),
        "hits": hits,
        "coverage": len(selected) / len(rows) if rows else 0.0,
        "accuracy": hits / len(selected) if selected else None,
        "gate": gate,
    }


def main() -> int:
    model_rows = _load_model_rows()
    rows, providers = _match_market(model_rows)
    if len(rows) < 300:
        raise RuntimeError(f"insufficient market-matched rows for repeated diagnostics: {len(rows)}")

    rng = random.Random(SEED + 697)
    audit_indices = set(rng.sample(range(len(rows)), AUDIT_N))
    audit = [r for i, r in enumerate(rows) if i in audit_indices]
    research = [r for i, r in enumerate(rows) if i not in audit_indices]

    model_all = _accuracy(rows, lambda r: _pick_probs(_model_probs(r)))
    market_all = _accuracy(rows, lambda r: _pick_probs(_market_probs(r)))

    runs = []
    fitted = []
    for repeat in range(REPEATS):
        rr = random.Random(SEED + 2000 + repeat)
        sample = rr.sample(research, DEV_N + TEST_N)
        dev, test = sample[:DEV_N], sample[DEV_N:]
        w = _fit_blend(dev)
        fitted.append(w)
        model_hits, _, model_acc = _accuracy(test, lambda r: _pick_probs(_model_probs(r)))
        market_hits, _, market_acc = _accuracy(test, lambda r: _pick_probs(_market_probs(r)))
        blend_hits, _, blend_acc = _accuracy(test, lambda r, ww=w: _pick_probs(_blend_probs(r, ww)))
        runs.append({
            "repeat": repeat + 1,
            "market_weight": w,
            "model_hits": model_hits,
            "model_accuracy": model_acc,
            "market_hits": market_hits,
            "market_accuracy": market_acc,
            "blend_hits": blend_hits,
            "blend_accuracy": blend_acc,
            "blend_vs_model_uplift_pp": (blend_acc - model_acc) * 100.0,
            "market_vs_model_uplift_pp": (market_acc - model_acc) * 100.0,
        })

    # Use the most frequent development-selected blend weight as the final research rule.
    weight_counts = Counter(fitted)
    consensus_weight, frequency = sorted(weight_counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0]

    audit_model = _accuracy(audit, lambda r: _pick_probs(_model_probs(r)))
    audit_market = _accuracy(audit, lambda r: _pick_probs(_market_probs(r)))
    audit_blend = _accuracy(audit, lambda r: _pick_probs(_blend_probs(r, consensus_weight)))

    selective = {}
    for target in (0.60, 0.65, 0.70):
        gate = _fit_selective_gate(research, consensus_weight, target)
        selective[f"target_{int(target*100)}"] = {
            "research_fit": gate,
            "untouched_100_audit": _eval_gate(audit, consensus_weight, gate),
        }

    payload = {
        "schema_version": "V6.9.7-retrospective-market-anchor-1x2-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "market_data_classification": "RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "matched_row_count": len(rows),
        "model_row_count": len(model_rows),
        "market_match_rate": len(rows) / len(model_rows),
        "provider_class_counts": providers,
        "overall_matched_subset": {
            "model": {"hits": model_all[0], "count": model_all[1], "accuracy": model_all[2]},
            "market_only": {"hits": market_all[0], "count": market_all[1], "accuracy": market_all[2]},
            "market_vs_model_uplift_pp": (market_all[2] - model_all[2]) * 100.0,
        },
        "repeated_100_match_tests": {
            "model_accuracy": _summary([r["model_accuracy"] for r in runs]),
            "market_accuracy": _summary([r["market_accuracy"] for r in runs]),
            "blend_accuracy": _summary([r["blend_accuracy"] for r in runs]),
            "blend_vs_model_uplift_pp": _summary([r["blend_vs_model_uplift_pp"] for r in runs]),
            "market_vs_model_uplift_pp": _summary([r["market_vs_model_uplift_pp"] for r in runs]),
            "consensus_market_weight": consensus_weight,
            "consensus_frequency": frequency,
            "runs": runs,
        },
        "untouched_random_100_audit": {
            "model_hits": audit_model[0], "model_accuracy": audit_model[2],
            "market_hits": audit_market[0], "market_accuracy": audit_market[2],
            "blend_hits": audit_blend[0], "blend_accuracy": audit_blend[2],
            "blend_vs_model_uplift_pp": (audit_blend[2] - audit_model[2]) * 100.0,
            "market_vs_model_uplift_pp": (audit_market[2] - audit_model[2]) * 100.0,
            "model_direction_counts": _direction_counts(audit, lambda r: _pick_probs(_model_probs(r))),
            "market_direction_counts": _direction_counts(audit, lambda r: _pick_probs(_market_probs(r))),
            "blend_direction_counts": _direction_counts(audit, lambda r: _pick_probs(_blend_probs(r, consensus_weight))),
            "actual_direction_counts": dict(Counter(r["actual"] for r in audit)),
        },
        "selective_accuracy": selective,
        "governance": {
            "research_only": True,
            "cannot_be_formal_market_snapshot": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "matched_row_count": len(rows),
        "overall_matched_subset": payload["overall_matched_subset"],
        "repeated_summary": payload["repeated_100_match_tests"],
        "untouched_random_100_audit": payload["untouched_random_100_audit"],
        "selective_accuracy": payload["selective_accuracy"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
