#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import validate_1x2_pit_lineup_increment_v6117c as lineup_fixed
import validate_1x2_injury_onset_fast100_v6131 as injury_source

OUTDIR = Path(__file__).resolve().parent / "results"
SUMMARY = OUTDIR / "summary_r40a.json"
DICTIONARY = OUTDIR / "recognition_dictionary_r40a.json"

SEASONS = {"2024/25", "2025/26"}


def safe_ratio(a, b):
    return a / b if b else None


def audit_lineups():
    matches = [r for r in lineup_fixed.base._load_matches() if r["season"] in SEASONS]
    lineups = {cid: lineup_fixed.base._load_lineups(cid) for cid in lineup_fixed.base.COMPETITIONS}
    team_hist = defaultdict(list)
    target_rows = 0
    both_actual = 0
    expected_both = 0
    formation_rows = 0
    unique_players = set()
    expected_overlap_values = []
    by_season = defaultdict(lambda: Counter())

    for r in matches:
        cid, season, ds = r["competition_id"], r["season"], str(r["date"])[:10]
        hk = (season, ds, r["home"])
        ak = (season, ds, r["away"])
        hi = lineups[cid].get(hk)
        ai = lineups[cid].get(ak)
        th = (cid, season, r["home"])
        ta = (cid, season, r["away"])

        target_rows += 1
        by_season[season]["matches"] += 1
        hp = lineup_fixed.base._predicted_xi(team_hist[th])
        ap = lineup_fixed.base._predicted_xi(team_hist[ta])
        if hp is not None and ap is not None:
            expected_both += 1
            by_season[season]["expected_both"] += 1
            if hi and ai:
                hset, aset = set(hi["starters"]), set(ai["starters"])
                expected_overlap_values.extend([
                    len(set(hp[0]) & hset) / 11.0,
                    len(set(ap[0]) & aset) / 11.0,
                ])

        if hi and ai:
            both_actual += 1
            by_season[season]["both_actual"] += 1
            unique_players.update(map(str, hi["starters"]))
            unique_players.update(map(str, ai["starters"]))
            if hi.get("formation") and ai.get("formation"):
                formation_rows += 1
                by_season[season]["both_formation"] += 1

            # Critical PIT rule: actual target XI enters history only after target features are frozen.
            team_hist[th].append((ds, tuple(hi["starters"])))
            team_hist[ta].append((ds, tuple(ai["starters"])))

    identity = lineup_fixed._ensure()[2]
    mapped = sum(x.get("mapped_count", 0) for x in identity if x.get("season") in SEASONS)
    market_teams = sum(x.get("market_team_count", 0) for x in identity if x.get("season") in SEASONS)
    low_similarity = []
    for x in identity:
        if x.get("season") not in SEASONS:
            continue
        for d in x.get("non_exact", []):
            if float(d.get("similarity", 0.0)) < 0.70:
                low_similarity.append(d)

    return {
        "matches": target_rows,
        "actual_lineup_both_coverage": safe_ratio(both_actual, target_rows),
        "strict_prior_expected_xi_both_coverage": safe_ratio(expected_both, target_rows),
        "formation_both_coverage": safe_ratio(formation_rows, target_rows),
        "mean_expected_xi_overlap_when_actual_available": safe_ratio(sum(expected_overlap_values), len(expected_overlap_values)),
        "unique_player_tokens": len(unique_players),
        "team_identity_mapped_rate": safe_ratio(mapped, market_teams),
        "low_similarity_identity_pairs_lt_0_70": len(low_similarity),
        "by_season": {k: dict(v) for k, v in sorted(by_season.items())},
        "pit_rule": "target actual XI is forbidden as an input and is appended to history only after target features are frozen",
    }, unique_players


