#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42K_DIR = HERE.parent / "r42k_r42h_v1_half_shrink_oldest20k"
R42F_SUMMARY = HERE.parent / "r42f_historical_season_bridge_lineup_audit" / "results" / "summary_r42f_lineup_audit.json"
R42K_SUMMARY = R42K_DIR / "results" / "summary_r42k_half_shrink_oldest20k.json"
sys.path.insert(0, str(R42K_DIR))
import run_r42k_half_shrink as r42k  # noqa: E402

r42i = r42k.r42i
r42h = r42i.r42h
r42g = r42h.r42g
r40c = r42h.r40c
r9 = r42h.r9

R42D_COMMIT = "102ea1729b97b4944fc280d73d6f67e9133b0df9"
R42D_BLOB_SHA = "e224de86f0e3e46ac8386c20ef23d8ba35cd3c58"
R42D_URL = (
    "https://raw.githubusercontent.com/FASHI188/FASHI188-football-analysis/"
    f"{R42D_COMMIT}/football-data/experiments/r42d_mw2_current_season_bridge/results/summary_r42d_mw2.json"
)
R42E_COMMIT = "b4763d93e5a6e91bf29ca6dc4744b6f5a2314370"
R42E_BLOB_SHA = "a4fa485227bed5a8af6044b86cf20803a1bf924f"
R42E_URL = (
    "https://raw.githubusercontent.com/FASHI188/FASHI188-football-analysis/"
    f"{R42E_COMMIT}/football-data/experiments/r42e_mw2_bridge_prospective_lock/results/r42e_mw2_bridge_lock.json"
)
TECH_ALPHA = 0.50
PREMATCH_BASELINE_TOL = 1e-8


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def fetch_json(url: str, expected_blob_sha: str, user_agent: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    got = git_blob_sha(data)
    if got != expected_blob_sha:
        raise RuntimeError(f"frozen input blob drift: expected={expected_blob_sha} got={got} url={url}")
    return json.loads(data.decode("utf-8"))


def key_of(target: dict) -> tuple[str, str, str]:
    return (
        str(target["home_team"]),
        str(target["away_team"]),
        str(target["kickoff_at_utc"]),
    )


def replay_and_fit():
    rows = r9.load()
    player_map, player_sha, matched_starters, player_path = r40c.download_player_rows(rows)
    stats_path = r42h.download_stats()
    tech_rows, tech_source = r42h.load_technical_rows(rows, Path(player_path), stats_path)

    base = r9.S()
    states = defaultdict(r40c.TeamState)
    base_ledger = r40c.Ledger()
    tech_ledger = r42h.TechnicalLedger()
    pred = []
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
            pred.append(
                {
                    "date": day,
                    "y": r9.actual(row),
                    "raw": raw,
                    "context_features": {**base_cf, **tech_cf},
                }
            )
            pending_updates.append((row, raw))

        for row, raw in pending_updates:
            fid = str(row["game_id"])
            htid = str(row["home_team"])
            atid = str(row["away_team"])
            hi = player_map.get((fid, htid), [])
            ai = player_map.get((fid, atid), [])
            y = r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"])
            ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                base_ledger.update(
                    hi,
                    hu - he,
                    float(row["home_xg"]) - float(raw["xg_mu_home"]),
                    float(row["away_xg"]) - float(raw["xg_mu_away"]),
                )
                states[htid].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                base_ledger.update(
                    ai,
                    au - ae,
                    float(row["away_xg"]) - float(raw["xg_mu_away"]),
                    float(row["home_xg"]) - float(raw["xg_mu_home"]),
                )
                states[atid].xis.append(frozenset(pid for pid, _ in ai))
            for rec in tech_rows.get((fid, htid), []):
                tech_ledger.update_row(rec)
            for rec in tech_rows.get((fid, atid), []):
                tech_ledger.update_row(rec)
            base.update(row, raw)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    train = pred[b1:b2]
    baseline_model = r40c.fit_model(train, r42h.BASE_NAMES)
    technical_model = r40c.fit_model(train, r42h.BASE_NAMES + r42h.TECH_NAMES)
    return {
        "rows": rows,
        "base": base,
        "base_ledger": base_ledger,
        "tech_ledger": tech_ledger,
        "baseline_model": baseline_model,
        "technical_model": technical_model,
        "player_sha": player_sha,
        "matched_starters": int(matched_starters),
        "tech_source": tech_source,
        "train_rows": len(train),
        "train_end_date": max(x["date"] for x in train),
        "history_end_date": max(x["date"] for x in rows),
    }


def starter_ids_and_roles(target_summary: dict, side: str):
    res = target_summary["current_season_bridge"]["resolution"][side]
    ids = [int(x) for x in res["resolved_starter_ids"]]
    roles = {}
    for item in res["starters"]:
        pid = int(item["resolution"]["player_id"])
        role = item["input"].get("role")
        if role in r40c.ROLES:
            roles[pid] = role
    if len(ids) != 11 or len(set(ids)) != 11:
        raise RuntimeError(f"invalid frozen bridge XI side={side}: {ids}")
    return ids, roles


def apply_confirmed_availability(target_summary: dict, side: str, ids: list[int]) -> tuple[list[int], list[dict]]:
    selected = list(ids)
    scenario = target_summary["raw_availability_scenarios"]["confirmed_out_only"]
    changes = list(scenario[f"{side}_changes"])
    for ch in changes:
        if not ch.get("hit_expected_xi"):
            continue
        excluded = int(ch["excluded_player_id"])
        if excluded not in selected:
            raise RuntimeError(f"confirmed exclusion expected XI mismatch side={side} excluded={excluded}")
        selected.remove(excluded)
        repl = ch.get("replacement_player_id")
        if repl is not None:
            repl = int(repl)
            if repl in selected:
                raise RuntimeError(f"replacement duplicates selected XI side={side} replacement={repl}")
            selected.append(repl)
    if len(selected) != 11 or len(set(selected)) != 11:
        raise RuntimeError(f"confirmed availability XI invalid side={side}: {selected}")
    return selected, changes


def side_spec(pids: list[int], role_overrides: dict[int, str]) -> dict:
    return {
        "legacy_expected": tuple(pids),
        "opener_xi": tuple(pids),
        "opener_roles": {int(k): v for k, v in role_overrides.items()},
    }


def to_simple(p: dict) -> dict:
    return {
        "home": float(p["p_home"]),
        "draw": float(p["p_draw"]),
        "away": float(p["p_away"]),
        "top1": ["home", "draw", "away"][int(p["top1"])],
    }


def max_prob_diff(a: dict, b: dict) -> float:
    return max(abs(float(a[k]) - float(b[k])) for k in ("home", "draw", "away"))


def run():
    if abs(float(r42k.TECH_DELTA_ALPHA) - TECH_ALPHA) > 1e-12:
        raise RuntimeError(f"R42K alpha drift: {r42k.TECH_DELTA_ALPHA}")

    r42f = json.loads(R42F_SUMMARY.read_text(encoding="utf-8"))
    r42k_summary = json.loads(R42K_SUMMARY.read_text(encoding="utf-8"))
    if not r42f["gate"]["passed"]:
        raise RuntimeError("R42F lineup bridge gate is not passed")
    if not r42k_summary["gate"]["passed"]:
        raise RuntimeError("R42K half-shrink confirmation gate is not passed")
    if abs(float(r42k_summary["governance"]["technical_delta_alpha"]) - TECH_ALPHA) > 1e-12:
        raise RuntimeError("R42K summary alpha mismatch")

    r42d = fetch_json(R42D_URL, R42D_BLOB_SHA, "football3-r42l-r42d-freeze")
    r42e = fetch_json(R42E_URL, R42E_BLOB_SHA, "football3-r42l-r42e-lock")
    if r42d["status"] != "COMPLETE" or r42d["target_count"] != 3:
        raise RuntimeError("R42D frozen aggregate is not the expected three-target prematch snapshot")
    if r42e["status"] != "LOCKED_PREMATCH":
        raise RuntimeError("R42E source is not a prematch lock")
    if r42e["source"]["r42d_generated_head"] != R42D_COMMIT:
        raise RuntimeError("R42E does not point to the frozen R42D head")

    engine = replay_and_fit()
    r42e_by_key = {key_of(x["target"]): x for x in r42e["targets"]}
    lock_time = datetime.now(timezone.utc)
    outputs = []

    for target_summary in r42d["targets"]:
        target = target_summary["target"]
        k = key_of(target)
        if k not in r42e_by_key:
            raise RuntimeError(f"R42D target missing from R42E lock: {k}")
        e_lock = r42e_by_key[k]
        kickoff = datetime.fromisoformat(str(target["kickoff_at_utc"]).replace("Z", "+00:00"))
        if not lock_time < kickoff:
            raise RuntimeError(f"cannot create prospective lock after kickoff: target={k} lock={lock_time.isoformat()}")

        home_ids, home_roles = starter_ids_and_roles(target_summary, "home")
        away_ids, away_roles = starter_ids_and_roles(target_summary, "away")
        home_final, home_changes = apply_confirmed_availability(target_summary, "home", home_ids)
        away_final, away_changes = apply_confirmed_availability(target_summary, "away", away_ids)

        hs = side_spec(home_final, home_roles)
        a_s = side_spec(away_final, away_roles)
        hbase = r42g.snapshot_values(hs, engine["base_ledger"])
        abase = r42g.snapshot_values(a_s, engine["base_ledger"])
        htech = r42h.freeze_tech_snapshot(hs, engine["tech_ledger"])
        atech = r42h.freeze_tech_snapshot(a_s, engine["tech_ledger"])
        bridge_cf = r42g.context_from_pids(home_final, away_final, hbase, abase)
        tech_cf = r42h.technical_context(home_final, away_final, htech, atech)

        home_tid = str(target_summary["current_season_bridge"]["resolution"]["home"]["team_id"])
        away_tid = str(target_summary["current_season_bridge"]["resolution"]["away"]["team_id"])
        target_row = {
            "date": str(target["kickoff_at_utc"])[:10],
            "game_id": f"R42L::{target['home_team']}::{target['away_team']}::{target['kickoff_at_utc']}",
            "competition_id": "1",
            "home_team": home_tid,
            "away_team": away_tid,
        }
        raw = engine["base"].pred(target_row)
        baseline_p = r42h.model_prob(
            engine["baseline_model"],
            raw,
            bridge_cf,
            r42h.BASE_NAMES,
        )
        full_p = r42h.model_prob(
            engine["technical_model"],
            raw,
            {**bridge_cf, **tech_cf},
            r42h.BASE_NAMES + r42h.TECH_NAMES,
        )
        half_p = r42k.blend_prob(baseline_p, full_p, TECH_ALPHA)

        baseline_simple = to_simple(baseline_p)
        full_simple = to_simple(full_p)
        half_simple = to_simple(half_p)
        prior_locked = e_lock["R42E_primary_current_season_bridge_plus_confirmed_availability"]
        diff = max_prob_diff(baseline_simple, prior_locked)
        if diff > PREMATCH_BASELINE_TOL:
            raise RuntimeError(
                f"R42L baseline does not reproduce R42E locked bridge target={k} max_abs_diff={diff}"
            )

        outputs.append(
            {
                "target": target,
                "team_ids": {"home": home_tid, "away": away_tid},
                "frozen_current_season_bridge_xi": {
                    "home_before_confirmed_availability": home_ids,
                    "away_before_confirmed_availability": away_ids,
                    "home_after_confirmed_availability": home_final,
                    "away_after_confirmed_availability": away_final,
                    "home_changes": home_changes,
                    "away_changes": away_changes,
                },
                "technical_coverage": {
                    "tech_known_share_min": float(tech_cf["tech_known_share_min"]),
                },
                "probabilities": {
                    "R42E_locked_primary_reproduced": prior_locked,
                    "R42L_baseline_bridge_confirmed_availability": baseline_simple,
                    "R42L_full_R42H_V1_diagnostic_only": full_simple,
                    "R42L_primary_half_shrink_alpha_0_5": half_simple,
                },
                "delta_primary_half_shrink_vs_baseline": {
                    q: float(half_simple[q] - baseline_simple[q]) for q in ("home", "draw", "away")
                },
                "baseline_reproduction_max_abs_diff": float(diff),
                "actual_result": None,
                "actual_score": None,
                "result_label_accessed": False,
            }
        )

    result = {
        "schema_version": "football3-r42l-r42f-r42h-half-shrink-mw2-prospective-lock-v1",
        "status": "LOCKED_PREMATCH",
        "classification": "TRUE_FORWARD_THREE_MATCH_LOCK_R42F_BRIDGE_R42H_TECH_FIXED_HALF_SHRINK",
        "formal_weight": 0,
        "locked_at_utc": lock_time.isoformat(),
        "question": "Does the frozen R42F current-season lineup bridge plus confirmed PIT availability, augmented by frozen R42H V1 player technical translation at fixed alpha=0.5, improve true-forward 1X2 probability quality and Top1 on the already locked MW2 targets?",
        "governance": {
            "target_count": 3,
            "target_results_accessed": False,
            "target_confirmed_xi_accessed": False,
            "target_postmatch_data_accessed": False,
            "r42d_prematch_snapshot_reused_exact": True,
            "r42e_existing_lock_reused_exact": True,
            "r42f_lineup_bridge_gate_passed": True,
            "r42h_v1_features_and_model_unchanged": True,
            "r42k_half_shrink_gate_passed": True,
            "technical_delta_alpha": TECH_ALPHA,
            "alpha_parameter_search_after_r42k": False,
            "probability_retuning_on_mw2_targets": False,
            "doubtful_primary_treatment": "DO_NOT_EXCLUDE",
            "confirmed_availability_statuses_applied": ["out", "suspended"],
            "reveal_policy": "append_only_after_results; never rewrite this lock",
        },
        "frozen_sources": {
            "r42d_commit": R42D_COMMIT,
            "r42d_summary_git_blob_sha": R42D_BLOB_SHA,
            "r42e_commit": R42E_COMMIT,
            "r42e_lock_git_blob_sha": R42E_BLOB_SHA,
            "r42f_summary_git_blob_sha": "0afd979daf3fe3f1596fede6dda3a5f096ab6a8f",
            "r42k_summary_git_blob_sha": "36de17feb7d3693810d770fd749802af3f5990de",
        },
        "engine": {
            "history_rows": len(engine["rows"]),
            "history_end_date": engine["history_end_date"],
            "model_train_rows": engine["train_rows"],
            "model_train_end_date": engine["train_end_date"],
            "fixture_players_sha256": engine["player_sha"],
            "matched_starter_rows": engine["matched_starters"],
            "technical_source": engine["tech_source"],
            "baseline_reproduction_tolerance": PREMATCH_BASELINE_TOL,
        },
        "targets": outputs,
        "next_gate": {
            "action": "WAIT_FOR_RESULTS_THEN_APPEND_ONLY_SCORE_R42E_BASELINE_VS_R42L_HALF_SHRINK",
            "primary_comparison": "R42L_primary_half_shrink_alpha_0_5 versus R42E_locked_primary_reproduced",
            "metrics": ["Top1 accuracy", "Log Loss", "Brier", "RPS"],
            "no_tuning_from_three_matches": True,
        },
        "limitations": [
            "This lock contains only three true-forward targets, so it can falsify obvious failures but cannot establish statistical significance.",
            "Player technical values are frozen from the historical source ending before the target fixtures; current-season prior-match results/xG are not used to update player strength.",
            "Current-season opener XI membership and confirmed prematch availability come from the already frozen R42D/R42E evidence line.",
            "The fixed alpha=0.5 rule was selected before this lock and is not changed from R42K.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "r42l_mw2_half_shrink_lock.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    p = OUT / "r42l_mw2_half_shrink_lock.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "LOCKED_PREMATCH"
    assert d["classification"] == "TRUE_FORWARD_THREE_MATCH_LOCK_R42F_BRIDGE_R42H_TECH_FIXED_HALF_SHRINK"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["target_count"] == 3
    assert g["target_results_accessed"] is False
    assert g["target_confirmed_xi_accessed"] is False
    assert g["target_postmatch_data_accessed"] is False
    assert g["r42f_lineup_bridge_gate_passed"] is True
    assert g["r42k_half_shrink_gate_passed"] is True
    assert g["technical_delta_alpha"] == 0.5
    assert g["alpha_parameter_search_after_r42k"] is False
    assert g["probability_retuning_on_mw2_targets"] is False
    assert len(d["targets"]) == 3
    for x in d["targets"]:
        assert x["actual_result"] is None and x["actual_score"] is None
        assert x["result_label_accessed"] is False
        assert x["baseline_reproduction_max_abs_diff"] <= PREMATCH_BASELINE_TOL
        for name in (
            "R42E_locked_primary_reproduced",
            "R42L_baseline_bridge_confirmed_availability",
            "R42L_full_R42H_V1_diagnostic_only",
            "R42L_primary_half_shrink_alpha_0_5",
        ):
            p = x["probabilities"][name]
            assert abs(float(p["home"]) + float(p["draw"]) + float(p["away"]) - 1.0) < 1e-10
    print("R42L_MW2_HALF_SHRINK_LOCK_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42l_mw2_forward_lock.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
