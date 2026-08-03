#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEASONS = {
    "2021/22": ("2021-22", "2122"),
    "2022/23": ("2022-23", "2223"),
    "2023/24": ("2023-24", "2324"),
    "2024/25": ("2024-25", "2425"),
    "2025/26": ("2025-26", "2526"),
}
TARGETS = ("2023/24", "2024/25", "2025/26")
ALIASES = {
    "manchesterunited": "manutd", "manunited": "manutd", "manutd": "manutd",
    "manchestercity": "mancity", "mancity": "mancity",
    "tottenhamhotspur": "tottenham", "tottenham": "tottenham", "spurs": "tottenham",
    "nottinghamforest": "nottmforest", "nottmforest": "nottmforest",
    "wolverhamptonwanderers": "wolves", "wolverhampton": "wolves", "wolves": "wolves",
    "newcastleunited": "newcastle", "newcastle": "newcastle",
    "westhamunited": "westham", "westham": "westham",
    "brightonandhovealbion": "brighton", "brighton": "brighton",
    "sheffieldunited": "sheffieldutd", "sheffieldutd": "sheffieldutd",
    "leicestercity": "leicester", "leicester": "leicester",
    "ipswichtown": "ipswich", "ipswich": "ipswich",
    "lutontown": "luton", "luton": "luton",
    "leedsunited": "leeds", "leeds": "leeds",
    "norwichcity": "norwich", "norwich": "norwich",
}
MARKET_FEATURES = ["fair_home", "fair_draw", "fair_away", "home_away_balance", "draw_vs_side_margin", "market_entropy"]
QUALITY_BASE = [
    "regular_absent_count", "regular_absent_goalkeeper", "regular_absent_defender",
    "regular_absent_midfielder", "regular_absent_forward", "lineup_prior_minutes_10_sum",
    "lineup_prior_starts_10_sum", "lineup_prior_bps_per90_mean",
    "lineup_prior_xgi_per90_sum", "lineup_prior_xgc_per90_mean",
    "lineup_prior_defensive_per90_sum", "lineup_low_history_count",
    "goalkeeper_prior_saves_per90", "goalkeeper_prior_goals_conceded_per90",
]
QUALITY_FEATURES = [f"{side}_{name}" for name in QUALITY_BASE for side in ("home", "away")] + [f"diff_{name}" for name in QUALITY_BASE]
POSITION_MAP = {"GK": "goalkeeper", "DEF": "defender", "MID": "midfielder", "FWD": "forward"}


