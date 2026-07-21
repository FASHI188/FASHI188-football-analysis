#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import clubelo_residual_oof_v515 as core


def _identity_receipt_guard(competition_id: str) -> None:
    ingest = core.load_json(core.INGEST_STATUS)
    if ingest.get("schema_version") != "V5.1.5-clubelo-history-ingest-r2":
        raise RuntimeError(
            f"superseded ClubElo identity receipt: {ingest.get('schema_version')}; "
            "require V5.1.5-clubelo-history-ingest-r2"
        )
    if competition_id == "ESP_LaLiga":
        mapping = core.load_json(core.EVIDENCE_ROOT / "ESP_LaLiga_team_map.json")
        ath = (mapping.get("mappings") or {}).get("Ath Madrid") or {}
        if ath.get("clubelo_name") != "Atletico" or ath.get("status") != "PASS":
            raise RuntimeError(f"ClubElo identity invariant failed for Ath Madrid: {ath}")


def _safe_project(matrix, elo_diff: float, beta: float, scale: float):
    """Conditional KL tilt preserving zero-probability total-goal slices exactly."""
    grouped = defaultdict(list)
    for h, a, p in core.score_matrix_rows(matrix):
        grouped[h + a].append((h, a, p))
    original_totals = core._total_marginals(matrix)
    result = []
    signal = float(elo_diff) / float(scale)
    zero_mass_slices = 0

    for total, cells in sorted(grouped.items()):
        mass = sum(p for _, _, p in cells)
        if mass <= 0.0:
            zero_mass_slices += 1
            for h, a, _ in cells:
                result.append({"home_goals": h, "away_goals": a, "probability": 0.0})
            continue

        weighted = []
        for h, a, p in cells:
            exponent = float(beta) * signal * float(h - a)
            exponent = min(40.0, max(-40.0, exponent))
            weighted.append((h, a, p * math.exp(exponent)))
        denom = sum(w for _, _, w in weighted)
        if denom <= 0.0 or not math.isfinite(denom):
            raise core.PlatformError(f"ClubElo conditional KL normalization failed positive_mass_total={total}")
        for h, a, weight in weighted:
            result.append({"home_goals": h, "away_goals": a, "probability": mass * weight / denom})

    prob_sum = sum(float(cell["probability"]) for cell in result)
    if prob_sum <= 0.0 or not math.isfinite(prob_sum):
        raise core.PlatformError("ClubElo projected probability sum invalid")
    result = [{**cell, "probability": float(cell["probability"]) / prob_sum} for cell in result]
    new_totals = core._total_marginals(result)
    max_total = max(abs(float(new_totals.get(t, 0.0)) - float(p)) for t, p in original_totals.items())
    return result, {
        "probability_sum_residual": abs(sum(float(cell["probability"]) for cell in result) - 1.0),
        "max_total_marginal_residual": max_total,
        "elo_difference": float(elo_diff),
        "elo_scaled_difference": signal,
        "beta": float(beta),
        "zero_mass_total_slice_count": zero_mass_slices,
    }


core._project = _safe_project


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        _identity_receipt_guard(args.competition)
        report = core.validate_domain(args.competition)
    except Exception as exc:
        report = {
            "schema_version": "V5.1.5-clubelo-residual-oof-domain-execution-r3",
            "competition_id": args.competition,
            "status": "EXECUTION_FAILURE_KEEP_FORMAL_WEIGHT_0",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "formal_weight": 0,
            "probability_change": False,
            "automatic_promotion": False,
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "competition_id": args.competition,
        "status": report.get("status"),
        "forward_prediction_count": report.get("forward_prediction_count"),
        "error": report.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
