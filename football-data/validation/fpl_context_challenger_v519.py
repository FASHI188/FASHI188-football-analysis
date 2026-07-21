#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from bayesian_dynamic_state_oof_v500 import _metric_row, _paired_summary
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, load_json, read_processed_matches, score_matrix_rows

CONFIG = ROOT / "config" / "fpl_context_challenger_v519.json"
OUT = ROOT / "manifests" / "fpl_context_challenger_v519_status.json"
EVIDENCE = ROOT / "evidence" / "fpl_context_v519"
EPS = 1e-15

TEAM_ALIASES = {
    "Man Utd": "Man United",
    "Spurs": "Tottenham",
    "Nott'm Forest": "Nott'm Forest",
    "Man City": "Man City",
    "Newcastle": "Newcastle",
    "Wolves": "Wolves",
}

FEATURES = [
    "unavailable_count",
    "injured_count",
    "doubtful_count",
    "suspended_count",
    "news_count",
    "unavailable_selected_sum",
    "unavailable_cost_sum",
    "unavailable_form_sum",
    "news_selected_sum",
    "known_chance_mean",
    "top11_form_sum",
    "top11_unavailable_count",
    "top11_unavailable_selected_sum",
]


def _fetch(url: str, retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except Exception as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"download failed {url}: {last}")


def _fetch_json(url: str) -> dict[str, Any]:
    return json.loads(_fetch(url).decode("utf-8"))


def _csv_bytes(payload: bytes) -> tuple[list[dict[str, str]], list[str]]:
    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader), list(reader.fieldnames or [])


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(str(value).strip())
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _chance(value: Any) -> float | None:
    token = str(value or "").strip()
    if token == "":
        return None
    try:
        number = float(token)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _resolve_source_commit() -> str:
    payload = _fetch_json("https://api.github.com/repos/olbauday/FPL-Core-Insights/commits/main")
    sha = str(payload.get("sha") or "")
    if len(sha) < 20:
        raise RuntimeError("could not resolve immutable FPL source commit")
    return sha


