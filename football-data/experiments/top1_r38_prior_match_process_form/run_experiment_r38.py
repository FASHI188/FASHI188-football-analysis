#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R37_DIR = HERE.parent / "top1_r37_prior_h2h_matchup_context"
R9_DIR = HERE.parent / "top1_r9b_xg_hf"
sys.path.insert(0, str(R37_DIR))
import run_experiment_r37 as r37  # noqa: E402

r34 = r37.r34
r9 = r37.r9

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
HALF_LIFE_DAYS = 180.0
MAX_AGE_DAYS = HALF_LIFE_DAYS * 8.0

SHOT_METRICS = ["shots_net", "sot_net", "corners_net", "sot_rate_net"]
CONTROL_METRICS = ["possession_net", "pass_accuracy_net", "fouls_net", "yellow_net"]


def metric_feature_names(metrics, prefix):
    out = []
    for m in metrics:
        out += [f"home_{m}", f"away_{m}", f"diff_{m}"]
    out.append(f"log_{prefix}_weight_min")
    return out

SHOT_NAMES = metric_feature_names(SHOT_METRICS, "shot")
CONTROL_NAMES = metric_feature_names(CONTROL_METRICS, "control")
FEATURE_SETS = {
    "SHOT_CHANCE_VOLUME_FORM": SHOT_NAMES,
    "CONTROL_DISCIPLINE_FORM": CONTROL_NAMES,
    "MATCH_PROCESS_FORM_COMBINED": SHOT_NAMES + CONTROL_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001

STAT_COLS = [
    "fixture_id", "home_shots_total", "away_shots_total", "home_shots_on_goal", "away_shots_on_goal",
    "home_corners", "away_corners", "home_possession", "away_possession",
    "home_pass_accuracy", "away_pass_accuracy", "home_fouls", "away_fouls",
    "home_yellow_cards", "away_yellow_cards", "known_at",
]


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r38-prior-process"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def load_metadata(game_ids):
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / "_fixtures_r38.parquet"
    sp = OUT / "_match_stats_r38.parquet"
    download(FIX_URL, fp)
    download(STAT_URL, sp)
    manifest = json.loads((R9_DIR / "data" / "source_manifest_r9b.json").read_text(encoding="utf-8"))
    f_sha = fsha(fp)
    s_sha = fsha(sp)
    if f_sha != manifest["fixtures_sha256"]:
        raise RuntimeError(f"R38 fixture snapshot drift: expected {manifest['fixtures_sha256']}, got {f_sha}")
    if s_sha != manifest["match_stats_sha256"]:
        raise RuntimeError(f"R38 match_stats snapshot drift: expected {manifest['match_stats_sha256']}, got {s_sha}")
    wanted = set(game_ids)
    try:
        fx = pd.read_parquet(fp, columns=["id", "date_utc"])
        st = pd.read_parquet(sp, columns=STAT_COLS)
    finally:
        for p in (fp, sp):
            try:
                p.unlink()
            except Exception:
                pass
    fx = fx[fx["id"].notna() & fx["date_utc"].notna()].copy()
    fx["game_id"] = fx["id"].astype("int64").astype(str)
    fx = fx[fx["game_id"].isin(wanted)].drop_duplicates("game_id")
    st = st[st["fixture_id"].notna() & st["known_at"].notna()].copy()
    st["game_id"] = st["fixture_id"].astype("int64").astype(str)
    st = st[st["game_id"].isin(wanted)].drop_duplicates("game_id")
    df = fx[["game_id", "date_utc"]].merge(st.drop(columns=["fixture_id"]), on="game_id", how="left", validate="one_to_one")
    missing = df[df["known_at"].isna()]["game_id"].tolist()
    if missing:
        raise RuntimeError(f"R38 missing match_stats for {len(missing)} frozen rows; first={missing[:5]}")
    meta = {}
    for rec in df.itertuples(index=False):
        kickoff = pd.Timestamp(rec.date_utc)
        known = pd.Timestamp(rec.known_at)
        if not known > kickoff:
            raise RuntimeError(f"R38 non-postmatch known_at for game {rec.game_id}")
        meta[str(rec.game_id)] = {c: getattr(rec, c) for c in df.columns if c != "game_id"}
        meta[str(rec.game_id)]["date_utc"] = kickoff
        meta[str(rec.game_id)]["known_at"] = known
    return meta, {"fixtures_sha256": f_sha, "match_stats_sha256": s_sha}


def num(x):
    return None if pd.isna(x) else float(x)


def diff(a, b):
    a, b = num(a), num(b)
    return None if a is None or b is None else a - b


def rate_diff(on_a, total_a, on_b, total_b):
    oa, ta, ob, tb = num(on_a), num(total_a), num(on_b), num(total_b)
    if oa is None or ta is None or ob is None or tb is None or ta <= 0 or tb <= 0:
        return None
    return oa / ta - ob / tb


def perspective_record(row, m, home_side):
    if home_side:
        p = "home"; q = "away"
    else:
        p = "away"; q = "home"
    return {
        "known_at": m["known_at"],
        "shots_net": diff(m[f"{p}_shots_total"], m[f"{q}_shots_total"]),
        "sot_net": diff(m[f"{p}_shots_on_goal"], m[f"{q}_shots_on_goal"]),
        "corners_net": diff(m[f"{p}_corners"], m[f"{q}_corners"]),
        "sot_rate_net": rate_diff(m[f"{p}_shots_on_goal"], m[f"{p}_shots_total"], m[f"{q}_shots_on_goal"], m[f"{q}_shots_total"]),
        "possession_net": diff(m[f"{p}_possession"], m[f"{q}_possession"]),
        "pass_accuracy_net": diff(m[f"{p}_pass_accuracy"], m[f"{q}_pass_accuracy"]),
        "fouls_net": diff(m[f"{p}_fouls"], m[f"{q}_fouls"]),
        "yellow_net": diff(m[f"{p}_yellow_cards"], m[f"{q}_yellow_cards"]),
    }


class ProcessState:
    def __init__(self):
        self.hist = defaultdict(list)

    def _form(self, team, kickoff, metric):
        sw = sx = 0.0
        for rec in reversed(self.hist[str(team)]):
            if rec["known_at"] >= kickoff:
                continue
            age = (kickoff - rec["known_at"]).total_seconds() / 86400.0
            if age < 0:
                continue
            if age > MAX_AGE_DAYS:
                break
            v = rec[metric]
            if v is None:
                continue
            w = math.exp(-math.log(2.0) * age / HALF_LIFE_DAYS)
            sw += w
            sx += w * float(v)
        return (sx / sw if sw > 0 else 0.0), sw

    def features(self, row, kickoff):
        out = {}
        weights = {}
        for group, metrics in (("shot", SHOT_METRICS), ("control", CONTROL_METRICS)):
            ws = []
            for m in metrics:
                hv, hw = self._form(row["home_team"], kickoff, m)
                av, aw = self._form(row["away_team"], kickoff, m)
                out[f"home_{m}"] = float(hv)
                out[f"away_{m}"] = float(av)
                out[f"diff_{m}"] = float(hv - av)
                ws.append(min(hw, aw))
            weights[group] = min(ws) if ws else 0.0
            out[f"log_{group}_weight_min"] = math.log1p(weights[group])
        return out

    def update(self, row, m):
        self.hist[str(row["home_team"])].append(perspective_record(row, m, True))
        self.hist[str(row["away_team"])].append(perspective_record(row, m, False))


def metadata_coverage(meta):
    n = len(meta)
    cols = [
        "home_shots_total", "home_shots_on_goal", "home_corners", "home_possession",
        "home_pass_accuracy", "home_fouls", "home_yellow_cards",
    ]
    out = {"rows": n}
    for c in cols:
        out[c + "_coverage"] = sum(int(not pd.isna(m[c])) for m in meta.values()) / n
    return out


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    meta, source_hashes = load_metadata([r["game_id"] for r in rows])
    base = r9.S()
    state = ProcessState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            m = meta[row["game_id"]]
            kickoff = m["date_utc"]
            if kickoff.date().isoformat() != row["date"]:
                raise RuntimeError(f"R38 date mismatch game {row['game_id']}")
            raw = base.pred(row)
            cf = state.features(row, kickoff)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw, m))
        # Current-date post-match process data is not visible to any prediction on that date.
        for row, raw, m in pending:
            base.update(row, raw)
            state.update(row, m)
    return pred, source_hashes, metadata_coverage(meta)


