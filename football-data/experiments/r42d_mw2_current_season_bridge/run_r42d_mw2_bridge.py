#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42C_DIR = HERE.parent / "r42c_current_season_xi_bridge"
R42B_DIR = HERE.parent / "r42b_mw2_live_availability_shadow"
R42_DIR = HERE.parent / "r42_live_availability_shadow"
sys.path.insert(0, str(R42C_DIR))
import run_r42c_bridge as r42c  # noqa: E402

r42 = r42c.r42
ORIG_R42C_RESOLVE_ONE = r42c._resolve_one

TARGETS = [
    {
        "key": "ENG_PL_MW2_CRY_MCI",
        "input": HERE / "inputs" / "crystal_palace_mancity_prior_xi.json",
        "legacy": R42_DIR / "results" / "summary_r42_shadow.json",
        "must_hit_bridge_expected": ["Chadi Riad"],
    },
    {
        "key": "ENG_PL_MW2_TOT_NEW",
        "input": HERE / "inputs" / "tottenham_newcastle_prior_xi.json",
        "legacy": R42B_DIR / "results" / "ENG_PL_MW2_TOT_NEW" / "summary_r42_shadow.json",
        "must_hit_bridge_expected": ["William Osula"],
    },
    {
        "key": "ENG_PL_MW2_AVL_ARS",
        "input": R42C_DIR / "inputs" / "current_season_prior_xi_v1.json",
        "legacy": R42B_DIR / "results" / "ENG_PL_MW2_AVL_ARS" / "summary_r42_shadow.json",
        "must_hit_bridge_expected": ["Joao Gomes"],
    },
]


def compact(x: str) -> str:
    return r42.compact(x)


def zero_history_pid(name: str) -> int:
    # Stable negative namespace cannot collide with provider player ids.
    return -int(hashlib.sha256(compact(name).encode("utf-8")).hexdigest()[:12], 16)


def resolve_one_general(name, role, state, ledger, by_id, by_surname, by_compact):
    """Prefer audited/history-backed identity; represent genuinely unseen current players neutrally.

    Completed-current-season XI membership is itself strong evidence that a player belongs to the
    current team. If the frozen July player master cannot map that current player uniquely, we do
    not guess an old provider id. Instead a deterministic negative id is created with zero historical
    strength and the observed role. This fixes roster freshness without fabricating performance.
    """
    try:
        return ORIG_R42C_RESOLVE_ONE(name, role, state, ledger, by_id, by_surname, by_compact)
    except RuntimeError as exc:
        pid = zero_history_pid(name)
        if pid in by_id and compact(by_id[pid]) != compact(name):
            raise RuntimeError(f"synthetic current-player id collision: {name} -> {pid}")
        by_id[pid] = name
        by_compact.setdefault(compact(name), []).append(pid)
        if role:
            ledger.last_role[pid] = role
        return pid, {
            "resolved": True,
            "live_name": name,
            "player_id": pid,
            "dataset_name": name,
            "score": None,
            "margin_to_second": None,
            "in_recent_team_xis": False,
            "ledger_matches": 0,
            "candidates": [],
            "bridge_fallback": "completed_current_season_xi_zero_history_identity",
            "historical_strength_prior": "ZERO_NEUTRAL",
            "original_resolution_error": str(exc),
        }


def prob_delta(a, b):
    return {k: float(b[k] - a[k]) for k in ("home", "draw", "away")}


def event_name(x):
    res = x.get("resolution") or {}
    return res.get("dataset_name") or res.get("live_name") or ""


