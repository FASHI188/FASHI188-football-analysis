#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
F5_DIR = ROOT / "football-data" / "experiments" / "r43f5_probability_weighted_technical_mixture"
if str(F5_DIR) not in sys.path:
    sys.path.insert(0, str(F5_DIR))
import run_r43f5 as f5  # noqa: E402

r42h = f5.r42h
r40c = f5.r40c
r9 = f5.r9
f0 = f5.f0
BASE_NAMES = f5.BASE_NAMES
TECH_NAMES = f5.TECH_NAMES

SOURCE_R43F5_HEAD = "0140944ee387e4328c14eef0837481d800dfcc38"
SOURCE_R43D0_HEAD = "0babd36bf846f941054b956ee64f96a526bb6d2a"
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
EXPECTED_FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
BLOCK_SIZE = 20000
SELECTION = "valid_sorted_[-20000:]"
STYLE_LOOKBACK = 20
COACH_SHRINK_MATCHES = 6.0
OUTCOME_TRAIN_TARGET = 4000
OUTCOME_VAL_TARGET = 3000
MIN_TEST_MATCHES = 1000
MIN_POSITIVE_LL_BLOCKS = 3
MAX_NEGATIVE_LL_BLOCKS = 1

STYLE_KEYS = [
    "possession", "pass_accuracy", "shots_for", "shots_against",
    "sot_share_for", "inside_share_for", "corners_per_shot", "fouls",
]
TACT_NAMES = [
    "tact_possession_diff", "tact_pass_accuracy_diff", "tact_shots_for_diff",
    "tact_shots_against_diff", "tact_sot_share_diff", "tact_inside_share_diff",
    "tact_corners_per_shot_diff", "tact_fouls_diff",
    "tact_possession_abs_gap", "tact_shots_for_abs_gap",
    "tact_event_volume_mean", "tact_defensive_openness_mean",
    "tact_known_share_min", "tact_coach_known_both",
]


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43d1/1"})
    with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as w:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            w.write(b)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_latest20k() -> tuple[list[dict], dict[int, dict], dict]:
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    download(FIX_URL, fp); download(STAT_URL, sp)
    fsha, ssha = sha256(fp), sha256(sp)
    if fsha != EXPECTED_FIX_SHA:
        raise RuntimeError(f"fixtures source drift {fsha}")
    if ssha != EXPECTED_STAT_SHA:
        raise RuntimeError(f"match_stats source drift {ssha}")

    fx = pd.read_parquet(fp, columns=[
        "id", "date_utc", "league_id", "home_team_id", "away_team_id",
        "goals_home", "goals_away", "status_norm", "is_played",
    ])
    ms = pd.read_parquet(sp)
    need = ["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"]
    st = ms[need].copy()
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["kick"] = pd.to_datetime(df["date_utc"], utc=True)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > df["kick"]) & df["home_xg"].between(0, 6) & df["away_xg"].between(0, 6)]
    df["date"] = df["kick"].dt.date.astype(str)
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    if len(df) < BLOCK_SIZE:
        raise RuntimeError(f"need {BLOCK_SIZE} rows, got {len(df)}")
    sl = df.tail(BLOCK_SIZE).copy()

    rows = []
    for x in sl.itertuples(index=False):
        rows.append({
            "date": str(x.date), "game_id": str(int(x.id)), "competition_id": str(int(x.league_id)),
            "home_team": str(int(x.home_team_id)), "away_team": str(int(x.away_team_id)),
            "home_goals": int(x.goals_home), "away_goals": int(x.goals_away),
            "home_xg": float(x.home_xg), "away_xg": float(x.away_xg),
            "xg_known_at": pd.Timestamp(x.known).isoformat(),
        })

    ids = {int(x["game_id"]) for x in rows}
    tcols = [
        "fixture_id", "home_shots_total", "away_shots_total", "home_shots_on_goal", "away_shots_on_goal",
        "home_shots_inside_box", "away_shots_inside_box", "home_corners", "away_corners",
        "home_possession", "away_possession", "home_pass_accuracy", "away_pass_accuracy",
        "home_fouls", "away_fouls",
    ]
    z = ms[ms["fixture_id"].isin(ids)][tcols].drop_duplicates("fixture_id", keep="last")
    tactical = {int(x.fixture_id): x._asdict() for x in z.itertuples(index=False)}
    meta = {
        "full_valid_xg_rows": int(len(df)), "snapshot_rows": len(rows), "selection": SELECTION,
        "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
        "fixtures_sha256": fsha, "match_stats_sha256": ssha,
        "tactical_fixture_rows": len(tactical),
    }
    return rows, tactical, meta