def hf(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "epl-lineup-quality-r2"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def read_rows(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def number(value: object) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


def truth(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def norm(value: object) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value).lower().replace("&", "and").replace("'", ""))
    return ALIASES.get(key, key)


def date_fd(value: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(value)


def first_complete(row: dict[str, str], groups: list[tuple[str, str, str]]) -> tuple[float, float, float] | None:
    for h, d, a in groups:
        values = (number(row.get(h)), number(row.get(d)), number(row.get(a)))
        if min(values) > 1.0:
            return values
    return None


def fair_probabilities(odds: tuple[float, float, float]) -> tuple[float, float, float]:
    inv = [1.0 / x for x in odds]
    total = sum(inv)
    return tuple(x / total for x in inv)  # type: ignore[return-value]


def load_markets(ledger: list[dict[str, object]]) -> dict[tuple[str, str, str, str], dict[str, float]]:
    index: dict[tuple[str, str, str, str], dict[str, float]] = {}
    for season, (_, code) in SEASONS.items():
        url = f"https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
        data = fetch(url)
        rows = read_rows(data)
        ledger.append({"source": "Football-Data", "season": season, "url": url, "sha256": hf(data), "bytes": len(data), "rows": len(rows)})
        for row in rows:
            odds = first_complete(row, [
                ("AvgCH", "AvgCD", "AvgCA"), ("B365CH", "B365CD", "B365CA"),
                ("MaxCH", "MaxCD", "MaxCA"), ("PSCH", "PSCD", "PSCA"),
            ])
            if odds is None:
                continue
            try:
                day = date_fd(row.get("Date", ""))
            except ValueError:
                continue
            ph, pd, pa = fair_probabilities(odds)
            entropy = -sum(p * math.log(max(p, 1e-15)) for p in (ph, pd, pa))
            index[(season, day, norm(row.get("HomeTeam", "")), norm(row.get("AwayTeam", "")))] = {
                "fair_home": ph, "fair_draw": pd, "fair_away": pa,
                "home_away_balance": abs(ph - pa),
                "draw_vs_side_margin": pd - max(ph, pa),
                "market_entropy": entropy,
            }
    return index


def player_summary(history: deque[dict[str, float]]) -> dict[str, float]:
    values = list(history)[-10:]
    minutes = sum(r["minutes"] for r in values)
    starts = sum(r["starts"] for r in values)
    matches = len(values)
    scale = 90.0 / max(minutes, 90.0)
    return {
        "matches": float(matches), "minutes": minutes, "starts": starts,
        "bps_per90": sum(r["bps"] for r in values) * scale,
        "xgi_per90": sum(r["xgi"] for r in values) * scale,
        "xgc_per90": sum(r["xgc"] for r in values) * scale,
        "defensive_per90": sum(r["defensive"] for r in values) * scale,
        "saves_per90": sum(r["saves"] for r in values) * scale,
        "goals_conceded_per90": sum(r["goals_conceded"] for r in values) * scale,
    }


def side_features(
    team: str,
    starter_rows: list[dict[str, str]],
    squad_rows: list[dict[str, str]],
    histories: dict[int, deque[dict[str, float]]],
) -> dict[str, float]:
    summaries: dict[int, dict[str, float]] = {}
    positions: dict[int, str] = {}
    for row in squad_rows:
        pid = int(number(row.get("element")))
        if pid <= 0:
            continue
        summaries[pid] = player_summary(histories[pid])
        positions[pid] = str(row.get("position", ""))
    ranking = sorted(summaries, key=lambda pid: (summaries[pid]["starts"], summaries[pid]["minutes"], pid), reverse=True)
    regulars = set(ranking[:11])
    starters = {int(number(row.get("element"))) for row in starter_rows if int(number(row.get("element"))) > 0}
    absent = regulars - starters
    starter_summaries = [summaries.get(pid, player_summary(deque())) for pid in starters]
    with_minutes = [s for s in starter_summaries if s["minutes"] > 0]
    gk_ids = [pid for pid in starters if positions.get(pid) == "GK"]
    gk = summaries.get(gk_ids[0], player_summary(deque())) if gk_ids else player_summary(deque())
    result = {
        "regular_absent_count": float(len(absent)),
        "lineup_prior_minutes_10_sum": sum(s["minutes"] for s in starter_summaries),
        "lineup_prior_starts_10_sum": sum(s["starts"] for s in starter_summaries),
        "lineup_prior_bps_per90_mean": sum(s["bps_per90"] for s in with_minutes) / max(len(with_minutes), 1),
        "lineup_prior_xgi_per90_sum": sum(s["xgi_per90"] for s in starter_summaries),
        "lineup_prior_xgc_per90_mean": sum(s["xgc_per90"] for s in with_minutes) / max(len(with_minutes), 1),
        "lineup_prior_defensive_per90_sum": sum(s["defensive_per90"] for s in starter_summaries),
        "lineup_low_history_count": float(sum(s["matches"] < 3 or s["minutes"] < 180 for s in starter_summaries)),
        "goalkeeper_prior_saves_per90": gk["saves_per90"],
        "goalkeeper_prior_goals_conceded_per90": gk["goals_conceded_per90"],
    }
    for raw, label in POSITION_MAP.items():
        result[f"regular_absent_{label}"] = float(sum(positions.get(pid) == raw for pid in absent))
    return result


def build_dataset(ledger: list[dict[str, object]], markets: dict[tuple[str, str, str, str], dict[str, float]]) -> tuple[list[dict[str, object]], dict[str, object]]:
    dataset: list[dict[str, object]] = []
    malformed: list[dict[str, object]] = []
    source_rows = 0
    source_fixtures = 0
    market_missing = 0
    cold_start = 0
    for season, (folder, _) in SEASONS.items():
        url = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{folder}/gws/merged_gw.csv"
        data = fetch(url)
        rows = read_rows(data)
        ledger.append({"source": "vaastav/Fantasy-Premier-League", "season": season, "url": url, "sha256": hf(data), "bytes": len(data), "rows": len(rows)})
        source_rows += len(rows)
        groups: dict[int, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            fixture = int(number(row.get("fixture")))
            if fixture > 0:
                groups[fixture].append(row)
        source_fixtures += len(groups)
        fixtures: list[tuple[str, int, list[dict[str, str]]]] = []
        for fixture, fixture_rows in groups.items():
            kickoff = next((r.get("kickoff_time", "") for r in fixture_rows if r.get("kickoff_time")), "")
            if kickoff:
                fixtures.append((kickoff, fixture, fixture_rows))
        fixtures.sort(key=lambda item: (item[0], item[1]))
        histories: dict[int, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=10))
        team_matches: Counter[str] = Counter()
        for kickoff, fixture, fixture_rows in fixtures:
            home_rows = [r for r in fixture_rows if truth(r.get("was_home"))]
            away_rows = [r for r in fixture_rows if not truth(r.get("was_home"))]
            if not home_rows or not away_rows:
                continue
            home_team = str(home_rows[0].get("team", ""))
            away_team = str(away_rows[0].get("team", ""))
            home_starters = [r for r in home_rows if number(r.get("starts")) > 0]
            away_starters = [r for r in away_rows if number(r.get("starts")) > 0]
            if len(home_starters) != 11 or len(away_starters) != 11:
                malformed.append({"season": season, "fixture": fixture, "kickoff": kickoff, "home_team": home_team, "away_team": away_team, "home_starters": len(home_starters), "away_starters": len(away_starters)})
            else:
                day = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).date().isoformat()
                market = markets.get((season, day, norm(home_team), norm(away_team)))
                if market is None:
                    market_missing += 1
                elif min(team_matches[home_team], team_matches[away_team]) < 5:
                    cold_start += 1
                else:
                    home = side_features(home_team, home_starters, home_rows, histories)
                    away = side_features(away_team, away_starters, away_rows, histories)
                    home_score = int(number(fixture_rows[0].get("team_h_score")))
                    away_score = int(number(fixture_rows[0].get("team_a_score")))
                    label = "H" if home_score > away_score else "A" if away_score > home_score else "D"
                    out: dict[str, object] = {
                        "season": season, "fixture": fixture, "date": day, "kickoff_time": kickoff,
                        "home_team": home_team, "away_team": away_team, "label_result": label,
                        "label_draw": int(label == "D"),
                    }
                    out.update(market)
                    for name in QUALITY_BASE:
                        out[f"home_{name}"] = home[name]
                        out[f"away_{name}"] = away[name]
                        out[f"diff_{name}"] = home[name] - away[name]
                    dataset.append(out)
            for row in fixture_rows:
                pid = int(number(row.get("element")))
                if pid <= 0:
                    continue
                histories[pid].append({
                    "minutes": number(row.get("minutes")), "starts": number(row.get("starts")),
                    "bps": number(row.get("bps")),
                    "xgi": number(row.get("expected_goal_involvements")) or number(row.get("expected_goals")) + number(row.get("expected_assists")),
                    "xgc": number(row.get("expected_goals_conceded")),
                    "defensive": number(row.get("defensive_contribution")) + number(row.get("tackles")) + number(row.get("recoveries")) + number(row.get("clearances_blocks_interceptions")),
                    "saves": number(row.get("saves")), "goals_conceded": number(row.get("goals_conceded")),
                })
            team_matches[home_team] += 1
            team_matches[away_team] += 1
    audit = {
        "source_player_rows": source_rows,
        "source_fixtures": source_fixtures,
        "usable_model_rows": len(dataset),
        "malformed_fixtures": malformed,
        "market_missing_fixtures": market_missing,
        "cold_start_excluded": cold_start,
        "rows_by_season": dict(sorted(Counter(str(r["season"]) for r in dataset).items())),
        "draws_by_season": dict(sorted(Counter(str(r["season"]) for r in dataset if int(r["label_draw"]) == 1).items())),
    }
    return dataset, audit


def matrix(rows: list[dict[str, object]], features: list[str]) -> np.ndarray:
    return np.asarray([[number(row.get(feature)) for feature in features] for row in rows], dtype=float)


def model_for(name: str, prereg: dict[str, object]):
    if name == "logistic":
        p = prereg["models"]["logistic"]
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=p["C"], class_weight=p["class_weight"], max_iter=p["max_iter"], random_state=p["random_state"])),
        ])
    p = prereg["models"]["hist_gradient_boosting"]
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("model", HistGradientBoostingClassifier(
            learning_rate=p["learning_rate"], max_iter=p["max_iter"], max_leaf_nodes=p["max_leaf_nodes"],
            l2_regularization=p["l2_regularization"], random_state=p["random_state"],
        )),
    ])


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {
        "pr_auc": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-9, 1 - 1e-9))),
        "brier": float(brier_score_loss(y, p)),
    }


