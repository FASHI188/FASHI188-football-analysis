#!/usr/bin/env python3
"""Compatibility wrapper for Football-Data extra-league archive schemas.

Extra-league files use Home/Away/HG/AG/Res and may contain many historical
seasons in one CSV. This wrapper:

1. canonicalizes only known field aliases;
2. filters rows to the exact configured recent seasons;
3. excludes promotion/relegation playoff rows involving low-frequency teams;
4. preserves the source Season value and maps it to the project season label;
5. records an explicit filter audit and delegates result validation/profile
   generation to the audited core.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CORE_PATH = Path(__file__).with_name("ingest_batch_001.py")
SPEC = importlib.util.spec_from_file_location("ingest_batch_001_core", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = CORE
SPEC.loader.exec_module(CORE)

ORIGINAL_PARSE = CORE.parse_csv
CONFIG = CORE.load_config()
EXTRA_POLICIES = {
    item["league_id"]: item
    for item in CONFIG["leagues"]
    if item.get("source_type") == "extra"
}
FILTER_AUDIT: dict[str, dict] = {}
ALIASES = {
    "Home": "HomeTeam",
    "Home Team": "HomeTeam",
    "Away": "AwayTeam",
    "Away Team": "AwayTeam",
    "HG": "FTHG",
    "AG": "FTAG",
    "Res": "FTR",
    "Result": "FTR",
}


def _canonicalize(content: bytes, spec) -> bytes:
    text = CORE.decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CORE.DataError(f"{spec.league_id} {spec.season}: missing CSV header")

    original_fields = [str(name).strip() for name in reader.fieldnames if name is not None]
    canonical_fields = []
    seen = set()
    for field in original_fields:
        mapped = ALIASES.get(field, field)
        if mapped not in seen:
            canonical_fields.append(mapped)
            seen.add(mapped)

    policy = EXTRA_POLICIES.get(spec.league_id)
    allowed = set(policy.get("allowed_source_seasons", [])) if policy else set()
    season_field = policy.get("row_season_field", "Season") if policy else "Season"

    rows = []
    observed_seasons = set()
    total_source_rows = 0
    for raw in reader:
        total_source_rows += 1
        row = {}
        for key, value in raw.items():
            if key is None:
                continue
            mapped = ALIASES.get(str(key).strip(), str(key).strip())
            if mapped not in row or not row[mapped]:
                row[mapped] = "" if value is None else str(value).strip()
        source_season = row.get(season_field, "")
        observed_seasons.add(source_season)
        if allowed and source_season not in allowed:
            continue
        rows.append(row)

    if policy and allowed:
        missing_seasons = sorted(allowed - observed_seasons)
        if missing_seasons:
            raise CORE.DataError(
                f"{spec.league_id}: configured seasons absent from source: {missing_seasons}"
            )
    if not rows:
        raise CORE.DataError(f"{spec.league_id}: no rows remained after season filtering")

    FILTER_AUDIT[spec.league_id] = {
        "raw_archive_rows": total_source_rows,
        "rows_after_season_filter": len(rows),
        "allowed_source_seasons": sorted(allowed),
    }

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=canonical_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _exclude_low_frequency_team_rows(rows: list[dict[str, str]], policy: dict) -> list[dict[str, str]]:
    threshold = int(policy.get("exclude_low_frequency_team_rows_below", 0))
    if threshold <= 0:
        return rows

    season_field = policy.get("row_season_field", "Season")
    by_season: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_season[row.get(season_field, "")].append(row)

    kept: list[dict[str, str]] = []
    excluded_details = []
    for source_season, subset in sorted(by_season.items()):
        appearances: Counter[str] = Counter()
        for row in subset:
            appearances[row["HomeTeam"]] += 1
            appearances[row["AwayTeam"]] += 1
        regular_teams = {team for team, count in appearances.items() if count >= threshold}
        season_kept = [
            row for row in subset
            if row["HomeTeam"] in regular_teams and row["AwayTeam"] in regular_teams
        ]
        season_excluded = [row for row in subset if row not in season_kept]
        kept.extend(season_kept)
        if season_excluded:
            excluded_details.append(
                {
                    "source_season": source_season,
                    "excluded_rows": len(season_excluded),
                    "excluded_matches": [
                        {
                            "date": row.get("Date"),
                            "home": row.get("HomeTeam"),
                            "away": row.get("AwayTeam"),
                            "score": f"{row.get('FTHG')}-{row.get('FTAG')}",
                        }
                        for row in season_excluded
                    ],
                }
            )

    audit = FILTER_AUDIT.setdefault(policy["league_id"], {})
    audit.update(
        {
            "low_frequency_team_threshold": threshold,
            "rows_after_playoff_filter": len(kept),
            "excluded_playoff_rows": len(rows) - len(kept),
            "excluded_details": excluded_details,
            "filter_reason": policy.get("excluded_rows_meaning"),
        }
    )
    return kept


def parse_csv_with_aliases(content: bytes, spec):
    text = CORE.decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    fields = [] if reader.fieldnames is None else [str(f).strip() for f in reader.fieldnames if f is not None]
    alias_targets = {ALIASES.get(field, field) for field in fields}

    policy = EXTRA_POLICIES.get(spec.league_id)
    needs_aliases = not all(field in fields for field in CORE.REQUIRED_RESULT_FIELDS)
    if needs_aliases and not all(field in alias_targets for field in CORE.REQUIRED_RESULT_FIELDS):
        preview = text[:300].replace("\n", "\\n")
        raise CORE.DataError(
            f"{spec.league_id} {spec.season}: unsupported CSV schema; "
            f"fields={fields[:20]!r}; preview={preview!r}"
        )

    normalized_content = _canonicalize(content, spec) if (needs_aliases or policy) else content
    rows, normalized_fields = ORIGINAL_PARSE(normalized_content, spec)

    if policy:
        rows = _exclude_low_frequency_team_rows(rows, policy)
        season_field = policy.get("row_season_field", "Season")
        season_map = policy.get("season_label_map", {})
        counts = {}
        for row in rows:
            source_season = row.get(season_field, "")
            if source_season not in season_map:
                raise CORE.DataError(
                    f"{spec.league_id}: unmapped source season after filtering: {source_season!r}"
                )
            row["season"] = season_map[source_season]
            counts[row["season"]] = counts.get(row["season"], 0) + 1

        minimum = int(CONFIG.get("hard_checks", {}).get("minimum_rows_complete_season", 150))
        current_label = max(season_map.values())
        for label, count in sorted(counts.items()):
            is_current_partial = spec.league_id == "SWE_Allsvenskan" and label == current_label
            if not is_current_partial and count < minimum:
                raise CORE.DataError(
                    f"{spec.league_id} {label}: only {count} rows; expected at least {minimum}"
                )
        FILTER_AUDIT[spec.league_id]["final_rows_by_project_season"] = counts

    return rows, normalized_fields


CORE.parse_csv = parse_csv_with_aliases


def _finalize_manifest(manifest: dict) -> dict:
    manifest["extra_archive_filter_audit"] = FILTER_AUDIT
    manifest["limitations"] = [
        "Swiss and Swedish raw files preserve the full upstream archive; processed profiles include only configured recent seasons.",
        "Promotion/relegation playoff rows involving teams with fewer than five seasonal appearances are excluded and listed in the filter audit.",
        "Split-stage labels for Scotland and Switzerland are not inferred without a trusted round/stage source.",
        "Historical profiles are descriptive priors and cannot override question-time market and lineup evidence.",
    ]
    path = CORE.ROOT / "manifests" / "latest_ingestion.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-extra", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = _finalize_manifest(CORE.run(strict_extra=args.strict_extra))
    except CORE.DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.print_summary:
        print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
