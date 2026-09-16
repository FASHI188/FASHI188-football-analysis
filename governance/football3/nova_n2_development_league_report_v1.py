from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OOF_PATH = HERE / "nova_n2_npxg_development_oof_v1.py"
spec = importlib.util.spec_from_file_location("n2oof", OOF_PATH)
n2oof = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(n2oof)

BIG5 = ["EPL", "Bundesliga", "La_liga", "Ligue_1", "Serie_A"]
ALL_REPORT_GROUPS = BIG5 + ["J1", "K1"]
TOL = 1e-12


class ReportError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportError(message)


def read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(obj, dict), f"NOT_OBJECT:{path}")
    return obj


def close(left: float, right: float, tol: float = TOL) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tol)


def compare_metrics(actual: dict[str, Any], expected: dict[str, Any], label: str) -> None:
    require(int(actual["n"]) == int(expected["n"]), f"{label}:N_DRIFT")
    for key in ("logloss", "brier", "rps", "top1", "ece"):
        require(close(float(actual[key]), float(expected[key])), f"{label}:{key}:DRIFT")


def route_predictions(
    rows: list[dict[str, Any]],
    bases: list[list[float]],
    outcomes: list[int],
    blocks: list[tuple[int, int]],
    route: str,
) -> list[list[float] | None]:
    _kind, _parameter, ridge_c = n2oof.ROUTES[route]
    raw = n2oof.build_raw_features(rows, route)
    predictions: list[list[float] | None] = [None] * len(rows)
    for start, end in blocks:
        train_indices = list(range(start))
        means, stds = n2oof.fit_scaler(raw, train_indices)
        train_features = [n2oof.transform(raw[index], means, stds) for index in train_indices]
        train_bases = [bases[index] for index in train_indices]
        train_outcomes = [outcomes[index] for index in train_indices]
        model = n2oof.fit_model(train_features, train_bases, train_outcomes, float(ridge_c))
        for index in range(start, end):
            predictions[index] = n2oof.softmax_offset(
                bases[index], n2oof.transform(raw[index], means, stds), model
            )
    return predictions


def unavailable_group() -> dict[str, Any]:
    return {
        "status": "NOT_AVAILABLE",
        "n": 0,
        "source_n": 0,
        "coverage": 0.0,
        "weight": 0,
        "matrix_delta": 0,
        "formal": None,
        "candidate": None,
        "formal_minus_candidate_logloss": None,
        "paired_bootstrap_95ci": None,
    }


