#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
E1_DIR = ROOT / "football-data" / "experiments" / "r43e1_coach_half_shrink_prev20k"
if str(E1_DIR) not in sys.path:
    sys.path.insert(0, str(E1_DIR))
import run_r43e1 as e1  # noqa: E402

r9 = e1.r9
e0 = e1.e0
SOURCE_R43E1_HEAD = "02d63f6611f2a3f7c632f21e82fe329e5987da91"
ALPHA = 0.50
EPS = 1e-10


def load_older20k() -> tuple[list[dict], dict]:
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    e1.download(e1.FIX_URL, fp)
    e1.download(e1.STAT_URL, sp)
    if e1.fsha(fp) != e1.EXPECTED_FIX_SHA:
        raise RuntimeError("fixtures source drift")
    if e1.fsha(sp) != e1.EXPECTED_STAT_SHA:
        raise RuntimeError("match_stats source drift")

    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"],
    )
    st = pd.read_parquet(sp, columns=["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"])
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["kick"] = pd.to_datetime(df["date_utc"], utc=True)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > df["kick"]) & (df["home_xg"].between(0, 6)) & (df["away_xg"].between(0, 6))]
    df["date"] = df["kick"].dt.date.astype(str)
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    if len(df) < 60000:
        raise RuntimeError(f"need >=60000 valid rows, got {len(df)}")
    sl = df.iloc[-60000:-40000].copy()
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
        "fixtures_sha256": e1.fsha(fp),
        "match_stats_sha256": e1.fsha(sp),
        "valid_joined_rows": int(len(df)),
        "slice": "[-60000:-40000]",
        "rows": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
    }


def fit_binary(train, fn, label_fn):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=e0.MODEL_C, max_iter=3000, random_state=0))
    m.fit([fn(r) for r in train], [label_fn(r) for r in train])
    return m


def p1(model, rows, fn) -> np.ndarray:
    p = model.predict_proba([fn(r) for r in rows])
    classes = list(model[-1].classes_)
    j = classes.index(1)
    return np.clip(p[:, j], EPS, 1.0 - EPS)


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1.0 - EPS)
    return np.log(p / (1.0 - p))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def flat_probs(model, rows) -> np.ndarray:
    pr = model.predict_proba([e0.feat_base(r) for r in rows])
    classes = list(model[-1].classes_)
    out = np.zeros((len(rows), 3), dtype=float)
    for j, c in enumerate(classes):
        out[:, int(c)] = pr[:, j]
    out = np.clip(out, EPS, 1.0)
    return out / out.sum(axis=1, keepdims=True)


def hierarchical(pd: np.ndarray, ph_given_not_draw: np.ndarray) -> np.ndarray:
    q = np.column_stack([
        (1.0 - pd) * ph_given_not_draw,
        pd,
        (1.0 - pd) * (1.0 - ph_given_not_draw),
    ])
    q = np.clip(q, EPS, 1.0)
    return q / q.sum(axis=1, keepdims=True)


def decorate_all(flat_model, draw_base, draw_coach, side_model, rows):
    pf = flat_probs(flat_model, rows)
    pdb = p1(draw_base, rows, e0.feat_base)
    pdc = p1(draw_coach, rows, e0.feat_coach)
    pdh = sigmoid((1.0 - ALPHA) * logit(pdb) + ALPHA * logit(pdc))
    ph = p1(side_model, rows, e0.feat_base)
    qb = hierarchical(pdb, ph)
    qf = hierarchical(pdc, ph)
    qh = hierarchical(pdh, ph)
    for rec, a, b, c, h in zip(rows, pf, qb, qf, qh):
        rec["flat"] = r9.decorate(a)
        rec["hier_base"] = r9.decorate(b)
        rec["hier_coach_full"] = r9.decorate(c)
        rec["hier_coach_half"] = r9.decorate(h)


