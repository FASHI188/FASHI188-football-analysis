#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43ab1.json"
R37_DIR = HERE.parent / "top1_r37_prior_h2h_matchup_context"
sys.path.insert(0, str(R37_DIR))
import run_experiment_r37 as r37  # noqa: E402

r9 = r37.r9
r34 = r37.r34
FIX_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/fixtures.parquet?download=true"

GLOBAL_PRIOR = 12.0
COMP_PRIOR = 8.0
MODEL_C = 0.5
MIN_VALIDATION_GAIN_PP = 1.0
MAX_NEGATIVE_BLOCKS = 1
MIN_POSITIVE_BLOCKS = 2

GLOBAL_FEATURES = [
    "ref_known",
    "log_ref_global_matches",
    "ref_global_home_minus_comp",
    "ref_global_draw_minus_comp",
    "ref_global_away_minus_comp",
]
COMP_FEATURES = [
    "ref_known",
    "log_ref_comp_matches",
    "ref_comp_home_minus_comp",
    "ref_comp_draw_minus_comp",
    "ref_comp_away_minus_comp",
]
FEATURE_SETS = {
    "REF_GLOBAL_STYLE": GLOBAL_FEATURES,
    "REF_COMP_STYLE": COMP_FEATURES,
    "REF_COMBINED_STYLE": list(dict.fromkeys(GLOBAL_FEATURES + COMP_FEATURES)),
}


def norm_ref(x) -> str | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    s = re.sub(r"\s+", " ", str(x).strip().lower())
    return s or None


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43ab1/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def load_ref_map() -> tuple[dict[str, str | None], dict]:
    tmp = HERE / "fixtures.parquet"
    download(FIX_URL, tmp)
    df = pd.read_parquet(tmp, columns=["id", "referee_name"])
    rows = r9.load()
    ids = {int(r["game_id"]) for r in rows}
    df = df[df["id"].isin(ids)].drop_duplicates("id")
    mapping = {str(int(r.id)): norm_ref(r.referee_name) for r in df.itertuples(index=False)}
    matched = len(mapping)
    known = sum(v is not None for v in mapping.values())
    meta = {
        "matched_fixture_ids": matched,
        "known_referee_rows": known,
        "known_referee_rate": known / 20000.0,
        "distinct_normalized_referees": len({v for v in mapping.values() if v is not None}),
    }
    tmp.unlink(missing_ok=True)
    if matched != 20000:
        raise RuntimeError(f"R43AB1 expected 20000 fixture joins, got {matched}")
    return mapping, meta


def rates(counts, prior, strength):
    c = np.asarray(counts, dtype=float)
    p = np.asarray(prior, dtype=float)
    v = c + float(strength) * p
    v = np.clip(v, 1e-12, None)
    return v / v.sum()


class RefState:
    def __init__(self):
        self.comp = defaultdict(lambda: np.zeros(3, dtype=float))
        self.global_ref = defaultdict(lambda: np.zeros(3, dtype=float))
        self.comp_ref = defaultdict(lambda: np.zeros(3, dtype=float))

    def features(self, comp: str, ref: str | None):
        cc = self.comp[comp]
        comp_rate = rates(cc, [1 / 3, 1 / 3, 1 / 3], 30.0)
        if ref is None:
            return {
                "ref_known": 0.0,
                "log_ref_global_matches": 0.0,
                "ref_global_home_minus_comp": 0.0,
                "ref_global_draw_minus_comp": 0.0,
                "ref_global_away_minus_comp": 0.0,
                "log_ref_comp_matches": 0.0,
                "ref_comp_home_minus_comp": 0.0,
                "ref_comp_draw_minus_comp": 0.0,
                "ref_comp_away_minus_comp": 0.0,
            }
        g = self.global_ref[ref]
        cr = self.comp_ref[(comp, ref)]
        gr = rates(g, comp_rate, GLOBAL_PRIOR)
        crr = rates(cr, comp_rate, COMP_PRIOR)
        return {
            "ref_known": 1.0,
            "log_ref_global_matches": math.log1p(float(g.sum())),
            "ref_global_home_minus_comp": float(gr[0] - comp_rate[0]),
            "ref_global_draw_minus_comp": float(gr[1] - comp_rate[1]),
            "ref_global_away_minus_comp": float(gr[2] - comp_rate[2]),
            "log_ref_comp_matches": math.log1p(float(cr.sum())),
            "ref_comp_home_minus_comp": float(crr[0] - comp_rate[0]),
            "ref_comp_draw_minus_comp": float(crr[1] - comp_rate[1]),
            "ref_comp_away_minus_comp": float(crr[2] - comp_rate[2]),
        }

    def update(self, comp: str, ref: str | None, y: int):
        self.comp[comp][y] += 1.0
        if ref is not None:
            self.global_ref[ref][y] += 1.0
            self.comp_ref[(comp, ref)][y] += 1.0


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    refmap, refmeta = load_ref_map()
    base = r9.S()
    rs = RefState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            ref = refmap.get(row["game_id"])
            cf = rs.features(row["competition_id"], ref)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw, ref))
        for row, raw, ref in pending:
            y = r9.actual(row)
            base.update(row, raw)
            rs.update(row["competition_id"], ref, y)
    return pred, refmeta


