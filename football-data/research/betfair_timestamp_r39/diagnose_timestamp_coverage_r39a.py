#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DT_FORMATS = (
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("registration root must be object")
    return value


def parse_dt(value: str) -> datetime | None:
    text = value.strip()
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def norm_header(value: str) -> str:
    return value.replace("\ufeff", "").strip().upper().replace(" ", "_")


def norm_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_csv(source_dir: Path) -> list[Path]:
    if not source_dir.exists():
        return []
    return sorted(path for path in source_dir.rglob("*.csv") if path.is_file())


def sniff(path: Path) -> csv.Dialect:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(65536)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def is_soccer(value: str) -> bool:
    text = norm_text(value)
    return text in {"1", "1.0", "soccer", "football"}


def is_draw_name(value: str) -> bool:
    text = norm_text(value)
    return text in {"draw", "the draw", "x"} or text.endswith(" draw")


def record_status(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def source_unavailable(reg: dict[str, Any], out_dir: Path, reason: str) -> dict[str, Any]:
    payload = {
        "schema_version": reg["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "STOP_SOURCE_DOWNLOAD_OR_EXTRACTION_UNAVAILABLE",
        "reason": reason,
        "source": reg["source"],
        "no_label_audit": {
            "winner_label_field_values_accessed": 0,
            "score_result_values_accessed": 0,
            "candidate_effect_metrics_computed": 0,
        },
        "hard_limits": reg["hard_limits"],
    }
    record_status(out_dir, payload)
    return payload


def read_markets(paths: list[Path]) -> tuple[dict[str, Any], dict[str, Any]]:
    markets: dict[str, Any] = {}
    stats = Counter()
    inplay_values = Counter()
    header_sets: list[list[str]] = []
    source_files = []
    winner_field_present = False

    required = {
        "SPORTS_ID", "EVENT_ID", "FULL_DESCRIPTION", "SCHEDULED_OFF", "EVENT",
        "SELECTION_ID", "SELECTION", "ODDS", "LATEST_TAKEN", "FIRST_TAKEN", "IN_PLAY",
    }

    for path in paths:
        dialect = sniff(path)
        source_files.append({
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle, dialect)
            try:
                header = next(reader)
            except StopIteration:
                continue
            normalized = [norm_header(value) for value in header]
            header_sets.append(normalized)
            index = {name: i for i, name in enumerate(normalized)}
            winner_field_present = winner_field_present or "WIN_FLAG" in index
            missing = required - set(index)
            if missing:
                stats["files_missing_required_header"] += 1
                continue

            for row in reader:
                stats["source_rows_seen"] += 1
                if len(row) < len(normalized):
                    stats["short_rows"] += 1
                    continue
                sport = row[index["SPORTS_ID"]]
                if not is_soccer(sport):
                    continue
                stats["soccer_rows"] += 1
                event_name = norm_text(row[index["EVENT"]])
                if event_name != "match odds":
                    continue
                stats["soccer_match_odds_rows"] += 1
                in_play = row[index["IN_PLAY"]].strip().upper()
                inplay_values[in_play] += 1
                if in_play != "PE":
                    continue
                stats["soccer_match_odds_pre_event_rows"] += 1

                scheduled = parse_dt(row[index["SCHEDULED_OFF"]])
                first = parse_dt(row[index["FIRST_TAKEN"]])
                latest = parse_dt(row[index["LATEST_TAKEN"]])
                if scheduled is None or first is None or latest is None:
                    stats["timestamp_parse_failures"] += 1
                    continue
                try:
                    odds = float(row[index["ODDS"]])
                except ValueError:
                    stats["odds_parse_failures"] += 1
                    continue
                if not math.isfinite(odds) or odds <= 1.0:
                    stats["invalid_odds"] += 1
                    continue
                if latest < first:
                    stats["latest_before_first"] += 1
                    continue

                event_id = row[index["EVENT_ID"]].strip()
                selection_id = row[index["SELECTION_ID"]].strip()
                selection_name = row[index["SELECTION"]].strip()
                full_description = row[index["FULL_DESCRIPTION"]].strip()
                market = markets.setdefault(event_id, {
                    "scheduled": scheduled,
                    "descriptions": Counter(),
                    "scheduled_values": Counter(),
                    "selections": {},
                })
                market["descriptions"][full_description] += 1
                market["scheduled_values"][scheduled.isoformat()] += 1
                selection = market["selections"].setdefault(selection_id, {
                    "name": selection_name,
                    "names": Counter(),
                    "trades": [],
                })
                selection["names"][selection_name] += 1
                selection["trades"].append((first, latest, odds))

    audit = {
        "source_files": source_files,
        "headers": header_sets,
        "winner_field_present": winner_field_present,
        "winner_label_field_values_accessed": 0,
        "score_result_values_accessed": 0,
        "stats": dict(stats),
        "in_play_values": dict(inplay_values),
    }
    return markets, audit


def snapshot(market: dict[str, Any], cutoff_minutes: int, mode: str) -> dict[str, Any] | None:
    cutoff = market["scheduled"] - timedelta(minutes=cutoff_minutes)
    chosen = []
    for selection in market["selections"].values():
        trades = selection["trades"]
        completed = [trade for trade in trades if trade[1] <= cutoff]
        straddlers = [trade for trade in trades if trade[0] <= cutoff < trade[1]]
        if mode == "strict_identifiable":
            if straddlers or not completed:
                return None
            picked = max(completed, key=lambda trade: trade[1])
            effective_time = picked[1]
        elif mode == "latest_completed_level_proxy":
            if not completed:
                return None
            picked = max(completed, key=lambda trade: trade[1])
            effective_time = picked[1]
        elif mode == "unique_straddler_proxy":
            if len(straddlers) != 1:
                return None
            picked = straddlers[0]
            effective_time = cutoff
        else:
            raise ValueError(mode)
        chosen.append({
            "name": selection["name"],
            "odds": picked[2],
            "time": effective_time,
            "straddlers": len(straddlers),
        })
    if len(chosen) != 3:
        return None
    timestamps = [item["time"] for item in chosen]
    staleness = max((cutoff - ts).total_seconds() for ts in timestamps) / 60.0
    span = (max(timestamps) - min(timestamps)).total_seconds() / 60.0
    inverses = [1.0 / item["odds"] for item in chosen]
    denom = sum(inverses)
    probabilities = [value / denom for value in inverses]
    draw_probs = [
        p for item, p in zip(chosen, probabilities)
        if is_draw_name(item["name"])
    ]
    return {
        "staleness_minutes": staleness,
        "runner_span_minutes": span,
        "draw_probability_available": len(draw_probs) == 1,
        "draw_probability": draw_probs[0] if len(draw_probs) == 1 else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    reg = load_json(args.registration)
    csv_paths = discover_csv(args.source_dir)
    if not csv_paths:
        payload = source_unavailable(reg, args.out_dir, "no CSV file found after public-source download/extraction")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    markets, source_audit = read_markets(csv_paths)
    raw_market_count = len(markets)
    market_quality = Counter()
    eligible: dict[str, Any] = {}
    draw_names = Counter()
    for event_id, market in markets.items():
        market_quality["pre_event_match_odds_markets"] += 1
        if len(market["scheduled_values"]) != 1:
            market_quality["multiple_scheduled_off_values"] += 1
            continue
        if len(market["selections"]) != 3:
            market_quality["not_exactly_three_selections"] += 1
            continue
        draw_selection_names = [
            selection["name"]
            for selection in market["selections"].values()
            if is_draw_name(selection["name"])
        ]
        if len(draw_selection_names) != 1:
            market_quality["draw_selection_not_unique"] += 1
            continue
        draw_names[draw_selection_names[0]] += 1
        eligible[event_id] = market
        market_quality["identity_eligible_markets"] += 1

    cutoffs = [int(value) for value in reg["cutoffs_minutes_before_kickoff"]]
    modes = ["strict_identifiable", "latest_completed_level_proxy", "unique_straddler_proxy"]
    snapshots: dict[str, dict[int, dict[str, dict[str, Any]]]] = {
        mode: {cutoff: {} for cutoff in cutoffs} for mode in modes
    }
    cutoff_counts: dict[str, dict[str, int]] = {mode: {} for mode in modes}

    for mode in modes:
        for cutoff in cutoffs:
            for event_id, market in eligible.items():
                snap = snapshot(market, cutoff, mode)
                if snap is not None and snap["draw_probability_available"]:
                    snapshots[mode][cutoff][event_id] = snap
            cutoff_counts[mode][f"T{cutoff}"] = len(snapshots[mode][cutoff])

    grid_rows = []
    for mode in ("strict_identifiable", "latest_completed_level_proxy"):
        for cutoff in cutoffs:
            for stale in reg["diagnostic_staleness_minutes"]:
                for span in reg["diagnostic_runner_span_minutes"]:
                    count = sum(
                        1 for snap in snapshots[mode][cutoff].values()
                        if snap["staleness_minutes"] <= float(stale) + 1e-12
                        and snap["runner_span_minutes"] <= float(span) + 1e-12
                    )
                    grid_rows.append({
                        "mode": mode,
                        "cutoff": cutoff,
                        "max_staleness": stale,
                        "max_runner_span": span,
                        "eligible_markets": count,
                    })

    joint_rows = []
    for mode in ("strict_identifiable", "latest_completed_level_proxy"):
        for config in reg["priority_joint_configs"]:
            start_set = {
                event_id for event_id, snap in snapshots[mode][int(config["start"])].items()
                if snap["staleness_minutes"] <= float(config["start_staleness"]) + 1e-12
                and snap["runner_span_minutes"] <= float(config["start_span"]) + 1e-12
            }
            end_set = {
                event_id for event_id, snap in snapshots[mode][int(config["end"])].items()
                if snap["staleness_minutes"] <= float(config["end_staleness"]) + 1e-12
                and snap["runner_span_minutes"] <= float(config["end_span"]) + 1e-12
            }
            joint_rows.append({
                "mode": mode,
                "config": config["name"],
                "eligible_markets": len(start_set & end_set),
            })

    best_strict = max((row["eligible_markets"] for row in joint_rows if row["mode"] == "strict_identifiable"), default=0)
    best_proxy = max((row["eligible_markets"] for row in joint_rows if row["mode"] == "latest_completed_level_proxy"), default=0)
    target = int(reg["sample_gate"]["minimum_for_next_stage"])
    if best_strict >= target:
        status = "PASS_R39A_STRICT_TIMESTAMP_COVERAGE_AT_LEAST_100_NO_LABELS"
    elif best_proxy >= target:
        status = "PASS_R39A_PROXY_TIMESTAMP_COVERAGE_AT_LEAST_100_NO_LABELS_STRICT_UNAVAILABLE"
    else:
        status = "STOP_R39A_TIMESTAMP_COVERAGE_BELOW_100_NO_LABELS"

    payload = {
        "schema_version": reg["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "source": {
            **reg["source"],
            "csv_files_found": len(csv_paths),
            "source_file_audit": source_audit["source_files"],
        },
        "no_label_audit": {
            "winner_field_present": source_audit["winner_field_present"],
            "winner_label_field_values_accessed": 0,
            "score_result_values_accessed": 0,
            "candidate_effect_metrics_computed": 0,
            "market_identity_selection_performed": false if False else False
        },
        "row_audit": source_audit["stats"],
        "in_play_value_audit": source_audit["in_play_values"],
        "market_audit": {
            "raw_pre_event_match_odds_market_ids": raw_market_count,
            **dict(market_quality),
            "draw_selection_names": dict(draw_names),
        },
        "snapshot_counts_without_staleness_gate": cutoff_counts,
        "priority_joint_coverage": joint_rows,
        "best_priority_joint_strict": best_strict,
        "best_priority_joint_proxy": best_proxy,
        "sample_gate_target": target,
        "snapshot_claim": {
            "exact_stream_snapshot_claim_allowed": False,
            "strict_identifiable_definition": reg["snapshot_definition"]["strict_identifiable"],
            "proxy_definition": reg["snapshot_definition"]["latest_completed_level_proxy"],
        },
        "hard_limits": reg["hard_limits"],
    }
    record_status(args.out_dir, payload)

    with (args.out_dir / "coverage_grid.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["mode", "cutoff", "max_staleness", "max_runner_span", "eligible_markets"])
        writer.writeheader()
        writer.writerows(grid_rows)
    with (args.out_dir / "priority_joint_coverage.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["mode", "config", "eligible_markets"])
        writer.writeheader()
        writer.writerows(joint_rows)

    manifest = []
    for path in sorted(args.out_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest.append({"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    (args.out_dir / "manifest.json").write_text(
        json.dumps({"schema": "r39a-manifest", "files": manifest}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": status,
        "market_audit": payload["market_audit"],
        "snapshot_counts": cutoff_counts,
        "priority_joint_coverage": joint_rows,
        "best_strict": best_strict,
        "best_proxy": best_proxy,
        "winner_label_values_accessed": 0,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
