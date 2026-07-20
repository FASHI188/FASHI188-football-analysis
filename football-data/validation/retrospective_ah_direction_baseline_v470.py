#!/usr/bin/env python3
"""Research-only Asian-handicap direction baseline using Football-Data historical lines.

Important governance:
- The source CSV is retrospective and does not provide an original per-row quote timestamp.
- Therefore these lines are NOT formal frozen market snapshots and cannot authorize EV.
- The audit measures only whether the unified score matrix preferred side settled positive
  versus the historical AH line. Pushes are reported separately and excluded from hit rate.
"""
from __future__ import annotations

import csv
import io
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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
from platform_core import PlatformError, atomic_write_json, load_json, normalize_team_token, read_processed_matches, score_matrix_rows

OUT = ROOT / "manifests" / "retrospective_ah_direction_baseline_v470_status.json"
SOURCES = {
    "ENG_PremierLeague": ("2025/26", "E0"),
    "GER_Bundesliga": ("2025/26", "D1"),
    "ITA_SerieA": ("2025/26", "I1"),
    "FRA_Ligue1": ("2025/26", "F1"),
    "ESP_LaLiga": ("2025/26", "SP1"),
    "POR_PrimeiraLiga": ("2025/26", "P1"),
    "NED_Eredivisie": ("2025/26", "N1"),
    "SCO_Premiership": ("2025/26", "SC0"),
}


def _url(code: str) -> str:
    return f"https://www.football-data.co.uk/mmz4281/2526/{code}.csv"


def _download_csv(code: str) -> list[dict[str, str]]:
    request = urllib.request.Request(_url(code), headers={"User-Agent": "Mozilla/5.0 football-research-audit"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _parse_date(token: str) -> datetime:
    raw = str(token or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise PlatformError(f"unsupported source date {raw!r}")


def _line_from_row(row: dict[str, str]) -> tuple[float | None, str | None]:
    for field in ("AHCh", "AHh"):
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        try:
            line = float(value)
        except ValueError:
            continue
        if math.isfinite(line):
            return line, field
    return None, None


def _split_quarter_line(line: float) -> list[float]:
    q = int(round(line * 4.0))
    snapped = q / 4.0
    if abs(snapped - line) > 1e-6:
        return [line]
    if abs(q) % 2 == 1:
        return [(q - 1) / 4.0, (q + 1) / 4.0]
    return [snapped]


def _home_payoff(home_goals: int, away_goals: int, line: float) -> float:
    values = []
    for component in _split_quarter_line(line):
        margin = home_goals + component - away_goals
        values.append(1.0 if margin > 1e-12 else -1.0 if margin < -1e-12 else 0.0)
    return sum(values) / len(values)


def _expected_home_payoff(matrix, line: float) -> float:
    return sum(float(p) * _home_payoff(h, a, line) for h, a, p in score_matrix_rows(matrix))


def _domain(cid: str, season: str, code: str) -> dict[str, Any]:
    rows = _download_csv(code)
    if not rows:
        raise PlatformError("downloaded CSV is empty")
    columns = set(rows[0].keys())
    available_line_fields = [field for field in ("AHCh", "AHh") if field in columns]
    if not available_line_fields:
        raise PlatformError("CSV has no AHCh/AHh line field")

    report = load_json(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError("missing target-season parameters")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    all_matches = read_processed_matches(cid)
    matches = [m for m in all_matches if str(m.season) == season]
    lookup = {(m.date.date().isoformat(), normalize_team_token(m.home_team), normalize_team_token(m.away_team)): m for m in matches}

    source_rows = matched = line_rows = model_eligible = 0
    wins = pushes = losses = abstains = 0
    field_counts: dict[str, int] = {}
    unmatched_examples = []
    for row in rows:
        if not row.get("HomeTeam") or not row.get("AwayTeam") or not row.get("Date"):
            continue
        source_rows += 1
        try:
            date = _parse_date(row["Date"])
        except PlatformError:
            continue
        key = (date.date().isoformat(), normalize_team_token(row["HomeTeam"]), normalize_team_token(row["AwayTeam"]))
        match = lookup.get(key)
        if match is None:
            if len(unmatched_examples) < 5:
                unmatched_examples.append({"date": key[0], "home": row["HomeTeam"], "away": row["AwayTeam"]})
            continue
        matched += 1
        line, field = _line_from_row(row)
        if line is None or field is None:
            continue
        line_rows += 1
        field_counts[field] = field_counts.get(field, 0) + 1
        try:
            matrix = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
        except PlatformError:
            continue
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        model_eligible += 1
        edge = _expected_home_payoff(matrix, line)
        if abs(edge) <= 1e-12:
            abstains += 1
            continue
        picked_home = edge > 0
        actual_home_payoff = _home_payoff(int(match.home_goals), int(match.away_goals), line)
        selected_payoff = actual_home_payoff if picked_home else -actual_home_payoff
        if selected_payoff > 1e-12:
            wins += 1
        elif selected_payoff < -1e-12:
            losses += 1
        else:
            pushes += 1

    decided = wins + losses
    return {
        "competition_id": cid,
        "season": season,
        "source_url": _url(code),
        "source_classification": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
        "original_quote_timestamp_available": False,
        "formal_market_snapshot": False,
        "formal_ev_authorized": False,
        "available_line_fields": available_line_fields,
        "line_field_usage": field_counts,
        "source_rows": source_rows,
        "matched_to_processed_matches": matched,
        "rows_with_ah_line": line_rows,
        "model_eligible_with_line": model_eligible,
        "direction": {
            "wins": wins,
            "pushes": pushes,
            "losses": losses,
            "abstains_zero_model_edge": abstains,
            "decided_count_excluding_pushes": decided,
            "hit_rate_excluding_pushes": wins / decided if decided else None,
            "non_loss_rate_including_pushes": (wins + pushes) / (wins + pushes + losses) if wins + pushes + losses else None,
        },
        "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
        "unmatched_examples": unmatched_examples,
    }


def main() -> int:
    reports = {}
    failures = {}
    total_w = total_p = total_l = 0
    for cid, (season, code) in SOURCES.items():
        try:
            item = _domain(cid, season, code)
            reports[cid] = item
            total_w += item["direction"]["wins"]
            total_p += item["direction"]["pushes"]
            total_l += item["direction"]["losses"]
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    decided = total_w + total_l
    payload = {
        "schema_version": "V4.7.0-retrospective-ah-direction-baseline-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(SOURCES) and not failures else "PARTIAL",
        "competition_count_requested": len(SOURCES),
        "competition_count_completed": len(reports),
        "aggregate": {
            "wins": total_w, "pushes": total_p, "losses": total_l,
            "hit_rate_excluding_pushes": total_w / decided if decided else None,
            "non_loss_rate_including_pushes": (total_w + total_p) / (total_w + total_p + total_l) if total_w + total_p + total_l else None,
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "research_only": True,
            "retrospective_market_reference_only": True,
            "formal_snapshot_count": 0,
            "formal_ev_authorized": False,
            "formal_weight_change": False,
            "probability_change": False,
            "unsupported_other_nine_domains_remain_unavailable": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": payload["status"], "aggregate": payload["aggregate"], "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
