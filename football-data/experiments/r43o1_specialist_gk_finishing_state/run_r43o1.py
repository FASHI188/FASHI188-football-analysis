#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
F5_DIR = ROOT / "football-data" / "experiments" / "r43f5_probability_weighted_technical_mixture"
if str(F5_DIR) not in sys.path:
    sys.path.insert(0, str(F5_DIR))

import run_r43f5 as f5  # noqa: E402

# Frozen before any R43O1 outcome scoring. All three blocks are already-consumed
# architecture-development evidence only; formal_weight therefore remains zero.
BLOCKS = [
    {"skip_newest": 20000, "selection": "valid_sorted_[-40000:-20000]"},
    {"skip_newest": 40000, "selection": "valid_sorted_[-60000:-40000]"},
    {"skip_newest": 60000, "selection": "valid_sorted_[-80000:-60000]"},
]
BLOCK_SIZE = 20000
OUTCOME_TRAIN_TARGET = 4000
OUTCOME_VAL_TARGET = 3000
MIN_TEST_MATCHES = 1000
TECH_ALPHA = 0.50
MIN_POSITIVE_LL_BLOCKS = 3
MAX_NEGATIVE_LL_BLOCKS = 1
BREAKTHROUGH_TOP1_PP = 1.0
SOURCE_R43F5_HEAD = "0140944ee387e4328c14eef0837481d800dfcc38"
SOURCE_SCHEMA_AUDIT_HEAD = "1d2dadd8b1237c22496bee897c98a593f9d49ac0"
SOURCE_R42H_SHA256 = "b7bf4bd5fbfd61936e8de52fa09e5b81f748389cb9e0d2de6f33641c1d394a45"
SOURCE_R42I_SHA256 = "17155fe709877801299b1b8f2d965cb11da4cb81c5d2fc5733cf7536bbe2faad"

# Fixed specialist definitions. Pseudo-counts are domain priors, not tuned values.
# SOT finishing prior mean 0.30 = Beta(1.5,3.5)
# all-shot finishing prior mean 0.10 = Beta(1,9)
# goalkeeper save prior mean 0.75 = Beta(3,1)
# penalty conversion prior mean 0.75 = Beta(3,1)
PRIORS = {
    "finish_sot": {"alpha": 1.5, "beta": 3.5},
    "finish_shot": {"alpha": 1.0, "beta": 9.0},
    "gk_save": {"alpha": 3.0, "beta": 1.0},
    "penalty_conversion": {"alpha": 3.0, "beta": 1.0},
}
SPEC_NAMES = [
    "spec_finish_sot_diff",
    "spec_finish_shot_diff",
    "spec_gk_save_diff",
    "spec_penalty_conversion_diff",
    "spec_gk_known_min",
    "spec_finish_known_min",
]
SPEC_FIELDS = [
    "goals_total", "shots_on", "shots_total", "goals_saves", "goals_conceded",
    "penalty_scored", "penalty_missed",
]
BASE_TECH_NAMES = f5.BASE_NAMES + f5.TECH_NAMES
CANDIDATE_NAMES = BASE_TECH_NAMES + SPEC_NAMES


def num(x) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else float("nan")
    except Exception:
        return float("nan")


def beta_rate(success: float, total: float, alpha: float, beta: float) -> float:
    s = max(0.0, float(success))
    t = max(s, float(total))
    return float((s + alpha) / (t + alpha + beta))


class SpecialistLedger:
    def __init__(self):
        self.total_minutes = defaultdict(float)
        self.sums = defaultdict(lambda: defaultdict(float))
        self.last_role = {}

    def update_row(self, rec: dict) -> None:
        pid = int(rec["player_id"])
        mins = num(rec.get("games_minutes"))
        if not np.isfinite(mins) or mins <= 0:
            return
        mins = float(min(130.0, mins))
        self.total_minutes[pid] += mins
        role = f5.r42h.norm_role(rec.get("games_position")) or f5.r42h.norm_role(rec.get("position"))
        if role:
            self.last_role[pid] = role
        for key in SPEC_FIELDS:
            v = num(rec.get(key))
            if np.isfinite(v) and v >= 0:
                self.sums[pid][key] += float(v)


