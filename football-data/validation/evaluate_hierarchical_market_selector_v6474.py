#!/usr/bin/env python3
"""V6.47.4 time-ordered hierarchical market-reliability selector.

Goal
----
Test whether the historical 70%+ "high confidence" effect can be reproduced without
choosing a pmax threshold on the frozen recent benchmark.

Design
------
1. Read historical market 1X2 rows from the 17 configured domains.
2. For each competition, DEVELOPMENT contains only matches strictly before the first
   season used by the frozen V6.13.1 recent-two-season benchmark.
3. Build reliability cells by (competition, market-pick direction, fixed pmax bin).
   Competition cells are Beta-shrunk toward the matching global direction/bin cell.
4. Choose a reliability threshold only on DEVELOPMENT from a predeclared grid.
5. Freeze that threshold, then evaluate it exactly once on the immutable fixed1000.
6. The selector never changes the market probabilities; it only decides SELECT/ABSTAIN.

Lead time is intentionally excluded: the historical benchmark uses closing-market
references while the real forward feed has explicit pre-kickoff lead time. Timing must
be validated prospectively by V6.46.9, not learned by pretending closing prices have a
known historical freeze offset.

Research only. No CURRENT, runtime probability, threshold, or formal-weight mutation.
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

import build_1x2_fixed1000_benchmark_v6130 as base
from diagnose_1x2_market_anchor_v697 import _extract_odds
from platform_core import parse_match_date

BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
OUT = ROOT / "manifests" / "v6_hierarchical_market_selector_v6474_status.json"
DIRECTIONS = ("home", "draw", "away")
PMAX_EDGES = (1.0 / 3.0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 1.0000001)
RELIABILITY_GRID = (0.55, 0.575, 0.60, 0.625, 0.65)
PRIOR_STRENGTH = 40.0
MIN_COMP_CELL_N = 20
DEV_MIN_SELECTED_N = 200
DEV_MIN_COVERAGE = 0.08
DEV_MIN_WILSON90_LOWER = 0.58
TEST_MIN_SELECTED_N = 50
TEST_MIN_COVERAGE = 0.05
TEST_MIN_WILSON90_LOWER = 0.55
TEST_MIN_ACCURACY = 0.60
TEST_MIN_DOMAINS = 5
TEST_MAX_DOMAIN_SHARE = 0.40
Z90 = 1.6448536269514722


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def wilson(hits: int, n: int, z: float = Z90) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = hits / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    rad = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / den
    return max(0.0, center - rad), min(1.0, center + rad)


def pbin(value: float) -> int:
    x = max(1.0 / 3.0, min(1.0, float(value)))
    for i in range(len(PMAX_EDGES) - 1):
        if PMAX_EDGES[i] <= x < PMAX_EDGES[i + 1]:
            return i
    return len(PMAX_EDGES) - 2


def team(raw: dict[str, str], side: str) -> str:
    keys = ("HomeTeam", "Home", "home_team", "home") if side == "home" else ("AwayTeam", "Away", "away_team", "away")
    for key in keys:
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    return ""


def read_all_market_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cid in base.TARGET_COMPETITIONS:
        comp_dir = ROOT / "processed" / cid
        if not comp_dir.exists():
            continue
        for path in sorted(comp_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row_index, raw0 in enumerate(csv.DictReader(handle)):
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    actual = base._actual(raw)
                    extracted = _extract_odds(raw)
                    if actual is None or extracted is None:
                        continue
                    market, provider = extracted
                    season = base._season_label(raw, path)
                    date_raw = str(raw.get("Date") or raw.get("date") or "").strip()
                    if not date_raw:
                        continue
                    try:
                        date_iso = parse_match_date(date_raw, season).isoformat()
                    except Exception:
                        continue
                    home, away = team(raw, "home"), team(raw, "away")
                    if not home or not away:
                        continue
                    probs = {d: float(market[d]) for d in DIRECTIONS}
                    s = sum(probs.values())
                    if not math.isfinite(s) or s <= 0:
                        continue
                    probs = {d: probs[d] / s for d in DIRECTIONS}
                    pick = max(DIRECTIONS, key=lambda d: probs[d])
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "date": date_iso,
                        "row_index": row_index,
                        "home_team": home,
                        "away_team": away,
                        "actual": actual,
                        "probabilities": probs,
                        "pick": pick,
                        "pmax": probs[pick],
                        "provider": provider,
                        "source_file": str(path.relative_to(ROOT)),
                    })
    rows.sort(key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"], r["row_index"]))
    return rows


def split_development(all_rows: list[dict[str, Any]], benchmark: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    # Strict temporal cutoff: first date in either of the two benchmark seasons for each domain.
    selected = benchmark.get("source_meta", {}).get("selected_seasons", {})
    cutoffs: dict[str, str] = {}
    for cid in base.TARGET_COMPETITIONS:
        seasons = set(str(x) for x in selected.get(cid, []))
        dates = [str(r["date"]) for r in all_rows if r["competition_id"] == cid and r["season"] in seasons]
        if dates:
            cutoffs[cid] = min(dates)
    dev = [r for r in all_rows if r["competition_id"] in cutoffs and str(r["date"]) < cutoffs[r["competition_id"]]]
    return dev, cutoffs


def build_reliability(dev: list[dict[str, Any]]) -> dict[str, Any]:
    global_cells: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
    comp_cells: dict[tuple[str, str, int], list[int]] = defaultdict(lambda: [0, 0])
    for r in dev:
        b = pbin(r["pmax"])
        hit = int(r["pick"] == r["actual"])
        g = global_cells[(r["pick"], b)]
        g[0] += hit; g[1] += 1
        c = comp_cells[(r["competition_id"], r["pick"], b)]
        c[0] += hit; c[1] += 1

    def global_rate(direction: str, b: int) -> tuple[float, int]:
        h, n = global_cells.get((direction, b), [0, 0])
        # Jeffreys smoothing for the global cell.
        return ((h + 0.5) / (n + 1.0) if n else 0.5), n

    model: dict[str, Any] = {"global": {}, "competition": {}}
    for direction in DIRECTIONS:
        for b in range(len(PMAX_EDGES) - 1):
            rate, n = global_rate(direction, b)
            model["global"][f"{direction}|{b}"] = {"n": n, "posterior_mean": rate}
    for (cid, direction, b), (hits, n) in sorted(comp_cells.items()):
        gr, gn = global_rate(direction, b)
        posterior = (hits + PRIOR_STRENGTH * gr) / (n + PRIOR_STRENGTH)
        model["competition"][f"{cid}|{direction}|{b}"] = {
            "hits": hits, "n": n, "global_n": gn, "global_rate": gr,
            "posterior_mean": posterior,
        }
    return model


def reliability_for(r: dict[str, Any], model: dict[str, Any]) -> tuple[float, str, int]:
    b = pbin(r["pmax"])
    ck = f"{r['competition_id']}|{r['pick']}|{b}"
    cell = model["competition"].get(ck)
    if cell and int(cell["n"]) >= MIN_COMP_CELL_N:
        return float(cell["posterior_mean"]), "COMPETITION_SHRUNK_CELL", int(cell["n"])
    g = model["global"].get(f"{r['pick']}|{b}") or {"posterior_mean": 0.5, "n": 0}
    return float(g["posterior_mean"]), "GLOBAL_FALLBACK_CELL", int(g["n"])


def scored_rows(rows: list[dict[str, Any]], model: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        score, level, support = reliability_for(r, model)
        out.append({**r, "reliability_score": score, "reliability_level": level, "reliability_support": support})
    return out


def selection_metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    selected = [r for r in rows if r["reliability_score"] >= threshold]
    n = len(selected); hits = sum(r["pick"] == r["actual"] for r in selected)
    lo, hi = wilson(hits, n)
    by_comp = Counter(r["competition_id"] for r in selected)
    max_share = max(by_comp.values()) / n if n else None
    return {
        "threshold": threshold,
        "population_n": len(rows),
        "selected_n": n,
        "coverage": n / len(rows) if rows else 0.0,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90": [lo, hi],
        "domain_count": len(by_comp),
        "max_domain_share": max_share,
        "by_competition_n": dict(sorted(by_comp.items())),
    }


def choose_on_development(rows: list[dict[str, Any]]) -> tuple[float | None, list[dict[str, Any]], str]:
    curve = [selection_metrics(rows, t) for t in RELIABILITY_GRID]
    eligible = [x for x in curve if x["selected_n"] >= DEV_MIN_SELECTED_N
                and x["coverage"] >= DEV_MIN_COVERAGE
                and x["wilson90"][0] is not None
                and x["wilson90"][0] >= DEV_MIN_WILSON90_LOWER]
    if not eligible:
        return None, curve, "NO_PREDECLARED_THRESHOLD_PASSES_DEVELOPMENT_GATE"
    # Choose the lowest passing threshold to maximize coverage; no benchmark outcome is consulted.
    chosen = min(eligible, key=lambda x: x["threshold"])
    return float(chosen["threshold"]), curve, "LOWEST_DEVELOPMENT_PASSING_THRESHOLD_MAXIMIZES_COVERAGE"


def benchmark_market_rows(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for r in benchmark.get("rows", []):
        m = r.get("market")
        if not m:
            continue
        rows.append({
            "competition_id": r["competition_id"], "season": r["season"], "date": r["date"],
            "row_index": r["row_index"], "home_team": r["home_team"], "away_team": r["away_team"],
            "actual": r["actual"], "probabilities": m["probabilities"], "pick": m["pick"],
            "pmax": m["pmax"], "provider": m.get("provider"), "source_file": r["source_file"],
        })
    return rows


def main() -> int:
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    all_rows = read_all_market_rows()
    dev, cutoffs = split_development(all_rows, benchmark)
    model = build_reliability(dev)
    dev_scored = scored_rows(dev, model)
    chosen, dev_curve, selection_rule = choose_on_development(dev_scored)

    test_market = benchmark_market_rows(benchmark)
    test_scored = scored_rows(test_market, model)
    test_metrics = selection_metrics(test_scored, chosen) if chosen is not None else None

    gates = None
    decision = "HOLD_NO_DEVELOPMENT_THRESHOLD"
    if test_metrics is not None:
        gates = {
            "selected_n": test_metrics["selected_n"] >= TEST_MIN_SELECTED_N,
            "coverage": test_metrics["coverage"] >= TEST_MIN_COVERAGE,
            "accuracy": test_metrics["accuracy"] is not None and test_metrics["accuracy"] >= TEST_MIN_ACCURACY,
            "wilson90_lower": test_metrics["wilson90"][0] is not None and test_metrics["wilson90"][0] >= TEST_MIN_WILSON90_LOWER,
            "domain_count": test_metrics["domain_count"] >= TEST_MIN_DOMAINS,
            "domain_concentration": test_metrics["max_domain_share"] is not None and test_metrics["max_domain_share"] <= TEST_MAX_DOMAIN_SHARE,
        }
        decision = "CHALLENGE_FORWARD_REQUIRED" if all(gates.values()) else "HOLD_FIXED1000_GATE_NOT_PASSED"

    payload = {
        "schema_version": "V6.47.4-time-ordered-hierarchical-market-selector-r1",
        "generated_at_utc": now(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_RESEARCH_AUDIT",
        "classification": "TIME_ORDERED_RETROSPECTIVE_SELECTOR_CHALLENGE_FORMAL_WEIGHT_0",
        "design": {
            "development_is_strictly_before_each_domains_recent_two_season_test_window": True,
            "development_row_count": len(dev),
            "fixed1000_market_row_count": len(test_market),
            "competition_cutoff_dates": cutoffs,
            "pmax_edges": list(PMAX_EDGES),
            "reliability_threshold_grid": list(RELIABILITY_GRID),
            "prior_strength": PRIOR_STRENGTH,
            "minimum_competition_cell_n": MIN_COMP_CELL_N,
            "probabilities_mutated": False,
            "lead_time_used": False,
            "lead_time_exclusion_reason": "Historical reference is closing market; explicit lead time is prospectively tested by V6.46.9.",
        },
        "development_gate": {
            "minimum_selected_n": DEV_MIN_SELECTED_N,
            "minimum_coverage": DEV_MIN_COVERAGE,
            "minimum_wilson90_lower": DEV_MIN_WILSON90_LOWER,
            "curve": dev_curve,
            "chosen_threshold": chosen,
            "selection_rule": selection_rule,
        },
        "fixed1000_test": {
            "benchmark_path": str(BENCHMARK.relative_to(ROOT)),
            "benchmark_target_n": benchmark.get("target_n"),
            "market_available_n": len(test_market),
            "metrics": test_metrics,
            "acceptance_gate": {
                "minimum_selected_n": TEST_MIN_SELECTED_N,
                "minimum_coverage": TEST_MIN_COVERAGE,
                "minimum_accuracy": TEST_MIN_ACCURACY,
                "minimum_wilson90_lower": TEST_MIN_WILSON90_LOWER,
                "minimum_domains": TEST_MIN_DOMAINS,
                "maximum_single_domain_share": TEST_MAX_DOMAIN_SHARE,
                "results": gates,
            },
        },
        "decision": decision,
        "governance": {
            "benchmark_outcomes_used_to_choose_threshold": False,
            "fixed1000_cannot_promote_formal_model": True,
            "true_postfreeze_forward_required_even_if_fixed1000_passes": True,
            "historical_70_percent_target_not_assumed": True,
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "development_n": len(dev), "chosen_threshold": chosen,
        "fixed1000": test_metrics, "decision": decision,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
