#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
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
        out.append({"fixture_id": stable_openfootball_id(competition, season, row), "competition": competition, "season": season, "kickoff": iso(kickoff), "home_team": h, "away_team": a, "round": str(row.get("round") or "")})
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
        if str(row.get("competitionName") or "").strip() != "J1" and str(row.get("category") or "").strip() != "1":
            continue
        k = dt(str(row.get("kickoffdate") or ""))
        if k <= observed_at:
            continue
        out.append({"fixture_id": stable_j1_id(row), "competition": "J1", "season": "2026/27", "kickoff": iso(k), "home_team": str(row.get("homeTeam") or "").strip(), "away_team": str(row.get("awayTeam") or "").strip(), "home_team_id": str(row.get("homeTeamId") or "").strip(), "away_team_id": str(row.get("awayTeamId") or "").strip(), "term": str(row.get("term") or "").strip(), "stadium": str(row.get("stadiumName") or "").strip()})
    return out

def unfold_ics(raw: bytes) -> list[str]:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in text.split("\n"):
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out

def ics_unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()

def parse_ics_dt(key: str, value: str, default_timezone: str) -> datetime:
    params: dict[str, str] = {}
    parts = key.split(";")
    for p in parts[1:]:
        if "=" in p:
            a, b = p.split("=", 1); params[a.upper()] = b
    v = value.strip()
    if v.endswith("Z"):
        return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    require("T" in v, f"ICS_DATE_ONLY_NOT_ALLOWED:{v}")
    fmt = "%Y%m%dT%H%M%S" if len(v.split("T", 1)[1]) == 6 else "%Y%m%dT%H%M"
    tz = ZoneInfo(params.get("TZID", default_timezone))
    return datetime.strptime(v, fmt).replace(tzinfo=tz).astimezone(timezone.utc)

def parse_k1_ics(raw: bytes, *, season: str, default_timezone: str, observed_at: datetime, team_aliases: list[str]) -> list[dict[str, Any]]:
    lines = unfold_ics(raw)
    events: list[dict[str, tuple[str, str]]] = []
    cur: dict[str, tuple[str, str]] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            require(cur is None, "ICS_NESTED_VEVENT"); cur = {}
            continue
        if line == "END:VEVENT":
            require(cur is not None, "ICS_END_WITHOUT_BEGIN"); events.append(cur); cur = None
            continue
        if cur is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        base = key.split(";", 1)[0].upper()
        if base in {"UID", "DTSTART", "SUMMARY", "LOCATION", "LAST-MODIFIED", "SEQUENCE"}:
            cur[base] = (key, value)
    require(cur is None, "ICS_UNCLOSED_VEVENT")
    aliases = set(team_aliases)
    out: list[dict[str, Any]] = []
    seen_uid: set[str] = set()
    for event in events:
        require("UID" in event and "DTSTART" in event, "ICS_UID_OR_DTSTART_MISSING")
        uid = ics_unescape(event["UID"][1])
        require(uid, "ICS_EMPTY_UID")
        kickoff = parse_ics_dt(event["DTSTART"][0], event["DTSTART"][1], default_timezone)
        if kickoff <= observed_at:
            continue
        require(uid not in seen_uid, f"ICS_DUPLICATE_UID:{uid}"); seen_uid.add(uid)
        require("SUMMARY" in event, f"ICS_SUMMARY_MISSING:{uid}")
        summary = ics_unescape(event["SUMMARY"][1])
        if " - " in summary:
            home, away = [x.strip() for x in summary.split(" - ", 1)]
        elif " vs " in summary.lower():
            low = summary.lower(); i = low.index(" vs "); home, away = summary[:i].strip(), summary[i+4:].strip()
        else:
            raise PrecheckError(f"ICS_SUMMARY_FORMAT:{uid}:{summary}")
        require(home in aliases and away in aliases, f"ICS_TEAM_ALIAS_UNKNOWN:{uid}:{home}:{away}")
        location = ics_unescape(event.get("LOCATION", ("", ""))[1])
        sequence = ics_unescape(event.get("SEQUENCE", ("", ""))[1])
        last_modified = ics_unescape(event.get("LAST-MODIFIED", ("", ""))[1])
        out.append({"fixture_id": "fixtur.es:" + sha(uid)[:24], "source_uid": uid, "competition": "K1", "season": season, "kickoff": iso(kickoff), "home_team": home, "away_team": away, "venue": location, "sequence": sequence, "last_modified": last_modified})
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
        raw, _ = http_get(f"{big5['raw_base_url']}/{path}", accept="application/json")
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

    k1 = c["routes"]["k1_fixture_calendar"]
    raw, headers = http_get(k1["feed_url"], accept="text/calendar,*/*;q=0.8")
    kfixtures = parse_k1_ics(raw, season=k1["season"], default_timezone=k1["timezone"], observed_at=observed_at, team_aliases=k1["team_aliases"])
    require(len(kfixtures) >= 6, f"K1_FUTURE_COVERAGE_TOO_SMALL:{len(kfixtures)}")
    require_unique(kfixtures, "K1")
    covered = {x["home_team"] for x in kfixtures} | {x["away_team"] for x in kfixtures}
    expected = set(k1["team_aliases"])
    require(expected.issubset(covered), "K1_TEAM_COVERAGE_MISSING:" + ",".join(sorted(expected - covered)))
    normalized.extend(kfixtures)
    source_receipts.append({"source_id": k1["source_id"], "competition": "K1", "feed_url": k1["feed_url"], "usage_basis": k1["usage_basis"], "redistribution_allowed": False, "payload_sha256": sha_bytes(raw), "http_etag": headers.get("etag"), "http_last_modified": headers.get("last-modified"), "future_fixture_n": len(kfixtures), "covered_team_n": len(expected & covered), "secret_required": False, "result_fields_read": 0})

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
        "external_api_keys": {},
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
