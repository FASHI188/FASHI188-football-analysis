#!/usr/bin/env python3
"""ClubElo cross-league cold-start fallback for operational-shadow runtime R1.

Purpose
-------
Provide an auditable probability head when a target club has no usable history in
its new top-flight strength-reference competition (for example a newly promoted
club before matchweek 1).

This is deliberately research/operational shadow only. It does not promote the
older V5.1.5 ClubElo residual experiment and does not mutate CURRENT.

Method
------
* fetch provider histories from ClubElo's public CSV API;
* entity-map with explicit aliases first, otherwise strict normalized identity;
* for every historical top-flight training match, use the ClubElo rating valid on
  the calendar day strictly before that match;
* fit one deterministic ridge multinomial 1X2 model from Elo difference;
* fit one deterministic ridge Poisson mean model for total goals from Elo gap and
  average rating; convert its mean to a Negative-Binomial total distribution with
  a training-only method-of-moments dispersion estimate;
* build a smoothed empirical top-flight score prior and reconcile it to BOTH
  independent heads with the V6.26 IPF core.

No target result, future rating interval, market price, lineup or manually chosen
promotion penalty is used.
"""
from __future__ import annotations

import csv
import io
import math
import re
import time
import unicodedata
import urllib.request
from collections import Counter
from datetime import date, timedelta
from typing import Any

import three_stage_core_v6260 as three_stage
from football_v460_engine import negative_binomial_pmf
from platform_core import PlatformError, normalize_team_token, top_scores

EPS = 1e-12
RATING_SCALE = 400.0
MAX_SCORE_TOTAL = 10
TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7+")

# Explicit identity corrections only. They carry no probability meaning.
PROVIDER_ALIASES = {
    "Nott'm Forest": "Forest",
    "Nottingham Forest": "Forest",
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "AFC Bournemouth": "Bournemouth",
    "Brighton & Hove Albion": "Brighton",
    "Newcastle United": "Newcastle",
    "Tottenham Hotspur": "Tottenham",
    "West Ham United": "West Ham",
    "Wolverhampton Wanderers": "Wolves",
    "Leeds United": "Leeds",
    "Ipswich Town": "Ipswich",
    "Coventry City": "Coventry",
    "Hull City": "Hull",
    "Sheffield United": "Sheffield United",
}

_HISTORY_CACHE: dict[str, list[dict[str, Any]]] = {}
_SNAPSHOT_CACHE: dict[str, list[dict[str, str]]] = {}


def _ascii(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _provider_slug(value: str) -> str:
    compact = re.sub(r"[\s']", "", _ascii(value))
    return re.sub(r"[^A-Za-z0-9-]", "", compact)


def _fetch_csv(url: str, retries: int = 4) -> list[dict[str, str]]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-live-shadow/1.0"})
            with urllib.request.urlopen(req, timeout=35) as response:
                raw = response.read().decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(raw)))
            if not rows:
                raise RuntimeError("empty ClubElo CSV")
            return rows
        except Exception as exc:  # network retry is deterministic and bounded
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise PlatformError(f"ClubElo fetch failed after {retries} attempts: {url}: {last}")


def _daily_snapshot(day: date) -> list[dict[str, str]]:
    key = day.isoformat()
    if key not in _SNAPSHOT_CACHE:
        _SNAPSHOT_CACHE[key] = _fetch_csv(f"http://api.clubelo.com/{key}")
    return _SNAPSHOT_CACHE[key]


def _candidate_names(day: date, country: str) -> set[str]:
    rows = _daily_snapshot(day)
    return {
        str(r.get("Club") or "").strip()
        for r in rows
        if str(r.get("Country") or "").strip() == country and str(r.get("Club") or "").strip()
    }


def _resolve_provider_name(team: str, day: date, country: str) -> str:
    requested = PROVIDER_ALIASES.get(team, team)
    candidates = _candidate_names(day, country)
    token = normalize_team_token(requested)
    exact = [name for name in candidates if normalize_team_token(name) == token]
    if len(exact) == 1:
        return exact[0]
    # Explicit alias may still be absent from a daily snapshot if provider changes
    # level metadata; a history fetch by the audited alias is safer than fuzzy mapping.
    if team in PROVIDER_ALIASES:
        return requested
    raise PlatformError(
        f"ClubElo identity fail closed for {team!r}: normalized exact matches={exact[:5]}"
    )