def num(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def ratio(a, b):
    a, b = num(a), num(b)
    if not np.isfinite(a) or not np.isfinite(b) or b <= 0:
        return np.nan
    return float(a / b)


def match_style(ms: dict, side: str) -> np.ndarray:
    opp = "away" if side == "home" else "home"
    shots = num(ms.get(f"{side}_shots_total")); opp_shots = num(ms.get(f"{opp}_shots_total"))
    vals = [
        num(ms.get(f"{side}_possession")) / 100.0,
        num(ms.get(f"{side}_pass_accuracy")) / 100.0,
        shots / 15.0 if np.isfinite(shots) else np.nan,
        opp_shots / 15.0 if np.isfinite(opp_shots) else np.nan,
        ratio(ms.get(f"{side}_shots_on_goal"), shots),
        ratio(ms.get(f"{side}_shots_inside_box"), shots),
        ratio(ms.get(f"{side}_corners"), shots),
        num(ms.get(f"{side}_fouls")) / 15.0,
    ]
    return np.asarray(vals, dtype=float)


def history_stats(hist: deque) -> tuple[np.ndarray, np.ndarray]:
    if not hist:
        return np.zeros(len(STYLE_KEYS), dtype=float), np.zeros(len(STYLE_KEYS), dtype=float)
    a = np.asarray(list(hist), dtype=float)
    means, counts = [], []
    for j in range(a.shape[1]):
        g = a[:, j][np.isfinite(a[:, j])]
        means.append(float(np.mean(g)) if len(g) else 0.0)
        counts.append(float(len(g)))
    return np.asarray(means), np.asarray(counts)


def team_style(team_hist: dict, tid: int) -> tuple[np.ndarray, float]:
    m, c = history_stats(team_hist[tid])
    return m, float(np.mean(c > 0))


def coach_blended_style(team_hist: dict, coach_hist: dict, team_last_coach: dict, tid: int) -> tuple[np.ndarray, float, float]:
    tm, tc = history_stats(team_hist[tid])
    cid = team_last_coach.get(tid)
    if cid is None:
        return tm, float(np.mean(tc > 0)), 0.0
    cm, cc = history_stats(coach_hist[cid])
    out = np.zeros(len(STYLE_KEYS), dtype=float)
    known = np.zeros(len(STYLE_KEYS), dtype=bool)
    for j in range(len(STYLE_KEYS)):
        if cc[j] > 0 and tc[j] > 0:
            out[j] = (cc[j] * cm[j] + COACH_SHRINK_MATCHES * tm[j]) / (cc[j] + COACH_SHRINK_MATCHES)
            known[j] = True
        elif cc[j] > 0:
            out[j] = cm[j]; known[j] = True
        elif tc[j] > 0:
            out[j] = tm[j]; known[j] = True
        else:
            out[j] = 0.0
    return out, float(np.mean(known)), float(np.any(cc > 0))


def tactical_context(h: np.ndarray, a: np.ndarray, h_known: float, a_known: float, h_coach: float, a_coach: float) -> dict:
    d = h - a
    return {
        "tact_possession_diff": float(d[0]),
        "tact_pass_accuracy_diff": float(d[1]),
        "tact_shots_for_diff": float(d[2]),
        "tact_shots_against_diff": float(d[3]),
        "tact_sot_share_diff": float(d[4]),
        "tact_inside_share_diff": float(d[5]),
        "tact_corners_per_shot_diff": float(d[6]),
        "tact_fouls_diff": float(d[7]),
        "tact_possession_abs_gap": float(abs(d[0])),
        "tact_shots_for_abs_gap": float(abs(d[2])),
        "tact_event_volume_mean": float(0.5 * ((h[2] + h[3]) + (a[2] + a[3]))),
        "tact_defensive_openness_mean": float(0.5 * (h[3] + a[3])),
        "tact_known_share_min": float(min(h_known, a_known)),
        "tact_coach_known_both": float(h_coach > 0 and a_coach > 0),
    }


def build_records(rows, tactical_map, context_probs, lineup_train_end):
    player_map, player_sha, matched_starters, player_path = r40c.download_player_rows(rows)
    stats_path = r42h.download_stats()
    tech_rows, tech_source = r42h.load_technical_rows(rows, Path(player_path), stats_path)
    fixture_ids = {int(x["game_id"]) for x in rows}
    coach_map, coach_meta = f0.load_coach_map(fixture_ids)

    base_state = r9.S()
    states = defaultdict(r40c.TeamState)
    base_ledger = r40c.Ledger()
    tech_ledger = r42h.TechnicalLedger()
    team_hist = defaultdict(lambda: deque(maxlen=STYLE_LOOKBACK))
    coach_hist = defaultdict(lambda: deque(maxlen=STYLE_LOOKBACK))
    team_last_coach = {}
    records = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            fid = int(row["game_id"]); htid = int(row["home_team"]); atid = int(row["away_team"])
            raw = base_state.pred(row)
            base_cf = r40c.context_features(row, states, base_ledger)
            hk, ak = (fid, htid), (fid, atid)
            if day > lineup_train_end and hk in context_probs and ak in context_probs:
                cp_h, cp_a = context_probs[hk], context_probs[ak]
                pids = {pid for pid, _ in cp_h} | {pid for pid, _ in cp_a}
                snap = r42h.live_snap_for_pids(pids, tech_ledger, base_ledger.last_role)
                tech_cf = f5.weighted_technical_context(cp_h, cp_a, snap, snap)

                th, thk = team_style(team_hist, htid); ta, tak = team_style(team_hist, atid)
                team_tact = tactical_context(th, ta, thk, tak, 0.0, 0.0)
                ch, chk, chcoach = coach_blended_style(team_hist, coach_hist, team_last_coach, htid)
                ca, cak, cacoach = coach_blended_style(team_hist, coach_hist, team_last_coach, atid)
                coach_tact = tactical_context(ch, ca, chk, cak, chcoach, cacoach)

                records.append({
                    "date": day, "fixture_id": str(fid), "y": int(r9.actual(row)), "raw": raw,
                    "base_cf": base_cf,
                    "tech_cf": {**base_cf, **tech_cf},
                    "team_tact_cf": {**base_cf, **tech_cf, **team_tact},
                    "coach_tact_cf": {**base_cf, **tech_cf, **coach_tact},
                    "team_tact_known": float(team_tact["tact_known_share_min"]),
                    "coach_tact_known": float(coach_tact["tact_known_share_min"]),
                    "coach_known_both": float(coach_tact["tact_coach_known_both"]),
                })
            pending.append((row, raw))

        # Strict PIT: all same-date matches are predicted before result/stats/coach updates.
        for row, raw in pending:
            fid_s = str(row["game_id"]); fid = int(fid_s)
            htid_s, atid_s = str(row["home_team"]), str(row["away_team"])
            htid, atid = int(htid_s), int(atid_s)
            hi = player_map.get((fid_s, htid_s), []); ai = player_map.get((fid_s, atid_s), [])
            y = r9.actual(row); hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0; au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"]); ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                base_ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[htid_s].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                base_ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[atid_s].xis.append(frozenset(pid for pid, _ in ai))
            for rec in tech_rows.get((fid_s, htid_s), []): tech_ledger.update_row(rec)
            for rec in tech_rows.get((fid_s, atid_s), []): tech_ledger.update_row(rec)

            ms = tactical_map.get(fid)
            if ms is not None:
                for side, tid in (("home", htid), ("away", atid)):
                    vec = match_style(ms, side)
                    if int(np.isfinite(vec).sum()) >= 4:
                        team_hist[tid].append(vec)
                        cid = coach_map.get((fid, tid))
                        if cid is not None:
                            coach_hist[cid].append(vec)
                            team_last_coach[tid] = cid
            else:
                for tid in (htid, atid):
                    cid = coach_map.get((fid, tid))
                    if cid is not None:
                        team_last_coach[tid] = cid
            base_state.update(row, raw)

    return records, {
        "fixture_players_sha256": player_sha,
        "matched_starter_rows": matched_starters,
        "technical_source": tech_source,
        "coach_source": coach_meta,
    }


def block_counts(a, b):
    blocks = f5.f3.paired_time_blocks(a, b)
    pos = sum(x["delta_logloss"] < 0 for x in blocks)
    neg = sum(x["delta_logloss"] > 0 for x in blocks)
    return blocks, int(pos), int(neg)


def gate_delta(delta, pos, neg):
    return bool(
        delta["logloss"] < 0 and delta["brier"] < 0 and delta["rps"] < 0
        and pos >= MIN_POSITIVE_LL_BLOCKS and neg <= MAX_NEGATIVE_LL_BLOCKS
    )


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, tactical_map, source_meta = load_latest20k()
    _, context_probs, _, _, lineup_meta = f5.make_lineup_probabilities(rows)
    raw, source2 = build_records(rows, tactical_map, context_probs, lineup_meta["lineup_train_end"])
    raw = sorted(raw, key=lambda x: (x["date"], x["fixture_id"]))
    if len(raw) < OUTCOME_TRAIN_TARGET + OUTCOME_VAL_TARGET + MIN_TEST_MATCHES:
        raise RuntimeError(f"undersized outcome cohort {len(raw)}")
    b1 = f5.f3.boundary_date_safe(raw, OUTCOME_TRAIN_TARGET)
    b2 = f5.f3.boundary_date_safe(raw, b1 + OUTCOME_VAL_TARGET)
    train, val, test = raw[:b1], raw[b1:b2], raw[b2:]

    tech_names = BASE_NAMES + TECH_NAMES
    tact_names = tech_names + TACT_NAMES
    m0 = f5.fit_records(train, "base_cf", BASE_NAMES)
    mt = f5.fit_records(train, "tech_cf", tech_names)
    mteam = f5.fit_records(train, "team_tact_cf", tact_names)
    mcoach = f5.fit_records(train, "coach_tact_cf", tact_names)

    def score(split):
        no = f5.score_model(m0, split, "base_cf", BASE_NAMES)
        tfull = f5.score_model(mt, split, "tech_cf", tech_names)
        teamfull = f5.score_model(mteam, split, "team_tact_cf", tact_names)
        coachfull = f5.score_model(mcoach, split, "coach_tact_cf", tact_names)
        return {
            "no": no,
            "tech_half": f5.half_records(no, tfull),
            "team_tact_half": f5.half_records(no, teamfull),
            "coach_tact_half": f5.half_records(no, coachfull),
        }

    vs, ts = score(val), score(test)
    team_delta = f5.compare(ts["tech_half"], ts["team_tact_half"])
    coach_delta = f5.compare(ts["tech_half"], ts["coach_tact_half"])
    coach_vs_team = f5.compare(ts["team_tact_half"], ts["coach_tact_half"])
    team_blocks, team_pos, team_neg = block_counts(ts["tech_half"], ts["team_tact_half"])
    coach_blocks, coach_pos, coach_neg = block_counts(ts["tech_half"], ts["coach_tact_half"])
    cvt_blocks, cvt_pos, cvt_neg = block_counts(ts["team_tact_half"], ts["coach_tact_half"])
    team_pass = gate_delta(team_delta, team_pos, team_neg)
    coach_pass = gate_delta(coach_delta, coach_pos, coach_neg)
    coach_increment_pass = gate_delta(coach_vs_team, cvt_pos, cvt_neg)
    if coach_pass and coach_increment_pass:
        action = "KEEP_STRICT_PRIOR_COACH_TACTICAL_FINGERPRINT_FOR_REPLICATION"
    elif team_pass:
        action = "KEEP_TEAM_TACTICAL_STATE_DROP_COACH_INCREMENT_FOR_NOW"
    else:
        action = "DO_NOT_PROMOTE_R43D1_TACTICAL_LAYER"

    result = {
        "schema_version": "football3-r43d1-coach-tactical-fingerprint-oos-v1",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_STRICT_PRIOR_TEAM_AND_COACH_TACTICAL_FINGERPRINT_CHRONOLOGICAL_OOS",
        "formal_weight": 0,
        "question": "Do strict-prior rolling tactical-state features improve the frozen R43F5 weighted technical 1X2 layer, and does a shrunk coach fingerprint add value beyond team tactical history alone?",
        "governance": {
            "source_r43f5_head": SOURCE_R43F5_HEAD,
            "source_r43d0_head": SOURCE_R43D0_HEAD,
            "source_slice": SELECTION,
            "source_previously_consumed_by_r42h": True,
            "parameter_search": False,
            "feature_search": False,
            "style_lookback": STYLE_LOOKBACK,
            "coach_shrink_matches": COACH_SHRINK_MATCHES,
            "target_match_stats_used_as_feature": False,
            "target_match_coach_used_before_prediction": False,
            "same_date_updates_after_all_predictions": True,
            "target_confirmed_xi_used_as_feature": False,
            "odds_used": False,
            "draw_research_priority": True,
            "no_manual_draw_override": True,
            "no_draw_threshold": True,
            "no_draw_class_weight": True,
            "unified_three_class_argmax_unchanged": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "R43F5 context P(start)-weighted R42H technical translation with fixed alpha=0.5 half shrink",
            "team_tactical_state": "rolling prior team match style only",
            "coach_tactical_state": "last previously observed coach, rolling prior coach style shrunk toward rolling team style",
            "style_fields": STYLE_KEYS,
            "pair_features": TACT_NAMES,
            "strength_duplication_control": "no raw goals or xG are included in tactical fingerprint features",
            "draw_mechanism_features": ["tact_possession_abs_gap", "tact_shots_for_abs_gap", "tact_event_volume_mean", "tact_defensive_openness_mean"],
            "draw_policy": "research may prioritize low-event/symmetry mechanisms; final 1X2 is never manually altered",
            "gate_policy": "proper-score improvement plus >=3/4 positive logloss time blocks; Top1 is diagnostic rather than a hard survival requirement",
        },
        "source": {**source_meta, **source2},
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
        },
        "coverage_test": {
            "team_tactical_known_mean": float(np.mean([x["team_tact_known"] for x in test])),
            "coach_tactical_known_mean": float(np.mean([x["coach_tact_known"] for x in test])),
            "coach_known_both_rate": float(np.mean([x["coach_known_both"] for x in test])),
        },
        "validation": {k: f5.f3.enriched_metrics(v) for k, v in vs.items()},
        "test": {
            "baseline_tech_half": f5.f3.enriched_metrics(ts["tech_half"]),
            "team_tact_half": f5.f3.enriched_metrics(ts["team_tact_half"]),
            "coach_tact_half": f5.f3.enriched_metrics(ts["coach_tact_half"]),
            "team_minus_baseline": team_delta,
            "coach_minus_baseline": coach_delta,
            "coach_minus_team": coach_vs_team,
            "team_time_blocks": team_blocks,
            "coach_time_blocks": coach_blocks,
            "coach_vs_team_time_blocks": cvt_blocks,
            "team_positive_logloss_blocks": team_pos, "team_negative_logloss_blocks": team_neg,
            "coach_positive_logloss_blocks": coach_pos, "coach_negative_logloss_blocks": coach_neg,
            "coach_vs_team_positive_logloss_blocks": cvt_pos, "coach_vs_team_negative_logloss_blocks": cvt_neg,
        },
        "gate": {
            "team_tactical_passed": team_pass,
            "coach_vs_baseline_passed": coach_pass,
            "coach_increment_over_team_passed": coach_increment_pass,
            "passed": bool(coach_pass and coach_increment_pass),
            "action": action,
        },
        "limitations": [
            "The latest 20k block was previously used by R42H, so this is architecture-development evidence, not pristine confirmation.",
            "The provider does not expose PPDA, field tilt or event-sequence transitions here; the first fingerprint uses possession, passing, shot-location/share, corners and fouls proxies.",
            "Target-match coach identity is intentionally unavailable before prediction because fixture_lineups has no publication timestamp; manager changes are therefore recognized only after the first observed match.",
            "Current-match injury publication timestamps and next-match importance remain outside this stage.",
            "R42L remains untouched and no draw output is forced.",
        ],
    }
    p = OUT / "summary_r43d1_coach_tactical_fingerprint_oos.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "gate": result["gate"], "coverage": result["coverage_test"]}, indent=2))
    shutil.rmtree(DATA, ignore_errors=True)
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43d1_coach_tactical_fingerprint_oos.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["target_match_stats_used_as_feature"] is False
    assert d["governance"]["target_match_coach_used_before_prediction"] is False
    assert d["governance"]["same_date_updates_after_all_predictions"] is True
    assert d["governance"]["no_manual_draw_override"] is True
    assert d["governance"]["r42l_lock_modified"] is False
    assert d["outcome_split"]["date_safe"] is True and d["outcome_split"]["test_n"] >= MIN_TEST_MATCHES
    print("R43D1 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
