#!/usr/bin/env python3
"""Stage-4 zero-label coverage gate for multi-OU base anatomy.

This script does NOT read source outcomes. It only asks whether the frozen fixed1000
BRA_SerieA identities can be matched exactly to the pinned Brazil five-line OU source.
No fuzzy matching, model fitting, label access, or Stage-3 mutation is permitted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from football_data_import_shim import normalize_team_token  # populated by workflow wrapper

CENTRAL = ("0.5", "1.5", "2.5", "3.5", "4.5")
EXPECTED_SOURCE_SHA = "4fd20bcf9636c755ddb8181b8d56870c20730564f138d1e01fb9f13a46351223"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def first_present(headers: list[str], names: tuple[str, ...]) -> str | None:
    by_lower = {h.lower(): h for h in headers}
    for n in names:
        if n.lower() in by_lower:
            return by_lower[n.lower()]
    return None


def parse_date(value: str) -> str | None:
    s = (value or "").strip()
    if not s:
        return None
    candidates = [s, s[:10]]
    fmts = (
        "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%m-%Y", "%m-%d-%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M",
    )
    for v in candidates:
        for fmt in fmts:
            try:
                return datetime.strptime(v, fmt).date().isoformat()
            except ValueError:
                pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def valid_odd(v: str) -> bool:
    try:
        x = float(v)
        return math.isfinite(x) and x > 1.0
    except Exception:
        return False


def load_benchmark(path: Path) -> list[dict[str, Any]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    for key in ("rows", "matches", "fixtures", "sample"):
        value = obj.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    raise SystemExit(f"BENCHMARK_ROWS_KEY_NOT_FOUND keys={sorted(obj)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--benchmark", default="football-data/benchmarks/v6_1x2_fixed1000_v6130.json")
    ap.add_argument("--output", default="football-data/research/base_anatomy_20260817/multi_ou_coverage_r1.json")
    args = ap.parse_args()
    source = Path(args.source)
    benchmark = Path(args.benchmark)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if sha256(source) != EXPECTED_SOURCE_SHA:
        raise SystemExit("SOURCE_SHA_MISMATCH")

    rows = load_benchmark(benchmark)
    bra = [r for r in rows if str(r.get("competition_id")) == "BRA_SerieA"]
    if not bra:
        raise SystemExit("NO_BRA_SERIEA_ROWS_IN_FIXED1000")

    with source.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        headers = list(rd.fieldnames or [])
        date_col = first_present(headers, ("Date", "date", "match_date", "MatchDate", "datetime", "match_datetime"))
        home_col = first_present(headers, ("Home", "home", "HomeTeam", "home_team", "home_team_name", "team_home"))
        away_col = first_present(headers, ("Away", "away", "AwayTeam", "away_team", "away_team_name", "team_away"))
        if None in (date_col, home_col, away_col):
            payload = {
                "schema": "BASE-ANATOMY-STAGE4-MULTIOU-COVERAGE-R1",
                "status": "STOP_DATA_SCHEMA_IDENTITY_COLUMNS_UNRESOLVED",
                "source_headers": headers,
                "detected": {"date": date_col, "home": home_col, "away": away_col},
                "outcome_columns_read": 0,
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        required_ou = [x for L in CENTRAL for x in ("AvgOver" + L, "AvgUnder" + L)]
        missing_ou = [c for c in required_ou if c not in headers]
        if missing_ou:
            raise SystemExit(f"MISSING_MULTI_OU_COLUMNS {missing_ou}")

        source_identities: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        date_counts: Counter[str] = Counter()
        eligible_rows = 0
        raw_rows = 0
        source_examples: list[dict[str, str]] = []
        for idx, r in enumerate(rd):
            raw_rows += 1
            # Hard zero-label gate: never reference HG/AG/result columns here.
            if not all(valid_odd(r.get(c, "")) for c in required_ou):
                continue
            d = parse_date(r.get(date_col or "", ""))
            h = normalize_team_token(str(r.get(home_col or "", "")))
            a = normalize_team_token(str(r.get(away_col or "", "")))
            if not d or not h or not a:
                continue
            eligible_rows += 1
            source_identities[(d, h, a)].append(idx)
            date_counts[d] += 1
            if len(source_examples) < 20:
                source_examples.append({"date": d, "home_raw": str(r.get(home_col or "", "")), "away_raw": str(r.get(away_col or "", "")), "home_norm": h, "away_norm": a})

    matches = []
    misses = []
    duplicate_matches = []
    for r in bra:
        d = parse_date(str(r.get("date", "")))
        h = normalize_team_token(str(r.get("home_team", "")))
        a = normalize_team_token(str(r.get("away_team", "")))
        key = (d or "", h, a)
        hits = source_identities.get(key, [])
        rec = {"date": d, "home": r.get("home_team"), "away": r.get("away_team"), "home_norm": h, "away_norm": a, "source_hit_count": len(hits)}
        if len(hits) == 1:
            matches.append(rec)
        elif len(hits) > 1:
            duplicate_matches.append(rec)
        else:
            same_date = int(date_counts.get(d or "", 0))
            rec["source_rows_same_date"] = same_date
            misses.append(rec)

    matched_n = len(matches)
    bra_n = len(bra)
    payload = {
        "schema": "BASE-ANATOMY-STAGE4-MULTIOU-COVERAGE-R1",
        "status": "PASS_EXACT_IDENTITY_COVERAGE_AVAILABLE" if matched_n > 0 else "STOP_DATA_NO_EXACT_FIXED1000_MULTIOU_OVERLAP",
        "classification": "ZERO_LABEL_IDENTITY_AND_MARKET_COVERAGE_ONLY",
        "formal_weight": 0,
        "source_sha256": EXPECTED_SOURCE_SHA,
        "source_schema": {"date_col": date_col, "home_col": home_col, "away_col": away_col, "required_lines": list(CENTRAL)},
        "source_raw_rows": raw_rows,
        "source_five_line_eligible_rows": eligible_rows,
        "fixed1000_bra_rows": bra_n,
        "exact_unique_matches": matched_n,
        "coverage_rate_of_fixed1000_bra": matched_n / bra_n,
        "duplicate_identity_matches": len(duplicate_matches),
        "unmatched": len(misses),
        "outcome_columns_read": 0,
        "selection_uses_outcomes": False,
        "matching_policy": "exact ISO date + repository normalize_team_token(home) + normalize_team_token(away); no fuzzy aliases",
        "exact_matches": matches,
        "duplicate_matches": duplicate_matches,
        "miss_examples": misses[:40],
        "source_examples": source_examples,
        "next_gate": "If exact_unique_matches is scientifically usable, Stage4 may evaluate Stage3 vs Stage3+five-line OU only on the exact paired subset. Otherwise STOP_DATA/COVERAGE.",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
