#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9_DIR = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

SOURCE_R43D0_HEAD = "931d70c27e7d5d42c770521b6a89fe747b723da9"
MAXG = 12
CONCENTRATIONS = (2.0, 5.0, 10.0, 20.0, 50.0)
EPS = 1e-15
IPF_TOL = 1e-12
IPF_ITERS = 200


def download(url: str, path: Path):
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43d1/1"})
    with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def fsha(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_prev20k() -> tuple[list[dict], dict]:
    DATA.mkdir(parents=True, exist_ok=True)
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    download(r9.FIX_URL, fp)
    download(r9.STAT_URL, sp)
    fx = pd.read_parquet(fp, columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"])
    st = pd.read_parquet(sp, columns=["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"])
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df["kick"] = pd.to_datetime(df["date_utc"], utc=True)
    df = df[(df["known"] > df["kick"]) & (df["home_xg"].between(0, 6)) & (df["away_xg"].between(0, 6))]
    df["date"] = df["kick"].dt.date.astype(str)
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    if len(df) < 40000:
        raise RuntimeError(f"need >=40000 valid rows, got {len(df)}")
    sl = df.iloc[-40000:-20000].copy()
    rows = []
    for x in sl.itertuples(index=False):
        rows.append({
            "date": str(x.date),
            "game_id": str(int(x.id)),
            "competition_id": str(int(x.league_id)),
            "home_team": str(int(x.home_team_id)),
            "away_team": str(int(x.away_team_id)),
            "home_goals": int(x.goals_home),
            "away_goals": int(x.goals_away),
            "home_xg": float(x.home_xg),
            "away_xg": float(x.away_xg),
            "xg_known_at": x.known.isoformat(),
        })
    return rows, {
        "fixtures_sha256": fsha(fp),
        "match_stats_sha256": fsha(sp),
        "valid_joined_rows": int(len(df)),
        "slice": "[-40000:-20000]",
        "rows": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
    }


def poisson_matrix(mu_h: float, mu_a: float) -> dict[tuple[int, int], float]:
    hp = [math.exp(-mu_h)]
    ap = [math.exp(-mu_a)]
    for k in range(1, MAXG + 1):
        hp.append(hp[-1] * mu_h / k)
        ap.append(ap[-1] * mu_a / k)
    m = {(h, a): hp[h] * ap[a] for h in range(MAXG + 1) for a in range(MAXG + 1)}
    s = sum(m.values())
    return {k: v / s for k, v in m.items()}


def beta_binom_pmf(k: int, n: int, p: float, concentration: float) -> float:
    p = min(1 - 1e-8, max(1e-8, p))
    a = p * concentration
    b = (1 - p) * concentration
    return math.exp(
        math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        + math.lgamma(k + a) + math.lgamma(n - k + b) - math.lgamma(n + a + b)
        + math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    )


def result_key(h: int, a: int) -> str:
    return "H" if h > a else "D" if h == a else "A"


def exact_total_marginal(m: dict) -> dict[int, float]:
    out = defaultdict(float)
    for (h, a), q in m.items():
        out[h + a] += q
    return dict(out)


def result_marginal(m: dict) -> dict[str, float]:
    out = {"H": 0.0, "D": 0.0, "A": 0.0}
    for (h, a), q in m.items():
        out[result_key(h, a)] += q
    return out


def beta_seed(prior: dict, mu_h: float, mu_a: float, concentration: float) -> dict:
    tm = exact_total_marginal(prior)
    p = mu_h / max(mu_h + mu_a, 1e-12)
    out = {}
    for n, mass in tm.items():
        cells = []
        for h in range(max(0, n - MAXG), min(MAXG, n) + 1):
            a = n - h
            if 0 <= a <= MAXG:
                cells.append((h, a, beta_binom_pmf(h, n, p, concentration)))
        z = sum(q for _, _, q in cells)
        for h, a, q in cells:
            out[(h, a)] = mass * q / z
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def ipf_preserve_total_and_result(seed: dict, prior: dict) -> tuple[dict, float, float, int]:
    target_t = exact_total_marginal(prior)
    target_r = result_marginal(prior)
    m = {k: max(EPS, float(v)) for k, v in seed.items()}
    it_used = 0
    for it in range(IPF_ITERS):
        it_used = it + 1
        cur_t = exact_total_marginal(m)
        for n, target in target_t.items():
            cur = cur_t.get(n, 0.0)
            if cur > 0:
                fac = target / cur
                for k in list(m):
                    if k[0] + k[1] == n:
                        m[k] *= fac
        cur_r = result_marginal(m)
        for r, target in target_r.items():
            cur = cur_r[r]
            if cur > 0:
                fac = target / cur
                for k in list(m):
                    if result_key(*k) == r:
                        m[k] *= fac
        rt = max(abs(exact_total_marginal(m).get(n, 0.0) - target_t[n]) for n in target_t)
        rr = max(abs(result_marginal(m)[r] - target_r[r]) for r in target_r)
        if max(rt, rr) <= IPF_TOL:
            break
    s = sum(m.values())
    m = {k: v / s for k, v in m.items()}
    rt = max(abs(exact_total_marginal(m).get(n, 0.0) - target_t[n]) for n in target_t)
    rr = max(abs(result_marginal(m)[r] - target_r[r]) for r in target_r)
    return m, rr, rt, it_used


def metric(m: dict, hg: int, ag: int) -> dict:
    ranked = sorted(((p, h, a) for (h, a), p in m.items()), reverse=True)
    top1 = (ranked[0][1], ranked[0][2])
    pactual = m.get((hg, ag), EPS)
    return {
        "top1_hit": int(top1 == (hg, ag)),
        "top3_hit": int((hg, ag) in {(h, a) for _, h, a in ranked[:3]}),
        "ll": -math.log(max(EPS, pactual)),
        "pactual": float(pactual),
        "top1_score": f"{top1[0]}-{top1[1]}",
        "top1_11": int(top1 == (1, 1)),
        "actual_11": int((hg, ag) == (1, 1)),
    }


def agg(rows: list[dict], key: str) -> dict:
    ms = [r[key] for r in rows]
    n = len(ms)
    tc = Counter(x["top1_score"] for x in ms)
    top11 = sum(x["top1_11"] for x in ms) / n
    act11 = sum(x["actual_11"] for x in ms) / n
    return {
        "n": n,
        "top1_accuracy": sum(x["top1_hit"] for x in ms) / n,
        "top3_accuracy": sum(x["top3_hit"] for x in ms) / n,
        "mean_logloss": sum(x["ll"] for x in ms) / n,
        "mean_actual_probability": sum(x["pactual"] for x in ms) / n,
        "top1_1_1_share": top11,
        "actual_1_1_rate": act11,
        "one_one_excess": top11 - act11,
        "abs_one_one_gap": abs(top11 - act11),
        "top1_score_counts": dict(tc.most_common(12)),
    }


def boundary(rows: list[dict], target: int) -> int:
    i = min(max(1, target), len(rows) - 1)
    while i < len(rows) and rows[i]["date"] == rows[i - 1]["date"]:
        i += 1
    return i


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, source = load_prev20k()
    state = r9.S()
    recs = []
    max_rr = max_rt = 0.0
    max_ipf_iters = 0
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)

    for ds in sorted(by_date):
        pending = []
        for row in sorted(by_date[ds], key=lambda x: x["game_id"]):
            pred = state.pred(row)
            mu_h, mu_a = float(pred["mu_home"]), float(pred["mu_away"])
            prior = poisson_matrix(mu_h, mu_a)
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            rec = {"date": ds, "baseline": metric(prior, hg, ag)}
            for c in CONCENTRATIONS:
                seed = beta_seed(prior, mu_h, mu_a, c)
                cand, rr, rt, itn = ipf_preserve_total_and_result(seed, prior)
                max_rr = max(max_rr, rr)
                max_rt = max(max_rt, rt)
                max_ipf_iters = max(max_ipf_iters, itn)
                rec[f"c{c:g}"] = metric(cand, hg, ag)
            recs.append(rec)
            pending.append((row, pred))
        for row, pred in pending:
            state.update(row, pred)

    b1 = boundary(recs, 4000)
    b2 = boundary(recs, b1 + 8000)
    b3 = boundary(recs, b2 + 4000)
    train, val, test = recs[b1:b2], recs[b2:b3], recs[b3:]
    train_base = agg(train, "baseline")
    train_cands = {str(c): agg(train, f"c{c:g}") for c in CONCENTRATIONS}
    selected = min(CONCENTRATIONS, key=lambda c: (train_cands[str(c)]["mean_logloss"], train_cands[str(c)]["abs_one_one_gap"]))
    skey = f"c{selected:g}"
    vb, vc = agg(val, "baseline"), agg(val, skey)
    tb, tc = agg(test, "baseline"), agg(test, skey)
    val_pass = bool(vc["mean_logloss"] <= vb["mean_logloss"] and vc["abs_one_one_gap"] < vb["abs_one_one_gap"])
    gate = bool(
        val_pass
        and tc["mean_logloss"] < tb["mean_logloss"]
        and tc["abs_one_one_gap"] < tb["abs_one_one_gap"]
        and tc["top1_accuracy"] >= tb["top1_accuracy"]
        and max_rr <= 1e-10
        and max_rt <= 1e-10
    )

    result = {
        "schema_version": "football3-r43d1-beta-binomial-score-dispersion-prev20k-v1",
        "status": "COMPLETE",
        "classification": "DISJOINT_PREVIOUS_20K_EXACT_SCORE_DISPERSION_CHALLENGE",
        "formal_weight": 0,
        "source_r43d0_head": SOURCE_R43D0_HEAD,
        "governance": {
            "source_overlap_with_r43d0_latest20k": False,
            "target_result_used_before_prediction": False,
            "same_date_update_before_prediction": False,
            "odds_used": False,
            "manual_one_one_penalty": False,
            "manual_draw_override": False,
            "concentrations_predeclared": list(CONCENTRATIONS),
            "selection_uses_train_only": True,
            "selection_uses_validation_or_test": False,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "independent Poisson score matrix",
            "candidate": "beta-binomial conditional home-goal allocation given total, followed by IPF to restore exact total-goals and 1X2 marginals",
            "interpretation": "tests whether within-total goal allocation is too concentrated around central splits such as 1-1",
            "concentrations": list(CONCENTRATIONS),
            "max_goal_axis": MAXG,
            "ipf_tolerance": IPF_TOL,
            "selection_rule": "minimum train exact-score logloss; 1-1 gap is tie-break only",
        },
        "source": source,
        "split": {
            "burn": [0, b1],
            "train": [b1, b2],
            "validation": [b2, b3],
            "test": [b3, len(recs)],
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "validation_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
        },
        "audit": {
            "max_1x2_residual": max_rr,
            "max_exact_total_residual": max_rt,
            "max_ipf_iterations": max_ipf_iters,
        },
        "train": {
            "baseline": train_base,
            "candidates": train_cands,
            "selected_concentration": selected,
        },
        "validation": {
            "baseline": vb,
            "selected": vc,
            "delta_logloss": vc["mean_logloss"] - vb["mean_logloss"],
            "delta_top1": vc["top1_accuracy"] - vb["top1_accuracy"],
            "delta_abs_one_one_gap": vc["abs_one_one_gap"] - vb["abs_one_one_gap"],
            "passed": val_pass,
        },
        "test": {
            "baseline": tb,
            "selected": tc,
            "delta_logloss": tc["mean_logloss"] - tb["mean_logloss"],
            "delta_top1": tc["top1_accuracy"] - tb["top1_accuracy"],
            "delta_top3": tc["top3_accuracy"] - tb["top3_accuracy"],
            "delta_abs_one_one_gap": tc["abs_one_one_gap"] - tb["abs_one_one_gap"],
        },
        "gate": {
            "passed": gate,
            "action": "PROMOTE_BETA_BINOMIAL_SCORE_DISPERSION_TO_CURRENT_ERA_REPLICATION" if gate else "DO_NOT_PROMOTE_R43D1_AND_DO_NOT_HAND_EDIT_1_1",
        },
        "limitations": [
            "This is retrospective disjoint-block evidence, not forward evidence.",
            "The beta-binomial concentration is global; no league or tactical regime conditioning is introduced in this stage.",
            "The transformation preserves the baseline 1X2 and exact total-goals marginals, so it cannot improve those marginals by construction.",
        ],
    }
    p = OUT / "summary_r43d1_beta_binomial_score_dispersion.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify():
    d = json.loads((OUT / "summary_r43d1_beta_binomial_score_dispersion.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["governance"]["source_overlap_with_r43d0_latest20k"] is False
    assert d["governance"]["selection_uses_validation_or_test"] is False
    assert d["governance"]["manual_one_one_penalty"] is False
    assert d["audit"]["max_1x2_residual"] <= 1e-10
    assert d["audit"]["max_exact_total_residual"] <= 1e-10
    print("R43D1 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
