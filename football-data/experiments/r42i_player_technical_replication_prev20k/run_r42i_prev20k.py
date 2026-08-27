#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42H_DIR = HERE.parent / "r42h_player_technical_translation"
sys.path.insert(0, str(R42H_DIR))
import run_r42h_player_technical_translation as r42h  # noqa: E402

r42g = r42h.r42g
r40c = r42h.r40c
r9 = r42h.r9
r33 = r42h.r33
BASE_NAMES = r42h.BASE_NAMES
TECH_NAMES = r42h.TECH_NAMES

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
EXPECTED_FIXTURES_SHA256 = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_MATCH_STATS_SHA256 = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
SNAPSHOT_N = 20000
SKIP_NEWEST_N = 20000
# Frozen before reading replication labels: exact R42H V1 gate, no changes.
MIN_OOS_MATCHES = r42h.MIN_OOS_MATCHES
MIN_GAIN_HITS = r42h.MIN_GAIN_HITS
MIN_POSITIVE_BLOCKS = r42h.MIN_POSITIVE_BLOCKS
MAX_NEGATIVE_BLOCKS = r42h.MAX_NEGATIVE_BLOCKS
MAX_PROPER_SCORE_DELTA = r42h.MAX_PROPER_SCORE_DELTA


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, path: Path, user_agent: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def freeze_previous_20k():
    data = HERE / "data"
    data.mkdir(parents=True, exist_ok=True)
    fp = data / "fixtures.parquet"
    sp = data / "match_stats.parquet"
    download(FIX_URL, fp, "football3-r42i")
    download(STAT_URL, sp, "football3-r42i")
    fsha_v, ssha_v = fsha(fp), fsha(sp)
    if fsha_v != EXPECTED_FIXTURES_SHA256:
        raise RuntimeError(f"fixtures source drift: {fsha_v}")
    if ssha_v != EXPECTED_MATCH_STATS_SHA256:
        raise RuntimeError(f"match_stats source drift: {ssha_v}")

    fx = pd.read_parquet(fp, columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"])
    st = pd.read_parquet(sp, columns=["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"])
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["date"] = pd.to_datetime(df["date_utc"], utc=True).dt.date.astype(str)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > pd.to_datetime(df["date_utc"], utc=True)) & (df["home_xg"].between(0, 6)) & (df["away_xg"].between(0, 6))]
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    need = SNAPSHOT_N + SKIP_NEWEST_N
    if len(df) < need:
        raise RuntimeError(f"only {len(df)} valid rows; need {need}")
    full_valid_n = int(len(df))
    selected = df.iloc[-need:-SKIP_NEWEST_N].copy()
    assert len(selected) == SNAPSHOT_N
    newest_ids = set(int(x) for x in df.tail(SKIP_NEWEST_N)["id"].tolist())
    selected_ids = set(int(x) for x in selected["id"].tolist())
    if newest_ids & selected_ids:
        raise RuntimeError("replication snapshot overlaps latest R9b 20k")

    rows = []
    for x in selected.itertuples(index=False):
        rows.append({
            "date": x.date,
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
    fp.unlink(); sp.unlink()
    return rows, {
        "full_valid_xg_rows": full_valid_n,
        "snapshot_rows": len(rows),
        "selection": "20,000 valid FT xG rows immediately preceding the latest R9b 20,000; exact sorted slice [-40000:-20000]",
        "overlap_with_latest_r9b_20k": 0,
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "fixtures_sha256": fsha_v,
        "match_stats_sha256": ssha_v,
    }


def model_prob(model, raw, cf, names):
    return r42h.model_prob(model, raw, cf, names)


def run():
    rows, snapshot_meta = freeze_previous_20k()
    player_map, player_sha, matched_starters, player_path = r40c.download_player_rows(rows)
    target_plans, opener_requests, detected_openers = r42g.build_plan_specs(rows, player_map)
    stats_path = r42h.download_stats()
    tech_rows, tech_source = r42h.load_technical_rows(rows, Path(player_path), stats_path)

    base = r9.S()
    states = defaultdict(r40c.TeamState)
    base_ledger = r40c.Ledger()
    tech_ledger = r42h.TechnicalLedger()
    pred = []
    frozen_base = {}
    frozen_tech = {}
    raw_targets = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending_updates = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            fid = str(row["game_id"])
            raw = base.pred(row)
            base_cf = r40c.context_features(row, states, base_ledger)
            tech_cf = r42h.live_technical_context(row, states, tech_ledger, base_ledger)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": {**base_cf, **tech_cf}})

            for side in ("home", "away"):
                tid = str(row[f"{side}_team"])
                req = opener_requests.get((fid, tid))
                if req is not None:
                    frozen_base[(req["target_fixture_id"], tid)] = r42g.snapshot_values(req, base_ledger)
                    frozen_tech[(req["target_fixture_id"], tid)] = r42h.freeze_tech_snapshot(req, tech_ledger)

            htid, atid = str(row["home_team"]), str(row["away_team"])
            hp = target_plans.get((fid, htid)); ap = target_plans.get((fid, atid))
            hbs = frozen_base.get((fid, htid)); abs_ = frozen_base.get((fid, atid))
            hts = frozen_tech.get((fid, htid)); ats = frozen_tech.get((fid, atid))
            if hp is not None and ap is not None and hbs is not None and abs_ is not None and hts is not None and ats is not None:
                bridge_cf = r42g.context_from_pids(hp["opener_xi"], ap["opener_xi"], hbs, abs_)
                bridge_tech_cf = r42h.technical_context(hp["opener_xi"], ap["opener_xi"], hts, ats)
                raw_targets.append({
                    "date": day, "fixture_id": fid, "y": r9.actual(row), "raw": raw,
                    "bridge_cf": bridge_cf, "bridge_tech_cf": bridge_tech_cf,
                    "home_roster_shock": int(hp["roster_shock"]), "away_roster_shock": int(ap["roster_shock"]),
                    "mean_roster_shock": 0.5 * (hp["roster_shock"] + ap["roster_shock"]),
                    "max_roster_shock": max(hp["roster_shock"], ap["roster_shock"]),
                })
            pending_updates.append((row, raw))

        for row, raw in pending_updates:
            fid = str(row["game_id"]); htid, atid = str(row["home_team"]), str(row["away_team"])
            hi = player_map.get((fid, htid), []); ai = player_map.get((fid, atid), [])
            y = r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0; au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"]); ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                base_ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[htid].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                base_ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[atid].xis.append(frozenset(pid for pid, _ in ai))
            for rec in tech_rows.get((fid, htid), []): tech_ledger.update_row(rec)
            for rec in tech_rows.get((fid, atid), []): tech_ledger.update_row(rec)
            base.update(row, raw)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    train = pred[b1:b2]
    train_end_date = max(x["date"] for x in train)
    baseline_model = r40c.fit_model(train, BASE_NAMES)
    candidate_model = r40c.fit_model(train, BASE_NAMES + TECH_NAMES)

    records = []
    for x in raw_targets:
        if x["date"] <= train_end_date:
            continue
        baseline_p = model_prob(baseline_model, x["raw"], x["bridge_cf"], BASE_NAMES)
        candidate_cf = {**x["bridge_cf"], **x["bridge_tech_cf"]}
        candidate_p = model_prob(candidate_model, x["raw"], candidate_cf, BASE_NAMES + TECH_NAMES)
        records.append({
            "date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"],
            "mean_roster_shock": x["mean_roster_shock"], "max_roster_shock": x["max_roster_shock"],
            "tech_known_share_min": x["bridge_tech_cf"]["tech_known_share_min"],
            "baseline_bridge": {"date": x["date"], "y": x["y"], "P": baseline_p},
            "candidate_bridge": {"date": x["date"], "y": x["y"], "P": candidate_p},
        })

    if not records:
        raise RuntimeError("R42I produced no post-training bridge target matches")
    baseline = [x["baseline_bridge"] for x in records]
    candidate = [x["candidate_bridge"] for x in records]
    bm = r33.metrics(baseline); cm = r33.metrics(candidate); pair = r33.paired_blocks(baseline, candidate)
    delta = {
        "gain_hits": int(cm["hits"] - bm["hits"]),
        "gain_top1_pp": 100.0 * float(cm["top1_accuracy"] - bm["top1_accuracy"]),
        "logloss_delta": float(cm["logloss"] - bm["logloss"]),
        "brier_delta": float(cm["brier"] - bm["brier"]),
        "rps_delta": float(cm["rps"] - bm["rps"]),
    }
    passed = bool(
        len(records) >= MIN_OOS_MATCHES
        and delta["gain_hits"] >= MIN_GAIN_HITS
        and pair["positive_time_blocks"] >= MIN_POSITIVE_BLOCKS
        and pair["negative_time_blocks"] <= MAX_NEGATIVE_BLOCKS
        and delta["logloss_delta"] <= MAX_PROPER_SCORE_DELTA
        and delta["brier_delta"] <= MAX_PROPER_SCORE_DELTA
        and delta["rps_delta"] <= MAX_PROPER_SCORE_DELTA
    )
    gate = {
        "contract": "exact R42H V1 gate frozen before replication labels",
        "min_oos_matches": MIN_OOS_MATCHES, "min_gain_hits": MIN_GAIN_HITS,
        "min_positive_blocks": MIN_POSITIVE_BLOCKS, "max_negative_blocks": MAX_NEGATIVE_BLOCKS,
        "max_logloss_delta": MAX_PROPER_SCORE_DELTA, "max_brier_delta": MAX_PROPER_SCORE_DELTA, "max_rps_delta": MAX_PROPER_SCORE_DELTA,
        "passed": passed,
        "action": "R42H_V1_SURVIVES_RETROSPECTIVE_EXTERNAL_REPLICATION_NEEDS_FRESH_FORWARD_CONFIRMATION" if passed else "R42H_V1_FAILS_RETROSPECTIVE_EXTERNAL_REPLICATION",
    }
    known = np.asarray([x["tech_known_share_min"] for x in records], dtype=float)
    boot = r42h.paired_bootstrap(records)
    result = {
        "schema_version": "football3-r42i-player-technical-replication-prev20k-v1",
        "status": "COMPLETE",
        "classification": "RETROSPECTIVE_EXTERNAL_REPLICATION_PRECEDING_20K_FIXED_R42H_V1",
        "formal_weight": 0,
        "question": "Does the frozen R42H V1 technical player translation replicate without tuning on the non-overlapping 20k valid xG rows immediately preceding the R9b development snapshot?",
        "governance": {
            "r42h_v1_spec_reused_without_change": True,
            "r42f_bridge_membership_unchanged": True,
            "parameter_search": False,
            "replication_slice_frozen_before_label_inspection": True,
            "replication_slice": "sorted valid FT xG rows [-40000:-20000]",
            "overlap_with_r42h_latest20k": 0,
            "technical_values_frozen_pre_opener": True,
            "target_confirmed_xi_used": False,
            "target_result_used_only_for_scoring": True,
            "automatic_promotion": False,
        },
        "source": {
            **snapshot_meta,
            "fixture_players_sha256": player_sha,
            "fixture_players_stats_flat_sha256": r42h.EXPECTED_STATS_SHA256,
            "matched_starter_rows": matched_starters,
            **tech_source,
            "model_train_rows": len(train), "model_train_end_date": train_end_date,
            "detected_gap_openers": detected_openers, "two_sided_candidate_matches_before_oos_cut": len(raw_targets),
        },
        "technical_translation": {
            "rate_shrink_minutes": r42h.RATE_SHRINK_MINUTES,
            "feature_names": TECH_NAMES,
            "oos_tech_known_share_min_mean": float(np.nanmean(known)),
            "oos_tech_known_share_min_ge_0_8": int(np.sum(known >= 0.8)),
        },
        "main_oos": {"n": len(records), "baseline_bridge": bm, "candidate_bridge_plus_technical": cm, **delta, "paired": pair},
        "paired_bootstrap": boot,
        "gate": gate,
        "limitations": [
            "This is a non-overlapping retrospective replication slice, not future prospective evidence.",
            "The R42H V1 design and gate were frozen after observing the later 66-match mechanism audit, but before this replication slice was opened or scored.",
            "Player technical rows are updated only after their historical match date; provider collection timestamps are not independently bound per row.",
            "R40C coarse xG/process baseline is unchanged.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42i_player_technical_replication_prev20k.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (OUT / "oos_records_r42i.jsonl").open("w", encoding="utf-8") as f:
        for x in records: f.write(json.dumps(x, ensure_ascii=False) + "\n")
    try: Path(player_path).unlink()
    except Exception: pass
    try: stats_path.unlink()
    except Exception: pass
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42i_player_technical_replication_prev20k.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["r42h_v1_spec_reused_without_change"] is True
    assert g["r42f_bridge_membership_unchanged"] is True
    assert g["parameter_search"] is False
    assert g["overlap_with_r42h_latest20k"] == 0
    assert d["source"]["snapshot_rows"] == 20000
    assert d["source"]["overlap_with_latest_r9b_20k"] == 0
    assert d["main_oos"]["n"] > 0
    print("R42I_PLAYER_TECHNICAL_REPLICATION_PREV20K_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42i_prev20k.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