def _history(provider_name: str) -> list[dict[str, Any]]:
    if provider_name in _HISTORY_CACHE:
        return _HISTORY_CACHE[provider_name]
    slug = _provider_slug(provider_name)
    if not slug:
        raise PlatformError(f"empty ClubElo slug for {provider_name!r}")
    rows = _fetch_csv(f"http://api.clubelo.com/{slug}")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            elo = float(row["Elo"])
            start = date.fromisoformat(str(row["From"]))
            end = date.fromisoformat(str(row["To"]))
        except Exception:
            continue
        parsed.append({"from": start, "to": end, "elo": elo})
    if not parsed:
        raise PlatformError(f"no usable ClubElo history for {provider_name}")
    _HISTORY_CACHE[provider_name] = parsed
    return parsed


def _elo_on(provider_name: str, day: date) -> float:
    for row in _history(provider_name):
        if row["from"] <= day <= row["to"]:
            return float(row["elo"])
    raise PlatformError(f"no ClubElo rating for {provider_name} on {day.isoformat()}")


def _softmax3(z_h: float, z_d: float) -> list[float]:
    m = max(z_h, z_d, 0.0)
    h = math.exp(z_h - m)
    d = math.exp(z_d - m)
    a = math.exp(-m)
    s = h + d + a
    return [h / s, d / s, a / s]


def _one_features(home_elo: float, away_elo: float) -> list[float]:
    diff = (home_elo - away_elo) / RATING_SCALE
    return [1.0, diff, abs(diff)]