def load_specialist_rows(rows: list[dict], player_path: Path, stats_path: Path):
    wanted = {int(r["game_id"]) for r in rows}
    fp_cols = ["id", "fixture_id", "team_id", "player_id", "position"]
    fp = pd.read_parquet(player_path, columns=fp_cols)
    fp = fp[fp["fixture_id"].isin(wanted)].copy()
    fp["id"] = pd.to_numeric(fp["id"], errors="coerce").astype("Int64")
    fp = fp.dropna(subset=["id", "player_id", "team_id"])
    wanted_fp = set(int(x) for x in fp["id"].astype(int).tolist())

    st_cols = [
        "fixture_player_id", "fixture_id", "player_id", "games_minutes", "games_position",
        *SPEC_FIELDS,
    ]
    st = pd.read_parquet(stats_path, columns=st_cols)
    st = st[st["fixture_player_id"].isin(wanted_fp)].copy()
    bridge = fp[["id", "fixture_id", "team_id", "player_id", "position"]].rename(
        columns={"id": "fixture_player_id", "fixture_id": "fp_fixture_id", "player_id": "fp_player_id"}
    )
    st = st.merge(bridge, on="fixture_player_id", how="inner", validate="many_to_one")
    bad_fixture = (pd.to_numeric(st["fixture_id"], errors="coerce") != pd.to_numeric(st["fp_fixture_id"], errors="coerce")).sum()
    bad_player = (pd.to_numeric(st["player_id"], errors="coerce") != pd.to_numeric(st["fp_player_id"], errors="coerce")).sum()
    if bad_fixture or bad_player:
        raise RuntimeError(f"specialist identity join conflict fixture={bad_fixture} player={bad_player}")
    out = defaultdict(list)
    for rec in st.to_dict("records"):
        out[(str(int(rec["fixture_id"])), str(int(rec["team_id"])))].append(rec)
    return out, {
        "matched_specialist_rows": int(len(st)),
        "specialist_fixture_count": int(st["fixture_id"].nunique()),
        "specialist_player_count": int(st["player_id"].nunique()),
    }


def _weighted(values, default: float) -> float:
    good = [(float(w), float(v)) for w, v in values if float(w) > 0 and np.isfinite(v)]
    den = sum(w for w, _ in good)
    if den <= 0:
        return float(default)
    return float(sum(w * v for w, v in good) / den)


def specialist_side(prob_pairs: list[tuple[int, float]], ledger: SpecialistLedger) -> dict:
    pairs = [(int(pid), float(p)) for pid, p in prob_pairs if float(p) > 0]
    attackers = []
    keepers = []
    attacker_mass = 0.0
    attacker_known_mass = 0.0
    gk_known_mass = 0.0
    for pid, p in pairs:
        role = ledger.last_role.get(pid)
        mins = float(ledger.total_minutes.get(pid, 0.0))
        sums = ledger.sums.get(pid, {})
        if role in {"F", "M"}:
            attacker_mass += p
            if mins > 0:
                attacker_known_mass += p
                goals = float(sums.get("goals_total", 0.0))
                sot = float(sums.get("shots_on", 0.0))
                shots = float(sums.get("shots_total", 0.0))
                psc = float(sums.get("penalty_scored", 0.0))
                pmi = float(sums.get("penalty_missed", 0.0))
                attackers.append((p, goals, sot, shots, psc, pmi))
        if role == "G" and mins > 0:
            gk_known_mass += p
            saves = float(sums.get("goals_saves", 0.0))
            conceded = float(sums.get("goals_conceded", 0.0))
            keepers.append((p, saves, conceded))

    finish_sot = _weighted([
        (p, beta_rate(g, sot, PRIORS["finish_sot"]["alpha"], PRIORS["finish_sot"]["beta"]))
        for p, g, sot, _, _, _ in attackers
    ], 0.30)
    finish_shot = _weighted([
        (p, beta_rate(g, sh, PRIORS["finish_shot"]["alpha"], PRIORS["finish_shot"]["beta"]))
        for p, g, _, sh, _, _ in attackers
    ], 0.10)
    penalty_conversion = _weighted([
        (p, beta_rate(psc, psc + pmi, PRIORS["penalty_conversion"]["alpha"], PRIORS["penalty_conversion"]["beta"]))
        for p, _, _, _, psc, pmi in attackers
    ], 0.75)
    gk_save = _weighted([
        (p, beta_rate(sv, sv + gc, PRIORS["gk_save"]["alpha"], PRIORS["gk_save"]["beta"]))
        for p, sv, gc in keepers
    ], 0.75)
    finish_known = float(attacker_known_mass / attacker_mass) if attacker_mass > 0 else 0.0
    return {
        "finish_sot": finish_sot,
        "finish_shot": finish_shot,
        "gk_save": gk_save,
        "penalty_conversion": penalty_conversion,
        "gk_known": float(min(1.0, gk_known_mass)),
        "finish_known": finish_known,
    }


