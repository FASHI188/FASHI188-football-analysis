from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
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
UA = {
    "User-Agent": "Mozilla/5.0 Football3HistoricalResearch/1.0",
    "Accept": "application/json,text/plain,*/*",
    "X-Requested-With": "XMLHttpRequest",
}
ASSETS = (
    "https://understat.com/js/league.min.js?t=1765269520",
    "https://understat.com/js/calendar.min.js?t=1765138215",
    "https://understat.com/js/main.min.js?t=1765138215",
)
FETCH_META: dict[str, dict] = {}


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


def fetch(url: str, *, min_bytes: int = 100) -> bytes:
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                transport = r.read()
                content_encoding = (r.headers.get("Content-Encoding") or "").strip().lower()
                content_type = (r.headers.get("Content-Type") or "").strip()
            gzip_encoded = content_encoding == "gzip" or transport[:2] == b"\x1f\x8b"
            logical = gzip.decompress(transport) if gzip_encoded else transport
            if len(logical) < min_bytes:
                raise RuntimeError(f"response too small after transport decode: {len(logical)}")
            FETCH_META[url] = {
                "transport_sha256": sha_bytes(transport),
                "transport_bytes": len(transport),
                "logical_sha256": sha_bytes(logical),
                "logical_bytes": len(logical),
                "content_encoding": content_encoding,
                "content_type": content_type,
                "gzip_decompressed": gzip_encoded,
            }
            return logical
        except Exception as exc:
            last = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"public Understat fetch failed without credentials: {type(last).__name__}: {last}")


def _extract_js_string(text: str, start: int) -> str:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] not in {"'", '"'}:
        raise RuntimeError("Understat JSON.parse opening quote not found")
    quote = text[start]
    i = start + 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == quote:
            backslashes = 0
            j = i - 1
            while j >= start and text[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:
                return "".join(out)
        out.append(ch)
        i += 1
    raise RuntimeError("Understat JSON.parse string is unterminated")


def probe_current_assets() -> None:
    print("UNDERSTAT_CLIENT_ROUTE_PROBE", file=sys.stderr)
    for url in ASSETS:
        try:
            raw = fetch(url, min_bytes=100)
        except Exception as exc:
            print(f"asset={url} fetch_error={type(exc).__name__}:{exc}", file=sys.stderr)
            continue
        text = raw.decode("utf-8", "replace")
        print(f"asset={url} bytes={len(raw)} sha256={sha_bytes(raw)}", file=sys.stderr)
        patterns = (
            r".{0,180}\$\.ajax.{0,500}",
            r".{0,180}\$\.get(?:JSON)?.{0,500}",
            r".{0,180}\$\.post.{0,500}",
            r".{0,180}XMLHttpRequest.{0,500}",
            r".{0,180}fetch\(.{0,500}",
            r".{0,180}(?:url|href)\s*[:=]\s*[^,;]{1,300}",
            r".{0,180}(?:calendar|dates|matches|league).{0,500}",
        )
        seen = set()
        emitted = 0
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.I | re.S):
                snippet = re.sub(r"\s+", " ", m.group(0)).strip()
                if snippet in seen:
                    continue
                seen.add(snippet)
                print("route_context=" + snippet[:700], file=sys.stderr)
                emitted += 1
                if emitted >= 14:
                    break
            if emitted >= 14:
                break


def parse_dates_data(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="strict")
    try:
        direct = json.loads(text)
    except json.JSONDecodeError:
        direct = None
    if isinstance(direct, dict):
        dates = direct.get("dates")
        if isinstance(dates, list):
            return dates
    if isinstance(direct, list):
        return direct
    marker = text.find("datesData")
    if marker < 0:
        probe_current_assets()
        raise RuntimeError("Understat response has neither league-data JSON nor datesData HTML marker")
    parse_at = text.find("JSON.parse", marker)
    if parse_at < 0:
        raise RuntimeError("Understat datesData JSON.parse marker not found")
    open_paren = text.find("(", parse_at + len("JSON.parse"))
    if open_paren < 0:
        raise RuntimeError("Understat datesData JSON.parse opening parenthesis not found")
    payload = _extract_js_string(text, open_paren + 1)
    try:
        decoded = bytes(payload, "utf-8").decode("unicode_escape")
        rows = json.loads(decoded)
    except Exception as exc:
        raise RuntimeError(f"Understat datesData JSON decode failed: {type(exc).__name__}: {exc}") from exc
    if isinstance(rows, dict) and isinstance(rows.get("dates"), list):
        rows = rows["dates"]
    if not isinstance(rows, list):
        raise RuntimeError("Understat datesData is not a list")
    return rows


def source_kickoff(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


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
        url = f"https://understat.com/getLeagueData/{slug}/{YEAR}"
        raw = fetch(url, min_bytes=5000)
        (raw_dir / f"{slug}_{YEAR}.json").write_bytes(raw)
        data = parse_dates_data(raw)
        result_rows = [x for x in data if bool(x.get("isResult"))]
        if len(result_rows) != expected:
            raise RuntimeError(f"{league} completed-result count mismatch: {len(result_rows)} != {expected}")
        counts[league] = len(result_rows)
        transport_meta = dict(FETCH_META[url])
        page_receipts.append({
            "league": league,
            "url": url,
            "request_mode": "understat_current_public_getLeagueData",
            "logical_source_sha256": sha_bytes(raw),
            "logical_source_bytes": len(raw),
            "result_n": len(result_rows),
            **transport_meta,
        })
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
            identities.append(ident)
            vault.append(label)

    identities.sort(key=lambda r: (r["kickoff"], r["competition_id"], r["fixture_id"]))
    label_map = {r["fixture_id"]: r for r in vault}
    vault = [label_map[r["fixture_id"]] for r in identities]
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
    receipt = {
        "schema_version": "football3-historical-xg-fusion-v2-source-freeze-v1",
        "status": "NEW_HISTORICAL_CONFIRMATION_SOURCE_FROZEN",
        "provider": "Understat public getLeagueData route",
        "season_key": YEAR,
        "historical_completed_only": True,
        "requires_secret_or_api_key": False,
        "expected_n": EXPECTED_N,
        "n": len(identities),
        "league_counts": counts,
        "source_pages": page_receipts,
        "source_page_set_sha256": sha_bytes(canon(sorted((x["url"], x["logical_source_sha256"]) for x in page_receipts))),
        "source_transport_set_sha256": sha_bytes(canon(sorted((x["url"], x["transport_sha256"]) for x in page_receipts))),
        "identity_sha256": sha_bytes(identity_path.read_bytes()),
        "vault_sha256": sha_bytes(vault_path.read_bytes()),
        "fixture_identity_set_sha256": sha_bytes(canon(sorted(ids))),
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
