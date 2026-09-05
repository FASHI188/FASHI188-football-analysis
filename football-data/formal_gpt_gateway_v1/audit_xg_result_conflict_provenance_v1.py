#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-xg-result-conflict-provenance-audit-v1"
TARGET_FIXTURE_ID = "8ac7540a70af27118955481e"


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def processed_row(repo_root: Path, fixture: rt.HistoryFixture) -> dict[str, Any]:
    path = repo_root / fixture.source_path
    aliases = rt._read_aliases(repo_root)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row_number, raw in enumerate(csv.DictReader(fh), start=2):
            season = str(raw.get("season") or "").strip()
            if season != fixture.season:
                continue
            kickoff = rt._parse_match_date(str(raw.get("Date") or raw.get("date") or ""), season)
            home = rt._canonical_team(fixture.competition_id, str(raw.get("HomeTeam") or raw.get("home_team") or ""), aliases)
            away = rt._canonical_team(fixture.competition_id, str(raw.get("AwayTeam") or raw.get("away_team") or ""), aliases)
            fid = rt._fixture_id(fixture.competition_id, season, kickoff, home, away)
            if fid == fixture.fixture_id:
                return {
                    "path": fixture.source_path,
                    "file_sha256": fixture.source_sha256,
                    "row_number": row_number,
                    "raw_row": raw,
                    "derived_identity": {
                        "fixture_id": fid,
                        "competition_id": fixture.competition_id,
                        "season": season,
                        "kickoff": kickoff.isoformat(),
                        "home_team_name": home,
                        "away_team_name": away,
                        "home_goals": fixture.home_goals,
                        "away_goals": fixture.away_goals,
                    },
                }
    raise rt.RuntimeGateError("target processed row not found")


def xg_source(repo_root: Path, fixture: rt.HistoryFixture, understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    key = rt._xg_join_key(fixture.competition_id, fixture.kickoff, fixture.home_team_name, fixture.away_team_name)

    con = sqlite3.connect(str(understat_db)); con.row_factory = sqlite3.Row
    try:
        old_rows = [dict(r) for r in con.execute(
            "select fid,date,league,season,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "and season in (2022,2023) order by date,fid"
        )]
    finally:
        con.close()
    for row in old_rows:
        dt = rt.datetime.fromisoformat(str(row["date"])).replace(tzinfo=rt.timezone.utc)
        comp = rt.BIG5[str(row["league"])]
        if rt._xg_join_key(comp, dt, str(row["team_h"]), str(row["team_a"])) == key:
            return {
                "source_family": "UNDERSTAT_FROZEN_DB",
                "source_id": f"understat:{int(row['fid'])}",
                "database_path": str(understat_db),
                "database_sha256": rt._sha_file(understat_db),
                "raw_row": row,
                "release_at": (dt + rt.timedelta(hours=3)).isoformat(),
                "freeze_receipt": read_json(understat_db.parent / "understat_freeze_receipt.json"),
                "identity_manifest": read_json(understat_db.parent / "understat_identity_manifest.json"),
                "join_key": list(key),
            }

    identity_path = confirmation_dir / "confirmation_identity.jsonl"
    vault_path = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    identities = [json.loads(x) for x in identity_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows = [json.loads(x) for x in vault_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault = {str(row["fixture_id"]): row for row in vault_rows}
    for row in identities:
        kickoff = rt._parse_dt(str(row["kickoff"]), "confirmation kickoff")
        comp = rt.BIG5[str(row["league"])]
        if rt._xg_join_key(comp, kickoff, str(row["home_team"]), str(row["away_team"])) != key:
            continue
        sid = str(row["fixture_id"])
        v = vault.get(sid)
        if v is None:
            raise rt.RuntimeGateError("confirmation identity/vault target mismatch")
        raw_page_hits: list[dict[str, Any]] = []
        for path in sorted((confirmation_dir / "raw_pages").glob("*.json")):
            try:
                page = read_json(path)
            except Exception:
                continue
            text = json.dumps(page, ensure_ascii=False, sort_keys=True)
            if sid in text or (str(row["home_team"]) in text and str(row["away_team"]) in text and str(row["kickoff"])[:10] in text):
                raw_page_hits.append({"path": str(path.relative_to(confirmation_dir)), "sha256": rt._sha_file(path)})
        return {
            "source_family": "CONFIRMATION_FROZEN_VAULT",
            "source_id": f"understat:{sid}",
            "identity_path": str(identity_path),
            "identity_sha256": rt._sha_file(identity_path),
            "identity_row": row,
            "vault_path": str(vault_path),
            "vault_sha256": rt._sha_file(vault_path),
            "vault_row": v,
            "source_freeze_receipt": read_json(confirmation_dir / "source_freeze_receipt.json"),
            "artifact_manifest": read_json(confirmation_dir / "artifact_manifest.json"),
            "raw_page_hits": raw_page_hits,
            "join_key": list(key),
        }
    raise rt.RuntimeGateError("target xG source row not found")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    understat_db = Path(args.understat_db)
    confirmation_dir = Path(args.confirmation_dir)
    history, v1_source = rt.load_frozen_v1_history(repo_root)
    matches = [row for row in history if row.fixture_id == TARGET_FIXTURE_ID]
    if len(matches) != 1:
        raise rt.RuntimeGateError(f"target fixture identity count {len(matches)} != 1")
    fixture = matches[0]
    processed = processed_row(repo_root, fixture)
    xg = xg_source(repo_root, fixture, understat_db, confirmation_dir)
    if xg["source_family"] == "UNDERSTAT_FROZEN_DB":
        xg_goals = [int(xg["raw_row"]["h_goals"]), int(xg["raw_row"]["a_goals"])]
    else:
        xg_goals = [int(xg["vault_row"]["home_goals"]), int(xg["vault_row"]["away_goals"])]
    processed_goals = [fixture.home_goals, fixture.away_goals]
    if processed_goals == xg_goals:
        raise rt.RuntimeGateError("target no longer reproduces XG/formal result conflict")

    core = {
        "schema_version": SCHEMA,
        "status": "CONFLICT_REPRODUCED_FAIL_CLOSED",
        "target_fixture_id": TARGET_FIXTURE_ID,
        "formal_head": rt.FORMAL_HEAD,
        "current_sha256": rt.CURRENT_SHA256,
        "processed_result": processed_goals,
        "xg_result": xg_goals,
        "processed": processed,
        "xg_source": xg,
        "v1_universe_provenance": v1_source,
        "conflict_gate_relaxed": False,
        "sample_deleted": False,
        "score_changed": False,
        "authoritative_adjudication": None,
        "resolution_status": "REQUIRES_AUTHORITATIVE_ADJUDICATION",
    }
    receipt = {**core, "receipt_sha256": sha(core)}
    Path(args.out).write_bytes(canon(receipt))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