def x_for(rec, names):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in names]


def fit_model(train, names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([x_for(r, names) for r in train], [r["y"] for r in train])
    return m


def decorate(model, rows, names):
    pr = model.predict_proba([x_for(r, names) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, row in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, row):
            v[int(cls)] = float(p)
        v = np.clip(v, 1e-12, None); v /= v.sum()
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def baseline_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    return m


def baseline_decorate(k1, rows):
    p = r34.r19.decorate_k1(k1, rows)
    return [{"date": r["date"], "y": r["y"], "P": q} for r, q in zip(rows, p)]


def metrics(rows):
    return r9.metrics([{"y": r["y"], "P": r["P"]} for r in rows], "P")


def date_blocks(rows, n=4):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), n)
    out = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist(): out[d] = i
    return out


def paired_blocks(base_rows, candidate_rows):
    block_map = date_blocks(base_rows, 4)
    blocks = {str(i): {"count": 0, "base_hits": 0, "candidate_hits": 0, "net": 0} for i in range(4)}
    gain = loss = 0
    for b, c in zip(base_rows, candidate_rows):
        if b["date"] != c["date"] or b["y"] != c["y"]: raise RuntimeError("R38 paired rows misaligned")
        y = b["y"]; cb = int(b["P"]["top1"] == y); cc = int(c["P"]["top1"] == y)
        gain += int(cc and not cb); loss += int(cb and not cc)
        z = blocks[str(block_map[b["date"]])]; z["count"] += 1; z["base_hits"] += cb; z["candidate_hits"] += cc
    for z in blocks.values(): z["net"] = z["candidate_hits"] - z["base_hits"]
    return {"challenger_gain": gain, "challenger_loss": loss, "net_hits": gain-loss,
            "positive_time_blocks": sum(int(z["net"] > 0) for z in blocks.values()),
            "negative_time_blocks": sum(int(z["net"] < 0) for z in blocks.values()), "time_blocks": blocks}


