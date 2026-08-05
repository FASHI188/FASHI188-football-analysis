#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

COMPS = {
    "ENG_PremierLeague": "E0",
    "ESP_LaLiga": "SP1",
    "FRA_Ligue1": "F1",
    "GER_Bundesliga": "D1",
    "ITA_SerieA": "I1",
}
MARKET_SEASONS = {"2024/25": "2425", "2025/26": "2526"}
OUTCOMES = ("H", "D", "A")
ORDINAL = {"A": 0, "D": 1, "H": 2}
TEAM_STATE = {"H": ("W", "L"), "D": ("D", "D"), "A": ("L", "W")}
STATE_INDEX = {"W": 0, "D": 1, "L": 2}
EPS = 1e-12


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def season_start(value: str) -> int:
    m = re.match(r"(\d{4})", value or "")
    if not m:
        raise ValueError(f"bad season {value!r}")
    return int(m.group(1))


def as_float(value: Any) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else math.nan
    except (TypeError, ValueError):
        return math.nan


def valid_odds(value: Any) -> float | None:
    x = as_float(value)
    return x if math.isfinite(x) and x > 1.0 else None


def norm_name(value: str) -> str:
    s = (value or "").lower().replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]", "", s)


def parse_market_date(value: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"bad market date {value!r}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def download(url: str, path: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 football-research-method-replication"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        path.write_bytes(response.read())


def read_downloaded_csv(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise UnicodeDecodeError("unknown", b"", 0, 1, "unable to decode")
    return list(csv.DictReader(text.splitlines()))


def load_history(repo_root: Path, competitions: list[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    for comp in competitions:
        path = repo_root / "football-data" / "training_datasets" / comp / "point_in_time.csv"
        if not path.is_file():
            raise FileNotFoundError(path)
        source_hashes[str(path.relative_to(repo_root)).replace("\\", "/")] = sha256_file(path)
        for raw in read_csv(path):
            row = dict(raw)
            row["competition_id"] = comp
            row["date"] = row["date"].strip()
            row["season_start"] = season_start(row["season"])
            row["match_identity"] = "|".join(
                [comp, row["season"], row["date"], row["home_team"], row["away_team"]]
            )
            if row.get("label_result") not in OUTCOMES:
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    return rows, source_hashes


def transition_distribution(
    team_counts: np.ndarray,
    league_counts: np.ndarray,
    league_outcomes: np.ndarray,
    last_state: str | None,
    alpha: float,
    shrink: float,
) -> np.ndarray:
    if last_state is None:
        base = league_outcomes + alpha
        return base / base.sum()
    idx = STATE_INDEX[last_state]
    league = league_counts[idx] + alpha
    league = league / league.sum()
    team = team_counts[idx]
    return (team + shrink * league) / (team.sum() + shrink)


def add_markov_probabilities(
    rows: list[dict[str, Any]], alpha: float, shrink: float
) -> dict[str, dict[str, float]]:
    by_comp_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_comp_date[(row["competition_id"], row["date"])].append(row)

    team_counts: dict[str, dict[str, np.ndarray]] = defaultdict(
        lambda: defaultdict(lambda: np.zeros((3, 3), dtype=float))
    )
    league_counts: dict[str, np.ndarray] = defaultdict(lambda: np.zeros((3, 3), dtype=float))
    league_outcomes: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(3, dtype=float))
    last_state: dict[str, dict[str, str]] = defaultdict(dict)
    result: dict[str, dict[str, float]] = {}

    for comp, date in sorted(by_comp_date):
        day = sorted(by_comp_date[(comp, date)], key=lambda r: (r["home_team"], r["away_team"]))
        pending: list[tuple[str, str, str, str]] = []
        for row in day:
            home = row["home_team"]
            away = row["away_team"]
            qh = transition_distribution(
                team_counts[comp][home],
                league_counts[comp],
                league_outcomes[comp],
                last_state[comp].get(home),
                alpha,
                shrink,
            )
            qa = transition_distribution(
                team_counts[comp][away],
                league_counts[comp],
                league_outcomes[comp],
                last_state[comp].get(away),
                alpha,
                shrink,
            )
            scores = np.array(
                [
                    math.sqrt(max(EPS, qh[0] * qa[2])),
                    math.sqrt(max(EPS, qh[1] * qa[1])),
                    math.sqrt(max(EPS, qh[2] * qa[0])),
                ],
                dtype=float,
            )
            probs = scores / scores.sum()
            result[row["match_identity"]] = {
                "markov_home": float(probs[0]),
                "markov_draw": float(probs[1]),
                "markov_away": float(probs[2]),
                "home_state_w": float(qh[0]),
                "home_state_d": float(qh[1]),
                "home_state_l": float(qh[2]),
                "away_state_w": float(qa[0]),
                "away_state_d": float(qa[1]),
                "away_state_l": float(qa[2]),
            }
            hs, aws = TEAM_STATE[row["label_result"]]
            pending.append((home, away, hs, aws))

        # Same-date outcomes update only after all same-date probabilities are frozen.
        for home, away, hs, aws in pending:
            for team, current in ((home, hs), (away, aws)):
                previous = last_state[comp].get(team)
                if previous is not None:
                    i = STATE_INDEX[previous]
                    j = STATE_INDEX[current]
                    team_counts[comp][team][i, j] += 1.0
                    league_counts[comp][i, j] += 1.0
                league_outcomes[comp][STATE_INDEX[current]] += 1.0
                last_state[comp][team] = current
    return result


def raw_features(row: dict[str, Any]) -> list[float]:
    home_venue_balance = as_float(row.get("home_venue_gf")) - as_float(row.get("home_venue_ga"))
    away_venue_balance = as_float(row.get("away_venue_gf")) - as_float(row.get("away_venue_ga"))
    return [
        as_float(row.get("elo_difference_with_home_advantage")),
        as_float(row.get("home_history_ppg")) - as_float(row.get("away_history_ppg")),
        as_float(row.get("home_last5_ppg")) - as_float(row.get("away_last5_ppg")),
        home_venue_balance - away_venue_balance,
    ]


@dataclass
class OrderedFit:
    beta: np.ndarray
    cut1: float
    cut2: float
    median: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    converged: bool
    iterations: int
    objective: float
    training_rows: int


def logistic(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=float)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    expx = np.exp(x[~pos])
    out[~pos] = expx / (1.0 + expx)
    return out


def prepare_matrix(rows: list[dict[str, Any]], fit: OrderedFit | None = None):
    raw = np.asarray([raw_features(r) for r in rows], dtype=float)
    if fit is None:
        median = np.nanmedian(raw, axis=0)
        median = np.where(np.isfinite(median), median, 0.0)
        filled = np.where(np.isfinite(raw), raw, median)
        mean = filled.mean(axis=0)
        scale = filled.std(axis=0)
        scale = np.where(scale > 1e-9, scale, 1.0)
    else:
        median, mean, scale = fit.median, fit.mean, fit.scale
        filled = np.where(np.isfinite(raw), raw, median)
    return (filled - mean) / scale, median, mean, scale


def ordered_probabilities(X: np.ndarray, beta: np.ndarray, cut1: float, cut2: float) -> np.ndarray:
    eta = X @ beta
    cdf1 = logistic(cut1 - eta)
    cdf2 = logistic(cut2 - eta)
    p_a = np.clip(cdf1, EPS, 1.0)
    p_d = np.clip(cdf2 - cdf1, EPS, 1.0)
    p_h = np.clip(1.0 - cdf2, EPS, 1.0)
    probs = np.column_stack([p_h, p_d, p_a])
    return probs / probs.sum(axis=1, keepdims=True)


def fit_ordered(rows: list[dict[str, Any]], ridge: float, maxiter: int) -> OrderedFit:
    X, median, mean, scale = prepare_matrix(rows)
    y = np.asarray([ORDINAL[r["label_result"]] for r in rows], dtype=int)
    counts = np.bincount(y, minlength=3).astype(float)
    cumulative1 = np.clip(counts[0] / counts.sum(), 0.02, 0.95)
    cumulative2 = np.clip((counts[0] + counts[1]) / counts.sum(), 0.05, 0.98)
    c1 = math.log(cumulative1 / (1.0 - cumulative1))
    c2 = math.log(cumulative2 / (1.0 - cumulative2))
    gap = max(0.1, c2 - c1)
    start = np.concatenate([np.zeros(X.shape[1]), np.array([c1, math.log(gap)])])

    def objective(theta: np.ndarray) -> float:
        beta = theta[:-2]
        cut1 = theta[-2]
        cut2 = cut1 + math.exp(theta[-1])
        probs = ordered_probabilities(X, beta, cut1, cut2)
        # ordered_probabilities is H,D,A while y is A,D,H.
        pick = np.choose(y, [probs[:, 2], probs[:, 1], probs[:, 0]])
        return float(-np.log(np.clip(pick, EPS, 1.0)).sum() + 0.5 * ridge * np.dot(beta, beta))

    result = minimize(
        objective,
        start,
        method="L-BFGS-B",
        options={"maxiter": int(maxiter), "ftol": 1e-12, "gtol": 1e-8},
    )
    beta = result.x[:-2]
    cut1 = float(result.x[-2])
    cut2 = cut1 + math.exp(float(result.x[-1]))
    return OrderedFit(
        beta=beta,
        cut1=cut1,
        cut2=cut2,
        median=median,
        mean=mean,
        scale=scale,
        converged=bool(result.success),
        iterations=int(getattr(result, "nit", 0)),
        objective=float(result.fun),
        training_rows=len(rows),
    )


def predict_ordered(rows: list[dict[str, Any]], fit: OrderedFit) -> np.ndarray:
    X, _, _, _ = prepare_matrix(rows, fit)
    return ordered_probabilities(X, fit.beta, fit.cut1, fit.cut2)


def build_market_index(
    out: Path, competitions: list[str], seasons: list[str]
) -> tuple[dict[tuple[str, str, str, str, str], dict[str, Any]], list[dict[str, Any]]]:
    raw_dir = out / "market_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    index: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    for comp in competitions:
        code = COMPS[comp]
        for season in seasons:
            scode = MARKET_SEASONS[season]
            url = f"https://www.football-data.co.uk/mmz4281/{scode}/{code}.csv"
            path = raw_dir / f"{comp}_{scode}_{code}.csv"
            download(url, path)
            rows = read_downloaded_csv(path)
            matched_odds = 0
            for row in rows:
                try:
                    date = parse_market_date(row.get("Date", ""))
                except ValueError:
                    continue
                primary = [valid_odds(row.get(x)) for x in ("AvgCH", "AvgCD", "AvgCA")]
                fallback = [valid_odds(row.get(x)) for x in ("AvgH", "AvgD", "AvgA")]
                if all(v is not None for v in primary):
                    odds = [float(v) for v in primary]
                    basis = "AVG_CLOSE"
                elif all(v is not None for v in fallback):
                    odds = [float(v) for v in fallback]
                    basis = "AVG_OPEN_FALLBACK"
                else:
                    continue
                inv = np.asarray([1.0 / v for v in odds], dtype=float)
                fair = inv / inv.sum()
                key = (
                    comp,
                    season,
                    date,
                    norm_name(row.get("HomeTeam", "")),
                    norm_name(row.get("AwayTeam", "")),
                )
                if key in index:
                    raise ValueError(f"duplicate market identity {key}")
                index[key] = {
                    "odds_home": odds[0],
                    "odds_draw": odds[1],
                    "odds_away": odds[2],
                    "market_home": float(fair[0]),
                    "market_draw": float(fair[1]),
                    "market_away": float(fair[2]),
                    "overround": float(inv.sum()),
                    "odds_basis": basis,
                    "source_url": url,
                    "source_sha256": sha256_file(path),
                }
                matched_odds += 1
            receipts.append(
                {
                    "competition": comp,
                    "season": season,
                    "url": url,
                    "file": str(path.relative_to(out)),
                    "sha256": sha256_file(path),
                    "rows": len(rows),
                    "rows_with_usable_odds": matched_odds,
                }
            )
    return index, receipts


def join_target(
    rows: list[dict[str, Any]],
    season: str,
    market_index: dict[tuple[str, str, str, str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    joined: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in rows:
        if row["season"] != season:
            continue
        key = (
            row["competition_id"],
            season,
            row["date"],
            norm_name(row["home_team"]),
            norm_name(row["away_team"]),
        )
        market = market_index.get(key)
        if market is None:
            missing.append(row["match_identity"])
            continue
        item = dict(row)
        item.update(market)
        joined.append(item)
    joined.sort(key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    return joined, missing


def multiclass_metrics(rows: list[dict[str, Any]], probs: np.ndarray) -> dict[str, float]:
    truth = np.asarray([OUTCOMES.index(r["label_result"]) for r in rows], dtype=int)
    selected = probs[np.arange(len(rows)), truth]
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(rows)), truth] = 1.0
    rps = ((np.cumsum(probs, axis=1)[:, :2] - np.cumsum(onehot, axis=1)[:, :2]) ** 2).mean()
    return {
        "rows": len(rows),
        "accuracy": float(np.mean(np.argmax(probs, axis=1) == truth)),
        "log_loss": float(-np.mean(np.log(np.clip(selected, EPS, 1.0)))),
        "brier": float(np.mean(np.sum((probs - onehot) ** 2, axis=1))),
        "rps": float(rps),
    }


def enrich_predictions(
    rows: list[dict[str, Any]],
    ordered: np.ndarray,
    markov_map: dict[str, dict[str, float]],
    ordered_weight: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        mk = markov_map[row["match_identity"]]
        markov = np.asarray([mk["markov_home"], mk["markov_draw"], mk["markov_away"]], dtype=float)
        empirical = ordered_weight * ordered[i] + (1.0 - ordered_weight) * markov
        empirical = empirical / empirical.sum()
        market = np.asarray([row["market_home"], row["market_draw"], row["market_away"]], dtype=float)
        odds = np.asarray([row["odds_home"], row["odds_draw"], row["odds_away"]], dtype=float)
        item = dict(row)
        for j, name in enumerate(("home", "draw", "away")):
            item[f"ordered_{name}"] = float(ordered[i, j])
            item[f"markov_{name}"] = float(markov[j])
            item[f"empirical_{name}"] = float(empirical[j])
            item[f"edge_{name}"] = float(empirical[j] * odds[j] - 1.0)
            item[f"prob_delta_{name}"] = float(empirical[j] - market[j])
        output.append(item)
    return output


def probability_array(rows: list[dict[str, Any]], prefix: str) -> np.ndarray:
    return np.asarray(
        [[r[f"{prefix}_home"], r[f"{prefix}_draw"], r[f"{prefix}_away"]] for r in rows],
        dtype=float,
    )


def threshold_result(rows: list[dict[str, Any]], outcome: str, threshold: float) -> dict[str, Any]:
    name = {"H": "home", "D": "draw", "A": "away"}[outcome]
    bets = [r for r in rows if float(r[f"edge_{name}"]) >= threshold]
    profits: list[float] = []
    league_profit: dict[str, list[float]] = defaultdict(list)
    for row in bets:
        odds = float(row[f"odds_{name}"])
        profit = odds - 1.0 if row["label_result"] == outcome else -1.0
        profits.append(profit)
        league_profit[row["competition_id"]].append(profit)
    league_roi = {
        comp: float(sum(vals) / len(vals)) for comp, vals in sorted(league_profit.items()) if vals
    }
    return {
        "threshold": float(threshold),
        "bets": len(bets),
        "wins": int(sum(r["label_result"] == outcome for r in bets)),
        "profit": float(sum(profits)),
        "roi": float(sum(profits) / len(profits)) if profits else math.nan,
        "hit_rate": float(sum(r["label_result"] == outcome for r in bets) / len(bets)) if bets else math.nan,
        "mean_edge": float(np.mean([r[f"edge_{name}"] for r in bets])) if bets else math.nan,
        "positive_leagues": int(sum(v > 0 for v in league_roi.values())),
        "league_roi": league_roi,
        "selected_identities": [r["match_identity"] for r in bets],
    }


def select_policy(
    rows: list[dict[str, Any]],
    outcome: str,
    thresholds: list[float],
    min_bets: int,
    min_positive_leagues: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    grid = [threshold_result(rows, outcome, t) for t in thresholds]
    valid = [
        r
        for r in grid
        if r["bets"] >= min_bets
        and math.isfinite(r["roi"])
        and r["roi"] > 0
        and r["positive_leagues"] >= min_positive_leagues
    ]
    if not valid:
        return None, grid
    selected = max(valid, key=lambda r: (r["roi"], r["profit"], r["bets"], -r["threshold"]))
    return selected, grid


def bootstrap_roi(
    rows: list[dict[str, Any]],
    outcome: str,
    threshold: float,
    repetitions: int,
    seed: int,
) -> dict[str, float]:
    name = {"H": "home", "D": "draw", "A": "away"}[outcome]
    bets = [r for r in rows if float(r[f"edge_{name}"]) >= threshold]
    by_cluster: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in bets:
        odds = float(row[f"odds_{name}"])
        profit = odds - 1.0 if row["label_result"] == outcome else -1.0
        by_cluster[(row["competition_id"], row["date"])].append(profit)
    clusters = list(by_cluster)
    if not clusters:
        return {"p05": math.nan, "p50": math.nan, "p95": math.nan}
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(repetitions):
        sampled = rng.choice(len(clusters), size=len(clusters), replace=True)
        profits: list[float] = []
        for idx in sampled:
            profits.extend(by_cluster[clusters[int(idx)]])
        values.append(sum(profits) / len(profits))
    return {
        "p05": float(np.quantile(values, 0.05)),
        "p50": float(np.quantile(values, 0.50)),
        "p95": float(np.quantile(values, 0.95)),
    }


def fit_receipt(fit: OrderedFit) -> dict[str, Any]:
    return {
        "training_rows": fit.training_rows,
        "converged": fit.converged,
        "iterations": fit.iterations,
        "objective": fit.objective,
        "beta": [float(x) for x in fit.beta],
        "cut1": fit.cut1,
        "cut2": fit.cut2,
        "feature_median": [float(x) for x in fit.median],
        "feature_mean": [float(x) for x in fit.mean],
        "feature_scale": [float(x) for x in fit.scale],
    }


def write_predictions(path: Path, rows: list[dict[str, Any]], selected: dict[str, Any]) -> None:
    fields = [
        "match_identity", "competition_id", "season", "date", "home_team", "away_team", "label_result",
        "odds_basis", "odds_home", "odds_draw", "odds_away",
        "market_home", "market_draw", "market_away",
        "ordered_home", "ordered_draw", "ordered_away",
        "markov_home", "markov_draw", "markov_away",
        "empirical_home", "empirical_draw", "empirical_away",
        "edge_home", "edge_draw", "edge_away",
        "prob_delta_home", "prob_delta_draw", "prob_delta_away",
        "selected_home", "selected_draw", "selected_away",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            item = {k: row.get(k, "") for k in fields}
            for outcome, name in (("H", "home"), ("D", "draw"), ("A", "away")):
                policy = selected.get(outcome)
                item[f"selected_{name}"] = int(
                    policy is not None and row[f"edge_{name}"] >= policy["threshold"]
                )
            writer.writerow(item)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))
    competitions = list(prereg["competitions"])
    policy_season = prereg["policy_season"]
    test_season = prereg["test_season"]
    policy_year = season_start(policy_season)
    test_year = season_start(test_season)

    history, history_hashes = load_history(args.repo_root, competitions)
    markov_map = add_markov_probabilities(
        history,
        float(prereg["markov_component"]["dirichlet_alpha"]),
        float(prereg["markov_component"]["league_shrinkage_mass"]),
    )
    market_index, market_receipts = build_market_index(
        args.out, competitions, [policy_season, test_season]
    )
    policy_rows, policy_missing = join_target(history, policy_season, market_index)
    test_rows, test_missing = join_target(history, test_season, market_index)

    train_policy = [r for r in history if r["season_start"] < policy_year]
    train_test = [r for r in history if r["season_start"] < test_year]
    ordered_cfg = prereg["ordered_logit"]
    policy_fit = fit_ordered(train_policy, float(ordered_cfg["ridge"]), int(ordered_cfg["maximum_iterations"]))
    test_fit = fit_ordered(train_test, float(ordered_cfg["ridge"]), int(ordered_cfg["maximum_iterations"]))
    if not policy_fit.converged or not test_fit.converged:
        raise RuntimeError("ordered-logit optimizer did not converge")

    policy_ordered = predict_ordered(policy_rows, policy_fit)
    test_ordered = predict_ordered(test_rows, test_fit)
    weight = float(prereg["mixture"]["ordered_logit_weight"])
    policy_pred = enrich_predictions(policy_rows, policy_ordered, markov_map, weight)
    test_pred = enrich_predictions(test_rows, test_ordered, markov_map, weight)

    policy_metrics = {
        "market": multiclass_metrics(policy_pred, probability_array(policy_pred, "market")),
        "ordered": multiclass_metrics(policy_pred, probability_array(policy_pred, "ordered")),
        "markov": multiclass_metrics(policy_pred, probability_array(policy_pred, "markov")),
        "empirical": multiclass_metrics(policy_pred, probability_array(policy_pred, "empirical")),
    }
    test_metrics = {
        "market": multiclass_metrics(test_pred, probability_array(test_pred, "market")),
        "ordered": multiclass_metrics(test_pred, probability_array(test_pred, "ordered")),
        "markov": multiclass_metrics(test_pred, probability_array(test_pred, "markov")),
        "empirical": multiclass_metrics(test_pred, probability_array(test_pred, "empirical")),
    }

    betting = prereg["betting"]
    thresholds = [float(x) for x in betting["threshold_grid"]]
    selected: dict[str, Any] = {}
    policy_grids: dict[str, Any] = {}
    test_results: dict[str, Any] = {}
    for outcome in OUTCOMES:
        choice, grid = select_policy(
            policy_pred,
            outcome,
            thresholds,
            int(betting["policy_minimum_bets"]),
            int(betting["policy_minimum_positive_leagues"]),
        )
        selected[outcome] = choice
        policy_grids[outcome] = grid
        if choice is None:
            test_results[outcome] = {"status": "DISABLED_NO_POLICY_EDGE"}
        else:
            result = threshold_result(test_pred, outcome, float(choice["threshold"]))
            result["bootstrap_90pct"] = bootstrap_roi(
                test_pred,
                outcome,
                float(choice["threshold"]),
                int(prereg["primary_draw_gate"]["date_cluster_bootstrap_repetitions"]),
                61438385 + OUTCOMES.index(outcome),
            )
            test_results[outcome] = result

    draw = test_results.get("D", {})
    gate_cfg = prereg["primary_draw_gate"]
    draw_pass = bool(
        selected.get("D") is not None
        and draw.get("bets", 0) >= int(gate_cfg["test_minimum_bets"])
        and draw.get("roi", -math.inf) > 0
        and draw.get("positive_leagues", 0) >= int(gate_cfg["test_minimum_positive_leagues"])
        and draw.get("bootstrap_90pct", {}).get("p05", -math.inf) > 0
    )
    status = (
        "PASS_RETROSPECTIVE_DRAW_MISPRICING_SCREEN_R1"
        if draw_pass
        else "FAIL_RETROSPECTIVE_DRAW_MISPRICING_SCREEN_R1"
    )

    selected_clean = {
        outcome: value for outcome, value in selected.items()
    }
    write_predictions(args.out / "policy_predictions.csv", policy_pred, selected_clean)
    write_predictions(args.out / "test_predictions.csv", test_pred, selected_clean)

    result = {
        "schema_version": "FEAR-DRAW-MISPRICING-RESULT-R1",
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "checked_out_head": os.environ.get("CHECKED_OUT_HEAD", "UNSET"),
        "method_class_replication_not_exact_paper_replication": True,
        "paper_full_text_available_to_run": False,
        "formal_weight": 0,
        "formal_promotion": False,
        "test_is_new_blind_holdout": False,
        "market_status": prereg["market_status"],
        "counts": {
            "history_rows": len(history),
            "policy_market_rows": len(policy_pred),
            "test_market_rows": len(test_pred),
            "policy_market_missing": len(policy_missing),
            "test_market_missing": len(test_missing),
            "policy_outcomes": dict(sorted(Counter(r["label_result"] for r in policy_pred).items())),
            "test_outcomes": dict(sorted(Counter(r["label_result"] for r in test_pred).items())),
        },
        "ordered_fits": {
            "policy": fit_receipt(policy_fit),
            "test": fit_receipt(test_fit),
        },
        "probability_metrics": {
            "policy": policy_metrics,
            "test": test_metrics,
        },
        "policy_threshold_grids": policy_grids,
        "selected_policy": selected_clean,
        "test_betting_results": test_results,
        "primary_draw_gate_pass": draw_pass,
        "hard_limits": prereg["hard_limits"],
        "source_identity": {
            "training_files": history_hashes,
            "market_files": market_receipts,
            "preregistration_sha256": sha256_file(args.prereg),
        },
    }
    result_path = args.out / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=True) + "\n", encoding="utf-8")

    lines = [
        "# Markov + ordered-logit market-mispricing screen R1",
        "",
        f"- Status: `{status}`",
        f"- Policy rows: {len(policy_pred)}",
        f"- Test rows: {len(test_pred)}",
        f"- Draw policy: `{json.dumps(selected_clean.get('D'), ensure_ascii=False)}`",
        f"- Draw test: `{json.dumps(test_results.get('D'), ensure_ascii=False)}`",
        "- Exact paper replication: NO (full text unavailable)",
        "- Formal weight: 0",
        "- Market data: retrospective reference without original row-level quote timestamps",
    ]
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = {"schema_version": "FEAR-DRAW-MISPRICING-ARTIFACT-R1", "files": {}}
    for path in sorted(args.out.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][str(path.relative_to(args.out))] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": status, "draw_gate_pass": draw_pass, "result": str(result_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
