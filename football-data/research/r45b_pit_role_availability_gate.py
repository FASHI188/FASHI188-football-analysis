#!/usr/bin/env python3
"""R45B zero-label PIT input gate.

This audit intentionally reads only evidence/configuration metadata and pre-match context.
It does not read match-result labels, fit a model, score predictions, tune parameters,
or call any provider/API.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "evidence"
TEAM_WEEKLY = EVID / "team_configuration_weekly"
LIVE_CONTEXT = EVID / "match_context_live" / "manual_web"
FPL_CONTEXT = EVID / "fpl_context_v519"
LINEUPS = EVID / "lineups"
MARKET_ALIGNED = EVID / "market_aligned_observations_prospective"
OUT = ROOT / "research" / "r45b_pit_role_availability_gate_status.json"

ROLE_KEYS = {
    "role", "position", "position_name", "detailed_position", "slot", "side",
    "formation_position", "player_role", "tactical_role", "zone",
}
PROCESS_KEYS = {
    "xg", "xa", "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "shots", "shots_on_target", "key_passes", "big_chances",
    "bps", "influence", "creativity", "threat",
}
TASK_KEYS = {
    "motivation", "stakes", "task", "mission", "must_win", "rotation",
    "rest_days", "aggregate_score", "first_leg_score", "qualification_state",
    "relegation_state", "title_race", "fixture_congestion",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def nested_keys(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k).lower())
            out |= nested_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= nested_keys(v)
    return out


def source_roles(snapshot: dict[str, Any]) -> list[str]:
    rows = snapshot.get("sources") or []
    out = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                out.append(str(row.get("source_role") or "").lower())
    return out


def latest_team_snapshots() -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any], str]] = {}
    if not TEAM_WEEKLY.exists():
        return []
    for path in sorted(TEAM_WEEKLY.glob("*.json")):
        if path.name.startswith("weekly_aggregate__"):
            continue
        try:
            obj = load_json(path)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        cid = str(obj.get("competition_id") or "")
        team = str(obj.get("team_name") or "").strip()
        ts = parse_ts(obj.get("observed_at_utc"))
        if not cid or not team or ts is None:
            continue
        key = (cid, team)
        prev = latest.get(key)
        if prev is None or ts > prev[0]:
            latest[key] = (ts, obj, path.name)
    return [
        {"competition_id": k[0], "team": k[1], "observed_at": v[0], "obj": v[1], "file": v[2]}
        for k, v in sorted(latest.items())
    ]


def audit_team_weekly() -> dict[str, Any]:
    rows = latest_team_snapshots()
    counts = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        obj = row["obj"]
        roles = source_roles(obj)
        keys = nested_keys(obj)
        availability = obj.get("availability") if isinstance(obj.get("availability"), list) else []
        predicted = []
        for k in ("predicted_xi", "expected_xi", "projected_xi", "probable_xi"):
            if isinstance(obj.get(k), list):
                predicted = obj[k]
                break
        availability_src = any(any(t in r for t in ("injury", "availability", "suspension", "fitness")) for r in roles)
        predicted_src = any(any(t in r for t in ("predicted xi", "expected xi", "projected xi", "probable xi", "predicted lineup", "projected lineup")) for r in roles)
        detailed_role = bool(ROLE_KEYS & keys)
        process = bool(PROCESS_KEYS & keys)
        if availability_src:
            counts["availability_timestamped_source"] += 1
        if availability_src and availability:
            counts["availability_nonempty"] += 1
        if predicted_src and len(predicted) >= 11:
            counts["predicted_xi_11plus"] += 1
        if detailed_role:
            counts["role_detail_any"] += 1
        if process:
            counts["process_metric_any"] += 1
        for label, ok in (
            ("availability", availability_src),
            ("predicted_xi", predicted_src and len(predicted) >= 11),
            ("role_detail", detailed_role),
            ("process", process),
        ):
            if ok and len(examples[label]) < 3:
                examples[label].append(row["file"])
    return {"latest_team_records": len(rows), "counts": dict(counts), "examples": dict(examples)}


def audit_live_context() -> dict[str, Any]:
    counts = Counter()
    files = []
    if not LIVE_CONTEXT.exists():
        return {"files": 0, "counts": {}, "examples": {}}
    examples: dict[str, list[str]] = defaultdict(list)
    for path in sorted(LIVE_CONTEXT.glob("*.json")):
        try:
            obj = load_json(path)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        files.append(path.name)
        obs = parse_ts(obj.get("observed_at_utc"))
        ident = obj.get("fixture_identity") if isinstance(obj.get("fixture_identity"), dict) else {}
        ko = parse_ts(ident.get("kickoff_at"))
        pre = bool(obs and ko and obs < ko)
        context = obj.get("context") if isinstance(obj.get("context"), dict) else {}
        avail = context.get("availability") if isinstance(context.get("availability"), dict) else {}
        xi = context.get("predicted_xi") if isinstance(context.get("predicted_xi"), dict) else {}
        home_xi = xi.get("home") if isinstance(xi.get("home"), list) else []
        away_xi = xi.get("away") if isinstance(xi.get("away"), list) else []
        keys = nested_keys(context)
        role_detail = bool(ROLE_KEYS & keys)
        process = bool(PROCESS_KEYS & keys)
        task = bool(TASK_KEYS & keys)
        has_availability = any(isinstance(avail.get(side), list) for side in ("home", "away"))
        if pre:
            counts["pre_kickoff"] += 1
        if pre and has_availability:
            counts["pre_kickoff_availability"] += 1
        if pre and len(home_xi) >= 11 and len(away_xi) >= 11:
            counts["pre_kickoff_predicted_xi_both"] += 1
        if pre and role_detail:
            counts["pre_kickoff_detailed_role"] += 1
        if pre and process:
            counts["pre_kickoff_process_metrics"] += 1
        if pre and task:
            counts["pre_kickoff_task_utility"] += 1
        for label, ok in (
            ("availability", pre and has_availability),
            ("predicted_xi", pre and len(home_xi) >= 11 and len(away_xi) >= 11),
            ("role_detail", pre and role_detail),
            ("process", pre and process),
            ("task", pre and task),
        ):
            if ok and len(examples[label]) < 3:
                examples[label].append(path.name)
    return {"files": len(files), "counts": dict(counts), "examples": dict(examples)}


def audit_fpl_context() -> dict[str, Any]:
    rows = 0
    row_keys: set[str] = set()
    path = FPL_CONTEXT / "team_gameweek_features.jsonl"
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rows += 1
                if rows <= 200:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    row_keys |= nested_keys(obj)
    has_row_available_at = any(k in row_keys for k in ("available_at", "available_at_utc", "observed_at_utc", "source_published_at_utc", "collector_first_observed_at_utc"))
    return {
        "rows": rows,
        "sampled_key_count": len(row_keys),
        "has_row_level_prematch_available_at": has_row_available_at,
        "classification": "PIT_CANDIDATE" if has_row_available_at else "RETROSPECTIVE_REFERENCE_ONLY",
    }


def audit_lineup_archive() -> dict[str, Any]:
    files = []
    if LINEUPS.exists():
        files = [str(p.relative_to(LINEUPS)) for p in LINEUPS.rglob("*.jsonl")]
    by_source = Counter()
    for name in files:
        if "statsbomb" in name.lower():
            by_source["statsbomb_open"] += 1
        elif "transfermarkt" in name.lower():
            by_source["transfermarkt_datasets"] += 1
        else:
            by_source["other"] += 1
    return {
        "jsonl_files": len(files),
        "by_source": dict(by_source),
        "classification": "RETROSPECTIVE_REFERENCE_ONLY_UNLESS_ORIGINAL_PREMATCH_AVAILABLE_AT_PROVEN",
        "content_read": False,
    }


def audit_market_aligned() -> dict[str, Any]:
    counts = Counter()
    files = []
    examples = []
    if not MARKET_ALIGNED.exists():
        return {"files": 0, "counts": {}, "examples": []}
    for path in sorted(MARKET_ALIGNED.glob("*.json")):
        try:
            obj = load_json(path)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        files.append(path.name)
        obs = parse_ts(obj.get("source_observed_at_utc"))
        ko = parse_ts(obj.get("kickoff_utc"))
        surfaces = obj.get("surface_observed_at_utc") if isinstance(obj.get("surface_observed_at_utc"), dict) else {}
        one = obj.get("one_x_two") if isinstance(obj.get("one_x_two"), dict) else {}
        ah = obj.get("asian_handicap") if isinstance(obj.get("asian_handicap"), dict) else {}
        ou = obj.get("over_under") if isinstance(obj.get("over_under"), dict) else {}
        complete = all(k in one for k in ("home", "draw", "away")) and all(k in ah for k in ("line", "home", "away")) and all(k in ou for k in ("line", "over", "under"))
        sync_values = [parse_ts(surfaces.get(k)) for k in ("one_x_two", "asian_handicap", "over_under")]
        sync = all(sync_values) and len({x.isoformat() for x in sync_values if x}) == 1
        pre = bool(obs and ko and obs < ko)
        if complete:
            counts["complete_1x2_ah_ou"] += 1
        if pre:
            counts["pre_kickoff"] += 1
        if sync:
            counts["same_window_surfaces"] += 1
        if complete and pre and sync:
            counts["strict_candidate"] += 1
            if len(examples) < 3:
                examples.append(path.name)
    return {"files": len(files), "counts": dict(counts), "examples": examples}


def main() -> int:
    team = audit_team_weekly()
    live = audit_live_context()
    fpl = audit_fpl_context()
    lineups = audit_lineup_archive()
    market = audit_market_aligned()

    live_counts = live.get("counts", {})
    team_counts = team.get("counts", {})
    market_counts = market.get("counts", {})

    axes = {
        "pit_availability": (live_counts.get("pre_kickoff_availability", 0) > 0 or team_counts.get("availability_timestamped_source", 0) > 0),
        "pit_predicted_xi": (live_counts.get("pre_kickoff_predicted_xi_both", 0) > 0 or team_counts.get("predicted_xi_11plus", 0) > 0),
        "pit_detailed_roles": live_counts.get("pre_kickoff_detailed_role", 0) > 0,
        "pit_replacement_gap_inputs": False,
        "pit_matchup_interaction_inputs": live_counts.get("pre_kickoff_detailed_role", 0) > 0,
        "pit_xg_process_capability": live_counts.get("pre_kickoff_process_metrics", 0) > 0,
        "pit_task_utility": live_counts.get("pre_kickoff_task_utility", 0) > 0,
        "pit_synchronized_market": market_counts.get("strict_candidate", 0) > 0,
    }
    # Replacement-gap requires unavailable-player identity + same-freeze role/depth + quality/process context.
    axes["pit_replacement_gap_inputs"] = bool(
        axes["pit_availability"] and axes["pit_detailed_roles"] and axes["pit_xg_process_capability"]
    )

    missing_core = [k for k, v in axes.items() if not v]
    overall = "PASS_ZERO_LABEL_INPUT_GATE" if not missing_core else "STOP_R45B_PIT_INPUTS_INCOMPLETE"

    payload = {
        "schema_version": "R45B-PIT-ROLE-AVAILABILITY-ZERO-LABEL-R1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": overall,
        "stage": "ZERO_LABEL_PIT_INPUT_GATE",
        "labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "axes": axes,
        "missing_core_axes": missing_core,
        "evidence": {
            "team_configuration_weekly": team,
            "match_context_live": live,
            "fpl_context_v519": fpl,
            "lineup_archive": lineups,
            "market_aligned_observations_prospective": market,
        },
        "ruling": {
            "historical_static_lineups_without_original_prematch_time": "RETROSPECTIVE_REFERENCE_ONLY",
            "fpl_aggregates_without_row_level_available_at": "RETROSPECTIVE_REFERENCE_ONLY",
            "pre_kickoff_live_context_can_support_availability_and_predicted_xi": True,
            "synchronized_market_candidates_may_be_used_only_if_fixture/freeze identity is independently bound": True,
            "independent_oos_authorized": not missing_core,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if overall.startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
