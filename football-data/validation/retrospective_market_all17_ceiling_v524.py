#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    canonical_team_name,
    derive_score_marginals,
    load_aliases,
    multiclass_brier,
    ranked_probability_score,
    read_processed_matches,
)

SEASONS = {
    "ENG_PremierLeague": "2025/26",
    "GER_Bundesliga": "2025/26",
    "ITA_SerieA": "2025/26",
    "FRA_Ligue1": "2025/26",
    "ESP_LaLiga": "2025/26",
    "POR_PrimeiraLiga": "2025/26",
    "NED_Eredivisie": "2025/26",
    "SUI_SuperLeague": "2025/26",
    "SCO_Premiership": "2025/26",
    "SWE_Allsvenskan": "2025",
    "NOR_Eliteserien": "2025",
    "JPN_J1": "2025",
    "KOR_KLeague1": "2025",
    "BRA_SerieA": "2025",
    "ARG_Primera": "2025",
    "USA_MLS": "2025",
    "UEFA_ChampionsLeague": "2025/26",
}
OUT = ROOT / "manifests" / "retrospective_market_all17_ceiling_v524_status.json"
BLOCK_SIZE = 20
DRAWS = 1200
SEED = 5242026


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 1.0:
        return None
    return number


def _devig(values: tuple[float, float, float]) -> dict[str, float]:
    h, d, a = values
    raw = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def _outcome(h: int, a: int) -> str:
    return "home" if h > a else "draw" if h == a else "away"


def _metric(prob: dict[str, float], actual: str) -> dict[str, float]:
    order = ["away", "draw", "home"]
    top = max(("home", "draw", "away"), key=lambda key: (prob[key], key))
    ranked = sorted(prob.values(), reverse=True)
    return {
        "accuracy": 1.0 if top == actual else 0.0,
        "brier": multiclass_brier(prob, actual),
        "rps": ranked_probability_score([prob[key] for key in order], order.index(actual)),
        "gap": ranked[0] - ranked[1],
    }


def _logical_season(row: dict[str, str]) -> str:
    return str(row.get("season") or row.get("Season") or "").strip()


def _market_lookup(cid: str, season: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    directory = ROOT / "processed" / cid
    aliases = load_aliases()
    output: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("*.csv")) if directory.exists() else []:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if _logical_season(row) != season:
                    continue
                try:
                    date = datetime.strptime(str(row.get("Date") or ""), "%d/%m/%Y").date().isoformat()
                except Exception:
                    continue
                home = canonical_team_name(cid, str(row.get("HomeTeam") or ""), aliases)
                away = canonical_team_name(cid, str(row.get("AwayTeam") or ""), aliases)
                opening_values = tuple(_num(row.get(key)) for key in ("AvgH", "AvgD", "AvgA"))
                closing_values = tuple(_num(row.get(key)) for key in ("AvgCH", "AvgCD", "AvgCA"))
                opening = _devig(opening_values) if all(value is not None for value in opening_values) else None
                closing = _devig(closing_values) if all(value is not None for value in closing_values) else None
                key = (date, home, away)
                current = output.get(key) or {"opening": None, "closing": None, "source_paths": []}
                # Prefer a non-null surface; preserve all provenance paths.
                if current["opening"] is None and opening is not None:
                    current["opening"] = opening
                if current["closing"] is None and closing is not None:
                    current["closing"] = closing
                current["source_paths"].append(str(path.relative_to(ROOT)))
                output[key] = current
    return output


def _summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, float]:
    return {
        "n": len(rows),
        "accuracy": mean(float(row[f"{prefix}_accuracy"]) for row in rows),
        "brier": mean(float(row[f"{prefix}_brier"]) for row in rows),
        "rps": mean(float(row[f"{prefix}_rps"]) for row in rows),
    }