def specialist_context(home_pairs, away_pairs, ledger: SpecialistLedger) -> dict:
    h = specialist_side(home_pairs, ledger)
    a = specialist_side(away_pairs, ledger)
    return {
        "spec_finish_sot_diff": float(h["finish_sot"] - a["finish_sot"]),
        "spec_finish_shot_diff": float(h["finish_shot"] - a["finish_shot"]),
        "spec_gk_save_diff": float(h["gk_save"] - a["gk_save"]),
        "spec_penalty_conversion_diff": float(h["penalty_conversion"] - a["penalty_conversion"]),
        "spec_gk_known_min": float(min(h["gk_known"], a["gk_known"])),
        "spec_finish_known_min": float(min(h["finish_known"], a["finish_known"])),
    }


def freeze_block(skip_newest: int, scratch: Path):
    old_here, old_out = f5.r42i.HERE, f5.r42i.OUT
    old_skip, old_n = f5.r42i.SKIP_NEWEST_N, f5.r42i.SNAPSHOT_N
    f5.r42i.HERE, f5.r42i.OUT = scratch, scratch / "results"
    f5.r42i.SKIP_NEWEST_N, f5.r42i.SNAPSHOT_N = skip_newest, BLOCK_SIZE
    try:
        return f5.r42i.freeze_previous_20k()
    finally:
        f5.r42i.HERE, f5.r42i.OUT = old_here, old_out
        f5.r42i.SKIP_NEWEST_N, f5.r42i.SNAPSHOT_N = old_skip, old_n


