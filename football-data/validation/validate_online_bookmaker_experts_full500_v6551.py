#!/usr/bin/env python3
"""V6.55.1 JSON-safe execution driver for unchanged V6.55.0 experiment."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "validation") not in sys.path:
    sys.path.insert(0, str(ROOT / "validation"))

import validate_online_bookmaker_experts_full500_v6550 as v  # noqa: E402

OUT = ROOT / "manifests" / "v6_online_bookmaker_experts_full500_v6551_status.json"


def json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=json_default)


def main() -> int:
    hist, audit = v.build_historical()
    chosen, board = v.historical_select(hist)
    hold = v.run_online(hist, str(chosen["mode"]), float(chosen["eta"]), float(chosen["alpha"]), "2024/25")
    hm, hc = hold["market"], hold["candidate"]
    hold_uplift = 100.0 * (hc["top1"] - hm["top1"])
    hold_log_delta = hc["logloss"] - hm["logloss"]
    hold_rps_delta = hc["rps"] - hm["rps"]
    selection = chosen["selection"]
    proper = bool(
        selection["logloss_delta"] <= v.PROPER_TOL
        and selection["rps_delta"] <= v.PROPER_TOL
        and hold_log_delta <= v.PROPER_TOL
        and hold_rps_delta <= v.PROPER_TOL
    )
    hist_gate = bool(
        selection["uplift_pp"] > 0.0
        and hold_uplift >= v.HOLDOUT_REQUIRED_UPLIFT_PP - 1e-12
        and proper
    )

    payload: dict[str, Any] = {
        "schema_version": "V6.55.1-online-bookmaker-experts-full500-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "algorithm_relation": "V6.55.0 unchanged; V6.55.1 only fixes JSON serialization",
        "governance": {
            "same_date_predictions_before_updates": True,
            "weights_use_prior_completed_results_only": True,
            "selection_season": "2023/24",
            "holdout_season": "2024/25",
            "A100_values_used_for_selection": False,
            "B_CONFIRM300_labels_read": False,
            "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": audit,
        "grid": {"modes": v.MODES, "etas": v.ETAS, "alphas": v.ALPHAS},
        "selected": chosen,
        "holdout_2024_25": {
            "market": hm,
            "candidate": hc,
            "uplift_pp": hold_uplift,
            "logloss_delta": hold_log_delta,
            "rps_delta": hold_rps_delta,
        },
        "proper_guard": proper,
        "historical_gate": hist_gate,
        "selection_leaderboard_top10": board[:10],
    }

    if not hist_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_HOLDOUT_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(dumps(payload) + "\n", encoding="utf-8")
        print(dumps(payload))
        return 0

    arows, _ = v.load_a100()
    ares = v.score_a100_with_warm_history(
        hist, arows, str(chosen["mode"]), float(chosen["eta"]), float(chosen["alpha"])
    )
    am, ac = ares["market"], ares["candidate"]
    au = 100.0 * (ac["top1"] - am["top1"])
    gate = {
        "required_candidate_hits": 63,
        "required_uplift_vs_market_pp": 3.0,
        "market_hits": am["hits"],
        "candidate_hits": ac["hits"],
        "uplift_vs_market_pp": au,
        "top1_gate": ac["hits"] >= 63,
        "uplift_gate": au >= 3.0 - 1e-12,
        "proper_score_guard": ac["logloss"] <= am["logloss"] + v.PROPER_TOL
        and ac["rps"] <= am["rps"] + v.PROPER_TOL,
    }
    gate["A_FAST100_passed"] = bool(
        gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"]
    )
    payload["A_FAST100"] = {
        "status": "SCORED_AFTER_HISTORICAL_GATE",
        "market": am,
        "candidate": ac,
        "gate": gate,
    }
    payload["next_step"] = "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(dumps(payload) + "\n", encoding="utf-8")
    print(dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
