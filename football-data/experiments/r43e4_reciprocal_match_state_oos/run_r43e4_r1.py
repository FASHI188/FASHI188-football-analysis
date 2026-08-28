#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import run_r43e4 as m  # noqa: E402


def transport_records_r1(base_half, tech_full, recip_full, alpha=m.RECIP_ALPHA):
    out = []
    keys = ("p_home", "p_draw", "p_away")
    for b, t, r in zip(base_half, tech_full, recip_full):
        if not (b["fixture_id"] == t["fixture_id"] == r["fixture_id"]):
            raise RuntimeError("paired fixture drift")
        pb = np.clip(np.asarray([b["P"][k] for k in keys], dtype=float), 1e-12, 1.0)
        pt = np.clip(np.asarray([t["P"][k] for k in keys], dtype=float), 1e-12, 1.0)
        pr = np.clip(np.asarray([r["P"][k] for k in keys], dtype=float), 1e-12, 1.0)
        z = np.log(pb) + alpha * (np.log(pr) - np.log(pt))
        z -= float(np.max(z))
        p = np.exp(z)
        p /= float(np.sum(p))
        out.append({
            "date": b["date"],
            "fixture_id": b["fixture_id"],
            "y": b["y"],
            "P": m.f5.r9.decorate(p),
        })
    return out


m.transport_records = transport_records_r1


def run_r1():
    result = m.run()
    result["schema_version"] = "football3-r43e4-r1-reciprocal-match-state-oos-v1"
    result["classification"] += "_R1_PRELABEL_PROBABILITY_REPRESENTATION_FIX"
    g = result["governance"]
    g["r1_runtime_representation_fix"] = True
    g["r1_reason"] = "attempt1 transport expected array but score records store decorated probability dicts"
    g["attempt1_outcome_metrics_produced"] = False
    g["attempt1_outcome_metrics_inspected"] = False
    g["features_changed_in_r1"] = False
    g["model_hyperparameters_changed_in_r1"] = False
    g["gate_changed_in_r1"] = False
    p = m.OUT / "summary_r43e4_reciprocal_match_state_oos.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("R43E4-R1 representation fix applied")
    return result


def verify_r1():
    m.verify()
    d = json.loads((m.OUT / "summary_r43e4_reciprocal_match_state_oos.json").read_text(encoding="utf-8"))
    assert d["governance"]["r1_runtime_representation_fix"] is True
    assert d["governance"]["attempt1_outcome_metrics_produced"] is False
    assert d["governance"]["attempt1_outcome_metrics_inspected"] is False
    assert d["governance"]["features_changed_in_r1"] is False
    assert d["governance"]["model_hyperparameters_changed_in_r1"] is False
    assert d["governance"]["gate_changed_in_r1"] is False
    print("R43E4-R1 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_r1()
    elif cmd == "verify":
        verify_r1()
    else:
        raise SystemExit(f"unknown command: {cmd}")
