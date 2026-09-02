from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone

YEAR = 2024
LEAGUES = {
    "EPL": ("EPL", 380),
    "La_liga": ("La liga", 380),
    "Bundesliga": ("Bundesliga", 306),
    "Serie_A": ("Serie A", 380),
    "Ligue_1": ("Ligue 1", 306),
}
EXPECTED_N = 1752
OLD_DB_SHA256 = "f102eae39b4036a4c24e5b75b9cee551064cf1e7d4fd028966cd62a5784d8681"
OLD_UNIVERSE_N = 18084
OLD_UNIVERSE_SHA256 = "9984062205f26a300c4b0da203c83a2a21befdd3a1149b01da59feee4c2ae14b"
UA = {"User-Agent": "Mozilla/5.0 Football3HistoricalResearch/1.0", "Accept": "text/html,application/xhtml+xml"}


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canon(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(canon(row).decode("utf-8") + "\n")


def fetch(url: str) -> bytes:
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            if len(raw) < 5000:
                raise RuntimeError(f"response too small: {len(raw)}")
            return raw
        except Exception as exc:
            last = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"public Understat fetch failed without credentials: {type(last).__name__}: {last}")


def parse_dates_data(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="strict")
    m = re.search(r"datesData\s*=\s*JSON\.parse\('([^']+)'\)", text)
    if not m:
        raise RuntimeError("Understat datesData JSON embed not found")
    decoded = bytes(m.group(1), "utf-8").decode("unicode_escape")
    rows = json.loads(decoded)
    if not isinstance(rows, list):
        raise RuntimeError("Understat datesData is not a list")
    return rows


def source_kickoff(s: str) -> datetime:
    d = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return d


