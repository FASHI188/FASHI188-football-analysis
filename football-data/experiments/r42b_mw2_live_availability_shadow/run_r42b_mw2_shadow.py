#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42_DIR = HERE.parent / "r42_live_availability_shadow"
sys.path.insert(0, str(R42_DIR))
import run_r42_shadow_r1 as r42r1  # noqa: E402

r42 = r42r1.r42
PIT_SNAPSHOT = HERE / "inputs" / "prematch_events_v2_snapshot.jsonl"
PIT_SNAPSHOT_BLOB_SHA = "0279012a5c3aba8cf64148061d1c9dc7a1a3d581"
PIT_SOURCE_HEAD = "eed32f3f105ecda107b8ea49da779627dcc7d9c2"

TARGETS = [
    {
        "key": "ENG_PL_MW2_TOT_NEW",
        "competition": "Premier League",
        "home_team": "Tottenham Hotspur",
        "away_team": "Newcastle United",
        "kickoff_at_utc": "2026-08-29T16:30:00Z",
        "target_record_ids": ["2bce72ff23c04760e52b94bb634ce5a3dadcbd0e77a09bb10972b5adcb94c5e0"],
    },
    {
        "key": "ENG_PL_MW2_AVL_ARS",
        "competition": "Premier League",
        "home_team": "Aston Villa",
        "away_team": "Arsenal",
        "kickoff_at_utc": "2026-08-31T19:00:00Z",
        "target_record_ids": ["d979014ce914567c93d85196c4b3700875d97b124956ca781002e06a32aad0d9"],
    },
]


def run_one(target: dict):
    target_dir = OUT / target["key"]
    target_dir.mkdir(parents=True, exist_ok=True)
    r42.TARGET = {
        "competition": target["competition"],
        "home_team": target["home_team"],
        "away_team": target["away_team"],
        "kickoff_at_utc": target["kickoff_at_utc"],
    }
    r42.LEDGER_PATH = PIT_SNAPSHOT
    r42.OUT = target_dir
    r42.load_target_fixture = r42r1.load_target_fixture_fixed
    r42.run()
    return json.loads((target_dir / "summary_r42_shadow.json").read_text(encoding="utf-8"))


def run():
    if not PIT_SNAPSHOT.is_file():
        raise RuntimeError(f"missing frozen PIT snapshot: {PIT_SNAPSHOT}")
    rows = [json.loads(x) for x in PIT_SNAPSHOT.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = {x["record_id"] for x in rows}
    assert len(rows) == 7, len(rows)
    for target in TARGETS:
        assert set(target["target_record_ids"]).issubset(ids)

    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for target in TARGETS:
        results.append({"target": target, "summary": run_one(target)})

    aggregate = {
        "schema_version": "football3-r42b-mw2-live-availability-shadow-v1",
        "status": "COMPLETE",
        "classification": "PROSPECTIVE_PIT_MECHANISM_SHADOW_ONLY_NOT_FORMAL_PREDICTION",
        "formal_weight": 0,
        "governance": {
            "pit_snapshot_source_head": PIT_SOURCE_HEAD,
            "pit_snapshot_blob_sha": PIT_SNAPSHOT_BLOB_SHA,
            "pit_snapshot_records": len(rows),
            "result_labels_accessed": False,
            "postmatch_target_data_used": False,
            "current_match_confirmed_xi_used": False,
            "absence_weight_refit": False,
            "probability_retuning": False,
            "same_R42_mechanism_reused_without_parameter_search": True
        },
        "targets": results,
        "interpretation_rule": "These are shadow diagnostics. A PIT event only changes the expected-XI player layer when its resolved player is present in the strict-prior expected XI; doubtful is shown separately from confirmed out/suspended and is never silently upgraded."
    }
    (OUT / "summary_r42b_mw2.json").write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42b_mw2.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["pit_snapshot_records"] == 7
    assert g["result_labels_accessed"] is False and g["postmatch_target_data_used"] is False
    assert g["absence_weight_refit"] is False and g["probability_retuning"] is False
    assert len(d["targets"]) == 2
    for item in d["targets"]:
        t = item["target"]
        s = item["summary"]
        assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
        assert s["governance"]["result_label_accessed"] is False
        assert s["governance"]["target_postmatch_data_used"] is False
        assert s["source"]["target_fixture"]["official_current_kickoff_utc"].startswith(t["kickoff_at_utc"].replace("Z", ""))
        live_ids = {x["record_id"] for x in s["entity_resolution"]}
        assert set(t["target_record_ids"]).issubset(live_ids)
        probs = s["shadow_probabilities"]
        for name in ("K1_without_live_player_layer", "R40C_before_live_availability", "R42_confirmed_out_only", "R42_confirmed_out_plus_doubtful_as_out"):
            p = probs[name]
            assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-10
    print("R42B_MW2_LIVE_AVAILABILITY_SHADOW_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42b_mw2_shadow.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
