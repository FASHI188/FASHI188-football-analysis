#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
REPO = "nm2890/football-data"
FILES = [
    "data/england/premier-league.csv",
    "data/spain/laliga.csv",
    "data/italy/serie-a.csv",
    "data/germany/bundesliga.csv",
    "data/france/ligue-1.csv",
    "data/belgium/jupiler-pro-league.csv",
    "data/netherlands/eredivisie.csv",
    "data/egypt/premier-league.csv",
]
ALLOWED = [
    "Date", "country", "league", "Season", "HomeTeam", "AwayTeam",
    "over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close",
]
PRICE = ["over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close"]
IDENTITY = ["Date", "country", "league", "HomeTeam", "AwayTeam"]
OUT = Path("football-data/research/c072d2_free_ou25_zero_label_audit.json")


def raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{REV}/{path}"


def fetch_bytes(path: str) -> bytes:
    req = urllib.request.Request(raw_url(path), headers={"User-Agent": "football3-c072d2-zero-label"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse_date(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def finite_price(value: str) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def main() -> int:
    file_stats: list[dict] = []
    total_rows = 0
    valid_dates = 0
    complete_prices = 0
    nonzero_moves = 0
    abs_moves: list[float] = []
    abs_move_logits: list[float] = []
    leagues: set[tuple[str, str]] = set()
    seasons: set[str] = set()
    identities: set[tuple[str, str, str, str, str]] = set()
    duplicate_identity_rows = 0

    for path in FILES:
        raw = fetch_bytes(path)
        sha = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            header = next(reader)
        except StopIteration as exc:
            raise RuntimeError(f"{path}: empty CSV") from exc

        index = {name: i for i, name in enumerate(header)}
        missing = [name for name in ALLOWED if name not in index]
        if missing:
            raise RuntimeError(f"{path}: missing required allowed columns {missing}")

        # Leakage guard: rows are raw CSV tokens; only indices for ALLOWED are ever read below.
        # Score/result columns can exist in raw source bytes but are never parsed/materialized.
        file_rows = 0
        for row in reader:
            if not row:
                continue
            max_idx = max(index[name] for name in ALLOWED)
            if len(row) <= max_idx:
                continue
            selected = {name: row[index[name]] for name in ALLOWED}
            file_rows += 1
            total_rows += 1

            if parse_date(selected["Date"]):
                valid_dates += 1

            country = selected["country"].strip()
            league = selected["league"].strip()
            season = selected["Season"].strip()
            if country or league:
                leagues.add((country, league))
            if season:
                seasons.add(season)

            ident = tuple(selected[name].strip() for name in IDENTITY)
            if ident in identities:
                duplicate_identity_rows += 1
            else:
                identities.add(ident)

            prices = [finite_price(selected[name]) for name in PRICE]
            if all(x is not None for x in prices):
                oo, uo, oc, uc = (float(x) for x in prices)
                complete_prices += 1
                inv_oo, inv_uo = 1.0 / oo, 1.0 / uo
                inv_oc, inv_uc = 1.0 / oc, 1.0 / uc
                p_open = inv_oo / (inv_oo + inv_uo)
                p_close = inv_oc / (inv_oc + inv_uc)
                move = p_close - p_open
                move_logit = logit(p_close) - logit(p_open)
                abs_moves.append(abs(move))
                abs_move_logits.append(abs(move_logit))
                if abs(move) > 1e-12:
                    nonzero_moves += 1

        file_stats.append({
            "path": path,
            "bytes": len(raw),
            "sha256": sha,
            "rows": file_rows,
            "materialized_columns": list(ALLOWED),
            "forbidden_score_result_columns_materialized": 0,
        })

    valid_date_fraction = valid_dates / total_rows if total_rows else 0.0
    four_price_fraction = complete_prices / total_rows if total_rows else 0.0
    duplicate_fraction = duplicate_identity_rows / total_rows if total_rows else 0.0
    nonzero_fraction = nonzero_moves / complete_prices if complete_prices else 0.0

    summary = {
        "contract": "C072-D2_FREE_OU25_OPEN_CLOSE_ZERO_LABEL",
        "project": "football3",
        "parent_c072c_head": "e3e73c998020beef585cc459a69ea5b73b44ddb3",
        "quarantine_c073_c077": True,
        "external_repo": REPO,
        "external_revision": REV,
        "files_expected": len(FILES),
        "files_parsed": len(file_stats),
        "file_stats": file_stats,
        "total_identity_rows": total_rows,
        "valid_date_rows": valid_dates,
        "valid_date_fraction": valid_date_fraction,
        "complete_valid_four_price_rows": complete_prices,
        "complete_valid_four_price_fraction": four_price_fraction,
        "league_count": len(leagues),
        "season_count": len(seasons),
        "duplicate_identity_rows": duplicate_identity_rows,
        "duplicate_identity_fraction": duplicate_fraction,
        "nonzero_movement_rows": nonzero_moves,
        "nonzero_movement_fraction_among_complete": nonzero_fraction,
        "mean_abs_de_vig_probability_movement": statistics.fmean(abs_moves) if abs_moves else None,
        "median_abs_de_vig_probability_movement": statistics.median(abs_moves) if abs_moves else None,
        "mean_abs_movement_logit": statistics.fmean(abs_move_logits) if abs_move_logits else None,
        "target_score_result_columns_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
    }

    gates = {
        "all_8_files": len(file_stats) == 8,
        "rows_ge_30000": total_rows >= 30000,
        "valid_date_ge_995pct": valid_date_fraction >= 0.995,
        "four_price_ge_80pct": four_price_fraction >= 0.80,
        "league_ge_8": len(leagues) >= 8,
        "season_ge_12": len(seasons) >= 12,
        "duplicate_le_01pct": duplicate_fraction <= 0.001,
        "movement_nonzero_ge_5pct": nonzero_fraction >= 0.05,
        "no_target_materialization": True,
        "no_model": True,
    }
    summary["gates"] = gates
    summary["all_gates_pass"] = all(gates.values())
    summary["terminal"] = (
        "COARSE_OU25_OPEN_CLOSE_SOURCE_PASS" if summary["all_gates_pass"] else "SOURCE_GATE_FAIL"
    )
    summary["interpretation_boundary"] = (
        "Coarse average O/U2.5 opening/closing research source only; no immutable quote timestamps, "
        "no multi-line market ladder, not Betfair-equivalent, no model evidence."
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if summary["all_gates_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
