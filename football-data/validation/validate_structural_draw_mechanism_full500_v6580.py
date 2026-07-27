#!/usr/bin/env python3
"""V6.58.0 structural draw-mechanism 1X2 challenger for Full500.

This is intentionally NOT another three-class boosted tree. It models the DRAW event
directly as a residual around the de-vigged closing-market draw probability.

Mechanism features are all observable strictly before the target match:
- market parity / entropy / margin;
- closing O/U 2.5 and its early->closing move as a low-event proxy;
- pre-match xG total / xG imbalance / deep-attacking total;
- rolling shot and shot-on-target pace;
- absolute Elo / recent-form imbalance;
- each team's own prior draw propensity over the last 5/20 completed matches;
- competition-wide prior draw propensity over the last 100 completed matches.

Same-date matches are frozen before same-date outcomes update any draw history.
The target result never enters its own draw-state features.

Model:
  logit(P(draw)) = logit(P_market(draw)) + alpha * (b0 + X beta)
where beta is ridge-regularized binary logistic regression fitted only on historical
training seasons. Home/away residual probability is distributed in the same ratio as
the closing market, so the only modeled mechanism is draw-vs-decisive.

Historical folds (fully predeclared):
- train 2022/23 -> validate 2023/24
- train 2022/23+2023/24 -> validate 2024/25

Candidate selection never reads Full500 A100 labels. Historical gate before A100:
- mean Top-1 uplift >= +0.5pp;
- neither fold negative;
- each fold LogLoss and RPS no worse than market by >0.005.
Only after passing that gate may the fixed Full500 A_FAST100 be scored.
B300/C100 remain unread unless the existing staged gate permits them.
Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402

OUT = ROOT / "manifests" / "v6_structural_draw_mechanism_full500_v6580_status.json"
FEATURES = ROOT / "manifests" / "full500_v6493" / "full500_features_v6493.jsonl"
LABELS = ROOT / "manifests" / "full500_v6493" / "full500_development_labels_v6493.jsonl"
HIST_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"
PART = "A_FAST100"
RIDGES = (0.1, 0.3, 1.0, 3.0, 10.0)
ALPHAS = (0.25, 0.50, 0.75, 1.0)
HIST_REQUIRED_MEAN_UPLIFT_PP = 0.5
PROPER_TOL = 0.005
EPS = 1e-10

STRUCT_NAMES = (
    "market_draw",
    "market_side_absdiff",
    "market_margin",
    "market_entropy",
    "closing_under25",
    "under25_move",
    "xg_edge_sum",
    "xg_edge_absdiff",
    "deep_edge_sum",
    "total_shot_chance",
    "total_sot_chance",
    "abs_elo_slow_diff",
    "abs_elo_fast_diff",
    "abs_form_pts5_diff",
    "abs_form_gd5_diff",
    "team_draw20_mean",
    "team_draw20_absdiff",
    "team_draw5_mean",
    "league_draw100",
    "parity_x_under25",
    "teamdraw_x_under25",
)


def _logit(p: float) -> float:
    q = min(1.0 - 1e-8, max(1e-8, float(p)))
    return math.log(q / (1.0 - q))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-z))


def _draw_state_maps(cids: list[str]) -> dict[str, dict[tuple[str, str, str, str], dict[str, float | None]]]:
    """Strict-prior draw propensities; all matches on a date are captured before updates."""
    outputs: dict[str, dict[tuple[str, str, str, str], dict[str, float | None]]] = {}
    wanted = set(HIST_SEASONS) | {TEST_SEASON}
    for cid in cids:
        matches = sorted(v632.read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
        team_hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=20))
        league_hist: deque[float] = deque(maxlen=100)
        by_date: dict[Any, list[Any]] = defaultdict(list)
        for m in matches:
            by_date[m.date].append(m)
        out: dict[tuple[str, str, str, str], dict[str, float | None]] = {}
        for dt in sorted(by_date):
            day = sorted(by_date[dt], key=lambda m: (m.home_team, m.away_team))
            pending: list[tuple[Any, str, str, float]] = []
            for m in day:
                h = v632._token(cid, m.home_team)
                a = v632._token(cid, m.away_team)
                hh = list(team_hist[h]); ah = list(team_hist[a]); lh = list(league_hist)
                league = float(np.mean(lh)) if lh else None
                h20 = float(np.mean(hh)) if hh else league
                a20 = float(np.mean(ah)) if ah else league
                h5 = float(np.mean(hh[-5:])) if hh else league
                a5 = float(np.mean(ah[-5:])) if ah else league
                if str(m.season) in wanted:
                    key = (str(m.season), dt.date().isoformat(), h, a)
                    out[key] = {
                        "home_draw20": h20,
                        "away_draw20": a20,
                        "home_draw5": h5,
                        "away_draw5": a5,
                        "league_draw100": league,
                        "home_prior_n": float(len(hh)),
                        "away_prior_n": float(len(ah)),
                        "league_prior_n": float(len(lh)),
                    }
                d = 1.0 if int(m.home_goals) == int(m.away_goals) else 0.0
                pending.append((m, h, a, d))
            for _m, h, a, d in pending:
                team_hist[h].append(d); team_hist[a].append(d); league_hist.append(d)
        outputs[cid] = out
    return outputs


def _metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    picks = probs.argmax(axis=1)
    n = len(y)
    hits = int(np.sum(picks == y))
    one = np.eye(3, dtype=float)[y]
    brier = float(np.mean(np.sum((probs - one) ** 2, axis=1)))
    logloss = float(-np.mean(np.log(np.clip(probs[np.arange(n), y], EPS, 1.0))))
    c1 = probs[:, 0] - (y == 0)
    c2 = probs[:, 0] + probs[:, 1] - (y <= 1)
    rps = float(np.mean((c1 * c1 + c2 * c2) / 2.0))
    return {
        "count": int(n), "hits": hits, "top1": hits / n,
        "brier": brier, "logloss": logloss, "rps": rps,
        "predicted_counts": dict(Counter(str(int(z)) for z in picks)),
        "actual_counts": dict(Counter(str(int(z)) for z in y)),
    }


def _market_probs(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([r["market"] for r in rows], dtype=float)


def _struct_vector(row: dict[str, Any], base_index: dict[str, int], base_count: int, state: dict[str, float | None]) -> list[float]:
    x = [float(z) for z in row["x"]]
    market = [float(z) for z in row["market"]]
    if len(x) <= base_count + 14:
        raise RuntimeError("rich-market feature packet shorter than V6.51 contract")

    # V6.51 market_extra layout after the base features:
    # early3, delta3, logmove3, ou_early2, ou_close2, ou_delta2, ...
    closing_under = float(x[base_count + 12])
    under_move = float(x[base_count + 14])

    md = float(market[1])
    h, a = float(market[0]), float(market[2])
    side_abs = abs(h - a)
    parity = max(0.0, 1.0 - side_abs)

    def s(name: str) -> float:
        return float(x[base_index[name]])

    league_draw = state.get("league_draw100")
    h20 = state.get("home_draw20"); a20 = state.get("away_draw20")
    h5 = state.get("home_draw5"); a5 = state.get("away_draw5")
    # Result-blind fallback: no historical draw observation -> current market draw probability.
    league_draw = md if league_draw is None else float(league_draw)
    h20 = league_draw if h20 is None else float(h20)
    a20 = league_draw if a20 is None else float(a20)
    h5 = h20 if h5 is None else float(h5)
    a5 = a20 if a5 is None else float(a5)
    team20 = 0.5 * (h20 + a20)
    team5 = 0.5 * (h5 + a5)

    return [
        md,
        side_abs,
        s("market_margin"),
        s("market_entropy"),
        closing_under,
        under_move,
        s("xg_edge_sum"),
        s("xg_edge_absdiff"),
        s("deep_edge_sum"),
        s("total_shot_chance"),
        s("total_sot_chance"),
        abs(s("elo_slow_diff")),
        abs(s("elo_fast_diff")),
        abs(s("form_pts5_diff")),
        abs(s("form_gd5_diff")),
        team20,
        abs(h20 - a20),
        team5,
        league_draw,
        parity * closing_under,
        team20 * closing_under,
    ]


def _build_historical() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, base_audit, base_names = v632._build_rows()
    base_index = {n: i for i, n in enumerate(base_names)}
    missing_names = [n for n in (
        "market_margin", "market_entropy", "xg_edge_sum", "xg_edge_absdiff", "deep_edge_sum",
        "total_shot_chance", "total_sot_chance", "elo_slow_diff", "elo_fast_diff",
        "form_pts5_diff", "form_gd5_diff",
    ) if n not in base_index]
    if missing_names:
        raise RuntimeError(f"V6.58 missing base features: {missing_names}")
    cids = sorted({str(r["competition_id"]) for r in base_rows})
    raw = v651.raw_lookups(cids)
    states = _draw_state_maps(cids)
    rows: list[dict[str, Any]] = []
    misses = Counter()
    for r in base_rows:
        season = str(r["season"])
        if season not in HIST_SEASONS:
            continue
        cid = str(r["competition_id"])
        htok = v632._token(cid, str(r["home_team"])); atok = v632._token(cid, str(r["away_team"]))
        key = (season, str(r["date"]), htok, atok)
        rr = raw[cid].get(key)
        st = states[cid].get(key)
        if rr is None:
            misses["raw_market_join"] += 1; continue
        if st is None:
            misses["draw_state_join"] += 1; continue
        extra = v651.market_extra(rr, [float(z) for z in r["market"]])
        if extra is None:
            misses["rich_market_packet"] += 1; continue
        z = dict(r)
        z["x"] = [float(v) for v in r["x"]] + [float(v) for v in extra]
        z["struct"] = _struct_vector(z, base_index, len(base_names), st)
        rows.append(z)
    return rows, {
        "base_audit": base_audit,
        "base_feature_count": len(base_names),
        "struct_feature_names": list(STRUCT_NAMES),
        "struct_feature_count": len(STRUCT_NAMES),
        "misses": dict(misses),
        "by_season": dict(Counter(str(r["season"]) for r in rows)),
        "same_date_draw_state_frozen": True,
    }


def _fit_binary(train: list[dict[str, Any]], ridge: float) -> dict[str, Any]:
    X = np.asarray([r["struct"] for r in train], dtype=float)
    y = np.asarray([1.0 if int(r["y"]) == 1 else 0.0 for r in train], dtype=float)
    offset = np.asarray([_logit(float(r["market"][1])) for r in train], dtype=float)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    Z = (X - mean) / std
    A = np.column_stack([np.ones(len(Z)), Z])

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + A @ beta
        p = _sigmoid(eta)
        nll = -np.sum(y * np.log(np.clip(p, EPS, 1.0)) + (1.0 - y) * np.log(np.clip(1.0 - p, EPS, 1.0)))
        # Do not penalize intercept.
        penalty = 0.5 * float(ridge) * float(np.dot(beta[1:], beta[1:]))
        grad = A.T @ (p - y)
        grad[1:] += float(ridge) * beta[1:]
        return float(nll + penalty), grad

    beta0 = np.zeros(A.shape[1], dtype=float)
    res = minimize(lambda b: objective(b)[0], beta0, jac=lambda b: objective(b)[1], method="L-BFGS-B", options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        raise RuntimeError(f"draw logistic fit failed ridge={ridge}: {res.message}")
    return {
        "ridge": float(ridge),
        "mean": mean,
        "std": std,
        "beta": np.asarray(res.x, dtype=float),
        "converged": bool(res.success),
        "iterations": int(res.nit),
        "objective": float(res.fun),
        "gradient_max_abs": float(np.max(np.abs(objective(np.asarray(res.x, dtype=float))[1]))),
    }


def _predict(rows: list[dict[str, Any]], model: dict[str, Any], alpha: float) -> np.ndarray:
    X = np.asarray([r["struct"] for r in rows], dtype=float)
    Z = (X - model["mean"]) / model["std"]
    A = np.column_stack([np.ones(len(Z)), Z])
    market = _market_probs(rows)
    offset = np.asarray([_logit(float(p[1])) for p in market], dtype=float)
    residual = A @ model["beta"]
    draw = _sigmoid(offset + float(alpha) * residual)
    side_total = np.clip(market[:, 0] + market[:, 2], EPS, 1.0)
    home_share = market[:, 0] / side_total
    out = np.zeros_like(market)
    out[:, 1] = draw
    out[:, 0] = (1.0 - draw) * home_share
    out[:, 2] = (1.0 - draw) * (1.0 - home_share)
    out /= out.sum(axis=1, keepdims=True)
    return out


def _load_a100_labels() -> dict[int, int]:
    labels: dict[int, int] = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("partition") == PART:
            labels[int(r["full_index"])] = int(r["label"])
    if len(labels) != 100:
        raise RuntimeError(f"A100 labels expected100 got{len(labels)}")
    return labels


def _build_a100(base_names: list[str]) -> list[dict[str, Any]]:
    base_index = {n: i for i, n in enumerate(base_names)}
    frozen = [json.loads(x) for x in FEATURES.read_text(encoding="utf-8").splitlines() if x.strip()]
    frozen = [r for r in frozen if r.get("partition") == PART]
    if len(frozen) != 100:
        raise RuntimeError(f"A100 features expected100 got{len(frozen)}")
    cids = sorted({str(r["competition_id"]) for r in frozen})
    raw = v651.raw_lookups(cids)
    states = _draw_state_maps(cids)
    out = []
    for r in frozen:
        cid = str(r["competition_id"]); season = str(r["season"]); date = str(r["date"])
        htok = v632._token(cid, str(r["home_team"])); atok = v632._token(cid, str(r["away_team"]))
        key = (season, date, htok, atok)
        rr = raw[cid].get(key); st = states[cid].get(key)
        if rr is None or st is None:
            raise RuntimeError(f"A100 V6.58 join missing {key}")
        extra = v651.market_extra(rr, [float(z) for z in r["market"]])
        if extra is None:
            raise RuntimeError(f"A100 V6.58 rich market missing {key}")
        z = {
            "full_index": int(r["full_index"]), "competition_id": cid, "season": season,
            "date": date, "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "market": [float(v) for v in r["market"]],
            "x": [float(v) for v in r["base_features"]] + [float(v) for v in extra],
        }
        z["struct"] = _struct_vector(z, base_index, len(base_names), st)
        out.append(z)
    out.sort(key=lambda r: r["full_index"])
    return out


def _jsonable_model(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "ridge": model["ridge"],
        "converged": model["converged"],
        "iterations": model["iterations"],
        "objective": model["objective"],
        "gradient_max_abs": model["gradient_max_abs"],
        "coefficients": {"intercept": float(model["beta"][0]), **{n: float(v) for n, v in zip(STRUCT_NAMES, model["beta"][1:])}},
    }


def main() -> int:
    hist, audit = _build_historical()
    # Retrieve canonical base names for the A100 structural mapping. This call is deterministic.
    _base_rows_unused, _base_audit_unused, base_names = v632._build_rows()
    folds = (({"2022/23"}, "2023/24"), ({"2022/23", "2023/24"}, "2024/25"))
    board = []
    for ridge in RIDGES:
        # Fit once per ridge/fold, then alpha only shrinks the residual; do not refit for alpha.
        fold_models = []
        for train_seasons, valid_season in folds:
            train = [r for r in hist if str(r["season"]) in train_seasons]
            valid = [r for r in hist if str(r["season"]) == valid_season]
            model = _fit_binary(train, ridge)
            fold_models.append((valid_season, valid, model))
        for alpha in ALPHAS:
            fresults = []
            proper = True
            for valid_season, valid, model in fold_models:
                cand = _predict(valid, model, alpha)
                market = _market_probs(valid)
                y = np.asarray([int(r["y"]) for r in valid], dtype=int)
                mm = _metrics(y, market); cm = _metrics(y, cand)
                rec = {
                    "valid_season": valid_season,
                    "market": mm, "candidate": cm,
                    "uplift_pp": 100.0 * (cm["top1"] - mm["top1"]),
                    "logloss_delta": cm["logloss"] - mm["logloss"],
                    "rps_delta": cm["rps"] - mm["rps"],
                }
                proper = proper and rec["logloss_delta"] <= PROPER_TOL + 1e-12 and rec["rps_delta"] <= PROPER_TOL + 1e-12
                fresults.append(rec)
            mean_up = float(np.mean([r["uplift_pp"] for r in fresults])); min_up = float(min(r["uplift_pp"] for r in fresults))
            board.append({"ridge": ridge, "alpha": alpha, "folds": fresults, "mean_uplift_pp": mean_up, "min_uplift_pp": min_up, "proper_guard": bool(proper)})
    board.sort(key=lambda z: (z["proper_guard"], z["min_uplift_pp"], z["mean_uplift_pp"], -z["ridge"], -z["alpha"]), reverse=True)
    chosen = board[0]
    hist_gate = bool(chosen["proper_guard"] and chosen["mean_uplift_pp"] >= HIST_REQUIRED_MEAN_UPLIFT_PP - 1e-12 and chosen["min_uplift_pp"] >= -1e-12)

    final_train = [r for r in hist if str(r["season"]) in HIST_SEASONS]
    final_model = _fit_binary(final_train, float(chosen["ridge"]))
    payload: dict[str, Any] = {
        "schema_version": "V6.58.0-structural-draw-mechanism-full500-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "governance": {
            "direct_draw_residual_model": True,
            "three_class_blackbox": False,
            "same_date_draw_history_frozen": True,
            "target_result_used_in_own_features": False,
            "A100_values_used_for_selection": False,
            "B_CONFIRM300_labels_read": False,
            "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": audit,
        "grid": {"ridges": list(RIDGES), "alphas": list(ALPHAS), "historical_required_mean_uplift_pp": HIST_REQUIRED_MEAN_UPLIFT_PP, "proper_tolerance": PROPER_TOL},
        "selected_historical": chosen,
        "historical_gate": hist_gate,
        "historical_leaderboard": board,
        "final_historical_fit": _jsonable_model(final_model),
        "external_tactical_source_screen": {
            "statsbomb_open": "NOT_SUITABLE_FOR_2022_23_TO_2024_25_BIG5_FULL_HISTORY; free Big5 release is 2015/16",
            "soccerdata": "SCRAPER_FRAMEWORK_NOT_FROZEN_HISTORICAL_SNAPSHOT; useful for later source engineering but not promoted here",
            "whoscored_scrape": "RESEARCH_SOURCE_RISK: browser/IP/locale stability issues documented",
        },
    }
    if not hist_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Only now open the fixed A100 labels.
    arows = _build_a100(base_names)
    labels = _load_a100_labels()
    for r in arows:
        r["y"] = labels[int(r["full_index"])]
    cand = _predict(arows, final_model, float(chosen["alpha"]))
    market = _market_probs(arows)
    y = np.asarray([int(r["y"]) for r in arows], dtype=int)
    mm = _metrics(y, market); cm = _metrics(y, cand)
    uplift = 100.0 * (cm["top1"] - mm["top1"])
    proper = bool(cm["logloss"] <= mm["logloss"] + 0.01 and cm["rps"] <= mm["rps"] + 0.01)
    gate = {
        "required_candidate_hits": 63,
        "required_uplift_vs_market_pp": 3.0,
        "candidate_hits": cm["hits"],
        "market_hits": mm["hits"],
        "uplift_vs_market_pp": uplift,
        "top1_gate": cm["hits"] >= 63,
        "uplift_gate": uplift >= 3.0 - 1e-12,
        "proper_score_guard": proper,
    }
    gate["A_FAST100_passed"] = bool(gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_GATE", "market": mm, "candidate": cm, "gate": gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
