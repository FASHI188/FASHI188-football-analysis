from __future__ import annotations

import hashlib
import io
import json
import pathlib
import shutil
import sqlite3
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import historical_pit_replay as core
import understat_compat as compat

OLD_TIMEOUT_RUN_ID = 33485502884
OLD_TIMEOUT_HEAD = "f02f6780067bf076501bb173226c02795d68d8f0"
OLD_TIMEOUT_STATUS = "TECHNICAL_TIMEOUT_NO_SCORE"
SHARD_SIZE = 50
SHARD_N = 6
SOURCE_TIMEOUT_SECONDS = 15
SOURCE_ATTEMPTS = 2
KAGGLE_TIMEOUT_SECONDS = 30
MAX_SOURCE_WORKERS = 8

class ShardError(RuntimeError):
    pass

def now() -> str:
    return core.iso(datetime.now(timezone.utc))

def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def sha_file(path: pathlib.Path) -> str:
    return core.sha_file(path)

def file_meta(path: pathlib.Path) -> dict[str, Any]:
    return {"sha256": sha_file(path), "bytes": path.stat().st_size}

def exact_files(root: pathlib.Path, names: list[str]) -> dict[str, dict[str, Any]]:
    return {name: file_meta(root / name) for name in names}

def verify_file_set(root: pathlib.Path, files: dict[str, dict[str, Any]]) -> None:
    for name, meta in files.items():
        p = root / name
        if not p.is_file() or p.stat().st_size != int(meta["bytes"]) or sha_file(p) != str(meta["sha256"]):
            raise ShardError(f"manifest file integrity mismatch: {name}")

def shard_bounds(shard: int) -> tuple[int, int, str]:
    if shard < 0 or shard >= SHARD_N:
        raise ShardError(f"invalid shard {shard}")
    start = shard * SHARD_SIZE
    end = start + SHARD_SIZE
    return start, end, f"shard-{start:03d}-{end-1:03d}"

