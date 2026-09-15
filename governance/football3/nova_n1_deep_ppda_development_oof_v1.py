#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any


class DevelopmentOOFError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DevelopmentOOFError(message)


def canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(obj, dict), f"NOT_JSON_OBJECT:{path}")
    return obj


def read_jsonl_prefix(path: Path, n: int) -> list[dict[str, Any]]:
    """Read exactly n JSONL rows and stop without touching row n+1."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index in range(n):
            line = handle.readline()
            require(bool(line), f"JSONL_PREFIX_SHORT:{path}:{index}:{n}")
            require(bool(line.strip()), f"JSONL_PREFIX_EMPTY:{path}:{index + 1}")
            row = json.loads(line)
            require(isinstance(row, dict), f"JSONL_ROW_NOT_OBJECT:{path}:{index + 1}")
            rows.append(row)
    return rows


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def group_indices_by_kickoff(rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    start = 0
    while start < len(rows):
        kickoff = rows[start]["kickoff"]
        end = start + 1
        while end < len(rows) and rows[end]["kickoff"] == kickoff:
            end += 1
        groups.append((start, end))
        start = end
    return groups


def team_state(history: list[tuple[float, float]], method: tuple[str, float | int]) -> tuple[float | None, float | None, int]:
    if not history:
        return None, None, 0
    kind, param = method
    if kind == "w":
        size = int(param)
        xs = history[-size:]
        return (
            sum(x[0] for x in xs) / len(xs),
            sum(x[1] for x in xs) / len(xs),
            len(history),
        )
    if kind == "ewma":
        alpha = float(param)
        ppda, deep = history[0]
        for current_ppda, current_deep in history[1:]:
            ppda = alpha * current_ppda + (1.0 - alpha) * ppda
            deep = alpha * current_deep + (1.0 - alpha) * deep
        return ppda, deep, len(history)
    raise DevelopmentOOFError(f"UNKNOWN_STATE_METHOD:{method}")


def build_raw_features(rows: list[dict[str, Any]], route: str) -> list[list[float | None]]:
    histories: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pending: deque[dict[str, Any]] = deque()
    out: list[list[float | None] | None] = [None] * len(rows)
    for start, end in group_indices_by_kickoff(rows):
        target_kickoff = parse_iso(rows[start]["kickoff"])
        while pending and parse_iso(pending[0]["release_at"]) <= target_kickoff:
            released = pending.popleft()
            histories[released["home_team_id"]].append((float(released["home_ppda"]), float(released["home_deep"])))
            histories[released["away_team_id"]].append((float(released["away_ppda"]), float(released["away_deep"])))
        for index in range(start, end):
            row = rows[index]
            home_history = histories[row["home_team_id"]]
            away_history = histories[row["away_team_id"]]
            if route == "R1_W5":
                hp, hd, hn = team_state(home_history, ("w", 5))
                ap, ad, an = team_state(away_history, ("w", 5))
                out[index] = [hp, ap, hd, ad, math.log1p(hn), math.log1p(an)]
            elif route == "R2_W10":
                hp, hd, hn = team_state(home_history, ("w", 10))
                ap, ad, an = team_state(away_history, ("w", 10))
                out[index] = [hp, ap, hd, ad, math.log1p(hn), math.log1p(an)]
            elif route == "R3_EWMA035":
                hp, hd, hn = team_state(home_history, ("ewma", 0.35))
                ap, ad, an = team_state(away_history, ("ewma", 0.35))
                out[index] = [hp, ap, hd, ad, math.log1p(hn), math.log1p(an)]
            elif route == "R4_W5_W10_STACK":
                hp5, hd5, hn = team_state(home_history, ("w", 5))
                ap5, ad5, an = team_state(away_history, ("w", 5))
                hp10, hd10, _ = team_state(home_history, ("w", 10))
                ap10, ad10, _ = team_state(away_history, ("w", 10))
                out[index] = [hp5, ap5, hd5, ad5, hp10, ap10, hd10, ad10, math.log1p(hn), math.log1p(an)]
            else:
                raise DevelopmentOOFError(f"UNKNOWN_ROUTE:{route}")
        for index in range(start, end):
            pending.append(rows[index])
    require(all(row is not None for row in out), "FEATURE_ROW_MISSING")
    return [list(row) for row in out if row is not None]


def fit_scaler(raw: list[list[float | None]], indices: list[int]) -> tuple[list[float], list[float]]:
    dimensions = len(raw[0])
    means: list[float] = []
    stds: list[float] = []
    for column in range(dimensions):
        values = [float(raw[i][column]) for i in indices if raw[i][column] is not None and math.isfinite(float(raw[i][column]))]
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        std = math.sqrt(variance)
        if std < 1e-9:
            std = 1.0
        means.append(mean)
        stds.append(std)
    return means, stds


def transform_row(row: list[float | None], means: list[float], stds: list[float]) -> list[float]:
    return [((means[index] if value is None else float(value)) - means[index]) / stds[index] for index, value in enumerate(row)]


def softmax_offset(base: list[float], features: list[float], beta: list[list[float]]) -> list[float]:
    eps = 1e-15
    offset_home = math.log(max(float(base[0]), eps) / max(float(base[2]), eps))
    offset_draw = math.log(max(float(base[1]), eps) / max(float(base[2]), eps))
    x = [1.0] + features
    home_logit = offset_home + sum(coef * x[index] for index, coef in enumerate(beta[0]))
    draw_logit = offset_draw + sum(coef * x[index] for index, coef in enumerate(beta[1]))
    away_logit = 0.0
    maximum = max(home_logit, draw_logit, away_logit)
    eh, ed, ea = math.exp(home_logit - maximum), math.exp(draw_logit - maximum), math.exp(away_logit - maximum)
    total = eh + ed + ea
    return [eh / total, ed / total, ea / total]


def loss_grad(beta: list[list[float]], X: list[list[float]], bases: list[list[float]], outcomes: list[int], ridge_c: float) -> tuple[float, list[list[float]]]:
    n = len(X)
    dimensions = len(beta[0])
    loss = 0.0
    grad_home = [0.0] * dimensions
    grad_draw = [0.0] * dimensions
    eps = 1e-15
    for features, base, outcome in zip(X, bases, outcomes):
        probabilities = softmax_offset(base, features, beta)
        loss -= math.log(max(probabilities[outcome], eps))
        x = [1.0] + features
        home_error = probabilities[0] - (1.0 if outcome == 0 else 0.0)
        draw_error = probabilities[1] - (1.0 if outcome == 1 else 0.0)
        for index, value in enumerate(x):
            grad_home[index] += home_error * value
            grad_draw[index] += draw_error * value
    loss /= n
    grad_home = [value / n for value in grad_home]
    grad_draw = [value / n for value in grad_draw]
    regularization = 1.0 / (ridge_c * n)
    for vector in beta:
        for index in range(1, dimensions):
            loss += 0.5 * regularization * vector[index] * vector[index]
    for index in range(1, dimensions):
        grad_home[index] += regularization * beta[0][index]
        grad_draw[index] += regularization * beta[1][index]
    return loss, [grad_home, grad_draw]


def fit_model(X: list[list[float]], bases: list[list[float]], outcomes: list[int], ridge_c: float, max_iter: int = 300) -> tuple[list[list[float]], float, int]:
    dimensions = len(X[0]) + 1
    beta = [[0.0] * dimensions, [0.0] * dimensions]
    loss, gradient = loss_grad(beta, X, bases, outcomes, ridge_c)
    iteration = 0
    for iteration in range(max_iter):
        norm2 = sum(value * value for vector in gradient for value in vector)
        if norm2 < 1e-12:
            break
        step = 1.0
        accepted = False
        while step > 1e-8:
            candidate = [[beta[k][j] - step * gradient[k][j] for j in range(dimensions)] for k in range(2)]
            candidate_loss, candidate_gradient = loss_grad(candidate, X, bases, outcomes, ridge_c)
            if candidate_loss <= loss - 1e-4 * step * norm2:
                beta, loss, gradient = candidate, candidate_loss, candidate_gradient
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
        if max(abs(value) for vector in gradient for value in vector) < 1e-6:
            break
    return beta, loss, iteration + 1


def make_blocks(rows: list[dict[str, Any]], warm_fraction: float = 0.2, nblocks: int = 5) -> tuple[int, list[tuple[int, int]]]:
    groups = group_indices_by_kickoff(rows)
    target = math.ceil(len(rows) * warm_fraction)
    cumulative = 0
    group_index = 0
    while group_index < len(groups) and cumulative < target:
        cumulative += groups[group_index][1] - groups[group_index][0]
        group_index += 1
    warm_end = groups[group_index - 1][1]
    remaining_groups = groups[group_index:]
    target_each = (len(rows) - warm_end) / nblocks
    blocks: list[tuple[int, int]] = []
    block_start = warm_end
    accumulated = 0
    for index, group in enumerate(remaining_groups):
        accumulated += group[1] - group[0]
        groups_left = len(remaining_groups) - index - 1
        if len(blocks) < nblocks - 1 and accumulated >= target_each and groups_left >= (nblocks - len(blocks) - 1):
            blocks.append((block_start, group[1]))
            block_start = group[1]
            accumulated = 0
    blocks.append((block_start, len(rows)))
    require(len(blocks) == nblocks, f"OOF_BLOCK_COUNT:{len(blocks)}")
    return warm_end, blocks


def outcome_index(value: str) -> int:
    mapping = {"home": 0, "draw": 1, "away": 2}
    require(value in mapping, f"BAD_OUTCOME:{value!r}")
    return mapping[value]


def metrics(probabilities: list[list[float]], outcomes: list[int]) -> dict[str, float | int]:
    n = len(outcomes)
    eps = 1e-15
    logloss = sum(-math.log(max(probability[outcome], eps)) for probability, outcome in zip(probabilities, outcomes)) / n
    brier = sum(sum((probability[k] - (1.0 if outcome == k else 0.0)) ** 2 for k in range(3)) for probability, outcome in zip(probabilities, outcomes)) / n
    rps = sum((((probability[0] - (1.0 if outcome == 0 else 0.0)) ** 2) + ((probability[0] + probability[1] - (1.0 if outcome in (0, 1) else 0.0)) ** 2)) / 2.0 for probability, outcome in zip(probabilities, outcomes)) / n
    top1 = sum(max(range(3), key=lambda k: probability[k]) == outcome for probability, outcome in zip(probabilities, outcomes)) / n
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for probability, outcome in zip(probabilities, outcomes):
        top = max(range(3), key=lambda k: probability[k])
        confidence = probability[top]
        correct = 1.0 if top == outcome else 0.0
        bins[min(9, int(confidence * 10))].append((confidence, correct))
    ece = 0.0
    for bucket in bins:
        if bucket:
            confidence = sum(item[0] for item in bucket) / len(bucket)
            accuracy = sum(item[1] for item in bucket) / len(bucket)
            ece += len(bucket) / n * abs(confidence - accuracy)
    return {"n": n, "logloss": logloss, "brier": brier, "rps": rps, "top1": top1, "ece": ece}


def paired_effects(base: list[list[float]], candidate: list[list[float]], outcomes: list[int]) -> list[float]:
    eps = 1e-15
    return [-math.log(max(base[index][outcomes[index]], eps)) - (-math.log(max(candidate[index][outcomes[index]], eps))) for index in range(len(outcomes))]


def bootstrap_mean_ci(values: list[float], repetitions: int, seed: int) -> list[float]:
    generator = random.Random(seed)
    n = len(values)
    samples: list[float] = []
    for _ in range(repetitions):
        samples.append(sum(values[generator.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    low_index = int(math.floor(0.025 * (repetitions - 1)))
    high_index = int(math.ceil(0.975 * (repetitions - 1)))
    return [samples[low_index], samples[high_index]]


def qualify(prereg: dict[str, Any], base_metrics: dict[str, Any], candidate_metrics: dict[str, Any]) -> bool:
    q = prereg["development_protocol"]["qualification"]
    return (float(base_metrics["logloss"]) - float(candidate_metrics["logloss"]) > float(q["logloss_gain_gt"]) and float(candidate_metrics["brier"]) - float(base_metrics["brier"]) <= float(q["candidate_minus_formal_brier_lte"]) and float(candidate_metrics["rps"]) - float(base_metrics["rps"]) <= float(q["candidate_minus_formal_rps_lte"]) and float(candidate_metrics["ece"]) - float(base_metrics["ece"]) <= float(q["candidate_minus_formal_ece_lte"]))


def run(prereg_path: Path, source_dir: Path, prelabel_dir: Path, data_dir: Path, predictions_out: Path, receipt_out: Path) -> dict[str, Any]:
    prereg = read_json(prereg_path)
    require(prereg.get("status") == "DESIGN_LOCKED_PRELABEL", "PREREG_STATUS")
    require(prereg["experiment_budget"]["batch_1"].startswith("development 2024"), "BATCH1_CONTRACT")
    require(prereg["development_protocol"]["selection_season"] == 2024, "DEVELOPMENT_SEASON")
    require(prereg["cohort"]["development"]["n"] == 1752, "DEVELOPMENT_N")
    require(prereg["cohort"]["isolated_test"]["open_once_after_route_freeze"] is True, "ISOLATED_OPEN_RULE")
    require(prereg["formal_v2"]["sole_baseline"] is True and prereg["formal_v2"]["must_remain_unchanged"] is True, "FORMAL_BASELINE_LOCK")
    require(prereg["legacy_v3"]["candidate_code_parameters_weights_inherited"] is False, "LEGACY_INHERITANCE")

    prelabel_receipt = read_json(prelabel_dir / "receipt.json")
    require(prelabel_receipt.get("status") == "N1_DEEP_PPDA_PRELABEL_GATE_PASS", "PRELABEL_STATUS")
    require(prelabel_receipt.get("allowed_next_phase") == "IMPLEMENTING", "PRELABEL_PHASE")
    require(prelabel_receipt.get("isolated_test_opened") is False, "PRELABEL_ISOLATED_OPEN")
    require(prelabel_receipt.get("label_vault_opened") is False, "PRELABEL_LABEL_VAULT_OPEN")
    require(prelabel_receipt.get("formal_v2_head") == prereg["formal_v2"]["head"], "PRELABEL_FORMAL_HEAD")
    require(prelabel_receipt.get("fixture_identity_sha256") == prereg["cohort"]["fixture_identity_sha256"], "PRELABEL_IDENTITY")

    development_n = int(prereg["cohort"]["development"]["n"])
    source_rows = read_jsonl_prefix(source_dir / "state_projection.jsonl", development_n)
    formal_rows = read_jsonl_prefix(prelabel_dir / "formal_v2_baseline_projection.jsonl", development_n)
    fixture_rows = read_jsonl_prefix(data_dir / "data/fixtures.jsonl", development_n)
    labels = read_jsonl_prefix(data_dir / "data/label_vault.jsonl", development_n)

    ids = [row["fixture_id"] for row in source_rows]
    require(len(ids) == development_n and len(set(ids)) == development_n, "DEVELOPMENT_ID_COUNT")
    require(all(int(row["season_start"]) == 2024 for row in source_rows), "SOURCE_NONDEVELOPMENT_ROW")
    require(all(int(row["season_start"]) == 2024 for row in formal_rows), "FORMAL_NONDEVELOPMENT_ROW")
    require(all(int(row["season"]) == 2024 for row in fixture_rows), "FIXTURE_NONDEVELOPMENT_ROW")
    require(ids == [row["fixture_id"] for row in formal_rows] == [row["fixture_id"] for row in fixture_rows] == [row["fixture_id"] for row in labels], "DEVELOPMENT_ROW_ALIGNMENT")
    require(all(row.get("label_read_after_prediction_freeze") is True for row in formal_rows), "BASELINE_FREEZE_ORDER")

    outcomes = [outcome_index(str(row["outcome"])) for row in labels]
    formal_probabilities = [[float(value) for value in row["formal_v2_1x2"]] for row in formal_rows]
    for probability in formal_probabilities:
        require(len(probability) == 3 and all(value >= 0.0 for value in probability) and abs(sum(probability) - 1.0) <= 1e-9, "FORMAL_PROBABILITY")

    route_specs = [(str(route["id"]), float(route["ridge_c"])) for route in prereg["subroutes"]]
    require([item[0] for item in route_specs] == ["R1_W5", "R2_W10", "R3_EWMA035", "R4_W5_W10_STACK"], "ROUTE_SET_DRIFT")
    warm_end, blocks = make_blocks(source_rows, float(prereg["development_protocol"]["warmup_fraction"]), int(prereg["development_protocol"]["oof_blocks"]))
    require(warm_end == 351, f"WARMUP_BOUNDARY:{warm_end}")
    require(blocks == [(351, 632), (632, 916), (916, 1198), (1198, 1479), (1479, 1752)], f"OOF_BLOCK_BOUNDARIES:{blocks}")

    routes: dict[str, Any] = {}
    route_predictions: dict[str, list[list[float]]] = {}
    oof_indices_reference: list[int] | None = None
    for route_id, ridge_c in route_specs:
        raw = build_raw_features(source_rows, route_id)
        oof_indices: list[int] = []
        candidate_probabilities: list[list[float]] = []
        folds: list[dict[str, Any]] = []
        for fold_index, (fold_start, fold_end) in enumerate(blocks):
            train_indices = list(range(0, fold_start))
            validation_indices = list(range(fold_start, fold_end))
            means, stds = fit_scaler(raw, train_indices)
            X_train = [transform_row(raw[index], means, stds) for index in train_indices]
            X_validation = [transform_row(raw[index], means, stds) for index in validation_indices]
            beta, objective, iterations = fit_model(X_train, [formal_probabilities[index] for index in train_indices], [outcomes[index] for index in train_indices], ridge_c, max_iter=300)
            fold_probabilities = [softmax_offset(formal_probabilities[index], features, beta) for index, features in zip(validation_indices, X_validation)]
            candidate_probabilities.extend(fold_probabilities)
            oof_indices.extend(validation_indices)
            folds.append({"fold": fold_index, "train_n": len(train_indices), "validation_n": len(validation_indices), "start_index": fold_start, "end_index_exclusive": fold_end, "first_fixture_id": source_rows[fold_start]["fixture_id"], "last_fixture_id": source_rows[fold_end - 1]["fixture_id"], "objective": objective, "iterations": iterations})
        if oof_indices_reference is None:
            oof_indices_reference = oof_indices
        require(oof_indices == oof_indices_reference, f"OOF_INDEX_DRIFT:{route_id}")
        base_oof = [formal_probabilities[index] for index in oof_indices]
        outcomes_oof = [outcomes[index] for index in oof_indices]
        base_metrics = metrics(base_oof, outcomes_oof)
        candidate_metrics = metrics(candidate_probabilities, outcomes_oof)
        effects = paired_effects(base_oof, candidate_probabilities, outcomes_oof)
        effect_mean = sum(effects) / len(effects)
        effect_sd = math.sqrt(sum((value - effect_mean) ** 2 for value in effects) / (len(effects) - 1))
        route_qualifies = qualify(prereg, base_metrics, candidate_metrics)
        routes[route_id] = {"ridge_c": ridge_c, "qualifies_development": route_qualifies, "formal": base_metrics, "candidate": candidate_metrics, "delta_candidate_minus_formal": {"logloss": float(candidate_metrics["logloss"]) - float(base_metrics["logloss"]), "brier": float(candidate_metrics["brier"]) - float(base_metrics["brier"]), "rps": float(candidate_metrics["rps"]) - float(base_metrics["rps"]), "top1": float(candidate_metrics["top1"]) - float(base_metrics["top1"]), "ece": float(candidate_metrics["ece"]) - float(base_metrics["ece"])}, "formal_minus_candidate_logloss_gain": float(base_metrics["logloss"]) - float(candidate_metrics["logloss"]), "paired_logloss_effect_mean": effect_mean, "paired_logloss_effect_sd": effect_sd, "folds": folds}
        route_predictions[route_id] = candidate_probabilities

    qualifying = [route_id for route_id, result in routes.items() if result["qualifies_development"]]
    require(bool(qualifying), prereg["development_protocol"]["no_qualifying_route_classification"])
    route_order = [route_id for route_id, _ in route_specs]
    selected = max(qualifying, key=lambda route_id: (routes[route_id]["formal_minus_candidate_logloss_gain"], -routes[route_id]["delta_candidate_minus_formal"]["brier"], -routes[route_id]["delta_candidate_minus_formal"]["rps"], -route_order.index(route_id)))
    selected_probabilities = route_predictions[selected]
    oof_indices = oof_indices_reference or []
    outcomes_oof = [outcomes[index] for index in oof_indices]
    base_oof = [formal_probabilities[index] for index in oof_indices]

    per_league: dict[str, Any] = {}
    for league in prereg["metrics"]["group_report"]:
        if league in {"J1", "K1"}:
            per_league[league] = {"status": "NOT_AVAILABLE", "n": 0, "coverage": 0.0, "weight": 0, "matrix_delta": 0}
            continue
        positions = [position for position, index in enumerate(oof_indices) if source_rows[index]["league"] == league]
        league_base = [base_oof[position] for position in positions]
        league_candidate = [selected_probabilities[position] for position in positions]
        league_outcomes = [outcomes_oof[position] for position in positions]
        total_development_league_n = sum(1 for row in source_rows if row["league"] == league)
        league_base_metrics = metrics(league_base, league_outcomes)
        league_candidate_metrics = metrics(league_candidate, league_outcomes)
        per_league[league] = {"status": "DEVELOPMENT_OOF", "n": len(positions), "coverage": 1.0 if positions else 0.0, "development_cohort_coverage": len(positions) / total_development_league_n if total_development_league_n else 0.0, "formal": league_base_metrics, "candidate": league_candidate_metrics, "formal_minus_candidate_logloss_gain": league_base_metrics["logloss"] - league_candidate_metrics["logloss"], "matrix_delta": 0, "score_matrix_metrics": "UNCHANGED_FROM_FORMAL_V2_BY_CONSTRUCTION"}

    effects = paired_effects(base_oof, selected_probabilities, outcomes_oof)
    bootstrap_spec = prereg["metrics"]["uncertainty"]
    bootstrap_ci = bootstrap_mean_ci(effects, int(bootstrap_spec["repetitions"]), int(bootstrap_spec["seed"]))

    predictions_out.parent.mkdir(parents=True, exist_ok=True)
    with predictions_out.open("w", encoding="utf-8") as handle:
        for position, index in enumerate(oof_indices):
            handle.write(json.dumps({"fixture_id": source_rows[index]["fixture_id"], "kickoff": source_rows[index]["kickoff"], "league": source_rows[index]["league"], "season_start": 2024, "route": selected, "formal_v2_1x2": base_oof[position], "candidate_1x2": selected_probabilities[position], "prediction_is_oof": True, "label_in_prediction_file": False, "matrix_delta": 0}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")

    mean_effect = sum(effects) / len(effects)
    receipt = {"schema_version": "football3-nova-n1-deep-ppda-development-oof-receipt-v1", "status": "N1_DEEP_PPDA_DEVELOPMENT_OOF_PASS", "classification": "POSITIVE_SIGNAL_DEVELOPMENT_PASS", "phase": "CANDIDATE_FROZEN", "design_lock_sha256": prelabel_receipt["design_lock_sha256"], "fixture_identity_sha256": prereg["cohort"]["fixture_identity_sha256"], "development_season_start": 2024, "development_n": development_n, "development_label_rows_read": development_n, "isolated_test_label_rows_read": 0, "isolated_test_opened": False, "oof_warmup_n": warm_end, "oof_prediction_n": len(oof_indices), "oof_blocks": [{"start": start, "end_exclusive": end, "n": end - start} for start, end in blocks], "routes": routes, "qualifying_routes": qualifying, "selected_route": selected, "selected_route_frozen": True, "selected_route_research_weight": 0, "selected_route_matrix_delta": 0, "per_league": per_league, "uncertainty": {"method": bootstrap_spec["method"], "repetitions": int(bootstrap_spec["repetitions"]), "seed": int(bootstrap_spec["seed"]), "paired_logloss_effect_mean": mean_effect, "paired_logloss_effect_sd": math.sqrt(sum((value - mean_effect) ** 2 for value in effects) / (len(effects) - 1)), "paired_logloss_effect_ci95": bootstrap_ci}, "oof_predictions_sha256": sha256_file(predictions_out), "formal_v2_head": prereg["formal_v2"]["head"], "formal_v2_sole_baseline": True, "formal_v2_changed": False, "current_changed": False, "production_changed": False, "candidate_activation_allowed": False, "promotion_allowed": False, "old_v3_code_parameters_weights_used": False, "candidate_weight": 0, "matrix_delta": 0, "score_matrix_policy": prereg["model"]["score_matrix_policy"], "allowed_next_phase": "ISOLATED_TEST_ONE_SHOT"}
    receipt_out.parent.mkdir(parents=True, exist_ok=True)
    receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--prelabel-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--predictions-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.prereg, args.source_dir, args.prelabel_dir, args.data_dir, args.predictions_out, args.receipt_out), sort_keys=True))


if __name__ == "__main__":
    main()
