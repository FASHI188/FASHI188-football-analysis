#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

class PrecheckError(RuntimeError):
    pass

def require(ok: bool, msg: str) -> None:
    if not ok:
        raise PrecheckError(msg)

def canon(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def sha_bytes(v: bytes) -> str:
    return hashlib.sha256(v).hexdigest()

def sha(v: Any) -> str:
    return sha_bytes(canon(v))

def dt(v: str) -> datetime:
    x = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)

def iso(x: datetime) -> str:
    return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def load_routes(path: Path) -> dict[str, Any]:
    c = json.loads(path.read_text(encoding="utf-8"))
    require(c["status"] == "PRECHECK_SOURCE_ROUTES_NOT_ENABLED", "ROUTE_STATUS")
    a = c["activation"]
    require(a["enabled"] is False and a["replay_coverage_start_at"] is None, "PRECHECK_MUST_NOT_ENABLE_REPLAY")
    g = c["global_contract"]
    require(g["secret_required"] is False, "SECRET_ROUTE_FORBIDDEN")
    require(g["result_fields_read"] == 0, "RESULT_READ_FORBIDDEN")
    require(g["formal_v2_changed"] is False and g["current_changed"] is False and g["production_changed"] is False, "FORMAL_BOUNDARY")
    require(g["candidate_weight"] == 0 and g["matrix_delta"] == 0, "WEIGHT_BOUNDARY")
    require(set(g["competitions"]) == {"EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1", "J1", "K1"}, "COMPETITION_SET")
    return c

def http_get(url: str, *, timeout: int = 25, accept: str = "*/*") -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-nova-t60-source-precheck/1.0", "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        require(200 <= r.status < 300, f"HTTP_STATUS:{r.status}:{url}")
        return r.read(), {k.lower(): v for k, v in r.headers.items()}

def github_latest_path_commit(api_base: str, path: str) -> dict[str, str]:
    q = urllib.parse.urlencode({"path": path, "per_page": 1})
    raw, _ = http_get(f"{api_base}/commits?{q}", accept="application/vnd.github+json")
    xs = json.loads(raw.decode("utf-8"))
    require(isinstance(xs, list) and xs, f"NO_SOURCE_COMMIT:{path}")
    x = xs[0]
    return {"commit_sha": str(x["sha"]), "commit_at": str(x["commit"]["committer"]["date"])}

def assert_fresh(commit_at: str, observed_at: datetime, max_age_hours: int, source: str) -> None:
    age = (observed_at - dt(commit_at)).total_seconds() / 3600.0
    require(age >= -1.0, f"SOURCE_COMMIT_FROM_FUTURE:{source}:{age:.2f}")
    require(age <= float(max_age_hours), f"SOURCE_STALE:{source}:{age:.2f}h")

def stable_openfootball_id(comp: str, season: str, row: dict[str, Any]) -> str:
    ident = {"competition": comp, "season": season, "round": str(row.get("round", "")), "home": str(row["team1"]), "away": str(row["team2"])}
    return f"openfootball:{comp}:{sha(ident)[:20]}"

def parse_openfootball_payload(raw: bytes, *, competition: str, season: str, timezone_name: str, observed_at: datetime) -> list[dict[str, Any]]:
    data = json.loads(raw.decode("utf-8"))
    rows = data.get("matches")
    require(isinstance(rows, list), f"OPENFOOTBALL_MATCHES:{competition}")
    out = []
    tz = ZoneInfo(timezone_name)
    for row in rows:
        require(isinstance(row, dict), f"OPENFOOTBALL_ROW:{competition}")
        # Deliberately consume only allowlisted fixture fields. Score/result values are never accessed.
        d = str(row.get("date") or "").strip()
        t = str(row.get("time") or "00:00").strip()
        h = str(row.get("team1") or "").strip()
        a = str(row.get("team2") or "").strip()
        if not (d and h and a):
            continue
        local = datetime.fromisoformat(f"{d}T{t}:00" if len(t) == 5 else f"{d}T{t}").replace(tzinfo=tz)
        kickoff = local.astimezone(timezone.utc)
        if kickoff <= observed_at:
            continue
        out.append({
            "fixture_id": stable_openfootball_id(competition, season, row),
            "competition": competition,
            "season": season,
            "kickoff": iso(kickoff),
            "home_team": h,
            "away_team": a,
            "round": str(row.get("round") or ""),
        })
    return out