def build_records(rows, context_probs, lineup_train_end):
    player_map, player_sha, matched_starters, player_path = f5.r40c.download_player_rows(rows)
    stats_path = f5.r42h.download_stats()
    tech_rows, tech_source = f5.r42h.load_technical_rows(rows, Path(player_path), stats_path)
    specialist_rows, specialist_source = load_specialist_rows(rows, Path(player_path), stats_path)

    base_state = f5.r9.S()
    states = defaultdict(f5.r40c.TeamState)
    base_ledger = f5.r40c.Ledger()
    tech_ledger = f5.r42h.TechnicalLedger()
    spec_ledger = SpecialistLedger()
    records = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            fid = int(row["game_id"])
            raw = base_state.pred(row)
            base_cf = f5.r40c.context_features(row, states, base_ledger)
            hk = (fid, int(row["home_team"])); ak = (fid, int(row["away_team"]))
            if day > lineup_train_end and hk in context_probs and ak in context_probs:
                cp_h, cp_a = context_probs[hk], context_probs[ak]
                pids = {pid for pid, _ in cp_h} | {pid for pid, _ in cp_a}
                snap = f5.r42h.live_snap_for_pids(pids, tech_ledger, base_ledger.last_role)
                tech = f5.weighted_technical_context(cp_h, cp_a, snap, snap)
                spec = specialist_context(cp_h, cp_a, spec_ledger)
                records.append({
                    "date": day,
                    "fixture_id": str(fid),
                    "y": int(f5.r9.actual(row)),
                    "raw": raw,
                    "base_cf": base_cf,
                    "context_weighted_cf": {**base_cf, **tech},
                    "context_specialist_cf": {**base_cf, **tech, **spec},
                    "tech_known": float(tech["tech_known_share_min"]),
                    "spec_gk_known": float(spec["spec_gk_known_min"]),
                    "spec_finish_known": float(spec["spec_finish_known_min"]),
                })
            pending.append((row, raw))

        # Same-date fixtures cannot update one another: all feature rows above are sealed first.
        for row, raw in pending:
            fid = str(row["game_id"]); htid = str(row["home_team"]); atid = str(row["away_team"])
            hi = player_map.get((fid, htid), []); ai = player_map.get((fid, atid), [])
            y = f5.r9.actual(row)
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
            for rec in specialist_rows.get((fid, htid), []): spec_ledger.update_row(rec)
            for rec in specialist_rows.get((fid, atid), []): spec_ledger.update_row(rec)
            base_state.update(row, raw)

    return records, {
        "fixture_players_sha256": player_sha,
        "matched_starter_rows": matched_starters,
        "technical_source": tech_source,
        "specialist_source": specialist_source,
        "stats_sha256": f5.r42h.fsha(stats_path),
    }


def score_half_models(train, val, test):
    m0 = f5.fit_records(train, "base_cf", f5.BASE_NAMES)
    mb = f5.fit_records(train, "context_weighted_cf", BASE_TECH_NAMES)
    ms = f5.fit_records(train, "context_specialist_cf", CANDIDATE_NAMES)

    def scored(split):
        no = f5.score_model(m0, split, "base_cf", f5.BASE_NAMES)
        base_full = f5.score_model(mb, split, "context_weighted_cf", BASE_TECH_NAMES)
        spec_full = f5.score_model(ms, split, "context_specialist_cf", CANDIDATE_NAMES)
        return {
            "no": no,
            "baseline": f5.half_records(no, base_full),
            "candidate": f5.half_records(no, spec_full),
        }
    return scored(val), scored(test)


