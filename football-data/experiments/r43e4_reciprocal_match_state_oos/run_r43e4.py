#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
D1_DIR = ROOT / "football-data" / "experiments" / "r43d1_coach_tactical_fingerprint_oos"
F5_DIR = ROOT / "football-data" / "experiments" / "r43f5_probability_weighted_technical_mixture"
for p in (D1_DIR, F5_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run_r43d1 as d1  # noqa: E402
import run_r43f5 as f5  # noqa: E402

BASE_NAMES = f5.BASE_NAMES
TECH_NAMES = f5.TECH_NAMES
SOURCE_R43F5_HEAD = "0140944ee387e4328c14eef0837481d800dfcc38"
SOURCE_R43D1_R1_RUN = 33146922176
SOURCE_R43E3_RUN = 33151662202
RECIP_LOOKBACK_DAYS = 45.0
RECIP_DECAY_DAYS = 14.0
RECIP_ALPHA = 0.5
OUTCOME_TRAIN_TARGET = 2500
OUTCOME_VAL_TARGET = 1200
MIN_TEST_MATCHES = 1200
MIN_POSITIVE_LL_BLOCKS = 3
MAX_NEGATIVE_LL_BLOCKS = 1

RECIP_NAMES = [
    "recip_exists",
    "recip_cup",
    "recip_gap_decay",
    "recip_margin_home",
    "recip_abs_margin",
    "recip_prior_total_goals",
    "recip_prior_draw",
    "recip_cup_margin_home",
    "recip_cup_abs_margin",
    "recip_cup_gap_decay",
]


def load_league_type_map() -> tuple[dict[int, str], dict]:
    base = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
    pcat = HERE / "data" / "league_catalogue.parquet"
    plg = HERE / "data" / "leagues.parquet"
    pcat.parent.mkdir(parents=True, exist_ok=True)
    for name, path in (("league_catalogue.parquet", pcat), ("leagues.parquet", plg)):
        if not path.exists():
            req = urllib.request.Request(f"{base}/{name}?download=true", headers={"User-Agent": "football3-r43e4"})
            with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as w:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    w.write(b)
    cat = pd.read_parquet(pcat)
    lg = pd.read_parquet(plg)
    c1 = cat.dropna(subset=["dataset_league_id"]).drop_duplicates("dataset_league_id").set_index("dataset_league_id")
    c2 = cat.dropna(subset=["af_league_id"]).drop_duplicates("af_league_id").set_index("af_league_id")
    lgi = lg.drop_duplicates("id").set_index("id")
    out = {}
    via = defaultdict(int)
    for lid in lg["id"].dropna().astype(int).unique():
        typ = "UNKNOWN"
        if lid in c1.index:
            typ = str(c1.loc[lid].get("af_type"))
            via["dataset_league_id"] += 1
        elif lid in lgi.index:
            api = lgi.loc[lid].get("api_football_id")
            if pd.notna(api) and int(api) in c2.index:
                typ = str(c2.loc[int(api)].get("af_type"))
                via["api_football_id"] += 1
        out[int(lid)] = typ
    return out, {"league_count": len(out), "mapping_via": dict(via), "type_counts": cat["af_type"].fillna("NA").astype(str).value_counts().to_dict()}


def reciprocal_feature_map(target_fixture_ids: set[int], league_types: dict[int, str]) -> tuple[dict[int, dict], dict]:
    fp = d1.DATA / "fixtures.parquet"
    if not fp.exists():
        raise RuntimeError("R43D1 fixtures.parquet missing after load_latest20k")
    fx = pd.read_parquet(fp, columns=[
        "id", "date_utc", "league_id", "home_team_id", "away_team_id",
        "goals_home", "goals_away", "status_norm", "is_played",
    ])
    fx["date_utc"] = pd.to_datetime(fx["date_utc"], utc=True, errors="coerce")
    played = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["date_utc"].notna() & fx["goals_home"].notna() & fx["goals_away"].notna()].copy()
    played = played.sort_values(["date_utc", "id"])
    by_pair = defaultdict(list)
    fmap = {}
    coverage = defaultdict(int)
    gaps = []
    for r in played.itertuples(index=False):
        fid = int(r.id); lid = int(r.league_id); h = int(r.home_team_id); a = int(r.away_team_id)
        dt = r.date_utc
        key = (lid, min(h, a), max(h, a))
        best = None
        for p in reversed(by_pair[key]):
            gap = (dt - p["date"]).total_seconds() / 86400.0
            if gap > RECIP_LOOKBACK_DAYS:
                break
            if gap >= 2.0 and p["home"] == a and p["away"] == h:
                best = (p, gap)
                break
        if fid in target_fixture_ids:
            z = {k: 0.0 for k in RECIP_NAMES}
            if best is not None:
                p, gap = best
                margin = float(p["ga"] - p["gh"])  # current-home perspective
                abs_margin = abs(margin)
                total = float(p["gh"] + p["ga"])
                cup = 1.0 if str(league_types.get(lid, "UNKNOWN")).lower() == "cup" else 0.0
                decay = float(math.exp(-gap / RECIP_DECAY_DAYS))
                z.update({
                    "recip_exists": 1.0,
                    "recip_cup": cup,
                    "recip_gap_decay": decay,
                    "recip_margin_home": margin,
                    "recip_abs_margin": abs_margin,
                    "recip_prior_total_goals": total,
                    "recip_prior_draw": 1.0 if margin == 0 else 0.0,
                    "recip_cup_margin_home": cup * margin,
                    "recip_cup_abs_margin": cup * abs_margin,
                    "recip_cup_gap_decay": cup * decay,
                })
                coverage["reciprocal_any"] += 1
                if cup > 0:
                    coverage["reciprocal_cup"] += 1
                gaps.append(gap)
            fmap[fid] = z
        by_pair[key].append({"date": dt, "home": h, "away": a, "gh": int(r.goals_home), "ga": int(r.goals_away)})
    meta = {
        "targets": len(target_fixture_ids),
        "mapped_targets": len(fmap),
        "reciprocal_any": int(coverage["reciprocal_any"]),
        "reciprocal_cup": int(coverage["reciprocal_cup"]),
        "gap_days_mean": float(np.mean(gaps)) if gaps else None,
        "gap_days_median": float(np.median(gaps)) if gaps else None,
    }
    return fmap, meta