def run(
    source_path: Path,
    baseline_path: Path,
    labels_path: Path,
    frozen_result_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    frozen = read_json(frozen_result_path)
    require(frozen.get("status") == "N2_DEVELOPMENT_OOF_COMPLETE", "FROZEN_RESULT_STATUS")
    require(frozen.get("isolated_2023_labels_read") == 0, "FROZEN_ISOLATED_LABEL_READ")
    require(frozen.get("candidate_weight") == 0 and frozen.get("matrix_delta") == 0, "FROZEN_WEIGHT_DRIFT")

    rows = n2oof.read_development_prefix(source_path)
    rows.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    require(len(rows) == n2oof.EXPECTED_N, "SOURCE_COUNT")
    require({str(row["league"]) for row in rows} == set(BIG5), "SOURCE_LEAGUE_SET")

    baseline_index = {row["n2_fixture_id"]: row for row in n2oof.read_jsonl(baseline_path)}
    label_index = {row["fixture_id"]: row for row in n2oof.read_jsonl(labels_path)}
    require(len(baseline_index) == n2oof.EXPECTED_N, "BASELINE_COUNT")
    require(len(label_index) == n2oof.EXPECTED_N, "LABEL_COUNT")

    bases: list[list[float]] = []
    outcomes: list[int] = []
    for row in rows:
        baseline = baseline_index.get(row["fixture_id"])
        label = label_index.get(row["fixture_id"])
        require(baseline is not None and label is not None, f"JOIN_MISSING:{row['fixture_id']}")
        bases.append([float(value) for value in baseline["formal_v2_1x2"]])
        outcomes.append(n2oof.outcome_index(str(label["outcome"])))

    warm_end, blocks = n2oof.make_blocks(rows)
    evaluation_indices = [index for start, end in blocks for index in range(start, end)]
    require(warm_end == int(frozen["warmup_end"]), "WARMUP_DRIFT")
    require(len(evaluation_indices) == int(frozen["oof_evaluation_n"]), "EVAL_N_DRIFT")

    source_counts = Counter(str(row["league"]) for row in rows)
    eval_counts = Counter(str(rows[index]["league"]) for index in evaluation_indices)
    frozen_routes = {row["route"]: row for row in frozen["routes"]}
    require(set(frozen_routes) == set(n2oof.ROUTES), "FROZEN_ROUTE_SET")

    route_reports: dict[str, Any] = {}
    for route_index, route in enumerate(n2oof.ROUTES):
        predictions = route_predictions(rows, bases, outcomes, blocks, route)
        candidate_eval = [predictions[index] for index in evaluation_indices]
        require(all(probability is not None for probability in candidate_eval), f"{route}:PREDICTION_MISSING")
        candidate_eval_clean = [list(probability) for probability in candidate_eval if probability is not None]
        base_eval = [bases[index] for index in evaluation_indices]
        outcome_eval = [outcomes[index] for index in evaluation_indices]
        pooled_formal = n2oof.metrics(base_eval, outcome_eval)
        pooled_candidate = n2oof.metrics(candidate_eval_clean, outcome_eval)
        frozen_route = frozen_routes[route]
        compare_metrics(pooled_formal, frozen_route["formal"], f"{route}:FORMAL")
        compare_metrics(pooled_candidate, frozen_route["candidate"], f"{route}:CANDIDATE")
        pooled_gain = float(pooled_formal["logloss"]) - float(pooled_candidate["logloss"])
        require(close(pooled_gain, frozen_route["formal_minus_candidate_logloss"]), f"{route}:LL_GAIN_DRIFT")

        groups: dict[str, Any] = {}
        for league_index, league in enumerate(BIG5):
            selected_positions = [
                position
                for position, row_index in enumerate(evaluation_indices)
                if str(rows[row_index]["league"]) == league
            ]
            require(selected_positions, f"{route}:{league}:NO_EVAL_ROWS")
            formal_probabilities = [base_eval[position] for position in selected_positions]
            candidate_probabilities = [candidate_eval_clean[position] for position in selected_positions]
            league_outcomes = [outcome_eval[position] for position in selected_positions]
            formal_metrics = n2oof.metrics(formal_probabilities, league_outcomes)
            candidate_metrics = n2oof.metrics(candidate_probabilities, league_outcomes)
            effects = [
                -math.log(max(formal_probabilities[index][league_outcomes[index]], 1e-15))
                + math.log(max(candidate_probabilities[index][league_outcomes[index]], 1e-15))
                for index in range(len(league_outcomes))
            ]
            groups[league] = {
                "status": "AVAILABLE",
                "n": len(selected_positions),
                "source_n": int(source_counts[league]),
                "coverage": len(selected_positions) / int(source_counts[league]),
                "weight": 0,
                "matrix_delta": 0,
                "formal": formal_metrics,
                "candidate": candidate_metrics,
                "formal_minus_candidate_logloss": float(formal_metrics["logloss"])
                - float(candidate_metrics["logloss"]),
                "paired_bootstrap_95ci": n2oof.bootstrap_ci(
                    effects,
                    repetitions=5000,
                    seed=620231 + route_index * 100 + league_index,
                ),
            }
        groups["J1"] = unavailable_group()
        groups["K1"] = unavailable_group()
        require(set(groups) == set(ALL_REPORT_GROUPS), f"{route}:REPORT_GROUP_SET")

        route_reports[route] = {
            "pooled": {
                "formal": pooled_formal,
                "candidate": pooled_candidate,
                "formal_minus_candidate_logloss": pooled_gain,
                "paired_bootstrap_95ci": frozen_route["paired_bootstrap_95ci"],
                "qualified": bool(frozen_route["qualified"]),
            },
            "groups": groups,
        }

    report = {
        "schema_version": "football3-nova-n2-development-league-report-v1",
        "status": "N2_DEVELOPMENT_LEAGUE_REPORT_PASS",
        "classification": frozen["classification"],
        "best_signal_route": frozen["best_signal_route"],
        "selected_route": frozen["selected_route"],
        "completed_matches_only": True,
        "development_season": 2022,
        "development_source_n": len(rows),
        "development_oof_n": len(evaluation_indices),
        "development_oof_coverage": len(evaluation_indices) / len(rows),
        "source_counts": dict(sorted(source_counts.items())),
        "evaluation_counts": dict(sorted(eval_counts.items())),
        "isolated_2023_labels_read": 0,
        "j1": unavailable_group(),
        "k1": unavailable_group(),
        "score_matrix": {
            "formal_v2_matrix_unchanged": True,
            "candidate_matrix_delta": 0,
            "candidate_exact_score_metrics": "UNCHANGED_BY_CONSTRUCTION",
        },
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "routes": route_reports,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--frozen-result", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.baseline, args.labels, args.frozen_result, args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
