#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for path in (VALIDATION, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import clubelo_history_ingest_v515 as core
from platform_core import read_processed_matches

MAX_HISTORY_WORKERS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_domain(cid: str) -> tuple[dict, list[dict]]:
    cfg = core.load_json(core.CONFIG)
    domains = cfg["domains"]
    if cid not in domains:
        raise RuntimeError(f"competition not frozen in ClubElo config: {cid}")
    country = domains[cid]
    gate = cfg["identity_gate"]

    seasons = core._report_seasons(cid)
    anchors = core._anchor_dates(seasons)
    candidates, snapshot_audit = core._snapshot_candidates(country, anchors)
    all_matches = read_processed_matches(cid)
    allowed = set(seasons)
    teams = sorted(
        {m.home_team for m in all_matches if m.season in allowed}
        | {m.away_team for m in all_matches if m.season in allowed}
    )
    mappings = {
        team: core._map_team(
            team,
            candidates,
            float(gate["minimum_similarity"]),
            float(gate["minimum_best_second_margin"]),
        )
        for team in teams
    }
    passed = {team: item for team, item in mappings.items() if item.get("status") == "PASS"}
    failed = {team: item for team, item in mappings.items() if item.get("status") != "PASS"}

    unique_names = sorted({str(item["clubelo_name"]) for item in passed.values()})
    histories: dict[str, list[dict]] = {}
    history_failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_HISTORY_WORKERS) as pool:
        futures = {pool.submit(core._history, name): name for name in unique_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                histories[name] = future.result()
            except Exception as exc:
                history_failures[name] = f"{type(exc).__name__}: {exc}"

    usable = {
        team: item for team, item in passed.items()
        if str(item["clubelo_name"]) in histories
    }
    coverage = len(usable) / max(1, len(teams))
    report = {
        "schema_version": "V5.1.5-clubelo-history-domain-r2",
        "generated_at_utc": utc_now(),
        "competition_id": cid,
        "country": country,
        "seasons": seasons,
        "anchor_dates": anchors,
        "snapshot_audit": snapshot_audit,
        "processed_team_count": len(teams),
        "identity_pass_count": len(passed),
        "usable_history_team_count": len(usable),
        "coverage": coverage,
        "identity_failures": failed,
        "history_failures": history_failures,
        "mappings": mappings,
        "status": "PASS" if coverage >= 0.95 and not history_failures else "PARTIAL",
        "execution_implementation": f"bounded_history_concurrency_{MAX_HISTORY_WORKERS}_same_frozen_identity_and_fetch_contract",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
    }
    history_rows = [row for name in sorted(histories) for row in histories[name]]
    return report, history_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--history", required=True)
    args = parser.parse_args()

    try:
        report, history_rows = run_domain(args.competition)
    except Exception as exc:
        report = {
            "schema_version": "V5.1.5-clubelo-history-domain-r2",
            "generated_at_utc": utc_now(),
            "competition_id": args.competition,
            "status": "EXECUTION_FAILURE",
            "coverage": 0.0,
            "identity_failures": {},
            "history_failures": {},
            "mappings": {},
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "execution_implementation": f"bounded_history_concurrency_{MAX_HISTORY_WORKERS}",
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
        }
        history_rows = []

    report_path = Path(args.report)
    history_path = Path(args.history)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with history_path.open("w", encoding="utf-8") as handle:
        for row in history_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({
        "competition_id": args.competition,
        "status": report.get("status"),
        "coverage": report.get("coverage"),
        "history_row_count": len(history_rows),
        "history_failure_count": len(report.get("history_failures") or {}),
        "error": report.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
