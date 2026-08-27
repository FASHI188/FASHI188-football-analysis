#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
INPUT = HERE / "inputs" / "current_season_prior_xi_v1.json"
R42B_DIR = HERE.parent / "r42b_mw2_live_availability_shadow"
sys.path.insert(0, str(R42B_DIR))
import run_r42b_mw2_shadow as r42b  # noqa: E402

r42 = r42b.r42
R42B_PIT = R42B_DIR / "inputs" / "prematch_events_v2_snapshot.jsonl"
LEGACY = R42B_DIR / "results" / "ENG_PL_MW2_AVL_ARS" / "summary_r42_shadow.json"

ORIG_REPLAY = r42.replay_history
ORIG_LOAD_PLAYERS = r42.load_player_names
ORIG_RANKED = r42.ranked_expected

_HISTORY = None
_TARGET_ROW = None
_BRIDGE_META = {}


def load_input():
    d = json.loads(INPUT.read_text(encoding="utf-8"))
    assert d["status"] == "FROZEN_PREMATCH_INPUT"
    return d


def replay_history_bridge():
    global _HISTORY
    _HISTORY = ORIG_REPLAY()
    return _HISTORY


def load_target_fixture_bridge(tmp: Path):
    global _TARGET_ROW
    row, meta, fp, tp = r42b.load_target_fixture_pair_fixed(tmp)
    _TARGET_ROW = row
    return row, meta, fp, tp


def _resolve_one(name, role, state, ledger, by_id, by_surname, by_compact):
    recent = r42.recent_team_players(state)
    res = r42.resolve_player(name, recent, ledger, by_id, by_surname, by_compact)
    if not res.get("resolved"):
        exact = list(by_compact.get(r42.compact(name), []))
        scored = sorted(
            [(int(ledger.n.get(pid, 0)), int(pid), by_id.get(pid)) for pid in exact],
            reverse=True,
        )
        if len(scored) == 1 or (len(scored) > 1 and scored[0][0] > scored[1][0]):
            n, pid, dataset_name = scored[0]
            res = {
                "resolved": True,
                "live_name": name,
                "player_id": pid,
                "dataset_name": dataset_name,
                "score": None,
                "margin_to_second": None,
                "in_recent_team_xis": bool(pid in recent),
                "ledger_matches": n,
                "candidates": [
                    {"player_id": x[1], "name": x[2], "ledger_matches": x[0]}
                    for x in scored[:5]
                ],
                "bridge_fallback": "unique_or_history_dominant_exact_compact_name",
            }
    if not res.get("resolved") or res.get("player_id") is None:
        raise RuntimeError(f"bridge player unresolved: {name} role={role} result={res}")
    pid = int(res["player_id"])
    if role and ledger.last_role.get(pid) not in r42.r40c.ROLES:
        ledger.last_role[pid] = role
    return pid, res


def load_player_names_bridge(tmp: Path):
    global _BRIDGE_META
    by_id, by_surname, by_compact, players_sha, path = ORIG_LOAD_PLAYERS(tmp)
    if _HISTORY is None or _TARGET_ROW is None:
        raise RuntimeError("bridge ordering failure")
    _, _, _, states, ledger, _ = _HISTORY
    d = load_input()
    _BRIDGE_META = {}
    for side in ("home", "away"):
        team_id = _TARGET_ROW[f"{side}_team"]
        state = states[team_id]
        cfg = d["prior_xi"][side]
        starters = []
        bench = []
        starter_meta = []
        bench_meta = []
        for item in cfg["starters"]:
            pid, res = _resolve_one(item["name"], item["role"], state, ledger, by_id, by_surname, by_compact)
            starters.append(pid)
            starter_meta.append({"input": item, "resolution": res})
        for item in cfg.get("known_current_bench", []):
            try:
                pid, res = _resolve_one(item["name"], item["role"], state, ledger, by_id, by_surname, by_compact)
            except RuntimeError as exc:
                bench_meta.append({"input": item, "resolved": False, "error": str(exc)})
                continue
            bench.append(pid)
            bench_meta.append({"input": item, "resolved": True, "resolution": res})
        if len(starters) != 11 or len(set(starters)) != 11:
            raise RuntimeError(f"bridge starters invalid side={side}: {starters}")
        state._current_season_bridge_expected = tuple(starters)
        state._current_season_bridge_bench = tuple(pid for pid in bench if pid not in starters)
        _BRIDGE_META[side] = {
            "team_id": team_id,
            "team": cfg["team"],
            "prior_fixture": cfg["prior_fixture"],
            "prior_kickoff_at_utc": cfg["prior_kickoff_at_utc"],
            "source_urls": cfg["source_urls"],
            "starters": starter_meta,
            "bench": bench_meta,
            "resolved_starter_ids": starters,
            "resolved_bench_ids": list(state._current_season_bridge_bench),
        }
    return by_id, by_surname, by_compact, players_sha, path


def ranked_expected_bridge(st):
    bridge = getattr(st, "_current_season_bridge_expected", None)
    if not bridge:
        return ORIG_RANKED(st)
    expected = list(bridge)
    bench = list(getattr(st, "_current_season_bridge_bench", ()))
    _, legacy_ranked = ORIG_RANKED(st)
    ranked = []
    seen = set()
    for pid, weight in [(x, 2.0) for x in expected] + [(x, 1.0) for x in bench] + list(legacy_ranked):
        pid = int(pid)
        if pid in seen:
            continue
        seen.add(pid)
        ranked.append((pid, float(weight)))
    return expected, ranked


def delta(a, b):
    return {k: float(b[k] - a[k]) for k in ("home", "draw", "away")}


