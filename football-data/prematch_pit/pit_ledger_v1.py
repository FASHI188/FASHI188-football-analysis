#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
TSDB_LINEUP_EXAMPLE = "https://www.thesportsdb.com/api/v1/json/123/lookuplineup.php?id=1032723"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if x.tzinfo is None:
        x = x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def canonical_json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_obj(obj) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def starter_identity(row: dict) -> str:
    for key in ("player_id", "idPlayer", "player_name", "strPlayer"):
        v = row.get(key)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def normalize_side(value) -> str | None:
    s = str(value or "").strip().lower()
    if s in {"home", "h", "yes", "1", "true"}:
        return "home"
    if s in {"away", "a", "no", "0", "false"}:
        return "away"
    return None


def is_substitute(row: dict) -> bool:
    for key in ("is_substitute", "strSubstitute", "substitute"):
        if key not in row:
            continue
        v = str(row.get(key) or "").strip().lower()
        if v in {"yes", "true", "1"}:
            return True
        if v in {"no", "false", "0"}:
            return False
    return False


def evaluate_confirmed_xi(observation: dict) -> dict:
    required = ["source_name", "source_url", "observed_at_utc", "kickoff_at_utc", "home_team", "away_team", "lineup_rows"]
    missing = [x for x in required if x not in observation]
    if missing:
        return {"eligible": False, "verdict": "STOP_SCHEMA_MISSING_FIELDS", "missing": missing}

    try:
        observed = parse_utc(observation["observed_at_utc"])
        kickoff = parse_utc(observation["kickoff_at_utc"])
    except Exception as exc:
        return {"eligible": False, "verdict": "STOP_INVALID_TIMESTAMP", "error": str(exc)}
    if not observed < kickoff:
        return {"eligible": False, "verdict": "STOP_NOT_OBSERVED_PREMATCH"}

    starters = {"home": [], "away": []}
    for row in observation.get("lineup_rows") or []:
        if is_substitute(row):
            continue
        side = normalize_side(row.get("side", row.get("strHome")))
        pid = starter_identity(row)
        if side in starters and pid:
            starters[side].append(pid)

    unique_home = sorted(set(starters["home"]))
    unique_away = sorted(set(starters["away"]))
    duplicate_home = len(starters["home"]) != len(unique_home)
    duplicate_away = len(starters["away"]) != len(unique_away)

    if duplicate_home or duplicate_away:
        return {
            "eligible": False,
            "verdict": "STOP_DUPLICATE_STARTER_IDENTITY",
            "home_unique_starters": len(unique_home),
            "away_unique_starters": len(unique_away),
        }
    if len(unique_home) != 11 or len(unique_away) != 11:
        return {
            "eligible": False,
            "verdict": "STOP_XI_NOT_CONFIRMED",
            "home_unique_starters": len(unique_home),
            "away_unique_starters": len(unique_away),
        }

    return {
        "eligible": True,
        "verdict": "PASS_CONFIRMED_XI_PIT",
        "home_unique_starters": 11,
        "away_unique_starters": 11,
        "observed_before_kickoff_seconds": int((kickoff - observed).total_seconds()),
    }