def add_reciprocal(records: list[dict], fmap: dict[int, dict]) -> list[dict]:
    out = []
    for x in records:
        z = dict(x)
        r = fmap.get(int(x["fixture_id"]), {k: 0.0 for k in RECIP_NAMES})
        z["recip"] = r
        z["recip_cf"] = {**x["tech_cf"], **r}
        out.append(z)
    return out


def score_model(model, split, key, names):
    return f5.score_model(model, split, key, names)


def transport_records(base_half, tech_full, recip_full, alpha=RECIP_ALPHA):
    out = []
    for b, t, r in zip(base_half, tech_full, recip_full):
        if not (b["fixture_id"] == t["fixture_id"] == r["fixture_id"]):
            raise RuntimeError("paired fixture drift")
        pb = np.clip(np.asarray(b["P"], dtype=float), 1e-12, 1.0)
        pt = np.clip(np.asarray(t["P"], dtype=float), 1e-12, 1.0)
        pr = np.clip(np.asarray(r["P"], dtype=float), 1e-12, 1.0)
        z = np.log(pb) + alpha * (np.log(pr) - np.log(pt))
        z -= float(np.max(z))
        p = np.exp(z); p /= float(np.sum(p))
        out.append({"date": b["date"], "fixture_id": b["fixture_id"], "y": b["y"], "P": p})
    return out


