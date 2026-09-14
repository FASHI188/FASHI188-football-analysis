#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import pathlib
import shutil
import sqlite3
import tempfile
import time
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any

KAGGLE_PAGE = "https://www.kaggle.com/datasets/codytipton/player-stats-per-game-understat"
KAGGLE_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/codytipton/player-stats-per-game-understat"
PROVIDER = "Cody Tipton player stats per game - Understat"
LICENSE = "MIT (Kaggle dataset page declaration)"
LICENSE_VERIFIED_AT = "2026-09-15"
EXPECTED_TEST_ARTIFACT_ID = 9827606506
EXPECTED_TEST_FIXTURE_SET_SHA256 = "0f156c22df1976b929d0db24135830e8b3fac8546130e7bf5e08673918f22bc6"
EXPECTED_TEST_N = 1752
EXPECTED_LEAGUE_COUNTS = {
    "Bundesliga": 306,
    "EPL": 380,
    "La liga": 380,
    "Ligue 1": 306,
    "Serie A": 380,
}
TARGET_SEASON = 2024
QUERY_COLUMNS = (
    "id", "date", "league", "season", "team_h", "team_a", "h_id", "a_id",
    "h_deep", "a_deep", "h_ppda", "a_ppda",
)
FORBIDDEN_QUERY_COLUMNS = {
    "h_goals", "a_goals", "h_xg", "a_xg", "result", "isresult", "winner",
}
UA = {
    "User-Agent": "Mozilla/5.0 Football3NovaN1Research/1.0",
    "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.1",
}