def run():
    d = load_input()
    target = d["target"]
    r42.TARGET = dict(target)
    r42.LEDGER_PATH = R42B_PIT
    r42.OUT = OUT / "raw_bridge"
    r42.replay_history = replay_history_bridge
    r42.load_target_fixture = load_target_fixture_bridge
    r42.load_player_names = load_player_names_bridge
    r42.ranked_expected = ranked_expected_bridge
    r42.run()

    raw = json.loads((r42.OUT / "summary_r42_shadow.json").read_text(encoding="utf-8"))
    legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
    rawp = raw["shadow_probabilities"]
    legp = legacy["shadow_probabilities"]
    bridge_pre = rawp["R40C_before_live_availability"]
    bridge_out = rawp["R42_confirmed_out_only"]
    legacy_pre = legp["R40C_before_live_availability"]

    gomes = None
    for x in raw["entity_resolution"]:
        if x["availability_status"] == "suspended" and r42.compact(x["resolution"].get("dataset_name") or "") == r42.compact("Joao Gomes"):
            gomes = x
            break
    if gomes is None:
        raise RuntimeError("Joao Gomes suspended event not resolved in R42C bridge")

    summary = {
        "schema_version": "football3-r42c-current-season-xi-bridge-v1",
        "status": "COMPLETE",
        "classification": "PROSPECTIVE_CURRENT_SEASON_XI_BRIDGE_SHADOW_ONLY_NOT_FORMAL_PREDICTION",
        "formal_weight": 0,
        "target": target,
        "governance": {
            **d["governance"],
            "result_labels_for_target_accessed": False,
            "current_target_confirmed_xi_used": False,
            "prior_completed_match_xi_membership_only": True,
            "prior_match_score_xg_not_applied_to_player_ledger": True,
            "availability_weight_refit": False,
            "probability_retuning": False,
            "formal_probability_change_allowed": False,
        },
        "bridge_input": d,
        "bridge_resolution": _BRIDGE_META,
        "legacy_r42b": {
            "legacy_expected_xi": legacy["strict_prior_expected_xi"],
            "gomes_in_legacy_expected_xi": False,
        },
        "r42c_bridge": {
            "bridge_expected_xi": raw["strict_prior_expected_xi"],
            "gomes_resolution": gomes,
            "availability_scenarios": raw["availability_scenarios"],
            "gomes_in_bridge_expected_xi": bool(gomes["in_strict_prior_expected_xi"]),
        },
        "shadow_probabilities": {
            "K1_without_live_player_layer": rawp["K1_without_live_player_layer"],
            "R40C_legacy_expected_xi": legacy_pre,
            "R42C_current_season_bridge_before_availability": bridge_pre,
            "R42C_current_season_bridge_confirmed_out": bridge_out,
            "delta_bridge_vs_legacy": delta(legacy_pre, bridge_pre),
            "delta_confirmed_out_vs_bridge_pre": delta(bridge_pre, bridge_out),
            "delta_confirmed_out_vs_legacy": delta(legacy_pre, bridge_out),
        },
        "mechanism_result": {
            "current_season_bridge_fixed_summer_transfer_blind_spot": bool(gomes["in_strict_prior_expected_xi"]),
            "confirmed_suspension_hit_expected_xi": bool(raw["availability_scenarios"]["confirmed_out_only"]["home_changes"][0]["hit_expected_xi"]),
            "replacement_player_id": raw["availability_scenarios"]["confirmed_out_only"]["home_changes"][0].get("replacement_player_id"),
            "replacement_role": raw["availability_scenarios"]["confirmed_out_only"]["home_changes"][0].get("replacement_role"),
        },
        "limitations": [
            "This is a mechanism shadow, not a validated probability model.",
            "Only one completed current-season XI per side is used in this first bridge proof; lineup persistence is not yet estimated over multiple current-season matches.",
            "The historical player-strength ledger still ends at the frozen 2026-07-04 snapshot; current-season completed-match outcomes are deliberately not used to update player strength in this isolation test.",
            "Known current bench is partial for Aston Villa and is used only as a replacement pool before falling back to legacy historical candidates.",
        ],
        "next_research_gate": "Generalize the current-season bridge to every prospective fixture using all completed prior-season-current-season XI observations available before kickoff, then accumulate locked forward outcomes before estimating any promotion value.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42c_bridge.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r42c_bridge.json").read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    g = s["governance"]
    assert g["target_current_match_xi_used"] is False
    assert g["result_labels_for_target_accessed"] is False
    assert g["prior_completed_match_xi_membership_only"] is True
    assert g["prior_match_score_xg_not_applied_to_player_ledger"] is True
    assert g["availability_weight_refit"] is False and g["probability_retuning"] is False
    for side in ("home", "away"):
        b = s["bridge_resolution"][side]
        assert len(b["resolved_starter_ids"]) == 11
        assert len(set(b["resolved_starter_ids"])) == 11
    assert s["r42c_bridge"]["gomes_in_bridge_expected_xi"] is True
    assert s["mechanism_result"]["current_season_bridge_fixed_summer_transfer_blind_spot"] is True
    assert s["mechanism_result"]["confirmed_suspension_hit_expected_xi"] is True
    for name in (
        "K1_without_live_player_layer",
        "R40C_legacy_expected_xi",
        "R42C_current_season_bridge_before_availability",
        "R42C_current_season_bridge_confirmed_out",
    ):
        p = s["shadow_probabilities"][name]
        assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-10
    print("R42C_CURRENT_SEASON_XI_BRIDGE_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42c_bridge.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
