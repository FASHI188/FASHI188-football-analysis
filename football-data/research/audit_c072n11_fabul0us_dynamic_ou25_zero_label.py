#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

EXPECTED_SHA256 = "c0e8854302159e1a8c529463f33280b728909c5e0ba95262515a7a144a43aa2a"
EXPECTED_REVISION = "211feb35f9dcd270bd7a1b27b39a8b1f45f239aa"
SUMMARY = Path("football-data/research/c072n11_fabul0us_dynamic_ou25_zero_label_summary.json")
FORBIDDEN_RESULT_NAMES = {
    "fthg","ftag","ftr","hthg","htag","htr","score","result","homegoals","awaygoals",
    "total_goals","target","winner","settlement","outcome_result"
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_dt(x: str) -> datetime | None:
    s = str(x or "").strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def valid_price(x: str) -> bool:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()
    path = Path(args.csv)
    digest = sha256(path)
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"immutable source SHA mismatch: {digest}")

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        lower = {h.lower().strip(): h for h in header}
        required = [
            "home_team", "away_team", "competition", "refreshed_at",
            "odds_under_2.5", "odds_over_2.5", "U/O 2.5 timestamp",
        ]
        missing = [h for h in required if h not in header]
        if missing:
            raise RuntimeError(f"missing required columns: {missing}")

        forbidden_headers = sorted(h for h in header if h.lower().strip() in FORBIDDEN_RESULT_NAMES)
        kickoff_like_headers = sorted(
            h for h in header
            if any(token in h.lower() for token in ("kickoff", "commence", "start_time", "match_date", "fixture_date"))
        )

        rows = 0
        valid_uo_rows = 0
        parseable_uo_ts_rows = 0
        parseable_refresh_rows = 0
        malformed_identity_rows = 0
        match_rows = Counter()
        match_uo_ts: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        match_refresh_ts: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        match_min_uo: dict[tuple[str, str, str], datetime] = {}
        match_max_uo: dict[tuple[str, str, str], datetime] = {}
        competition_counts = Counter()

        for r in reader:
            rows += 1
            comp = str(r.get("competition", "")).strip()
            home = str(r.get("home_team", "")).strip()
            away = str(r.get("away_team", "")).strip()
            key = (comp, home, away)
            if not all(key):
                malformed_identity_rows += 1
                continue
            match_rows[key] += 1
            competition_counts[comp] += 1

            refresh_s = str(r.get("refreshed_at", "")).strip()
            refresh_dt = parse_dt(refresh_s)
            if refresh_dt is not None:
                parseable_refresh_rows += 1
                match_refresh_ts[key].add(refresh_s)

            uo_ts_s = str(r.get("U/O 2.5 timestamp", "")).strip()
            uo_dt = parse_dt(uo_ts_s)
            if uo_dt is not None:
                parseable_uo_ts_rows += 1
                match_uo_ts[key].add(uo_ts_s)
                if key not in match_min_uo or uo_dt < match_min_uo[key]:
                    match_min_uo[key] = uo_dt
                if key not in match_max_uo or uo_dt > match_max_uo[key]:
                    match_max_uo[key] = uo_dt

            if valid_price(r.get("odds_under_2.5", "")) and valid_price(r.get("odds_over_2.5", "")):
                valid_uo_rows += 1

    unique_matches = len(match_rows)
    matches_ge2_uo_ts = sum(len(v) >= 2 for v in match_uo_ts.values())
    matches_ge3_uo_ts = sum(len(v) >= 3 for v in match_uo_ts.values())
    matches_span_ge1h = 0
    matches_span_ge6h = 0
    matches_span_ge24h = 0
    spans_hours = []
    for k in match_rows:
        if k in match_min_uo and k in match_max_uo:
            span_h = (match_max_uo[k] - match_min_uo[k]).total_seconds() / 3600.0
            spans_hours.append(span_h)
            matches_span_ge1h += span_h >= 1
            matches_span_ge6h += span_h >= 6
            matches_span_ge24h += span_h >= 24

    spans_sorted = sorted(spans_hours)
    def quantile(q: float) -> float | None:
        if not spans_sorted:
            return None
        i = min(len(spans_sorted) - 1, max(0, int(round(q * (len(spans_sorted) - 1)))))
        return float(spans_sorted[i])

    has_native_kickoff = bool(kickoff_like_headers)
    gates = {
        "source_sha_exact": digest == EXPECTED_SHA256,
        "zero_forbidden_result_headers": len(forbidden_headers) == 0,
        "rows_ge_1m": rows >= 1_000_000,
        "unique_match_identities_ge_1500": unique_matches >= 1500,
        "valid_two_sided_uo25_rows_ge_95pct": (valid_uo_rows / rows if rows else 0.0) >= 0.95,
        "parseable_uo25_timestamp_rows_ge_95pct": (parseable_uo_ts_rows / rows if rows else 0.0) >= 0.95,
        "matches_with_ge2_uo25_timestamps_ge_1500": matches_ge2_uo_ts >= 1500,
        "matches_with_uo25_span_ge_6h_ge_1000": matches_span_ge6h >= 1000,
        "native_kickoff_timestamp_available": has_native_kickoff,
    }

    # Source-asset verdict is separated from strict PIT verdict. This prevents a large dynamic
    # file with no native kickoff field from being mislabeled as a strict prematch PASS.
    dynamic_asset_pass = all(v for k, v in gates.items() if k != "native_kickoff_timestamp_available")
    strict_pit_pass = dynamic_asset_pass and gates["native_kickoff_timestamp_available"]
    terminal = (
        "C072N11_FABULOUS_DYNAMIC_OU25_STRICT_PIT_PASS" if strict_pit_pass else
        "C072N11_FABULOUS_DYNAMIC_OU25_ASSET_PASS_KICKOFF_JOIN_REQUIRED" if dynamic_asset_pass else
        "C072N11_FABULOUS_DYNAMIC_OU25_SOURCE_STOP"
    )

    summary = {
        "schema": "C072N11_FABULOUS_DYNAMIC_OU25_ZERO_LABEL_V1",
        "project_line": "football3",
        "classification": "ZERO_LABEL_SOURCE_AUDIT",
        "provider": "Hugging Face fabul0us/football_odds_2023-24",
        "revision": EXPECTED_REVISION,
        "file": "match_odds.csv",
        "sha256": digest,
        "terminal": terminal,
        "dynamic_asset_pass": dynamic_asset_pass,
        "strict_pit_pass": strict_pit_pass,
        "header": header,
        "forbidden_result_headers": forbidden_headers,
        "kickoff_like_headers": kickoff_like_headers,
        "rows": rows,
        "unique_match_identities_comp_home_away": unique_matches,
        "malformed_identity_rows": malformed_identity_rows,
        "valid_two_sided_uo25_rows": valid_uo_rows,
        "valid_two_sided_uo25_fraction": valid_uo_rows / rows if rows else 0.0,
        "parseable_uo25_timestamp_rows": parseable_uo_ts_rows,
        "parseable_uo25_timestamp_fraction": parseable_uo_ts_rows / rows if rows else 0.0,
        "parseable_refreshed_at_rows": parseable_refresh_rows,
        "parseable_refreshed_at_fraction": parseable_refresh_rows / rows if rows else 0.0,
        "matches_with_ge2_distinct_uo25_timestamps": matches_ge2_uo_ts,
        "matches_with_ge3_distinct_uo25_timestamps": matches_ge3_uo_ts,
        "matches_uo25_span_ge_1h": matches_span_ge1h,
        "matches_uo25_span_ge_6h": matches_span_ge6h,
        "matches_uo25_span_ge_24h": matches_span_ge24h,
        "uo25_timestamp_span_hours_quantiles": {
            "p10": quantile(0.10), "p25": quantile(0.25), "p50": quantile(0.50),
            "p75": quantile(0.75), "p90": quantile(0.90),
        },
        "competition_row_counts": dict(sorted(competition_counts.items())),
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_scientific_results_used": False,
        "C070F_confirmation1597_opened": False,
        "formal_weight": 0,
        "gates": gates,
        "next_if_kickoff_join_required": "Freeze a zero-label fixture/kickoff identity join before any target access; do not infer kickoff from the final odds timestamp.",
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
