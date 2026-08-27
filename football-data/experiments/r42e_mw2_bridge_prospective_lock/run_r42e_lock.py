#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "r42d_mw2_current_season_bridge" / "results" / "summary_r42d_mw2.json"
OUT = HERE / "results"
LOCK = OUT / "r42e_mw2_bridge_lock.json"
SOURCE_HEAD = "102ea1729b97b4944fc280d73d6f67e9133b0df9"
SOURCE_ARTIFACT_ID = 9652258824
SOURCE_ARTIFACT_DIGEST = "sha256:a47a40b5edc0ab4c4484cdb9e191f347f8c70e9839d42d1c37482dd0647e218d"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_dt(x: str) -> datetime:
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def build_lock():
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert src["status"] == "COMPLETE"
    assert src["formal_weight"] == 0
    assert src["governance"]["target_results_accessed"] is False
    assert src["governance"]["target_confirmed_xi_accessed"] is False

    locked_at = datetime.now(timezone.utc)
    targets = []
    for x in src["targets"]:
        p = x["shadow_probabilities"]
        target = x["target"]
        kickoff = parse_dt(target["kickoff_at_utc"])
        if not locked_at < kickoff:
            raise RuntimeError(f"cannot create prospective lock at/after kickoff: {target}")
        targets.append({
            "target": target,
            "K1_baseline": p["K1_without_live_player_layer"],
            "R40C_legacy_expected_xi": p["R40C_legacy_expected_xi"],
            "R42E_primary_current_season_bridge_plus_confirmed_availability": p["R42D_bridge_confirmed_out"],
            "R42E_stress_doubtful_as_out": p["R42D_bridge_confirmed_plus_doubtful_as_out"],
            "bridge_before_availability": p["R42D_current_season_bridge_before_availability"],
            "availability_event_impacts": x["availability_event_impacts"],
            "actual_result": None,
            "actual_score": None,
            "result_label_accessed": False,
        })

    return {
        "schema_version": "football3-r42e-mw2-bridge-prospective-lock-v1",
        "status": "LOCKED_PREMATCH",
        "classification": "PROSPECTIVE_THREE_MATCH_SHADOW_LOCK_NO_RESULT_LABELS",
        "formal_weight": 0,
        "locked_at_utc": locked_at.isoformat(),
        "source": {
            "r42d_generated_head": SOURCE_HEAD,
            "r42d_summary_sha256": sha256(SOURCE),
            "r42d_artifact_id": SOURCE_ARTIFACT_ID,
            "r42d_artifact_digest": SOURCE_ARTIFACT_DIGEST,
        },
        "policy": {
            "primary_probability": "R42E_primary_current_season_bridge_plus_confirmed_availability",
            "confirmed_availability_statuses_applied": ["out", "suspended"],
            "doubtful_primary_treatment": "DO_NOT_EXCLUDE",
            "doubtful_stress_scenario_retained": True,
            "no_parameter_search_after_r42d": True,
            "no_target_confirmed_xi": True,
            "no_target_result": True,
            "reveal_must_be_separate_append_only_evidence": True,
        },
        "targets": targets,
    }


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        existing = json.loads(LOCK.read_text(encoding="utf-8"))
        if existing.get("status") != "LOCKED_PREMATCH":
            raise RuntimeError("existing lock has unexpected status")
        print(json.dumps(existing, indent=2, ensure_ascii=False))
        return
    lock = build_lock()
    LOCK.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(lock, indent=2, ensure_ascii=False))


def verify():
    d = json.loads(LOCK.read_text(encoding="utf-8"))
    assert d["status"] == "LOCKED_PREMATCH" and d["formal_weight"] == 0
    assert len(d["targets"]) == 3
    assert d["policy"]["doubtful_primary_treatment"] == "DO_NOT_EXCLUDE"
    assert d["policy"]["no_parameter_search_after_r42d"] is True
    locked_at = parse_dt(d["locked_at_utc"])
    for x in d["targets"]:
        assert x["actual_result"] is None and x["actual_score"] is None
        assert x["result_label_accessed"] is False
        assert locked_at < parse_dt(x["target"]["kickoff_at_utc"])
        for name in (
            "K1_baseline",
            "R40C_legacy_expected_xi",
            "R42E_primary_current_season_bridge_plus_confirmed_availability",
            "R42E_stress_doubtful_as_out",
            "bridge_before_availability",
        ):
            p = x[name]
            assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-10
    print("R42E_MW2_BRIDGE_PROSPECTIVE_LOCK_VERIFY_PASS")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42e_lock.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
