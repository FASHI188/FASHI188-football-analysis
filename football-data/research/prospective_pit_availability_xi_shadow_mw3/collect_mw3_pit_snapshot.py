#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEFAULT_CONTRACT = HERE / "MW3_PROSPECTIVE_SHADOW_CONTRACT.json"
DEFAULT_FIXTURES = HERE / "ENG_PL_2026_27_MW3_FIXTURE_FREEZE.json"
DEFAULT_OUT = HERE / "artifacts" / "mw3_snapshot"

FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
PL_INJURIES = "https://www.premierleague.com/en/latest-player-injuries"
PL_SUSPENSIONS = (
    "https://www.premierleague.com/en/news/4425344/"
    "which-players-are-suspended-or-close-to-a-ban-in-fantasy"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
TERMINAL_PASS = "MW3_PROSPECTIVE_PIT_SNAPSHOT_LOCKED_WAITING_FOR_FUTURE_XI_LABELS"
TERMINAL_STOP = "MW3_PROSPECTIVE_PIT_SNAPSHOT_STOPPED_NO_TARGET_SCORING"


class SnapshotError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SnapshotError(f"{path}: expected object")
    return data


def fetch(url: str) -> dict[str, Any]:
    observed = utc_now()
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=35) as resp:  # nosec B310 - fixed official HTTPS endpoints
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        final_url = str(resp.geturl())
        content_type = str(resp.headers.get("content-type") or "")
    retrieved = utc_now()
    if status != 200:
        raise SnapshotError(f"HTTP {status}: {url}")
    return {
        "requested_url": url,
        "final_url": final_url,
        "observed_at_utc": iso(observed),
        "retrieved_at_utc": iso(retrieved),
        "content_type": content_type,
        "sha256": sha256_bytes(raw),
        "raw": raw,
    }


