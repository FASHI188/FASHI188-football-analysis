#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA = "football3-promoted-cold-start-pre-metric-transport-mirror-transition-v1"
EXCEPTION_SCHEMA = "football3-promoted-cold-start-pre-metric-nonstandard-fixture-adjudication-v1"
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


def load_nonstandard_exceptions(path: Path) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], bytes]:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema_version") != EXCEPTION_SCHEMA:
        raise RuntimeError("unexpected nonstandard-fixture adjudication schema")
    if data.get("status") != "FROZEN_BEFORE_CANDIDATE_EVALUATION":
        raise RuntimeError("nonstandard-fixture adjudication is not frozen")
    if data.get("candidate_metric_observations_before_transition") != 0:
        raise RuntimeError("nonstandard-fixture adjudication was not frozen pre-metric")
    if data.get("candidate_evaluator_completed_before_transition") is not False:
        raise RuntimeError("candidate evaluator completed before nonstandard-fixture adjudication")
    if data.get("scientific_parameter_or_gate_change") is not False:
        raise RuntimeError("nonstandard-fixture adjudication claims a scientific parameter/gate change")

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_pairs: set[tuple[str, str, str, str]] = set()
    for ex in data.get("exceptions", []):
        comp = str(ex.get("competition_id") or "")
        season = str(ex.get("season") or "")
        div = str(ex.get("division_code") or "")
        home = str(ex.get("home_team_name_in_source") or "").strip()
        away = str(ex.get("away_team_name_in_source") or "").strip()
        if comp not in OUTPUT_CODE or season not in SEASON_CODE:
            raise RuntimeError(f"unexpected nonstandard fixture scope {(comp, season)}")
        if div != OUTPUT_CODE[comp]:
            raise RuntimeError(f"nonstandard fixture division {div!r} != {OUTPUT_CODE[comp]!r}")
        if not home or not away or home == away:
            raise RuntimeError(f"invalid nonstandard fixture pair {(home, away)}")
        if ex.get("source_scored_row_expected") is not False:
            raise RuntimeError("nonstandard omission must state source_scored_row_expected=false")
        if ex.get("include_as_model_result_row") is not False:
            raise RuntimeError("nonstandard omission must not be included as a model result row")
        if ex.get("synthetic_score_allowed") is not False:
            raise RuntimeError("synthetic score must be explicitly prohibited")
        evidence = ex.get("authoritative_evidence") or {}
        if evidence.get("authority") != "Ligue de Football Professionnel (LFP)" or not evidence.get("url"):
            raise RuntimeError("nonstandard omission lacks frozen authoritative competition evidence")
        pair_key = (comp, season, home, away)
        if pair_key in seen_pairs:
            raise RuntimeError(f"duplicate nonstandard omission {pair_key}")
        seen_pairs.add(pair_key)
        by_key.setdefault((comp, season), []).append(ex)
    return by_key, raw


def validate_and_strip(
    raw: bytes,
    *,
    source_name: str,
    expected_div: str,
    documented_omissions: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], int, int, int]:
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

    omission_pairs: set[tuple[str, str]] = set()
    for ex in documented_omissions:
        pair = (
            str(ex["home_team_name_in_source"]).strip(),
            str(ex["away_team_name_in_source"]).strip(),
        )
        if pair in omission_pairs:
            raise RuntimeError(f"{source_name}: duplicate frozen omission pair {pair}")
        omission_pairs.add(pair)
        if pair in directed_pairs:
            raise RuntimeError(
                f"{source_name}: frozen nonstandard omission {pair} is present as a scored row"
            )
        if pair[0] not in teams or pair[1] not in teams:
            raise RuntimeError(
                f"{source_name}: frozen omission pair {pair} references team outside source season"
            )

    team_count = len(teams)
    expected_scheduled_rows = team_count * (team_count - 1)
    expected_scored_rows = expected_scheduled_rows - len(omission_pairs)
    if expected_scored_rows <= 0:
        raise RuntimeError(f"{source_name}: invalid expected scored row count {expected_scored_rows}")
    if len(rows) != expected_scored_rows:
        raise RuntimeError(
            f"{source_name}: completed rows {len(rows)} != expected scored-result count "
            f"{expected_scored_rows} (scheduled={expected_scheduled_rows}, "
            f"documented_nonstandard_omissions={len(omission_pairs)}) for {team_count} teams"
        )
    if len(directed_pairs) != expected_scored_rows:
        raise RuntimeError(
            f"{source_name}: directed scored-pair coverage {len(directed_pairs)} != {expected_scored_rows}"
        )
    return rows, team_count, expected_scheduled_rows, expected_scored_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--nonstandard-fixture-receipt", required=True)
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

    exception_path = Path(args.nonstandard_fixture_receipt)
    exceptions_by_key, exception_bytes = load_nonstandard_exceptions(exception_path)

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
    consumed_exception_keys: set[tuple[str, str]] = set()
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
        documented_omissions = exceptions_by_key.get(key, [])
        if documented_omissions:
            consumed_exception_keys.add(key)
        rows, team_count, expected_scheduled_rows, expected_scored_rows = validate_and_strip(
            raw,
            source_name=f"{repository}@{commit}:{source_path}",
            expected_div=expected_div,
            documented_omissions=documented_omissions,
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
                "expected_scheduled_directed_matches": expected_scheduled_rows,
                "documented_nonstandard_omission_count": len(documented_omissions),
                "documented_nonstandard_omissions": [
                    {
                        "home_team_name_in_source": ex["home_team_name_in_source"],
                        "away_team_name_in_source": ex["away_team_name_in_source"],
                        "scheduled_date": ex["scheduled_date"],
                        "classification": ex["classification"],
                        "include_as_model_result_row": ex["include_as_model_result_row"],
                        "synthetic_score_allowed": ex["synthetic_score_allowed"],
                    }
                    for ex in documented_omissions
                ],
                "expected_scored_matches": expected_scored_rows,
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
    if consumed_exception_keys != set(exceptions_by_key):
        raise RuntimeError(
            f"unconsumed frozen nonstandard exceptions: {sorted(set(exceptions_by_key)-consumed_exception_keys)}"
        )

    payload = {
        "schema_version": "football3-footballdata-transport-mirror-source-manifest-v2",
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256_bytes(receipt_bytes),
        "nonstandard_fixture_receipt_path": str(exception_path),
        "nonstandard_fixture_receipt_sha256": sha256_bytes(exception_bytes),
        "mirror_repository": repository,
        "mirror_commit": commit,
        "file_count": len(files_out),
        "competition_count": len(OUTPUT_CODE),
        "season_count": len(SEASON_CODE),
        "total_parsed_matches": total_rows,
        "total_documented_nonstandard_omissions": sum(
            x["documented_nonstandard_omission_count"] for x in files_out
        ),
        "adapter_contract": (
            "verify frozen Git blob+bytes before parse; validate all scored result rows; "
            "permit only pre-metric frozen authoritative nonstandard omissions from the "
            "scheduled directed-pair count without synthesizing a score; strip to "
            "Date/HomeTeam/AwayTeam/FTHG/FTAG only; no aliases, fuzzy identity, odds, "
            "postmatch statistics or xG inference"
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
                "documented_nonstandard_omissions": payload[
                    "total_documented_nonstandard_omissions"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
