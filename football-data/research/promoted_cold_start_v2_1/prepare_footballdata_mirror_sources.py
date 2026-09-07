#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA = "football3-promoted-cold-start-pre-metric-transport-mirror-transition-v1"
SEASON_CODE = {
    "2021/22": "2122",
    "2022/23": "2223",
    "2023/24": "2324",
    "2024/25": "2425",
    "2025/26": "2526",
}
OUTPUT_CODE = {
    "ENG_PremierLeague": "E1",
    "ESP_LaLiga": "SP2",
    "GER_Bundesliga": "D2",
    "ITA_SerieA": "I2",
    "FRA_Ligue1": "F2",
}
OUTPUT_FIELDS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(f"blob {len(data)}\0".encode("ascii"))
    h.update(data)
    return h.hexdigest()


def validate_and_strip(
    raw: bytes,
    *,
    source_name: str,
    expected_div: str,
) -> tuple[list[dict[str, str]], int, int]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise RuntimeError(f"{source_name}: missing CSV header")
    missing = [f for f in OUTPUT_FIELDS if f not in reader.fieldnames]
    if missing:
        raise RuntimeError(f"{source_name}: missing required fields {missing}")

    rows: list[dict[str, str]] = []
    teams: set[str] = set()
    directed_pairs: set[tuple[str, str]] = set()

    for lineno, row in enumerate(reader, 2):
        vals = {k: (row.get(k) or "").strip() for k in OUTPUT_FIELDS}
        if any(not vals[k] for k in OUTPUT_FIELDS):
            raise RuntimeError(
                f"{source_name}:{lineno}: incomplete required result fields "
                f"{ {k: vals[k] for k in OUTPUT_FIELDS} }"
            )
        home = vals["HomeTeam"]
        away = vals["AwayTeam"]
        if home == away:
            raise RuntimeError(f"{source_name}:{lineno}: identical home/away team {home!r}")
        pair = (home, away)
        if pair in directed_pairs:
            raise RuntimeError(f"{source_name}:{lineno}: duplicate directed fixture {pair}")
        directed_pairs.add(pair)
        teams.add(home)
        teams.add(away)

        if "Div" in reader.fieldnames:
            div = (row.get("Div") or "").strip()
            if div != expected_div:
                raise RuntimeError(
                    f"{source_name}:{lineno}: Div={div!r} != expected {expected_div!r}"
                )

        try:
            hg = int(vals["FTHG"])
            ag = int(vals["FTAG"])
        except ValueError as exc:
            raise RuntimeError(
                f"{source_name}:{lineno}: non-integer full-time score "
                f"{vals['FTHG']!r}-{vals['FTAG']!r}"
            ) from exc
        if hg < 0 or ag < 0:
            raise RuntimeError(f"{source_name}:{lineno}: negative full-time score")

        rows.append(
            {
                "Date": vals["Date"],
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": str(hg),
                "FTAG": str(ag),
            }
        )

    if not rows:
        raise RuntimeError(f"{source_name}: no completed result rows")
    team_count = len(teams)
    expected_rows = team_count * (team_count - 1)
    if len(rows) != expected_rows:
        raise RuntimeError(
            f"{source_name}: completed rows {len(rows)} != inferred double-round-robin "
            f"count {expected_rows} for {team_count} teams"
        )
    if len(directed_pairs) != expected_rows:
        raise RuntimeError(
            f"{source_name}: directed-pair coverage {len(directed_pairs)} != {expected_rows}"
        )
    return rows, team_count, expected_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    receipt_path = Path(args.receipt)
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    if receipt.get("schema_version") != EXPECTED_SCHEMA:
        raise RuntimeError("unexpected transport-mirror transition schema")
    if receipt.get("status") != "FROZEN_BEFORE_CANDIDATE_EVALUATION":
        raise RuntimeError("transport-mirror transition is not frozen")
    if receipt.get("candidate_metric_observations_before_transition") != 0:
        raise RuntimeError("transport mirror was not frozen pre-metric")
    if receipt.get("candidate_evaluator_completed_before_transition") is not False:
        raise RuntimeError("candidate evaluator completed before transport transition")

    mirror = receipt.get("mirror") or {}
    if mirror.get("file_count") != 25:
        raise RuntimeError("expected exactly 25 frozen mirror files")
    repository = str(mirror.get("repository") or "")
    commit = str(mirror.get("commit") or "")
    if repository != "IBalazs433/football-analytics-dashboard":
        raise RuntimeError(f"unexpected mirror repository {repository!r}")
    if len(commit) != 40:
        raise RuntimeError("mirror commit is not a full SHA")

    raw_root = Path(args.raw_root)
    out_root = Path(args.out_root)
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[tuple[str, str]] = set()
    files_out: list[dict[str, Any]] = []
    total_rows = 0

    for f in mirror["files"]:
        comp = str(f["competition_id"])
        season = str(f["season"])
        if comp not in OUTPUT_CODE:
            raise RuntimeError(f"unexpected competition {comp}")
        if season not in SEASON_CODE:
            raise RuntimeError(f"unexpected season {season}")
        key = (comp, season)
        if key in seen:
            raise RuntimeError(f"duplicate competition-season {key}")
        seen.add(key)

        expected_div = OUTPUT_CODE[comp]
        raw_path = raw_root / comp / f"{SEASON_CODE[season]}-{expected_div}.csv"
        if not raw_path.is_file():
            raise RuntimeError(f"missing frozen mirror source {raw_path}")
        raw = raw_path.read_bytes()

        expected_bytes = int(f["bytes"])
        expected_blob = str(f["git_blob_sha"])
        if len(raw) != expected_bytes:
            raise RuntimeError(
                f"{raw_path}: bytes {len(raw)} != frozen {expected_bytes}"
            )
        actual_blob = git_blob_sha(raw)
        if actual_blob != expected_blob:
            raise RuntimeError(
                f"{raw_path}: git blob {actual_blob} != frozen {expected_blob}"
            )

        source_path = str(f["path"])
        rows, team_count, expected_rows = validate_and_strip(
            raw,
            source_name=f"{repository}@{commit}:{source_path}",
            expected_div=expected_div,
        )

        dst = out_root / SEASON_CODE[season] / f"{expected_div}.csv"
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("w", encoding="utf-8", newline="") as out:
            w = csv.DictWriter(out, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        converted = dst.read_bytes()

        total_rows += len(rows)
        files_out.append(
            {
                "competition_id": comp,
                "season": season,
                "source_provider": "Football-Data.co.uk",
                "transport_mirror_repository": repository,
                "transport_mirror_commit": commit,
                "source_path": source_path,
                "source_url": (
                    f"https://raw.githubusercontent.com/{repository}/{commit}/{source_path}"
                ),
                "git_blob_sha": actual_blob,
                "raw_bytes": len(raw),
                "raw_sha256": sha256_bytes(raw),
                "parsed_matches": len(rows),
                "team_count": team_count,
                "expected_double_round_robin_matches": expected_rows,
                "converted_path": str(dst),
                "converted_bytes": len(converted),
                "converted_sha256": sha256_bytes(converted),
                "converted_fields": OUTPUT_FIELDS,
                "odds_fields_used": False,
                "postmatch_stats_used": False,
                "xg_fields_used": False,
            }
        )

    expected = {(c, s) for c in OUTPUT_CODE for s in SEASON_CODE}
    if seen != expected:
        raise RuntimeError(
            f"frozen mirror coverage mismatch missing={sorted(expected-seen)} "
            f"extra={sorted(seen-expected)}"
        )

    payload = {
        "schema_version": "football3-footballdata-transport-mirror-source-manifest-v1",
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256_bytes(receipt_bytes),
        "mirror_repository": repository,
        "mirror_commit": commit,
        "file_count": len(files_out),
        "competition_count": len(OUTPUT_CODE),
        "season_count": len(SEASON_CODE),
        "total_parsed_matches": total_rows,
        "adapter_contract": (
            "verify frozen Git blob+bytes before parse; validate complete double-round-robin "
            "result coverage; strip to Date/HomeTeam/AwayTeam/FTHG/FTAG only; no aliases, "
            "fuzzy identity, odds, postmatch statistics or xG inference"
        ),
        "files": sorted(files_out, key=lambda x: (x["competition_id"], x["season"])),
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "files": len(files_out),
                "total_parsed_matches": total_rows,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
