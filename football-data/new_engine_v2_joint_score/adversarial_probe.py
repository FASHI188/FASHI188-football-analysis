from __future__ import annotations

import json
from pathlib import Path

from engine import Parameters
from strict import GovernanceError, compute_forward_row_hash, strict_nonnegative_int, validate_forward_row_schema, verify_hash_chain

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"


def expect_fail(name, fn, checks):
    try:
        fn()
    except Exception as exc:
        checks[name] = {"passed": True, "error": f"{type(exc).__name__}:{exc}"}
    else:
        checks[name] = {"passed": False, "error": None}


def base_forward_row():
    row = {
        "schema_version": "x", "fixture_id": "f", "provider_event_id": "p", "competition_id": "c", "season": "s",
        "canonical_home": "h", "canonical_away": "a", "kickoff_utc": "2026-09-01T10:00:00+00:00",
        "observed_at_utc": "2026-09-01T08:00:00+00:00", "prediction_cutoff": "T-60",
        "source": {"provider": "public", "capture_sha256": "a"*64, "manifest_sha256": "b"*64, "observed_at_utc": "2026-09-01T08:00:00+00:00"},
        "model": {"candidate_head": "v2", "engine_sha256": "c"*64, "config_sha256": "d"*64},
        "prediction": {"matrix": [{"home_goals": 0, "away_goals": 0, "probability": 1.0}],
                       "one_x_two": {"home": 0.0, "draw": 1.0, "away": 0.0}, "uncertainty": 0.5, "cold_start_bucket": "zero"},
        "labels_present": False, "outcomes_read": False, "previous_row_hash": "0"*64, "row_hash": "0"*64,
    }
    row["row_hash"] = compute_forward_row_hash(row)
    return row


def main() -> int:
    checks = {}
    for i, bad in enumerate((1.9, 0.2, True, False, "1", float("nan"), float("inf"))):
        expect_fail(f"strict_score_{i}_{type(bad).__name__}", lambda b=bad: strict_nonnegative_int(b, "score"), checks)
    expect_fail("parameters_nan", lambda: Parameters(half_life_days=float("nan")), checks)
    expect_fail("parameters_min_ge_max", lambda: Parameters(min_rate=3.0, max_rate=2.0), checks)
    row = base_forward_row()
    bad_nested = json.loads(json.dumps(row)); bad_nested["prediction"]["nested"] = {"result": "H"}; bad_nested["row_hash"] = compute_forward_row_hash(bad_nested)
    expect_fail("recursive_unknown_nested_result", lambda: validate_forward_row_schema(bad_nested), checks)
    bad_alias = json.loads(json.dumps(row)); bad_alias["final_score_90"] = "1-0"; bad_alias["row_hash"] = compute_forward_row_hash(bad_alias)
    expect_fail("final_score_90_rejected", lambda: validate_forward_row_schema(bad_alias), checks)
    r1 = base_forward_row(); r2 = base_forward_row(); r2["fixture_id"] = "f2"; r2["provider_event_id"] = "p2"; r2["previous_row_hash"] = r1["row_hash"]; r2["row_hash"] = compute_forward_row_hash(r2)
    verify_hash_chain([r1, r2], "0"*64)
    tampered = json.loads(json.dumps(r2)); tampered["prediction"]["uncertainty"] = 0.2
    expect_fail("row_hash_tamper", lambda: verify_hash_chain([r1, tampered], "0"*64), checks)
    engine_source = (HERE / "engine.py").read_text(encoding="utf-8").casefold()
    blind_source = (HERE / "blind_predict.py").read_text(encoding="utf-8").casefold()
    dataset_source = (HERE / "dataset.py").read_text(encoding="utf-8").casefold()
    scorer_source = (HERE / "score_unseal.py").read_text(encoding="utf-8").casefold()
    checks["pure_no_legacy_or_market_tokens"] = {"passed": not any(t in engine_source for t in ("bayesian_dynamic_state_oof", "v500", "closing odds", "market_assist")), "error": None}
    checks["blind_predictor_no_final_label_path"] = {"passed": "final_labels.jsonl" not in blind_source and "--label-file" not in blind_source, "error": None}
    checks["dataset_no_float_goal_coercion"] = {"passed": "int(float(" not in dataset_source, "error": None}
    checks["scorer_reopens_blind_bytes"] = {"passed": "read_bytes()" in scorer_source and "blind_sha256" in scorer_source, "error": None}
    passed = all(x["passed"] for x in checks.values())
    result = {"schema_version": "football3-v2-adversarial-probe-v1", "passed": passed, "checks": checks,
              "probe_scope": ["strict integer labels", "finite/relational parameters", "recursive default deny", "hash-chain recomputation", "pure legacy/market isolation", "blind label isolation", "no float->int goal coercion", "persisted blind byte/hash verification"]}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "adversarial_probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "failed": [k for k, v in checks.items() if not v["passed"]]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
