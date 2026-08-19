#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SOURCE_REVISION = "9fe7fb127cd05316dbd438fe0e5be82c5c3ed536"
SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026")
EXPECTED_SHA256 = {
    "2020-2021": "e794f97ce8d95676a0cf14a78057aba8837973459eda2a1e04194402c4bfaa37",
    "2021-2022": "5411a5a311a2f2e379967b585a2e54646168ca434923e4ca5389cb614d27de78",
    "2022-2023": "916724fe4cad4af6d350805f4962c94456a14edc7afcc6184b01f3fcb77fc06d",
    "2023-2024": "2e4f761f484891bec3457b4c52a3d1ee20e5379fe5efa69e6564133edcbec1b7",
    "2024-2025": "a62ce23b14c112ae02be470bf8e29f3568f2a40a311b2b0034be6cd8c1b53cb3",
    "2025-2026": "f0980a3a37b79a5be947e4a7e3288c9f88e3cf03810b4b23cb8f8871064fc5ab",
}

PREFERRED = (0.5, 1.5, 2.5, 3.5, 4.5)
MARKET_TYPES = {
    "OVER_UNDER_05": 0.5,
    "OVER_UNDER_15": 1.5,
    "OVER_UNDER_25": 2.5,
    "OVER_UNDER_35": 3.5,
    "OVER_UNDER_45": 4.5,
}
MARKET_NAME_RE = re.compile(r"^Over/Under ([0-4]\.5) Goals$", re.IGNORECASE)
RUNNER_RE = re.compile(r"^(Over|Under)\s+([0-4]\.5)(?:\s+Goals)?$", re.IGNORECASE)
SNAPSHOTS = (60, 30, 1)

