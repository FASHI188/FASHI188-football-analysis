#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

TRANSPORT_SCHEMA = "football3-promoted-cold-start-pre-metric-transport-mirror-transition-v1"
EXCEPTION_SCHEMA_V1 = "football3-promoted-cold-start-pre-metric-nonstandard-fixture-adjudication-v1"
EXCEPTION_APPEND_SCHEMA_V1 = "football3-promoted-cold-start-pre-metric-nonstandard-fixture-adjudication-append-v1"
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


def _base_receipt_checks(data: dict[str, Any]) -> None:
    if data.get("status") != "FROZEN_BEFORE_CANDIDATE_EVALUATION":
        raise RuntimeError("nonstandard-fixture adjudication is not frozen")
    if data.get("candidate_metric_observations_before_transition") != 0:
        raise RuntimeError("nonstandard-fixture adjudication was not frozen pre-metric")
    if data.get("candidate_evaluator_completed_before_transition") is not False:
        raise RuntimeError("candidate evaluator completed before nonstandard-fixture adjudication")
    if data.get("scientific_parameter_or_gate_change") is not False:
        raise RuntimeError("nonstandard-fixture adjudication claims a scientific parameter/gate change")


def _normalize_exception(ex: dict[str, Any], schema: str) -> dict[str, Any]:
    comp = str(ex.get("competition_id") or "")
    season = str(ex.get("season") or "")
    div = str(ex.get("division_code") or "")
    date = str(ex.get("scheduled_date") or "").strip()
    home = str(ex.get("home_team_name_in_source") or "").strip()
    away = str(ex.get("away_team_name_in_source") or "").strip()
    if comp not in OUTPUT_CODE or season not in SEASON_CODE:
        raise RuntimeError(f"unexpected nonstandard fixture scope {(comp, season)}")
    if div != OUTPUT_CODE[comp]:
        raise RuntimeError(f"nonstandard fixture division {div!r} != {OUTPUT_CODE[comp]!r}")
    if not date or not home or not away or home == away:
        raise RuntimeError(f"invalid nonstandard fixture identity {(date, home, away)}")
    if ex.get("include_as_model_result_row") is not False:
        raise RuntimeError("nonstandard omission must not be included as a model result row")
    if ex.get("synthetic_score_allowed") is not False:
        raise RuntimeError("synthetic score must be explicitly prohibited")

    if schema == EXCEPTION_SCHEMA_V1:
        if ex.get("source_scored_row_expected") is not False:
            raise RuntimeError("v1 nonstandard omission must state source_scored_row_expected=false")
        evidence = ex.get("authoritative_evidence") or {}
        if evidence.get("authority") != "Ligue de Football Professionnel (LFP)" or not evidence.get("url"):
            raise RuntimeError("v1 nonstandard omission lacks frozen LFP evidence")
        evidence_urls = [str(evidence["url"])]
    elif schema == EXCEPTION_APPEND_SCHEMA_V1:
        if ex.get("administrative_score_as_real_goal_result_allowed") is not False:
            raise RuntimeError("append omission must prohibit administrative score as observed goals")
        evidence = ex.get("official_evidence") or []
        evidence_urls = [str(x.get("url") or "") for x in evidence if x.get("publisher") == "LFP"]
        if not evidence_urls or any(not u for u in evidence_urls):
            raise RuntimeError("append omission lacks frozen LFP evidence")
        semantics = ex.get("football_data_source_semantics") or {}
        if semantics.get("full_time_result_fields") != "FTHG/FTAG/FTR blank":
            raise RuntimeError("append omission does not bind Football-Data blank FT semantics")
        if semantics.get("partial_match_fields_must_not_be_promoted_to_full_time") is not True:
            raise RuntimeError("append omission does not prohibit partial-to-FT promotion")
    else:
        raise RuntimeError(f"unexpected nonstandard-fixture schema {schema!r}")

    return {
        "competition_id": comp,
        "season": season,
        "division_code": div,
        "scheduled_date": date,
        "home_team_name_in_source": home,
        "away_team_name_in_source": away,
        "classification": str(ex.get("classification") or ""),
        "include_as_model_result_row": False,
        "synthetic_score_allowed": False,
        "evidence_urls": evidence_urls,
        "receipt_schema": schema,
    }


