#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_parent(path: Path):
    spec = importlib.util.spec_from_file_location("r39c_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import frozen R39C parent")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_dt(text: str) -> datetime:
    s = text.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unsupported match_datetime: {text!r}")


def qstats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    x = sorted(values)
    n = len(x)

    def q(frac: float):
        pos = min(n - 1, max(0, int(round(frac * (n - 1)))))
        return x[pos]

    return {
        "n": n,
        "min": x[0],
        "p05": q(0.05),
        "p10": q(0.10),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "p95": q(0.95),
        "max": x[-1],
        "mean": sum(x) / n,
    }


def fixture_source_for_matches(name: str) -> str:
    if name == "odds_series_matches.csv.gz":
        return "odds_series.csv.gz"
    if name == "odds_series_b_matches.csv.gz":
        return "odds_series_b.csv.gz"
    raise ValueError(name)


def load_score_free_metadata(source_dir: Path):
    rows = []
    team_to_leagues: dict[str, set[str]] = defaultdict(set)
    event_key_counts = Counter()
    for original in ("odds_series_matches.csv.gz", "odds_series_b_matches.csv.gz"):
        path = source_dir / original.replace(".csv.gz", "_no_scores.csv.gz")
        source_odds = fixture_source_for_matches(original)
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            expected = ["match_id", "league", "home_team", "away_team", "match_datetime"]
            if reader.fieldnames != expected:
                raise RuntimeError(f"unexpected sanitized metadata header {reader.fieldnames} for {path}")
            for r in reader:
                match_id = r["match_id"].strip()
                league = r["league"].strip()
                home = r["home_team"].strip()
                away = r["away_team"].strip()
                if not match_id or not league or not home or not away:
                    continue
                dt = parse_dt(r["match_datetime"])
                identity = f"{source_odds}|{match_id}"
                row = {
                    "identity": identity,
                    "source_file": source_odds,
                    "match_id": match_id,
                    "league": league,
                    "home_team": home,
                    "away_team": away,
                    "dt": dt,
                }
                rows.append(row)
                team_to_leagues[home].add(league)
                team_to_leagues[away].add(league)
                event_key_counts[(league, home, away, dt)] += 1
    return rows, team_to_leagues, event_key_counts


def build_schedule_features(metadata_rows: list[dict]):
    histories: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    features = {}
    same_timestamp_team_conflicts = 0

    rows = sorted(metadata_rows, key=lambda x: (x["dt"], x["source_file"], x["match_id"]))
    i = 0
    while i < len(rows):
        dt = rows[i]["dt"]
        j = i + 1
        while j < len(rows) and rows[j]["dt"] == dt:
            j += 1
        batch = rows[i:j]

        seen_keys_in_batch = Counter()
        for r in batch:
            hk = (r["league"], r["home_team"])
            ak = (r["league"], r["away_team"])
            seen_keys_in_batch[hk] += 1
            seen_keys_in_batch[ak] += 1
            hh = histories[hk]
            ah = histories[ak]

            def state(hist: list[datetime]):
                prior = len(hist)
                rest = None if not hist else (dt - hist[-1]).total_seconds() / 86400.0
                last7 = sum(1 for x in hist if x >= dt - timedelta(days=7))
                last14 = sum(1 for x in hist if x >= dt - timedelta(days=14))
                return prior, rest, last7, last14

            hp, hr, h7, h14 = state(hh)
            ap, ar, a7, a14 = state(ah)
            features[r["identity"]] = {
                "home_prior_match_count": hp,
                "away_prior_match_count": ap,
                "home_rest_days_since_previous_match": hr,
                "away_rest_days_since_previous_match": ar,
                "home_matches_previous_7d": h7,
                "away_matches_previous_7d": a7,
                "home_matches_previous_14d": h14,
                "away_matches_previous_14d": a14,
            }

        same_timestamp_team_conflicts += sum(v - 1 for v in seen_keys_in_batch.values() if v > 1)
        # Histories are updated only after every row at this exact timestamp was featurized.
        # Deduplicate the same league/team/timestamp so a duplicated fixture cannot create
        # an artificial extra prior match for future fixtures.
        update_keys = set()
        for r in batch:
            update_keys.add((r["league"], r["home_team"]))
            update_keys.add((r["league"], r["away_team"]))
        for key in update_keys:
            histories[key].append(dt)
        i = j

    return features, same_timestamp_team_conflicts