def stable_j1_id(row: dict[str, str]) -> str:
    ident = [str(row.get("year", "")), str(row.get("term", "")), str(row.get("homeTeamId", "")), str(row.get("awayTeamId", ""))]
    require(all(ident), "J1_IDENTITY_FIELD_MISSING")
    return "j1:" + ":".join(ident)

def parse_j1_csv(raw: bytes, *, observed_at: datetime) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig")
    r = csv.DictReader(io.StringIO(text))
    required = {"year", "category", "term", "kickoffdate", "homeTeam", "awayTeam", "homeTeamId", "awayTeamId", "competitionName"}
    require(r.fieldnames is not None and required.issubset(set(r.fieldnames)), "J1_SCHEMA")
    out = []
    for row in r:
        # No access to score, visitors or other: only fixture identity/schedule fields are consumed.
        if str(row.get("competitionName") or "").strip() != "J1" and str(row.get("category") or "").strip() != "1":
            continue
        k = dt(str(row.get("kickoffdate") or ""))
        if k <= observed_at:
            continue
        out.append({
            "fixture_id": stable_j1_id(row),
            "competition": "J1",
            "season": "2026/27",
            "kickoff": iso(k),
            "home_team": str(row.get("homeTeam") or "").strip(),
            "away_team": str(row.get("awayTeam") or "").strip(),
            "home_team_id": str(row.get("homeTeamId") or "").strip(),
            "away_team_id": str(row.get("awayTeamId") or "").strip(),
            "term": str(row.get("term") or "").strip(),
            "stadium": str(row.get("stadiumName") or "").strip(),
        })
    return out

def parse_k1_response(raw: bytes, *, expected_home_team_id: str, league_id: str, season: str, observed_at: datetime) -> list[dict[str, Any]]:
    data = json.loads(raw.decode("utf-8"))
    events = data.get("events")
    if events is None:
        return []
    require(isinstance(events, list), f"K1_EVENTS_SCHEMA:{expected_home_team_id}")
    out = []
    for event in events:
        require(isinstance(event, dict), "K1_EVENT_ROW")
        # Strict safe-field allowlist consumption; all score/result/status fields remain untouched.
        lid = str(event.get("idLeague") or "")
        hid = str(event.get("idHomeTeam") or "")
        if lid != str(league_id):
            continue
        require(hid == str(expected_home_team_id), f"K1_NOT_HOME_EVENT:{expected_home_team_id}:{hid}")
        eid = str(event.get("idEvent") or "").strip()
        aid = str(event.get("idAwayTeam") or "").strip()
        hname = str(event.get("strHomeTeam") or "").strip()
        aname = str(event.get("strAwayTeam") or "").strip()
        require(eid and aid and hname and aname, f"K1_IDENTITY_MISSING:{expected_home_team_id}")
        ts = str(event.get("strTimestamp") or "").strip()
        if ts:
            k = dt(ts)
        else:
            de = str(event.get("dateEvent") or "").strip()
            ti = str(event.get("strTime") or "00:00:00").strip() or "00:00:00"
            require(de, f"K1_KICKOFF_MISSING:{eid}")
            # TheSportsDB schedule timestamps are treated as UTC only when strTimestamp is unavailable.
            k = datetime.fromisoformat(f"{de}T{ti}").replace(tzinfo=timezone.utc)
        if k <= observed_at:
            continue
        out.append({
            "fixture_id": f"thesportsdb:{eid}",
            "source_event_id": eid,
            "competition": "K1",
            "season": season,
            "kickoff": iso(k),
            "home_team": hname,
            "away_team": aname,
            "home_team_id": hid,
            "away_team_id": aid,
            "venue": str(event.get("strVenue") or "").strip(),
        })
    return out

def require_unique(fixtures: list[dict[str, Any]], label: str) -> None:
    ids = [x["fixture_id"] for x in fixtures]
    require(len(ids) == len(set(ids)), f"DUPLICATE_FIXTURE_ID:{label}")