def build_ledger_record(observation: dict) -> dict:
    gate = evaluate_confirmed_xi(observation)
    raw_hash = sha256_obj(observation)
    return {
        "schema_version": "football3-prematch-pit-ledger-v1",
        "record_id": raw_hash,
        "created_at_utc": utc_now(),
        "source_name": observation.get("source_name"),
        "source_url": observation.get("source_url"),
        "retrieved_at_utc": observation.get("retrieved_at_utc", observation.get("observed_at_utc")),
        "observed_at_utc": observation.get("observed_at_utc"),
        "kickoff_at_utc": observation.get("kickoff_at_utc"),
        "home_team": observation.get("home_team"),
        "away_team": observation.get("away_team"),
        "raw_observation_sha256": raw_hash,
        "gate": gate,
        "immutability": "APPEND_ONLY_NEVER_OVERWRITE_PRIOR_OBSERVATION",
    }


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-prematch-pit-ledger/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def probe_sources():
    OUT.mkdir(parents=True, exist_ok=True)
    retrieved = utc_now()
    payload = fetch_json(TSDB_LINEUP_EXAMPLE)
    rows = payload.get("lineup") or []
    starter_counts = {"home": 0, "away": 0, "unknown": 0}
    for row in rows:
        if is_substitute(row):
            continue
        side = normalize_side(row.get("strHome"))
        starter_counts[side if side in {"home", "away"} else "unknown"] += 1

    source_capabilities = {
        "schema_version": "football3-prematch-pit-source-capabilities-v1",
        "generated_at_utc": retrieved,
        "sources": {
            "THESPORTSDB_V1_FREE": {
                "status": "PARTIAL_LINEUP_ONLY_NOT_CONFIRMED_XI_ELIGIBLE",
                "registration_required": False,
                "paid_required": False,
                "api_key": "public development key 123",
                "official_api_endpoint_use_allowed": True,
                "website_scraping_allowed": False,
                "documented_free_rate_limit_per_minute": 30,
                "documented_lineup_free_return_limit": 5,
                "probe_url": TSDB_LINEUP_EXAMPLE,
                "probe_rows_returned": len(rows),
                "probe_non_sub_starter_counts": starter_counts,
                "rule": "Never upgrade a <=5-row free response into confirmed XI. Store only as source-capability evidence or partial observation.",
            },
            "PREMIER_LEAGUE_OFFICIAL_BROWSER_CAPTURE": {
                "status": "FULL_XI_POSSIBLE_MANUAL_BROWSER_CAPTURE_ONLY",
                "registration_required": False,
                "paid_required": False,
                "automated_scraping": False,
                "eligibility": "Only a browser-read observation captured before kickoff and passing the 11+11 identity gate is model-eligible.",
            },
            "BSD_API_V2": {
                "status": "POTENTIALLY_FULL_LINEUP_AND_AVAILABILITY_BUT_TOKEN_NOT_BOUND",
                "registration_required": True,
                "paid_required_for_football_base_tier": False,
                "model_eligible_now": False,
                "rule": "Do not use until a user-authorized token exists and a live PIT probe verifies fields/timing.",
            },
            "FOTMOB_AUTOMATED_CAPTURE": {
                "status": "BLOCKED_SOURCE_TERMS",
                "model_eligible_now": False,
            },
        },
    }
    (OUT / "source_capabilities_v1.json").write_text(json.dumps(source_capabilities, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return source_capabilities


def selftest():
    OUT.mkdir(parents=True, exist_ok=True)
    full_rows = []
    for side in ("home", "away"):
        for i in range(11):
            full_rows.append({"side": side, "player_id": f"{side}-{i}", "is_substitute": False})
    base = {
        "source_name": "SELFTEST",
        "source_url": "https://example.invalid/selftest",
        "observed_at_utc": "2026-08-27T10:00:00Z",
        "retrieved_at_utc": "2026-08-27T10:00:01Z",
        "kickoff_at_utc": "2026-08-27T11:00:00Z",
        "home_team": "HOME",
        "away_team": "AWAY",
        "lineup_rows": full_rows,
    }
    cases = {}
    cases["full_11x11_prematch"] = evaluate_confirmed_xi(base)
    partial = dict(base)
    partial["lineup_rows"] = full_rows[:5]
    cases["partial_5"] = evaluate_confirmed_xi(partial)
    late = dict(base)
    late["observed_at_utc"] = "2026-08-27T11:01:00Z"
    cases["postkickoff"] = evaluate_confirmed_xi(late)
    dup = dict(base)
    dup["lineup_rows"] = full_rows[:-1] + [dict(full_rows[-2])]
    cases["duplicate"] = evaluate_confirmed_xi(dup)

    assert cases["full_11x11_prematch"]["eligible"]
    assert cases["partial_5"]["verdict"] == "STOP_XI_NOT_CONFIRMED"
    assert cases["postkickoff"]["verdict"] == "STOP_NOT_OBSERVED_PREMATCH"
    assert cases["duplicate"]["verdict"] == "STOP_DUPLICATE_STARTER_IDENTITY"

    schema = {
        "schema_version": "football3-prematch-pit-observation-schema-v1",
        "required_fields": [
            "source_name", "source_url", "observed_at_utc", "kickoff_at_utc",
            "home_team", "away_team", "lineup_rows",
        ],
        "lineup_row_minimum_fields": ["side", "player_id_or_name", "is_substitute"],
        "confirmed_xi_gate": [
            "observed_at_utc < kickoff_at_utc",
            "exactly 11 unique non-substitute starters for home",
            "exactly 11 unique non-substitute starters for away",
            "source identity must be auditable",
            "append-only: later corrections are new observations, never overwrites",
        ],
        "optional_future_fields": [
            "formation", "manager", "injury_status", "suspension_status", "availability_reason",
            "source_publication_timestamp", "raw_payload_sha256",
        ],
    }
    (OUT / "pit_observation_schema_v1.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "selftest_v1.json").write_text(json.dumps({"status": "PASS", "cases": cases}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PIT_LEDGER_SELFTEST_PASS")


def verify():
    cap = json.loads((OUT / "source_capabilities_v1.json").read_text(encoding="utf-8"))
    test = json.loads((OUT / "selftest_v1.json").read_text(encoding="utf-8"))
    tsdb = cap["sources"]["THESPORTSDB_V1_FREE"]
    assert test["status"] == "PASS"
    assert tsdb["registration_required"] is False and tsdb["paid_required"] is False
    assert tsdb["documented_lineup_free_return_limit"] == 5
    assert tsdb["probe_rows_returned"] <= 5
    assert tsdb["status"] == "PARTIAL_LINEUP_ONLY_NOT_CONFIRMED_XI_ELIGIBLE"
    assert cap["sources"]["FOTMOB_AUTOMATED_CAPTURE"]["status"] == "BLOCKED_SOURCE_TERMS"
    print("PIT_LEDGER_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"probe", "selftest", "verify"}:
        raise SystemExit("usage: pit_ledger_v1.py {probe|selftest|verify}")
    if sys.argv[1] == "probe":
        x = probe_sources()
        print(json.dumps(x, indent=2, ensure_ascii=False))
    elif sys.argv[1] == "selftest":
        selftest()
    else:
        verify()


if __name__ == "__main__":
    main()