def bounded_fetch(url: str, *, timeout: int = SOURCE_TIMEOUT_SECONDS, attempts: int = SOURCE_ATTEMPTS) -> tuple[bytes | None, dict[str, Any]]:
    events = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        retrieved = now()
        try:
            req = urllib.request.Request(url, headers=core.UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                event = {
                    "url": url, "attempt": attempt, "retrieved_at": retrieved,
                    "status": "OK", "http_status": getattr(resp, "status", None),
                    "response_sha256": sha_bytes(raw), "response_bytes": len(raw),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
                events.append(event)
                return raw, {**event, "attempts": events}
        except Exception as exc:
            events.append({
                "url": url, "attempt": attempt, "retrieved_at": retrieved,
                "status": "ERROR", "error_type": type(exc).__name__,
                "error": str(exc)[:300], "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
    return None, {"url": url, "status": "SOURCE_UNAVAILABLE", "attempts": events}

def _db_tables(db: pathlib.Path) -> tuple[str, str]:
    con = sqlite3.connect(db)
    try:
        game, _ = compat._find_table(con, {"id", "date", "team_h", "team_a"})
        lineup, _ = compat._find_table(con, {"match_id", "player_id", "team_id", "position", "player", "time", "xg", "xa", "xgchain", "xgbuildup"})
        return game, lineup
    finally:
        con.close()

def freeze_understat(out: pathlib.Path) -> dict[str, Any]:
    raw, fetch_audit = bounded_fetch(compat.KAGGLE_DOWNLOAD, timeout=KAGGLE_TIMEOUT_SECONDS, attempts=SOURCE_ATTEMPTS)
    if raw is None or len(raw) < 1000:
        raise ShardError("frozen public Understat/Kaggle archive unavailable")
    z = zipfile.ZipFile(io.BytesIO(raw))
    bad = z.testzip()
    if bad:
        raise ShardError(f"Understat/Kaggle ZIP CRC failed: {bad}")
    members = [x for x in z.namelist() if not x.endswith("/")]
    dbs = [x for x in members if pathlib.Path(x).suffix.lower() in {".db", ".sqlite", ".sqlite3"}]
    ranked = sorted(dbs, key=lambda x: (0 if pathlib.Path(x).name.lower() == "understat.db" else 1, len(x), x))
    if not ranked or (len(ranked) > 1 and pathlib.Path(ranked[0]).name.lower() != "understat.db"):
        raise ShardError(f"ambiguous/missing Understat SQLite member: {ranked}")
    db = out / "understat_frozen.db"
    with z.open(ranked[0]) as src, db.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    game_table, lineup_table = _db_tables(db)
    receipt = {
        "schema_version": "football3-understat-base-freeze-v1",
        "provider": "Cody Tipton player stats per game - Understat",
        "source_page": compat.KAGGLE_PAGE, "download_url": compat.KAGGLE_DOWNLOAD,
        "retrieved_at": now(), "request_timeout_seconds": KAGGLE_TIMEOUT_SECONDS,
        "max_attempts": SOURCE_ATTEMPTS, "fetch_audit": fetch_audit,
        "archive_sha256": sha_bytes(raw), "archive_bytes": len(raw), "zip_crc": "PASS",
        "database_member": ranked[0], "database_sha256": sha_file(db), "database_bytes": db.stat().st_size,
        "game_table": game_table, "lineup_table": lineup_table,
        "labels_read": 0, "score_or_result_columns_read": False, "season_aggregate_player_rows_read": False,
    }
    core.dump(out / "understat_freeze_receipt.json", receipt)
    return receipt

def build_understat_identity(db: pathlib.Path, full_rows: list[dict[str, Any]], out: pathlib.Path, receipt: dict[str, Any]) -> dict[str, Any]:
    game_table = str(receipt["game_table"])
    con = sqlite3.connect(db)
    try:
        cols = compat._columns(con, game_table)
        safe = ["id", "date", "team_h", "team_a"] + [x for x in ("h_id", "a_id", "league", "season") if x in cols]
        sql = "select " + ",".join(compat._q(cols[x]) for x in safe) + " from " + compat._q(game_table)
        source_rows = [dict(zip(safe, row)) for row in con.execute(sql)]
    finally:
        con.close()
    idx: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for x in source_rows:
        date = str(x.get("date") or "")[:10]
        home = str(x.get("team_h") or "")
        away = str(x.get("team_a") or "")
        if date and home and away:
            idx[(date, compat._team_key(home), compat._team_key(away))].append({
                "understat_match_id": str(x["id"]), "date": date,
                "source_home": home, "source_away": away,
                "source_home_id": None if x.get("h_id") is None else str(x.get("h_id")),
                "source_away_id": None if x.get("a_id") is None else str(x.get("a_id")),
            })
    rows = []
    for r in full_rows:
        key = (str(r["cutoff"])[:10], compat._team_key(r["home_team"]), compat._team_key(r["away_team"]))
        cand = idx.get(key, [])
        if len(cand) != 1:
            raise ShardError(f"Understat frozen identity collision/miss {key}: {len(cand)}")
        rows.append({"fixture_id": str(r["fixture_id"]), "canonical_home": key[1], "canonical_away": key[2], **cand[0]})
    if len(rows) != core.FULL_SEASON_N or len({x["understat_match_id"] for x in rows}) != core.FULL_SEASON_N:
        raise ShardError("Understat frozen full-season mapping is not one-to-one 380")
    core.writejl(out / "understat_identity_rows.jsonl", rows)
    ident = {
        "schema_version": "football3-understat-frozen-identity-v1", "mapped_n": len(rows),
        "identity_rule": "date+frozen EPL cross-source team aliases+home/away direction",
        "team_aliases": compat.TEAM_KEYS, "result_fields_read": False,
        "rows_sha256": sha_file(out / "understat_identity_rows.jsonl"),
    }
    core.dump(out / "understat_identity_manifest.json", ident)
    return ident

def prepare(v2: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if (v2 / "dataset/evaluation_label_vault.jsonl").exists():
        raise ShardError("labels present during base freeze")
    _, cm, v2pred = core.select_cohort(v2, out)
    full_rows = [x for x in core.readjl(v2 / "dataset/evaluation_features.jsonl") if str(x["competition_id"]) == core.LEAGUE and str(x["season"]) == core.SEASON]
    full_rows.sort(key=lambda x: (core.dt(str(x["cutoff"])), str(x["fixture_id"])))
    receipt = freeze_understat(out)
    ident = build_understat_identity(out / "understat_frozen.db", full_rows, out, receipt)
    core.writejl(out / "protected_v2_prediction_subset.jsonl", [{"fixture_id": str(r["fixture_id"]), "v2_joint": v2pred[str(r["fixture_id"])]["v2_joint"]} for r in cm["rows"]])
    shutil.copy2(v2 / "locks/v2_lock.json", out / "v2_lock.json")
    core.dump(out / "technical_timeout_record.json", {
        "schema_version": "football3-technical-timeout-record-v1", "status": OLD_TIMEOUT_STATUS,
        "run_id": OLD_TIMEOUT_RUN_ID, "head": OLD_TIMEOUT_HEAD, "conclusion": "cancelled",
        "cancelled_step": "Predict historical PIT with labels absent", "scorer_invoked": False,
        "historical_labels_read": 0, "artifact_count": 0, "model_pass_fail_claim": None,
    })
    payload_names = ["cohort_manifest.json", "protected_v2_t15_equivalence.json", "understat_freeze_receipt.json", "understat_identity_rows.jsonl", "understat_identity_manifest.json", "protected_v2_prediction_subset.jsonl", "v2_lock.json", "technical_timeout_record.json", "understat_frozen.db"]
    manifest = {
        "schema_version": "football3-historical-pit-base-freeze-v1", "status": "HISTORICAL_PIT_REPLAY_SOURCE_BASE_FROZEN",
        "cohort_identity_sha256": cm["cohort_identity_sha256"], "n": core.COHORT_N, "full_season_n": core.FULL_SEASON_N,
        "selection_rule": cm["selection_rule"], "prediction_cutoff": cm["prediction_cutoff"],
        "old_timeout_status": OLD_TIMEOUT_STATUS, "labels_read": 0, "scorer_invoked": False,
        "understat_identity_mapped_n": ident["mapped_n"], "payload": exact_files(out, payload_names),
    }
    core.dump(out / "base_freeze_manifest.json", manifest)
    return manifest

def init_frozen_understat(base: pathlib.Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    receipt = json.load(open(base / "understat_freeze_receipt.json"))
    db = base / "understat_frozen.db"
    if sha_file(db) != receipt["database_sha256"]:
        raise ShardError("frozen Understat DB SHA mismatch")
    compat._DB_PATH = db; compat._ARCHIVE_SHA = str(receipt["archive_sha256"]); compat._ARCHIVE_BYTES = int(receipt["archive_bytes"])
    compat._GAME_TABLE = str(receipt["game_table"]); compat._LINEUP_TABLE = str(receipt["lineup_table"]); compat._GAME_SIDE = {}
    mapping, identity = {}, {}
    for x in core.readjl(base / "understat_identity_rows.jsonl"):
        fid = str(x["fixture_id"]); mid = str(x["understat_match_id"])
        mapping[fid] = mid; identity[fid] = x
        compat._GAME_SIDE[mid] = (str(x.get("source_home_id") or ""), str(x.get("source_away_id") or ""))
    core.resolve_source_name = compat.strict_resolve_source_name
    return mapping, identity
