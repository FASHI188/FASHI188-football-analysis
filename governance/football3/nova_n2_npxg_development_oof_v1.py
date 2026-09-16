from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

EXPECTED_N = 1826
ROUTES = {
    "R1_W5": ("w", 5, 0.25),
    "R2_W10": ("w", 10, 0.25),
    "R3_EWMA035": ("ewma", 0.35, 0.25),
    "R4_W5_W10_STACK": ("stack", None, 0.10),
}


class DevelopmentOOFError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DevelopmentOOFError(message)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def read_development_prefix(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index in range(EXPECTED_N):
            line = handle.readline()
            require(bool(line), f"SOURCE_PREFIX_SHORT:{index}")
            row = json.loads(line)
            require(int(row["season_start"]) == 2022, f"SOURCE_NON_DEVELOPMENT:{index}")
            rows.append(row)
    return rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                require(isinstance(row, dict), f"ROW_NOT_OBJECT:{path}")
                rows.append(row)
    return rows


def kickoff_groups(rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end]["kickoff"] == rows[start]["kickoff"]:
            end += 1
        groups.append((start, end))
        start = end
    return groups


def team_state(
    history: list[tuple[float, float]], kind: str, parameter: float | int
) -> tuple[float | None, float | None, int]:
    if not history:
        return None, None, 0
    if kind == "w":
        sample = history[-int(parameter) :]
        return (
            sum(row[0] for row in sample) / len(sample),
            sum(row[1] for row in sample) / len(sample),
            len(history),
        )
    if kind == "ewma":
        alpha = float(parameter)
        attack, defense = history[0]
        for current_attack, current_defense in history[1:]:
            attack = alpha * current_attack + (1.0 - alpha) * attack
            defense = alpha * current_defense + (1.0 - alpha) * defense
        return attack, defense, len(history)
    raise DevelopmentOOFError(f"UNKNOWN_STATE:{kind}")


def build_raw_features(rows: list[dict[str, Any]], route: str) -> list[list[float | None]]:
    kind, parameter, _ridge = ROUTES[route]
    histories: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pending: deque[dict[str, Any]] = deque()
    output: list[list[float | None] | None] = [None] * len(rows)

    for start, end in kickoff_groups(rows):
        target_kickoff = parse_iso(rows[start]["kickoff"])
        while pending and parse_iso(pending[0]["release_at"]) <= target_kickoff:
            released = pending.popleft()
            histories[released["home_team_id"]].append(
                (float(released["home_npxg"]), float(released["home_npxga"]))
            )
            histories[released["away_team_id"]].append(
                (float(released["away_npxg"]), float(released["away_npxga"]))
            )

        for index in range(start, end):
            row = rows[index]
            home_history = histories[row["home_team_id"]]
            away_history = histories[row["away_team_id"]]
            if kind in {"w", "ewma"}:
                home_attack, home_defense, home_n = team_state(home_history, kind, parameter)
                away_attack, away_defense, away_n = team_state(away_history, kind, parameter)
                output[index] = [
                    home_attack,
                    home_defense,
                    away_attack,
                    away_defense,
                    math.log1p(home_n),
                    math.log1p(away_n),
                ]
            else:
                ha5, hd5, home_n = team_state(home_history, "w", 5)
                aa5, ad5, away_n = team_state(away_history, "w", 5)
                ha10, hd10, _ = team_state(home_history, "w", 10)
                aa10, ad10, _ = team_state(away_history, "w", 10)
                output[index] = [
                    ha5,
                    hd5,
                    aa5,
                    ad5,
                    ha10,
                    hd10,
                    aa10,
                    ad10,
                    math.log1p(home_n),
                    math.log1p(away_n),
                ]
        for index in range(start, end):
            pending.append(rows[index])

    require(all(row is not None for row in output), "FEATURE_ROW_MISSING")
    return [list(row) for row in output if row is not None]


def fit_scaler(raw: list[list[float | None]], indices: list[int]) -> tuple[list[float], list[float]]:
    means: list[float] = []
    stds: list[float] = []
    for column in range(len(raw[0])):
        values = [
            float(raw[index][column])
            for index in indices
            if raw[index][column] is not None and math.isfinite(float(raw[index][column]))
        ]
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        std = math.sqrt(variance)
        means.append(mean)
        stds.append(std if std >= 1e-9 else 1.0)
    return means, stds


def transform(row: list[float | None], means: list[float], stds: list[float]) -> list[float]:
    return [
        ((means[index] if value is None else float(value)) - means[index]) / stds[index]
        for index, value in enumerate(row)
    ]


def softmax_offset(base: list[float], features: list[float], beta: list[list[float]]) -> list[float]:
    eps = 1e-15
    vector = [1.0] + features
    home_logit = math.log(max(base[0], eps) / max(base[2], eps)) + sum(
        coefficient * vector[index] for index, coefficient in enumerate(beta[0])
    )
    draw_logit = math.log(max(base[1], eps) / max(base[2], eps)) + sum(
        coefficient * vector[index] for index, coefficient in enumerate(beta[1])
    )
    maximum = max(home_logit, draw_logit, 0.0)
    home = math.exp(home_logit - maximum)
    draw = math.exp(draw_logit - maximum)
    away = math.exp(-maximum)
    total = home + draw + away
    return [home / total, draw / total, away / total]


def loss_gradient(
    beta: list[list[float]],
    features: list[list[float]],
    bases: list[list[float]],
    outcomes: list[int],
    ridge_c: float,
) -> tuple[float, list[list[float]]]:
    n = len(features)
    dimensions = len(beta[0])
    gradient = [[0.0] * dimensions, [0.0] * dimensions]
    loss = 0.0
    for row, base, outcome in zip(features, bases, outcomes):
        probabilities = softmax_offset(base, row, beta)
        loss -= math.log(max(probabilities[outcome], 1e-15))
        vector = [1.0] + row
        for klass in (0, 1):
            error = probabilities[klass] - (1.0 if outcome == klass else 0.0)
            for index, value in enumerate(vector):
                gradient[klass][index] += error * value
    loss /= n
    gradient = [[value / n for value in row] for row in gradient]
    regularization = 1.0 / (ridge_c * n)
    for klass in (0, 1):
        for index in range(1, dimensions):
            loss += 0.5 * regularization * beta[klass][index] ** 2
            gradient[klass][index] += regularization * beta[klass][index]
    return loss, gradient


def fit_model(
    features: list[list[float]], bases: list[list[float]], outcomes: list[int], ridge_c: float
) -> list[list[float]]:
    dimensions = len(features[0]) + 1
    beta = [[0.0] * dimensions, [0.0] * dimensions]
    loss, gradient = loss_gradient(beta, features, bases, outcomes, ridge_c)
    for _ in range(300):
        norm_squared = sum(value * value for row in gradient for value in row)
        if norm_squared < 1e-12:
            break
        step = 1.0
        accepted = False
        while step > 1e-8:
            candidate = [
                [beta[klass][index] - step * gradient[klass][index] for index in range(dimensions)]
                for klass in (0, 1)
            ]
            candidate_loss, candidate_gradient = loss_gradient(
                candidate, features, bases, outcomes, ridge_c
            )
            if candidate_loss <= loss - 1e-4 * step * norm_squared:
                beta, loss, gradient = candidate, candidate_loss, candidate_gradient
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
        if max(abs(value) for row in gradient for value in row) < 1e-6:
            break
    return beta


def make_blocks(
    rows: list[dict[str, Any]], warm_fraction: float = 0.2, block_count: int = 5
) -> tuple[int, list[tuple[int, int]]]:
    groups = kickoff_groups(rows)
    warm_target = math.ceil(len(rows) * warm_fraction)
    cumulative = 0
    group_index = 0
    while group_index < len(groups) and cumulative < warm_target:
        cumulative += groups[group_index][1] - groups[group_index][0]
        group_index += 1
    warm_end = groups[group_index - 1][1]
    remaining_groups = groups[group_index:]
    target_each = (len(rows) - warm_end) / block_count
    blocks: list[tuple[int, int]] = []
    block_start = warm_end
    accumulated = 0
    for index, group in enumerate(remaining_groups):
        accumulated += group[1] - group[0]
        groups_left = len(remaining_groups) - index - 1
        if (
            len(blocks) < block_count - 1
            and accumulated >= target_each
            and groups_left >= block_count - len(blocks) - 1
        ):
            blocks.append((block_start, group[1]))
            block_start = group[1]
            accumulated = 0
    blocks.append((block_start, len(rows)))
    require(len(blocks) == block_count, f"OOF_BLOCK_COUNT:{len(blocks)}")
    return warm_end, blocks


def outcome_index(value: str) -> int:
    mapping = {"home": 0, "draw": 1, "away": 2}
    require(value in mapping, f"BAD_OUTCOME:{value}")
    return mapping[value]


def metrics(probabilities: list[list[float]], outcomes: list[int]) -> dict[str, float | int]:
    n = len(outcomes)
    logloss = sum(
        -math.log(max(probability[outcome], 1e-15))
        for probability, outcome in zip(probabilities, outcomes)
    ) / n
    brier = sum(
        sum((probability[index] - (1.0 if outcome == index else 0.0)) ** 2 for index in range(3))
        for probability, outcome in zip(probabilities, outcomes)
    ) / n
    rps = sum(
        (
            (probability[0] - (1.0 if outcome == 0 else 0.0)) ** 2
            + (
                probability[0]
                + probability[1]
                - (1.0 if outcome in (0, 1) else 0.0)
            )
            ** 2
        )
        / 2.0
        for probability, outcome in zip(probabilities, outcomes)
    ) / n
    top1 = sum(
        max(range(3), key=lambda index: probability[index]) == outcome
        for probability, outcome in zip(probabilities, outcomes)
    ) / n
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for probability, outcome in zip(probabilities, outcomes):
        predicted = max(range(3), key=lambda index: probability[index])
        confidence = probability[predicted]
        bins[min(9, int(confidence * 10))].append(
            (confidence, 1.0 if predicted == outcome else 0.0)
        )
    ece = sum(
        len(bucket)
        / n
        * abs(
            sum(confidence for confidence, _ in bucket) / len(bucket)
            - sum(correct for _, correct in bucket) / len(bucket)
        )
        for bucket in bins
        if bucket
    )
    return {"n": n, "logloss": logloss, "brier": brier, "rps": rps, "top1": top1, "ece": ece}


def bootstrap_ci(values: list[float], repetitions: int = 5000, seed: int = 620231) -> list[float]:
    generator = random.Random(seed)
    n = len(values)
    samples = sorted(
        sum(values[generator.randrange(n)] for _ in range(n)) / n for _ in range(repetitions)
    )
    return [samples[int(0.025 * (repetitions - 1))], samples[math.ceil(0.975 * (repetitions - 1))]]


def run(source_path: Path, baseline_path: Path, labels_path: Path, out_dir: Path) -> dict[str, Any]:
    rows = read_development_prefix(source_path)
    rows.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    require(len(rows) == EXPECTED_N, "DEVELOPMENT_COUNT")

    baseline_index = {row["n2_fixture_id"]: row for row in read_jsonl(baseline_path)}
    label_index = {row["fixture_id"]: row for row in read_jsonl(labels_path)}
    require(len(baseline_index) == EXPECTED_N, "BASELINE_COUNT")
    require(len(label_index) == EXPECTED_N, "LABEL_COUNT")

    bases: list[list[float]] = []
    outcomes: list[int] = []
    for row in rows:
        baseline = baseline_index.get(row["fixture_id"])
        label = label_index.get(row["fixture_id"])
        require(baseline is not None and label is not None, f"JOIN_MISSING:{row['fixture_id']}")
        bases.append([float(value) for value in baseline["formal_v2_1x2"]])
        outcomes.append(outcome_index(str(label["outcome"])))

    warm_end, blocks = make_blocks(rows)
    evaluation_indices = [index for start, end in blocks for index in range(start, end)]
    base_evaluation = [bases[index] for index in evaluation_indices]
    outcome_evaluation = [outcomes[index] for index in evaluation_indices]
    formal_metrics = metrics(base_evaluation, outcome_evaluation)

    route_results: list[dict[str, Any]] = []
    for route, (_kind, _parameter, ridge_c) in ROUTES.items():
        raw = build_raw_features(rows, route)
        predictions: list[list[float] | None] = [None] * len(rows)
        for start, end in blocks:
            train_indices = list(range(start))
            means, stds = fit_scaler(raw, train_indices)
            train_features = [transform(raw[index], means, stds) for index in train_indices]
            train_bases = [bases[index] for index in train_indices]
            train_outcomes = [outcomes[index] for index in train_indices]
            model = fit_model(train_features, train_bases, train_outcomes, float(ridge_c))
            for index in range(start, end):
                predictions[index] = softmax_offset(
                    bases[index], transform(raw[index], means, stds), model
                )

        candidate_probabilities = [predictions[index] for index in evaluation_indices]
        require(all(row is not None for row in candidate_probabilities), "PREDICTION_MISSING")
        candidate = [list(row) for row in candidate_probabilities if row is not None]
        candidate_metrics = metrics(candidate, outcome_evaluation)
        effects = [
            -math.log(max(base_evaluation[index][outcome_evaluation[index]], 1e-15))
            + math.log(max(candidate[index][outcome_evaluation[index]], 1e-15))
            for index in range(len(candidate))
        ]
        logloss_gain = float(formal_metrics["logloss"]) - float(candidate_metrics["logloss"])
        brier_delta = float(candidate_metrics["brier"]) - float(formal_metrics["brier"])
        rps_delta = float(candidate_metrics["rps"]) - float(formal_metrics["rps"])
        ece_delta = float(candidate_metrics["ece"]) - float(formal_metrics["ece"])
        qualified = (
            logloss_gain > 0.0
            and brier_delta <= 0.0005
            and rps_delta <= 0.0005
            and ece_delta <= 0.005
        )
        route_results.append(
            {
                "route": route,
                "formal": formal_metrics,
                "candidate": candidate_metrics,
                "formal_minus_candidate_logloss": logloss_gain,
                "candidate_minus_formal_brier": brier_delta,
                "candidate_minus_formal_rps": rps_delta,
                "candidate_minus_formal_ece": ece_delta,
                "paired_bootstrap_95ci": bootstrap_ci(effects),
                "qualified": qualified,
            }
        )

    qualified_routes = [row for row in route_results if row["qualified"]]
    best_logloss_gain = max(row["formal_minus_candidate_logloss"] for row in route_results)
    if qualified_routes:
        selected = max(
            qualified_routes,
            key=lambda row: (
                row["formal_minus_candidate_logloss"],
                -row["candidate_minus_formal_brier"],
                -row["candidate_minus_formal_rps"],
            ),
        )
        classification = "DEVELOPMENT_ROUTE_QUALIFIED"
    elif best_logloss_gain > 0.0:
        selected = max(route_results, key=lambda row: row["formal_minus_candidate_logloss"])
        classification = "POSITIVE_SIGNAL_DEVELOPMENT_ONLY"
    else:
        selected = max(route_results, key=lambda row: row["formal_minus_candidate_logloss"])
        classification = "FAIL_RESEARCH_DIRECTION"

    result = {
        "schema_version": "football3-nova-n2-npxg-development-oof-v1",
        "status": "N2_DEVELOPMENT_OOF_COMPLETE",
        "classification": classification,
        "completed_matches_only": True,
        "development_season": 2022,
        "development_n": EXPECTED_N,
        "oof_evaluation_n": len(evaluation_indices),
        "warmup_end": warm_end,
        "isolated_2023_labels_read": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "selected_route": selected["route"] if qualified_routes else None,
        "best_signal_route": selected["route"],
        "routes": route_results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "development_oof_result.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    freeze = {
        "status": "N2_ROUTE_FROZEN" if qualified_routes else "N2_NO_ROUTE_FROZEN",
        "selected_route": result["selected_route"],
        "classification": classification,
        "isolated_open_allowed": bool(qualified_routes),
        "isolated_2023_labels_read": 0,
    }
    (out_dir / "route_freeze.json").write_text(
        json.dumps(freeze, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.baseline, args.labels, args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
