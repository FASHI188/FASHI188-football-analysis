#!/usr/bin/env python3
"""R50 development-only PIT draw architecture race.

This executable is intentionally limited to already-consumed development seasons
2021-22..2024-25. It never opens 2025-26 result/score files. The untouched
2025-26 confirmation window remains closed unless a later preregistration
explicitly authorizes one frozen winner.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

DEV_SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25")
FOLDS = (
    (("2021-22",), "2022-23"),
    (("2021-22", "2022-23"), "2023-24"),
    (("2021-22", "2022-23", "2023-24"), "2024-25"),
)
FORBIDDEN_CONFIRMATION_SEASON = "2025-26"
MIN_LEAD_HOURS = 6.0
MAX_STALENESS_HOURS = 240.0
L2 = 1.0
MAX_ITER = 50
TOL = 1e-8
SHOCK_Q = 0.75
EPS = 1e-12
CANDIDATES = (
    "A_R41R2_ADDITIVE_DRAW_RESIDUAL",
    "B_SHOCK_REGIME_TWO_DRAW_EXPERTS",
    "C_OUTCOME_SPECIFIC_THREE_EXPERTS",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return p.stdout


def git_show(repo: Path, sha: str, path: str) -> Optional[str]:
    p = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{path}"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return p.stdout if p.returncode == 0 else None


def parse_dt(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def truthy(x: object) -> bool:
    return str(x).strip().lower() in {"1", "true", "yes"}


def fnum(x: object) -> Optional[float]:
    try:
        v = float(str(x).strip())
    except Exception:
        return None
    return v if math.isfinite(v) else None


def csv_rows(text: str) -> List[Dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def norm_team(s: str) -> str:
    x = str(s).strip().lower()
    for a, b in {"&": "and", "'": "", "’": "", ".": "", "-": " ", "_": " "}.items():
        x = x.replace(a, b)
    x = " ".join(x.split())
    aliases = {
        "man utd": "man united", "manchester united": "man united",
        "manchester city": "man city", "spurs": "tottenham",
        "tottenham hotspur": "tottenham", "newcastle united": "newcastle",
        "nottingham forest": "nottm forest", "sheffield utd": "sheffield united",
        "sheff utd": "sheffield united", "west bromwich albion": "west brom",
        "wolverhampton": "wolves", "wolverhampton wanderers": "wolves",
        "leicester city": "leicester", "norwich city": "norwich",
        "leeds united": "leeds", "brighton and hove albion": "brighton",
    }
    return aliases.get(x, x)


@dataclass
class TeamState:
    regular_count: int = 0
    regular_risk_count: int = 0
    attack_bps_available: float = 0.0
    attack_bps_at_risk: float = 0.0

    def vector(self) -> Tuple[float, float, float]:
        rr = self.regular_risk_count / max(self.regular_count, 1)
        denom = self.attack_bps_available + self.attack_bps_at_risk
        ar = self.attack_bps_at_risk / max(denom, 1e-9)
        la = math.log1p(max(self.attack_bps_available, 0.0))
        return rr, ar, la


def build_team_states(rows: List[Dict[str, str]]) -> Dict[str, TeamState]:
    out: Dict[str, TeamState] = {}
    for r in rows:
        team = str(r.get("team", "")).strip()
        if not team:
            continue
        s = out.setdefault(team, TeamState())
        status = str(r.get("status", "") or "").strip().lower()
        chance = fnum(r.get("chance_of_playing_this_round"))
        at_risk = (bool(status) and status != "a") or (chance is not None and chance < 100.0)
        starts = fnum(r.get("starts")) or 0.0
        minutes = fnum(r.get("minutes")) or 0.0
        regular = starts >= 3.0 or minutes >= 270.0
        pos = int(fnum(r.get("element_type")) or 0)
        attack = pos in {3, 4}
        bps = fnum(r.get("bps")) or 0.0
        if regular:
            s.regular_count += 1
            if at_risk:
                s.regular_risk_count += 1
            if attack:
                if at_risk:
                    s.attack_bps_at_risk += bps
                else:
                    s.attack_bps_available += bps
    return out


def load_team_names(repo: Path, sha: str, season: str) -> Dict[str, str]:
    text = git_show(repo, sha, f"data/{season}/teams.csv")
    if text is None:
        return {}
    out = {}
    for r in csv_rows(text):
        tid = str(r.get("id", "")).strip()
        name = str(r.get("name", "")).strip()
        if tid and name:
            out[tid] = name
    return out


def season_commits(repo: Path, season: str) -> List[Tuple[str, datetime]]:
    path = f"data/{season}/players_raw.csv"
    out = run_git(repo, "log", "--format=%H\t%cI", "--", path)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, ts = line.split("\t", 1)
        rows.append((sha, parse_dt(ts)))
    return sorted(rows, key=lambda z: z[1])


def snapshot_components(h: TeamState, a: TeamState) -> Tuple[np.ndarray, np.ndarray]:
    hrr, har, hla = h.vector()
    arr, aar, ala = a.vector()
    S = np.array([
        0.5 * (hrr + arr), abs(hrr - arr), 0.5 * (har + aar), abs(har - aar),
        hla + ala, abs(hla - ala),
    ], dtype=float)
    signed = np.array([hrr - arr, har - aar, hla - ala], dtype=float)
    return S, signed


def build_transitions(repo: Path, season: str) -> List[Dict[str, object]]:
    assert season != FORBIDDEN_CONFIRMATION_SEASON
    history: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for sha, commit_dt in season_commits(repo, season):
        pt = git_show(repo, sha, f"data/{season}/players_raw.csv")
        ft = git_show(repo, sha, f"data/{season}/fixtures.csv")
        if pt is None or ft is None:
            continue
        states = build_team_states(csv_rows(pt))
        fixtures = csv_rows(ft)
        names = load_team_names(repo, sha, season)
        for fx in fixtures:
            kickoff_raw = str(fx.get("kickoff_time", "") or "").strip()
            if not kickoff_raw:
                continue
            kickoff = parse_dt(kickoff_raw)
            lead = (kickoff - commit_dt).total_seconds() / 3600.0
            if lead < MIN_LEAD_HOURS or lead > MAX_STALENESS_HOURS:
                continue
            if truthy(fx.get("finished", "")) or truthy(fx.get("started", "")):
                continue
            if str(fx.get("team_h_score", "") or "").strip() or str(fx.get("team_a_score", "") or "").strip():
                continue
            fid = str(fx.get("id", "")).strip()
            ht = str(fx.get("team_h", "")).strip()
            at = str(fx.get("team_a", "")).strip()
            if not fid or ht not in states or at not in states or ht not in names or at not in names:
                continue
            S, U0 = snapshot_components(states[ht], states[at])
            history[fid].append({
                "snapshot_time": commit_dt, "kickoff": kickoff, "home_id": ht, "away_id": at,
                "home_name": names[ht], "away_name": names[at], "S": S, "U0": U0,
                "sha": sha, "lead_hours": lead,
            })
    out = []
    for fid, snaps in history.items():
        snaps = sorted(snaps, key=lambda z: z["snapshot_time"])
        uniq = []
        last_ts = None
        for s in snaps:
            if s["snapshot_time"] != last_ts:
                uniq.append(s)
                last_ts = s["snapshot_time"]
        if len(uniq) < 2:
            continue
        prev, cur = uniq[-2], uniq[-1]
        out.append({
            "season": season, "fixture_id": fid, "kickoff": cur["kickoff"],
            "date": cur["kickoff"].date().isoformat(), "home": cur["home_name"], "away": cur["away_name"],
            "X": cur["S"] - prev["S"], "U": cur["U0"] - prev["U0"],
            "previous_snapshot": prev["snapshot_time"].isoformat(),
            "current_snapshot": cur["snapshot_time"].isoformat(),
            "previous_sha": prev["sha"], "current_sha": cur["sha"],
            "current_lead_hours": cur["lead_hours"],
        })
    return sorted(out, key=lambda r: (r["kickoff"], r["fixture_id"]))


def parse_market_file(path: Path, season: str) -> List[Dict[str, object]]:
    assert season != FORBIDDEN_CONFIRMATION_SEASON
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            h = str(r.get("HomeTeam", "")).strip()
            a = str(r.get("AwayTeam", "")).strip()
            d = str(r.get("Date", "")).strip()
            y = str(r.get("FTR", "")).strip().upper()
            if not h or not a or not d or y not in {"H", "D", "A"}:
                continue
            dt = None
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    dt = datetime.strptime(d, fmt).date()
                    break
                except ValueError:
                    pass
            if dt is None:
                continue
            trip = None
            source = None
            for cols, label in (
                (("AvgCH", "AvgCD", "AvgCA"), "AvgC"),
                (("B365CH", "B365CD", "B365CA"), "B365C"),
                (("MaxCH", "MaxCD", "MaxCA"), "MaxC"),
                (("PSCH", "PSCD", "PSCA"), "PSC"),
            ):
                vals = [fnum(r.get(c)) for c in cols]
                if all(v is not None and v > 1.0 for v in vals):
                    inv = np.array([1.0 / float(v) for v in vals])
                    trip = inv / inv.sum()
                    source = label
                    break
            if trip is None:
                continue
            rows.append({
                "season": season, "date": dt.isoformat(), "home": h, "away": a,
                "actual": y, "p": trip, "market_source": source,
            })
    return rows


def join_rows(transitions: Sequence[Dict[str, object]], markets: Sequence[Dict[str, object]]) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    idx: Dict[Tuple[str, str, str], List[Dict[str, object]]] = defaultdict(list)
    for m in markets:
        idx[(m["date"], norm_team(m["home"]), norm_team(m["away"]))].append(m)
    out = []
    stats = {"transition_rows": len(transitions), "matched": 0, "ambiguous": 0, "unmatched": 0}
    for t in transitions:
        key = (t["date"], norm_team(t["home"]), norm_team(t["away"]))
        hits = idx.get(key, [])
        if len(hits) != 1:
            stats["ambiguous" if len(hits) > 1 else "unmatched"] += 1
            continue
        m = hits[0]
        row = dict(t)
        row.update({"actual": m["actual"], "p": m["p"], "market_source": m["market_source"]})
        out.append(row)
        stats["matched"] += 1
    return out, stats


def standardize(train: np.ndarray, test: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = train.mean(axis=0)
    sd = train.std(axis=0)
    sd = np.where(sd > 1e-12, sd, 1.0)
    return (train - mean) / sd, (test - mean) / sd, mean, sd


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-8, 1 - 1e-8)
    return np.log(p / (1 - p))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def fit_offset_logistic(Z: np.ndarray, y: np.ndarray, offset: np.ndarray) -> Tuple[np.ndarray, Dict[str, object]]:
    n, k = Z.shape
    X = np.column_stack([np.ones(n), Z])
    b = np.zeros(k + 1)
    pen = np.diag(np.r_[0.0, np.repeat(L2, k)])
    converged = False
    for it in range(1, MAX_ITER + 1):
        p = sigmoid(offset + X @ b)
        w = np.maximum(p * (1 - p), 1e-7)
        grad = X.T @ (y - p) - pen @ b
        H = X.T @ (X * w[:, None]) + pen
        try:
            delta = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            delta = np.linalg.lstsq(H, grad, rcond=None)[0]
        b = b + delta
        if np.max(np.abs(delta)) < TOL:
            converged = True
            break
    return b, {"converged": converged, "iterations": it, "coef": b.tolist()}


def pred_offset(Z: np.ndarray, base_p: np.ndarray, b: np.ndarray) -> np.ndarray:
    X = np.column_stack([np.ones(len(Z)), Z])
    return sigmoid(logit(base_p) + X @ b)


def draw_mass_to_hda(qd: np.ndarray, base: np.ndarray) -> np.ndarray:
    ha = base[:, [0, 2]]
    denom = np.maximum(ha.sum(axis=1), EPS)
    qh = (1 - qd) * ha[:, 0] / denom
    qa = (1 - qd) * ha[:, 1] / denom
    return np.column_stack([qh, qd, qa])


def fit_predict_A(train, test):
    Xtr = np.vstack([r["X"] for r in train]); Xte = np.vstack([r["X"] for r in test])
    Ztr, Zte, mean, sd = standardize(Xtr, Xte)
    y = np.array([r["actual"] == "D" for r in train], dtype=float)
    base_tr = np.array([r["p"][1] for r in train]); base_te = np.vstack([r["p"] for r in test])
    b, audit = fit_offset_logistic(Ztr, y, logit(base_tr))
    qd = pred_offset(Zte, base_te[:, 1], b)
    return draw_mass_to_hda(qd, base_te), {"fit": audit, "mean": mean.tolist(), "sd": sd.tolist()}


def fit_predict_B(train, test):
    Xtr = np.vstack([r["X"] for r in train]); Xte = np.vstack([r["X"] for r in test])
    Ztr, Zte, mean, sd = standardize(Xtr, Xte)
    shock_tr = np.sqrt((Ztr ** 2).sum(axis=1)); shock_te = np.sqrt((Zte ** 2).sum(axis=1))
    threshold = float(np.quantile(shock_tr, SHOCK_Q))
    y = np.array([r["actual"] == "D" for r in train], dtype=float)
    base_tr = np.array([r["p"][1] for r in train]); base_te = np.vstack([r["p"] for r in test])
    pred = np.zeros(len(test)); audits = {}
    for label, mask_tr, mask_te in (
        ("calm", shock_tr < threshold, shock_te < threshold),
        ("shock", shock_tr >= threshold, shock_te >= threshold),
    ):
        n = int(mask_tr.sum())
        if n < 50 or len(np.unique(y[mask_tr])) < 2:
            raise RuntimeError(f"R50 regime {label} insufficient train rows/classes: {n}")
        b, audit = fit_offset_logistic(Ztr[mask_tr], y[mask_tr], logit(base_tr[mask_tr]))
        pred[mask_te] = pred_offset(Zte[mask_te], base_te[mask_te, 1], b)
        audits[label] = {"n_train": n, **audit}
    return draw_mass_to_hda(pred, base_te), {
        "threshold": threshold, "shock_quantile": SHOCK_Q, "experts": audits,
        "mean": mean.tolist(), "sd": sd.tolist(),
        "train_shock_count": int((shock_tr >= threshold).sum()),
        "test_shock_count": int((shock_te >= threshold).sum()),
    }


def fit_predict_C(train, test):
    Xtr = np.vstack([r["X"] for r in train]); Xte = np.vstack([r["X"] for r in test])
    Utr = np.vstack([r["U"] for r in train]); Ute = np.vstack([r["U"] for r in test])
    ZXtr, ZXte, mx, sx = standardize(Xtr, Xte)
    ZUtr, ZUte, mu, su = standardize(Utr, Ute)
    base_tr = np.vstack([r["p"] for r in train]); base_te = np.vstack([r["p"] for r in test])
    actual = [r["actual"] for r in train]
    raw = np.zeros((len(test), 3)); audits = {}
    for label, ztr, zte, j in (("H", ZUtr, ZUte, 0), ("D", ZXtr, ZXte, 1), ("A", -ZUtr, -ZUte, 2)):
        y = np.array([a == label for a in actual], dtype=float)
        b, audit = fit_offset_logistic(ztr, y, logit(base_tr[:, j]))
        raw[:, j] = pred_offset(zte, base_te[:, j], b)
        audits[label] = audit
    probs = raw / np.maximum(raw.sum(axis=1, keepdims=True), EPS)
    return probs, {
        "experts": audits, "X_mean": mx.tolist(), "X_sd": sx.tolist(),
        "U_mean": mu.tolist(), "U_sd": su.tolist(),
    }


def labels_idx(rows: Sequence[Dict[str, object]]) -> np.ndarray:
    mp = {"H": 0, "D": 1, "A": 2}
    return np.array([mp[r["actual"]] for r in rows], dtype=int)


def auc_binary(y: np.ndarray, score: np.ndarray) -> float:
    y = y.astype(int); n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(score, kind="mergesort"); ranks = np.empty(len(score), dtype=float); sorted_s = score[order]
    i = 0
    while i < len(score):
        j = i + 1
        while j < len(score) and sorted_s[j] == sorted_s[i]:
            j += 1
        ranks[order[i:j]] = 0.5 * ((i + 1) + j)
        i = j
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def metrics(rows: Sequence[Dict[str, object]], probs: np.ndarray) -> Dict[str, float]:
    yidx = labels_idx(rows); yd = (yidx == 1).astype(float); p = np.clip(probs, EPS, 1 - EPS); n = len(rows)
    hda_ll = -float(np.mean(np.log(p[np.arange(n), yidx])))
    pd = p[:, 1]
    draw_ll = -float(np.mean(yd * np.log(pd) + (1 - yd) * np.log(1 - pd)))
    brier = float(np.mean((pd - yd) ** 2)); auc = auc_binary(yd, pd)
    pred = np.argmax(p, axis=1); acc = float(np.mean(pred == yidx)); draw_mask = pred == 1
    draw_count = int(draw_mask.sum()); draw_hits = int(np.sum(draw_mask & (yidx == 1))); draw_actual = int(np.sum(yidx == 1))
    return {
        "n": n, "draw_actual": draw_actual, "draw_prevalence": draw_actual / n,
        "hda_logloss": hda_ll, "draw_logloss": draw_ll, "draw_brier": brier,
        "draw_auc": auc, "hda_accuracy": acc, "natural_top1_draw_count": draw_count,
        "natural_top1_draw_hits": draw_hits,
        "natural_top1_draw_precision": draw_hits / draw_count if draw_count else 0.0,
        "natural_top1_draw_recall": draw_hits / draw_actual if draw_actual else 0.0,
        "probability_sum_max_abs_error": float(np.max(np.abs(probs.sum(axis=1) - 1.0))),
    }


def metric_delta(candidate: Dict[str, float], base: Dict[str, float]) -> Dict[str, float]:
    return {k: candidate[k] - base[k] for k in ("hda_logloss", "draw_logloss", "draw_brier", "draw_auc", "hda_accuracy")}


def bootstrap_hda_delta(rows, cand_probs, base_probs, reps=5000, seed=20260814):
    dates = sorted(set(r["date"] for r in rows))
    by_date = {d: np.array([i for i, r in enumerate(rows) if r["date"] == d], dtype=int) for d in dates}
    y = labels_idx(rows); cp = np.clip(cand_probs, EPS, 1 - EPS); bp = np.clip(base_probs, EPS, 1 - EPS)
    delta = -np.log(cp[np.arange(len(rows)), y]) + np.log(bp[np.arange(len(rows)), y])
    rng = np.random.default_rng(seed); vals = np.empty(reps)
    for b in range(reps):
        sampled_dates = rng.choice(dates, size=len(dates), replace=True)
        idx = np.concatenate([by_date[d] for d in sampled_dates])
        vals[b] = float(delta[idx].mean())
    return {
        "replicates": reps, "seed": seed, "unit": "calendar_date",
        "p05": float(np.quantile(vals, 0.05)), "median": float(np.quantile(vals, 0.50)),
        "p95": float(np.quantile(vals, 0.95)), "probability_delta_lt_0": float(np.mean(vals < 0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fpl-repo", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prereg", default="football-data/research/r50_pit_draw_architecture_dev_prereg_20260814.json")
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve(); fpl_repo = Path(args.fpl_repo).resolve(); out = Path(args.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    prereg = (repo_root / args.prereg).resolve(); prereg_obj = json.loads(prereg.read_text(encoding="utf-8"))
    assert prereg_obj["confirmation_protection"]["labels_open"] is False
    assert prereg_obj["authorization"]["confirmation_2025_26_label_access"] is False
    assert FORBIDDEN_CONFIRMATION_SEASON not in DEV_SEASONS
    assert all(FORBIDDEN_CONFIRMATION_SEASON not in train and test != FORBIDDEN_CONFIRMATION_SEASON for train, test in FOLDS)

    season_rows: Dict[str, List[Dict[str, object]]] = {}; input_audit = {}
    for season in DEV_SEASONS:
        transitions = build_transitions(fpl_repo, season)
        market_path = repo_root / "football-data" / "processed" / "ENG_PremierLeague" / f"{season}.csv"
        if not market_path.exists():
            raise FileNotFoundError(market_path)
        markets = parse_market_file(market_path, season)
        joined, jstats = join_rows(transitions, markets); season_rows[season] = joined
        input_audit[season] = {**jstats, "market_rows": len(markets), "joined_rows": len(joined), "market_file": str(market_path.relative_to(repo_root)), "market_sha256": sha256_file(market_path)}
    for season in DEV_SEASONS:
        if len(season_rows[season]) < 100:
            raise RuntimeError(f"R50 insufficient joined transition rows for {season}: {len(season_rows[season])}")

    all_oof = {c: [] for c in CANDIDATES}; all_base = []; fold_results = []; fit_audits = []
    funcs = {CANDIDATES[0]: fit_predict_A, CANDIDATES[1]: fit_predict_B, CANDIDATES[2]: fit_predict_C}
    for fold_no, (train_seasons, test_season) in enumerate(FOLDS, 1):
        train = [r for s in train_seasons for r in season_rows[s]]; test = list(season_rows[test_season])
        base_probs = np.vstack([r["p"] for r in test]); base_m = metrics(test, base_probs); all_base.extend(zip(test, base_probs))
        fold_item = {"fold": fold_no, "train_seasons": list(train_seasons), "test_season": test_season, "train_n": len(train), "test_n": len(test), "baseline": base_m, "candidates": {}}
        for c in CANDIDATES:
            probs, audit = funcs[c](train, test); cm = metrics(test, probs)
            fold_item["candidates"][c] = {"metrics": cm, "delta": metric_delta(cm, base_m)}
            fit_audits.append({"fold": fold_no, "candidate": c, "audit": audit}); all_oof[c].extend(zip(test, probs))
        fold_results.append(fold_item)

    pooled_rows = [r for r, _ in all_base]; pooled_base_probs = np.vstack([p for _, p in all_base]); base_pooled = metrics(pooled_rows, pooled_base_probs)
    pooled = {}; eligible = []
    for c in CANDIDATES:
        rows_c = [r for r, _ in all_oof[c]]
        assert [(r["season"], r["fixture_id"]) for r in rows_c] == [(r["season"], r["fixture_id"]) for r in pooled_rows]
        probs_c = np.vstack([p for _, p in all_oof[c]]); cm = metrics(rows_c, probs_c); delta = metric_delta(cm, base_pooled)
        fold_nonworse = sum(1 for fr in fold_results if fr["candidates"][c]["delta"]["hda_logloss"] <= 0)
        proper = delta["hda_logloss"] < 0 and delta["draw_logloss"] < 0 and delta["draw_brier"] < 0 and delta["draw_auc"] > 0 and fold_nonworse >= 2
        n_req = max(10, math.ceil(0.01 * cm["n"])); precision_req = max(cm["draw_prevalence"] + 0.08, 0.35)
        execution = cm["natural_top1_draw_count"] >= n_req and cm["natural_top1_draw_precision"] >= precision_req and cm["natural_top1_draw_recall"] >= 0.05 and delta["hda_accuracy"] >= -0.005
        pooled[c] = {
            "metrics": cm, "delta": delta, "fold_hda_ll_nonworse_count": fold_nonworse,
            "proper_score_gate": proper, "natural_top1_gate": execution,
            "top1_requirements": {"min_count": n_req, "min_precision": precision_req, "min_recall": 0.05, "min_accuracy_delta": -0.005},
            "bootstrap90_hda_ll_delta": bootstrap_hda_delta(rows_c, probs_c, pooled_base_probs),
            "development_gate_pass": proper and execution,
        }
        if proper and execution:
            eligible.append(c)
    winner = min(eligible, key=lambda c: pooled[c]["metrics"]["hda_logloss"]) if eligible else None
    status = "PASS_R50_DEVELOPMENT_ONE_WINNER_FREEZE_BEFORE_CONFIRMATION" if winner else "FAIL_R50_DEVELOPMENT_NO_ARCHITECTURE_EARNS_CONFIRMATION"

    row_path = out / "r50_development_oof_predictions.csv"
    with row_path.open("w", encoding="utf-8", newline="") as f:
        fields = ["season", "fixture_id", "date", "home", "away", "actual", "base_pH", "base_pD", "base_pA"]
        for c in CANDIDATES:
            fields += [f"{c}_pH", f"{c}_pD", f"{c}_pA"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); cprobs = {c: np.vstack([p for _, p in all_oof[c]]) for c in CANDIDATES}
        for i, r in enumerate(pooled_rows):
            d = {"season": r["season"], "fixture_id": r["fixture_id"], "date": r["date"], "home": r["home"], "away": r["away"], "actual": r["actual"], "base_pH": pooled_base_probs[i, 0], "base_pD": pooled_base_probs[i, 1], "base_pA": pooled_base_probs[i, 2]}
            for c in CANDIDATES:
                d[f"{c}_pH"], d[f"{c}_pD"], d[f"{c}_pA"] = cprobs[c][i].tolist()
            w.writerow(d)

    result = {
        "schema_version": "R50-PIT-DRAW-ARCHITECTURE-DEV-RESULT-R1", "status": status,
        "selected_winner": winner, "eligible_candidates": eligible, "prereg_sha256": sha256_file(prereg),
        "fpl_source_head": run_git(fpl_repo, "rev-parse", "HEAD").strip(), "development_seasons": list(DEV_SEASONS),
        "folds": fold_results, "pooled_baseline": base_pooled, "pooled_candidates": pooled,
        "fit_audits": fit_audits, "input_audit": input_audit,
        "governance": {
            "confirmation_2025_26_label_access": 0, "confirmation_2025_26_score_access": 0,
            "confirmation_window_remains_closed": True, "provider_requests": 0, "paid_provider_requests": 0,
            "formal_weight": 0, "formal_model_data_config_current_changes": [0, 0, 0, 0],
            "candidate_count_frozen": len(CANDIDATES), "candidate_search_after_result": 0,
        },
        "oof_predictions_sha256": sha256_file(row_path),
        "interpretation": "PASS selects exactly one architecture for a separately frozen confirmation preregistration; it does not validate the architecture on 2025/26 and does not authorize formal use. FAIL leaves the protected 2025/26 labels unopened.",
    }
    result_text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out / "r50_development_result.json").write_text(result_text, encoding="utf-8")
    receipt = {
        "status": status, "selected_winner": winner, "pooled_test_n": base_pooled["n"],
        "pooled_actual_draws": base_pooled["draw_actual"], "prereg_sha256": result["prereg_sha256"],
        "oof_predictions_sha256": result["oof_predictions_sha256"], "result_sha256": sha256_bytes(result_text.encode("utf-8")),
        "confirmation_2025_26_label_access": 0, "confirmation_window_remains_closed": True,
    }
    (out / "r50_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
