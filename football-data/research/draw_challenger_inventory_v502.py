#!/usr/bin/env python3
"""Read-only inventory for the V5.0.2 isolated H/D/A draw challenger.

The script uses only repository files. It does not import provider clients, access
secrets, change formal assets, or train a model. Output is written outside the
repository when invoked by CI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".yml", ".yaml", ".csv", ".tsv"}
MATCH_TERMS = (
    "draw", "1x2", "h/d/a", "selector", "market", "odds", "elo", "strength",
    "matrix", "score", "oof", "calibr", "train", "dataset", "feature", "e3e",
    "v650", "v651", "v658", "b100", "6251", "5524", "5110", "1611",
)
LEAK_PATTERNS = (
    r"(^|_)(result|final_result|outcome|label|target)($|_)",
    r"(^|_)(home_goals|away_goals|fthg|ftag|ftr|score|final_score)($|_)",
    r"(^|_)(postmatch|full_time|actual_)($|_)",
)
DATE_NAMES = {"date", "match_date", "kickoff", "kickoff_utc", "scheduled_kickoff_utc", "utc_date"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def text_matches(path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                lower = line.lower()
                found = [term for term in MATCH_TERMS if term in lower]
                if found:
                    results.append({"line": lineno, "terms": found, "text": line.rstrip()[:500]})
                    if len(results) >= 80:
                        break
    except OSError:
        pass
    return results


def csv_profile(path: Path) -> dict[str, Any]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    profile: dict[str, Any] = {"kind": "tabular", "delimiter": delimiter, "row_count": 0}
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            columns = reader.fieldnames or []
            profile["columns"] = columns
            profile["leakage_candidate_columns"] = [
                col for col in columns if any(re.search(pattern, col.lower()) for pattern in LEAK_PATTERNS)
            ]
            date_columns = [col for col in columns if col.lower() in DATE_NAMES or "date" in col.lower() or "kickoff" in col.lower()]
            profile["date_columns"] = date_columns
            mins: dict[str, str] = {}
            maxs: dict[str, str] = {}
            samples: list[dict[str, str]] = []
            value_counts: dict[str, Counter[str]] = {col: Counter() for col in profile["leakage_candidate_columns"][:8]}
            for row in reader:
                profile["row_count"] += 1
                if len(samples) < 3:
                    samples.append({key: (value or "")[:160] for key, value in row.items()})
                for col in date_columns[:5]:
                    value = (row.get(col) or "").strip()
                    if value:
                        mins[col] = min(mins.get(col, value), value)
                        maxs[col] = max(maxs.get(col, value), value)
                for col, counter in value_counts.items():
                    value = (row.get(col) or "").strip()
                    if value:
                        counter[value] += 1
            profile["date_min"] = mins
            profile["date_max"] = maxs
            profile["sample_rows"] = samples
            profile["candidate_target_counts"] = {col: dict(counter.most_common(20)) for col, counter in value_counts.items()}
    except Exception as exc:  # inventory must report, not hide
        profile["error"] = f"{type(exc).__name__}: {exc}"
    return profile


def json_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): json_shape(v, depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "first": json_shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def json_profile(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {"kind": "json", "shape": json_shape(value)}
    except Exception as exc:
        return {"kind": "json", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    root = Path(git_output("rev-parse", "--show-toplevel"))
    tracked = [Path(line) for line in git_output("ls-files").splitlines() if line.strip()]
    head = git_output("rev-parse", "HEAD")
    formal_prefixes = (
        "football-data/models/", "football-data/model/", "football-data/processed/",
        "football-data/config/", "football-data/team_strengths/",
    )
    records: list[dict[str, Any]] = []
    grep_lines: list[str] = []
    number_hits: list[dict[str, Any]] = []

    for rel in tracked:
        path = root / rel
        lower = rel.as_posix().lower()
        if not path.is_file():
            continue
        name_match = any(term in lower for term in MATCH_TERMS)
        content_hits: list[dict[str, Any]] = []
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 8 * 1024 * 1024:
            content_hits = text_matches(path)
        if not name_match and not content_hits:
            continue
        record: dict[str, Any] = {
            "path": rel.as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "suffix": path.suffix.lower(),
            "formal_asset_path": rel.as_posix().startswith(formal_prefixes),
            "content_match_count": len(content_hits),
        }
        if path.suffix.lower() in {".csv", ".tsv"} and path.stat().st_size <= 200 * 1024 * 1024:
            record["profile"] = csv_profile(path)
        elif path.suffix.lower() == ".json" and path.stat().st_size <= 30 * 1024 * 1024:
            record["profile"] = json_profile(path)
        records.append(record)
        for hit in content_hits:
            grep_lines.append(f"{rel}:{hit['line']} [{','.join(hit['terms'])}] {hit['text']}")
            if any(str(number) in hit["text"] for number in (5524, 5110, 1611, 6251, 1252, 359, 311)):
                number_hits.append({"path": rel.as_posix(), **hit})

    all_suffixes = Counter(path.suffix.lower() or "<none>" for path in tracked)
    inventory = {
        "schema_version": "DRAW-CHALLENGER-INVENTORY-V502-1.0",
        "head": head,
        "tracked_file_count": len(tracked),
        "matched_file_count": len(records),
        "formal_weight": 0,
        "provider_network_used": False,
        "external_request_attempts": 0,
        "suffix_counts": dict(sorted(all_suffixes.items())),
        "matched_files": sorted(records, key=lambda row: row["path"]),
        "legacy_number_hits": number_hits,
    }
    (out / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "grep_matches.txt").write_text("\n".join(grep_lines) + "\n", encoding="utf-8")
    (out / "tracked_files.txt").write_text("\n".join(path.as_posix() for path in tracked) + "\n", encoding="utf-8")
    (out / "git_status.txt").write_text(git_output("status", "--short") + "\n", encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({
        "checkout_head": head,
        "formal_weight": 0,
        "provider_network_used": False,
        "external_request_attempts": 0,
        "api_football_key_accessed": False,
        "model_training": 0,
        "phase": "READ_ONLY_INVENTORY",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"head": head, "matched_files": len(records), "legacy_number_hits": len(number_hits)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