def run_target(spec: dict):
    d = json.loads(Path(spec["input"]).read_text(encoding="utf-8"))
    legacy = json.loads(Path(spec["legacy"]).read_text(encoding="utf-8"))
    target_dir = OUT / spec["key"]
    raw_dir = target_dir / "raw_bridge"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Reset bridge globals for each independent prospective target.
    r42c.INPUT = Path(spec["input"])
    r42c.OUT = target_dir
    r42c.LEGACY = Path(spec["legacy"])
    r42c._HISTORY = None
    r42c._TARGET_ROW = None
    r42c._BRIDGE_META = {}
    r42c._resolve_one = resolve_one_general

    r42.TARGET = dict(d["target"])
    r42.LEDGER_PATH = R42B_DIR / "inputs" / "prematch_events_v2_snapshot.jsonl"
    r42.OUT = raw_dir
    r42.replay_history = r42c.replay_history_bridge
    r42.load_target_fixture = r42c.load_target_fixture_bridge
    r42.load_player_names = r42c.load_player_names_bridge
    r42.ranked_expected = r42c.ranked_expected_bridge
    r42.run()

    raw = json.loads((raw_dir / "summary_r42_shadow.json").read_text(encoding="utf-8"))
    legacy_p = legacy["shadow_probabilities"]
    raw_p = raw["shadow_probabilities"]
    legacy_pre = legacy_p["R40C_before_live_availability"]
    bridge_pre = raw_p["R40C_before_live_availability"]
    bridge_confirmed = raw_p["R42_confirmed_out_only"]
    bridge_all = raw_p["R42_confirmed_out_plus_doubtful_as_out"]

    impacts = []
    for x in raw["entity_resolution"]:
        impacts.append({
            "record_id": x["record_id"],
            "player": event_name(x),
            "live_name": (x.get("resolution") or {}).get("live_name"),
            "status": x["availability_status"],
            "role": x.get("role"),
            "resolved": bool((x.get("resolution") or {}).get("resolved")),
            "in_bridge_expected_xi": bool(x.get("in_strict_prior_expected_xi")),
            "historical_matches": int(((x.get("player_strength") or {}).get("matches") or 0)),
        })

    starter_resolution = r42c._BRIDGE_META
    unseen = []
    for side in ("home", "away"):
        for x in starter_resolution[side]["starters"]:
            res = x["resolution"]
            if res.get("bridge_fallback") == "completed_current_season_xi_zero_history_identity":
                unseen.append({"side": side, "name": x["input"]["name"], "role": x["input"]["role"], "player_id": res["player_id"]})

    out = {
        "schema_version": "football3-r42d-target-current-season-bridge-v1",
        "status": "COMPLETE",
        "classification": "PROSPECTIVE_CURRENT_SEASON_XI_BRIDGE_SHADOW_ONLY_NOT_FORMAL_PREDICTION",
        "formal_weight": 0,
        "target": d["target"],
        "governance": {
            "target_current_match_xi_used": False,
            "target_result_used": False,
            "prior_match_results_used_for_player_strength_update": False,
            "prior_completed_match_xi_membership_only": True,
            "availability_weight_refit": False,
            "probability_retuning": False,
            "formal_probability_change_allowed": False,
            "unseen_current_players_receive_zero_neutral_history_prior": True,
        },
        "current_season_bridge": {
            "input_file": str(spec["input"]),
            "resolution": starter_resolution,
            "zero_history_current_players": unseen,
            "zero_history_current_player_count": len(unseen),
        },
        "availability_event_impacts": impacts,
        "shadow_probabilities": {
            "K1_without_live_player_layer": raw_p["K1_without_live_player_layer"],
            "R40C_legacy_expected_xi": legacy_pre,
            "R42D_current_season_bridge_before_availability": bridge_pre,
            "R42D_bridge_confirmed_out": bridge_confirmed,
            "R42D_bridge_confirmed_plus_doubtful_as_out": bridge_all,
            "delta_bridge_vs_legacy": prob_delta(legacy_pre, bridge_pre),
            "delta_confirmed_out_vs_bridge_pre": prob_delta(bridge_pre, bridge_confirmed),
            "delta_all_out_vs_bridge_pre": prob_delta(bridge_pre, bridge_all),
        },
        "raw_availability_scenarios": raw["availability_scenarios"],
        "must_hit_bridge_expected": spec["must_hit_bridge_expected"],
        "limitations": [
            "Shadow mechanism only; R40C itself is not formally promoted.",
            "One completed current-season XI per team is used; persistence over multiple current-season matches is not yet estimated.",
            "Current-season prior-match scores/xG are deliberately not used to update player strength in this isolation test.",
            "A current player absent from the frozen July player master is represented with zero neutral historical strength rather than guessed onto another identity.",
        ],
    }
    (target_dir / "summary_r42d_target.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [run_target(x) for x in TARGETS]
    agg = {
        "schema_version": "football3-r42d-mw2-current-season-bridge-v1",
        "status": "COMPLETE",
        "classification": "THREE_TARGET_PROSPECTIVE_CURRENT_SEASON_ROSTER_XI_BRIDGE_MECHANISM_SHADOW",
        "formal_weight": 0,
        "target_count": len(targets),
        "governance": {
            "target_results_accessed": False,
            "target_confirmed_xi_accessed": False,
            "current_season_completed_prior_xi_membership_only": True,
            "prior_match_results_used_for_player_strength_update": False,
            "parameter_search": False,
        },
        "targets": targets,
        "next_research_gate": "Keep this bridge frozen for prospective MW2 scoring; after results and confirmed target XIs are available, score whether bridge membership and PIT availability changes improved locked Top1/probability quality. Do not tune from these three matches."
    }
    (OUT / "summary_r42d_mw2.json").write_text(json.dumps(agg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(agg, indent=2, ensure_ascii=False))


def verify():
    agg = json.loads((OUT / "summary_r42d_mw2.json").read_text(encoding="utf-8"))
    assert agg["status"] == "COMPLETE" and agg["formal_weight"] == 0
    assert agg["target_count"] == 3
    g = agg["governance"]
    assert g["target_results_accessed"] is False and g["target_confirmed_xi_accessed"] is False
    assert g["prior_match_results_used_for_player_strength_update"] is False and g["parameter_search"] is False
    for target in agg["targets"]:
        assert target["status"] == "COMPLETE" and target["formal_weight"] == 0
        impacts = target["availability_event_impacts"]
        for required in target["must_hit_bridge_expected"]:
            hits = [x for x in impacts if compact(x["player"]) == compact(required) or compact(x["live_name"]) == compact(required)]
            assert len(hits) == 1, (required, hits)
            assert hits[0]["in_bridge_expected_xi"] is True, (required, hits[0])
        for name in (
            "K1_without_live_player_layer",
            "R40C_legacy_expected_xi",
            "R42D_current_season_bridge_before_availability",
            "R42D_bridge_confirmed_out",
            "R42D_bridge_confirmed_plus_doubtful_as_out",
        ):
            p = target["shadow_probabilities"][name]
            assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-10
    print("R42D_MW2_CURRENT_SEASON_BRIDGE_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42d_mw2_bridge.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
