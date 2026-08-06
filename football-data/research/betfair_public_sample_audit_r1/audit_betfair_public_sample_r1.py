#!/usr/bin/env python3
"""Audit a pinned public Betfair MATCH_ODDS sample without retaining raw files."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PARSER_PATH = ROOT / "research" / "betfair_basic_trajectory_r1" / "ingest_betfair_basic_trajectory_r1.py"
PARSER_CONFIG = ROOT / "research" / "betfair_basic_trajectory_r1" / "preregistration.json"


def load_module():
    spec = importlib.util.spec_from_file_location("betfair_ingest_r1", PARSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import Betfair parser")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def normalized_probabilities(row: dict[str, Any]) -> tuple[float, float, float]:
    odds = [float(row["home_ltp"]), float(row["draw_ltp"]), float(row["away_ltp"])]
    inv = [1.0 / value for value in odds]
    total = sum(inv)
    return tuple(value / total for value in inv)


def settlement(path: Path, module: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    accepted = {str(value).casefold() for value in cfg["input_contract"]["accepted_extensions"]}
    last_definition = None
    first_pt = None
    last_pt = None
    for text in module.lines(path, accepted):
        message = json.loads(text)
        pt = module.epoch_ms(message.get("pt"))
        first_pt = pt if first_pt is None else min(first_pt, pt)
        last_pt = pt if last_pt is None else max(last_pt, pt)
        for change in message.get("mc") or []:
            if isinstance(change, dict) and isinstance(change.get("marketDefinition"), dict):
                last_definition = change["marketDefinition"]
    if not isinstance(last_definition, dict):
        return {"settled": False, "outcome": None, "first_pt": first_pt, "last_pt": last_pt}
    mapping = module.runner_map(last_definition, cfg)
    winners = [int(row["id"]) for row in last_definition.get("runners") or [] if row.get("status") == "WINNER"]
    if len(winners) != 1:
        return {"settled": False, "outcome": None, "first_pt": first_pt, "last_pt": last_pt}
    winner = winners[0]
    outcome = "H" if winner == mapping["home_id"] else "D" if winner == mapping["draw_id"] else "A" if winner == mapping["away_id"] else None
    return {"settled": outcome is not None, "outcome": outcome, "first_pt": first_pt, "last_pt": last_pt}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError("cannot write empty CSV")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(manifest_path: Path, input_dir: Path, out_json: Path, out_csv: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    cfg = load_json(PARSER_CONFIG)
    module = load_module()
    paths = list(manifest["paths"])
    contract = manifest["audit_contract"]
    cutoff_values = [int(value) for value in contract["cutoffs_minutes_before_kickoff"]]
    core_cutoffs = {int(value) for value in contract["core_cutoffs_minutes_before_kickoff"]}
    if len(paths) != int(contract["expected_files"]):
        raise RuntimeError("manifest path count differs from frozen expected_files")

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for source_path in paths:
        market_id = Path(source_path).name
        local = input_dir / f"{market_id}.jsonl"
        if not local.is_file():
            failures.append({"source_path": source_path, "reason": "downloaded file missing"})
            continue
        try:
            parsed = module.parse_file(local, cfg)
            valid_receipts = [row for row in parsed["market_receipts"] if row["valid_match_odds_definition"]]
            if parsed["status"] != "PASS_BETFAIR_BASIC_TRAJECTORY_INGESTED" or len(valid_receipts) != 1:
                raise RuntimeError(f"parser status/market count invalid: {parsed['status']} {len(valid_receipts)}")
            snapshots = {int(row["minutes_before_kickoff"]): row for row in parsed["snapshots"]}
            receipt = valid_receipts[0]
            settled = settlement(local, module, cfg)
            first_snapshot = min((module.parse_iso(row["snapshot_publish_time_utc"]) for row in snapshots.values()), default=None)
            market_time = module.parse_iso(next(iter(snapshots.values()))["market_time_utc"]) if snapshots else None
            first_complete_minutes = None if first_snapshot is None or market_time is None else (market_time - first_snapshot).total_seconds() / 60.0
            result_row: dict[str, Any] = {
                "source_path": source_path,
                "market_id": market_id,
                "input_sha256": parsed["input"]["sha256"],
                "event_name": next(iter(snapshots.values()))["event_name"] if snapshots else "",
                "market_time_utc": next(iter(snapshots.values()))["market_time_utc"] if snapshots else "",
                "settled": bool(settled["settled"]),
                "outcome": settled["outcome"] or "",
                "first_complete_three_runner_minutes_before_kickoff_lower_bound": first_complete_minutes if first_complete_minutes is not None else "",
                "available_cutoff_count": len(snapshots),
                "complete_core_cutoffs": all(value in snapshots for value in core_cutoffs),
            }
            for cutoff in cutoff_values:
                row = snapshots.get(cutoff)
                prefix = f"t_minus_{cutoff}m"
                result_row[f"{prefix}_available"] = row is not None
                result_row[f"{prefix}_snapshot_pt"] = row["snapshot_publish_time_utc"] if row else ""
                result_row[f"{prefix}_home_ltp"] = row["home_ltp"] if row else ""
                result_row[f"{prefix}_draw_ltp"] = row["draw_ltp"] if row else ""
                result_row[f"{prefix}_away_ltp"] = row["away_ltp"] if row else ""
                if row:
                    ph, pd, pa = normalized_probabilities(row)
                    result_row[f"{prefix}_home_fair"] = ph
                    result_row[f"{prefix}_draw_fair"] = pd
                    result_row[f"{prefix}_away_fair"] = pa
                else:
                    result_row[f"{prefix}_home_fair"] = ""
                    result_row[f"{prefix}_draw_fair"] = ""
                    result_row[f"{prefix}_away_fair"] = ""
            rows.append(result_row)
        except Exception as exc:
            failures.append({"source_path": source_path, "reason": f"{type(exc).__name__}: {exc}"})

    cutoff_coverage = {
        str(cutoff): sum(bool(row[f"t_minus_{cutoff}m_available"]) for row in rows)
        for cutoff in cutoff_values
    }
    settled_rows = [row for row in rows if row["settled"]]
    outcome_counts = Counter(row["outcome"] for row in settled_rows)
    complete_core = sum(bool(row["complete_core_cutoffs"]) for row in rows)
    draw_movements = []
    for row in rows:
        start = row.get("t_minus_1440m_draw_fair")
        end = row.get("t_minus_15m_draw_fair")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            draw_movements.append(float(end) - float(start))
    gates = {
        "all_files_downloaded_and_parsed": len(rows) == int(contract["expected_files"]) and not failures,
        "required_valid_match_odds_markets": len(rows) >= int(contract["required_valid_match_odds_markets"]),
        "minimum_markets_complete_at_all_core_cutoffs": complete_core >= int(contract["minimum_markets_complete_at_all_core_cutoffs"]),
        "minimum_settled_markets": len(settled_rows) >= int(contract["minimum_settled_markets"]),
        "minimum_settled_draws": outcome_counts.get("D", 0) >= int(contract["minimum_settled_draws"]),
    }
    pilot_ready = all(gates.values())
    status = "PASS_PUBLIC_SAMPLE_READY_FOR_PREREGISTERED_PILOT" if pilot_ready else "PASS_PUBLIC_SAMPLE_AUDITED_COVERAGE_GATE_NOT_MET" if rows and not failures else "FAIL_PUBLIC_SAMPLE_AUDIT"
    result = {
        "schema_version": "BETFAIR-PUBLIC-SAMPLE-AUDIT-R1-STATUS",
        "status": status,
        "source": manifest["source"],
        "counts": {
            "expected_files": int(contract["expected_files"]),
            "parsed_valid_markets": len(rows),
            "failures": len(failures),
            "settled_markets": len(settled_rows),
            "settled_home": outcome_counts.get("H", 0),
            "settled_draw": outcome_counts.get("D", 0),
            "settled_away": outcome_counts.get("A", 0),
            "complete_all_core_cutoffs": complete_core,
        },
        "cutoff_coverage": cutoff_coverage,
        "draw_fair_probability_movement_t24h_to_t15m": {
            "rows": len(draw_movements),
            "mean": statistics.fmean(draw_movements) if draw_movements else None,
            "median": statistics.median(draw_movements) if draw_movements else None,
            "minimum": min(draw_movements) if draw_movements else None,
            "maximum": max(draw_movements) if draw_movements else None,
        },
        "readiness_gates": gates,
        "pilot_ready": pilot_ready,
        "failures": failures,
        "ruling": {
            "raw_files_persisted_or_uploaded": False,
            "raw_data_redistributed": False,
            "model_fit_performed": False,
            "threshold_selected": False,
            "settlement_labels_used_for_performance_claim": False,
            "formal_weight": 0,
            "current_match_use_allowed": False,
            "formal_ev_allowed": False,
        },
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_csv, rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.manifest, args.input_dir, args.out_json, args.out_csv)
    print(json.dumps({"status": result["status"], "counts": result["counts"], "cutoff_coverage": result["cutoff_coverage"]}, ensure_ascii=False))
    if result["status"].startswith("FAIL"):
        raise SystemExit(2)

if __name__ == "__main__":
    main()