def precheck(routes_path: Path, out_dir: Path, observed_at_raw: str) -> dict[str, Any]:
    c = load_routes(routes_path)
    observed_at = dt(observed_at_raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_receipts: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []

    big5 = c["routes"]["big5_openfootball"]
    for spec in big5["competitions"]:
        path = spec["path"]
        url = f"{big5['raw_base_url']}/{path}"
        raw, _ = http_get(url, accept="application/json")
        commit = github_latest_path_commit(big5["github_api_base"], path)
        assert_fresh(commit["commit_at"], observed_at, int(big5["max_source_age_hours"]), spec["competition"])
        fixtures = parse_openfootball_payload(raw, competition=spec["competition"], season=big5["season"], timezone_name=spec["timezone"], observed_at=observed_at)
        require(fixtures, f"NO_FUTURE_FIXTURES:{spec['competition']}")
        require_unique(fixtures, spec["competition"])
        normalized.extend(fixtures)
        source_receipts.append({"source_id": big5["source_id"], "competition": spec["competition"], "path": path, "payload_sha256": sha_bytes(raw), "source_commit_sha": commit["commit_sha"], "source_commit_at": commit["commit_at"], "future_fixture_n": len(fixtures), "result_fields_read": 0})

    j1 = c["routes"]["j1_open_data"]
    raw, _ = http_get(j1["raw_url"], accept="text/csv")
    commit = github_latest_path_commit(j1["github_api_base"], j1["path"])
    assert_fresh(commit["commit_at"], observed_at, int(j1["max_source_age_hours"]), "J1")
    jfixtures = parse_j1_csv(raw, observed_at=observed_at)
    require(len(jfixtures) >= 10, f"J1_FUTURE_COVERAGE_TOO_SMALL:{len(jfixtures)}")
    require_unique(jfixtures, "J1")
    normalized.extend(jfixtures)
    source_receipts.append({"source_id": j1["source_id"], "competition": "J1", "path": j1["path"], "payload_sha256": sha_bytes(raw), "source_commit_sha": commit["commit_sha"], "source_commit_at": commit["commit_at"], "future_fixture_n": len(jfixtures), "result_fields_read": 0})

    k1 = c["routes"]["k1_thesportsdb"]
    kfixtures: list[dict[str, Any]] = []
    no_k1_home = []
    for i, team_id in enumerate(k1["home_team_ids"]):
        if i:
            time.sleep(0.05)
        url = f"{k1['base_url']}/" + k1["endpoint"].format(team_id=team_id)
        raw, _ = http_get(url, accept="application/json")
        xs = parse_k1_response(raw, expected_home_team_id=team_id, league_id=k1["league_id"], season=k1["season"], observed_at=observed_at)
        if not xs:
            no_k1_home.append(team_id)
        kfixtures.extend(xs)
    require(not no_k1_home, "K1_NEXT_HOME_NOT_EXPOSED:" + ",".join(no_k1_home))
    require(len(kfixtures) == len(k1["home_team_ids"]), f"K1_HOME_ROUTE_COUNT:{len(kfixtures)}")
    require_unique(kfixtures, "K1")
    normalized.extend(kfixtures)
    source_receipts.append({"source_id": k1["source_id"], "competition": "K1", "endpoint": k1["endpoint"], "team_poll_n": len(k1["home_team_ids"]), "future_fixture_n": len(kfixtures), "public_free_key": k1["public_free_key"], "secret_required": False, "result_fields_read": 0})

    require_unique(normalized, "ALL")
    per_comp: dict[str, int] = {}
    for x in normalized:
        per_comp[x["competition"]] = per_comp.get(x["competition"], 0) + 1
    require(set(per_comp) == set(c["global_contract"]["competitions"]), "SEVEN_COMPETITIONS_NOT_PRESENT")
    normalized_sorted = sorted(normalized, key=lambda x: (x["kickoff"], x["competition"], x["fixture_id"]))
    (out_dir / "future_fixture_projection.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in normalized_sorted), encoding="utf-8")
    receipt = {
        "schema_version": "football3-nova-t60-source-precheck-receipt-v1",
        "status": "T60_SOURCE_PRECHECK_PASS",
        "observed_at": iso(observed_at),
        "competition_count": 7,
        "competitions": c["global_contract"]["competitions"],
        "future_fixture_n": len(normalized_sorted),
        "future_fixture_n_by_competition": dict(sorted(per_comp.items())),
        "fixture_projection_sha256": sha(normalized_sorted),
        "source_receipts": source_receipts,
        "source_route_count": 3,
        "secret_required": False,
        "external_api_keys": {"TheSportsDB": "public_123"},
        "result_fields_read": 0,
        "replay_enabled": False,
        "replay_coverage_start_at": None,
        "coverage_guarantee_active": False,
        "retroactive_coverage_fabrication": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "allowed_next_phase": "DESIGN_LOCKED_ADAPTER_CANDIDATE"
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return receipt

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--observed-at", required=True)
    a = ap.parse_args()
    precheck(a.routes, a.out_dir, a.observed_at)

if __name__ == "__main__":
    main()
