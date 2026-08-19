#!/usr/bin/env python3
import datetime as dt
import gzip
import hashlib
import json
import os
import random
import time
from collections import Counter
from pathlib import Path

from curl_cffi import requests as curl_requests

TARGET_N = 5000
MIN_XG_SHOTS = 6
SEED_PREFIX = "C072N18A_HISTORY5000"
ELIGIBLE_YEARS = {"21/22", "22/23", "23/24", "24/25"}
TOURNAMENTS = [
    (17, "Premier League"),
    (8, "LaLiga"),
    (35, "Bundesliga"),
    (23, "Serie A"),
    (34, "Ligue 1"),
    (37, "Eredivisie"),
    (238, "Liga Portugal"),
]
BASE_URLS = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]
OUTDIR = Path(os.environ.get("N18A_OUTDIR", "football-data/research/_n18a_sofascore_history5000"))
USER_AGENT = "football3-n18a-zero-target-source-audit/1.1"
SESSION = curl_requests.Session()


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def request_json(path, attempts=5):
    last = None
    for attempt in range(attempts):
        for base in BASE_URLS:
            url = base + path
            try:
                resp = SESSION.get(
                    url,
                    impersonate="chrome",
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json,text/plain,*/*",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.sofascore.com/",
                        "Cache-Control": "no-cache",
                    },
                    timeout=25,
                    allow_redirects=True,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code} {url}")
                return resp.json(), url
            except Exception as exc:
                last = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 429 or "HTTP 429" in str(exc):
                    time.sleep(min(30, 2 ** attempt + random.random()))
                else:
                    time.sleep(min(5, 0.4 * (attempt + 1)))
        time.sleep(min(10, 1.2 * (attempt + 1)))
    raise RuntimeError(f"request failed path={path}: {last}")


def parse_xg(shot):
    for key in ("xg", "xG", "expectedGoals", "expectedGoal"):
        val = shot.get(key)
        if val is None:
            continue
        try:
            x = float(val)
        except (TypeError, ValueError):
            continue
        if 0.0 <= x <= 1.5:
            return x
    return None


def safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def filtered_shot(event_id, shot):
    xg = parse_xg(shot)
    if xg is None:
        return None
    coords = shot.get("playerCoordinates") or shot.get("player_coordinates") or {}
    if not isinstance(coords, dict):
        coords = {}
    is_home = shot.get("isHome")
    if not isinstance(is_home, bool):
        return None
    row = {
        "event_id": int(event_id),
        "is_home": is_home,
        "xg": xg,
        "situation": shot.get("situation"),
        "body_part": shot.get("bodyPart") or shot.get("body_part"),
        "shot_time": safe_int(shot.get("time")),
        "added_time": safe_int(shot.get("addedTime")),
        "x": coords.get("x"),
        "y": coords.get("y"),
    }
    # Intentionally do not persist shotType/result/goal flag/player name/score/winner.
    return row


def candidate_rank(event_id):
    s = f"{SEED_PREFIX}|{int(event_id)}".encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def get_seasons(tournament_id, expected_name, failures, source_urls):
    try:
        obj, url = request_json(f"/unique-tournament/{tournament_id}/seasons")
        source_urls.add(url.split("/unique-tournament/")[0])
    except Exception as exc:
        failures.append({"stage": "seasons", "tournament_id": tournament_id, "error": str(exc)})
        print(f"ACCESS_FAIL seasons tournament={tournament_id} error={exc}", flush=True)
        return []
    out = []
    observed_years = []
    for s in obj.get("seasons", []):
        year = str(s.get("year", ""))
        observed_years.append(year)
        if year in ELIGIBLE_YEARS:
            sid = safe_int(s.get("id"))
            if sid is not None:
                out.append({"id": sid, "year": year, "name": s.get("name") or expected_name})
    if not out:
        print(f"NO_ELIGIBLE_SEASON tournament={tournament_id} observed_years={observed_years[:12]}", flush=True)
    return out


def get_finished_events(tournament_id, tournament_name, season, failures, source_urls):
    rows = []
    page = 0
    while True:
        path = f"/unique-tournament/{tournament_id}/season/{season['id']}/events/last/{page}"
        try:
            obj, url = request_json(path)
            source_urls.add(url.split("/unique-tournament/")[0])
        except Exception as exc:
            failures.append({
                "stage": "events",
                "tournament_id": tournament_id,
                "season_id": season["id"],
                "page": page,
                "error": str(exc),
            })
            print(f"ACCESS_FAIL events tournament={tournament_id} season={season['year']} page={page} error={exc}", flush=True)
            break
        events = obj.get("events") or []
        for ev in events:
            status = (ev.get("status") or {}).get("type")
            if status != "finished":
                continue
            eid = safe_int(ev.get("id"))
            ts = safe_int(ev.get("startTimestamp"))
            home = ev.get("homeTeam") or {}
            away = ev.get("awayTeam") or {}
            hid = safe_int(home.get("id"))
            aid = safe_int(away.get("id"))
            if None in (eid, ts, hid, aid):
                continue
            # Whitelist identity/time fields only. Do NOT read score/winner/result fields.
            rows.append({
                "event_id": eid,
                "start_timestamp": ts,
                "date_utc": dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date().isoformat(),
                "home_team_id": hid,
                "home_team": home.get("name"),
                "away_team_id": aid,
                "away_team": away.get("name"),
                "tournament_id": tournament_id,
                "tournament": tournament_name,
                "season_id": season["id"],
                "season_year": season["year"],
                "season_name": season["name"],
            })
        if not obj.get("hasNextPage"):
            break
        page += 1
        if page > 100:
            failures.append({"stage": "events", "tournament_id": tournament_id, "season_id": season["id"], "error": "page_guard"})
            break
        time.sleep(0.08)
    return rows


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_technical_receipt(started, failures, inventory_count, status):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    receipt = {
        "project": "football3",
        "experiment": "C072-N18A",
        "status": status,
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "inventory_identities": inventory_count,
        "request_failures": len(failures),
        "failure_examples": failures[:50],
        "selected_matches": 0,
        "persisted_outcome_fields": 0,
        "model_fits": 0,
        "target_scores": 0,
    }
    (OUTDIR / "sofascore_n18a_technical_receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def main():
    started = utc_now()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    failures = []
    source_urls = set()
    inventory = []

    print(f"N18A START {started} target={TARGET_N} transport=curl_cffi_chrome", flush=True)
    for tid, tname in TOURNAMENTS:
        seasons = get_seasons(tid, tname, failures, source_urls)
        if not seasons:
            print(f"WARN no eligible seasons tournament={tid} {tname}", flush=True)
        for season in seasons:
            evs = get_finished_events(tid, tname, season, failures, source_urls)
            inventory.extend(evs)
            print(f"INVENTORY tournament={tname} season={season['year']} events={len(evs)} total={len(inventory)}", flush=True)

    by_id = {}
    for row in inventory:
        by_id[row["event_id"]] = row
    candidates = list(by_id.values())
    for row in candidates:
        row["selection_hash"] = candidate_rank(row["event_id"])
    candidates.sort(key=lambda r: (r["selection_hash"], r["event_id"]))

    if len(candidates) < TARGET_N:
        status = "TECHNICAL_ACCESS_FAILURE_PRE_IDENTITY" if not candidates and failures else "STOP_COVERAGE"
        write_technical_receipt(started, failures, len(candidates), status)
        raise SystemExit(f"{status} candidate identities {len(candidates)} < {TARGET_N}")

    selected_matches = []
    selected_shots = []
    skipped_no_shotmap = 0
    skipped_xg_gate = 0
    idx = 0

    for idx, row in enumerate(candidates, 1):
        eid = row["event_id"]
        try:
            obj, url = request_json(f"/event/{eid}/shotmap")
            source_urls.add(url.split("/event/")[0])
        except Exception as exc:
            failures.append({"stage": "shotmap", "event_id": eid, "error": str(exc)})
            skipped_no_shotmap += 1
            continue
        raw_shots = obj.get("shotmap") or []
        kept = []
        for shot in raw_shots:
            if isinstance(shot, dict):
                x = filtered_shot(eid, shot)
                if x is not None:
                    kept.append(x)
        home_n = sum(1 for x in kept if x["is_home"])
        away_n = sum(1 for x in kept if not x["is_home"])
        if len(kept) < MIN_XG_SHOTS or home_n < 1 or away_n < 1:
            skipped_xg_gate += 1
            continue

        m = dict(row)
        m["numeric_xg_shots"] = len(kept)
        m["home_numeric_xg_shots"] = home_n
        m["away_numeric_xg_shots"] = away_n
        selected_matches.append(m)
        selected_shots.extend(kept)

        n = len(selected_matches)
        if n % 100 == 0 or n == TARGET_N:
            print(f"SELECTED {n}/{TARGET_N} scanned={idx}/{len(candidates)} shots={len(selected_shots)} failures={len(failures)}", flush=True)
        if n >= TARGET_N:
            break
        time.sleep(0.10)

    status = "PASS_HISTORY5000" if len(selected_matches) == TARGET_N else "STOP_COVERAGE"

    matches_path = OUTDIR / "sofascore_n18a_history5000_matches.jsonl.gz"
    shots_path = OUTDIR / "sofascore_n18a_history5000_shots.jsonl.gz"
    with gzip.open(matches_path, "wt", encoding="utf-8", compresslevel=6) as f:
        for row in selected_matches:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with gzip.open(shots_path, "wt", encoding="utf-8", compresslevel=6) as f:
        for row in selected_shots:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    competition_counts = Counter(m["tournament"] for m in selected_matches)
    season_counts = Counter(f"{m['tournament']}|{m['season_year']}" for m in selected_matches)
    dates = [m["date_utc"] for m in selected_matches]
    summary = {
        "project": "football3",
        "experiment": "C072-N18A",
        "status": status,
        "role": "HISTORY_FEATURE_SOURCE_ONLY_GLOBALLY_CONSUMED_AS_TARGET_IDENTITIES",
        "transport": "curl_cffi==0.15.0 impersonate=chrome",
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "target_matches": TARGET_N,
        "selected_matches": len(selected_matches),
        "selected_numeric_xg_shots": len(selected_shots),
        "candidate_identities": len(candidates),
        "scanned_candidates": idx,
        "skipped_no_shotmap": skipped_no_shotmap,
        "skipped_xg_gate": skipped_xg_gate,
        "request_failures": len(failures),
        "failure_examples": failures[:50],
        "competition_counts": dict(sorted(competition_counts.items())),
        "competition_season_counts": dict(sorted(season_counts.items())),
        "first_date_utc": min(dates) if dates else None,
        "last_date_utc": max(dates) if dates else None,
        "eligible_seasons": sorted(ELIGIBLE_YEARS),
        "selection_rule": "sha256(C072N18A_HISTORY5000|event_id), first 5000 with >=6 numeric xG shots and >=1 each side",
        "persisted_outcome_fields": 0,
        "model_fits": 0,
        "target_scores": 0,
        "source_base_urls_observed": sorted(source_urls),
        "matches_gz_sha256": sha256_file(matches_path),
        "shots_gz_sha256": sha256_file(shots_path),
        "matches_gz_bytes": matches_path.stat().st_size,
        "shots_gz_bytes": shots_path.stat().st_size,
    }
    summary_path = OUTDIR / "sofascore_n18a_history5000_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), flush=True)

    if status != "PASS_HISTORY5000":
        raise SystemExit(f"{status}: selected={len(selected_matches)} target={TARGET_N}")


if __name__ == "__main__":
    main()