def write_raw(out: Path, name: str, item: dict[str, Any]) -> str:
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_bytes(item["raw"])
    return str(path.relative_to(out))


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_freezes(contract: dict[str, Any], fixtures: dict[str, Any], snapshot_start: datetime) -> tuple[list[dict[str, Any]], list[str], datetime]:
    violations: list[str] = []
    if contract.get("status") != "FROZEN_BEFORE_ANY_MW3_TARGET_KICKOFF":
        violations.append("CONTRACT_STATUS_MISMATCH")
    gov = contract.get("governance") or {}
    for key in (
        "target_result_access",
        "target_score_access",
        "target_confirmed_xi_access",
        "target_postmatch_event_access",
        "market_or_odds_access",
        "2023_confirmation_set_access",
        "3504_access",
    ):
        if gov.get(key) is not False:
            violations.append(f"FORBIDDEN_GOVERNANCE_FLAG:{key}")
    if fixtures.get("label_access") is not False:
        violations.append("FIXTURE_FREEZE_LABEL_ACCESS_NOT_FALSE")
    if fixtures.get("confirmed_xi_access") is not False:
        violations.append("FIXTURE_FREEZE_XI_ACCESS_NOT_FALSE")
    if fixtures.get("market_access") is not False:
        violations.append("FIXTURE_FREEZE_MARKET_ACCESS_NOT_FALSE")
    rows = fixtures.get("fixtures")
    expected_codes = fixtures.get("expected_team_short_names")
    if not isinstance(rows, list) or len(rows) != 10:
        raise SnapshotError("fixture freeze must contain exactly 10 fixtures")
    if not isinstance(expected_codes, list) or len(expected_codes) != 20 or len(set(expected_codes)) != 20:
        raise SnapshotError("fixture freeze must contain exactly 20 unique team codes")
    frozen_at = parse_utc(str(contract.get("frozen_at_utc")))
    kicks = [parse_utc(str(row["kickoff_at_utc"])) for row in rows]
    earliest = min(kicks)
    if not all(k > frozen_at for k in kicks):
        violations.append("TARGET_KICKOFF_NOT_AFTER_CONTRACT_FREEZE")
    if snapshot_start >= earliest:
        violations.append("SNAPSHOT_STARTED_AT_OR_AFTER_EARLIEST_KICKOFF")
    return rows, violations, earliest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    ap.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    snapshot_start = utc_now()
    contract = load_object(args.contract)
    fixtures = load_object(args.fixtures)
    rows, violations, earliest = validate_freezes(contract, fixtures, snapshot_start)

    # If the temporal gate is already broken, stop before touching any public target-adjacent source.
    if violations:
        final = {
            "schema_version": "football3-mw3-pit-snapshot-final-v1",
            "status": TERMINAL_STOP,
            "reason": "pre_source_governance_gate_failed",
            "violations": violations,
            "snapshot_started_at_utc": iso(snapshot_start),
            "earliest_target_kickoff_utc": iso(earliest),
            "research_only": True,
            "target_result_access": False,
            "target_confirmed_xi_access": False,
            "market_access": False,
            "2023_opened": False,
            "3504_opened": False,
        }
        (out / "final_status.json").write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(final, sort_keys=True))
        return 2

    source_defs = (
        ("fpl_bootstrap", FPL_BOOTSTRAP, "fpl_bootstrap.json", True),
        ("pl_injuries", PL_INJURIES, "pl_injuries.html", False),
        ("pl_suspensions", PL_SUSPENSIONS, "pl_suspensions.html", False),
    )
    sources: dict[str, dict[str, Any]] = {}
    source_errors: dict[str, str] = {}
    for key, url, filename, mandatory in source_defs:
        try:
            item = fetch(url)
            raw_path = write_raw(out, filename, item)
            meta = {k: v for k, v in item.items() if k != "raw"}
            meta["raw_path"] = raw_path
            meta["mandatory"] = mandatory
            sources[key] = meta
        except Exception as exc:
            source_errors[key] = f"{type(exc).__name__}: {exc}"

    fpl_raw = out / "raw" / "fpl_bootstrap.json"
    if not fpl_raw.exists():
        violations.append("MANDATORY_FPL_BOOTSTRAP_UNAVAILABLE")
        fpl = {}
    else:
        try:
            fpl = json.loads(fpl_raw.read_text(encoding="utf-8"))
        except Exception as exc:
            violations.append(f"FPL_JSON_INVALID:{type(exc).__name__}")
            fpl = {}

    teams = fpl.get("teams") if isinstance(fpl, dict) else None
    elements = fpl.get("elements") if isinstance(fpl, dict) else None
    if not isinstance(teams, list) or not isinstance(elements, list):
        violations.append("FPL_SCHEMA_MISSING_TEAMS_OR_ELEMENTS")
        teams, elements = [], []

    by_code: dict[str, dict[str, Any]] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        code = str(team.get("short_name") or "").strip().upper()
        if code:
            by_code[code] = team

    expected = sorted(str(x).upper() for x in fixtures["expected_team_short_names"])
    observed = sorted(by_code)
    if set(expected) != set(observed):
        missing = sorted(set(expected) - set(observed))
        extras = sorted(set(observed) - set(expected))
        if missing:
            violations.append("FPL_EXPECTED_TEAMS_MISSING:" + ",".join(missing))
        if extras:
            violations.append("FPL_UNEXPECTED_TEAMS_PRESENT:" + ",".join(extras))

    players_by_tid: dict[int, list[dict[str, Any]]] = {}
    for player in elements:
        if not isinstance(player, dict):
            continue
        tid = safe_int(player.get("team"))
        if tid is not None:
            players_by_tid.setdefault(tid, []).append(player)

    team_rows: list[dict[str, Any]] = []
    future_news_violations = 0
    for code in expected:
        team = by_code.get(code)
        if team is None:
            continue
        tid = safe_int(team.get("id"))
        prows = players_by_tid.get(tid or -1, [])
        if not prows:
            violations.append(f"FPL_TEAM_HAS_NO_PLAYERS:{code}")
        availability: list[dict[str, Any]] = []
        flagged = 0
        for p in prows:
            status = str(p.get("status") or "").strip() or None
            news = str(p.get("news") or "").strip() or None
            news_added = p.get("news_added")
            if status not in (None, "a") or news:
                flagged += 1
            if isinstance(news_added, str) and news_added:
                try:
                    if parse_utc(news_added) > snapshot_start:
                        future_news_violations += 1
                except Exception:
                    pass
            availability.append({
                "player_id": p.get("id"),
                "player_name": p.get("web_name") or p.get("second_name"),
                "status": status,
                "news": news,
                "news_added": news_added,
                "chance_of_playing_this_round": p.get("chance_of_playing_this_round"),
                "chance_of_playing_next_round": p.get("chance_of_playing_next_round"),
            })
        team_rows.append({
            "team_code": code,
            "team_id": tid,
            "team_name": team.get("name"),
            "player_count": len(availability),
            "flagged_player_count": flagged,
            "availability": availability,
        })
    if future_news_violations:
        violations.append(f"FPL_NEWS_ADDED_AFTER_SNAPSHOT_START:{future_news_violations}")

    team_row_by_code = {x["team_code"]: x for x in team_rows}
    fpl_meta = sources.get("fpl_bootstrap") or {}
    fixture_rows: list[dict[str, Any]] = []
    for row in rows:
        home_code = str(row["home_short"]).upper()
        away_code = str(row["away_short"]).upper()
        kickoff = parse_utc(str(row["kickoff_at_utc"]))
        fviol = []
        if home_code not in team_row_by_code:
            fviol.append("HOME_UNBOUND")
        if away_code not in team_row_by_code:
            fviol.append("AWAY_UNBOUND")
        if snapshot_start >= kickoff:
            fviol.append("NOT_PREMATCH_AT_SNAPSHOT_START")
        fixture_rows.append({
            **row,
            "snapshot_started_at_utc": iso(snapshot_start),
            "seconds_to_kickoff_at_snapshot_start": int((kickoff - snapshot_start).total_seconds()),
            "home_player_count": team_row_by_code.get(home_code, {}).get("player_count", 0),
            "away_player_count": team_row_by_code.get(away_code, {}).get("player_count", 0),
            "availability_source_observed_at_utc": fpl_meta.get("observed_at_utc"),
            "availability_source_sha256": fpl_meta.get("sha256"),
            "violations": fviol,
        })
        if fviol:
            violations.append(f"FIXTURE_BINDING_OR_TIME:{row['match_id']}:{','.join(fviol)}")

    snapshot_end = utc_now()
    snapshot = {
        "schema_version": "football3-mw3-prospective-pit-availability-snapshot-v1",
        "status": "LOCKED_PREMATCH_INPUT" if not violations else "STOPPED",
        "research_only": True,
        "formal_weight": 0,
        "snapshot_started_at_utc": iso(snapshot_start),
        "snapshot_completed_at_utc": iso(snapshot_end),
        "contract_path": str(args.contract.relative_to(ROOT)),
        "fixture_freeze_path": str(args.fixtures.relative_to(ROOT)),
        "contract_sha256": sha256_file(args.contract),
        "fixture_freeze_sha256": sha256_file(args.fixtures),
        "earliest_target_kickoff_utc": iso(earliest),
        "source_snapshots": sources,
        "source_errors": source_errors,
        "team_rows": team_rows,
        "fixture_rows": fixture_rows,
        "coverage": {
            "expected_teams": 20,
            "bound_teams": len(team_rows),
            "expected_fixtures": 10,
            "bound_future_fixtures": sum(1 for x in fixture_rows if not x["violations"]),
            "mandatory_fpl_fetched": "fpl_bootstrap" in sources,
            "corroboration_sources_fetched": sum(1 for k in ("pl_injuries", "pl_suspensions") if k in sources),
        },
        "hard_violations": violations,
        "governance_receipt": {
            "target_result_access": False,
            "target_score_access": False,
            "target_confirmed_xi_access": False,
            "target_postmatch_event_access": False,
            "market_or_odds_access": False,
            "retrospective_availability_backfill": False,
            "2023_opened": False,
            "3504_opened": False,
            "formal_v2_unchanged": True,
            "v3_1_1_unchanged": True,
            "CURRENT_changed": False,
            "production_pointer_changed": False,
            "formal_weights_changed": False,
        },
    }
    snapshot_path = out / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    final_status = TERMINAL_PASS if not violations else TERMINAL_STOP
    final = {
        "schema_version": "football3-mw3-pit-snapshot-final-v1",
        "status": final_status,
        "reason": "all_zero_label_prematch_snapshot_gates_passed" if not violations else "snapshot_gate_failed",
        "snapshot_started_at_utc": iso(snapshot_start),
        "snapshot_completed_at_utc": iso(snapshot_end),
        "earliest_target_kickoff_utc": iso(earliest),
        "snapshot_sha256": sha256_file(snapshot_path),
        "coverage": snapshot["coverage"],
        "source_errors": source_errors,
        "violations": violations,
        "research_only": True,
        "promotion_allowed": False,
        "target_result_access": False,
        "target_confirmed_xi_access": False,
        "market_access": False,
        "2023_opened": False,
        "3504_opened": False,
        "formal_v2_unchanged": True,
        "v3_1_1_unchanged": True,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_weights_changed": False,
        "git_head": os.getenv("GITHUB_SHA"),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
    }
    final_path = out / "final_status.json"
    final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "football3-mw3-pit-snapshot-artifact-manifest-v1",
        "contract_sha256": sha256_file(args.contract),
        "fixture_freeze_sha256": sha256_file(args.fixtures),
        "snapshot_sha256": sha256_file(snapshot_path),
        "final_status_sha256": sha256_file(final_path),
        "raw_source_sha256": {
            p.name: sha256_file(p) for p in sorted((out / "raw").glob("*")) if p.is_file()
        },
        "git_head": os.getenv("GITHUB_SHA"),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "terminal_status": final_status,
    }
    (out / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(final, sort_keys=True))
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