class FeatureFreezeError(RuntimeError):
    pass


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def columns(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return {str(r[1]).casefold(): str(r[1]) for r in con.execute(f"pragma table_info({q(table)})")}


def find_game_table(con: sqlite3.Connection) -> tuple[str, dict[str, str]]:
    required = {x.casefold() for x in QUERY_COLUMNS}
    found: list[tuple[str, dict[str, str]]] = []
    for (table,) in con.execute("select name from sqlite_master where type='table' order by name"):
        cols = columns(con, str(table))
        if required <= set(cols):
            found.append((str(table), cols))
    if len(found) != 1:
        raise FeatureFreezeError(f"expected exactly one game table with locked feature schema; found={[x[0] for x in found]}")
    return found[0]


def fixture_set_sha(ids: list[str]) -> str:
    return sha_bytes(canon(sorted(ids)))


def _finite_nonnegative(name: str, value: Any) -> float:
    x = float(value)
    if not math.isfinite(x) or x < 0:
        raise FeatureFreezeError(f"invalid {name}: {value!r}")
    return x


def _finite_positive(name: str, value: Any) -> float:
    x = float(value)
    if not math.isfinite(x) or x <= 0:
        raise FeatureFreezeError(f"invalid {name}: {value!r}")
    return x


def extract_feature_rows(
    db: pathlib.Path,
    *,
    expected_n: int | None = EXPECTED_TEST_N,
    expected_counts: dict[str, int] | None = EXPECTED_LEAGUE_COUNTS,
    expected_fixture_sha: str | None = EXPECTED_TEST_FIXTURE_SET_SHA256,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if {x.casefold() for x in QUERY_COLUMNS} & {x.casefold() for x in FORBIDDEN_QUERY_COLUMNS}:
        raise FeatureFreezeError("locked query column contract includes forbidden label/result column")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        table, cols = find_game_table(con)
        actual_cols = [cols[x.casefold()] for x in QUERY_COLUMNS]
        marks = ",".join("?" for _ in EXPECTED_LEAGUE_COUNTS)
        sql = (
            "select " + ",".join(q(x) for x in actual_cols) + " from " + q(table)
            + f" where {q(cols['season'])}=? and {q(cols['league'])} in ({marks})"
            + f" order by {q(cols['date'])} asc, {q(cols['id'])} asc"
        )
        params = [TARGET_SEASON] + list(EXPECTED_LEAGUE_COUNTS)
        source_rows = list(con.execute(sql, params))
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in source_rows:
        x = dict(zip(QUERY_COLUMNS, raw))
        fid = str(x["id"])
        if not fid or fid in seen:
            raise FeatureFreezeError(f"empty/duplicate fixture id: {fid!r}")
        seen.add(fid)
        league = str(x["league"])
        if league not in EXPECTED_LEAGUE_COUNTS:
            raise FeatureFreezeError(f"unexpected league: {league}")
        row = {
            "fixture_id": fid,
            "source_kickoff": str(x["date"]),
            "league": league,
            "season_key": int(x["season"]),
            "home_team_id": str(x["h_id"]),
            "away_team_id": str(x["a_id"]),
            "home_team": str(x["team_h"]),
            "away_team": str(x["team_a"]),
            "h_deep": _finite_nonnegative("h_deep", x["h_deep"]),
            "a_deep": _finite_nonnegative("a_deep", x["a_deep"]),
            "h_ppda": _finite_positive("h_ppda", x["h_ppda"]),
            "a_ppda": _finite_positive("a_ppda", x["a_ppda"]),
        }
        if not row["home_team_id"] or not row["away_team_id"] or row["home_team_id"] == row["away_team_id"]:
            raise FeatureFreezeError(f"invalid team identity fixture={fid}")
        rows.append(row)

    ids = [x["fixture_id"] for x in rows]
    counts = dict(sorted(Counter(x["league"] for x in rows).items()))
    got_sha = fixture_set_sha(ids)
    if expected_n is not None and len(rows) != expected_n:
        raise FeatureFreezeError(f"target count mismatch: {len(rows)} != {expected_n}")
    if expected_counts is not None and counts != dict(sorted(expected_counts.items())):
        raise FeatureFreezeError(f"league counts mismatch: {counts} != {dict(sorted(expected_counts.items()))}")
    if expected_fixture_sha is not None and got_sha != expected_fixture_sha:
        raise FeatureFreezeError(f"fixture identity set mismatch: {got_sha} != {expected_fixture_sha}")
    audit = {
        "game_table": table,
        "query_columns": list(QUERY_COLUMNS),
        "forbidden_columns_read": [],
        "labels_read": 0,
        "score_or_result_columns_read": False,
        "xg_columns_read": False,
        "n": len(rows),
        "league_counts": counts,
        "fixture_identity_set_sha256": got_sha,
    }
    return rows, audit


def download_public_dataset() -> tuple[bytes, dict[str, Any]]:
    events = []
    last: Exception | None = None
    for attempt in range(1, 3):
        started = time.monotonic()
        retrieved_at = now()
        try:
            req = urllib.request.Request(KAGGLE_DOWNLOAD, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read()
                status = getattr(r, "status", None)
            if len(raw) < 1000:
                raise FeatureFreezeError(f"public dataset response too small: {len(raw)}")
            events.append({
                "attempt": attempt, "retrieved_at": retrieved_at, "status": "OK",
                "http_status": status, "archive_bytes": len(raw), "archive_sha256": sha_bytes(raw),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
            return raw, {"attempts": events, "credentials_used": False, "secret_used": False}
        except Exception as exc:
            last = exc
            events.append({
                "attempt": attempt, "retrieved_at": retrieved_at, "status": "ERROR",
                "error_type": type(exc).__name__, "error": str(exc)[:300],
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
            if attempt < 2:
                time.sleep(2)
    raise FeatureFreezeError(f"public Kaggle download failed without credentials: {type(last).__name__}: {last}")


def materialize_database(raw: bytes, temp_root: pathlib.Path) -> tuple[pathlib.Path, dict[str, Any]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as exc:
        raise FeatureFreezeError("public dataset response is not a ZIP archive") from exc
    bad = z.testzip()
    if bad:
        raise FeatureFreezeError(f"dataset ZIP CRC failed: {bad}")
    members = [x for x in z.namelist() if not x.endswith("/")]
    dbs = [x for x in members if pathlib.Path(x).suffix.casefold() in {".db", ".sqlite", ".sqlite3"}]
    ranked = sorted(dbs, key=lambda x: (0 if pathlib.Path(x).name.casefold() == "understat.db" else 1, len(x), x))
    if not ranked or (len(ranked) > 1 and pathlib.Path(ranked[0]).name.casefold() != "understat.db"):
        raise FeatureFreezeError(f"ambiguous/missing Understat SQLite member: {ranked}")
    db = temp_root / "understat_ephemeral.db"
    with z.open(ranked[0]) as src, db.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    return db, {
        "zip_crc": "PASS", "database_member": ranked[0],
        "database_sha256": sha_file(db), "database_bytes": db.stat().st_size,
    }


def write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(canon(row).decode("utf-8") + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()
    execution_head = os.environ.get("NOVA_N1_EXECUTION_HEAD", "")
    if len(execution_head) != 40 or any(c not in "0123456789abcdef" for c in execution_head):
        raise FeatureFreezeError("exact execution head missing")
    args.out.mkdir(parents=True, exist_ok=True)

    raw, fetch_audit = download_public_dataset()
    archive_sha = sha_bytes(raw)
    archive_bytes = len(raw)
    with tempfile.TemporaryDirectory(prefix="football3_nova_n1_feature_") as td:
        db, db_meta = materialize_database(raw, pathlib.Path(td))
        rows, row_audit = extract_feature_rows(db)

    feature_path = args.out / "feature_updates_2024.jsonl"
    write_jsonl(feature_path, rows)
    receipt = {
        "schema_version": "football3-nova-n1-2024-feature-source-freeze-v1",
        "status": "FEATURE_SOURCE_FROZEN",
        "execution_head": execution_head,
        "provider": PROVIDER,
        "source_page": KAGGLE_PAGE,
        "download_url": KAGGLE_DOWNLOAD,
        "license": LICENSE,
        "license_verified_at": LICENSE_VERIFIED_AT,
        "historical_completed_only": True,
        "requires_secret_or_api_key": False,
        "credentials_used": False,
        "target_season_key": TARGET_SEASON,
        "locked_independent_test_artifact_id": EXPECTED_TEST_ARTIFACT_ID,
        "locked_fixture_identity_set_sha256": EXPECTED_TEST_FIXTURE_SET_SHA256,
        "archive_sha256": archive_sha,
        "archive_bytes": archive_bytes,
        "fetch_audit": fetch_audit,
        **db_meta,
        **row_audit,
        "feature_rows_sha256": sha_file(feature_path),
        "raw_archive_persisted": False,
        "raw_database_persisted": False,
        "feature_file_contains_goals_or_xg": False,
        "test_result_vault_opened": False,
        "test_labels_read": False,
        "promotion_authorized": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "frozen_at": now(),
    }
    receipt_path = args.out / "feature_source_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "football3-nova-n1-2024-feature-source-artifact-v1",
        "execution_head": execution_head,
        "payload": {
            "feature_updates_2024.jsonl": {"sha256": sha_file(feature_path), "bytes": feature_path.stat().st_size},
            "feature_source_receipt.json": {"sha256": sha_file(receipt_path), "bytes": receipt_path.stat().st_size},
        },
        "labels_read": 0,
        "test_result_vault_opened": False,
    }
    manifest_path = args.out / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"], "execution_head": execution_head, "n": receipt["n"],
        "fixture_identity_set_sha256": receipt["fixture_identity_set_sha256"],
        "feature_rows_sha256": receipt["feature_rows_sha256"], "archive_sha256": archive_sha,
        "labels_read": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