def run_block(block: dict, idx: int):
    scratch = HERE / f"scratch_block_{idx}"
    shutil.rmtree(scratch, ignore_errors=True); scratch.mkdir(parents=True, exist_ok=True)
    rows, snap_meta = freeze_block(int(block["skip_newest"]), scratch)
    if len(rows) != BLOCK_SIZE:
        raise RuntimeError(f"block {idx} source length drift")
    _, context_probs, _, _, lineup_meta = f5.make_lineup_probabilities(rows)
    records, source_meta = build_records(rows, context_probs, lineup_meta["lineup_train_end"])
    records = sorted(records, key=lambda x: (x["date"], x["fixture_id"]))
    if len(records) < OUTCOME_TRAIN_TARGET + OUTCOME_VAL_TARGET + MIN_TEST_MATCHES:
        raise RuntimeError(f"block {idx} undersized outcome cohort {len(records)}")
    b1 = f5.f3.boundary_date_safe(records, OUTCOME_TRAIN_TARGET)
    b2 = f5.f3.boundary_date_safe(records, b1 + OUTCOME_VAL_TARGET)
    train, val, test = records[:b1], records[b1:b2], records[b2:]
    vs, ts = score_half_models(train, val, test)
    vd = f5.compare(vs["baseline"], vs["candidate"])
    td = f5.compare(ts["baseline"], ts["candidate"])
    time_blocks = f5.f3.paired_time_blocks(ts["baseline"], ts["candidate"])
    pos = sum(x["delta_logloss"] < 0 for x in time_blocks)
    neg = sum(x["delta_logloss"] > 0 for x in time_blocks)
    gate = bool(
        vd["hits"] >= 0 and vd["logloss"] < 0 and vd["brier"] < 0 and vd["rps"] < 0
        and td["hits"] >= 0 and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
        and td["draw_binary_logloss"] < 0 and td["draw_binary_brier"] < 0
        and pos >= MIN_POSITIVE_LL_BLOCKS and neg <= MAX_NEGATIVE_LL_BLOCKS
    )
    summary = {
        "block_index": idx,
        "selection": block["selection"],
        "skip_newest": block["skip_newest"],
        "source": {
            "snapshot_rows": len(rows),
            "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
            "fixtures_sha256": snap_meta["fixtures_sha256"],
            "match_stats_sha256": snap_meta["match_stats_sha256"],
            **source_meta,
        },
        "lineup": {
            "lineup_train_end": lineup_meta["lineup_train_end"],
            "context_xi": lineup_meta["context_xi"],
        },
        "split": {
            "eligible_matches": len(records), "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
        },
        "coverage_test": {
            "tech_known_mean": float(np.mean([x["tech_known"] for x in test])),
            "gk_known_mean": float(np.mean([x["spec_gk_known"] for x in test])),
            "finish_known_mean": float(np.mean([x["spec_finish_known"] for x in test])),
        },
        "validation": {
            "baseline": f5.f3.enriched_metrics(vs["baseline"]),
            "candidate": f5.f3.enriched_metrics(vs["candidate"]),
            "delta": vd,
        },
        "test": {
            "baseline": f5.f3.enriched_metrics(ts["baseline"]),
            "candidate": f5.f3.enriched_metrics(ts["candidate"]),
            "delta": td,
            "time_blocks": time_blocks,
            "positive_logloss_blocks": pos,
            "negative_logloss_blocks": neg,
        },
        "gate_passed": gate,
    }
    shutil.rmtree(scratch, ignore_errors=True)
    return summary, ts["baseline"], ts["candidate"]


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    block_summaries = []
    all_base, all_candidate = [], []
    for idx, block in enumerate(BLOCKS, start=1):
        summary, base_scores, cand_scores = run_block(block, idx)
        block_summaries.append(summary)
        all_base.extend(base_scores); all_candidate.extend(cand_scores)

    aggregate_delta = f5.compare(all_base, all_candidate)
    block_gates = sum(bool(b["gate_passed"]) for b in block_summaries)
    nonnegative_top1_blocks = sum(b["test"]["delta"]["hits"] >= 0 for b in block_summaries)
    aggregate_gate = bool(
        block_gates >= 2
        and nonnegative_top1_blocks >= 2
        and aggregate_delta["hits"] >= 0
        and aggregate_delta["logloss"] < 0
        and aggregate_delta["brier"] < 0
        and aggregate_delta["rps"] < 0
        and aggregate_delta["draw_binary_logloss"] < 0
        and aggregate_delta["draw_binary_brier"] < 0
    )
    breakthrough = bool(aggregate_gate and aggregate_delta["accuracy_pp"] >= BREAKTHROUGH_TOP1_PP)
    if breakthrough:
        action = "FREEZE_R43O1_SPECIALIST_FOR_DISJOINT_FORWARD_CONFIRMATION"
    elif aggregate_gate:
        action = "KEEP_R43O1_AS_DEVELOPMENT_ONLY_NOT_BREAKTHROUGH"
    else:
        action = "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_THESE_TEST_BLOCKS"

    result = {
        "schema_version": "football3-r43o1-specialist-gk-finishing-state-v1",
        "status": "COMPLETE",
        "classification": "CONSUMED_BLOCK_ARCHITECTURE_DEVELOPMENT_SPECIALIST_STATE",
        "formal_weight": 0,
        "question": "Do strictly prior player finishing, goalkeeper save and penalty specialist states add stable 1X2 information beyond the frozen R43F5 context-weighted technical half-tech baseline?",
        "governance": {
            "source_r43f5_head": SOURCE_R43F5_HEAD,
            "source_schema_audit_head": SOURCE_SCHEMA_AUDIT_HEAD,
            "r42h_runner_sha256": SOURCE_R42H_SHA256,
            "r42i_runner_sha256": SOURCE_R42I_SHA256,
            "blocks_predeclared": BLOCKS,
            "all_blocks_previously_consumed": True,
            "parameter_search": False,
            "feature_search": False,
            "prior_search": False,
            "priors_fixed_before_outcome_scoring": PRIORS,
            "tech_alpha": TECH_ALPHA,
            "alpha_inherited_from_r43f5": True,
            "target_current_match_lineup_used_as_feature": False,
            "same_date_updates_before_prediction": False,
            "odds_used": False,
            "manual_draw_override": False,
            "draw_threshold": False,
            "draw_class_weight": False,
            "unified_three_class_argmax_unchanged": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "R43F5 context P(start)-weighted R42H technical model, fixed 0.5 geometric half-shrink versus no-tech",
            "candidate": "same baseline plus six fixed specialist-state features, same fixed 0.5 half-shrink",
            "specialist_features": SPEC_NAMES,
            "finish_roles": ["F", "M"],
            "goalkeeper_role": "G",
            "state_update": "only after every fixture on the same date has had its prematch features sealed",
            "breakthrough_threshold_top1_pp": BREAKTHROUGH_TOP1_PP,
            "per_block_gate": "validation Top1 nonnegative and LL/Brier/RPS improve; test Top1 nonnegative and LL/Brier/RPS plus draw-binary LL/Brier improve; >=3 positive LL time blocks and <=1 negative",
            "aggregate_gate": ">=2/3 block gates, >=2/3 nonnegative Top1 blocks, pooled Top1 nonnegative, pooled LL/Brier/RPS and draw-binary LL/Brier all improve",
        },
        "blocks": block_summaries,
        "aggregate": {
            "test_n": len(all_base),
            "baseline": f5.f3.enriched_metrics(all_base),
            "candidate": f5.f3.enriched_metrics(all_candidate),
            "delta": aggregate_delta,
            "block_gates_passed": block_gates,
            "nonnegative_top1_blocks": nonnegative_top1_blocks,
            "gate_passed": aggregate_gate,
            "breakthrough_candidate": breakthrough,
        },
        "gate": {
            "passed": aggregate_gate,
            "breakthrough_candidate": breakthrough,
            "action": action,
        },
        "limitations": [
            "Every scored historical block was already consumed by earlier research, so this can only generate architecture-development evidence and carries formal_weight=0.",
            "The specialist priors are fixed generic domain priors, not learned or tuned on the scored blocks.",
            "Penalty conversion is sparse by construction and is not given any manual outcome-specific weight.",
            "Cold-start players with no completed-match history remain unknown until observed.",
            "No promotion is valid without genuinely fresh forward confirmation.",
        ],
    }
    p = OUT / "summary_r43o1_specialist_gk_finishing_state.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"gate": result["gate"], "aggregate": result["aggregate"]["delta"]}, indent=2))
    return result


def verify() -> None:
    p = OUT / "summary_r43o1_specialist_gk_finishing_state.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["all_blocks_previously_consumed"] is True
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["feature_search"] is False
    assert d["governance"]["prior_search"] is False
    assert d["governance"]["tech_alpha"] == 0.5
    assert d["governance"]["same_date_updates_before_prediction"] is False
    assert d["governance"]["manual_draw_override"] is False
    assert d["governance"]["unified_three_class_argmax_unchanged"] is True
    assert d["governance"]["r42l_lock_modified"] is False
    assert len(d["blocks"]) == 3
    assert all(b["split"]["date_safe"] and b["split"]["test_n"] >= MIN_TEST_MATCHES for b in d["blocks"])
    assert d["design"]["breakthrough_threshold_top1_pp"] == 1.0
    print("R43O1 specialist-state contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(f"unknown command: {cmd}")
