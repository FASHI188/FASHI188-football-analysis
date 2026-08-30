from research_fit import *

def run_research() -> dict[str, Any]:
    manifest = json.loads((EVIDENCE / "universe_manifest.json").read_text(encoding="utf-8"))
    research_path = EVIDENCE / "research_rows.jsonl"
    if sha256_file(research_path) != manifest["research_rows_sha256"]:
        raise GovernanceError("research rows digest drift")
    rows = read_jsonl(research_path)
    if len(rows) != int(manifest["research_n"]):
        raise GovernanceError("research row count drift")
    folds = outer_folds(rows)

    chosen_params, features, dynamic_trials = select_dynamic(rows, folds)

    family_results: dict[str, Any] = {}
    fold_metrics_by_family: dict[str, list[dict[str, Any]]] = {}
    for family in FAMILIES:
        fold_metrics = []
        for fold_id, (train_end, start, end) in enumerate(folds, start=1):
            dispersion = comp_dispersion(rows, train_end)
            dep = fit_dependence(family, rows, features, train_end, dispersion)
            items = build_items(rows, features, start, end, family, dispersion, dep)
            m = evaluate_predictions(items)
            m["fold_id"] = fold_id
            m["train_end"] = train_end
            m["test_start"] = start
            m["test_end"] = end
            m["dependence"] = dep
            fold_metrics.append(m)
        fold_metrics_by_family[family] = fold_metrics
        family_results[family] = {
            "folds": fold_metrics,
            "mean_logloss": statistics.fmean(m["logloss"] for m in fold_metrics),
            "mean_brier": statistics.fmean(m["brier"] for m in fold_metrics),
            "mean_rps": statistics.fmean(m["rps"] for m in fold_metrics),
            "mean_exact_score_logloss": statistics.fmean(m["exact_score_logloss"] for m in fold_metrics),
            "mean_draw_binary_logloss": statistics.fmean(m["draw_binary_logloss"] for m in fold_metrics),
        }

    baseline_fold_ll = [m["logloss"] for m in fold_metrics_by_family["INDEPENDENT_POISSON_FROZEN"]]
    candidates = []
    for family in FAMILIES:
        result = family_results[family]
        stability = fold_gain_status([m["logloss"] for m in result["folds"]], baseline_fold_ll)
        result["stability_vs_C1"] = stability
        if family == "INDEPENDENT_POISSON_FROZEN" or stability["retention_stability"]:
            candidates.append(family)
    joint_winner = min(
        candidates,
        key=lambda f: (
            family_results[f]["mean_logloss"],
            family_results[f]["mean_brier"],
            family_results[f]["mean_rps"],
            family_results[f]["mean_exact_score_logloss"],
            family_results[f]["mean_draw_binary_logloss"],
        ),
    )

    # Fitness ablation on the frozen joint winner.
    fitness_fold = []
    joint_fold = fold_metrics_by_family[joint_winner]
    for fold_id, (train_end, start, end) in enumerate(folds, start=1):
        dispersion = comp_dispersion(rows, train_end)
        best_fit = None
        for fitness in FITNESS_GRID:
            dep = fit_dependence(joint_winner, rows, features, train_end, dispersion, fitness)
            sample_start = max(0, train_end - min(train_end, 1800))
            items = build_items(rows, features, sample_start, train_end, joint_winner, dispersion, dep, fitness)
            score = evaluate_predictions(items)["logloss"]
            if best_fit is None or score < best_fit[0]:
                best_fit = (score, fitness, dep)
        assert best_fit is not None
        _, fitness, dep = best_fit
        m = evaluate_predictions(build_items(rows, features, start, end, joint_winner, dispersion, dep, fitness))
        m["fold_id"] = fold_id
        m["fitness"] = list(fitness)
        m["dependence"] = dep
        fitness_fold.append(m)
    fitness_status = fold_gain_status([m["logloss"] for m in fitness_fold], [m["logloss"] for m in joint_fold])
    fitness_retained = bool(fitness_status["retention_stability"])
    selected_fitness = (0.0, 0.0)
    if fitness_retained:
        counts: dict[tuple[float, float], int] = defaultdict(int)
        for m in fitness_fold:
            counts[tuple(m["fitness"])] += 1
        selected_fitness = max(sorted(counts), key=lambda x: (counts[x], -abs(x[0]) - abs(x[1])))

    dual_fold = []
    base_for_dual_ll = []
    base_for_dual_metrics = []
    for fold_id, (train_end, start, end) in enumerate(folds, start=1):
        dispersion = comp_dispersion(rows, train_end)
        dep = fit_dependence(joint_winner, rows, features, train_end, dispersion, selected_fitness)
        base_items = build_items(rows, features, start, end, joint_winner, dispersion, dep, selected_fitness)
        base_metric = evaluate_predictions(base_items)
        base_for_dual_ll.append(base_metric["logloss"])
        base_for_dual_metrics.append(base_metric)
        weights = fit_head(rows, features, train_end, selected_fitness)
        items = build_items(rows, features, start, end, joint_winner, dispersion, dep, selected_fitness, weights)
        m = evaluate_predictions(items)
        m["fold_id"] = fold_id
        m["dependence"] = dep
        dual_fold.append(m)
    dual_status = fold_gain_status([m["logloss"] for m in dual_fold], base_for_dual_ll)
    dual_draw_delta = statistics.fmean(m["draw_binary_logloss"] for m in dual_fold) - statistics.fmean(
        m["draw_binary_logloss"] for m in base_for_dual_metrics
    )
    dual_ece_delta = statistics.fmean(m["macro_ece"] for m in dual_fold) - statistics.fmean(
        m["macro_ece"] for m in base_for_dual_metrics
    )
    dual_retained = bool(dual_status["retention_stability"] and dual_draw_delta <= 0.003 and dual_ece_delta <= 0.005)

    full_dispersion = comp_dispersion(rows, len(rows))
    final_dep = fit_dependence(joint_winner, rows, features, len(rows), full_dispersion, selected_fitness)
    final_head = fit_head(rows, features, len(rows), selected_fitness) if dual_retained else None

    registry = json.loads((ROOT / "config" / "platform_registry.json").read_text(encoding="utf-8"))
    line_status = registry.get("global_capabilities", {}).get("historical_lineups_and_injuries")
    layer_status = {
        "dynamic_team": "RETAINED",
        "joint_score": f"RETAINED:{joint_winner}",
        "player_lineup": "BLOCKED_DATA_PIT_BACKFILL_NOT_COMPLETE" if line_status else "BLOCKED_DATA",
        "bench": "BLOCKED_DATA_REQUIRES_PIT_LINEUP_AND_SUBSTITUTION_HISTORY",
        "coach_tactical_regime": "BLOCKED_DATA_NO_VERIFIED_COACH_REGIME_LEDGER",
        "fitness_schedule": "RETAINED" if fitness_retained else "REJECTED_ABLATION",
        "match_process": "BLOCKED_DATA_NO_LAWFUL_MINUTE_EVENT_HISTORY",
        "dual_head_kl": "RETAINED" if dual_retained else "REJECTED_ABLATION",
    }

    final_features_path = EVIDENCE / "final_features.jsonl"
    if sha256_file(final_features_path) != manifest["final_features_sha256"]:
        raise GovernanceError("final feature digest drift")
    final_features = read_jsonl(final_features_path)
    final_fixture_manifest = [{
        "fixture_id": r["fixture_id"],
        "cutoff": r["cutoff"],
        "competition_id": r["competition_id"],
        "season": r["season"],
    } for r in final_features]
    final_fixture_sha = hashlib.sha256(b"".join(canonical_json_bytes(x) + b"\n" for x in final_fixture_manifest)).hexdigest()

    lock = {
        "schema_version": "football3-v2-final-model-lock-v1",
        "anchor": "7c1815c47102412e88f72189e2b8f837d9b73a42",
        "contract_commit": "b0cc96085159b1d215589264fb3dad759a016c3f",
        "selected_dynamic_parameters": chosen_params.__dict__,
        "joint_family": joint_winner,
        "competition_dispersion": {cid: [v[0], v[1]] for cid, v in sorted(full_dispersion.items())},
        "dependence": final_dep,
        "fitness": list(selected_fitness),
        "fitness_retained": fitness_retained,
        "dual_head_retained": dual_retained,
        "dual_head_weights": final_head,
        "max_goals": chosen_params.max_goals,
        "layer_status": layer_status,
        "pure_market_input": False,
        "v1_reference": {
            "head": "22f639304d2e32fc952dbec2255153ee45dcd41a",
            "run_id": 33313470476,
            "artifact_id": 9732754224,
            "artifact_name": "football3-new-engine-v1-22f639304d2e32fc952dbec2255153ee45dcd41a-33313470476",
            "artifact_digest": "sha256:5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb",
        },
    }
    lock_path = EVIDENCE / "final_model_lock.json"
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = {
        "schema_version": "football3-v2-research-result-v1",
        "research_n": len(rows),
        "outer_fold_count": len(folds),
        "outer_folds": [{"fold": i + 1, "train_end": a, "test_start": b, "test_end": c} for i, (a, b, c) in enumerate(folds)],
        "dynamic_trials": dynamic_trials,
        "joint_candidates": family_results,
        "joint_winner": joint_winner,
        "fitness_ablation": {"folds": fitness_fold, "status": fitness_status, "retained": fitness_retained, "selected": list(selected_fitness)},
        "dual_head_candidate": {
            "folds": dual_fold,
            "status": dual_status,
            "draw_ll_delta": dual_draw_delta,
            "macro_ece_delta": dual_ece_delta,
            "retained": dual_retained,
        },
        "layer_status": layer_status,
        "blocked_data_explanation": {
            "player_lineup": "M10 registry states public observed-lineup routes exist but complete PIT injury/suspension backfill is credential-gated and incomplete; no Secret/paid backfill authorized.",
            "coach_tactical_regime": "No verified historical coach-regime ledger with known_at was found in the anchor-scoped repository search.",
            "match_process": "No lawful minute-level substitution/event history with known_at was found; score-only rows cannot validate a process layer.",
            "true_promoted_team_label": "Lower-division/promotion identity history is not in the frozen domestic top-flight universe; report new-to-competition/cold-start slices, not a fabricated promotion label.",
        },
    }
    (EVIDENCE / "research_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gates_text = (HERE / "contracts" / "VALIDATION_PREREG.md").read_bytes()
    final_rule = {
        "schema_version": "football3-v2-final-rule-freeze-v1",
        "status": "FINAL_RULE_FROZEN_BEFORE_HOLDOUT_LABEL_ACCESS",
        "research_result_sha256": sha256_file(EVIDENCE / "research_result.json"),
        "model_lock_sha256": sha256_file(lock_path),
        "validation_prereg_sha256": hashlib.sha256(gates_text).hexdigest(),
        "final_features_sha256": manifest["final_features_sha256"],
        "final_fixture_manifest_sha256": final_fixture_sha,
        "final_fixture_n": len(final_features),
        "final_first_cutoff": final_features[0]["cutoff"],
        "final_last_cutoff": final_features[-1]["cutoff"],
        "label_file_not_opened_by_research_stage": True,
        "reliability_bins": [i / 10.0 for i in range(11)],
        "promotion_target": "V1 exact same-match intersection plus all absolute/governance gates in VALIDATION_PREREG.md",
    }
    (EVIDENCE / "final_rule_freeze.json").write_text(json.dumps(final_rule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_research(), ensure_ascii=False, indent=2))
