#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9_DIR = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf"
E0_DIR = ROOT / "football-data" / "experiments" / "r43e0_coach_fingerprint_oos"
for p in (R9_DIR, E0_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run_experiment_r9b as r9  # noqa: E402
import run_r43e0 as e0  # noqa: E402

SOURCE_R43E0_HEAD = "8625e28a8567e348fe6cb8bd87ff3733751362df"
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
EXPECTED_FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
EXPECTED_LINEUP_SHA = "dcd9181f54df52193877ffd8a41b5d1097b404eb46b4890069c0e4d1c8c13abd"
ALPHA = 0.50
EPS = 1e-12


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43e1/1"})
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
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    download(FIX_URL, fp)
    download(STAT_URL, sp)
    if fsha(fp) != EXPECTED_FIX_SHA:
        raise RuntimeError("fixtures source drift")
    if fsha(sp) != EXPECTED_STAT_SHA:
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


def vector(model, x, fn) -> np.ndarray:
    pr = model.predict_proba([fn(r) for r in x])
    classes = list(model[-1].classes_)
    out = np.zeros((len(x), 3), dtype=float)
    for j, c in enumerate(classes):
        out[:, int(c)] = pr[:, j]
    out = np.clip(out, EPS, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def half_blend(base: np.ndarray, coach: np.ndarray) -> np.ndarray:
    z = np.exp((1.0 - ALPHA) * np.log(np.clip(base, EPS, 1.0)) + ALPHA * np.log(np.clip(coach, EPS, 1.0)))
    return z / z.sum(axis=1, keepdims=True)


def decorate_models(base_model, coach_model, rows):
    pb = vector(base_model, rows, e0.feat_base)
    pc = vector(coach_model, rows, e0.feat_coach)
    ph = half_blend(pb, pc)
    for rec, b, c, h in zip(rows, pb, pc, ph):
        rec["base"] = r9.decorate(b)
        rec["coach_full"] = r9.decorate(c)
        rec["coach_half"] = r9.decorate(h)


def block_metrics(test, n=4):
    out = []
    for ix in np.array_split(np.arange(len(test)), n):
        rr = [test[int(i)] for i in ix]
        b = r9.metrics(rr, "base")
        f = r9.metrics(rr, "coach_full")
        h = r9.metrics(rr, "coach_half")
        out.append({
            "first_date": rr[0]["date"],
            "last_date": rr[-1]["date"],
            "n": len(rr),
            "base_hits": b["hits"],
            "full_hits": f["hits"],
            "half_hits": h["hits"],
            "half_delta_hits": h["hits"] - b["hits"],
            "half_delta_logloss": h["logloss"] - b["logloss"],
            "half_delta_brier": h["brier"] - b["brier"],
            "half_delta_rps": h["rps"] - b["rps"],
        })
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, raw_meta = load_prev20k()
    coach_map, stats_map, aux_meta = e0.load_aux(rows)
    if aux_meta["fixture_lineups_sha256"] != EXPECTED_LINEUP_SHA:
        raise RuntimeError("fixture_lineups source drift")
    if aux_meta["match_stats_sha256"] != EXPECTED_STAT_SHA:
        raise RuntimeError("aux match_stats source drift")

    pred = e0.build(rows, coach_map, stats_map)
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    base_model = e0.fit(train, e0.feat_base)
    coach_model = e0.fit(train, e0.feat_coach)
    decorate_models(base_model, coach_model, val)
    decorate_models(base_model, coach_model, test)

    vb = r9.metrics(val, "base")
    vf = r9.metrics(val, "coach_full")
    vh = r9.metrics(val, "coach_half")
    tb = r9.metrics(test, "base")
    tf = r9.metrics(test, "coach_full")
    th = r9.metrics(test, "coach_half")
    blocks = block_metrics(test)
    pos_ll = sum(x["half_delta_logloss"] < 0 for x in blocks)
    neg_ll = sum(x["half_delta_logloss"] > 0 for x in blocks)
    pos_hits = sum(x["half_delta_hits"] > 0 for x in blocks)
    neg_hits = sum(x["half_delta_hits"] < 0 for x in blocks)

    gate = bool(
        th["logloss"] < tb["logloss"]
        and th["brier"] < tb["brier"]
        and th["rps"] < tb["rps"]
        and th["hits"] >= tb["hits"]
        and pos_ll >= 2
        and neg_ll <= 2
        and pos_hits >= 2
        and neg_hits <= 2
    )

    result = {
        "schema_version": "football3-r43e1-coach-half-shrink-prev20k-v1",
        "status": "COMPLETE",
        "classification": "DISJOINT_PREVIOUS_20K_FIXED_HALF_COACH_SIGNAL_REPLICATION",
        "formal_weight": 0,
        "source_r43e0_head": SOURCE_R43E0_HEAD,
        "governance": {
            "source_overlap_with_r43e0_latest20k": False,
            "target_match_coach_used_for_prediction": False,
            "target_match_stats_used_before_prediction": False,
            "same_date_update_before_prediction": False,
            "odds_used": False,
            "parameter_search": False,
            "alpha_fixed_before_disjoint_test": ALPHA,
            "alpha_selected_from_test": False,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "R9b K1 multinomial probability model",
            "full_signal": "R43E0 strictly prior last-observed coach fingerprint model",
            "candidate": "fixed geometric half-shrink between baseline and full coach probabilities",
            "formula": "p_half proportional to p_base^(1-alpha) * p_coach^alpha",
            "alpha": ALPHA,
            "reason": "R43E0 improved Top1 but worsened proper scores; test whether lower signal magnitude survives a zero-overlap era without tuning",
            "model_C": e0.MODEL_C,
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
        "validation": {
            "baseline": vb,
            "coach_full": vf,
            "coach_half": vh,
            "half_delta_hits": vh["hits"] - vb["hits"],
            "half_delta_logloss": vh["logloss"] - vb["logloss"],
            "half_delta_brier": vh["brier"] - vb["brier"],
            "half_delta_rps": vh["rps"] - vb["rps"],
        },
        "test": {
            "baseline": tb,
            "coach_full": tf,
            "coach_half": th,
            "half_delta_hits": th["hits"] - tb["hits"],
            "half_delta_accuracy_pp": 100.0 * (th["top1_accuracy"] - tb["top1_accuracy"]),
            "half_delta_logloss": th["logloss"] - tb["logloss"],
            "half_delta_brier": th["brier"] - tb["brier"],
            "half_delta_rps": th["rps"] - tb["rps"],
            "blocks": blocks,
            "positive_logloss_blocks": pos_ll,
            "negative_logloss_blocks": neg_ll,
            "positive_hit_blocks": pos_hits,
            "negative_hit_blocks": neg_hits,
        },
        "gate": {
            "passed": gate,
            "action": "KEEP_FIXED_HALF_COACH_SIGNAL_FOR_NEXT_CONTEXT_INTERACTION_STAGE" if gate else "DO_NOT_PROMOTE_R43E1_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "This is a disjoint historical replication, not fresh forward evidence.",
            "Current-match coach identity remains unavailable under a verified prematch timestamp, so the first match after a coaching change is intentionally treated as unknown.",
            "This stage only tests signal magnitude; fatigue, lineup depth and match-importance interactions remain separate future challengers.",
        ],
    }
    p = OUT / "summary_r43e1_coach_half_shrink_prev20k.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43e1_coach_half_shrink_prev20k.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["source_overlap_with_r43e0_latest20k"] is False
    assert g["target_match_coach_used_for_prediction"] is False
    assert g["target_match_stats_used_before_prediction"] is False
    assert g["same_date_update_before_prediction"] is False
    assert g["parameter_search"] is False
    assert abs(float(g["alpha_fixed_before_disjoint_test"]) - 0.5) < 1e-12
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    print("R43E1 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