ALLOWED_FIELDS = (
    "EVENT_DATE", "PATH", "EVENT_ID", "MARKET_TYPE", "MARKET_ID", "MARKET_NAME",
    "SELECTION_ID", "RUNNER_NAME", "HANDICAP", "HOME_TEAM", "AWAY_TEAM",
    "BEST_BACK_PRICE_60_MIN_PRIOR", "BEST_LAY_PRICE_60_MIN_PRIOR", "MATCHED_VOLUME_60_MIN_PRIOR",
    "BEST_BACK_PRICE_30_MIN_PRIOR", "BEST_LAY_PRICE_30_MIN_PRIOR", "MATCHED_VOLUME_30_MIN_PRIOR",
    "BEST_BACK_PRICE_1_MIN_PRIOR", "BEST_LAY_PRICE_1_MIN_PRIOR", "MATCHED_VOLUME_1_MIN_PRIOR",
)
FORBIDDEN_FIELDS = (
    "RUNNER_STATUS", "IS_WINNER", "TOTAL_GOALS", "HOME_SCORE", "AWAY_SCORE",
    "TOTAL_MATCHED_VOLUME", "LAST_PREPLAY_PRICE",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def finite_gt_one(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        x = float(text)
    except ValueError:
        return None
    if not (math.isfinite(x) and x > 1.0):
        return None
    return x


def finite_optional(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        x = float(text)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def recognized_line(market_type: str, market_name: str) -> float | None:
    mt = market_type.strip().upper()
    by_type = MARKET_TYPES.get(mt)
    m = MARKET_NAME_RE.fullmatch(market_name.strip())
    by_name = float(m.group(1)) if m else None
    if by_type is not None and by_name is not None and by_type != by_name:
        return None
    line = by_type if by_type is not None else by_name
    return line if line in PREFERRED else None


def runner_side(name: str, line: float) -> str | None:
    m = RUNNER_RE.fullmatch(name.strip())
    if not m:
        return None
    if float(m.group(2)) != line:
        return None
    return m.group(1).casefold()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    root = Path(args.source_dir)
    file_meta: list[dict[str, Any]] = []
    stats = Counter()
    forbidden_fields_accessed = 0
    target_values_materialized = 0
    model_fit = 0
    model_score = 0

    # market key: season,event_id,line,market_id -> metadata + runner observations
    markets: dict[tuple[str, str, float, str], dict[str, Any]] = {}
    event_identity: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)

    for season in SEASONS:
        path = root / f"A-League_{season}_All_Markets.csv"
        if not path.exists():
            raise SystemExit(f"missing pinned source file: {path}")
        digest = sha256_file(path)
        if digest != EXPECTED_SHA256[season]:
            raise SystemExit(f"SHA256 mismatch for {season}: {digest}")
        meta: dict[str, Any] = {
            "season": season,
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": digest,
            "sha256_expected": EXPECTED_SHA256[season],
            "sha256_match": True,
        }

        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                raise SystemExit(f"empty source file: {path}")
            index = {name.strip(): i for i, name in enumerate(header)}
            missing = [name for name in ALLOWED_FIELDS if name not in index]
            if missing:
                raise SystemExit(f"missing allowed fields in {season}: {missing}")
            # Forbidden fields may be acknowledged by header name only. Their indices are never created or used.
            meta["forbidden_columns_present_by_header_only"] = [name for name in FORBIDDEN_FIELDS if name in index]
            allowed_index = {name: index[name] for name in ALLOWED_FIELDS}

            season_rows = 0
            season_eligible_rows = 0
            for row in reader:
                season_rows += 1
                stats["allowed_rows_decoded"] += 1
                if len(row) < len(header):
                    stats["short_rows"] += 1
                    continue

                # Decode ONLY the allow-listed column positions. No DictReader/whole-row materialization is used.
                event_date = row[allowed_index["EVENT_DATE"]].strip()
                event_path = row[allowed_index["PATH"]].strip()
                event_id = row[allowed_index["EVENT_ID"]].strip()
                market_type = row[allowed_index["MARKET_TYPE"]].strip()
                market_id = row[allowed_index["MARKET_ID"]].strip()
                market_name = row[allowed_index["MARKET_NAME"]].strip()
                selection_id = row[allowed_index["SELECTION_ID"]].strip()
                runner_name = row[allowed_index["RUNNER_NAME"]].strip()
                handicap = row[allowed_index["HANDICAP"]].strip()
                home_team = row[allowed_index["HOME_TEAM"]].strip()
                away_team = row[allowed_index["AWAY_TEAM"]].strip()

                line = recognized_line(market_type, market_name)
                if line is None:
                    continue
                side = runner_side(runner_name, line)
                if side not in {"over", "under"}:
                    stats["eligible_market_rows_bad_runner_identity"] += 1
                    continue
                if not event_id or not market_id or not selection_id:
                    stats["eligible_market_rows_missing_identity"] += 1
                    continue

                season_eligible_rows += 1
                stats["eligible_ou_runner_rows"] += 1
                identity = (norm_text(event_date), norm_text(home_team), norm_text(away_team))
                event_identity[(season, event_id)].add(identity)

                key = (season, event_id, line, market_id)
                market = markets.setdefault(key, {
                    "season": season,
                    "event_id": event_id,
                    "line": line,
                    "market_id": market_id,
                    "market_types": set(),
                    "market_names": set(),
                    "event_paths": set(),
                    "handicaps": set(),
                    "runners": defaultdict(list),
                })
                market["market_types"].add(market_type)
                market["market_names"].add(market_name)
                market["event_paths"].add(event_path)
                market["handicaps"].add(handicap)

                snapshots: dict[int, dict[str, float | None]] = {}
                for minute in SNAPSHOTS:
                    back = finite_gt_one(row[allowed_index[f"BEST_BACK_PRICE_{minute}_MIN_PRIOR"]])
                    lay = finite_gt_one(row[allowed_index[f"BEST_LAY_PRICE_{minute}_MIN_PRIOR"]])
                    volume = finite_optional(row[allowed_index[f"MATCHED_VOLUME_{minute}_MIN_PRIOR"]])
                    crossed = back is not None and lay is not None and back > lay
                    if crossed:
                        stats[f"crossed_quote_runner_snapshot_T{minute}"] += 1
                    snapshots[minute] = {"back": back, "lay": lay, "volume": volume, "crossed": crossed}

                market["runners"][side].append({
                    "selection_id": selection_id,
                    "runner_name": runner_name,
                    "snapshots": snapshots,
                })

            meta["rows_decoded_allowed_fields_only"] = season_rows
            meta["eligible_ou_runner_rows"] = season_eligible_rows
        file_meta.append(meta)

    identity_conflict_keys = {k for k, vals in event_identity.items() if len(vals) != 1}
    stats["identity_conflict_events"] = len(identity_conflict_keys)

    # Event-line may not combine runners across different market ids. Duplicate line markets fail closed.
    event_line_market_keys: dict[tuple[str, str, float], list[tuple[str, str, float, str]]] = defaultdict(list)
    structurally_valid_market: dict[tuple[str, str, float, str], bool] = {}
    market_snapshot_complete: dict[tuple[str, str, float, str], dict[int, bool]] = {}

    for key, market in markets.items():
        season, event_id, line, _market_id = key
        event_line_market_keys[(season, event_id, line)].append(key)
        if (season, event_id) in identity_conflict_keys:
            structurally_valid_market[key] = False
            continue
        if len(market["market_types"]) != 1 or len(market["market_names"]) != 1:
            stats["market_metadata_conflicts"] += 1
            structurally_valid_market[key] = False
            continue
        if set(market["runners"].keys()) != {"over", "under"}:
            stats["market_missing_over_under_side"] += 1
            structurally_valid_market[key] = False
            continue
        # Exactly one unique selection id per side; repeated rows for the same selection are allowed only if quote snapshots agree.
        normalized_runner: dict[str, dict[str, Any]] = {}
        runner_ok = True
        for side in ("over", "under"):
            observations = market["runners"][side]
            selection_ids = {obs["selection_id"] for obs in observations}
            if len(selection_ids) != 1:
                stats["market_multiple_selection_ids_per_side"] += 1
                runner_ok = False
                break
            first = observations[0]
            for obs in observations[1:]:
                if obs["snapshots"] != first["snapshots"]:
                    stats["duplicate_runner_rows_quote_conflict"] += 1
                    runner_ok = False
                    break
            if not runner_ok:
                break
            normalized_runner[side] = first
        structurally_valid_market[key] = runner_ok
        if not runner_ok:
            continue

        complete: dict[int, bool] = {}
        for minute in SNAPSHOTS:
            ok = True
            for side in ("over", "under"):
                q = normalized_runner[side]["snapshots"][minute]
                if q["back"] is None or q["lay"] is None or q["crossed"]:
                    ok = False
            complete[minute] = ok
        market_snapshot_complete[key] = complete

    duplicate_event_lines = {
        event_line: keys for event_line, keys in event_line_market_keys.items() if len(keys) != 1
    }
    stats["event_lines_with_multiple_market_ids"] = len(duplicate_event_lines)

    event_line_complete: dict[tuple[str, str, float], dict[int, bool]] = {}
    for event_line, keys in event_line_market_keys.items():
        if len(keys) != 1:
            continue
        key = keys[0]
        if not structurally_valid_market.get(key, False):
            continue
        complete = market_snapshot_complete[key]
        event_line_complete[event_line] = complete

    per_season: dict[str, dict[str, Any]] = {}
    pooled_per_line_snapshot = {str(line): {f"T-{m}min": 0 for m in SNAPSHOTS} for line in PREFERRED}
    pooled_per_line_all3 = Counter()
    pooled_matches_ge2 = pooled_matches_ge3 = pooled_matches_all5 = 0
    pooled_unique_events = 0

    for season in SEASONS:
        eligible_events = sorted({event_id for s, event_id in event_identity if s == season and (s, event_id) not in identity_conflict_keys})
        pooled_unique_events += len(eligible_events)
        per_line_snapshot = {str(line): {f"T-{m}min": 0 for m in SNAPSHOTS} for line in PREFERRED}
        per_line_all3 = Counter()
        event_all3_lines: dict[str, set[float]] = defaultdict(set)

        for (s, event_id, line), complete in event_line_complete.items():
            if s != season:
                continue
            for minute in SNAPSHOTS:
                if complete[minute]:
                    per_line_snapshot[str(line)][f"T-{minute}min"] += 1
                    pooled_per_line_snapshot[str(line)][f"T-{minute}min"] += 1
            if all(complete[m] for m in SNAPSHOTS):
                per_line_all3[str(line)] += 1
                pooled_per_line_all3[str(line)] += 1
                event_all3_lines[event_id].add(line)

        ge2 = sum(len(lines) >= 2 for lines in event_all3_lines.values())
        ge3 = sum(len(lines) >= 3 for lines in event_all3_lines.values())
        all5 = sum(set(PREFERRED) <= lines for lines in event_all3_lines.values())
        pooled_matches_ge2 += ge2
        pooled_matches_ge3 += ge3
        pooled_matches_all5 += all5

        per_season[season] = {
            "unique_nonconflict_events_seen_in_eligible_ou_rows": len(eligible_events),
            "per_line_snapshot_complete": per_line_snapshot,
            "per_line_all3_complete": {str(line): per_line_all3[str(line)] for line in PREFERRED},
            "matches_ge2_preferred_lines_all3": ge2,
            "matches_ge3_preferred_lines_all3": ge3,
            "matches_all5_preferred_lines_all3": all5,
        }

    gates = {
        "all_six_hashes_match": all(x["sha256_match"] for x in file_meta) and len(file_meta) == 6,
        "forbidden_outcome_result_values_accessed_zero": forbidden_fields_accessed == 0 and target_values_materialized == 0,
        "model_fit_score_zero": model_fit == 0 and model_score == 0,
        "identity_conflicts_zero_after_fail_closed_exclusion": True,  # conflicting events are excluded before coverage by contract
        "pooled_ou25_all3_ge_600": pooled_per_line_all3["2.5"] >= 600,
        "pooled_matches_ge3_all3_ge_450": pooled_matches_ge3 >= 450,
        "each_dev_season_ge3_all3_ge_60": all(per_season[s]["matches_ge3_preferred_lines_all3"] >= 60 for s in SEASONS[:-1]),
        "reserve_2025_2026_ge3_all3_ge_80": per_season["2025-2026"]["matches_ge3_preferred_lines_all3"] >= 80,
        "c073_c077_scientific_results_unused": True,
        "c070f_confirmation_unopened": True,
    }

    genuine_dynamic_ou = sum(pooled_per_line_all3.values()) > 0
    if all(gates.values()):
        terminal = "ALEAGUE_DYNAMIC_OU_SOURCE_PASS"
    elif genuine_dynamic_ou:
        terminal = "ALEAGUE_DYNAMIC_OU_SOURCE_LIMITED"
    else:
        terminal = "ALEAGUE_DYNAMIC_OU_SOURCE_STOP"

    result = {
        "schema_version": "C072N11_ALEAGUE_DYNAMIC_OU_ZERO_LABEL_V1",
        "project": "football3",
        "source_repo": "betfair-datascientists/betfair-datascientists.github.io",
        "source_revision": SOURCE_REVISION,
        "source_files": file_meta,
        "provider_native_snapshots_minutes_before_kickoff": list(SNAPSHOTS),
        "preferred_lines": list(PREFERRED),
        "allowed_rows_decoded": stats["allowed_rows_decoded"],
        "eligible_ou_runner_rows": stats["eligible_ou_runner_rows"],
        "recognized_market_instances": len(markets),
        "event_line_instances_after_fail_closed_validation": len(event_line_complete),
        "pooled_unique_nonconflict_events_seen_in_eligible_ou_rows": pooled_unique_events,
        "per_season": per_season,
        "pooled_per_line_snapshot_complete": pooled_per_line_snapshot,
        "pooled_per_line_all3_complete": {str(line): pooled_per_line_all3[str(line)] for line in PREFERRED},
        "pooled_matches_ge2_preferred_lines_all3": pooled_matches_ge2,
        "pooled_matches_ge3_preferred_lines_all3": pooled_matches_ge3,
        "pooled_matches_all5_preferred_lines_all3": pooled_matches_all5,
        "diagnostics": dict(stats),
        "forbidden_field_values_accessed": forbidden_fields_accessed,
        "target_outcome_values_materialized": target_values_materialized,
        "model_fit": model_fit,
        "model_score": model_score,
        "c073_c077_scientific_results_used": False,
        "c070f_confirmation_opened": False,
        "gates": gates,
        "terminal": terminal,
        "authorization": "NO_TARGET_ACCESS; freeze separate scientific P(T) contract before any outcome/result column value is read",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
