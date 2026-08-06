#!/usr/bin/env python3
"""Audit all pinned public Betfair football MATCH_ODDS files without retaining raw data."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PARSER_PATH = ROOT / "research" / "betfair_basic_trajectory_r1" / "ingest_betfair_basic_trajectory_r1.py"
PARSER_CONFIG = ROOT / "research" / "betfair_basic_trajectory_r1" / "preregistration.json"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def load_parser():
    spec = importlib.util.spec_from_file_location("betfair_inventory_parser_r1", PARSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Betfair parser")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspect_stream(path: Path, module: Any, parser_cfg: dict[str, Any]) -> dict[str, Any]:
    accepted = {str(value).casefold() for value in parser_cfg["input_contract"]["accepted_extensions"]}
    definition = None
    ltp: dict[int, float] = {}
    first_complete_minutes = None
    first_message_pt = None
    last_message_pt = None
    for text in module.lines(path, accepted):
        message = json.loads(text)
        pt = module.epoch_ms(message.get("pt"))
        first_message_pt = pt if first_message_pt is None else min(first_message_pt, pt)
        last_message_pt = pt if last_message_pt is None else max(last_message_pt, pt)
        for change in message.get("mc") or []:
            if not isinstance(change, dict):
                continue
            if isinstance(change.get("marketDefinition"), dict):
                definition = change["marketDefinition"]
            for row in change.get("rc") or []:
                if isinstance(row, dict) and row.get("id") is not None and "ltp" in row:
                    try:
                        value = float(row["ltp"])
                    except (TypeError, ValueError):
                        continue
                    if value >= float(parser_cfg["market_contract"]["ltp_minimum"]):
                        ltp[int(row["id"])] = value
            if definition is not None and definition.get("inPlay") is False:
                mapping = module.runner_map(definition, parser_cfg)
                ids = [mapping["home_id"], mapping["draw_id"], mapping["away_id"]]
                if first_complete_minutes is None and all(runner_id in ltp for runner_id in ids):
                    market_time = module.parse_iso(str(definition["marketTime"]))
                    minutes = (market_time - pt).total_seconds() / 60.0
                    if minutes > 0:
                        first_complete_minutes = minutes
    outcome = None
    if isinstance(definition, dict):
        mapping = module.runner_map(definition, parser_cfg)
        winners = [int(row["id"]) for row in definition.get("runners") or [] if row.get("status") == "WINNER"]
        if len(winners) == 1:
            winner = winners[0]
            outcome = "H" if winner == mapping["home_id"] else "D" if winner == mapping["draw_id"] else "A" if winner == mapping["away_id"] else None
    return {
        "outcome": outcome,
        "first_complete_three_runner_minutes_before_kickoff": first_complete_minutes,
        "first_message_pt": first_message_pt.isoformat() if first_message_pt else None,
        "last_message_pt": last_message_pt.isoformat() if last_message_pt else None,
    }


def gate_pass(counts: dict[str, int], gate: dict[str, int]) -> bool:
    return (
        counts["valid_markets"] >= int(gate["minimum_valid_markets"])
        and counts["complete_all_core_cutoffs"] >= int(gate["minimum_markets_complete_at_all_core_cutoffs"])
        and counts["settled_draw"] >= int(gate["minimum_settled_draws"])
    )


def run(contract_path: Path, source_checkout: Path, out_path: Path) -> dict[str, Any]:
    contract = load_json(contract_path)
    parser_cfg = load_json(PARSER_CONFIG)
    module = load_parser()
    source_root = source_checkout / str(contract["source"]["root"])
    if not source_root.is_dir():
        raise RuntimeError(f"source root missing: {source_root}")
    required = [text.encode("utf-8") for text in contract["candidate_rule"]["must_contain"]]
    all_files = sorted(path for path in source_root.rglob("*") if path.is_file())
    candidates: list[Path] = []
    for path in all_files:
        data = path.read_bytes()
        if all(token in data for token in required):
            candidates.append(path)

    cutoff_values = [int(value) for value in contract["cutoffs_minutes_before_kickoff"]]
    core_cutoffs = {int(value) for value in contract["core_cutoffs_minutes_before_kickoff"]}
    cutoff_coverage = Counter({value: 0 for value in cutoff_values})
    outcomes = Counter()
    failures: list[dict[str, Any]] = []
    first_complete_values: list[float] = []
    complete_core = 0
    valid = 0
    with tempfile.TemporaryDirectory(prefix="betfair-inventory-r1-") as tmp:
        temp_root = Path(tmp)
        for index, path in enumerate(candidates):
            relative = path.relative_to(source_checkout).as_posix()
            try:
                # Source market IDs such as 1.232827159 look like file extensions to pathlib.
                # Copy each source byte-for-byte to an ephemeral .jsonl alias so the already
                # validated PR #100 parser can enforce its frozen extension contract unchanged.
                alias = temp_root / f"market_{index:06d}.jsonl"
                shutil.copyfile(path, alias)
                parsed = module.parse_file(alias, parser_cfg)
                receipts = [row for row in parsed["market_receipts"] if row["valid_match_odds_definition"]]
                if parsed["status"] != "PASS_BETFAIR_BASIC_TRAJECTORY_INGESTED" or len(receipts) != 1:
                    raise RuntimeError(f"parser status/market count invalid: {parsed['status']} {len(receipts)}")
                snapshots = {int(row["minutes_before_kickoff"]): row for row in parsed["snapshots"]}
                for cutoff in cutoff_values:
                    if cutoff in snapshots:
                        cutoff_coverage[cutoff] += 1
                if all(cutoff in snapshots for cutoff in core_cutoffs):
                    complete_core += 1
                stream = inspect_stream(alias, module, parser_cfg)
                if stream["outcome"]:
                    outcomes[stream["outcome"]] += 1
                if stream["first_complete_three_runner_minutes_before_kickoff"] is not None:
                    first_complete_values.append(float(stream["first_complete_three_runner_minutes_before_kickoff"]))
                valid += 1
            except Exception as exc:
                failures.append({"path": relative, "reason": f"{type(exc).__name__}: {exc}"})

    counts = {
        "files_scanned": len(all_files),
        "candidate_match_odds_files": len(candidates),
        "valid_markets": valid,
        "invalid_candidate_files": len(failures),
        "settled_markets": sum(outcomes.values()),
        "settled_home": outcomes.get("H", 0),
        "settled_draw": outcomes.get("D", 0),
        "settled_away": outcomes.get("A", 0),
        "complete_all_core_cutoffs": complete_core,
    }
    gates = contract["readiness_gates"]
    exploratory_ready = gate_pass(counts, gates["exploratory_pilot"])
    model_ready = gate_pass(counts, gates["model_screen"])
    if model_ready:
        status = "PASS_PUBLIC_INVENTORY_READY_FOR_PREREGISTERED_MODEL_SCREEN"
    elif exploratory_ready:
        status = "PASS_PUBLIC_INVENTORY_READY_FOR_EXPLORATORY_PILOT_ONLY"
    else:
        status = "FAIL_PUBLIC_INVENTORY_TRAJECTORY_COVERAGE_INSUFFICIENT"
    first_summary = {
        "rows": len(first_complete_values),
        "minimum": min(first_complete_values) if first_complete_values else None,
        "p25": statistics.quantiles(first_complete_values, n=4, method="inclusive")[0] if first_complete_values else None,
        "median": statistics.median(first_complete_values) if first_complete_values else None,
        "p75": statistics.quantiles(first_complete_values, n=4, method="inclusive")[2] if first_complete_values else None,
        "maximum": max(first_complete_values) if first_complete_values else None,
    }
    result = {
        "schema_version": "BETFAIR-PUBLIC-INVENTORY-AUDIT-R1-STATUS",
        "status": status,
        "source": contract["source"],
        "counts": counts,
        "cutoff_coverage": {str(value): cutoff_coverage[value] for value in cutoff_values},
        "first_complete_three_runner_minutes_before_kickoff": first_summary,
        "readiness": {
            "exploratory_pilot_ready": exploratory_ready,
            "model_screen_ready": model_ready,
            "exploratory_gate": gates["exploratory_pilot"],
            "model_gate": gates["model_screen"],
        },
        "failures": failures,
        "ruling": {
            "audit_process_completed": len(candidates) == valid + len(failures),
            "source_files_copied_only_to_ephemeral_jsonl_aliases": True,
            "raw_files_persisted_or_uploaded": False,
            "raw_data_redistributed": False,
            "model_fit_performed": False,
            "threshold_selected": False,
            "performance_claim_made": False,
            "formal_weight": 0,
            "current_match_use_allowed": False,
            "formal_ev_allowed": False,
        },
        "hard_limits": contract["hard_limits"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.contract, args.source_checkout, args.out)
    print(json.dumps({
        "status": result["status"],
        "counts": result["counts"],
        "cutoff_coverage": result["cutoff_coverage"],
        "first_complete": result["first_complete_three_runner_minutes_before_kickoff"],
        "readiness": result["readiness"],
        "failures": result["failures"],
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