def audit_injuries(unique_lineup_players):
    injuries, meta = injury_source.load_injury_onsets()
    lineup_pids = {injury_source._pid(p) for p in unique_lineup_players if injury_source._pid(p)}
    injury_pids = set(injuries)
    linked = lineup_pids & injury_pids
    return {
        "source": meta,
        "lineup_player_tokens_normalized": len(lineup_pids),
        "injury_players_in_scope": len(injury_pids),
        "linked_lineup_to_injury_players": len(linked),
        "entity_link_rate_vs_lineup_players": safe_ratio(len(linked), len(lineup_pids)),
        "temporal_status": "RETROSPECTIVE_ONLY_NO_ORIGINAL_PUBLIC_ANNOUNCEMENT_TIMESTAMP",
        "allowed_for_formal_prematch_model": False,
        "allowed_fields_for_research": ["player_id", "season_name", "injury_reason", "from_date"],
        "forbidden_for_features": ["end_date", "days_missed", "games_missed"],
    }


def audit_schedule_load():
    matches = [r for r in lineup_fixed.base._load_matches() if r["season"] in SEASONS]
    last_date = {}
    count = 0
    rest_known = 0
    for r in matches:
        count += 1
        ds = datetime.fromisoformat(str(r["date"])[:10]).date()
        for team in (r["home"], r["away"]):
            key = (r["competition_id"], r["season"], team)
            if key in last_date and ds > last_date[key]:
                rest_known += 1
            last_date[key] = ds
    return {
        "matches": count,
        "team_sides": count * 2,
        "prior_rest_days_available_sides": rest_known,
        "prior_rest_days_coverage": safe_ratio(rest_known, count * 2),
        "temporal_status": "PREMATCH_READY_FROM_PRIOR_FIXTURE_DATES",
        "recognizable_fields": ["rest_days", "matches_last_3d", "matches_last_7d", "matches_last_14d", "consecutive_away_sequence"],
    }


def build_dictionary():
    common = ["fixture_id", "team_id", "player_id", "event_time", "known_at", "source", "confidence", "temporal_class"]
    return {
        "schema_version": "football3-r40a-recognition-dictionary-v1",
        "common_event_fields": common,
        "temporal_classes": {
            "PREMATCH_READY": "known_at is auditable and strictly earlier than prediction cutoff",
            "DERIVED_STRICT_PRIOR": "computed only from events strictly earlier than target kickoff",
            "RETROSPECTIVE_ONLY": "historical fact exists but original public availability time is not auditable",
            "BLOCKED": "source or identity/timestamp quality insufficient for modeling",
        },
        "event_types": {
            "expected_lineup": {
                "fields": ["expected_starters", "starter_probabilities", "lineup_certainty", "lineup_continuity", "prior_churn", "formation_tendency"],
                "temporal_class": "DERIVED_STRICT_PRIOR",
            },
            "player_unavailable_injury": {
                "fields": ["player_id", "reason", "onset_date", "announcement_known_at"],
                "temporal_class": "RETROSPECTIVE_ONLY until announcement_known_at exists",
            },
            "player_unavailable_suspension": {
                "fields": ["player_id", "reason", "effective_from", "known_at"],
                "temporal_class": "BLOCKED until timestamped source is added",
            },
            "player_strength": {
                "fields": ["strict_prior_player_residual", "expected_minutes_share", "replacement_drop"],
                "temporal_class": "DERIVED_STRICT_PRIOR for residual; expected_minutes/replacement quality need later validation",
            },
            "schedule_load": {
                "fields": ["rest_days", "matches_last_3d", "matches_last_7d", "matches_last_14d", "consecutive_away_sequence"],
                "temporal_class": "PREMATCH_READY",
            },
            "travel_load": {
                "fields": ["distance_km", "cross_border", "timezone_shift_hours"],
                "temporal_class": "BLOCKED until audited venue geocodes exist",
            },
            "manager_context": {
                "fields": ["manager_id", "appointment_known_at", "matches_in_charge"],
                "temporal_class": "BLOCKED until timestamped manager source is added",
            },
            "tactical_shape": {
                "fields": ["formation_tendency", "pressing_proxy", "possession_style", "directness_proxy"],
                "temporal_class": "LIMITED: prior formation recognizable; richer tactics need source",
            },
            "weather_context": {
                "fields": ["temperature_c", "wind_kph", "precipitation_mm", "pitch_state", "forecast_known_at"],
                "temporal_class": "BLOCKED until timestamped forecast archive is added",
            },
        },
        "hard_rules": [
            "never use target actual XI for a prediction made before official lineup publication",
            "never use injury end_date/days_missed/games_missed to infer earlier availability",
            "every external event must carry known_at before it can enter a formal prematch model",
            "entity joins must be stable by competition/season and ambiguous mappings must remain auditable",
            "unavailable fields remain null/BLOCKED; no synthetic backfill from post-match facts",
        ],
    }