def _download_gameweeks(source_sha: str) -> tuple[dict[int, dict[str, Any]], dict[str, str]]:
    base = f"https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/{source_sha}/data/2025-2026/By%20Gameweek"
    output: dict[int, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for gw in range(1, 39):
        bundle = {}
        for filename in ("players.csv", "player_gameweek_stats.csv", "teams.csv", "fixtures.csv"):
            url = f"{base}/GW{gw}/{filename}"
            raw = _fetch(url)
            hashes[f"GW{gw}/{filename}"] = hashlib.sha256(raw).hexdigest()
            rows, columns = _csv_bytes(raw)
            bundle[filename] = {"rows": rows, "columns": columns, "url": url}
        output[gw] = bundle
    return output, hashes


def _team_features(bundle: dict[str, Any]) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    teams = bundle["teams.csv"]["rows"]
    players = bundle["players.csv"]["rows"]
    stats = bundle["player_gameweek_stats.csv"]["rows"]

    team_by_code = {str(row.get("code") or ""): str(row.get("name") or "") for row in teams}
    player_to_team = {}
    for row in players:
        pid = str(row.get("player_id") or "")
        name = team_by_code.get(str(row.get("team_code") or ""))
        if pid and name:
            player_to_team[pid] = name

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched = 0
    for row in stats:
        pid = str(row.get("id") or row.get("player_id") or "")
        team = player_to_team.get(pid)
        if not team:
            unmatched += 1
            continue
        grouped[team].append(row)

    features: dict[str, dict[str, float]] = {}
    for team, rows in grouped.items():
        unavailable = [row for row in rows if str(row.get("status") or "").strip().lower() not in ("", "a")]
        known_chances = [_chance(row.get("chance_of_playing_this_round")) for row in rows]
        known_chances = [value for value in known_chances if value is not None]
        ranked = sorted(rows, key=lambda row: _float(row.get("selected_by_percent")), reverse=True)[:11]
        features[team] = {
            "unavailable_count": float(len(unavailable)),
            "injured_count": float(sum(1 for row in rows if str(row.get("status") or "").lower() == "i")),
            "doubtful_count": float(sum(1 for row in rows if str(row.get("status") or "").lower() == "d")),
            "suspended_count": float(sum(1 for row in rows if str(row.get("status") or "").lower() == "s")),
            "news_count": float(sum(1 for row in rows if str(row.get("news") or "").strip())),
            "unavailable_selected_sum": sum(_float(row.get("selected_by_percent")) for row in unavailable),
            "unavailable_cost_sum": sum(_float(row.get("now_cost")) for row in unavailable),
            "unavailable_form_sum": sum(_float(row.get("form")) for row in unavailable),
            "news_selected_sum": sum(_float(row.get("selected_by_percent")) for row in rows if str(row.get("news") or "").strip()),
            "known_chance_mean": mean(known_chances) if known_chances else 100.0,
            "top11_form_sum": sum(_float(row.get("form")) for row in ranked),
            "top11_unavailable_count": float(sum(1 for row in ranked if str(row.get("status") or "").strip().lower() not in ("", "a"))),
            "top11_unavailable_selected_sum": sum(_float(row.get("selected_by_percent")) for row in ranked if str(row.get("status") or "").strip().lower() not in ("", "a")),
        }
    return features, {
        "team_count": len(features),
        "unmatched_player_rows": unmatched,
        "player_row_count": len(stats),
        "team_names": sorted(features),
    }


def _fixture_team(value: str) -> str:
    return TEAM_ALIASES.get(str(value), str(value))


def _date_from_kickoff(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    token = token.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(token).date().isoformat()
    except Exception:
        return token[:10]


def _match_lookup(matches) -> dict[tuple[str, str, str], list[Any]]:
    result: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for match in matches:
        result[(match.date.date().isoformat(), match.home_team, match.away_team)].append(match)
    return result


def _pair_processed_match(lookup, date: str, home: str, away: str):
    candidates = lookup.get((date, _fixture_team(home), _fixture_team(away)), [])
    if len(candidates) == 1:
        return candidates[0]
    return None


def _expected_margin(matrix: list[dict[str, Any]]) -> float:
    return sum((h - a) * p for h, a, p in score_matrix_rows(matrix))


def _project(matrix: list[dict[str, Any]], residual: float, scale: float) -> tuple[list[dict[str, Any]], dict[str, float]]:
    grouped: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
    original = defaultdict(float)
    for h, a, p in score_matrix_rows(matrix):
        grouped[h + a].append((h, a, p))
        original[h + a] += p
    out = []
    for total, cells in grouped.items():
        mass = sum(p for _, _, p in cells)
        weighted = []
        for h, a, p in cells:
            exponent = max(-40.0, min(40.0, float(scale) * float(residual) * float(h - a)))
            weighted.append((h, a, p * math.exp(exponent)))
        denom = sum(item[2] for item in weighted)
        if denom <= 0:
            raise PlatformError(f"FPL conditional KL normalization failed total={total}")
        for h, a, weight in weighted:
            out.append({"home_goals": h, "away_goals": a, "probability": mass * weight / denom})
    total_prob = sum(float(cell["probability"]) for cell in out)
    out = [{**cell, "probability": float(cell["probability"]) / total_prob} for cell in out]
    new = defaultdict(float)
    for h, a, p in score_matrix_rows(out):
        new[h + a] += p
    return out, {
        "probability_sum_residual": abs(sum(float(cell["probability"]) for cell in out) - 1.0),
        "max_total_marginal_residual": max(abs(float(new[t]) - float(original[t])) for t in original),
    }


def _standardize_fit(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = x.mean(axis=0)
    sigma = x.std(axis=0)
    sigma = np.where(sigma < 1e-9, 1.0, sigma)
    return mu, sigma


def _standardize(x: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    return (x - mu) / sigma


def _ridge_fit(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    return np.linalg.solve(design.T @ design + penalty, design.T @ y)


def _ridge_predict(x: np.ndarray, coef: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(x)), x]) @ coef


def _objective(rows: list[dict[str, Any]]) -> float:
    return mean(float(row["one_x_two_rps"]) for row in rows) + 0.25 * mean(float(row["one_x_two_brier"]) for row in rows) + 0.02 * mean(float(row["joint_log"]) for row in rows)


def _bootstrap(rows: list[dict[str, Any]], cand: str, base: str, draws: int, block_size: int, seed: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["date"], row["match_key"]))
    blocks = [ordered[i:i + block_size] for i in range(0, len(ordered), block_size)]
    point = mean(float(row[cand]) - float(row[base]) for row in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = []
        for _ in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        values.append(mean(float(row[cand]) - float(row[base]) for row in sample))
    values.sort()
    return {
        "mean_difference": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "blocks": len(blocks),
        "draws": draws,
    }


def main() -> int:
    cfg = load_json(CONFIG)
    source_sha = _resolve_source_commit()
    bundles, source_hashes = _download_gameweeks(source_sha)
    feature_by_gw = {}
    feature_audit = {}
    for gw, bundle in bundles.items():
        feature_by_gw[gw], feature_audit[gw] = _team_features(bundle)

    cid = cfg["competition_id"]
    season = cfg["season"]
    report = load_json(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError("missing frozen formal parameters")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    all_matches = read_processed_matches(cid)
    target_matches = [m for m in all_matches if str(m.season) == season]
    lookup = _match_lookup(target_matches)

    rows = []
    identity_failures = []
    baseline_failures = 0
    for gw in range(1, 39):
        fixtures = bundles[gw]["fixtures.csv"]["rows"]
        features = feature_by_gw[gw]
        for fixture in fixtures:
            date = _date_from_kickoff(fixture.get("kickoff_time"))
            home = str(fixture.get("home_team") or "")
            away = str(fixture.get("away_team") or "")
            if not date or not home or not away:
                continue
            match = _pair_processed_match(lookup, date, home, away)
            if match is None:
                identity_failures.append({"gw": gw, "date": date, "home": home, "away": away})
                continue
            home_feat = features.get(home) or features.get(TEAM_ALIASES.get(home, ""))
            away_feat = features.get(away) or features.get(TEAM_ALIASES.get(away, ""))
            if home_feat is None or away_feat is None:
                identity_failures.append({"gw": gw, "date": date, "home": home, "away": away, "reason": "team_feature_missing"})
                continue
            try:
                baseline = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
                if abs(temperature - 1.0) > 1e-15:
                    baseline = temperature_scale_matrix(baseline, temperature)
            except PlatformError:
                baseline_failures += 1
                continue
            vector = [float(home_feat[name]) - float(away_feat[name]) for name in FEATURES]
            rows.append({
                "gw": gw,
                "date": date,
                "match_key": f"{cid}:{date}:{match.home_team}:{match.away_team}",
                "match": match,
                "features": vector,
                "baseline": baseline,
                "baseline_metrics": _metric_row(baseline, match),
                "target_residual": float(match.home_goals - match.away_goals) - _expected_margin(baseline),
            })

    rows.sort(key=lambda row: (row["gw"], row["date"], row["match_key"]))
    if len(rows) < 250:
        raise PlatformError(f"too few FPL-aligned baseline rows: {len(rows)}")
    outer_cut = int(math.floor(len(rows) * float(cfg["model"]["outer_training_fraction"])))
    train = rows[:outer_cut]
    forward = rows[outer_cut:]
    inner_cut = int(math.floor(len(train) * float(cfg["model"]["inner_train_fraction"])))
    inner_train = train[:inner_cut]
    inner_valid = train[inner_cut:]
    if len(inner_train) < 70 or len(inner_valid) < 30:
        raise PlatformError("FPL inner chronological split too small")

    x_inner = np.asarray([row["features"] for row in inner_train], dtype=float)
    y_inner = np.asarray([row["target_residual"] for row in inner_train], dtype=float)
    mu_inner, sigma_inner = _standardize_fit(x_inner)
    x_inner_s = _standardize(x_inner, mu_inner, sigma_inner)
    x_valid_s = _standardize(np.asarray([row["features"] for row in inner_valid], dtype=float), mu_inner, sigma_inner)

    selections = []
    for alpha in cfg["model"]["ridge_alpha_grid"]:
        coef = _ridge_fit(x_inner_s, y_inner, float(alpha))
        pred = _ridge_predict(x_valid_s, coef)
        for scale in cfg["model"]["projection_scale_grid"]:
            metric_rows = []
            max_prob = max_total = 0.0
            for row, residual in zip(inner_valid, pred):
                candidate, audit = _project(row["baseline"], float(residual), float(scale))
                metrics = _metric_row(candidate, row["match"])
                metric_rows.append(metrics)
                max_prob = max(max_prob, float(audit["probability_sum_residual"]))
                max_total = max(max_total, float(audit["max_total_marginal_residual"]))
            selections.append({
                "alpha": float(alpha),
                "scale": float(scale),
                "objective": _objective(metric_rows),
                "validation_rows": len(metric_rows),
                "max_probability_sum_residual": max_prob,
                "max_total_marginal_residual": max_total,
            })
    selections.sort(key=lambda item: (item["objective"], abs(item["scale"]), item["alpha"]))
    selected = selections[0]

    x_train = np.asarray([row["features"] for row in train], dtype=float)
    y_train = np.asarray([row["target_residual"] for row in train], dtype=float)
    mu, sigma = _standardize_fit(x_train)
    coef = _ridge_fit(_standardize(x_train, mu, sigma), y_train, float(selected["alpha"]))
    pred_forward = _ridge_predict(_standardize(np.asarray([row["features"] for row in forward], dtype=float), mu, sigma), coef)

    paired = []
    max_prob = max_total = 0.0
    for row, residual in zip(forward, pred_forward):
        candidate, audit = _project(row["baseline"], float(residual), float(selected["scale"]))
        cand = _metric_row(candidate, row["match"])
        base = row["baseline_metrics"]
        item = {"date": row["date"], "match_key": row["match_key"]}
        for metric in ("one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"):
            item[f"baseline_{metric}"] = base[metric]
            item[f"candidate_{metric}"] = cand[metric]
        paired.append(item)
        max_prob = max(max_prob, float(audit["probability_sum_residual"]))
        max_total = max(max_total, float(audit["max_total_marginal_residual"]))

    pooled = _paired_summary(paired)
    boot = cfg["bootstrap"]
    ci = {
        "one_x_two_brier": _bootstrap(paired, "candidate_one_x_two_brier", "baseline_one_x_two_brier", int(boot["draws"]), int(boot["block_size"]), int(boot["seed"]) + 1),
        "one_x_two_rps": _bootstrap(paired, "candidate_one_x_two_rps", "baseline_one_x_two_rps", int(boot["draws"]), int(boot["block_size"]), int(boot["seed"]) + 2),
        "joint_log": _bootstrap(paired, "candidate_joint_log", "baseline_joint_log", int(boot["draws"]), int(boot["block_size"]), int(boot["seed"]) + 3),
    }
    gate = cfg["forward_gate"]
    brier_improves = ci["one_x_two_brier"]["ci95_upper"] < 0.0
    rps_improves = ci["one_x_two_rps"]["ci95_upper"] < 0.0
    ni = float(gate["other_one_x_two_proper_score_ci95_upper_noninferiority"])
    other_noninferior = (
        (brier_improves and ci["one_x_two_rps"]["ci95_upper"] <= ni)
        or (rps_improves and ci["one_x_two_brier"]["ci95_upper"] <= ni)
        or (brier_improves and rps_improves)
    )
    checks = {
        "minimum_forward_predictions": len(paired) >= int(gate["minimum_forward_predictions"]),
        "nonzero_projection_scale": abs(float(selected["scale"])) > 1e-12,
        "at_least_one_one_x_two_proper_score_ci95_upper_below_zero": brier_improves or rps_improves,
        "other_one_x_two_proper_score_ci95_upper_noninferiority": other_noninferior,
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate"] + 1e-12 >= pooled["one_x_two_accuracy"]["baseline"],
        "joint_log_nonworse": pooled["joint_log"]["candidate"] <= pooled["joint_log"]["baseline"] + 1e-12,
        "score_top1_nonworse": pooled["score_top1"]["candidate"] + 1e-12 >= pooled["score_top1"]["baseline"],
        "score_top3_nonworse": pooled["score_top3"]["candidate"] + 1e-12 >= pooled["score_top3"]["baseline"],
        "total_top1_exactly_preserved": abs(pooled["total_top1"]["candidate_minus_baseline"]) <= 1e-12,
        "total_top2_exactly_preserved": abs(pooled["total_top2"]["candidate_minus_baseline"]) <= 1e-12,
        "total_rps_exactly_preserved": abs(pooled["total_rps"]["candidate_minus_baseline"]) <= 1e-12,
        "probability_conservation": max_prob <= float(gate["probability_sum_tolerance"]),
        "total_marginal_conservation": max_total <= float(gate["total_marginal_tolerance"]),
    }

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    feature_path = EVIDENCE / "team_gameweek_features.jsonl"
    with feature_path.open("w", encoding="utf-8") as handle:
        for gw in range(1, 39):
            for team, values in sorted(feature_by_gw[gw].items()):
                handle.write(json.dumps({"gameweek": gw, "team": team, "features": values}, ensure_ascii=False, sort_keys=True) + "\n")
    hash_path = EVIDENCE / "source_file_hashes.json"
    hash_path.write_text(json.dumps({"source_commit_sha": source_sha, "files": source_hashes}, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = {
        "schema_version": "V5.1.9-fpl-context-challenger-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": cid,
        "season": season,
        "status": "FPL_CONTEXT_SIGNAL_PASS_SHADOW_ONLY" if all(checks.values()) else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "source_repository": cfg["source_repository"],
        "source_commit_sha": source_sha,
        "source_file_count": len(source_hashes),
        "source_hash_manifest": str(hash_path.relative_to(ROOT)),
        "feature_evidence": str(feature_path.relative_to(ROOT)),
        "allowed_fields": cfg["allowed_same_gameweek_fields"],
        "forbidden_fields": cfg["forbidden_same_gameweek_fields"],
        "aligned_rows": len(rows),
        "identity_failure_count": len(identity_failures),
        "identity_failure_examples": identity_failures[:20],
        "baseline_failure_count": baseline_failures,
        "outer_training_rows": len(train),
        "inner_training_rows": len(inner_train),
        "inner_validation_rows": len(inner_valid),
        "forward_prediction_count": len(paired),
        "selected_hyperparameters": selected,
        "selection_top10": selections[:10],
        "ridge_coefficients": {"intercept": float(coef[0]), **{name: float(value) for name, value in zip(FEATURES, coef[1:])}},
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "max_probability_sum_residual": max_prob,
        "max_total_marginal_residual": max_total,
        "checks": checks,
        "feature_audit": feature_audit,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_status": "SHADOW_ONLY_SOURCE_COLLECTION_TIMING_NOT_INDEPENDENTLY_BOUND",
        "policy": "Same-gameweek use is restricted to source-described deadline snapshot fields. Match-performance fields from the target gameweek are forbidden. Hyperparameters are selected inside the first chronological 45%; the final 55% is untouched forward validation. Total-goal marginals are preserved exactly."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "source_commit_sha": source_sha,
        "aligned_rows": len(rows),
        "forward_prediction_count": len(paired),
        "selected_hyperparameters": selected,
        "accuracy_diff": pooled["one_x_two_accuracy"]["candidate_minus_baseline"],
        "brier_diff": pooled["one_x_two_brier"]["candidate_minus_baseline"],
        "rps_diff": pooled["one_x_two_rps"]["candidate_minus_baseline"],
        "score_top1_diff": pooled["score_top1"]["candidate_minus_baseline"],
        "score_top3_diff": pooled["score_top3"]["candidate_minus_baseline"],
        "checks": checks
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