def load_exception_receipts(paths: list[Path]) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], list[dict[str, Any]]]:
    if not paths:
        raise RuntimeError("at least one nonstandard-fixture receipt is required")
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    receipt_meta: list[dict[str, Any]] = []
    seen_fixture_keys: set[tuple[str, str, str, str, str]] = set()
    for path in paths:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        schema = str(data.get("schema_version") or "")
        if schema not in {EXCEPTION_SCHEMA_V1, EXCEPTION_APPEND_SCHEMA_V1}:
            raise RuntimeError(f"unexpected nonstandard-fixture adjudication schema {schema!r}")
        _base_receipt_checks(data)
        normalized: list[dict[str, Any]] = []
        for ex in data.get("exceptions", []):
            n = _normalize_exception(ex, schema)
            fk = (
                n["competition_id"], n["season"], n["scheduled_date"],
                n["home_team_name_in_source"], n["away_team_name_in_source"],
            )
            if fk in seen_fixture_keys:
                raise RuntimeError(f"duplicate nonstandard fixture across receipts {fk}")
            seen_fixture_keys.add(fk)
            by_key.setdefault((n["competition_id"], n["season"]), []).append(n)
            normalized.append(n)
        receipt_meta.append({
            "path": str(path),
            "schema_version": schema,
            "sha256": sha256_bytes(raw),
            "exception_count": len(normalized),
        })
    return by_key, receipt_meta