def classify(lineup_audit, injury_audit, schedule_audit):
    fields = {
        "expected_lineup": "READY" if (lineup_audit["strict_prior_expected_xi_both_coverage"] or 0) > 0 else "BLOCKED",
        "lineup_continuity": "READY" if (lineup_audit["strict_prior_expected_xi_both_coverage"] or 0) > 0 else "BLOCKED",
        "formation_tendency": "READY" if (lineup_audit["formation_both_coverage"] or 0) > 0 else "LIMITED",
        "player_identity": "READY" if (lineup_audit["team_identity_mapped_rate"] or 0) >= 0.90 else "LIMITED",
        "injury_onset": "RETROSPECTIVE_ONLY",
        "confirmed_injury_availability": "BLOCKED_NO_ANNOUNCEMENT_KNOWN_AT",
        "suspension": "BLOCKED_NO_AUDITED_SOURCE",
        "strict_prior_player_strength": "READY_DERIVED",
        "schedule_load": "READY" if (schedule_audit["prior_rest_days_coverage"] or 0) > 0.80 else "LIMITED",
        "travel_load": "BLOCKED_NO_VENUE_GEOCODES",
        "manager_change": "BLOCKED_NO_TIMESTAMPED_SOURCE",
        "rich_tactics": "BLOCKED_NO_AUDITED_SOURCE",
        "weather": "BLOCKED_NO_TIMESTAMPED_FORECAST_ARCHIVE",
    }
    ready = [k for k, v in fields.items() if v.startswith("READY")]
    return fields, ready


def run():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    dictionary = build_dictionary()
    DICTIONARY.write_text(json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lineup_audit, players = audit_lineups()
    injury_audit = audit_injuries(players)
    schedule_audit = audit_schedule_load()
    field_status, ready = classify(lineup_audit, injury_audit, schedule_audit)

    payload = {
        "schema_version": "football3-r40a-prematch-information-recognition-v1",
        "status": "COMPLETE",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "goal": "Build an auditable football reality -> structured prematch field recognition layer before new predictive research.",
        "formal_model_changed": False,
        "prediction_weights_changed": False,
        "lineup_audit": lineup_audit,
        "injury_audit": injury_audit,
        "schedule_audit": schedule_audit,
        "field_status": field_status,
        "ready_information_families": ready,
        "next_research_gate": {
            "allowed_now": ["expected_lineup", "lineup_continuity", "formation_tendency", "strict_prior_player_strength", "schedule_load"],
            "research_only": ["injury_onset"],
            "blocked_until_new_source": ["confirmed_injury_availability", "suspension", "travel_load", "manager_change", "rich_tactics", "weather"],
            "rule": "Only fields with auditable prematch timing can enter subsequent R40 predictive experiments.",
        },
    }
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def verify():
    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    dic = json.loads(DICTIONARY.read_text(encoding="utf-8"))
    assert payload["formal_model_changed"] is False
    assert payload["prediction_weights_changed"] is False
    assert payload["injury_audit"]["allowed_for_formal_prematch_model"] is False
    assert payload["field_status"]["confirmed_injury_availability"].startswith("BLOCKED")
    assert payload["field_status"]["weather"].startswith("BLOCKED")
    assert "known_at" in dic["common_event_fields"]
    print(json.dumps({"status": "PASS", "ready": payload["ready_information_families"]}, ensure_ascii=False))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