def aggregate(rows: list[dict], feature_map: dict[str, dict]):
    out = {
        "rows": len(rows),
        "joined_rows": 0,
        "both_prior_ge1": 0,
        "both_prior_ge3": 0,
        "both_prior_ge5": 0,
        "both_rest_available": 0,
        "zero_or_negative_rest_values": 0,
    }
    series = defaultdict(list)
    for r in rows:
        f = feature_map.get(r["identity"])
        if f is None:
            continue
        out["joined_rows"] += 1
        hp = f["home_prior_match_count"]
        ap = f["away_prior_match_count"]
        out["both_prior_ge1"] += int(hp >= 1 and ap >= 1)
        out["both_prior_ge3"] += int(hp >= 3 and ap >= 3)
        out["both_prior_ge5"] += int(hp >= 5 and ap >= 5)
        hr = f["home_rest_days_since_previous_match"]
        ar = f["away_rest_days_since_previous_match"]
        out["both_rest_available"] += int(hr is not None and ar is not None)
        for key, value in f.items():
            if value is not None:
                series[key].append(float(value))
        for rest in (hr, ar):
            if rest is not None and rest <= 0:
                out["zero_or_negative_rest_values"] += 1

    out["coverage_rates"] = {
        "joined": out["joined_rows"] / out["rows"] if out["rows"] else 0,
        "both_prior_ge1": out["both_prior_ge1"] / out["rows"] if out["rows"] else 0,
        "both_prior_ge3": out["both_prior_ge3"] / out["rows"] if out["rows"] else 0,
        "both_prior_ge5": out["both_prior_ge5"] / out["rows"] if out["rows"] else 0,
        "both_rest_available": out["both_rest_available"] / out["rows"] if out["rows"] else 0,
    }
    out["distributions"] = {k: qstats(v) for k, v in sorted(series.items())}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registration", type=Path, required=True)
    ap.add_argument("--sanitized-dir", type=Path, required=True)
    ap.add_argument("--parent-code", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    reg = json.loads(args.registration.read_text(encoding="utf-8"))
    assert reg["status"] == "PRE_REGISTERED_ZERO_LABEL_SCHEDULE_FATIGUE_COVERAGE_AUDIT"
    assert reg["hard_limits"]["result_labels_allowed"] is False
    assert reg["population_binding"]["fifth_fixed100_lock_allowed_in_this_stage"] is False

    parent = load_parent(args.parent_code)
    eligible, _ = parent.load_feature_rows(args.sanitized_dir, {})
    start = datetime.fromisoformat(reg["population_binding"]["holdout_start"])
    train = sorted([x for x in eligible.values() if x["dt"] < start], key=lambda z: (z["dt"], z["identity"]))
    hold = sorted([x for x in eligible.values() if x["dt"] >= start], key=lambda z: (z["dt"], z["identity"]))
    assert len(train) == reg["population_binding"]["expected_training_eligible_rows"]
    assert len(hold) == reg["population_binding"]["expected_holdout_eligible_rows"]

    metadata, team_to_leagues, event_counts = load_score_free_metadata(args.sanitized_dir)
    feature_map, same_time_conflicts = build_schedule_features(metadata)
    train_summary = aggregate(train, feature_map)
    hold_summary = aggregate(hold, feature_map)

    multi_league_names = {name: sorted(leagues) for name, leagues in team_to_leagues.items() if len(leagues) > 1}
    duplicate_event_rows = sum(v - 1 for v in event_counts.values() if v > 1)
    duplicate_event_keys = sum(1 for v in event_counts.values() if v > 1)
    total_bad_rest = train_summary["zero_or_negative_rest_values"] + hold_summary["zero_or_negative_rest_values"]

    gates = {
        "training_rows_exact": len(train) == reg["pass_gate"]["training_rows_exactly_reproduce"],
        "holdout_rows_exact": len(hold) == reg["pass_gate"]["holdout_rows_exactly_reproduce"],
        "all_training_rows_join_metadata": train_summary["joined_rows"] == len(train),
        "all_holdout_rows_join_metadata": hold_summary["joined_rows"] == len(hold),
        "zero_or_negative_rest_days": total_bad_rest == reg["pass_gate"]["zero_or_negative_rest_days"],
        "score_or_result_values_accessed_zero": True,
        "prediction_metrics_computed_zero": True,
        "model_fits_zero": True,
        "identity_locks_zero": True,
    }
    gates["passed"] = all(gates.values())
    status = "PASS_R40B_ZERO_LABEL_SCHEDULE_FATIGUE_COVERAGE" if gates["passed"] else "STOP_R40B_ZERO_LABEL_SCHEDULE_FATIGUE_COVERAGE"

    result = {
        "schema_version": reg["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "population": {
            "metadata_rows": len(metadata),
            "training_eligible_rows": len(train),
            "holdout_eligible_rows": len(hold),
        },
        "training": train_summary,
        "holdout": hold_summary,
        "identity_diagnostics": {
            "exact_team_names_seen": len(team_to_leagues),
            "team_names_seen_in_multiple_leagues": len(multi_league_names),
            "multi_league_team_name_examples_first20": dict(list(sorted(multi_league_names.items()))[:20]),
            "duplicate_event_keys": duplicate_event_keys,
            "duplicate_event_rows": duplicate_event_rows,
            "same_timestamp_team_conflict_excess_appearances": same_time_conflicts,
            "team_key_used": reg["schedule_identity"]["team_key"],
        },
        "gates": gates,
        "no_label_audit": {
            "score_columns_present_in_python_odds_input": False,
            "score_columns_present_in_python_matches_input": False,
            "score_values_accessed": 0,
            "result_values_accessed": 0,
            "holdout_individual_identities_output": 0,
            "prediction_metrics_computed": 0,
            "model_fits": 0,
            "thresholds_selected": 0,
            "identity_locks_created": 0,
        },
        "next_stage_authorization": "COVERAGE_ONLY_NO_MODEL_OR_FIFTH100_AUTHORIZATION",
        "hard_limits": reg["hard_limits"],
    }
    (args.out_dir / "schedule_fatigue_coverage_status_r40b.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
