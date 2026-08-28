#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]

R42H_DIR = ROOT / "football-data" / "experiments" / "r42h_player_technical_translation"
R42I_DIR = ROOT / "football-data" / "experiments" / "r42i_player_technical_replication_prev20k"
R43F0_DIR = ROOT / "football-data" / "experiments" / "r43f0_coach_rotation_depth_lineup_older20k"
R43B0R1_DIR = ROOT / "football-data" / "experiments" / "r43b0r1_probabilistic_lineup_eligible_split"
R43F3_DIR = ROOT / "football-data" / "experiments" / "r43f3_context_lineup_technical_translation"
for p in (R42H_DIR, R42I_DIR, R43F0_DIR, R43B0R1_DIR, R43F3_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run_r42h_player_technical_translation as r42h  # noqa: E402
import run_r42i_prev20k as r42i  # noqa: E402
import run_r43f0 as f0  # noqa: E402
import run_r43b0r1 as r1  # noqa: E402
import run_r43f3 as f3  # noqa: E402

r40c = r42h.r40c
r9 = r42h.r9
r33 = r42h.r33
b0 = f0.b0
BASE_NAMES = r42h.BASE_NAMES
TECH_NAMES = r42h.TECH_NAMES
RATE_FIELDS = r42h.RATE_FIELDS

SOURCE_R43F4_HEAD = "c6be5ae7c65aa628ecd38705f71cba6e4d44e393"
SOURCE_R43F0_HEAD = "1581e8dfca000fc66d63313a5990bf7a803a8b77"
SOURCE_R42H_SHA256 = "b7bf4bd5fbfd61936e8de52fa09e5b81f748389cb9e0d2de6f33641c1d394a45"
SOURCE_R42I_SHA256 = "17155fe709877801299b1b8f2d965cb11da4cb81c5d2fc5733cf7536bbe2faad"

# Architectural development block. It is disjoint from R43F3/R43F4 outcome blocks,
# but it was previously consumed by R42I/R43E1, so formal_weight remains zero.
SKIP_NEWEST_N = 20000
BLOCK_SIZE = 20000
SELECTION = "valid_sorted_[-40000:-20000]"
TECH_ALPHA = 0.50
OUTCOME_TRAIN_TARGET = 4000
OUTCOME_VAL_TARGET = 3000
MIN_TEST_MATCHES = 1000
MIN_POSITIVE_LL_BLOCKS = 2
MAX_NEGATIVE_LL_BLOCKS = 1


def weighted_mean(items: list[tuple[float, float]]) -> float:
    good = [(float(w), float(v)) for w, v in items if w > 0 and np.isfinite(v)]
    den = sum(w for w, _ in good)
    if den <= 0:
        return 0.0
    return float(sum(w * v for w, v in good) / den)


def weighted_side_technical(prob_pairs: list[tuple[int, float]], snap: dict) -> dict:
    pairs = [(int(pid), float(p)) for pid, p in prob_pairs if float(p) > 0]
    total_p = sum(p for _, p in pairs)
    if not pairs or total_p <= 0:
        return {
            "attack_sot": 0.0, "attack_shots": 0.0, "creation_key": 0.0,
            "progression_dribble": 0.0, "control_pass": 0.0, "defense_actions": 0.0,
            "duels_won": 0.0, "dribbled_past": 0.0, "fouls_drawn": 0.0,
            "fouls_committed": 0.0, "known_share": 0.0, "exp_log": 0.0,
        }

    vals = []
    for pid, p in pairs:
        v = snap.get(pid, {"role": None, "minutes": 0.0, "rates": {k: np.nan for k in RATE_FIELDS}})
        vals.append((p, v))

    def pool(key: str, roles: set[str]) -> float:
        return weighted_mean([(p, v["rates"].get(key, np.nan)) for p, v in vals if v.get("role") in roles])

    def defense_actions(v: dict) -> float:
        rs = v["rates"]
        xs = [rs.get("tackles_total", np.nan), rs.get("tackles_interceptions", np.nan), rs.get("tackles_blocks", np.nan)]
        good = [float(x) for x in xs if np.isfinite(x)]
        return float(sum(good)) if good else np.nan

    known_weight = sum(p for p, v in vals if float(v.get("minutes", 0.0)) > 0)
    exp = weighted_mean([
        (p, math.log1p(float(v.get("minutes", 0.0)) / 90.0))
        for p, v in vals if float(v.get("minutes", 0.0)) > 0
    ])
    return {
        "attack_sot": pool("shots_on", {"F", "M"}),
        "attack_shots": pool("shots_total", {"F", "M"}),
        "creation_key": pool("passes_key", {"F", "M"}),
        "progression_dribble": pool("dribbles_success", {"F", "M"}),
        "control_pass": pool("passes_total", {"M", "D"}),
        "defense_actions": weighted_mean([(p, defense_actions(v)) for p, v in vals if v.get("role") in {"D", "M"}]),
        "duels_won": pool("duels_won", {"D", "M"}),
        "dribbled_past": pool("dribbles_past", {"D"}),
        "fouls_drawn": pool("fouls_drawn", {"F", "M"}),
        "fouls_committed": pool("fouls_committed", {"D", "M"}),
        "known_share": float(known_weight / total_p),
        "exp_log": float(exp),
    }


def weighted_technical_context(home_pairs, away_pairs, home_snap, away_snap) -> dict:
    h = weighted_side_technical(home_pairs, home_snap)
    a = weighted_side_technical(away_pairs, away_snap)
    def d(key):
        return float(h[key]) - float(a[key])
    return {
        "tech_attack_sot_diff": d("attack_sot"),
        "tech_attack_shots_diff": d("attack_shots"),
        "tech_creation_key_diff": d("creation_key"),
        "tech_progression_dribble_diff": d("progression_dribble"),
        "tech_control_pass_diff": d("control_pass"),
        "tech_defense_actions_diff": d("defense_actions"),
        "tech_duels_won_diff": d("duels_won"),
        "tech_dribbled_past_diff": d("dribbled_past"),
        "tech_fouls_drawn_diff": d("fouls_drawn"),
        "tech_fouls_committed_diff": d("fouls_committed"),
        "tech_known_share_min": min(h["known_share"], a["known_share"]),
        "tech_exp_log_diff": h["exp_log"] - a["exp_log"],
    }


def probability_map(sides: list[dict], examples: list[dict], prob_key: str) -> dict[tuple[int, int], list[tuple[int, float]]]:
    out = {}
    for s in sides:
        xs = examples[s["example_start"]:s["example_end"]]
        if not xs or any(prob_key not in x for x in xs):
            continue
        pairs = [(int(x["player_id"]), float(x[prob_key])) for x in xs]
        total = sum(p for _, p in pairs)
        if abs(total - 11.0) > 1e-6:
            raise RuntimeError(f"P(start) projection drift fixture={s['fixture_id']} team={s['team_id']} sum={total}")
        out[(int(s["fixture_id"]), int(s["team_id"]))] = pairs
    return out


def top11_from_prob_map(m: dict) -> dict:
    out = {}
    for key, pairs in m.items():
        ranked = sorted(pairs, key=lambda z: (z[1], z[0]), reverse=True)
        out[key] = [pid for pid, _ in ranked[:11]]
    return out


def entropy_diag(m: dict) -> dict:
    vals = []
    eff = []
    for pairs in m.values():
        ps = np.asarray([max(0.0, p / 11.0) for _, p in pairs], dtype=float)
        ps = ps[ps > 0]
        h = float(-np.sum(ps * np.log(ps))) if len(ps) else 0.0
        vals.append(h)
        eff.append(float(np.exp(h)))
    return {
        "n_sides": len(vals),
        "mean_normalized_entropy": float(np.mean(vals)) if vals else None,
        "mean_effective_candidates": float(np.mean(eff)) if eff else None,
    }


def make_lineup_probabilities(rows: list[dict]):
    lineup_rows = [
        {"date": r["date"], "fixture_id": int(r["game_id"]), "home_team": int(r["home_team"]), "away_team": int(r["away_team"])}
        for r in rows
    ]
    fixture_ids = {r["fixture_id"] for r in lineup_rows}
    player_map, player_meta = b0.prepare_player_rows(fixture_ids)
    coach_map, coach_meta = f0.load_coach_map(fixture_ids)
    examples, sides = f0.build_examples(lineup_rows, player_map, coach_map)
    split = r1.assign_eligible_side_phases(sides, examples)
    train = [x for x in examples if x["phase"] == "train"]
    if not train:
        raise RuntimeError("no lineup train examples")
    base_model = f0.fit_model(train, "base_features")
    context_model = f0.fit_model(train, "context_features")
    train_end = split["dates"]["train"]["last"]
    later_sides = [s for s in sides if s["date"] > train_end]
    f0.score_sides(base_model, later_sides, examples, "base_features", "base_p")
    f0.score_sides(context_model, later_sides, examples, "context_features", "context_p")
    base_probs = probability_map(later_sides, examples, "base_p")
    context_probs = probability_map(later_sides, examples, "context_p")
    base_top11 = top11_from_prob_map(base_probs)
    context_top11 = top11_from_prob_map(context_probs)
    eligible_sides = [s for s in later_sides if (int(s["fixture_id"]), int(s["team_id"])) in base_probs and (int(s["fixture_id"]), int(s["team_id"])) in context_probs]
    meta = {
        "split": split,
        "lineup_train_end": train_end,
        "later_sides": len(eligible_sides),
        "base_xi": f0.side_metrics(eligible_sides, examples, "base_p"),
        "context_xi": f0.side_metrics(eligible_sides, examples, "context_p"),
        "base_probability_entropy": entropy_diag(base_probs),
        "context_probability_entropy": entropy_diag(context_probs),
        "player_source": player_meta,
        "coach_source": coach_meta,
    }
    meta["delta_mean_xi_overlap"] = float(meta["context_xi"]["mean_xi_overlap"] - meta["base_xi"]["mean_xi_overlap"])
    return base_probs, context_probs, base_top11, context_top11, meta


def build_outcome_records(rows, base_probs, context_probs, base_top11, context_top11, lineup_train_end):
    player_map, player_sha, matched_starters, player_path = r40c.download_player_rows(rows)
    stats_path = r42h.download_stats()
    tech_rows, tech_source = r42h.load_technical_rows(rows, Path(player_path), stats_path)
    base_state = r9.S()
    states = defaultdict(r40c.TeamState)
    base_ledger = r40c.Ledger()
    tech_ledger = r42h.TechnicalLedger()
    records = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            fid = int(row["game_id"])
            raw = base_state.pred(row)
            base_cf = r40c.context_features(row, states, base_ledger)
            hk = (fid, int(row["home_team"])); ak = (fid, int(row["away_team"]))
            if day > lineup_train_end and all(k in base_probs and k in context_probs for k in (hk, ak)):
                roles = base_ledger.last_role
                bp_h, bp_a = base_probs[hk], base_probs[ak]
                cp_h, cp_a = context_probs[hk], context_probs[ak]
                base_pids = {pid for pid, _ in bp_h} | {pid for pid, _ in bp_a}
                context_pids = {pid for pid, _ in cp_h} | {pid for pid, _ in cp_a}
                base_snap = r42h.live_snap_for_pids(base_pids, tech_ledger, roles)
                context_snap = r42h.live_snap_for_pids(context_pids, tech_ledger, roles)
                bw = weighted_technical_context(bp_h, bp_a, base_snap, base_snap)
                cw = weighted_technical_context(cp_h, cp_a, context_snap, context_snap)
                bh = r42h.technical_context(base_top11[hk], base_top11[ak], base_snap, base_snap)
                ch = r42h.technical_context(context_top11[hk], context_top11[ak], context_snap, context_snap)
                records.append({
                    "date": day, "fixture_id": str(fid), "y": int(r9.actual(row)), "raw": raw,
                    "base_cf": base_cf,
                    "base_weighted_cf": {**base_cf, **bw},
                    "context_weighted_cf": {**base_cf, **cw},
                    "base_hard_cf": {**base_cf, **bh},
                    "context_hard_cf": {**base_cf, **ch},
                    "base_weighted_known": float(bw["tech_known_share_min"]),
                    "context_weighted_known": float(cw["tech_known_share_min"]),
                })
            pending.append((row, raw))

        for row, raw in pending:
            fid = str(row["game_id"]); htid = str(row["home_team"]); atid = str(row["away_team"])
            hi = player_map.get((fid, htid), []); ai = player_map.get((fid, atid), [])
            y = r9.actual(row); hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0; au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"]); ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                base_ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[htid].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                base_ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[atid].xis.append(frozenset(pid for pid, _ in ai))
            for rec in tech_rows.get((fid, htid), []): tech_ledger.update_row(rec)
            for rec in tech_rows.get((fid, atid), []): tech_ledger.update_row(rec)
            base_state.update(row, raw)
    return records, {"fixture_players_sha256": player_sha, "matched_starter_rows": matched_starters, "technical_source": tech_source}


def fit_records(train, key, names):
    xs = [{"date": x["date"], "y": x["y"], "raw": x["raw"], "context_features": x[key]} for x in train]
    return r40c.fit_model(xs, names)


def score_model(model, split, key, names):
    out = []
    for x in split:
        p = r42h.model_prob(model, x["raw"], x[key], names)
        out.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": p})
    return out


def half_records(base_records, tech_records):
    out = []
    for b, t in zip(base_records, tech_records):
        if b["fixture_id"] != t["fixture_id"]:
            raise RuntimeError("paired score order drift")
        out.append({"date": b["date"], "fixture_id": b["fixture_id"], "y": b["y"], "P": f3.blend_prob(b["P"], t["P"], TECH_ALPHA)})
    return out


def compare(a, b):
    # b - a; negative proper-score deltas are improvements.
    am, bm = f3.enriched_metrics(a), f3.enriched_metrics(b)
    return {
        "hits": int(bm["hits"] - am["hits"]),
        "accuracy_pp": 100.0 * float(bm["top1_accuracy"] - am["top1_accuracy"]),
        "logloss": float(bm["logloss"] - am["logloss"]),
        "brier": float(bm["brier"] - am["brier"]),
        "rps": float(bm["rps"] - am["rps"]),
        "draw_binary_logloss": float(bm["draw_binary"]["logloss"] - am["draw_binary"]["logloss"]),
        "draw_binary_brier": float(bm["draw_binary"]["brier"] - am["draw_binary"]["brier"]),
    }


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = HERE / "scratch"
    shutil.rmtree(scratch, ignore_errors=True); scratch.mkdir(parents=True, exist_ok=True)
    old_here, old_out, old_skip, old_n = r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N
    r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = scratch, scratch / "results", SKIP_NEWEST_N, BLOCK_SIZE
    try:
        rows, snap_meta = r42i.freeze_previous_20k()
    finally:
        r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = old_here, old_out, old_skip, old_n
    if len(rows) != BLOCK_SIZE:
        raise RuntimeError("source block length drift")

    base_probs, context_probs, base_top11, context_top11, lineup_meta = make_lineup_probabilities(rows)
    raw_records, tech_meta = build_outcome_records(rows, base_probs, context_probs, base_top11, context_top11, lineup_meta["lineup_train_end"])
    raw_records = sorted(raw_records, key=lambda x: (x["date"], x["fixture_id"]))
    if len(raw_records) < OUTCOME_TRAIN_TARGET + OUTCOME_VAL_TARGET + MIN_TEST_MATCHES:
        raise RuntimeError(f"undersized outcome cohort {len(raw_records)}")
    b1 = f3.boundary_date_safe(raw_records, OUTCOME_TRAIN_TARGET)
    b2 = f3.boundary_date_safe(raw_records, b1 + OUTCOME_VAL_TARGET)
    train, val, test = raw_records[:b1], raw_records[b1:b2], raw_records[b2:]

    names = BASE_NAMES + TECH_NAMES
    m0 = fit_records(train, "base_cf", BASE_NAMES)
    mbw = fit_records(train, "base_weighted_cf", names)
    mcw = fit_records(train, "context_weighted_cf", names)
    mbh = fit_records(train, "base_hard_cf", names)
    mch = fit_records(train, "context_hard_cf", names)

    def score(split):
        no = score_model(m0, split, "base_cf", BASE_NAMES)
        bwf = score_model(mbw, split, "base_weighted_cf", names)
        cwf = score_model(mcw, split, "context_weighted_cf", names)
        bhf = score_model(mbh, split, "base_hard_cf", names)
        chf = score_model(mch, split, "context_hard_cf", names)
        return {
            "no": no,
            "base_weighted_full": bwf, "context_weighted_full": cwf,
            "base_hard_full": bhf, "context_hard_full": chf,
            "base_weighted_half": half_records(no, bwf),
            "context_weighted_half": half_records(no, cwf),
            "base_hard_half": half_records(no, bhf),
            "context_hard_half": half_records(no, chf),
        }
    vs, ts = score(val), score(test)

    primary = compare(ts["base_weighted_half"], ts["context_weighted_half"])
    architecture = compare(ts["context_hard_half"], ts["context_weighted_half"])
    vs_no = compare(ts["no"], ts["context_weighted_half"])
    blocks_primary = f3.paired_time_blocks(ts["base_weighted_half"], ts["context_weighted_half"])
    blocks_arch = f3.paired_time_blocks(ts["context_hard_half"], ts["context_weighted_half"])
    pos_primary = sum(x["delta_logloss"] < 0 for x in blocks_primary); neg_primary = sum(x["delta_logloss"] > 0 for x in blocks_primary)
    pos_arch = sum(x["delta_logloss"] < 0 for x in blocks_arch); neg_arch = sum(x["delta_logloss"] > 0 for x in blocks_arch)
    primary_pass = bool(primary["hits"] >= 0 and primary["logloss"] < 0 and primary["brier"] < 0 and primary["rps"] < 0 and pos_primary >= MIN_POSITIVE_LL_BLOCKS and neg_primary <= MAX_NEGATIVE_LL_BLOCKS)
    architecture_pass = bool(architecture["hits"] >= 0 and architecture["logloss"] < 0 and architecture["brier"] < 0 and architecture["rps"] < 0 and pos_arch >= MIN_POSITIVE_LL_BLOCKS and neg_arch <= MAX_NEGATIVE_LL_BLOCKS)
    passed = bool(primary_pass and architecture_pass)

    result = {
        "schema_version": "football3-r43f5-probability-weighted-technical-mixture-v1",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_PROBABILITY_WEIGHTED_EXPECTED_XI_TECHNICAL_MIXTURE_STRICT_CHRONOLOGICAL",
        "formal_weight": 0,
        "question": "Can the full frozen P(start) distribution, rather than a hard top-11 XI, translate R43F0 fatigue x coach-rotation x depth information into a more stable R42H technical 1X2 signal?",
        "governance": {
            "source_r43f4_head": SOURCE_R43F4_HEAD,
            "source_r43f0_head": SOURCE_R43F0_HEAD,
            "r42h_runner_sha256": SOURCE_R42H_SHA256,
            "r42i_runner_sha256": SOURCE_R42I_SHA256,
            "source_slice": SELECTION,
            "source_overlap_with_r43f3_or_r43f4_outcome_blocks": False,
            "source_previously_consumed_by_r42i_or_r43e1": True,
            "architectural_development_after_r43f4": True,
            "parameter_search": False,
            "feature_search": False,
            "mixture_temperature_or_power": 1.0,
            "mixture_uses_raw_projected_p_start": True,
            "tech_alpha": TECH_ALPHA,
            "alpha_inherited_from_frozen_r42k": True,
            "target_current_match_lineup_used_as_feature": False,
            "target_current_match_coach_used_before_prediction": False,
            "same_date_result_or_lineup_update_before_prediction": False,
            "odds_used": False,
            "draw_research_priority": True,
            "no_manual_draw_override": True,
            "no_draw_threshold": True,
            "no_draw_class_weight": True,
            "unified_three_class_argmax_unchanged": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "base_lineup_probability": "R43B0R1 projected P(start), sum=11",
            "context_lineup_probability": "frozen R43F0 P(start) with fatigue x coach rotation x squad depth",
            "hard_bridge": "top-11 by P(start), retained only as architecture comparator",
            "weighted_bridge": "for each R42H role-specific technical rate, compute P(start)-weighted mean across all prior-history candidate players; weights are the unmodified projected P(start) values",
            "weighted_known_share": "sum P(start) for players with prior technical minutes divided by total projected starting mass 11",
            "weighted_experience": "P(start)-weighted mean log1p(prior technical minutes/90) among known players",
            "technical_model": "same R42H 12-feature technical translation",
            "half_shrink": "fixed alpha=0.5 geometric blend versus no-tech baseline",
            "primary_comparison": "context-weighted half-tech minus base-weighted half-tech",
            "architecture_comparison": "context-weighted half-tech minus context-hard-top11 half-tech",
            "no_tuning_on_test": True,
        },
        "source": {
            "full_valid_xg_rows": snap_meta["full_valid_xg_rows"], "snapshot_rows": len(rows), "selection": SELECTION,
            "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
            "fixtures_sha256": snap_meta["fixtures_sha256"], "match_stats_sha256": snap_meta["match_stats_sha256"], **tech_meta,
        },
        "lineup_stage": lineup_meta,
        "outcome_split": {
            "eligible_matches": len(raw_records), "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]], "val_dates": [val[0]["date"], val[-1]["date"]], "test_dates": [test[0]["date"], test[-1]["date"]], "date_safe": True,
        },
        "technical_coverage_test": {
            "base_weighted_known_share_mean": float(np.mean([x["base_weighted_known"] for x in test])),
            "context_weighted_known_share_mean": float(np.mean([x["context_weighted_known"] for x in test])),
        },
        "validation": {k: f3.enriched_metrics(v) for k, v in vs.items() if k in {"no", "base_weighted_half", "context_weighted_half", "context_hard_half"}},
        "test": {
            "no_tech": f3.enriched_metrics(ts["no"]),
            "base_weighted_half": f3.enriched_metrics(ts["base_weighted_half"]),
            "context_weighted_half": f3.enriched_metrics(ts["context_weighted_half"]),
            "context_hard_half": f3.enriched_metrics(ts["context_hard_half"]),
            "context_weighted_minus_base_weighted": primary,
            "context_weighted_minus_context_hard": architecture,
            "context_weighted_minus_no_tech": vs_no,
            "primary_time_blocks": blocks_primary,
            "architecture_time_blocks": blocks_arch,
            "primary_positive_logloss_blocks": pos_primary, "primary_negative_logloss_blocks": neg_primary,
            "architecture_positive_logloss_blocks": pos_arch, "architecture_negative_logloss_blocks": neg_arch,
        },
        "gate": {
            "development_only": True,
            "primary_context_vs_base_weighted_passed": primary_pass,
            "architecture_context_weighted_vs_context_hard_passed": architecture_pass,
            "passed": passed,
            "action": "FREEZE_WEIGHTED_MIXTURE_ARCHITECTURE_FOR_FORWARD_CONFIRMATION" if passed else "DO_NOT_PROMOTE_R43F5_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "This block was previously consumed by R42I/R43E1 and R43F5 was designed after observing R43F4, so the result is architecture-development evidence only.",
            "No untouched 20k historical block remains for a pristine confirmation of this new bridge; any surviving architecture needs future/forward confirmation.",
            "Candidate pools still exclude cold-start players never observed in prior completed fixtures.",
            "Current-match injury publication timestamps and future-match importance remain outside this stage.",
            "R42L remains untouched and draw is never manually promoted.",
        ],
    }
    p = OUT / "summary_r43f5_probability_weighted_technical_mixture.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    shutil.rmtree(scratch, ignore_errors=True)
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43f5_probability_weighted_technical_mixture.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["source_slice"] == SELECTION
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["mixture_temperature_or_power"] == 1.0
    assert d["governance"]["mixture_uses_raw_projected_p_start"] is True
    assert d["governance"]["no_manual_draw_override"] is True
    assert d["governance"]["unified_three_class_argmax_unchanged"] is True
    assert d["governance"]["r42l_lock_modified"] is False
    assert d["outcome_split"]["date_safe"] is True and d["outcome_split"]["test_n"] >= MIN_TEST_MATCHES
    print("R43F5 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(f"unknown command: {cmd}")
