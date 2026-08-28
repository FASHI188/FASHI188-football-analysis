#!/usr/bin/env python3
from __future__ import annotations

import json
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
for p in (R42H_DIR, R42I_DIR, R43F0_DIR, R43B0R1_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run_r42h_player_technical_translation as r42h  # noqa: E402
import run_r42i_prev20k as r42i  # noqa: E402
import run_r43f0 as f0  # noqa: E402
import run_r43b0r1 as r1  # noqa: E402

r40c = r42h.r40c
r9 = r42h.r9
r33 = r42h.r33
b0 = f0.b0
BASE_NAMES = r42h.BASE_NAMES
TECH_NAMES = r42h.TECH_NAMES

SOURCE_R43F2_HEAD = "676715b628cb0421ec932842c9212c07380fa141"
SOURCE_R43F0_HEAD = "1581e8dfca000fc66d63313a5990bf7a803a8b77"
SOURCE_R42H_SHA256 = "b7bf4bd5fbfd61936e8de52fa09e5b81f748389cb9e0d2de6f33641c1d394a45"
SOURCE_R42I_SHA256 = "17155fe709877801299b1b8f2d965cb11da4cb81c5d2fc5733cf7536bbe2faad"

# Frozen before this run. This 20k block is disjoint from R43F0/F1/F2 scored blocks,
# but it was previously consumed by R43E2, so this remains development evidence only.
SKIP_NEWEST_N = 40000
BLOCK_SIZE = 20000
TECH_ALPHA = 0.50
OUTCOME_TRAIN_TARGET = 4000
OUTCOME_VAL_TARGET = 3000
MIN_TEST_MATCHES = 1000
MIN_POSITIVE_LL_BLOCKS = 2
MAX_NEGATIVE_LL_BLOCKS = 1


def boundary_date_safe(records: list[dict], target: int) -> int:
    if not records:
        return 0
    i = min(max(1, int(target)), len(records) - 1)
    while i < len(records) and records[i]["date"] == records[i - 1]["date"]:
        i += 1
    return i


def blend_prob(base_p: dict, tech_p: dict, alpha: float = TECH_ALPHA) -> dict:
    b = np.clip(np.asarray([base_p["p_home"], base_p["p_draw"], base_p["p_away"]], dtype=float), 1e-12, 1.0)
    c = np.clip(np.asarray([tech_p["p_home"], tech_p["p_draw"], tech_p["p_away"]], dtype=float), 1e-12, 1.0)
    z = np.exp((1.0 - alpha) * np.log(b) + alpha * np.log(c))
    z /= z.sum()
    return r9.decorate(z)


def top11_map(sides: list[dict], examples: list[dict], prob_key: str) -> dict[tuple[int, int], list[int]]:
    out = {}
    for s in sides:
        xs = examples[s["example_start"]:s["example_end"]]
        if not xs or any(prob_key not in x for x in xs):
            continue
        ranked = sorted(xs, key=lambda z: (float(z[prob_key]), z["player_id"]), reverse=True)
        pids = [int(x["player_id"]) for x in ranked[:11]]
        if len(pids) == 11 and len(set(pids)) == 11:
            out[(int(s["fixture_id"]), int(s["team_id"]))] = pids
    return out


def class_counts(records: list[dict]) -> dict:
    picks = {"home": 0, "draw": 0, "away": 0}
    hits = {"home": 0, "draw": 0, "away": 0}
    actuals = {"home": 0, "draw": 0, "away": 0}
    names = ["home", "draw", "away"]
    for x in records:
        y = int(x["y"])
        p = x["P"]
        top = int(p["top1"])
        actuals[names[y]] += 1
        picks[names[top]] += 1
        if top == y:
            hits[names[y]] += 1
    return {"top1_picks": picks, "top1_hits": hits, "actuals": actuals}


def draw_binary_metrics(records: list[dict]) -> dict:
    if not records:
        return {"n": 0, "logloss": None, "brier": None}
    y = np.asarray([1.0 if int(x["y"]) == 1 else 0.0 for x in records], dtype=float)
    p = np.clip(np.asarray([float(x["P"]["p_draw"]) for x in records], dtype=float), 1e-12, 1.0 - 1e-12)
    return {
        "n": len(records),
        "logloss": float(np.mean(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))),
        "brier": float(np.mean((p - y) ** 2)),
    }