def run():
    pred, hashes, source_coverage = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN); b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN); b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]
    k1 = baseline_model(train); val_base = baseline_decorate(k1, val0); base_v = metrics(val_base)
    if base_v["hits"] != 2064: raise RuntimeError(f"R38 K1 validation reproduction gate failed: {base_v['hits']}")
    r37_summary = json.loads((R37_DIR / "results" / "summary_r37.json").read_text(encoding="utf-8"))
    if r37_summary["decision"]["eligible_for_next_fresh_confirmation"]: raise RuntimeError("R38 expects frozen R37 failure control")

    candidates = []; models = {}
    for name, names in FEATURE_SETS.items():
        model = fit_model(train, names); models[name] = model
        val = decorate(model, val0, names); mv = metrics(val); paired = paired_blocks(val_base, val)
        gain = mv["hits"] - base_v["hits"]; ll_delta = mv["logloss"] - base_v["logloss"]
        viable = gain >= MIN_VALIDATION_GAIN_HITS and paired["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS and paired["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS and ll_delta <= MAX_VALIDATION_LOGLOSS_WORSEN
        candidates.append({"name": name, "features": names, "viable": viable, "validation": mv, "gain_hits": gain,
                           "gain_top1_pp": 100.0*(mv["top1_accuracy"]-base_v["top1_accuracy"]), "logloss_delta": ll_delta,
                           "brier_delta": mv["brier"]-base_v["brier"], "rps_delta": mv["rps"]-base_v["rps"], "paired": paired})
    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(viable, key=lambda x:(x["gain_hits"], x["paired"]["positive_time_blocks"], -x["paired"]["negative_time_blocks"], -x["logloss_delta"]))
        name = selected["name"]; test_base = baseline_decorate(k1, test0); base_t = metrics(test_base)
        if base_t["hits"] != 1877: raise RuntimeError(f"R38 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[name], test0, FEATURE_SETS[name]); mt = metrics(test); paired_t = paired_blocks(test_base, test)
        tg = mt["hits"] - base_t["hits"]; tll = mt["logloss"] - base_t["logloss"]
        historical_test = {"baseline": base_t, "candidate": mt, "gain_hits": tg, "gain_top1_pp":100.0*(mt["top1_accuracy"]-base_t["top1_accuracy"]),
                           "logloss_delta":tll, "brier_delta":mt["brier"]-base_t["brier"], "rps_delta":mt["rps"]-base_t["rps"], "paired":paired_t}
        confirmed = tg >= MIN_TEST_GAIN_HITS and paired_t["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS and paired_t["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS and tll <= MAX_TEST_LOGLOSS_WORSEN
        stop_reason = None if confirmed else "FROZEN_PRIOR_MATCH_PROCESS_FORM_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None; historical_test = None; confirmed = False; stop_reason = "NO_VALIDATION_ROBUST_PRIOR_MATCH_PROCESS_FORM_GAIN"

    summary = {
        "schema_version":"football3-top1-r38-prior-match-process-form", "status":"COMPLETE",
        "classification":"DEVELOPMENT_INDEPENDENT_STRICT_PRIOR_MATCH_PROCESS_INFORMATION_FAMILY", "formal_weight":0,
        "governance":{"base_r37_commit":"b8acea8179833f3c5b5b34ee4bd664df37dd6f53", "snapshot_rows":20000,
            "strict_prior_features":True, "exact_known_at_guard_for_prior_process_stats":True, "same_date_results_xg_and_process_stats_withheld":True,
            "current_match_process_stats_feature_visible":False, "odds_used":False, "market_prices_used":False, "lineup_used":False,
            "fixed_half_life_days":HALF_LIFE_DAYS, "half_life_search_used":False, "model_hyperparameter_search_used":False,
            "candidate_selected_on_validation_only":True, "test_evaluated_only_after_viable_validation_freeze":True, "test_used_for_candidate_selection":False,
            "batch005_labels_used":False, "formal_promotion_allowed_from_this_run":False},
        "question":"Do strictly prior, timestamp-audited match-process forms (shots/SOT/corners and control/discipline) add stable 1X2 Top1 information beyond K1 xG/goal strength?",
        "source":{"fixtures_sha256":hashes["fixtures_sha256"], "match_stats_sha256":hashes["match_stats_sha256"], "frozen_20k_match_stats_coverage":source_coverage},
        "prematch_information_family":{"family":"PRIOR_MATCH_PROCESS_FORM_BEYOND_XG", "causal_contract":"Only prior match process rows with known_at strictly before current kickoff are aggregated; all current-date stats remain invisible until predictions are complete.", "candidate_feature_sets":FEATURE_SETS},
        "selection_contract":{"min_validation_gain_hits":MIN_VALIDATION_GAIN_HITS,"min_positive_validation_blocks":MIN_POSITIVE_VALIDATION_BLOCKS,"max_negative_validation_blocks":MAX_NEGATIVE_VALIDATION_BLOCKS,"max_validation_logloss_worsen":MAX_VALIDATION_LOGLOSS_WORSEN,"min_test_gain_hits":MIN_TEST_GAIN_HITS,"min_positive_test_blocks":MIN_POSITIVE_TEST_BLOCKS,"max_negative_test_blocks":MAX_NEGATIVE_TEST_BLOCKS,"max_test_logloss_worsen":MAX_TEST_LOGLOSS_WORSEN},
        "controls":{"K1_validation":base_v,"R37_stop_reason":r37_summary["decision"]["stop_reason"]},
        "validation_candidates":candidates,"selected_feature_set":selected,"historical_test_confirmation":historical_test,
        "decision":{"eligible_for_next_fresh_confirmation":confirmed,"action":"LOCK_FRESH_CONFIRMATION_FOR_FROZEN_R38" if confirmed else "DO_NOT_PROMOTE_R38","stop_reason":stop_reason},
        "next_if_fail":"Continue another independent auditable prematch information family; do not tune draw thresholds on held-out labels."}
    OUT.mkdir(parents=True, exist_ok=True); (OUT/"summary_r38.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False))


def verify():
    s=json.loads((OUT/"summary_r38.json").read_text(encoding="utf-8")); g=s["governance"]
    assert s["status"]=="COMPLETE" and g["strict_prior_features"] and g["exact_known_at_guard_for_prior_process_stats"]
    assert g["same_date_results_xg_and_process_stats_withheld"] and not g["current_match_process_stats_feature_visible"]
    assert not g["odds_used"] and not g["market_prices_used"] and g["fixed_half_life_days"]==HALF_LIFE_DAYS
    assert not g["half_life_search_used"] and not g["model_hyperparameter_search_used"] and g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch005_labels_used"] and not g["formal_promotion_allowed_from_this_run"] and s["controls"]["K1_validation"]["hits"]==2064
    assert len(s["validation_candidates"])==len(FEATURE_SETS)
    if s["decision"]["eligible_for_next_fresh_confirmation"]: assert s["selected_feature_set"] is not None and s["historical_test_confirmation"] is not None
    print("R38_VERIFY_PASS")


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in {"run","verify"}: raise SystemExit("usage: run_experiment_r38.py {run|verify}")
    {"run":run,"verify":verify}[sys.argv[1]]()

if __name__=="__main__": main()