def validate_and_strip(
    raw: bytes,
    *,
    source_name: str,
    expected_div: str,
    documented_omissions: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], int, int, int, set[tuple[str, str, str]]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise RuntimeError(f"{source_name}: missing CSV header")
    missing = [f for f in OUTPUT_FIELDS if f not in reader.fieldnames]
    if missing:
        raise RuntimeError(f"{source_name}: missing required fields {missing}")

    omission_by_row = {
        (x["scheduled_date"], x["home_team_name_in_source"], x["away_team_name_in_source"]): x
        for x in documented_omissions
    }
    omission_pairs = {
        (x["home_team_name_in_source"], x["away_team_name_in_source"])
        for x in documented_omissions
    }
    if len(omission_by_row) != len(documented_omissions) or len(omission_pairs) != len(documented_omissions):
        raise RuntimeError(f"{source_name}: duplicate documented omission identity")

    rows: list[dict[str, str]] = []
    teams: set[str] = set()
    directed_pairs: set[tuple[str, str]] = set()
    observed_unscored: set[tuple[str, str, str]] = set()

    for lineno, row in enumerate(reader, 2):
        date = (row.get("Date") or "").strip()
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        fthg = (row.get("FTHG") or "").strip()
        ftag = (row.get("FTAG") or "").strip()
        if not date or not home or not away:
            raise RuntimeError(f"{source_name}:{lineno}: missing fixture identity fields")
        if home == away:
            raise RuntimeError(f"{source_name}:{lineno}: identical home/away team {home!r}")
        if "Div" in reader.fieldnames:
            div = (row.get("Div") or "").strip()
            if div != expected_div:
                raise RuntimeError(f"{source_name}:{lineno}: Div={div!r} != expected {expected_div!r}")

        teams.add(home)
        teams.add(away)
        row_key = (date, home, away)
        pair = (home, away)

        if not fthg or not ftag:
            if fthg or ftag:
                raise RuntimeError(f"{source_name}:{lineno}: partially populated FT score {fthg!r}-{ftag!r}")
            if row_key not in omission_by_row:
                raise RuntimeError(
                    f"{source_name}:{lineno}: incomplete FT result not covered by exact frozen exception "
                    f"{row_key}"
                )
            observed_unscored.add(row_key)
            continue

        if pair in omission_pairs:
            raise RuntimeError(f"{source_name}:{lineno}: frozen nonstandard omission {pair} appears as scored FT row")
        if pair in directed_pairs:
            raise RuntimeError(f"{source_name}:{lineno}: duplicate directed scored fixture {pair}")
        try:
            hg = int(fthg)
            ag = int(ftag)
        except ValueError as exc:
            raise RuntimeError(f"{source_name}:{lineno}: non-integer full-time score {fthg!r}-{ftag!r}") from exc
        if hg < 0 or ag < 0:
            raise RuntimeError(f"{source_name}:{lineno}: negative full-time score")
        directed_pairs.add(pair)
        rows.append({"Date": date, "HomeTeam": home, "AwayTeam": away, "FTHG": str(hg), "FTAG": str(ag)})

    if not rows:
        raise RuntimeError(f"{source_name}: no completed result rows")
    for ex in documented_omissions:
        if ex["home_team_name_in_source"] not in teams or ex["away_team_name_in_source"] not in teams:
            raise RuntimeError(f"{source_name}: frozen omission references team outside source season")

    team_count = len(teams)
    expected_scheduled_rows = team_count * (team_count - 1)
    expected_scored_rows = expected_scheduled_rows - len(omission_pairs)
    if len(rows) != expected_scored_rows:
        raise RuntimeError(
            f"{source_name}: completed rows {len(rows)} != expected scored-result count {expected_scored_rows} "
            f"(scheduled={expected_scheduled_rows}, documented_nonstandard_omissions={len(omission_pairs)}) "
            f"for {team_count} teams"
        )
    if len(directed_pairs) != expected_scored_rows:
        raise RuntimeError(f"{source_name}: directed scored-pair coverage {len(directed_pairs)} != {expected_scored_rows}")
    return rows, team_count, expected_scheduled_rows, expected_scored_rows, observed_unscored


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--nonstandard-fixture-receipt", action="append", required=True)
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    receipt_path = Path(args.receipt)
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    if receipt.get("schema_version") != TRANSPORT_SCHEMA:
        raise RuntimeError("unexpected transport-mirror transition schema")
    if receipt.get("status") != "FROZEN_BEFORE_CANDIDATE_EVALUATION":
        raise RuntimeError("transport-mirror transition is not frozen")
    if receipt.get("candidate_metric_observations_before_transition") != 0:
        raise RuntimeError("transport mirror was not frozen pre-metric")
    if receipt.get("candidate_evaluator_completed_before_transition") is not False:
        raise RuntimeError("candidate evaluator completed before transport transition")

    exception_paths = [Path(x) for x in args.nonstandard_fixture_receipt]
    exceptions_by_key, exception_receipts = load_exception_receipts(exception_paths)

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
        if comp not in OUTPUT_CODE or season not in SEASON_CODE:
            raise RuntimeError(f"unexpected competition-season {(comp, season)}")
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
            raise RuntimeError(f"{raw_path}: bytes {len(raw)} != frozen {expected_bytes}")
        actual_blob = git_blob_sha(raw)
        if actual_blob != expected_blob:
            raise RuntimeError(f"{raw_path}: git blob {actual_blob} != frozen {expected_blob}")

        source_path = str(f["path"])
        documented_omissions = exceptions_by_key.get(key, [])
        if documented_omissions:
            consumed_exception_keys.add(key)
        rows, team_count, scheduled_n, scored_n, observed_unscored = validate_and_strip(
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

        omissions_out = []
        for ex in documented_omissions:
            rk = (ex["scheduled_date"], ex["home_team_name_in_source"], ex["away_team_name_in_source"])
            omissions_out.append({
                "home_team_name_in_source": ex["home_team_name_in_source"],
                "away_team_name_in_source": ex["away_team_name_in_source"],
                "scheduled_date": ex["scheduled_date"],
                "classification": ex["classification"],
                "include_as_model_result_row": False,
                "synthetic_score_allowed": False,
                "source_unscored_row_observed": rk in observed_unscored,
                "receipt_schema": ex["receipt_schema"],
            })

        files_out.append({
            "competition_id": comp,
            "season": season,
            "source_provider": "Football-Data.co.uk",
            "transport_mirror_repository": repository,
            "transport_mirror_commit": commit,
            "source_path": source_path,
            "source_url": f"https://raw.githubusercontent.com/{repository}/{commit}/{source_path}",
            "git_blob_sha": actual_blob,
            "raw_bytes": len(raw),
            "raw_sha256": sha256_bytes(raw),
            "parsed_matches": len(rows),
            "team_count": team_count,
            "expected_scheduled_directed_matches": scheduled_n,
            "documented_nonstandard_omission_count": len(documented_omissions),
            "documented_nonstandard_omissions": omissions_out,
            "expected_scored_matches": scored_n,
            "converted_path": str(dst),
            "converted_bytes": len(converted),
            "converted_sha256": sha256_bytes(converted),
            "converted_fields": OUTPUT_FIELDS,
            "odds_fields_used": False,
            "postmatch_stats_used": False,
            "xg_fields_used": False,
        })

    expected = {(c, s) for c in OUTPUT_CODE for s in SEASON_CODE}
    if seen != expected:
        raise RuntimeError(f"frozen mirror coverage mismatch missing={sorted(expected-seen)} extra={sorted(seen-expected)}")
    if consumed_exception_keys != set(exceptions_by_key):
        raise RuntimeError(f"unconsumed frozen nonstandard exceptions: {sorted(set(exceptions_by_key)-consumed_exception_keys)}")

    payload = {
        "schema_version": "football3-footballdata-transport-mirror-source-manifest-v3",
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256_bytes(receipt_bytes),
        "nonstandard_fixture_receipts": exception_receipts,
        "mirror_repository": repository,
        "mirror_commit": commit,
        "file_count": len(files_out),
        "competition_count": len(OUTPUT_CODE),
        "season_count": len(SEASON_CODE),
        "total_parsed_matches": total_rows,
        "total_documented_nonstandard_omissions": sum(x["documented_nonstandard_omission_count"] for x in files_out),
        "adapter_contract": (
            "verify frozen Git blob+bytes before parse; reject every incomplete ordinary FT result; permit only exact pre-metric "
            "frozen authoritative nonstandard fixture omissions, whether the source row is absent or retained with blank FT fields; "
            "never synthesize or promote partial/admin scores; strip to Date/HomeTeam/AwayTeam/FTHG/FTAG only; no aliases, fuzzy "
            "identity, odds, postmatch statistics or xG inference"
        ),
        "files": sorted(files_out, key=lambda x: (x["competition_id"], x["season"])),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "files": len(files_out),
        "total_parsed_matches": total_rows,
        "documented_nonstandard_omissions": payload["total_documented_nonstandard_omissions"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
