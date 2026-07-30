#!/usr/bin/env python3
"""E3f-0: PIT feature coverage, freeze-time and leakage audit.

Research-only. This script does not fit a candidate model, tune a threshold,
or modify any formal asset. It reconstructs the frozen 6,251-match identity
through the existing Champion OOS chain only to audit whether candidate
pre-match feature families are already available, safely derivable from prior
matches, absent, or leakage-prone.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import market_joint_direct_outcome_e3b1 as e3b1  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT, read_processed_matches  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3f0_pit_feature_coverage"
EXPECTED_SAMPLE = 6251
MIN_HISTORY = 3
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".md", ".txt", ".py"}

FAMILIES = {
    "lineup_availability": ("lineup", "starting_xi", "starter", "bench", "squad", "injury", "injured", "suspension", "suspended", "unavailable", "absence", "availability"),
    "task_state": ("standings", "table_position", "rank", "points_gap", "relegation", "title_race", "qualification", "task_state", "motivation"),
    "fatigue_schedule": ("rest_days", "fatigue", "congestion", "travel", "distance", "rotation"),
    "tactical_style": ("formation", "manager", "coach", "pressing", "possession", "tempo", "tactical", "style", "ppda"),
    "xg_chance_quality": ("xg", "expected_goals", "npxg", "shot_quality", "big_chance", "expected_assists", "xa"),
    "game_state_response": ("half_time_state", "leading_to_win", "trailing_to_draw", "equaliser", "comeback", "game_state", "ht_to_ft"),
}

POSTMATCH_CURRENT_FIELDS = {
    "shots": ("HS", "AS"),
    "shots_on_target": ("HST", "AST"),
    "corners": ("HC", "AC"),
    "cards": ("HY", "AY"),
    "half_time_score": ("HTHG", "HTAG", "HTR"),
    "full_time_result": ("FTHG", "FTAG", "FTR"),
}


def repository_head() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def nonempty(value: Any) -> bool:
    return str("" if value is None else value).strip() != ""


def parse_key(match_key: str) -> tuple[str, str, str, str]:
    parts = str(match_key).split("|", 3)
    if len(parts) != 4:
        raise RuntimeError(f"bad match key: {match_key}")
    return parts[0], parts[1], parts[2], parts[3]


def reconstruct_fixed_sample() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, joins, fold_counts = [], {}, {}
    for competition_id in b100.BIG5:
        seasons, folds = e3a.nested_competition(competition_id)
        joined, audit = e3b1.join(competition_id, seasons)
        for item in joined:
            season, date, home, away = parse_key(item["match_key"])
            record = dict(item)
            record.update({"season": season, "date": date, "home_team": home, "away_team": away})
            rows.append(record)
        joins[competition_id] = audit
        fold_counts[competition_id] = {"folds": len(folds), "oos_records": sum(len(values) for values in seasons.values()), "joined": len(joined)}
    rows.sort(key=lambda row: (row["date"], row["competition_id"], row["match_key"]))
    if len(rows) != EXPECTED_SAMPLE:
        raise RuntimeError(f"fixed sample contract failed: {len(rows)} != {EXPECTED_SAMPLE}")
    if len({row["match_key"] for row in rows}) != len(rows):
        raise RuntimeError("fixed sample has duplicate match keys")
    return rows, {"reconstruction": "existing_frozen_champion_oos_identity_only", "new_candidate_model_fit": False, "joins": joins, "fold_counts": fold_counts}


def load_raw_observations(competition_id: str) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    metadata = b100.raw_rows(competition_id)
    wanted_by_path: dict[str, dict[int, str]] = defaultdict(dict)
    for key, meta in metadata.items():
        wanted_by_path[str(meta["source_path"])][int(meta["source_line"])] = key
    output, schemas, paths = {}, Counter(), []
    for relative, wanted in sorted(wanted_by_path.items()):
        path = ROOT / relative
        paths.append(relative)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for name in reader.fieldnames or []:
                schemas[str(name)] += 1
            for line_no, raw in enumerate(reader, start=2):
                key = wanted.get(line_no)
                if key is not None:
                    output[key] = {str(k).strip(): "" if v is None else str(v).strip() for k, v in raw.items() if k}
    return output, {"metadata_rows": len(metadata), "loaded_rows": len(output), "source_files": paths, "schema_columns": sorted(schemas)}


def contains_pattern(name: str, patterns: tuple[str, ...]) -> bool:
    token = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return any(pattern in token for pattern in patterns)


def current_row_family_presence(raw: dict[str, str], family: str) -> bool:
    return any(contains_pattern(key, FAMILIES[family]) and nonempty(value) for key, value in raw.items())


def complete_fields(raw: dict[str, str], keys: tuple[str, ...]) -> bool:
    return all(nonempty(raw.get(key)) for key in keys)


def same_day_schedule_features(competition_id: str, sample_keys: set[str], raw_lookup: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    matches = read_processed_matches(competition_id)
    by_date: dict[datetime, list[Any]] = defaultdict(list)
    for match in matches:
        by_date[match.date].append(match)
    last_date, recent_dates = {}, defaultdict(deque)
    table = defaultdict(lambda: {"played": 0, "points": 0, "gd": 0})
    shot_history, state_history = Counter(), Counter()
    derived = {}
    for date in sorted(by_date):
        day = sorted(by_date[date], key=lambda match: (match.home_team, match.away_team))
        for match in day:
            key = f"{match.season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}"
            if key not in sample_keys:
                continue
            for team in (match.home_team, match.away_team):
                cutoff = date - timedelta(days=14)
                while recent_dates[team] and recent_dates[team][0] < cutoff:
                    recent_dates[team].popleft()
            home_last, away_last = last_date.get(match.home_team), last_date.get(match.away_team)
            ht, at = table[match.home_team], table[match.away_team]
            derived[key] = {
                "rest_days_home": None if home_last is None else (date - home_last).days,
                "rest_days_away": None if away_last is None else (date - away_last).days,
                "matches_14d_home": len(recent_dates[match.home_team]),
                "matches_14d_away": len(recent_dates[match.away_team]),
                "standings_available": True,
                "home_matches_played_before": ht["played"],
                "away_matches_played_before": at["played"],
                "points_gap_before": ht["points"] - at["points"],
                "goal_difference_gap_before": ht["gd"] - at["gd"],
                "shot_proxy_history_available": shot_history[match.home_team] >= MIN_HISTORY and shot_history[match.away_team] >= MIN_HISTORY,
                "game_state_history_available": state_history[match.home_team] >= MIN_HISTORY and state_history[match.away_team] >= MIN_HISTORY,
            }
        for match in day:
            key = f"{match.season}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}"
            raw = raw_lookup.get(key, {})
            hg, ag = int(match.home_goals), int(match.away_goals)
            hp = 3 if hg > ag else 1 if hg == ag else 0
            ap = 3 if ag > hg else 1 if hg == ag else 0
            table[match.home_team]["played"] += 1
            table[match.away_team]["played"] += 1
            table[match.home_team]["points"] += hp
            table[match.away_team]["points"] += ap
            table[match.home_team]["gd"] += hg - ag
            table[match.away_team]["gd"] += ag - hg
            last_date[match.home_team] = date
            last_date[match.away_team] = date
            recent_dates[match.home_team].append(date)
            recent_dates[match.away_team].append(date)
            if complete_fields(raw, ("HS", "AS", "HST", "AST", "HC", "AC")):
                shot_history[match.home_team] += 1
                shot_history[match.away_team] += 1
            if complete_fields(raw, ("HTHG", "HTAG", "FTHG", "FTAG")):
                state_history[match.home_team] += 1
                state_history[match.away_team] += 1
    return derived


def scan_repository() -> dict[str, Any]:
    roots = [ROOT / "raw", ROOT / "processed", ROOT / "engine", ROOT / "validation", ROOT / "config", ROOT / "manifests"]
    hits = {name: [] for name in FAMILIES}
    scanned = 0
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.open("r", encoding="utf-8", errors="ignore").readline() if path.stat().st_size > 2_000_000 else path.read_text(encoding="utf-8", errors="ignore")[:250_000]
            except OSError:
                continue
            scanned += 1
            relative = str(path.relative_to(ROOT.parent))
            haystack = (relative + "\n" + text).lower()
            for family, keywords in FAMILIES.items():
                if any(keyword in haystack for keyword in keywords):
                    hits[family].append(relative)
    return {"files_scanned": scanned, "hits": {family: sorted(set(paths))[:50] for family, paths in hits.items()}, "hit_counts": {family: len(set(paths)) for family, paths in hits.items()}}


def summarize(rows: list[dict[str, Any]], raw_by_comp: dict[str, dict[str, dict[str, str]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    derived_all, source_audit = {}, {}
    for competition_id in b100.BIG5:
        subset_keys = {row["match_key"] for row in rows if row["competition_id"] == competition_id}
        derived_all.update(same_day_schedule_features(competition_id, subset_keys, raw_by_comp[competition_id]))
        source_audit[competition_id] = {"sample_rows": len(subset_keys), "raw_rows_joined": sum(key in raw_by_comp[competition_id] for key in subset_keys)}
    enriched, direct_counts = [], {family: 0 for family in FAMILIES}
    postmatch_counts = {name: 0 for name in POSTMATCH_CURRENT_FIELDS}
    for row in rows:
        raw = raw_by_comp[row["competition_id"]].get(row["match_key"], {})
        item = dict(row)
        item["pit_derived"] = derived_all.get(row["match_key"], {})
        item["direct_family_presence"] = {family: current_row_family_presence(raw, family) for family in FAMILIES}
        item["current_postmatch_fields"] = {name: complete_fields(raw, keys) for name, keys in POSTMATCH_CURRENT_FIELDS.items()}
        for family, present in item["direct_family_presence"].items():
            direct_counts[family] += int(present)
        for name, present in item["current_postmatch_fields"].items():
            postmatch_counts[name] += int(present)
        enriched.append(item)
    def coverage(predicate) -> dict[str, Any]:
        count = sum(bool(predicate(row)) for row in enriched)
        return {"count": count, "coverage": count / len(enriched)}
    feature_coverage = {
        "lineup_availability_direct": coverage(lambda row: row["direct_family_presence"]["lineup_availability"]),
        "xg_chance_quality_direct": coverage(lambda row: row["direct_family_presence"]["xg_chance_quality"]),
        "formation_manager_direct": coverage(lambda row: row["direct_family_presence"]["tactical_style"]),
        "task_state_direct": coverage(lambda row: row["direct_family_presence"]["task_state"]),
        "fatigue_direct": coverage(lambda row: row["direct_family_presence"]["fatigue_schedule"]),
        "rest_days_safe_derived": coverage(lambda row: row["pit_derived"].get("rest_days_home") is not None and row["pit_derived"].get("rest_days_away") is not None),
        "congestion_14d_safe_derived": coverage(lambda row: "matches_14d_home" in row["pit_derived"] and "matches_14d_away" in row["pit_derived"]),
        "standings_safe_derived": coverage(lambda row: bool(row["pit_derived"].get("standings_available"))),
        "shot_style_proxy_prior_history": coverage(lambda row: bool(row["pit_derived"].get("shot_proxy_history_available"))),
        "game_state_response_prior_history": coverage(lambda row: bool(row["pit_derived"].get("game_state_history_available"))),
        "referee_current_row": coverage(lambda row: nonempty(raw_by_comp[row["competition_id"]].get(row["match_key"], {}).get("Referee"))),
    }
    return {"sample_count": len(enriched), "source_join": source_audit, "direct_candidate_field_counts": direct_counts, "feature_coverage": feature_coverage, "current_match_postmatch_field_coverage": {name: {"count": count, "coverage": count / len(enriched)} for name, count in postmatch_counts.items()}}, enriched


def subset_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    def rate(predicate) -> dict[str, Any]:
        count = sum(bool(predicate(row)) for row in rows)
        return {"count": count, "coverage": count / total if total else None}
    return {
        "count": total,
        "rest_days": rate(lambda row: row["pit_derived"].get("rest_days_home") is not None and row["pit_derived"].get("rest_days_away") is not None),
        "standings": rate(lambda row: row["pit_derived"].get("standings_available")),
        "shot_proxy": rate(lambda row: row["pit_derived"].get("shot_proxy_history_available")),
        "game_state_proxy": rate(lambda row: row["pit_derived"].get("game_state_history_available")),
        "lineup_direct": rate(lambda row: row["direct_family_presence"]["lineup_availability"]),
        "xg_direct": rate(lambda row: row["direct_family_presence"]["xg_chance_quality"]),
    }


def status_matrix(coverage: dict[str, Any], repo_scan: dict[str, Any]) -> dict[str, Any]:
    fc = coverage["feature_coverage"]
    return {
        "lineup_availability": {"status": "ABSENT_FROM_FIXED_SAMPLE", "coverage": fc["lineup_availability_direct"]["coverage"], "freeze_contract": "NONE", "leakage_risk": "UNKNOWN_UNTIL_SOURCE_SELECTED", "decision": "NEW_EXTERNAL_PIT_SOURCE_REQUIRED"},
        "task_state": {"status": "DERIVABLE_PIT_READY_FOR_FEATURE_PROTOTYPE", "coverage": fc["standings_safe_derived"]["coverage"], "freeze_contract": "before each match date; same-day batch updated only after predictions", "leakage_risk": "LOW_IF_SAME_DAY_BATCH_RULE_PRESERVED", "decision": "CAN_BUILD_FROM_PRIOR_RESULTS_WITHOUT_EXTERNAL_API"},
        "fatigue_schedule": {"status": "PARTIALLY_DERIVABLE_PIT", "coverage": fc["rest_days_safe_derived"]["coverage"], "congestion_coverage": fc["congestion_14d_safe_derived"]["coverage"], "freeze_contract": "prior fixture dates only", "leakage_risk": "LOW_FOR_REST_CONGESTION; TRAVEL_AND_ROTATION_ABSENT", "decision": "REST_AND_CONGESTION_READY; TRAVEL_ROTATION_NEED_NEW_SOURCE"},
        "tactical_style": {"status": "PROXY_ONLY", "coverage": fc["shot_style_proxy_prior_history"]["coverage"], "freeze_contract": "prior completed-match shots/SOT/corners only", "leakage_risk": "HIGH_IF_CURRENT_MATCH_STATS_JOINED; LOW_IF_PRIOR_ONLY", "decision": "SHOT_STYLE_PROXY_AVAILABLE; FORMATION_PRESSING_POSSESSION_ABSENT"},
        "xg_chance_quality": {"status": "ABSENT_XG_PROXY_AVAILABLE", "coverage": fc["xg_chance_quality_direct"]["coverage"], "shot_proxy_coverage": fc["shot_style_proxy_prior_history"]["coverage"], "freeze_contract": "no xG source contract", "leakage_risk": "CURRENT_MATCH_SHOTS_ARE_POSTMATCH_AND_FORBIDDEN", "decision": "NEW_XG_OR_EVENT_SOURCE_REQUIRED"},
        "game_state_response": {"status": "DERIVABLE_COARSE_PROXY", "coverage": fc["game_state_response_prior_history"]["coverage"], "freeze_contract": "prior HT/FT results only; same-day batch safe", "leakage_risk": "HIGH_IF_CURRENT_MATCH_HT_FT_USED; LOW_IF_PRIOR_ONLY", "decision": "COARSE_HT_TO_FT_RESPONSE_READY; EVENT_TIMING_ABSENT"},
        "referee": {"status": "PARTIAL_OPTIONAL", "coverage": fc["referee_current_row"]["coverage"], "freeze_contract": "named pre-match assignment not timestamped", "leakage_risk": "MEDIUM_WITHOUT_ORIGINAL_ANNOUNCEMENT_TIMESTAMP", "decision": "COVERAGE_AUDIT_ONLY"},
        "repository_evidence": {family: {"hit_count": repo_scan["hit_counts"][family], "paths": repo_scan["hits"][family]} for family in FAMILIES},
    }


def markdown(report: dict[str, Any]) -> str:
    lines = ["# E3f-0 PIT Feature Coverage and Leakage Audit", "", "Research-only; no candidate model fit; no threshold; formal_weight=0.", "", f"- Repository HEAD: `{report['repository_head']}`", f"- Fixed sample: {report['sample']['count']}", f"- Fixed B100: {report['b100']['count']}", "", "## Feature-family status", "", "| Family | Status | Full coverage | B100 coverage | Decision |", "|---|---|---:|---:|---|"]
    status, b100_cov, full_cov = report["status_matrix"], report["b100"]["coverage"], report["coverage"]["feature_coverage"]
    mapping = {"lineup_availability": ("lineup_availability_direct", "lineup_direct"), "task_state": ("standings_safe_derived", "standings"), "fatigue_schedule": ("rest_days_safe_derived", "rest_days"), "tactical_style": ("shot_style_proxy_prior_history", "shot_proxy"), "xg_chance_quality": ("xg_chance_quality_direct", "xg_direct"), "game_state_response": ("game_state_response_prior_history", "game_state_proxy")}
    for family, (full_key, b100_key) in mapping.items():
        item = status[family]
        lines.append(f"| {family} | {item['status']} | {full_cov[full_key]['coverage']:.2%} | {b100_cov[b100_key]['coverage']:.2%} | {item['decision']} |")
    lines.extend(["", "## Leakage boundary", "", "- Current-match shots, SOT, corners, cards and HT/FT fields are post-match and forbidden as same-match inputs.", "- The same fields may be transformed into prior-match history only after date-batched updates.", "- Rest, congestion and standings are derived before each match date and updated after all same-day matches.", "- No lineup, injury, formation or xG source with an original pre-match timestamp exists in the fixed sample.", "", "## Verdict", "", f"- Ready derivable families: {', '.join(report['verdict']['ready_derivable'])}", f"- Proxy-only families: {', '.join(report['verdict']['proxy_only'])}", f"- New external PIT source required: {', '.join(report['verdict']['external_source_required'])}", "- No model was trained and no formal asset was modified.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        rows, lineage = reconstruct_fixed_sample()
        raw_by_comp, raw_audit = {}, {}
        for competition_id in b100.BIG5:
            raw_by_comp[competition_id], raw_audit[competition_id] = load_raw_observations(competition_id)
        coverage, enriched = summarize(rows, raw_by_comp)
        by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in enriched:
            by_competition[row["competition_id"]].append(row)
        b100_rows, selection = e3a.fixed_b100(by_competition)
        if len(b100_rows) != 100:
            raise RuntimeError(f"B100 contract failed: {len(b100_rows)}")
        repo_scan = scan_repository()
        report = {
            "schema_version": "1.0", "research_status": "PASS", "experiment": "E3F0_PIT_FEATURE_COVERAGE_AND_LEAKAGE_AUDIT", "repository_head": repository_head(), "scope": "90_minutes_pure_hda_feature_availability_only",
            "sample": {"count": len(enriched), "expected": EXPECTED_SAMPLE, "leagues": list(b100.BIG5)},
            "b100": {"count": len(b100_rows), "selection": selection, "coverage": subset_coverage(b100_rows)},
            "lineage": lineage, "raw_source_audit": raw_audit, "coverage": coverage, "status_matrix": status_matrix(coverage, repo_scan), "repository_scan": repo_scan,
            "leakage_rules": {"same_day_batch_update_after_prediction": True, "current_match_postmatch_stats_forbidden": True, "prior_match_postmatch_stats_allowed_after_completion": True, "original_ingestion_timestamps_present": False, "new_candidate_model_fit": False, "class_weights": False, "manual_threshold": False},
            "verdict": {"ready_derivable": ["task_state", "rest_and_congestion"], "proxy_only": ["shot_style", "coarse_game_state_response"], "external_source_required": ["lineup_availability", "injury_suspension", "formation_tactical_style", "xg_chance_quality", "travel_rotation"], "training_allowed_next": False, "next_step": "SOURCE_CONTRACT_AND_COVERAGE_PLAN_BEFORE_ANY_MODEL_TRAINING"},
            "promotion": {"automatic_promotion": False, "formal_weight": 0, "status": "COVERAGE_AUDIT_ONLY"},
            "formal_mutation": {"model": 0, "data": 0, "config": 0, "current": 0, "formal_weight": 0}, "failures": [],
        }
    except Exception as exc:
        report = {"schema_version": "1.0", "research_status": "FAIL", "experiment": "E3F0_PIT_FEATURE_COVERAGE_AND_LEAKAGE_AUDIT", "repository_head": repository_head(), "failures": [{"error": f"{type(exc).__name__}: {exc}"}], "promotion": {"automatic_promotion": False, "formal_weight": 0}, "formal_mutation": {"model": 0, "data": 0, "config": 0, "current": 0, "formal_weight": 0}}
    json_path = output_dir / "e3f0_pit_feature_coverage.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["research_status"] == "PASS":
        (output_dir / "e3f0_pit_feature_coverage.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({"research_status": report["research_status"], "repository_head": report.get("repository_head"), "sample": report.get("sample"), "b100": report.get("b100"), "coverage": report.get("coverage", {}).get("feature_coverage"), "verdict": report.get("verdict"), "failures": report.get("failures")}, ensure_ascii=False, indent=2))
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
