#!/usr/bin/env python3
"""One-shot research-only KOR_KLeague1 baseline versus baseline+round ablation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import statistics
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "football-data/research/kor_round_ablation_r1_contract.json"
AUTH_PATH = ROOT / "football-data/research/kor_round_run_authorization_r1.json"
PIT_PATH = ROOT / "football-data/training_datasets/KOR_KLeague1/point_in_time.csv"
OFFICIAL_PATH = ROOT / "football-data/processed/KOR_KLeague1/official_results.csv"
CLASSES = ["H", "D", "A"]


class ExperimentError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ExperimentError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exact_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def changed_since(base: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base}..HEAD"], cwd=ROOT, text=True
    )
    return [line for line in out.splitlines() if line.strip()]


def load_rows() -> tuple[list[dict[str, str]], dict[tuple[str, str, str, str], int]]:
    with OFFICIAL_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        official = list(csv.DictReader(fh))
    round_map: dict[tuple[str, str, str, str], int] = {}
    for row in official:
        season = row["season"].strip()
        if season not in {"2021", "2022", "2023", "2024", "2025"}:
            continue
        key = (season, row["Date"].strip(), row["HomeTeam"].strip(), row["AwayTeam"].strip())
        require(key not in round_map, f"duplicate official round key: {key}")
        value = row["round"].strip()
        require(value != "", f"missing round: {key}")
        round_id = int(value)
        require(1 <= round_id <= 38, f"round outside 1..38: {key}={round_id}")
        round_map[key] = round_id

    with PIT_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    included = [row for row in rows if row["season"] in {"2021", "2022", "2023", "2024", "2025"}]
    require(all(row["season"] != "2026" for row in included), "partial 2026 season entered experiment")
    require(included, "no included PIT rows")
    for row in included:
        key = (
            row["season"].strip(),
            row["date"].strip(),
            row["home_team"].strip(),
            row["away_team"].strip(),
        )
        require(key in round_map, f"no official round mapping for PIT row: {key}")
        row["_round"] = str(round_map[key])
        require(row["label_result"] in CLASSES, f"invalid target class: {row['label_result']}")
    require(len(round_map) == len(included), "official/PIT included row count mismatch")
    return included, round_map


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


class Encoder:
    def __init__(self, numeric: list[str], categorical: list[str], include_round: bool):
        self.numeric = numeric + (["round_scaled", "round_scaled_squared"] if include_round else [])
        self.categorical = categorical
        self.include_round = include_round
        self.medians: dict[str, float] = {}
        self.means: dict[str, float] = {}
        self.stds: dict[str, float] = {}
        self.categories: dict[str, list[str]] = {}
        self.category_offsets: dict[str, int] = {}
        self.dimension = 0

    @staticmethod
    def _raw_numeric(row: dict[str, str], name: str) -> float | None:
        if name == "round_scaled":
            return int(row["_round"]) / 38.0
        if name == "round_scaled_squared":
            value = int(row["_round"]) / 38.0
            return value * value
        raw = row.get(name, "").strip()
        if raw == "":
            return None
        return float(raw)

    def fit(self, rows: list[dict[str, str]]) -> None:
        for name in self.numeric:
            vals = [v for row in rows if (v := self._raw_numeric(row, name)) is not None]
            med = median(vals)
            completed = [self._raw_numeric(row, name) for row in rows]
            filled = [med if value is None else value for value in completed]
            mean = sum(filled) / len(filled)
            var = sum((value - mean) ** 2 for value in filled) / len(filled)
            std = math.sqrt(var) or 1.0
            self.medians[name] = med
            self.means[name] = mean
            self.stds[name] = std
        offset = 1 + len(self.numeric)
        for name in self.categorical:
            cats = sorted({row[name] for row in rows})
            self.categories[name] = cats
            self.category_offsets[name] = offset
            offset += len(cats)
        self.dimension = offset

    def transform(self, row: dict[str, str]) -> list[float]:
        x = [0.0] * self.dimension
        x[0] = 1.0
        for idx, name in enumerate(self.numeric, start=1):
            value = self._raw_numeric(row, name)
            if value is None:
                value = self.medians[name]
            x[idx] = (value - self.means[name]) / self.stds[name]
        for name in self.categorical:
            value = row[name]
            cats = self.categories[name]
            try:
                local = cats.index(value)
            except ValueError:
                continue
            x[self.category_offsets[name] + local] = 1.0
        return x


def solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    n = len(rhs)
    aug = [matrix[i][:] + [rhs[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        require(abs(aug[pivot][col]) > 1e-12, f"singular ridge system at column {col}")
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        aug[col] = [value / scale for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if factor == 0.0:
                continue
            aug[row] = [a - factor * b for a, b in zip(aug[row], aug[col])]
    return [aug[i][-1] for i in range(n)]


def fit_ridge_softmax(xs: list[list[float]], labels: list[str], ridge: float) -> list[list[float]]:
    d = len(xs[0])
    xtx = [[0.0] * d for _ in range(d)]
    xty = [[0.0] * d for _ in CLASSES]
    for x, label in zip(xs, labels):
        for i, xi in enumerate(x):
            for j in range(i, d):
                xtx[i][j] += xi * x[j]
            for class_idx, cls in enumerate(CLASSES):
                xty[class_idx][i] += xi * (1.0 if label == cls else 0.0)
    for i in range(d):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    for i in range(1, d):
        xtx[i][i] += ridge
    return [solve_linear(xtx, target) for target in xty]


def predict_proba(x: list[float], weights: list[list[float]]) -> list[float]:
    scores = [sum(a * b for a, b in zip(w, x)) for w in weights]
    maximum = max(scores)
    exp_scores = [math.exp(score - maximum) for score in scores]
    total = sum(exp_scores)
    return [value / total for value in exp_scores]


def class_metrics(actual: list[str], probs: list[list[float]]) -> dict[str, float]:
    predicted = [CLASSES[max(range(3), key=lambda i: p[i])] for p in probs]
    accuracy = sum(a == p for a, p in zip(actual, predicted)) / len(actual)
    per_class = {}
    for cls in CLASSES:
        tp = sum(a == cls and p == cls for a, p in zip(actual, predicted))
        fp = sum(a != cls and p == cls for a, p in zip(actual, predicted))
        fn = sum(a == cls and p != cls for a, p in zip(actual, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[cls] = {"precision": precision, "recall": recall, "f1": f1}
    macro_f1 = sum(per_class[cls]["f1"] for cls in CLASSES) / 3.0
    epsilon = 1e-15
    log_loss = -sum(math.log(max(epsilon, probs[i][CLASSES.index(actual[i])])) for i in range(len(actual))) / len(actual)
    brier = 0.0
    rps = 0.0
    for label, p in zip(actual, probs):
        y = [1.0 if label == cls else 0.0 for cls in CLASSES]
        brier += sum((pi - yi) ** 2 for pi, yi in zip(p, y))
        cum_p1 = p[0]
        cum_y1 = y[0]
        cum_p2 = p[0] + p[1]
        cum_y2 = y[0] + y[1]
        rps += ((cum_p1 - cum_y1) ** 2 + (cum_p2 - cum_y2) ** 2) / 2.0
    brier /= len(actual)
    rps /= len(actual)
    draw = per_class["D"]
    return {
        "Accuracy": accuracy,
        "Macro-F1": macro_f1,
        "Draw Precision": draw["precision"],
        "Draw Recall": draw["recall"],
        "Draw F1": draw["f1"],
        "Log Loss": log_loss,
        "Brier": brier,
        "RPS": rps,
    }


def evaluate_fold(all_rows, train_seasons, eval_season, numeric, categorical, include_round, ridge):
    train = [row for row in all_rows if row["season"] in train_seasons]
    evaluate = [row for row in all_rows if row["season"] == eval_season]
    require(train and evaluate, f"empty fold train={train_seasons} eval={eval_season}")
    encoder = Encoder(numeric, categorical, include_round)
    encoder.fit(train)
    xs_train = [encoder.transform(row) for row in train]
    ys_train = [row["label_result"] for row in train]
    weights = fit_ridge_softmax(xs_train, ys_train, ridge)
    probs = [predict_proba(encoder.transform(row), weights) for row in evaluate]
    actual = [row["label_result"] for row in evaluate]
    return {
        "train_seasons": train_seasons,
        "evaluation_season": eval_season,
        "train_rows": len(train),
        "evaluation_rows": len(evaluate),
        "feature_dimension": encoder.dimension,
        "metrics": class_metrics(actual, probs),
    }


def delta(round_metrics, baseline_metrics):
    return {name: round_metrics[name] - baseline_metrics[name] for name in baseline_metrics}


def determine_pass(per_season, thresholds):
    holdout = next(item for item in per_season if item["season"] == "2025")
    d = holdout["delta"]
    checks = {
        "holdout_draw_f1": d["Draw F1"] >= thresholds["holdout_draw_f1_delta_min"],
        "holdout_rps": d["RPS"] <= thresholds["holdout_rps_delta_max"],
        "holdout_accuracy": d["Accuracy"] >= thresholds["holdout_accuracy_delta_min"],
        "holdout_log_loss": d["Log Loss"] <= thresholds["holdout_log_loss_delta_max"],
        "holdout_brier": d["Brier"] <= thresholds["holdout_brier_delta_max"],
        "season_draw_f1_stability": sum(item["delta"]["Draw F1"] >= 0 for item in per_season)
        >= thresholds["seasons_with_nonnegative_draw_f1_delta_min"],
        "season_rps_stability": sum(item["delta"]["RPS"] <= 0 for item in per_season)
        >= thresholds["seasons_with_nonpositive_rps_delta_min"],
    }
    return {"checks": checks, "all_pass": all(checks.values())}


def render_markdown(result):
    lines = [
        "# KOR_KLeague1 round ablation R1", "",
        f"- Status: `{result['status']}`", f"- Exact HEAD: `{result['head']}`",
        f"- Decision: `{result['decision']}`",
        f"- Holdout first accessed in this run: `{result['holdout_access']['first_access_utc']}`", "",
        "| Season | Model | Accuracy | Macro-F1 | Draw P | Draw R | Draw F1 | Log Loss | Brier | RPS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in result["per_season"]:
        for model_key, label in (("baseline", "baseline"), ("baseline_plus_round", "baseline+round")):
            m = item[model_key]["metrics"]
            lines.append(
                f"| {item['season']} | {label} | {m['Accuracy']:.6f} | {m['Macro-F1']:.6f} | "
                f"{m['Draw Precision']:.6f} | {m['Draw Recall']:.6f} | {m['Draw F1']:.6f} | "
                f"{m['Log Loss']:.6f} | {m['Brier']:.6f} | {m['RPS']:.6f} |"
            )
    lines += ["", "## Per-season deltas (baseline+round minus baseline)", ""]
    for item in result["per_season"]:
        d = item["delta"]
        lines.append(
            f"- {item['season']}: Accuracy {d['Accuracy']:+.6f}; Macro-F1 {d['Macro-F1']:+.6f}; "
            f"Draw F1 {d['Draw F1']:+.6f}; Log Loss {d['Log Loss']:+.6f}; "
            f"Brier {d['Brier']:+.6f}; RPS {d['RPS']:+.6f}."
        )
    lines += ["", "## Governance boundary", "",
              "- Research-only; formal_weight remains 0.",
              "- No API-Football, Provider, Secret, or new-data access.",
              "- No formal model, formal data, config, or CURRENT mutation.",
              "- A passing research gate does not authorize promotion, Ready conversion, or merge.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-receipt", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        require(AUTH_PATH.is_file(), "run authorization file missing")
        contract_raw = CONTRACT_PATH.read_bytes()
        contract = json.loads(contract_raw.decode("utf-8"))
        auth = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        receipt = json.loads(args.preflight_receipt.read_text(encoding="utf-8"))
        require(receipt["status"] == "PASS", "current preflight did not pass")
        require(auth["status"] == "AUTHORIZED_ONCE_RESEARCH_ONLY", "authorization status invalid")
        require(auth["contract_sha256"] == sha256_bytes(contract_raw), "contract changed after authorization")
        require(auth["frozen_code_head"] == auth["preflight_head"], "frozen code/preflight head mismatch")
        require(
            changed_since(auth["frozen_code_head"]) == ["football-data/research/kor_round_run_authorization_r1.json"],
            "post-freeze diff is not authorization-only",
        )
        require(auth["preflight_receipt_sha256"] == auth["preflight_receipt"]["receipt_sha256"],
                "embedded preflight receipt hash mismatch")
        require(auth["preflight_receipt"]["holdout_gate"]["application_level_holdout_labels_read"] == 0,
                "prior preflight did not seal holdout")
        require(contract["run_policy"]["maximum_experiment_runs"] == 1, "contract run count changed")
        require(contract["run_policy"]["formal_weight"] == 0, "formal weight changed")

        rows, round_map = load_rows()
        folds = contract["time_split"]["rolling_evaluation"]
        numeric = contract["baseline_features"]["numeric"]
        categorical = contract["baseline_features"]["categorical"]
        ridge = float(contract["model"]["ridge_lambda"])
        per_season = []
        for fold in folds:
            base = evaluate_fold(rows, fold["train"], fold["evaluate"], numeric, categorical, False, ridge)
            plus = evaluate_fold(rows, fold["train"], fold["evaluate"], numeric, categorical, True, ridge)
            per_season.append({
                "season": fold["evaluate"], "role": fold["role"],
                "baseline": base, "baseline_plus_round": plus,
                "delta": delta(plus["metrics"], base["metrics"]),
            })
        gate = determine_pass(per_season, contract["pass_thresholds"])
        decision = "RESEARCH_GATE_PASS_NO_FORMAL_PROMOTION" if gate["all_pass"] else "RESEARCH_GATE_FAIL_NO_PROMOTION"
        from datetime import datetime, timezone
        observed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        result = {
            "schema_version": "KOR-ROUND-ABLATION-R1-RESULT-1.0",
            "status": "PASS_EXECUTION_COMPLETE", "head": exact_head(),
            "contract_sha256": sha256_bytes(contract_raw),
            "authorization_sha256": sha256_bytes(AUTH_PATH.read_bytes()),
            "decision": decision, "gate": gate, "per_season": per_season,
            "data": {"included_rows": len(rows), "round_mapped_rows": len(round_map),
                     "included_seasons": ["2021", "2022", "2023", "2024", "2025"],
                     "excluded_partial_season": "2026"},
            "holdout_access": {"season": "2025", "first_access_utc": observed,
                               "access_after_frozen_contract_and_preflight_pass": True,
                               "access_count_for_authorized_experiment": 1},
            "network": {"api_football_requests": 0, "provider_requests": 0,
                        "secret_access": False, "new_data_collection": False},
            "formal_boundary": {"formal_weight": 0, "model_diff": 0, "formal_data_diff": 0,
                                "config_diff": 0, "current_diff": 0,
                                "formal_promotion_authorized": False,
                                "ready_authorized": False, "merge_authorized": False},
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "ablation_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (args.output_dir / "ablation_report.md").write_text(render_markdown(result), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ExperimentError, KeyError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
