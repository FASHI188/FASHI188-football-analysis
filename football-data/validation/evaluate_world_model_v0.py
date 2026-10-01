#!/usr/bin/env python3
"""World Model V0: retrospective, research-only sequential score-path simulator.

This experiment is deliberately small and falsifiable. It uses Hudl/StatsBomb Open
Data for EPL 2015/16, predicts each date before adding any same-date outcomes, and
compares a static independent-Poisson score model with a sequential generative
match-state simulator using the same pre-match goal intensities.

Hard boundary: retrospective open event data are not proven historical PIT inputs.
This file cannot change formal weights, CURRENT, protected labels, or main runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

MATCHES_URL = "https://raw.githubusercontent.com/hudl/open-data/master/data/matches/2/27.json"
EVENT_URL = "https://raw.githubusercontent.com/hudl/open-data/master/data/events/{match_id}.json"
USER_AGENT = "FASHI188-football-analysis/world-model-v0"

EXPECTED_MATCHES = 380
WINDOW = 12
PRIOR_MATCH_EQUIV = 6.0
STATE_PRIOR_EXPOSURES = 180.0
PACE_SIGMA_MIN = 0.05
PACE_SIGMA_MAX = 0.35
STATE_MULT_MIN = 0.65
STATE_MULT_MAX = 1.50
LAMBDA_MIN = 0.15
LAMBDA_MAX = 4.00
SIMS = 10_000
RANDOM_SEED = 20260818
PROB_FLOOR = 1e-12
BOOTSTRAP_REPS = 5_000
MIN_EVENT_SUCCESS_RATE = 0.98
MIN_TOTAL_MATCHES = 360
MIN_TEST_MATCHES = 150
MIN_TEST_EACH_FOLD = 40
MIN_PRIOR_MATCHES = 150


@dataclass(frozen=True)
class MatchMeta:
    match_id: int
    match_date: str
    kick_off: str
    home: str
    away: str
    home_score: int
    away_score: int


@dataclass(frozen=True)
class MatchSummary:
    meta: MatchMeta
    home_xg: float
    away_xg: float
    home_shots: int
    away_shots: int
    home_goal_bins: tuple[int, ...]
    away_goal_bins: tuple[int, ...]
    event_sha256: str


def _download_bytes(url: str, attempts: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:  # network retry path
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def _download_json(url: str) -> tuple[Any, str]:
    raw = _download_bytes(url)
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def load_matches() -> tuple[list[MatchMeta], str]:
    payload, sha = _download_json(MATCHES_URL)
    if not isinstance(payload, list):
        raise RuntimeError("match payload is not a list")
    matches: list[MatchMeta] = []
    for item in payload:
        matches.append(
            MatchMeta(
                match_id=int(item["match_id"]),
                match_date=str(item["match_date"]),
                kick_off=str(item.get("kick_off") or ""),
                home=str(item["home_team"]["home_team_name"]),
                away=str(item["away_team"]["away_team_name"]),
                home_score=int(item["home_score"]),
                away_score=int(item["away_score"]),
            )
        )
    matches.sort(key=lambda m: (m.match_date, m.kick_off, m.match_id))
    return matches, sha


def _goal_bin(minute: int) -> int:
    return min(max(int(minute), 0) // 5, 17)


def summarize_events(meta: MatchMeta) -> MatchSummary:
    events, event_sha = _download_json(EVENT_URL.format(match_id=meta.match_id))
    if not isinstance(events, list):
        raise RuntimeError(f"events payload not a list for {meta.match_id}")
    hxg = axg = 0.0
    hshots = ashots = 0
    hbins = [0] * 18
    abins = [0] * 18
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str((event.get("type") or {}).get("name") or "")
        team = str((event.get("team") or {}).get("name") or "")
        minute = int(event.get("minute") or 0)
        if event_type == "Shot":
            shot = event.get("shot") or {}
            xg = float(shot.get("statsbomb_xg") or 0.0)
            if team == meta.home:
                hxg += xg
                hshots += 1
            elif team == meta.away:
                axg += xg
                ashots += 1
            if str((shot.get("outcome") or {}).get("name") or "") == "Goal":
                if team == meta.home:
                    hbins[_goal_bin(minute)] += 1
                elif team == meta.away:
                    abins[_goal_bin(minute)] += 1
        elif event_type == "Own Goal Against":
            # StatsBomb assigns Own Goal Against to the conceding team.
            if team == meta.home:
                abins[_goal_bin(minute)] += 1
            elif team == meta.away:
                hbins[_goal_bin(minute)] += 1
        elif event_type == "Own Goal For":
            if team == meta.home:
                hbins[_goal_bin(minute)] += 1
            elif team == meta.away:
                abins[_goal_bin(minute)] += 1
    return MatchSummary(
        meta=meta,
        home_xg=hxg,
        away_xg=axg,
        home_shots=hshots,
        away_shots=ashots,
        home_goal_bins=tuple(hbins),
        away_goal_bins=tuple(abins),
        event_sha256=event_sha,
    )


def download_summaries(matches: list[MatchMeta]) -> tuple[dict[int, MatchSummary], list[dict[str, Any]]]:
    summaries: dict[int, MatchSummary] = {}
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(summarize_events, meta): meta for meta in matches}
        for future in as_completed(future_map):
            meta = future_map[future]
            try:
                summaries[meta.match_id] = future.result()
            except Exception as exc:
                failures.append({"match_id": meta.match_id, "error": f"{type(exc).__name__}: {exc}"})
    return summaries, failures


def poisson_bucket_probs(lam: float, exact_max: int = 7) -> np.ndarray:
    probs = [math.exp(-lam) * (lam**k) / math.factorial(k) for k in range(exact_max + 1)]
    tail = max(0.0, 1.0 - sum(probs))
    out = np.asarray(probs + [tail], dtype=float)
    return out / out.sum()


def poisson_hda(lh: float, la: float, max_goal: int = 16) -> np.ndarray:
    ph = np.asarray([math.exp(-lh) * (lh**k) / math.factorial(k) for k in range(max_goal + 1)], dtype=float)
    pa = np.asarray([math.exp(-la) * (la**k) / math.factorial(k) for k in range(max_goal + 1)], dtype=float)
    ph[-1] += max(0.0, 1.0 - ph.sum())
    pa[-1] += max(0.0, 1.0 - pa.sum())
    joint = np.outer(ph, pa)
    home = float(np.tril(joint, -1).sum())
    draw = float(np.trace(joint))
    away = float(np.triu(joint, 1).sum())
    return np.asarray([home, draw, away], dtype=float) / joint.sum()


def poisson_total_probs(lam: float, exact_max: int = 6) -> np.ndarray:
    return poisson_bucket_probs(lam, exact_max=exact_max)


def team_recent_rates(team: str, history: list[MatchSummary], league_team_xg: float) -> tuple[float, float, int]:
    rows: list[tuple[float, float]] = []
    for item in reversed(history):
        if item.meta.home == team:
            rows.append((item.home_xg, item.away_xg))
        elif item.meta.away == team:
            rows.append((item.away_xg, item.home_xg))
        if len(rows) >= WINDOW:
            break
    n = len(rows)
    xg_for = sum(row[0] for row in rows)
    xg_against = sum(row[1] for row in rows)
    smoothed_for = (xg_for + PRIOR_MATCH_EQUIV * league_team_xg) / (n + PRIOR_MATCH_EQUIV)
    smoothed_against = (xg_against + PRIOR_MATCH_EQUIV * league_team_xg) / (n + PRIOR_MATCH_EQUIV)
    return smoothed_for, smoothed_against, n


def estimate_lambdas(home: str, away: str, history: list[MatchSummary]) -> tuple[float, float, dict[str, Any]]:
    league_home = float(np.mean([item.home_xg for item in history]))
    league_away = float(np.mean([item.away_xg for item in history]))
    league_team = max((league_home + league_away) / 2.0, 0.2)
    h_for, h_against, hn = team_recent_rates(home, history, league_team)
    a_for, a_against, an = team_recent_rates(away, history, league_team)
    h_att = h_for / league_team
    h_def_weak = h_against / league_team
    a_att = a_for / league_team
    a_def_weak = a_against / league_team
    lh = float(np.clip(league_home * h_att * a_def_weak, LAMBDA_MIN, LAMBDA_MAX))
    la = float(np.clip(league_away * a_att * h_def_weak, LAMBDA_MIN, LAMBDA_MAX))
    return lh, la, {
        "league_home_xg": league_home,
        "league_away_xg": league_away,
        "home_history_n": hn,
        "away_history_n": an,
    }


def estimate_state_multipliers(history: list[MatchSummary]) -> dict[str, float]:
    exposures = defaultdict(float)
    goals = defaultdict(float)
    for item in history:
        hs = as_ = 0
        for hgoals, agoals in zip(item.home_goal_bins, item.away_goal_bins):
            if hs > as_:
                hrole, arole = "lead", "trail"
            elif hs < as_:
                hrole, arole = "trail", "lead"
            else:
                hrole = arole = "draw"
            exposures[hrole] += 1.0
            exposures[arole] += 1.0
            goals[hrole] += float(hgoals)
            goals[arole] += float(agoals)
            hs += int(hgoals)
            as_ += int(agoals)
    total_exposure = sum(exposures.values())
    total_goals = sum(goals.values())
    overall = max(total_goals / max(total_exposure, 1.0), 1e-6)
    multipliers: dict[str, float] = {}
    for role in ("lead", "draw", "trail"):
        rate = (goals[role] + overall * STATE_PRIOR_EXPOSURES) / (exposures[role] + STATE_PRIOR_EXPOSURES)
        multipliers[role] = float(np.clip(rate / overall, STATE_MULT_MIN, STATE_MULT_MAX))
    return multipliers


def estimate_pace_sigma(history: list[MatchSummary]) -> float:
    totals = np.asarray([item.home_xg + item.away_xg for item in history], dtype=float)
    mean = max(float(totals.mean()), 0.2)
    ratios = np.clip(totals / mean, 0.15, 6.0)
    sigma = float(np.std(np.log(ratios)))
    return float(np.clip(sigma, PACE_SIGMA_MIN, PACE_SIGMA_MAX))


def simulate_world(lh: float, la: float, state_mult: dict[str, float], pace_sigma: float, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    h = np.zeros(SIMS, dtype=np.int16)
    a = np.zeros(SIMS, dtype=np.int16)
    pace = rng.lognormal(mean=-0.5 * pace_sigma * pace_sigma, sigma=pace_sigma, size=SIMS)
    for _ in range(18):
        diff = h - a
        hm = np.where(diff > 0, state_mult["lead"], np.where(diff < 0, state_mult["trail"], state_mult["draw"]))
        am = np.where(diff < 0, state_mult["lead"], np.where(diff > 0, state_mult["trail"], state_mult["draw"]))
        h += rng.poisson((lh / 18.0) * pace * hm).astype(np.int16)
        a += rng.poisson((la / 18.0) * pace * am).astype(np.int16)
    score = np.zeros((9, 9), dtype=float)
    hb = np.minimum(h, 8)
    ab = np.minimum(a, 8)
    np.add.at(score, (hb, ab), 1.0)
    score = (score + 0.5) / (score.sum() + 0.5 * score.size)
    hda = np.asarray([(h > a).mean(), (h == a).mean(), (h < a).mean()], dtype=float)
    totals = h + a
    total_probs = np.asarray([(totals == k).mean() for k in range(7)] + [(totals >= 7).mean()], dtype=float)
    total_probs = np.maximum(total_probs, PROB_FLOOR)
    total_probs /= total_probs.sum()
    return score, hda, total_probs


def baseline_distributions(lh: float, la: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ph = poisson_bucket_probs(lh, 7)
    pa = poisson_bucket_probs(la, 7)
    score = np.outer(ph, pa)
    return score, poisson_hda(lh, la), poisson_total_probs(lh + la, 6)


def score_bucket(value: int) -> int:
    return min(int(value), 8)


def total_bucket(value: int) -> int:
    return min(int(value), 7)


def exact_logscore(score_probs: np.ndarray, hg: int, ag: int) -> float:
    p = float(score_probs[score_bucket(hg), score_bucket(ag)])
    return -math.log(max(p, PROB_FLOOR))


def hda_index(hg: int, ag: int) -> int:
    if hg > ag:
        return 0
    if hg == ag:
        return 1
    return 2


def multiclass_logloss(probs: np.ndarray, target: int) -> float:
    return -math.log(max(float(probs[target]), PROB_FLOOR))


def brier(probs: np.ndarray, target: int) -> float:
    y = np.zeros(len(probs), dtype=float)
    y[target] = 1.0
    return float(np.sum((probs - y) ** 2))


def rps(probs: np.ndarray, target: int) -> float:
    y = np.zeros(len(probs), dtype=float)
    y[target] = 1.0
    return float(np.sum((np.cumsum(probs)[:-1] - np.cumsum(y)[:-1]) ** 2) / (len(probs) - 1))


def binary_logloss(p: float, y: int) -> float:
    p = min(max(float(p), PROB_FLOOR), 1.0 - PROB_FLOOR)
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def build_date_groups(matches: list[MatchMeta], available: set[int]) -> list[tuple[str, list[MatchMeta]]]:
    grouped: dict[str, list[MatchMeta]] = defaultdict(list)
    for match in matches:
        if match.match_id in available:
            grouped[match.match_date].append(match)
    return [(date, sorted(grouped[date], key=lambda m: (m.kick_off, m.match_id))) for date in sorted(grouped)]


def define_test_folds(groups: list[tuple[str, list[MatchMeta]]]) -> tuple[set[str], dict[str, int], int]:
    total = sum(len(items) for _, items in groups)
    burn_target = math.ceil(total * 0.5)
    cumulative = 0
    test_start = 0
    for idx, (_, items) in enumerate(groups):
        cumulative += len(items)
        if cumulative >= burn_target:
            test_start = idx + 1
            break
    burn_dates = {date for date, _ in groups[:test_start]}
    test_groups = groups[test_start:]
    test_total = sum(len(items) for _, items in test_groups)
    targets = [test_total / 3.0, 2.0 * test_total / 3.0]
    fold_by_date: dict[str, int] = {}
    seen = 0
    fold = 0
    for date, items in test_groups:
        if fold < 2 and seen >= targets[fold]:
            fold += 1
        fold_by_date[date] = fold
        seen += len(items)
    burn_count = sum(len(items) for date, items in groups if date in burn_dates)
    return burn_dates, fold_by_date, burn_count


def aggregate_metrics(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "exact_score_logscore": float(np.mean([r[f"{prefix}_exact"] for r in rows])),
        "hda_logloss": float(np.mean([r[f"{prefix}_hda_ll"] for r in rows])),
        "hda_brier": float(np.mean([r[f"{prefix}_hda_brier"] for r in rows])),
        "total_rps": float(np.mean([r[f"{prefix}_total_rps"] for r in rows])),
        "draw_binary_logloss": float(np.mean([r[f"{prefix}_draw_ll"] for r in rows])),
        "top1_draw_calls": int(sum(r[f"{prefix}_top1"] == 1 for r in rows)),
        "top1_draw_hits": int(sum(r[f"{prefix}_top1"] == 1 and r["target_hda"] == 1 for r in rows)),
    }


def draw_calibration(rows: list[dict[str, Any]], prefix: str, bins: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda r: r[f"{prefix}_pdraw"])
    out = []
    for chunk in np.array_split(np.asarray(ordered, dtype=object), bins):
        items = list(chunk)
        if not items:
            continue
        out.append({
            "n": len(items),
            "mean_probability": float(np.mean([r[f"{prefix}_pdraw"] for r in items])),
            "observed_draw_rate": float(np.mean([1.0 if r["target_hda"] == 1 else 0.0 for r in items])),
        })
    return out


def cluster_bootstrap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_date[row["match_date"]].append(row["candidate_exact"] - row["baseline_exact"])
    dates = sorted(by_date)
    cluster_means = np.asarray([np.mean(by_date[d]) for d in dates], dtype=float)
    cluster_sizes = np.asarray([len(by_date[d]) for d in dates], dtype=float)
    observed = float(np.average(cluster_means, weights=cluster_sizes))
    rng = np.random.default_rng(RANDOM_SEED)
    boot = np.empty(BOOTSTRAP_REPS, dtype=float)
    n = len(dates)
    for i in range(BOOTSTRAP_REPS):
        idx = rng.integers(0, n, size=n)
        boot[i] = float(np.average(cluster_means[idx], weights=cluster_sizes[idx]))
    return {
        "clusters": n,
        "replicates": BOOTSTRAP_REPS,
        "observed_mean_delta": observed,
        "p05": float(np.quantile(boot, 0.05)),
        "median": float(np.quantile(boot, 0.50)),
        "p95": float(np.quantile(boot, 0.95)),
        "probability_candidate_better": float(np.mean(boot < 0.0)),
    }


def run() -> dict[str, Any]:
    matches, match_list_sha = load_matches()
    if len(matches) != EXPECTED_MATCHES:
        raise RuntimeError(f"expected {EXPECTED_MATCHES} matches, found {len(matches)}")
    summaries_by_id, failures = download_summaries(matches)
    success_rate = len(summaries_by_id) / len(matches)
    coverage_checks = {
        "minimum_total_matches": len(summaries_by_id) >= MIN_TOTAL_MATCHES,
        "event_download_success_rate": success_rate >= MIN_EVENT_SUCCESS_RATE,
    }
    if not all(coverage_checks.values()):
        return {
            "schema_version": "WORLD_MODEL_V0_RESULT_1",
            "status": "STOP_DATA_COVERAGE",
            "coverage": {"checks": coverage_checks, "success_rate": success_rate, "failures": failures},
            "boundary": {"formal_weight": 0, "b05_opened": false if False else False, "formal_model_mutation": False, "main_mutation": False},
        }

    groups = build_date_groups(matches, set(summaries_by_id))
    burn_dates, fold_by_date, burn_count = define_test_folds(groups)
    if burn_count < MIN_PRIOR_MATCHES:
        raise RuntimeError(f"burn-in too small: {burn_count}")

    history: list[MatchSummary] = []
    rows: list[dict[str, Any]] = []
    for date, day_matches in groups:
        if date in burn_dates:
            history.extend(summaries_by_id[m.match_id] for m in day_matches)
            continue
        fold = fold_by_date[date]
        if len(history) < MIN_PRIOR_MATCHES:
            raise RuntimeError("history gate violated")
        # Predict the whole date from the same pre-date history. No same-day update is allowed here.
        state_mult = estimate_state_multipliers(history)
        pace_sigma = estimate_pace_sigma(history)
        for meta in day_matches:
            lh, la, lambda_receipt = estimate_lambdas(meta.home, meta.away, history)
            base_score, base_hda, base_total = baseline_distributions(lh, la)
            candidate_score, candidate_hda, candidate_total = simulate_world(
                lh, la, state_mult, pace_sigma, seed=RANDOM_SEED + meta.match_id
            )
            target_hda = hda_index(meta.home_score, meta.away_score)
            target_total = total_bucket(meta.home_score + meta.away_score)
            baseline_top1 = int(np.argmax(base_hda))
            candidate_top1 = int(np.argmax(candidate_hda))
            rows.append({
                "match_id": meta.match_id,
                "match_date": date,
                "fold": fold,
                "target_hda": target_hda,
                "baseline_exact": exact_logscore(base_score, meta.home_score, meta.away_score),
                "candidate_exact": exact_logscore(candidate_score, meta.home_score, meta.away_score),
                "baseline_hda_ll": multiclass_logloss(base_hda, target_hda),
                "candidate_hda_ll": multiclass_logloss(candidate_hda, target_hda),
                "baseline_hda_brier": brier(base_hda, target_hda),
                "candidate_hda_brier": brier(candidate_hda, target_hda),
                "baseline_total_rps": rps(base_total, target_total),
                "candidate_total_rps": rps(candidate_total, target_total),
                "baseline_draw_ll": binary_logloss(base_hda[1], 1 if target_hda == 1 else 0),
                "candidate_draw_ll": binary_logloss(candidate_hda[1], 1 if target_hda == 1 else 0),
                "baseline_pdraw": float(base_hda[1]),
                "candidate_pdraw": float(candidate_hda[1]),
                "baseline_top1": baseline_top1,
                "candidate_top1": candidate_top1,
                "lambda_home": lh,
                "lambda_away": la,
                "pace_sigma": pace_sigma,
                "state_mult": dict(state_mult),
                "lambda_receipt": lambda_receipt,
            })
        history.extend(summaries_by_id[m.match_id] for m in day_matches)

    fold_counts = {str(f): sum(r["fold"] == f for r in rows) for f in range(3)}
    coverage_checks.update({
        "minimum_test_matches": len(rows) >= MIN_TEST_MATCHES,
        "minimum_each_fold": all(v >= MIN_TEST_EACH_FOLD for v in fold_counts.values()),
        "minimum_prior_matches_before_prediction": burn_count >= MIN_PRIOR_MATCHES,
    })
    if not all(coverage_checks.values()):
        raise RuntimeError(f"post-split coverage gate failed: {coverage_checks}, folds={fold_counts}")

    pooled_baseline = aggregate_metrics(rows, "baseline")
    pooled_candidate = aggregate_metrics(rows, "candidate")
    by_fold = {}
    fold_wins = 0
    for fold in range(3):
        subset = [r for r in rows if r["fold"] == fold]
        b = aggregate_metrics(subset, "baseline")
        c = aggregate_metrics(subset, "candidate")
        delta = c["exact_score_logscore"] - b["exact_score_logscore"]
        if delta < 0:
            fold_wins += 1
        by_fold[str(fold)] = {"baseline": b, "candidate": c, "primary_delta": delta}

    bootstrap = cluster_bootstrap(rows)
    primary_delta = pooled_candidate["exact_score_logscore"] - pooled_baseline["exact_score_logscore"]
    hda_delta = pooled_candidate["hda_logloss"] - pooled_baseline["hda_logloss"]
    total_rps_delta = pooled_candidate["total_rps"] - pooled_baseline["total_rps"]
    checks = {
        "primary_logscore_gain_at_least_0_005": primary_delta <= -0.005,
        "bootstrap_p95_lt_zero": bootstrap["p95"] < 0.0,
        "fold_primary_wins_at_least_2": fold_wins >= 2,
        "hda_logloss_nonworse": hda_delta <= 0.002,
        "total_rps_nonworse": total_rps_delta <= 0.002,
    }
    scientific_component_pass = all(checks.values())
    event_identity_material = "\n".join(
        f"{mid}:{summaries_by_id[mid].event_sha256}" for mid in sorted(summaries_by_id)
    ).encode("utf-8")
    sample_material = "\n".join(str(r["match_id"]) for r in rows).encode("utf-8")

    return {
        "schema_version": "WORLD_MODEL_V0_RESULT_1",
        "status": "SCIENTIFIC_COMPONENT_PASS_RESEARCH_ONLY" if scientific_component_pass else "FAIL_RESEARCH_ONLY",
        "scientific_component_pass": scientific_component_pass,
        "question": "Does sequential generative match-state simulation improve the full score distribution versus static independent Poisson using the same pre-match intensities?",
        "data": {
            "source": "Hudl/StatsBomb Open Data",
            "competition": "England Premier League 2015/2016",
            "match_list_sha256": match_list_sha,
            "event_bundle_identity_sha256": hashlib.sha256(event_identity_material).hexdigest(),
            "event_download_success_rate": success_rate,
            "event_failures": failures,
            "retrospective_not_formal_pit": True,
        },
        "split": {
            "total_available_matches": len(summaries_by_id),
            "burn_in_matches": burn_count,
            "test_matches": len(rows),
            "test_sample_sha256": hashlib.sha256(sample_material).hexdigest(),
            "fold_counts": fold_counts,
            "same_day_update_forbidden": True,
        },
        "model": {
            "baseline": "STATIC_INDEPENDENT_POISSON",
            "candidate": "WORLD_MODEL_V0_STATE_PATH",
            "team_history_window": WINDOW,
            "prior_match_equivalent": PRIOR_MATCH_EQUIV,
            "simulation_steps": 18,
            "simulation_paths": SIMS,
            "shared_latent_pace": True,
            "dynamic_score_state_feedback": True,
            "hyperparameter_search": False,
        },
        "metrics": {
            "pooled": {"baseline": pooled_baseline, "candidate": pooled_candidate},
            "deltas_candidate_minus_baseline": {
                "exact_score_logscore": primary_delta,
                "hda_logloss": hda_delta,
                "hda_brier": pooled_candidate["hda_brier"] - pooled_baseline["hda_brier"],
                "total_rps": total_rps_delta,
                "draw_binary_logloss": pooled_candidate["draw_binary_logloss"] - pooled_baseline["draw_binary_logloss"],
            },
            "by_fold": by_fold,
            "draw_calibration": {
                "baseline": draw_calibration(rows, "baseline"),
                "candidate": draw_calibration(rows, "candidate"),
            },
        },
        "bootstrap": bootstrap,
        "development_gate": {"checks": checks, "passed": scientific_component_pass},
        "boundary": {
            "retrospective_viewed_labels": True,
            "strict_formal_pit_claim": False,
            "formal_weight": 0,
            "b05_opened": False,
            "new_protected_labels_opened": 0,
            "market_data_used": False,
            "current_match_events_used_for_prediction": False,
            "same_day_outcomes_used_for_prediction": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
            "automatic_promotion": False,
        },
        "post_result_rule": "One frozen run only. A failure cannot be retuned on these test labels; a pass is research evidence only and does not authorize B05 or formal promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "scientific_component_pass": payload.get("scientific_component_pass"),
        "test_matches": (payload.get("split") or {}).get("test_matches"),
        "deltas": ((payload.get("metrics") or {}).get("deltas_candidate_minus_baseline")),
        "bootstrap": payload.get("bootstrap"),
        "development_gate": payload.get("development_gate"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