def x_for(rec, names):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in names]


def fit_model(train, names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    m.fit([x_for(r, names) for r in train], [r["y"] for r in train])
    return m


def baseline_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    m.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    return m


def decorate_candidate(model, rows, names):
    pr = model.predict_proba([x_for(r, names) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, p in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, q in zip(classes, p):
            v[int(cls)] = float(q)
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def decorate_baseline(model, rows):
    p = r34.r19.decorate_k1(model, rows)
    return [{"date": r["date"], "y": r["y"], "P": q} for r, q in zip(rows, p)]


def metrics(rows):
    m = r9.metrics([{"y": r["y"], "P": r["P"]} for r in rows], "P")
    n = len(rows)
    dll = dbr = 0.0
    for r in rows:
        pd = min(max(float(r["P"]["p_draw"]), 1e-15), 1 - 1e-15)
        yd = 1.0 if r["y"] == 1 else 0.0
        dll -= yd * math.log(pd) + (1 - yd) * math.log(1 - pd)
        dbr += (pd - yd) ** 2
    m["draw_logloss"] = dll / n
    m["draw_brier"] = dbr / n
    return m


def paired_blocks(base_rows, cand_rows):
    dates = sorted({r["date"] for r in base_rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), 4)
    block = {d: i for i, c in enumerate(chunks) for d in c.tolist()}
    z = {str(i): {"n": 0, "base_hits": 0, "candidate_hits": 0, "net_hits": 0} for i in range(4)}
    for b, c in zip(base_rows, cand_rows):
        if b["date"] != c["date"] or b["y"] != c["y"]:
            raise RuntimeError("R43AB1 paired row mismatch")
        q = z[str(block[b["date"]])]
        hb = int(b["P"]["top1"] == b["y"])
        hc = int(c["P"]["top1"] == c["y"])
        q["n"] += 1; q["base_hits"] += hb; q["candidate_hits"] += hc
    for q in z.values():
        q["net_hits"] = q["candidate_hits"] - q["base_hits"]
    return {
        "blocks": z,
        "positive_blocks": sum(q["net_hits"] > 0 for q in z.values()),
        "negative_blocks": sum(q["net_hits"] < 0 for q in z.values()),
        "nonnegative_blocks": sum(q["net_hits"] >= 0 for q in z.values()),
    }


def delta(b, c):
    return {
        "hits": c["hits"] - b["hits"],
        "accuracy_pp": 100.0 * (c["top1_accuracy"] - b["top1_accuracy"]),
        "logloss": c["logloss"] - b["logloss"],
        "brier": c["brier"] - b["brier"],
        "rps": c["rps"] - b["rps"],
        "draw_logloss": c["draw_logloss"] - b["draw_logloss"],
        "draw_brier": c["draw_brier"] - b["draw_brier"],
    }


def dev_gate(d, pb):
    return bool(
        d["accuracy_pp"] >= MIN_VALIDATION_GAIN_PP
        and d["logloss"] < 0 and d["brier"] < 0 and d["rps"] < 0
        and d["draw_logloss"] <= 0 and d["draw_brier"] <= 0
        and pb["nonnegative_blocks"] >= 3
        and pb["positive_blocks"] >= MIN_POSITIVE_BLOCKS
        and pb["negative_blocks"] <= MAX_NEGATIVE_BLOCKS
    )


def run():
    pred, refmeta = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]
    base_model = baseline_model(train)
    val_base_rows = decorate_baseline(base_model, val0)
    vb = metrics(val_base_rows)
    if vb["hits"] != 2064:
        raise RuntimeError(f"R43AB1 K1 validation reproduction failed: {vb['hits']}")

    candidates = []
    models = {}
    for name, names in FEATURE_SETS.items():
        m = fit_model(train, names); models[name] = m
        rows = decorate_candidate(m, val0, names)
        cm = metrics(rows); d = delta(vb, cm); pb = paired_blocks(val_base_rows, rows)
        candidates.append({"name": name, "features": names, "validation": cm, "candidate_minus_baseline": d, "paired_time_blocks": pb, "strong_validation_gate": dev_gate(d, pb)})

    viable = [x for x in candidates if x["strong_validation_gate"]]
    selected = None; historical_test = None; action = "CLOSE_REFEREE_STYLE_AXIS_NO_STRONG_VALIDATION_SIGNAL"
    if viable:
        selected = max(viable, key=lambda x: (x["candidate_minus_baseline"]["accuracy_pp"], -x["candidate_minus_baseline"]["logloss"]))
        test_base_rows = decorate_baseline(base_model, test0)
        tb = metrics(test_base_rows)
        if tb["hits"] != 1877:
            raise RuntimeError(f"R43AB1 K1 test reproduction failed: {tb['hits']}")
        name = selected["name"]
        cr = decorate_candidate(models[name], test0, FEATURE_SETS[name])
        tc = metrics(cr); td = delta(tb, tc); tpb = paired_blocks(test_base_rows, cr)
        strong_test = bool(td["accuracy_pp"] >= 1.0 and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0 and td["draw_logloss"] <= 0 and td["draw_brier"] <= 0 and tpb["nonnegative_blocks"] >= 3)
        historical_test = {"baseline": tb, "candidate": tc, "candidate_minus_baseline": td, "paired_time_blocks": tpb, "strong_test_gate": strong_test}
        action = "FREEZE_REFEREE_ARCHITECTURE_FOR_GENUINELY_FRESH_CONFIRMATION" if strong_test else "DO_NOT_PROMOTE_OR_RETUNE_REFEREE_STYLE_ON_CONSUMED_HISTORY"

    out = {
        "schema_version": "football3-r43ab1-referee-prior-style-screen-v1",
        "status": "COMPLETE",
        "classification": "POSTVIEW_HISTORICAL_DEVELOPMENT_PIT_UNVERIFIED_FORMAL_WEIGHT_ZERO",
        "formal_weight": 0,
        "governance": {
            "source_r43ab0_branch_head": "bd394f1b2cea8e28b238659170291e554575f121",
            "same_r9_consumed_20k_history": True,
            "current_referee_identity_used": True,
            "current_referee_publication_timestamp_verified": False,
            "current_match_result_or_xg_used_in_referee_features": False,
            "same_date_referee_updates_withheld": True,
            "odds_used": False,
            "current_lineup_used": False,
            "feature_sets_predeclared": True,
            "hyperparameter_search": False,
            "test_opened_only_after_strong_validation_gate": True,
            "promotion_allowed_from_this_run": False,
        },
        "design": {
            "feature_sets": FEATURE_SETS,
            "global_prior_strength": GLOBAL_PRIOR,
            "competition_ref_prior_strength": COMP_PRIOR,
            "model": f"StandardScaler + multinomial LogisticRegression C={MODEL_C}",
            "strong_validation_gate": ">=+1.0pp Top1; LL/Brier/RPS all improve; draw LL/Brier nonworse; >=3/4 time blocks nonnegative and >=2 positive",
            "strong_test_gate": ">=+1.0pp Top1; LL/Brier/RPS all improve; draw LL/Brier nonworse; >=3/4 time blocks nonnegative",
        },
        "coverage": refmeta,
        "split": {"train_n": len(train), "validation_n": len(val0), "historical_test_n": len(test0)},
        "validation_baseline": vb,
        "validation_candidates": candidates,
        "selected_feature_set": selected["name"] if selected else None,
        "historical_test": historical_test,
        "action": action,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "coverage": refmeta, "selected": out["selected_feature_set"], "validation": [{"name": x["name"], "delta": x["candidate_minus_baseline"], "gate": x["strong_validation_gate"]} for x in candidates], "historical_test": historical_test, "action": action}, ensure_ascii=False, indent=2))
    return out


def verify():
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    assert s["coverage"]["matched_fixture_ids"] == 20000
    assert s["governance"]["current_referee_publication_timestamp_verified"] is False
    assert s["governance"]["same_date_referee_updates_withheld"] is True
    assert s["validation_baseline"]["hits"] == 2064
    if s["historical_test"] is not None:
        assert s["historical_test"]["baseline"]["hits"] == 1877
    print("R43AB1 referee prior-style development contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