def enriched_metrics(records: list[dict]) -> dict:
    m = dict(r33.metrics(records))
    m.update(class_counts(records))
    m["draw_binary"] = draw_binary_metrics(records)
    return m


def paired_time_blocks(base_records: list[dict], cand_records: list[dict], nblocks: int = 4) -> list[dict]:
    if len(base_records) != len(cand_records):
        raise RuntimeError("paired record length mismatch")
    idxs = np.array_split(np.arange(len(base_records)), nblocks)
    out = []
    for idx in idxs:
        b = [base_records[int(i)] for i in idx]
        c = [cand_records[int(i)] for i in idx]
        bm, cm = r33.metrics(b), r33.metrics(c)
        out.append({
            "first_date": b[0]["date"],
            "last_date": b[-1]["date"],
            "n": len(b),
            "base_hits": int(bm["hits"]),
            "candidate_hits": int(cm["hits"]),
            "delta_hits": int(cm["hits"] - bm["hits"]),
            "delta_logloss": float(cm["logloss"] - bm["logloss"]),
            "delta_brier": float(cm["brier"] - bm["brier"]),
            "delta_rps": float(cm["rps"] - bm["rps"]),
        })
    return out


def make_side_predictions(rows: list[dict]) -> tuple[dict, dict, dict]:
    lineup_rows = [
        {
            "date": r["date"],
            "fixture_id": int(r["game_id"]),
            "home_team": int(r["home_team"]),
            "away_team": int(r["away_team"]),
        }
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
    base_top11 = top11_map(later_sides, examples, "base_p")
    context_top11 = top11_map(later_sides, examples, "context_p")

    later_examples = [x for x in examples if x["date"] > train_end]
    later_sides_eval = [s for s in later_sides if (s["fixture_id"], s["team_id"]) in base_top11 and (s["fixture_id"], s["team_id"]) in context_top11]
    base_xi = f0.side_metrics(later_sides_eval, examples, "base_p")
    context_xi = f0.side_metrics(later_sides_eval, examples, "context_p")
    meta = {
        "lineup_split": split,
        "lineup_train_end": train_end,
        "lineup_later_sides": len(later_sides_eval),
        "base_xi": base_xi,
        "context_xi": context_xi,
        "delta_mean_xi_overlap": float(context_xi["mean_xi_overlap"] - base_xi["mean_xi_overlap"]),
        "player_source": player_meta,
        "coach_source": coach_meta,
        "candidate_examples_later": len(later_examples),
    }
    return base_top11, context_top11, meta


def build_outcome_records(rows: list[dict], base_top11: dict, context_top11: dict, lineup_train_end: str):
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
            hkey = (fid, int(row["home_team"]))
            akey = (fid, int(row["away_team"]))
            if day > lineup_train_end and hkey in base_top11 and akey in base_top11 and hkey in context_top11 and akey in context_top11:
                roles = base_ledger.last_role
                hb, ab = base_top11[hkey], base_top11[akey]
                hc, ac = context_top11[hkey], context_top11[akey]
                hbs = r42h.live_snap_for_pids(hb, tech_ledger, roles)
                abs_ = r42h.live_snap_for_pids(ab, tech_ledger, roles)
                hcs = r42h.live_snap_for_pids(hc, tech_ledger, roles)
                acs = r42h.live_snap_for_pids(ac, tech_ledger, roles)
                base_tech = r42h.technical_context(hb, ab, hbs, abs_)
                context_tech = r42h.technical_context(hc, ac, hcs, acs)
                records.append({
                    "date": day,
                    "fixture_id": str(fid),
                    "y": int(r9.actual(row)),
                    "raw": raw,
                    "base_cf": base_cf,
                    "base_xi_tech_cf": {**base_cf, **base_tech},
                    "context_xi_tech_cf": {**base_cf, **context_tech},
                    "base_tech_known_share_min": float(base_tech["tech_known_share_min"]),
                    "context_tech_known_share_min": float(context_tech["tech_known_share_min"]),
                    "base_context_xi_overlap": len(set(hb) & set(hc)) + len(set(ab) & set(ac)),
                })
            pending.append((row, raw))

        # Strict same-date discipline: all predictions above happen before any result/XI/technical update for this date.
        for row, raw in pending:
            fid = str(row["game_id"])
            htid, atid = str(row["home_team"]), str(row["away_team"])
            hi = player_map.get((fid, htid), [])
            ai = player_map.get((fid, atid), [])
            y = r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"])
            ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                base_ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[htid].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                base_ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[atid].xis.append(frozenset(pid for pid, _ in ai))
            for rec in tech_rows.get((fid, htid), []):
                tech_ledger.update_row(rec)
            for rec in tech_rows.get((fid, atid), []):
                tech_ledger.update_row(rec)
            base_state.update(row, raw)

    source = {
        "fixture_players_sha256": player_sha,
        "matched_starter_rows": matched_starters,
        "technical_source": tech_source,
    }
    return records, source


def model_record(model, x: dict, cf_key: str, names: list[str]) -> dict:
    p = r42h.model_prob(model, x["raw"], x[cf_key], names)
    return {"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": p}


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = HERE / "scratch"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    # Reuse the frozen R42I loader only for exact source construction; the selected block is fixed here.
    old_here, old_out = r42i.HERE, r42i.OUT
    old_skip, old_n = r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N
    r42i.HERE, r42i.OUT = scratch, scratch / "results"
    r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = SKIP_NEWEST_N, BLOCK_SIZE
    try:
        rows, snap_meta = r42i.freeze_previous_20k()
    finally:
        r42i.HERE, r42i.OUT = old_here, old_out
        r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = old_skip, old_n

    if len(rows) != BLOCK_SIZE:
        raise RuntimeError("source block length drift")
    base_top11, context_top11, lineup_meta = make_side_predictions(rows)
    raw_records, tech_meta = build_outcome_records(rows, base_top11, context_top11, lineup_meta["lineup_train_end"])
    raw_records = sorted(raw_records, key=lambda x: (x["date"], x["fixture_id"]))
    if len(raw_records) < OUTCOME_TRAIN_TARGET + OUTCOME_VAL_TARGET + MIN_TEST_MATCHES:
        raise RuntimeError(f"undersized outcome cohort: {len(raw_records)}")

    b1 = boundary_date_safe(raw_records, OUTCOME_TRAIN_TARGET)
    b2 = boundary_date_safe(raw_records, b1 + OUTCOME_VAL_TARGET)
    train = raw_records[:b1]
    val = raw_records[b1:b2]
    test = raw_records[b2:]
    if len(test) < MIN_TEST_MATCHES:
        raise RuntimeError("test cohort too small")

    train_base = [{"date": x["date"], "y": x["y"], "raw": x["raw"], "context_features": x["base_cf"]} for x in train]
    train_base_xi = [{"date": x["date"], "y": x["y"], "raw": x["raw"], "context_features": x["base_xi_tech_cf"]} for x in train]
    train_context_xi = [{"date": x["date"], "y": x["y"], "raw": x["raw"], "context_features": x["context_xi_tech_cf"]} for x in train]

    no_tech_model = r40c.fit_model(train_base, BASE_NAMES)
    base_xi_model = r40c.fit_model(train_base_xi, BASE_NAMES + TECH_NAMES)
    context_xi_model = r40c.fit_model(train_context_xi, BASE_NAMES + TECH_NAMES)

    def score(split_records: list[dict]):
        no_tech, base_full, context_full, base_half, context_half = [], [], [], [], []
        for x in split_records:
            p0 = r42h.model_prob(no_tech_model, x["raw"], x["base_cf"], BASE_NAMES)
            pb = r42h.model_prob(base_xi_model, x["raw"], x["base_xi_tech_cf"], BASE_NAMES + TECH_NAMES)
            pc = r42h.model_prob(context_xi_model, x["raw"], x["context_xi_tech_cf"], BASE_NAMES + TECH_NAMES)
            no_tech.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": p0})
            base_full.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": pb})
            context_full.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": pc})
            base_half.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": blend_prob(p0, pb)})
            context_half.append({"date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"], "P": blend_prob(p0, pc)})
        return no_tech, base_full, context_full, base_half, context_half

    v0, vbf, vcf, vbh, vch = score(val)
    t0, tbf, tcf, tbh, tch = score(test)
    vm0, vmbh, vmch = enriched_metrics(v0), enriched_metrics(vbh), enriched_metrics(vch)
    tm0, tmbh, tmch = enriched_metrics(t0), enriched_metrics(tbh), enriched_metrics(tch)
    blocks = paired_time_blocks(tbh, tch)
    pos_ll = sum(1 for x in blocks if x["delta_logloss"] < 0)
    neg_ll = sum(1 for x in blocks if x["delta_logloss"] > 0)

    delta_vs_base = {
        "hits": int(tmch["hits"] - tmbh["hits"]),
        "accuracy_pp": 100.0 * float(tmch["top1_accuracy"] - tmbh["top1_accuracy"]),
        "logloss": float(tmch["logloss"] - tmbh["logloss"]),
        "brier": float(tmch["brier"] - tmbh["brier"]),
        "rps": float(tmch["rps"] - tmbh["rps"]),
        "draw_binary_logloss": float(tmch["draw_binary"]["logloss"] - tmbh["draw_binary"]["logloss"]),
        "draw_binary_brier": float(tmch["draw_binary"]["brier"] - tmbh["draw_binary"]["brier"]),
    }
    delta_vs_no_tech = {
        "hits": int(tmch["hits"] - tm0["hits"]),
        "accuracy_pp": 100.0 * float(tmch["top1_accuracy"] - tm0["top1_accuracy"]),
        "logloss": float(tmch["logloss"] - tm0["logloss"]),
        "brier": float(tmch["brier"] - tm0["brier"]),
        "rps": float(tmch["rps"] - tm0["rps"]),
    }
    passed = bool(
        delta_vs_base["hits"] >= 0
        and delta_vs_base["logloss"] < 0
        and delta_vs_base["brier"] < 0
        and delta_vs_base["rps"] < 0
        and pos_ll >= MIN_POSITIVE_LL_BLOCKS
        and neg_ll <= MAX_NEGATIVE_LL_BLOCKS
    )

    base_known = np.asarray([x["base_tech_known_share_min"] for x in test], dtype=float)
    context_known = np.asarray([x["context_tech_known_share_min"] for x in test], dtype=float)
    overlap = np.asarray([x["base_context_xi_overlap"] for x in test], dtype=float)

    result = {
        "schema_version": "football3-r43f3-context-lineup-technical-translation-v1",
        "status": "COMPLETE",
        "classification": "STRICT_CHRONOLOGICAL_CONTEXT_START_PROBABILITY_TO_R42H_TECHNICAL_1X2_TRANSLATION",
        "formal_weight": 0,
        "question": "Does the frozen R43F0 fatigue x coach-rotation x depth P(start) mechanism improve unified 1X2 when used only to choose the expected XI feeding the frozen R42H player-technical translation?",
        "governance": {
            "source_r43f2_head": SOURCE_R43F2_HEAD,
            "source_r43f0_head": SOURCE_R43F0_HEAD,
            "r42h_runner_sha256": SOURCE_R42H_SHA256,
            "r42i_runner_sha256": SOURCE_R42I_SHA256,
            "source_slice": "valid_sorted_[-60000:-40000]",
            "source_overlap_with_r43f0_f1_f2_scored_blocks": False,
            "source_previously_consumed_by_r43e2": True,
            "parameter_search": False,
            "feature_search": False,
            "tech_alpha": TECH_ALPHA,
            "alpha_inherited_from_frozen_r42k": True,
            "target_current_match_lineup_used_as_feature": False,
            "target_current_match_lineup_used_only_for_historical_eligibility_and_evaluation": True,
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
            "lineup_base": "R43B0R1 historical P(start) features",
            "lineup_candidate": "frozen R43F0 base + fatigue + coach rotation + squad depth interactions",
            "lineup_to_technical_bridge": "top-11 expected XI obtained by ranking each frozen P(start) distribution; no target XI membership enters prediction",
            "technical_layer": "frozen R42H TechnicalLedger and 12 technical context features",
            "outcome_baseline": "R40C/R9b K1 + BASE_NAMES",
            "technical_full_models": "same outcome architecture; only expected-XI source differs",
            "primary_comparison": "fixed alpha=0.5 half-shrunk base-P(start)-XI technical model vs fixed alpha=0.5 half-shrunk R43F0-context-P(start)-XI technical model",
            "draw_policy": "draw mechanisms may be studied in diagnostics, but final 1X2 probabilities are never manually altered",
            "outcome_train_target": OUTCOME_TRAIN_TARGET,
            "outcome_val_target": OUTCOME_VAL_TARGET,
        },
        "source": {
            "full_valid_xg_rows": snap_meta["full_valid_xg_rows"],
            "snapshot_rows": len(rows),
            "selection": "valid_sorted_[-60000:-40000]",
            "first_date": rows[0]["date"],
            "last_date": rows[-1]["date"],
            "fixtures_sha256": snap_meta["fixtures_sha256"],
            "match_stats_sha256": snap_meta["match_stats_sha256"],
            **tech_meta,
        },
        "lineup_stage": lineup_meta,
        "outcome_split": {
            "eligible_matches": len(raw_records),
            "train_n": len(train),
            "val_n": len(val),
            "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
        },
        "technical_coverage_test": {
            "base_known_share_mean": float(base_known.mean()),
            "context_known_share_mean": float(context_known.mean()),
            "base_context_expected_xi_overlap_mean_of_22": float(overlap.mean()),
        },
        "validation": {
            "no_tech": vm0,
            "base_xi_half_technical": vmbh,
            "context_xi_half_technical": vmch,
        },
        "test": {
            "no_tech": tm0,
            "base_xi_full_technical_diagnostic": enriched_metrics(tbf),
            "context_xi_full_technical_diagnostic": enriched_metrics(tcf),
            "base_xi_half_technical": tmbh,
            "context_xi_half_technical": tmch,
            "candidate_minus_base_xi_half": delta_vs_base,
            "candidate_minus_no_tech": delta_vs_no_tech,
            "paired_time_blocks": blocks,
            "positive_logloss_blocks": pos_ll,
            "negative_logloss_blocks": neg_ll,
        },
        "gate": {
            "primary_gate_is_candidate_vs_base_xi_half": True,
            "require_top1_nonworse": True,
            "require_logloss_improve": True,
            "require_brier_improve": True,
            "require_rps_improve": True,
            "min_positive_logloss_blocks": MIN_POSITIVE_LL_BLOCKS,
            "max_negative_logloss_blocks": MAX_NEGATIVE_LL_BLOCKS,
            "passed": passed,
            "action": "KEEP_R43F0_CONTEXT_LINEUP_FOR_NEXT_1X2_REPLICATION" if passed else "DO_NOT_PROMOTE_R43F3_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "This block was previously scored by R43E2, so R43F3 is controlled development evidence, not pristine forward confirmation.",
            "The current experiment uses the top-11 expected XI implied by P(start), not a full mixture over every possible XI.",
            "Cold-start players absent from prior completed fixture history remain outside the candidate pool.",
            "Current-match injury publication timestamps and future-match importance are not yet integrated.",
            "R42L remains untouched and no draw output is forced.",
        ],
    }

    (OUT / "summary_r43f3_context_lineup_technical_translation.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    shutil.rmtree(scratch, ignore_errors=True)
    return result


def verify() -> None:
    p = OUT / "summary_r43f3_context_lineup_technical_translation.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["governance"]["parameter_search"] is False
    assert d["governance"]["target_current_match_lineup_used_as_feature"] is False
    assert d["governance"]["same_date_result_or_lineup_update_before_prediction"] is False
    assert d["governance"]["no_manual_draw_override"] is True
    assert d["governance"]["unified_three_class_argmax_unchanged"] is True
    assert d["governance"]["tech_alpha"] == 0.5
    assert d["outcome_split"]["date_safe"] is True
    assert d["outcome_split"]["test_n"] >= MIN_TEST_MATCHES
    print("R43F3 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