def _bootstrap(rows: list[dict[str, Any]], metric: str, seed: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["date"], row["match_key"]))
    blocks = [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]
    point = mean(float(row[f"market_{metric}"]) - float(row[f"formal_{metric}"]) for row in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(DRAWS):
        sampled = []
        for _ in range(len(blocks)):
            sampled.extend(rng.choice(blocks))
        values.append(mean(float(row[f"market_{metric}"]) - float(row[f"formal_{metric}"]) for row in sampled))
    values.sort()
    return {
        "market_minus_formal": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "blocks": len(blocks),
        "draws": DRAWS,
    }


def _selective(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    output = {}
    for threshold in (0.10, 0.15, 0.20, 0.25, 0.30):
        chosen = [row for row in rows if float(row[f"{prefix}_gap"]) >= threshold]
        output[f"gap_ge_{threshold:.2f}"] = {
            "selected": len(chosen),
            "coverage": len(chosen) / len(rows) if rows else 0.0,
            "accuracy": mean(float(row[f"{prefix}_accuracy"]) for row in chosen) if chosen else None,
        }
    return output


def audit_domain(cid: str, season: str) -> dict[str, Any]:
    formal_report = _load(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(formal_report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise RuntimeError(f"missing frozen formal parameters for {cid} {season}")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    all_matches = read_processed_matches(cid)
    targets = [match for match in all_matches if str(match.season) == season]
    if not targets:
        raise RuntimeError(f"no target matches for {cid} {season}")
    market = _market_lookup(cid, season)
    rows = []
    baseline_failures = 0
    no_market = 0
    closing_used = opening_used = 0

    for match in targets:
        key = (match.date.date().isoformat(), match.home_team, match.away_team)
        reference = market.get(key)
        if not reference:
            no_market += 1
            continue
        market_prob = reference.get("closing") or reference.get("opening")
        market_surface = "closing_average" if reference.get("closing") is not None else "opening_average"
        if market_prob is None:
            no_market += 1
            continue
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
            if abs(temperature - 1.0) > 1e-15:
                matrix = temperature_scale_matrix(matrix, temperature)
        except Exception:
            baseline_failures += 1
            continue
        formal_prob = derive_score_marginals(matrix)["1x2"]
        actual = _outcome(match.home_goals, match.away_goals)
        formal_metric = _metric(formal_prob, actual)
        market_metric = _metric(market_prob, actual)
        if market_surface == "closing_average":
            closing_used += 1
        else:
            opening_used += 1
        row = {
            "date": match.date.date().isoformat(),
            "match_key": f"{cid}:{season}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}",
            "market_surface": market_surface,
        }
        for prefix, metric in (("formal", formal_metric), ("market", market_metric)):
            for name in ("accuracy", "brier", "rps", "gap"):
                row[f"{prefix}_{name}"] = metric[name]
        rows.append(row)

    if not rows:
        return {
            "competition_id": cid,
            "season": season,
            "status": "MARKET_REFERENCE_UNAVAILABLE",
            "target_match_count": len(targets),
            "comparable_row_count": 0,
            "no_market_count": no_market,
            "formal_pit_market_eligible": False,
        }
    bootstrap = {}
    if len(rows) >= 80:
        bootstrap = {
            "brier": _bootstrap(rows, "brier", SEED + 1),
            "rps": _bootstrap(rows, "rps", SEED + 2),
        }
    formal = _summary(rows, "formal")
    market_summary = _summary(rows, "market")
    proper_improves = bool(
        bootstrap
        and bootstrap["brier"]["ci95_upper"] < 0.0
        and bootstrap["rps"]["ci95_upper"] < 0.0
    )
    return {
        "competition_id": cid,
        "season": season,
        "status": "RETROSPECTIVE_MARKET_STRONGER_PROPER_SCORES" if proper_improves else "RETROSPECTIVE_REFERENCE_NO_STRICT_PROPER_SCORE_WIN",
        "target_match_count": len(targets),
        "comparable_row_count": len(rows),
        "comparable_coverage": len(rows) / len(targets),
        "baseline_failure_count": baseline_failures,
        "no_market_count": no_market,
        "closing_average_used": closing_used,
        "opening_average_fallback_used": opening_used,
        "formal": formal,
        "market": market_summary,
        "accuracy_gain_pp": 100.0 * (market_summary["accuracy"] - formal["accuracy"]),
        "paired_bootstrap_vs_formal": bootstrap,
        "selective_accuracy_by_raw_gap": {
            "formal": _selective(rows, "formal"),
            "market": _selective(rows, "market"),
        },
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "formal_pit_market_eligible": False,
        "usage": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
    }


def main() -> int:
    reports = {}
    failures = {}
    for cid, season in SEASONS.items():
        try:
            reports[cid] = audit_domain(cid, season)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    strict_wins = [cid for cid, report in reports.items() if report.get("status") == "RETROSPECTIVE_MARKET_STRONGER_PROPER_SCORES"]
    unavailable = [cid for cid, report in reports.items() if report.get("status") == "MARKET_REFERENCE_UNAVAILABLE"]
    payload = {
        "schema_version": "V5.2.4-retrospective-market-all17-ceiling-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season_policy": SEASONS,
        "reports": reports,
        "failures": failures,
        "strict_market_proper_score_win_domains": strict_wins,
        "strict_market_proper_score_win_count": len(strict_wins),
        "market_reference_unavailable_domains": unavailable,
        "status": "PASS" if len(reports) == len(SEASONS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": "Latest-completed-season market references are retrospective only because original quote timestamps are unavailable. This audit ranks evidence-acquisition priority and cannot authorize formal historical market promotion or EV."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "strict_market_proper_score_win_count": len(strict_wins),
        "strict_market_proper_score_win_domains": strict_wins,
        "market_reference_unavailable_domains": unavailable,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
