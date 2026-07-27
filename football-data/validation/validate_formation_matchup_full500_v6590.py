#!/usr/bin/env python3
"""V6.59.0 strict-PIT historical formation-matchup 1X2 challenger.

Research question
-----------------
Does a team's *prior observed tactical-formation tendency* add stable market-excess
1X2 Top-1 signal? The target match's actual formation is NEVER used.

Data / PIT contract
-------------------
- Transfermarkt public games.csv supplies historical home/away formation strings.
- For every target, only formation observations from matches with date STRICTLY BEFORE
  the target date are eligible.
- Histories may include domestic league, domestic cup and UEFA matches; only completed
  prior match formation labels are used, never their scores/results.
- The target game's own home_club_formation/away_club_formation fields are ignored.
- Same-date matches cannot update one another because the cutoff is strict date-before.
- Team cross-provider identity uses the existing result-blind schedule fingerprint.

Formation representation
------------------------
Strings such as 4-2-3-1, 4-3-3 Attacking, 3-4-2-1 are reduced to outfield line
counts whose digits sum to 10. For each team from the last 10/20 valid prior formations
(within 365 days), we derive mean line counts, back-three/four/five shares, front-one/
front-two-plus shares, modal share, entropy, change rate, last observed shape, and
history depth. Pair features include both teams, differences, and attack-vs-defence
matchup contrasts.

Model
-----
Multinomial ridge residual around closing market probabilities:
  eta_H = log Pmkt(H) + alpha * X beta_H
  eta_D = log Pmkt(D) + alpha * X beta_D
  eta_A = log Pmkt(A)                     [reference residual]
No CatBoost/tree learner is used.

Historical folds:
- 2022/23 -> 2023/24
- 2022/23+2023/24 -> 2024/25
Historical gate before A100:
- mean Top-1 uplift >= +0.5pp;
- neither fold negative;
- LogLoss and RPS no worse than market by >0.005 in either fold.
Only after passing that gate may fixed Full500 A_FAST100 labels be opened.
B300/C100 remain unread unless the staged gate permits them.
Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import re
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6361 as g1  # noqa: E402
import dynamic_strength_oof_screen_v470 as dyn  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402
from platform_core import load_json  # noqa: E402

OUT = ROOT / "manifests" / "v6_formation_matchup_full500_v6590_status.json"
FEATURES = ROOT / "manifests" / "full500_v6493" / "full500_features_v6493.jsonl"
LABELS = ROOT / "manifests" / "full500_v6493" / "full500_development_labels_v6493.jsonl"
CACHE = Path("/tmp/football-v6590-cache")
HIST_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"
PART = "A_FAST100"
RIDGES = (0.1, 0.3, 1.0, 3.0, 10.0)
ALPHAS = (0.25, 0.50, 0.75, 1.0)
HIST_REQUIRED_MEAN_UPLIFT_PP = 0.5
PROPER_TOL = 0.005
MIN_PRIOR_FORMATIONS = 5
MAX_HISTORY_DAYS = 365
EPS = 1e-10

TM_COMP = {
    "ENG_PremierLeague": "GB1",
    "GER_Bundesliga": "L1",
    "ITA_SerieA": "IT1",
    "FRA_Ligue1": "FR1",
    "ESP_LaLiga": "ES1",
}
SEASON_START = {"2022/23": 2022, "2023/24": 2023, "2024/25": 2024, "2025/26": 2025}
FORMATION_RE = re.compile(r"(?<!\d)([1-5](?:-[1-5]){1,4})(?!\d)")

TEAM_FEATURE_NAMES = (
    "mean_def10", "mean_mid10", "mean_att10",
    "mean_def20", "mean_mid20", "mean_att20",
    "back3_share10", "back4_share10", "back5_share10",
    "front1_share10", "front2plus_share10",
    "modal_share10", "entropy10", "change_rate10",
    "last_def", "last_mid", "last_att", "history_depth20",
)
PAIR_EXTRA_NAMES = (
    "home_att_vs_away_def", "away_att_vs_home_def", "midfield_balance",
    "last_shape_same", "home_back3_minus_away", "home_back5_minus_away",
    "home_front2plus_minus_away", "home_change_minus_away",
)
PAIR_FEATURE_NAMES = tuple(
    [f"home_{x}" for x in TEAM_FEATURE_NAMES]
    + [f"away_{x}" for x in TEAM_FEATURE_NAMES]
    + [f"diff_{x}" for x in TEAM_FEATURE_NAMES]
    + list(PAIR_EXTRA_NAMES)
)


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return None


def _parse_formation(value: Any) -> tuple[int, int, int, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = FORMATION_RE.search(text)
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split("-")]
    if len(parts) < 2 or sum(parts) != 10:
        return None
    defenders = parts[0]
    attackers = parts[-1]
    midfield = sum(parts[1:-1]) if len(parts) > 2 else 0
    if not (1 <= defenders <= 5 and 1 <= attackers <= 5 and 0 <= midfield <= 8):
        return None
    label = "-".join(str(x) for x in parts)
    return defenders, midfield, attackers, label


def _load_games() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = load_json(dyn.EVIDENCE_CONFIG)
    path = dyn.download("games", config, CACHE)
    rows: list[dict[str, Any]] = []
    bad = missing_formation = parsed_side = 0
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {
            "game_id", "competition_id", "season", "date",
            "home_club_id", "away_club_id", "home_club_name", "away_club_name",
            "home_club_formation", "away_club_formation",
        }
        if not required.issubset(fields):
            raise RuntimeError(f"V6.59 unexpected games schema; missing={sorted(required-fields)} fields={sorted(fields)}")
        for raw in reader:
            d = _parse_date(raw.get("date")); gid = _int(raw.get("game_id"))
            hid = _int(raw.get("home_club_id")); aid = _int(raw.get("away_club_id")); season = _int(raw.get("season"))
            comp = str(raw.get("competition_id") or "").strip()
            if d is None or gid is None or hid is None or aid is None or season is None or not comp:
                bad += 1; continue
            hf = _parse_formation(raw.get("home_club_formation")); af = _parse_formation(raw.get("away_club_formation"))
            missing_formation += int(hf is None) + int(af is None)
            parsed_side += int(hf is not None) + int(af is not None)
            rows.append({
                "game_id": gid, "competition_id": comp, "season": season, "date": d,
                "home_id": hid, "away_id": aid,
                "home_name": str(raw.get("home_club_name") or "").strip(),
                "away_name": str(raw.get("away_club_name") or "").strip(),
                "home_formation": hf, "away_formation": af,
            })
    return rows, {
        "source_file_bytes": path.stat().st_size,
        "rows": len(rows), "bad_rows": bad,
        "formation_sides_parsed": parsed_side,
        "formation_sides_missing_or_unparseable": missing_formation,
        "formation_parse_rate": parsed_side / max(1, parsed_side + missing_formation),
        "min_date": min((r["date"] for r in rows), default=None).isoformat() if rows else None,
        "max_date": max((r["date"] for r in rows), default=None).isoformat() if rows else None,
        "target_match_formation_used": False,
        "result_columns_used_for_formation_features": False,
    }


def _formation_histories(games: list[dict[str, Any]]) -> dict[int, list[tuple[date, tuple[int, int, int, str]]]]:
    out: dict[int, list[tuple[date, tuple[int, int, int, str]]]] = defaultdict(list)
    for g in games:
        if g["home_formation"] is not None:
            out[int(g["home_id"])].append((g["date"], g["home_formation"]))
        if g["away_formation"] is not None:
            out[int(g["away_id"])].append((g["date"], g["away_formation"]))
    for club in out:
        out[club].sort(key=lambda z: z[0])
    return out


def _entropy(labels: list[str]) -> float:
    if not labels:
        return 0.0
    c = Counter(labels); n = len(labels)
    return -sum((v / n) * math.log(v / n) for v in c.values())


def _team_features(club: int, target: date, histories: dict[int, list[tuple[date, tuple[int, int, int, str]]]]) -> list[float] | None:
    hist = histories.get(int(club), [])
    dates = [x[0] for x in hist]
    idx = bisect_left(dates, target)
    prior = [(d, f) for d, f in hist[:idx] if 1 <= (target - d).days <= MAX_HISTORY_DAYS]
    if len(prior) < MIN_PRIOR_FORMATIONS:
        return None
    z20 = prior[-20:]; z10 = z20[-10:]

    def av(items: list[tuple[date, tuple[int, int, int, str]]], k: int) -> float:
        return float(np.mean([x[1][k] for x in items]))

    labels10 = [x[1][3] for x in z10]
    c10 = Counter(labels10)
    n10 = len(z10)
    changes = sum(1 for a, b in zip(labels10, labels10[1:]) if a != b) / max(1, n10 - 1)
    last = z10[-1][1]
    return [
        av(z10, 0), av(z10, 1), av(z10, 2),
        av(z20, 0), av(z20, 1), av(z20, 2),
        sum(1 for x in z10 if x[1][0] == 3) / n10,
        sum(1 for x in z10 if x[1][0] == 4) / n10,
        sum(1 for x in z10 if x[1][0] == 5) / n10,
        sum(1 for x in z10 if x[1][2] == 1) / n10,
        sum(1 for x in z10 if x[1][2] >= 2) / n10,
        max(c10.values()) / n10,
        _entropy(labels10),
        float(changes),
        float(last[0]), float(last[1]), float(last[2]),
        len(z20) / 20.0,
    ]


def _pair_features(home: list[float], away: list[float]) -> list[float]:
    diff = [h - a for h, a in zip(home, away)]
    last_same = 1.0 if (home[14], home[15], home[16]) == (away[14], away[15], away[16]) else 0.0
    extra = [
        home[2] - away[0],
        away[2] - home[0],
        home[1] - away[1],
        last_same,
        home[6] - away[6],
        home[8] - away[8],
        home[10] - away[10],
        home[13] - away[13],
    ]
    out = [*home, *away, *diff, *extra]
    if len(out) != len(PAIR_FEATURE_NAMES):
        raise RuntimeError(f"V6.59 formation feature length {len(out)} != {len(PAIR_FEATURE_NAMES)}")
    return out


def _tm_domestic_rows(games: list[dict[str, Any]], cid: str, season: str) -> list[dict[str, Any]]:
    comp = TM_COMP[cid]; sy = SEASON_START[season]
    return [
        {
            "competition_id": cid, "date": g["date"].isoformat(),
            "home_name": g["home_name"], "away_name": g["away_name"],
            "home_id": g["home_id"], "away_id": g["away_id"],
        }
        for g in games if g["competition_id"] == comp and int(g["season"]) == sy
    ]


def _attach_formations(base_rows: list[dict[str, Any]], games: list[dict[str, Any]], histories: dict[int, list[tuple[date, tuple[int, int, int, str]]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    joined: list[dict[str, Any]] = []
    misses = Counter(); crosswalk_audit: dict[str, Any] = {}
    for season in sorted({str(r["season"]) for r in base_rows}):
        bseason = [r for r in base_rows if str(r["season"]) == season]
        tseason: list[dict[str, Any]] = []
        for cid in TM_COMP:
            tseason.extend(_tm_domestic_rows(games, cid, season))
        mapping, ca = g1.build_crosswalk(bseason, tseason)
        crosswalk_audit[season] = {
            cid: {
                "tm_teams": meta["tm_teams"], "fd_teams": meta["fd_teams"], "mapped_teams": meta["mapped_teams"],
                "unmapped_tm": meta["unmapped_tm"], "unmapped_fd": meta["unmapped_fd"],
                "min_role_overlap": meta["min_role_overlap"], "min_schedule_margin": meta["min_schedule_margin"],
            }
            for cid, meta in ca.items()
        }
        tmap: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for t in tseason:
            cid = str(t["competition_id"])
            mh = mapping.get(cid, {}).get(str(t["home_name"])); ma = mapping.get(cid, {}).get(str(t["away_name"]))
            if not mh or not ma:
                misses[f"{season}:unmapped_tm_game"] += 1; continue
            key = (cid, str(t["date"]), v632._token(cid, mh), v632._token(cid, ma))
            tmap[key] = t
        for r in bseason:
            cid = str(r["competition_id"])
            key = (cid, str(r["date"]), v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])))
            t = tmap.get(key)
            if t is None:
                misses[f"{season}:target_tm_join"] += 1; continue
            target_date = date.fromisoformat(str(r["date"]))
            hf = _team_features(int(t["home_id"]), target_date, histories)
            af = _team_features(int(t["away_id"]), target_date, histories)
            if hf is None or af is None:
                misses[f"{season}:prior_formation_context"] += 1; continue
            z = dict(r)
            z["formation_features"] = _pair_features(hf, af)
            joined.append(z)
    return joined, {"misses": dict(misses), "crosswalk": crosswalk_audit, "by_season": dict(Counter(str(r["season"]) for r in joined))}


def _metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = len(y); picks = probs.argmax(axis=1); hits = int(np.sum(picks == y))
    one = np.eye(3)[y]
    brier = float(np.mean(np.sum((probs - one) ** 2, axis=1)))
    logloss = float(-np.mean(np.log(np.clip(probs[np.arange(n), y], EPS, 1.0))))
    c1 = probs[:, 0] - (y == 0); c2 = probs[:, 0] + probs[:, 1] - (y <= 1)
    rps = float(np.mean((c1 * c1 + c2 * c2) / 2.0))
    return {
        "count": int(n), "hits": hits, "top1": hits / n,
        "brier": brier, "logloss": logloss, "rps": rps,
        "predicted_counts": dict(Counter(str(int(z)) for z in picks)),
        "actual_counts": dict(Counter(str(int(z)) for z in y)),
    }


def _fit(train: list[dict[str, Any]], ridge: float) -> dict[str, Any]:
    X = np.asarray([r["formation_features"] for r in train], dtype=float)
    y = np.asarray([int(r["y"]) for r in train], dtype=int)
    market = np.asarray([r["market"] for r in train], dtype=float)
    mean = X.mean(axis=0); std = X.std(axis=0); std = np.where(std < 1e-8, 1.0, std)
    Z = (X - mean) / std
    A = np.column_stack([np.ones(len(Z)), Z])
    p = A.shape[1]

    def calc(theta: np.ndarray) -> tuple[float, np.ndarray]:
        B = theta.reshape(2, p)
        residual = A @ B.T
        eta = np.log(np.clip(market, EPS, 1.0))
        eta[:, 0] += residual[:, 0]; eta[:, 1] += residual[:, 1]
        eta -= eta.max(axis=1, keepdims=True)
        probs = np.exp(eta); probs /= probs.sum(axis=1, keepdims=True)
        nll = -float(np.sum(np.log(np.clip(probs[np.arange(len(y)), y], EPS, 1.0))))
        penalty = 0.5 * float(ridge) * float(np.sum(B[:, 1:] ** 2))
        target = np.zeros((len(y), 2), dtype=float)
        target[:, 0] = (y == 0); target[:, 1] = (y == 1)
        gradB = (probs[:, :2] - target).T @ A
        gradB[:, 1:] += float(ridge) * B[:, 1:]
        return nll + penalty, gradB.reshape(-1)

    theta0 = np.zeros(2 * p, dtype=float)
    res = minimize(lambda t: calc(t)[0], theta0, jac=lambda t: calc(t)[1], method="L-BFGS-B", options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        raise RuntimeError(f"V6.59 multinomial fit failed ridge={ridge}: {res.message}")
    return {
        "ridge": float(ridge), "mean": mean, "std": std,
        "B": np.asarray(res.x, dtype=float).reshape(2, p),
        "converged": True, "iterations": int(res.nit), "objective": float(res.fun),
        "gradient_max_abs": float(np.max(np.abs(calc(np.asarray(res.x, dtype=float))[1]))),
    }


def _predict(rows: list[dict[str, Any]], model: dict[str, Any], alpha: float) -> np.ndarray:
    X = np.asarray([r["formation_features"] for r in rows], dtype=float)
    Z = (X - model["mean"]) / model["std"]
    A = np.column_stack([np.ones(len(Z)), Z])
    residual = A @ model["B"].T
    market = np.asarray([r["market"] for r in rows], dtype=float)
    eta = np.log(np.clip(market, EPS, 1.0))
    eta[:, 0] += float(alpha) * residual[:, 0]
    eta[:, 1] += float(alpha) * residual[:, 1]
    eta -= eta.max(axis=1, keepdims=True)
    p = np.exp(eta); p /= p.sum(axis=1, keepdims=True)
    return p


def _json_model(model: dict[str, Any]) -> dict[str, Any]:
    p = len(PAIR_FEATURE_NAMES) + 1
    bh = model["B"][0]; bd = model["B"][1]
    return {
        "ridge": model["ridge"], "converged": model["converged"], "iterations": model["iterations"],
        "objective": model["objective"], "gradient_max_abs": model["gradient_max_abs"],
        "home_residual_coefficients": {"intercept": float(bh[0]), **{n: float(v) for n, v in zip(PAIR_FEATURE_NAMES, bh[1:p])}},
        "draw_residual_coefficients": {"intercept": float(bd[0]), **{n: float(v) for n, v in zip(PAIR_FEATURE_NAMES, bd[1:p])}},
    }


def _load_a100_labels() -> dict[int, int]:
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        r = json.loads(line)
        if r.get("partition") == PART:
            labels[int(r["full_index"])] = int(r["label"])
    if len(labels) != 100:
        raise RuntimeError(f"V6.59 A100 labels expected100 got{len(labels)}")
    return labels


def main() -> int:
    rich, rich_audit = v651.build_historical()
    games, games_audit = _load_games()
    histories = _formation_histories(games)
    hist, form_audit = _attach_formations(rich, games, histories)
    by_season = Counter(str(r["season"]) for r in hist)
    if any(by_season.get(s, 0) < 900 for s in HIST_SEASONS):
        raise RuntimeError(f"V6.59 historical formation coverage too small: {dict(by_season)}")

    folds = (({"2022/23"}, "2023/24"), ({"2022/23", "2023/24"}, "2024/25"))
    board = []
    for ridge in RIDGES:
        cached = []
        for train_seasons, valid_season in folds:
            train = [r for r in hist if str(r["season"]) in train_seasons]
            valid = [r for r in hist if str(r["season"]) == valid_season]
            model = _fit(train, ridge)
            cached.append((valid_season, valid, model))
        for alpha in ALPHAS:
            frecs = []; proper = True
            for valid_season, valid, model in cached:
                y = np.asarray([int(r["y"]) for r in valid], dtype=int)
                market = np.asarray([r["market"] for r in valid], dtype=float)
                cand = _predict(valid, model, alpha)
                mm = _metrics(y, market); cm = _metrics(y, cand)
                rec = {
                    "valid_season": valid_season, "market": mm, "candidate": cm,
                    "uplift_pp": 100.0 * (cm["top1"] - mm["top1"]),
                    "logloss_delta": cm["logloss"] - mm["logloss"],
                    "rps_delta": cm["rps"] - mm["rps"],
                }
                proper = proper and rec["logloss_delta"] <= PROPER_TOL + 1e-12 and rec["rps_delta"] <= PROPER_TOL + 1e-12
                frecs.append(rec)
            ups = [x["uplift_pp"] for x in frecs]
            board.append({
                "ridge": ridge, "alpha": alpha, "folds": frecs,
                "mean_uplift_pp": float(np.mean(ups)), "min_uplift_pp": float(min(ups)),
                "proper_guard": bool(proper),
            })
    board.sort(key=lambda z: (z["proper_guard"], z["min_uplift_pp"], z["mean_uplift_pp"], -z["ridge"], -z["alpha"]), reverse=True)
    chosen = board[0]
    hist_gate = bool(chosen["proper_guard"] and chosen["mean_uplift_pp"] >= HIST_REQUIRED_MEAN_UPLIFT_PP - 1e-12 and chosen["min_uplift_pp"] >= -1e-12)
    final_model = _fit(hist, float(chosen["ridge"]))

    payload: dict[str, Any] = {
        "schema_version": "V6.59.0-formation-matchup-full500-r1",
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "governance": {
            "target_match_actual_formation_used": False,
            "formation_history_strictly_before_target": True,
            "same_date_target_updates_each_other": False,
            "match_results_used_for_formation_features": False,
            "A100_values_used_for_selection": False,
            "B_CONFIRM300_labels_read": False, "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "source_audit": games_audit,
        "historical_audit": {"rich_market": rich_audit, "formation": form_audit, "joined_by_season": dict(by_season)},
        "feature_contract": {
            "team_feature_names": list(TEAM_FEATURE_NAMES), "team_feature_count": len(TEAM_FEATURE_NAMES),
            "pair_feature_names": list(PAIR_FEATURE_NAMES), "pair_feature_count": len(PAIR_FEATURE_NAMES),
            "minimum_prior_formations": MIN_PRIOR_FORMATIONS, "max_history_days": MAX_HISTORY_DAYS,
        },
        "grid": {"ridges": list(RIDGES), "alphas": list(ALPHAS), "historical_required_mean_uplift_pp": HIST_REQUIRED_MEAN_UPLIFT_PP, "proper_tolerance": PROPER_TOL},
        "selected_historical": chosen, "historical_gate": hist_gate, "historical_leaderboard": board,
        "final_historical_fit": _json_model(final_model),
    }
    if not hist_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Only after historical gate: build fixed A100 formation contexts and open labels.
    frozen = [json.loads(x) for x in FEATURES.read_text(encoding="utf-8").splitlines() if x.strip()]
    frozen = [r for r in frozen if r.get("partition") == PART]
    frozen.sort(key=lambda r: int(r["full_index"]))
    if len(frozen) != 100:
        raise RuntimeError(f"V6.59 A100 features expected100 got{len(frozen)}")
    arich, _ = v651.load_a100_features(); by_idx = {int(r["full_index"]): r for r in arich}
    abase = []
    for f in frozen:
        rr = dict(by_idx[int(f["full_index"])])
        rr.update({"full_index": int(f["full_index"]), "season": str(f["season"]), "date": str(f["date"]), "home_team": str(f["home_team"]), "away_team": str(f["away_team"])})
        abase.append(rr)
    arows, aaudit = _attach_formations(abase, games, histories)
    if len(arows) != 100:
        raise RuntimeError(f"V6.59 A100 formation coverage incomplete {len(arows)} audit={aaudit['misses']}")
    arows.sort(key=lambda r: int(r["full_index"]))
    labels = _load_a100_labels()
    for r in arows: r["y"] = labels[int(r["full_index"])]
    y = np.asarray([int(r["y"]) for r in arows], dtype=int)
    market = np.asarray([r["market"] for r in arows], dtype=float)
    cand = _predict(arows, final_model, float(chosen["alpha"]))
    mm = _metrics(y, market); cm = _metrics(y, cand)
    uplift = 100.0 * (cm["top1"] - mm["top1"])
    proper = bool(cm["logloss"] <= mm["logloss"] + 0.01 and cm["rps"] <= mm["rps"] + 0.01)
    gate = {
        "required_candidate_hits": 63, "required_uplift_vs_market_pp": 3.0,
        "candidate_hits": cm["hits"], "market_hits": mm["hits"], "uplift_vs_market_pp": uplift,
        "top1_gate": cm["hits"] >= 63, "uplift_gate": uplift >= 3.0 - 1e-12, "proper_score_guard": proper,
    }
    gate["A_FAST100_passed"] = bool(gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_GATE", "formation_audit": aaudit, "market": mm, "candidate": cm, "gate": gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