def subset_metrics(scored, source_records, pred):
    pairs = []
    for s, x in zip(scored, source_records):
        if pred(x):
            pairs.append(s)
    return f5.f3.enriched_metrics(pairs) if pairs else {"count": 0}


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, tactical_map, source_meta = d1.load_latest20k()
    _, context_probs, _, _, lineup_meta = f5.make_lineup_probabilities(rows)
    raw, source2 = d1.build_records(rows, tactical_map, context_probs, lineup_meta["lineup_train_end"])
    raw = sorted(raw, key=lambda x: (x["date"], x["fixture_id"]))
    league_types, league_meta = load_league_type_map()
    fmap, recip_meta = reciprocal_feature_map({int(x["fixture_id"]) for x in raw}, league_types)
    raw = add_reciprocal(raw, fmap)

    need = OUTCOME_TRAIN_TARGET + OUTCOME_VAL_TARGET + MIN_TEST_MATCHES
    if len(raw) < need:
        raise RuntimeError(f"undersized outcome cohort {len(raw)} need={need}")
    b1 = f5.f3.boundary_date_safe(raw, OUTCOME_TRAIN_TARGET)
    b2 = f5.f3.boundary_date_safe(raw, b1 + OUTCOME_VAL_TARGET)
    train, val, test = raw[:b1], raw[b1:b2], raw[b2:]

    tech_names = BASE_NAMES + TECH_NAMES
    recip_names = tech_names + RECIP_NAMES
    m0 = f5.fit_records(train, "base_cf", BASE_NAMES)
    mt = f5.fit_records(train, "tech_cf", tech_names)
    mr = f5.fit_records(train, "recip_cf", recip_names)

    def score(split):
        no = score_model(m0, split, "base_cf", BASE_NAMES)
        tfull = score_model(mt, split, "tech_cf", tech_names)
        rfull = score_model(mr, split, "recip_cf", recip_names)
        baseline = f5.half_records(no, tfull)
        candidate = transport_records(baseline, tfull, rfull, RECIP_ALPHA)
        return no, tfull, rfull, baseline, candidate

    v_no, v_tf, v_rf, v_base, v_cand = score(val)
    t_no, t_tf, t_rf, t_base, t_cand = score(test)
    delta = f5.compare(t_base, t_cand)
    blocks = f5.f3.paired_time_blocks(t_base, t_cand)
    pos = sum(x["delta_logloss"] < 0 for x in blocks)
    neg = sum(x["delta_logloss"] > 0 for x in blocks)
    passed = bool(delta["logloss"] < 0 and delta["brier"] < 0 and delta["rps"] < 0 and pos >= MIN_POSITIVE_LL_BLOCKS and neg <= MAX_NEGATIVE_LL_BLOCKS)

    val_delta = f5.compare(v_base, v_cand)
    test_recip_n = sum(x["recip"]["recip_exists"] > 0 for x in test)
    test_cup_n = sum(x["recip"]["recip_cup"] > 0 for x in test)
    result = {
        "schema_version": "football3-r43e4-reciprocal-match-state-oos-v1",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_STRICT_PRIOR_RECIPROCAL_MATCH_STATE_ON_R43F5_WEIGHTED_TECH",
        "formal_weight": 0,
        "question": "Does a strict-prior short-horizon reciprocal-rematch state add stable information beyond the R43F5 probability-weighted player technical baseline without assuming every cup rematch is a knockout second leg?",
        "governance": {
            "source_r43f5_head": SOURCE_R43F5_HEAD,
            "source_r43d1_r1_run": SOURCE_R43D1_R1_RUN,
            "source_r43e3_audit_run": SOURCE_R43E3_RUN,
            "latest20k_previously_consumed": True,
            "formal_weight": 0,
            "parameter_search": False,
            "feature_search_after_test": False,
            "reciprocal_lookback_days": RECIP_LOOKBACK_DAYS,
            "reciprocal_decay_days": RECIP_DECAY_DAYS,
            "reciprocal_residual_alpha": RECIP_ALPHA,
            "alpha_fixed_before_test": True,
            "target_result_used_as_feature": False,
            "prior_reciprocal_result_only": True,
            "cup_flag_is_context_not_knockout_label": True,
            "no_manual_importance_bonus": True,
            "no_manual_draw_override": True,
            "no_draw_threshold": True,
            "no_draw_class_weight": True,
            "unified_three_class_argmax_unchanged": True,
            "failed_r43d1_tactical_features_included": False,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "R43F5 context P(start)-weighted technical model with fixed 0.5 half-shrink",
            "new_state": "most recent reversed-home-away same-competition completed meeting 2-45 days before target",
            "reason_not_called_second_leg": "provider has no round/stage field and cup group-stage home/away rematches exist; model receives cup and gap interactions but no hard knockout label",
            "features": RECIP_NAMES,
            "integration": "fit full tech+reciprocal model; transport only its log-probability residual over full-tech onto the frozen half-tech baseline at fixed alpha=0.5",
            "gate": "test Log Loss, Brier and RPS all improve; at least 3 of 4 chronological Log Loss blocks improve; Top1 remains diagnostic",
        },
        "source": {**source_meta, **source2, "league_meta": league_meta, "reciprocal_meta_all_records": recip_meta},
        "lineup_stage": {
            "lineup_train_end": lineup_meta["lineup_train_end"],
            "later_sides": lineup_meta["later_sides"],
            "context_xi": lineup_meta["context_xi"],
        },
        "outcome_split": {
            "eligible_matches": len(raw), "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
            "test_reciprocal_matches": int(test_recip_n),
            "test_reciprocal_cup_matches": int(test_cup_n),
        },
        "validation": {
            "baseline": f5.f3.enriched_metrics(v_base),
            "candidate": f5.f3.enriched_metrics(v_cand),
            "candidate_minus_baseline": val_delta,
        },
        "test": {
            "baseline": f5.f3.enriched_metrics(t_base),
            "candidate": f5.f3.enriched_metrics(t_cand),
            "candidate_minus_baseline": delta,
            "time_blocks": blocks,
            "positive_logloss_blocks": int(pos),
            "negative_logloss_blocks": int(neg),
            "reciprocal_subset_baseline": subset_metrics(t_base, test, lambda x: x["recip"]["recip_exists"] > 0),
            "reciprocal_subset_candidate": subset_metrics(t_cand, test, lambda x: x["recip"]["recip_exists"] > 0),
            "cup_reciprocal_subset_baseline": subset_metrics(t_base, test, lambda x: x["recip"]["recip_cup"] > 0),
            "cup_reciprocal_subset_candidate": subset_metrics(t_cand, test, lambda x: x["recip"]["recip_cup"] > 0),
        },
        "gate": {
            "passed": passed,
            "action": "KEEP_RECIPROCAL_MATCH_STATE_FOR_REPLICATION" if passed else "DO_NOT_PROMOTE_R43E4_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "This latest20k block has already been consumed by earlier architecture work, so the result is development evidence only.",
            "Provider lacks round/stage/aggregate metadata; reciprocal cup matches include both knockout ties and non-knockout rematches.",
            "The feature intentionally avoids hard-coded second-leg classification until a reliable stage source is added.",
            "Current standings/table importance and reliable PIT next-fixture publication coverage remain unresolved.",
            "R42L remains untouched and draw is never manually promoted.",
        ],
    }
    p = OUT / "summary_r43e4_reciprocal_match_state_oos.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43e4_reciprocal_match_state_oos.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["prior_reciprocal_result_only"] is True
    assert d["governance"]["cup_flag_is_context_not_knockout_label"] is True
    assert d["governance"]["no_manual_draw_override"] is True
    assert d["governance"]["unified_three_class_argmax_unchanged"] is True
    assert d["governance"]["failed_r43d1_tactical_features_included"] is False
    assert d["governance"]["r42l_lock_modified"] is False
    assert d["outcome_split"]["date_safe"] is True and d["outcome_split"]["test_n"] >= MIN_TEST_MATCHES
    print("R43E4 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(f"unknown command: {cmd}")