def rolling_oof(dataset: list[dict[str, object]], prereg: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    fold_rows: list[dict[str, object]] = []
    predictions: list[dict[str, object]] = []
    season_order = list(SEASONS)
    for target in TARGETS:
        target_index = season_order.index(target)
        train_seasons = set(season_order[:target_index])
        train = [r for r in dataset if str(r["season"]) in train_seasons]
        test = [r for r in dataset if str(r["season"]) == target]
        y_train = np.asarray([int(r["label_draw"]) for r in train])
        y_test = np.asarray([int(r["label_draw"]) for r in test])
        for model_name in ("logistic", "hist_gradient_boosting"):
            family_scores: dict[str, np.ndarray] = {}
            for family, features in (("market", MARKET_FEATURES), ("market_plus_quality", MARKET_FEATURES + QUALITY_FEATURES)):
                model = model_for(model_name, prereg)
                model.fit(matrix(train, features), y_train)
                score = model.predict_proba(matrix(test, features))[:, 1]
                family_scores[family] = score
                result = metrics(y_test, score)
                fold_rows.append({
                    "target_season": target, "train_seasons": ";".join(sorted(train_seasons)),
                    "model": model_name, "feature_family": family, "rows": len(test), "draws": int(y_test.sum()), **result,
                })
            for index, row in enumerate(test):
                predictions.append({
                    "season": target, "fixture": row["fixture"], "date": row["date"],
                    "home_team": row["home_team"], "away_team": row["away_team"],
                    "label_draw": row["label_draw"], "model": model_name,
                    "market_score": float(family_scores["market"][index]),
                    "quality_score": float(family_scores["market_plus_quality"][index]),
                })
    return fold_rows, predictions


def select_lane(folds: list[dict[str, object]], predictions: list[dict[str, object]], prereg: dict[str, object]) -> dict[str, object]:
    gate = prereg["paired_increment_gate"]
    candidates = []
    for model_name in ("logistic", "hist_gradient_boosting"):
        rows = [r for r in folds if r["model"] == model_name]
        by = {(r["target_season"], r["feature_family"]): r for r in rows}
        deltas = []
        for target in TARGETS:
            market = by[(target, "market")]
            quality = by[(target, "market_plus_quality")]
            deltas.append({
                "target_season": target,
                "pr_auc": float(quality["pr_auc"]) - float(market["pr_auc"]),
                "roc_auc": float(quality["roc_auc"]) - float(market["roc_auc"]),
                "log_loss": float(quality["log_loss"]) - float(market["log_loss"]),
                "brier": float(quality["brier"]) - float(market["brier"]),
            })
        pr = [d["pr_auc"] for d in deltas]
        roc = [d["roc_auc"] for d in deltas]
        ll = [d["log_loss"] for d in deltas]
        br = [d["brier"] for d in deltas]
        checks = {
            "minimum_oof_folds": len(deltas) >= int(gate["minimum_oof_folds"]),
            "pr_auc_positive_folds": sum(x > 0 for x in pr) >= int(gate["pr_auc_positive_folds"]),
            "median_pr_auc_increment": float(np.median(pr)) >= float(gate["minimum_median_pr_auc_increment"]),
            "worst_pr_auc_increment": min(pr) >= float(gate["minimum_worst_pr_auc_increment"]),
            "roc_auc_nonnegative_folds": sum(x >= 0 for x in roc) >= int(gate["roc_auc_nonnegative_folds"]),
            "median_log_loss_increase": float(np.median(ll)) <= float(gate["maximum_median_log_loss_increase"]),
            "median_brier_increase": float(np.median(br)) <= float(gate["maximum_median_brier_increase"]),
        }
        candidate = {
            "model": model_name, "fold_deltas": deltas,
            "median_pr_auc_increment": float(np.median(pr)), "worst_pr_auc_increment": min(pr),
            "median_roc_auc_increment": float(np.median(roc)), "median_log_loss_increment": float(np.median(ll)),
            "median_brier_increment": float(np.median(br)), "checks": checks, "pass": all(checks.values()),
        }
        candidates.append(candidate)
    passed = [c for c in candidates if c["pass"]]
    passed.sort(key=lambda c: (c["median_pr_auc_increment"], c["worst_pr_auc_increment"], c["median_roc_auc_increment"]), reverse=True)
    winner = passed[0] if passed else None
    thresholds: list[dict[str, float]] = []
    if winner:
        scores = np.asarray([float(r["quality_score"]) for r in predictions if r["model"] == winner["model"]])
        for coverage in prereg["forward_policy"]["selection_coverage_levels"]:
            threshold = float(np.quantile(scores, 1.0 - float(coverage)))
            thresholds.append({"coverage": float(coverage), "threshold": threshold})
    return {
        "schema_version": "EPL-LINEUP-QUALITY-SELECTION-R2",
        "status": "HISTORICAL_LANE_FROZEN_PENDING_2026_27_FORWARD" if winner else "NO_STABLE_HISTORICAL_LINEUP_QUALITY_LANE",
        "candidates": candidates,
        "winner": winner,
        "forward_thresholds": thresholds,
        "formal_weight": 0,
        "promotion_allowed": False,
        "future_test_required": True,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))
    ledger: list[dict[str, object]] = []
    markets = load_markets(ledger)
    dataset, audit = build_dataset(ledger, markets)
    if len(dataset) < 1500:
        raise RuntimeError(f"insufficient dataset rows: {len(dataset)}")
    folds, predictions = rolling_oof(dataset, prereg)
    selection = select_lane(folds, predictions, prereg)
    write_csv(args.out / "EPL_LINEUP_QUALITY_R2_dataset.csv", dataset)
    write_csv(args.out / "EPL_LINEUP_QUALITY_R2_fold_metrics.csv", folds)
    write_csv(args.out / "EPL_LINEUP_QUALITY_R2_oof_predictions.csv", predictions)
    write_csv(args.out / "EPL_LINEUP_QUALITY_R2_source_ledger.csv", ledger)
    write_csv(args.out / "EPL_LINEUP_QUALITY_R2_malformed_fixtures.csv", audit["malformed_fixtures"])
    (args.out / "EPL_LINEUP_QUALITY_R2_selection.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": "EPL-LINEUP-QUALITY-R2",
        "status": selection["status"],
        "prereg_sha256": hf(args.prereg.read_bytes()),
        "dataset_audit": audit,
        "market_index_rows": len(markets),
        "fold_metric_rows": len(folds),
        "oof_prediction_rows": len(predictions),
        "source_downloads": len(ledger),
        "consumed_test_sets_reused_for_selection": [],
        "2025_26_role": "historical development fold; not untouched after PR81",
        "2026_27_forward_required": True,
        "formal_weight": 0,
        "formal_model_data_config_current_writes": [0, 0, 0, 0],
    }
    (args.out / "EPL_LINEUP_QUALITY_R2_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "EPL-LINEUP-QUALITY-ARTIFACT-R2", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "artifact_manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": hf(data), "bytes": len(data)}
    (args.out / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": selection["status"], "rows": len(dataset), "folds": len(folds), "winner": selection["winner"]["model"] if selection["winner"] else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