def block_metrics(test, n=4):
    out = []
    for ix in np.array_split(np.arange(len(test)), n):
        rr = [test[int(i)] for i in ix]
        a = r9.metrics(rr, "flat")
        b = r9.metrics(rr, "hier_base")
        h = r9.metrics(rr, "hier_coach_half")
        out.append({
            "first_date": rr[0]["date"],
            "last_date": rr[-1]["date"],
            "n": len(rr),
            "flat_hits": a["hits"],
            "hier_base_hits": b["hits"],
            "candidate_hits": h["hits"],
            "candidate_delta_hits_vs_flat": h["hits"] - a["hits"],
            "candidate_delta_hits_vs_hier_base": h["hits"] - b["hits"],
            "candidate_delta_logloss_vs_flat": h["logloss"] - a["logloss"],
            "candidate_delta_logloss_vs_hier_base": h["logloss"] - b["logloss"],
        })
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, raw_meta = load_older20k()
    coach_map, stats_map, aux_meta = e0.load_aux(rows)
    if aux_meta["fixture_lineups_sha256"] != e1.EXPECTED_LINEUP_SHA:
        raise RuntimeError("fixture_lineups source drift")
    if aux_meta["match_stats_sha256"] != e1.EXPECTED_STAT_SHA:
        raise RuntimeError("aux match_stats source drift")

    pred = e0.build(rows, coach_map, stats_map)
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    flat_model = e0.fit(train, e0.feat_base)
    draw_base = fit_binary(train, e0.feat_base, lambda r: int(r["y"] == 1))
    draw_coach = fit_binary(train, e0.feat_coach, lambda r: int(r["y"] == 1))
    nondraw_train = [r for r in train if r["y"] != 1]
    side_model = fit_binary(nondraw_train, e0.feat_base, lambda r: int(r["y"] == 0))

    decorate_all(flat_model, draw_base, draw_coach, side_model, val)
    decorate_all(flat_model, draw_base, draw_coach, side_model, test)

    keys = ("flat", "hier_base", "hier_coach_full", "hier_coach_half")
    vm = {k: r9.metrics(val, k) for k in keys}
    tm = {k: r9.metrics(test, k) for k in keys}
    blocks = block_metrics(test)
    pos_flat_ll = sum(x["candidate_delta_logloss_vs_flat"] < 0 for x in blocks)
    pos_hier_ll = sum(x["candidate_delta_logloss_vs_hier_base"] < 0 for x in blocks)
    pos_flat_hits = sum(x["candidate_delta_hits_vs_flat"] > 0 for x in blocks)

    flat = tm["flat"]
    hb = tm["hier_base"]
    cand = tm["hier_coach_half"]
    gate = bool(
        cand["logloss"] < flat["logloss"]
        and cand["brier"] < flat["brier"]
        and cand["rps"] < flat["rps"]
        and cand["hits"] >= flat["hits"]
        and cand["logloss"] <= hb["logloss"]
        and cand["hits"] >= hb["hits"]
        and pos_flat_ll >= 2
        and pos_hier_ll >= 2
        and pos_flat_hits >= 2
    )

    result = {
        "schema_version": "football3-r43e2-hierarchical-draw-coach-older20k-v1",
        "status": "COMPLETE",
        "classification": "THIRD_DISJOINT_20K_HIERARCHICAL_DRAW_COACH_TEST",
        "formal_weight": 0,
        "source_r43e1_head": SOURCE_R43E1_HEAD,
        "governance": {
            "source_overlap_with_r43e0_or_r43e1_scored_blocks": False,
            "target_match_coach_used_for_prediction": False,
            "target_match_stats_used_before_prediction": False,
            "same_date_update_before_prediction": False,
            "odds_used": False,
            "parameter_search": False,
            "draw_half_alpha_fixed_before_test": ALPHA,
            "test_used_for_architecture_selection": False,
            "r42l_lock_modified": False,
        },
        "design": {
            "problem": "coach features repeatedly add draw/Top1 discrimination but degrade joint multinomial proper scores",
            "flat_baseline": "R9b K1 multinomial",
            "hierarchical_base": "binary draw-vs-not-draw plus binary home-vs-away conditional on non-draw",
            "coach_candidate": "coach fingerprint enters only the draw-vs-not-draw rail; home-vs-away rail stays baseline",
            "candidate_shrink": "fixed 50% logit shrink between base draw probability and coach draw probability",
            "alpha": ALPHA,
            "model_C": e0.MODEL_C,
            "no_manual_draw_override": True,
        },
        "source": {**raw_meta, **aux_meta},
        "split": {
            "burn_n": b1,
            "train_n": len(train),
            "val_n": len(val),
            "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
        },
        "validation": vm,
        "test": {
            **tm,
            "candidate_delta_vs_flat": {
                "hits": cand["hits"] - flat["hits"],
                "accuracy_pp": 100.0 * (cand["top1_accuracy"] - flat["top1_accuracy"]),
                "logloss": cand["logloss"] - flat["logloss"],
                "brier": cand["brier"] - flat["brier"],
                "rps": cand["rps"] - flat["rps"],
            },
            "candidate_delta_vs_hier_base": {
                "hits": cand["hits"] - hb["hits"],
                "logloss": cand["logloss"] - hb["logloss"],
                "brier": cand["brier"] - hb["brier"],
                "rps": cand["rps"] - hb["rps"],
            },
            "blocks": blocks,
            "positive_ll_blocks_vs_flat": pos_flat_ll,
            "positive_ll_blocks_vs_hier_base": pos_hier_ll,
            "positive_hit_blocks_vs_flat": pos_flat_hits,
        },
        "gate": {
            "passed": gate,
            "action": "KEEP_HIERARCHICAL_DRAW_COACH_RAIL_FOR_CONTEXT_INTERACTION_STAGE" if gate else "DO_NOT_PROMOTE_R43E2_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "This is historical evidence from an older disjoint block, not forward confirmation.",
            "Current-match coach changes remain unknown until one completed fixture is observed because no verified prematch coach timestamp is available.",
            "Fatigue, lineup depth and future-match importance are not yet included.",
        ],
    }
    p = OUT / "summary_r43e2_hierarchical_draw_coach_older20k.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43e2_hierarchical_draw_coach_older20k.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["source_overlap_with_r43e0_or_r43e1_scored_blocks"] is False
    assert g["target_match_coach_used_for_prediction"] is False
    assert g["target_match_stats_used_before_prediction"] is False
    assert g["same_date_update_before_prediction"] is False
    assert g["parameter_search"] is False
    assert abs(float(g["draw_half_alpha_fixed_before_test"]) - 0.5) < 1e-12
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    print("R43E2 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