def _fit_multinomial(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 300:
        raise PlatformError(f"ClubElo 1X2 training rows below minimum: {len(rows)}")
    beta = [0.0] * 6  # H three coefs, D three coefs; away is reference.
    ridge = 1.0 / len(rows)

    def objective_gradient(values: list[float]) -> tuple[float, list[float]]:
        obj = 0.0
        grad = [0.0] * 6
        for r in rows:
            x = _one_features(float(r["home_elo"]), float(r["away_elo"]))
            zh = sum(values[j] * x[j] for j in range(3))
            zd = sum(values[3 + j] * x[j] for j in range(3))
            q = _softmax3(zh, zd)
            y = int(r["result_index"])
            obj -= math.log(max(EPS, q[y]))
            eh = q[0] - (1.0 if y == 0 else 0.0)
            ed = q[1] - (1.0 if y == 1 else 0.0)
            for j in range(3):
                grad[j] += eh * x[j]
                grad[3 + j] += ed * x[j]
        n = len(rows)
        obj /= n
        grad = [g / n for g in grad]
        obj += 0.5 * ridge * sum(v * v for v in values)
        grad = [grad[i] + ridge * values[i] for i in range(6)]
        return obj, grad

    converged = False
    iterations = 0
    for it in range(1, 401):
        iterations = it
        obj, grad = objective_gradient(beta)
        g2 = sum(g * g for g in grad)
        if math.sqrt(g2) <= 1e-8:
            converged = True
            break
        step = 1.0
        accepted = False
        for _ in range(35):
            cand = [b - step * g for b, g in zip(beta, grad)]
            cobj, _ = objective_gradient(cand)
            if cobj <= obj - 1e-4 * step * g2:
                beta = cand
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break
    _, final_grad = objective_gradient(beta)
    if math.sqrt(sum(g * g for g in final_grad)) <= 1e-7:
        converged = True
    return {
        "beta": beta,
        "ridge_lambda": ridge,
        "iterations": iterations,
        "converged": converged,
        "training_count": len(rows),
    }


def _predict_one(model: dict[str, Any], home_elo: float, away_elo: float) -> list[float]:
    x = _one_features(home_elo, away_elo)
    b = model["beta"]
    return _softmax3(
        sum(float(b[j]) * x[j] for j in range(3)),
        sum(float(b[3 + j]) * x[j] for j in range(3)),
    )


def _fit_poisson_total(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 300:
        raise PlatformError(f"ClubElo total training rows below minimum: {len(rows)}")
    mean_rating = sum((float(r["home_elo"]) + float(r["away_elo"])) * 0.5 for r in rows) / len(rows)

    def features(r: dict[str, Any]) -> list[float]:
        he, ae = float(r["home_elo"]), float(r["away_elo"])
        return [1.0, abs(he - ae) / RATING_SCALE, (((he + ae) * 0.5) - mean_rating) / RATING_SCALE]

    beta = [math.log(2.7), 0.0, 0.0]
    ridge = 1.0 / len(rows)

    def objective_gradient(values: list[float]) -> tuple[float, list[float]]:
        obj = 0.0
        grad = [0.0] * 3
        for r in rows:
            x = features(r)
            eta = max(-4.0, min(4.0, sum(values[j] * x[j] for j in range(3))))
            mu = math.exp(eta)
            y = int(r["total"])
            obj += mu - y * eta  # constants omitted
            err = mu - y
            for j in range(3):
                grad[j] += err * x[j]
        n = len(rows)
        obj /= n
        grad = [g / n for g in grad]
        obj += 0.5 * ridge * sum(v * v for v in values[1:])
        for j in range(1, 3):
            grad[j] += ridge * values[j]
        return obj, grad

    iterations = 0
    converged = False
    for it in range(1, 401):
        iterations = it
        obj, grad = objective_gradient(beta)
        g2 = sum(g * g for g in grad)
        if math.sqrt(g2) <= 1e-8:
            converged = True
            break
        step = 0.5
        accepted = False
        for _ in range(35):
            cand = [b - step * g for b, g in zip(beta, grad)]
            cobj, _ = objective_gradient(cand)
            if cobj <= obj - 1e-4 * step * g2:
                beta = cand
                accepted = True
                break
            step *= 0.5
        if not accepted:
            break

    mus = []
    sq_resid = []
    for r in rows:
        x = features(r)
        eta = max(-4.0, min(4.0, sum(beta[j] * x[j] for j in range(3))))
        mu = math.exp(eta)
        mus.append(mu)
        sq_resid.append((int(r["total"]) - mu) ** 2)
    avg_mu = sum(mus) / len(mus)
    resid_var = sum(sq_resid) / len(sq_resid)
    if resid_var > avg_mu + 1e-8:
        k = avg_mu * avg_mu / max(1e-8, resid_var - avg_mu)
    else:
        k = 100.0
    k = min(100.0, max(1.5, k))
    return {
        "beta": beta,
        "mean_rating": mean_rating,
        "nb_dispersion_k": k,
        "ridge_lambda": ridge,
        "iterations": iterations,
        "converged": converged,
        "training_count": len(rows),
    }


def _predict_total(model: dict[str, Any], home_elo: float, away_elo: float) -> tuple[dict[str, float], float]:
    x = [
        1.0,
        abs(home_elo - away_elo) / RATING_SCALE,
        (((home_elo + away_elo) * 0.5) - float(model["mean_rating"])) / RATING_SCALE,
    ]
    eta = max(-4.0, min(4.0, sum(float(model["beta"][j]) * x[j] for j in range(3))))
    mu = math.exp(eta)
    k = float(model["nb_dispersion_k"])
    probs = [negative_binomial_pmf(i, mu, k) for i in range(7)]
    tail = max(0.0, 1.0 - sum(probs))
    out = {str(i): probs[i] for i in range(7)}
    out["7+"] = tail
    z = sum(out.values())
    return {key: float(out[key]) / z for key in TOTAL_KEYS}, mu


def _empirical_score_prior(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter((int(r["home_goals"]), int(r["away_goals"])) for r in rows)
    alpha = 0.20
    cells = []
    for total in range(MAX_SCORE_TOTAL + 1):
        for home in range(total + 1):
            away = total - home
            cells.append({
                "home_goals": home,
                "away_goals": away,
                "probability": float(counts[(home, away)]) + alpha,
            })
    z = sum(float(c["probability"]) for c in cells)
    for c in cells:
        c["probability"] = float(c["probability"]) / z
    return cells


def _prepare_training(history, freeze_day: date, country: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    teams = sorted({m.home_team for m in history} | {m.away_team for m in history})
    provider_names: dict[str, str] = {}
    identity_failures: dict[str, str] = {}
    for team in teams:
        try:
            provider_names[team] = _resolve_provider_name(team, freeze_day - timedelta(days=1), country)
        except Exception as exc:
            identity_failures[team] = f"{type(exc).__name__}: {exc}"

    rows = []
    missing_rating = 0
    for m in history:
        hp = provider_names.get(m.home_team)
        ap = provider_names.get(m.away_team)
        if not hp or not ap:
            continue
        rating_day = m.date.date() - timedelta(days=1)
        try:
            he = _elo_on(hp, rating_day)
            ae = _elo_on(ap, rating_day)
        except Exception:
            missing_rating += 1
            continue
        result_index = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
        rows.append({
            "home_elo": he,
            "away_elo": ae,
            "result_index": result_index,
            "total": int(m.home_goals + m.away_goals),
            "home_goals": int(m.home_goals),
            "away_goals": int(m.away_goals),
        })
    coverage = len(rows) / max(1, len(history))
    if coverage < 0.90:
        raise PlatformError(
            f"ClubElo historical training coverage below 90%: {len(rows)}/{len(history)}={coverage:.3f}; "
            f"identity_failures={identity_failures} missing_rating={missing_rating}"
        )
    return rows, {
        "historical_match_count": len(history),
        "training_count": len(rows),
        "coverage": coverage,
        "identity_failures": identity_failures,
        "missing_rating_rows": missing_rating,
        "provider_names": provider_names,
    }


def predict_clubelo_coldstart(
    history,
    competition_id: str,
    home_team: str,
    away_team: str,
    freeze,
    *,
    country: str = "ENG",
) -> dict[str, Any]:
    if competition_id != "ENG_PremierLeague":
        raise PlatformError("ClubElo cold-start R1 is currently validated only for ENG_PremierLeague")
    if not history:
        raise PlatformError("ClubElo cold-start requires top-flight historical training matches")

    freeze_day = freeze.date()
    rows, training_audit = _prepare_training(history, freeze_day, country)
    one_model = _fit_multinomial(rows)
    total_model = _fit_poisson_total(rows)

    target_rating_day = freeze_day - timedelta(days=1)
    home_provider = _resolve_provider_name(home_team, target_rating_day, country)
    away_provider = _resolve_provider_name(away_team, target_rating_day, country)
    home_elo = _elo_on(home_provider, target_rating_day)
    away_elo = _elo_on(away_provider, target_rating_day)

    one = _predict_one(one_model, home_elo, away_elo)
    total, mu_total = _predict_total(total_model, home_elo, away_elo)
    prior = _empirical_score_prior(rows)
    matrix, reconciliation = three_stage.reconcile(
        prior,
        one,
        [float(total[k]) for k in TOTAL_KEYS],
    )
    if not reconciliation.get("converged"):
        raise PlatformError(f"ClubElo cold-start reconciliation failed: {reconciliation}")
    final_one = three_stage.one_x_two_vector(matrix)
    final_total = three_stage.total_goals_vector(matrix)
    return {
        "competition_id": competition_id,
        "season": "CROSS_LEAGUE_CLUBELO_COLDSTART",
        "history_matches": len(history),
        "team_sample": {
            "home_raw_matches": 0.0,
            "away_raw_matches": 0.0,
            "home_effective_matches": 0.0,
            "away_effective_matches": 0.0,
            "ess": 0.0,
            "mu_total": mu_total,
        },
        "probabilities": {
            "one_x_two": {"home": final_one[0], "draw": final_one[1], "away": final_one[2]},
            "total_goals": {k: final_total[i] for i, k in enumerate(TOTAL_KEYS)},
            "score_matrix": matrix,
        },
        "top_scores": top_scores(matrix, 10),
        "audit": {
            "classification": "OPERATIONAL_SHADOW_CLUBELO_CROSS_LEAGUE_COLDSTART_R1",
            "formal_weight": 0,
            "target_rating_day": target_rating_day.isoformat(),
            "target": {
                "home_team": home_team,
                "home_provider": home_provider,
                "home_elo": home_elo,
                "away_team": away_team,
                "away_provider": away_provider,
                "away_elo": away_elo,
                "elo_difference": home_elo - away_elo,
            },
            "training": training_audit,
            "one_x_two_model": one_model,
            "total_model": total_model,
            "reconciliation": reconciliation,
            "manual_promotion_penalty": False,
            "market_used": False,
            "target_result_used": False,
        },
    }