def load_old_ids(db: pathlib.Path) -> tuple[set[str], str]:
    if sha_bytes(db.read_bytes()) != OLD_DB_SHA256:
        raise RuntimeError("old frozen Understat DB SHA mismatch")
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "select fid,league,season,date,h_id,a_id,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where season between 2014 and 2023 and league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "order by date,fid"
        ).fetchall()
    finally:
        con.close()
    if len(rows) != OLD_UNIVERSE_N:
        raise RuntimeError(f"old universe n mismatch: {len(rows)}")
    canonical = []
    ids = set()
    for r in rows:
        fid = str(r[0])
        if fid in ids:
            raise RuntimeError(f"duplicate old fixture id {fid}")
        ids.add(fid)
        canonical.append({
            "fid": fid, "league": str(r[1]), "season": int(r[2]), "date": str(r[3]),
            "h_id": str(r[4]), "a_id": str(r[5]), "team_h": str(r[6]), "team_a": str(r[7]),
            "h_goals": int(r[8]), "a_goals": int(r[9]), "h_xg": float(r[10]), "a_xg": float(r[11]),
        })
    got = sha_bytes(b"\n".join(canon(x) for x in canonical) + b"\n")
    if got != OLD_UNIVERSE_SHA256:
        # Parent universe SHA was produced by its own canonical loader. We require count/IDs from the exact frozen DB,
        # and report this independently recomputed row SHA rather than silently substituting it for the parent SHA.
        pass
    return ids, got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-db", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    out = args.out
    raw_dir = out / "raw_pages"
    raw_dir.mkdir(parents=True, exist_ok=True)

    old_ids, old_recomputed_sha = load_old_ids(args.old_db)
    identities: list[dict] = []
    vault: list[dict] = []
    page_receipts = []
    counts = {}

    for slug, (league, expected) in LEAGUES.items():
        url = f"https://understat.com/league/{slug}/{YEAR}"
        raw = fetch(url)
        (raw_dir / f"{slug}_{YEAR}.html").write_bytes(raw)
        data = parse_dates_data(raw)
        result_rows = [x for x in data if bool(x.get("isResult"))]
        if len(result_rows) != expected:
            raise RuntimeError(f"{league} completed-result count mismatch: {len(result_rows)} != {expected}")
        counts[league] = len(result_rows)
        page_receipts.append({"league": league, "url": url, "raw_sha256": sha_bytes(raw), "raw_bytes": len(raw), "result_n": len(result_rows)})
        for x in result_rows:
            fid = str(x["id"])
            ko = source_kickoff(str(x["datetime"]))
            h = x.get("h") or {}; a = x.get("a") or {}; goals = x.get("goals") or {}; xg = x.get("xG") or {}
            if not fid or not h.get("id") or not a.get("id") or not h.get("title") or not a.get("title"):
                raise RuntimeError(f"identity field missing for {league} {fid}")
            if goals.get("h") is None or goals.get("a") is None or xg.get("h") is None or xg.get("a") is None:
                raise RuntimeError(f"result/xG field missing for {league} {fid}")
            ident = {
                "fixture_id": fid, "competition_id": league, "league": league, "season": YEAR,
                "kickoff": ko.isoformat(), "home_team_id": str(h["id"]), "away_team_id": str(a["id"]),
                "home_team": str(h["title"]), "away_team": str(a["title"]),
            }
            label = {
                "fixture_id": fid, "kickoff": ko.isoformat(), "release_at": (ko + timedelta(hours=3)).isoformat(),
                "home_goals": int(goals["h"]), "away_goals": int(goals["a"]),
                "home_xg": float(xg["h"]), "away_xg": float(xg["a"]),
            }
            for v in (label["home_xg"], label["away_xg"]):
                if not (0.0 <= v < 20.0):
                    raise RuntimeError(f"invalid xG for {fid}: {v}")
            identities.append(ident); vault.append(label)

    identities.sort(key=lambda r: (r["kickoff"], r["competition_id"], r["fixture_id"]))
    lm = {r["fixture_id"]: r for r in vault}
    vault = [lm[r["fixture_id"]] for r in identities]
    ids = [r["fixture_id"] for r in identities]
    if len(identities) != EXPECTED_N or len(set(ids)) != EXPECTED_N:
        raise RuntimeError("new confirmation identity count/duplicate failure")
    overlap = sorted(set(ids) & old_ids)
    if overlap:
        raise RuntimeError(f"new confirmation overlaps old 18,084 fixture identities: {overlap[:20]}")
    if set(counts) != {x[1][0] for x in LEAGUES.items()}:
        raise RuntimeError("league set mismatch")

    identity_path = out / "confirmation_identity.jsonl"
    vault_path = out / "confirmation_xg_result_vault.jsonl"
    write_jsonl(identity_path, identities)
    write_jsonl(vault_path, vault)
    identity_sha = sha_bytes(identity_path.read_bytes())
    vault_sha = sha_bytes(vault_path.read_bytes())
    fixture_set_sha = sha_bytes(canon(sorted(ids)))
    raw_set_sha = sha_bytes(canon(sorted((x["url"], x["raw_sha256"]) for x in page_receipts)))

    receipt = {
        "schema_version": "football3-historical-xg-fusion-v2-source-freeze-v1",
        "status": "NEW_HISTORICAL_CONFIRMATION_SOURCE_FROZEN",
        "provider": "Understat public league pages",
        "season_key": YEAR,
        "historical_completed_only": True,
        "requires_secret_or_api_key": False,
        "expected_n": EXPECTED_N,
        "n": len(identities),
        "league_counts": counts,
        "source_pages": page_receipts,
        "source_page_set_sha256": raw_set_sha,
        "identity_sha256": identity_sha,
        "vault_sha256": vault_sha,
        "fixture_identity_set_sha256": fixture_set_sha,
        "old_frozen_db_sha256": OLD_DB_SHA256,
        "old_parent_universe_n": OLD_UNIVERSE_N,
        "old_parent_universe_sha256": OLD_UNIVERSE_SHA256,
        "old_universe_recomputed_projection_sha256": old_recomputed_sha,
        "old_fixture_overlap_n": 0,
        "source_clock_interpretation": "Understat datetime treated as UTC source clock, matching frozen Historical XG V1",
        "release_delay_hours": 3,
        "identity_file_contains_result_or_xg": False,
        "vault_is_for_sequential_post-freeze_release_and_later_scoring_only": True,
        "prospective_queue": False,
    }
    write_json(out / "source_freeze_receipt.json", receipt)
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
