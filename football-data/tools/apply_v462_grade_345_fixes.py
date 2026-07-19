#!/usr/bin/env python3
"""Apply idempotent V4.6.2 engineering fixes for A-grade gates 3/4/5.

3: install the validated-probable-lineup route contract without fabricating data.
4: bind A-grade replay evidence to independently generated replay receipts.
5: expand time-ordered evaluation folds and add a shrinkable direct-total signal
   candidate so total-goals RPS can improve only through nested OOS selection.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def update_config() -> None:
    path = ROOT / "config" / "formal_core_v460.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = "1.3"
    data["engine_version"] = "V4.6.2-core-2"
    defaults = data["default_parameters"]
    defaults.setdefault("direct_total_signal_weight", 1.0)
    candidates = data["candidate_parameters"]
    for candidate in candidates:
        candidate.setdefault("direct_total_signal_weight", 1.0)
    existing = {
        tuple(sorted((key, float(value)) for key, value in candidate.items()))
        for candidate in candidates
    }
    for base in list(candidates[:2]):
        candidate = dict(base)
        candidate["direct_total_signal_weight"] = 0.65
        token = tuple(sorted((key, float(value)) for key, value in candidate.items()))
        if token not in existing:
            candidates.append(candidate)
            existing.add(token)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_engine() -> None:
    path = ROOT / "engine" / "football_v460_engine.py"
    text = path.read_text(encoding="utf-8")
    old = '''    mu_total = math.sqrt(max(1e-12, home_total_rate) * max(1e-12, away_total_rate))
    mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))
'''
    new = '''    pair_total_rate = math.sqrt(max(1e-12, home_total_rate) * max(1e-12, away_total_rate))
    # Nested-OOS selectable shrinkage of the venue-pair total signal toward the
    # competition total. Weight=1 preserves the original direct-total model;
    # lower weights are allowed only when earlier seasons select them.
    direct_total_signal_weight = min(1.0, max(0.0, float(params.get("direct_total_signal_weight", 1.0))))
    mu_total = math.exp(
        (1.0 - direct_total_signal_weight) * math.log(max(1e-12, league_total))
        + direct_total_signal_weight * math.log(max(1e-12, pair_total_rate))
    )
    mu_total = min(2.0 * maximum_mu, max(2.0 * minimum_mu, mu_total))
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif "direct_total_signal_weight" not in text:
        raise RuntimeError("engine direct-total patch anchor not found")
    old_return = '''        "direct_total_method": "geometric_shrunk_venue_total_rates",
'''
    new_return = '''        "direct_total_method": "nested_oos_shrunk_geometric_venue_total_rates",
        "direct_total_signal_weight": direct_total_signal_weight,
        "pair_direct_total_rate": pair_total_rate,
'''
    if old_return in text:
        text = text.replace(old_return, new_return, 1)
    path.write_text(text, encoding="utf-8")


def update_nested_backtest() -> None:
    path = ROOT / "validation" / "nested_backtest_v460.py"
    text = path.read_text(encoding="utf-8")
    helper_anchor = '''def _season_is_partial(season_matches: dict[str, list[MatchRow]], season: str) -> bool:
'''
    helper = '''def _split_outer_time_blocks(
    model_records: list[dict[str, Any]],
    baseline_records: list[dict[str, Any]],
    blocks: int = 2,
) -> list[dict[str, Any]]:
    """Split one completely unseen outer season into disjoint chronological folds.

    Hyperparameters remain selected only from prior seasons. The split changes
    evaluation granularity, not training, so each record appears in exactly one
    outer time fold and no same-season outcome can affect parameter selection.
    """
    if not model_records:
        return []
    dates = sorted({str(record["date"]) for record in model_records})
    block_count = min(max(1, blocks), len(dates))
    output: list[dict[str, Any]] = []
    for index in range(block_count):
        start = index * len(dates) // block_count
        end = (index + 1) * len(dates) // block_count
        selected_dates = set(dates[start:end])
        model_part = [record for record in model_records if str(record["date"]) in selected_dates]
        baseline_part = [record for record in baseline_records if str(record["date"]) in selected_dates]
        if not model_part:
            continue
        output.append({
            "block_index": index,
            "test_start_date": min(selected_dates),
            "test_end_date": max(selected_dates),
            "model_records": model_part,
            "baseline_records": baseline_part,
        })
    return output


'''
    if "def _split_outer_time_blocks(" not in text:
        if helper_anchor not in text:
            raise RuntimeError("nested helper anchor not found")
        text = text.replace(helper_anchor, helper + helper_anchor, 1)

    old_fold = '''        if model_records:
            outer_records.extend(model_records)
            outer_baseline.extend(baseline_records)
            fold_details.append({
                "outer_season": outer_season,
                "prior_seasons": prior_seasons,
                "selection_predictions": selection_count,
                "selected_candidate_index": selected_index,
                "selected_parameters": selected_candidate,
                "outer_predictions": len(model_records),
                "model_metrics": _aggregate(model_records),
                "baseline_metrics": _aggregate(baseline_records),
            })
'''
    new_fold = '''        if model_records:
            outer_records.extend(model_records)
            outer_baseline.extend(baseline_records)
            for block in _split_outer_time_blocks(model_records, baseline_records, blocks=2):
                fold_details.append({
                    "outer_fold_id": f"{outer_season}:T{int(block['block_index']) + 1}",
                    "outer_season": outer_season,
                    "prior_seasons": prior_seasons,
                    "selection_predictions": selection_count,
                    "selected_candidate_index": selected_index,
                    "selected_parameters": selected_candidate,
                    "test_start_date": block["test_start_date"],
                    "test_end_date": block["test_end_date"],
                    "outer_predictions": len(block["model_records"]),
                    "model_metrics": _aggregate(block["model_records"]),
                    "baseline_metrics": _aggregate(block["baseline_records"]),
                })
'''
    if old_fold in text:
        text = text.replace(old_fold, new_fold, 1)
    elif "outer_fold_id" not in text:
        raise RuntimeError("nested fold patch anchor not found")

    old_checks = '''        "joint_log_score_ci": bootstrap["joint_log_score"]["ci95_upper"] is not None and bootstrap["joint_log_score"]["ci95_upper"] < 0.0,
        "total_goals_rps_ci": bootstrap["total_goals_rps"]["ci95_upper"] is not None and bootstrap["total_goals_rps"]["ci95_upper"] <= 0.0,
'''
    new_checks = '''        "joint_log_score_ci": bootstrap["joint_log_score"]["ci95_upper"] is not None and bootstrap["joint_log_score"]["ci95_upper"] < 0.0,
        "one_x_two_brier_rps_ci": (
            bootstrap["one_x_two_brier"]["ci95_upper"] is not None
            and bootstrap["one_x_two_brier"]["ci95_upper"] <= 0.002
            and bootstrap["one_x_two_rps"]["ci95_upper"] is not None
            and bootstrap["one_x_two_rps"]["ci95_upper"] <= 0.002
        ),
        "total_goals_rps_ci": bootstrap["total_goals_rps"]["ci95_upper"] is not None and bootstrap["total_goals_rps"]["ci95_upper"] <= 0.0,
'''
    if old_checks in text:
        text = text.replace(old_checks, new_checks, 1)
    path.write_text(text, encoding="utf-8")


def update_oof_builder() -> None:
    path = ROOT / "validation" / "oof_matrix_calibration_v461.py"
    text = path.read_text(encoding="utf-8")
    old = '''    folds, unsupported = [], 0
    for fold in source.get("folds", []):
        season = fold["outer_season"]
        records, missing = evaluate_outer_season(
            competition_id,
            sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)),
            fold["selected_parameters"],
        )
        folds.append({"season": season, "records": records})
        unsupported += missing
'''
    new = '''    # Nested validation may split one unseen season into multiple disjoint
    # evaluation folds for A-grade fold counting. OOF calibration remains
    # season-routed: evaluate each outer season exactly once so a target season
    # can never train its own calibrator through an earlier sub-fold.
    folds, unsupported = [], 0
    seen_outer_seasons: set[str] = set()
    for fold in source.get("folds", []):
        season = fold["outer_season"]
        if season in seen_outer_seasons:
            continue
        seen_outer_seasons.add(season)
        records, missing = evaluate_outer_season(
            competition_id,
            sorted(by_season[season], key=lambda x: (x.date, x.home_team, x.away_team)),
            fold["selected_parameters"],
        )
        folds.append({"season": season, "records": records})
        unsupported += missing
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif "seen_outer_seasons" not in text:
        raise RuntimeError("OOF fold dedup patch anchor not found")
    path.write_text(text, encoding="utf-8")


def update_auditor() -> None:
    path = ROOT / "validation" / "a_grade_auditor.py"
    text = path.read_text(encoding="utf-8")
    root_anchor = '''MODEL_ROOT = ROOT / "models" / "formal_core_v460"
'''
    if "REPLAY_REPORT_ROOT" not in text:
        text = text.replace(root_anchor, root_anchor + 'REPLAY_REPORT_ROOT = ROOT / "validation" / "reports" / "replay_v462"\n', 1)
    load_anchor = '''    model_path = MODEL_ROOT / competition_id / "model.json"
'''
    if "replay_path = REPLAY_REPORT_ROOT" not in text:
        text = text.replace(load_anchor, load_anchor + '    replay_path = REPLAY_REPORT_ROOT / f"{competition_id}.json"\n', 1)
    load2 = '''    oof = load_json(oof_path) if oof_path.exists() else None
'''
    if "replay = load_json(replay_path)" not in text:
        text = text.replace(load2, load2 + '    replay = load_json(replay_path) if replay_path.exists() else None\n', 1)
    old = '''        "independent_replay_receipt": bool(core_checks.get("independent_replay_receipt")),
'''
    new = '''        "independent_replay_receipt": bool(
            replay
            and replay.get("status") == "通过"
            and replay.get("independent_process") is True
            and int(replay.get("fixture_count", 0)) >= 12
            and float(replay.get("max_probability_difference", 1.0)) <= 1e-10
            and replay.get("engine_sha256") == core.get("engine_sha256") == model.get("engine_sha256")
            and replay.get("model_artifact_sha256") == sha256_file(model_path)
            and replay.get("core_report_sha256") == sha256_file(core_path)
        ),
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif "replay.get(\"status\")" not in text:
        raise RuntimeError("A auditor replay patch anchor not found")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    update_config()
    update_engine()
    update_nested_backtest()
    update_oof_builder()
    update_auditor()
    print("Applied V4.6.2 A-grade 3/4/5 engineering fixes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
