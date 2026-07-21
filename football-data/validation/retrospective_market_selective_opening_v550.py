#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from statistics import NormalDist
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import retrospective_market_all17_ceiling_v524 as market17

OUT = ROOT / "manifests" / "retrospective_market_selective_opening_v550_status.json"
THRESHOLD = 0.30
CANDIDATES = {
    "ESP_LaLiga": "2025/26",
    "POR_PrimeiraLiga": "2025/26",
    "GER_Bundesliga": "2025/26",
    "NOR_Eliteserien": "2025",
    "SCO_Premiership": "2025/26",
}
REFERENCE = ROOT / "config" / "prospective_market_selective_challenger_v526.json"


def _wilson(successes: int, n: int, confidence: float = 0.95) -> dict[str, float | None]:
    if n <= 0:
        return {"lower": None, "upper": None}
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}


def _opening_rows(cid: str, season: str) -> list[dict[str, Any]]:
    report = market17.audit_domain(cid, season)
    # Re-run the same match universe, but explicitly read the opening surface.
    formal_report = market17._load(market17.REPORT_ROOT / f"{cid}.json")
    fold = market17._fold_for_season(formal_report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise RuntimeError(f"missing frozen formal parameters {cid} {season}")
    temperature, _mode = market17._target_season_temperature(cid, season)
    all_matches = market17.read_processed_matches(cid)
    targets = [m for m in all_matches if str(m.season) == season]
    market = market17._market_lookup(cid, season)
    rows: list[dict[str, Any]] = []
    for match in targets:
        key = (match.date.date().isoformat(), match.home_team, match.away_team)
        ref = market.get(key)
        if not ref or ref.get("opening") is None:
            continue
        opening = ref["opening"]
        ranked = sorted(opening.items(), key=lambda kv: (-float(kv[1]), kv[0]))
        gap = float(ranked[0][1]) - float(ranked[1][1])
        pick = ranked[0][0]
        actual = market17._outcome(int(match.home_goals), int(match.away_goals))
        rows.append({
            "date": match.date.date().isoformat(),
            "gap": gap,
            "pick": pick,
            "actual": actual,
            "correct": 1 if pick == actual else 0,
        })
    return rows


def main() -> int:
    frozen = json.loads(REFERENCE.read_text(encoding="utf-8"))
    frozen_domains = frozen.get("candidate_domains") or {}
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    timing_robust = []
    timing_not_robust = []

    for cid, season in CANDIDATES.items():
        try:
            frozen_row = frozen_domains.get(cid) or {}
            frozen_threshold = float(frozen_row.get("gap_threshold"))
            if abs(frozen_threshold - THRESHOLD) > 1e-12:
                raise RuntimeError(f"frozen threshold mismatch for {cid}: {frozen_threshold}")
            rows = _opening_rows(cid, season)
            selected = [r for r in rows if float(r["gap"]) >= THRESHOLD]
            successes = sum(int(r["correct"]) for r in selected)
            accuracy = successes / len(selected) if selected else None
            ci = _wilson(successes, len(selected))
            closing_selected = int(frozen_row.get("retrospective_selected") or 0)
            closing_accuracy = float(frozen_row.get("retrospective_accuracy") or 0.0)
            robust = bool(selected and len(selected) >= 40 and accuracy is not None and accuracy >= 0.70 and closing_accuracy >= 0.70)
            reports[cid] = {
                "competition_id": cid,
                "season": season,
                "frozen_gap_threshold": THRESHOLD,
                "opening_comparable_count": len(rows),
                "opening_selected": len(selected),
                "opening_accuracy": accuracy,
                "opening_correct": successes,
                "opening_wilson95": ci,
                "closing_reference_selected": closing_selected,
                "closing_reference_accuracy": closing_accuracy,
                "timing_robust_point_gate": robust,
                "threshold_retuned": False,
            }
            (timing_robust if robust else timing_not_robust).append(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V5.5.0-retrospective-market-selective-opening-r1",
        "frozen_threshold": THRESHOLD,
        "purpose": "Opening-average timing robustness test of the already-frozen closing-derived 0.30 selective market gap hypothesis. No threshold selection or retuning is performed.",
        "reports": reports,
        "failures": failures,
        "timing_robust_point_gate_domains": timing_robust,
        "timing_not_robust_domains": timing_not_robust,
        "status": "PASS" if len(reports) == len(CANDIDATES) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": "Opening and closing historical averages have no original quote timestamps and remain retrospective references. This audit may only restrict future shadow permissions; it cannot authorize formal promotion."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
