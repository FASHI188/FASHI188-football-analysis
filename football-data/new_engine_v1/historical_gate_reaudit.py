from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import historical_eval as he

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"

EXPECTED_CANDIDATE_HEAD = "7986b5b528338d1d359f1287677f4ab92e453f39"
EXPECTED_PURE_ENGINE_SHA256 = "cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318"
EXPECTED_PREREG_SHA256 = "cf83987c37a375d03acbc06c696a102ffe13fd8ac460c50ec6e6d631111f8d3a"
EXPECTED_BLIND_SHA256 = "c9f9ee587ec7a509fecc5444b54290a6458c8e9396445c976df895029ddca41f"
EXPECTED_SCORED_SHA256 = "70b114f9781d1ee8b512ff99d8a05d076fa23c35df49edf1d646e690dc900c8e"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pick(pred: dict[str, Any]) -> int:
    values = [float(pred["p_home"]), float(pred["p_draw"]), float(pred["p_away"])]
    return max(range(3), key=lambda idx: values[idx])


def load_blind() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    pure: dict[str, dict[str, Any]] = {}
    legacy: dict[str, dict[str, Any]] = {}
    path = EVIDENCE / "holdout_predictions_blind.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pure.update(row.get("pure") or {})
        legacy.update(row.get("legacy") or {})
    return pure, legacy


def main() -> int:
    blind_path = EVIDENCE / "holdout_predictions_blind.jsonl"
    scored_path = EVIDENCE / "holdout_scored.jsonl"
    result_path = EVIDENCE / "historical_oos_result.json"
    prereg_path = HERE / "contracts" / "VALIDATION_PREREG.md"
    pure_engine_path = HERE / "pure_engine.py"

    checks = {
        "pure_engine_hash_bound_to_candidate": sha256_file(pure_engine_path) == EXPECTED_PURE_ENGINE_SHA256,
        "prereg_hash_unchanged": sha256_file(prereg_path) == EXPECTED_PREREG_SHA256,
        "blind_predictions_reproduced_exactly": sha256_file(blind_path) == EXPECTED_BLIND_SHA256,
        "scored_labels_reproduced_exactly": sha256_file(scored_path) == EXPECTED_SCORED_SHA256,
        "pure_core_no_forbidden_market_or_legacy_tokens": not any(
            token in pure_engine_path.read_text(encoding="utf-8").casefold()
            for token in ("bayesian_dynamic_state_oof_v500", "v500_baseline_adapter", "legacy_r43_registry", "odds", "market")
        ),
    }

    result = json.loads(result_path.read_text(encoding="utf-8"))
    fixtures, labels, _ = he.load_evaluation_universe()
    _, holdout_start = he.split_boundaries(fixtures)
    holdout = fixtures[holdout_start:]
    pure, legacy = load_blind()

    table = he.TableTracker()
    for batch in he.batches(fixtures[:holdout_start]):
        for fixture in batch:
            table.update(fixture, labels[fixture.fixture_id])

    paired_events = 0
    pure_hits = 0
    legacy_hits = 0
    for batch in he.batches(holdout):
        for fixture in batch:
            ctx = table.context(fixture)
            side = ctx.get("underdog_side")
            if side is None:
                continue
            actual = he._actual_index(labels[fixture.fixture_id])
            if actual != side:
                continue
            if fixture.fixture_id not in pure or fixture.fixture_id not in legacy:
                continue
            paired_events += 1
            pure_hits += int(pick(pure[fixture.fixture_id]) == side)
            legacy_hits += int(pick(legacy[fixture.fixture_id]) == side)
        for fixture in batch:
            table.update(fixture, labels[fixture.fixture_id])

    pure_recall = pure_hits / paired_events if paired_events else None
    legacy_recall = legacy_hits / paired_events if paired_events else None
    paired_gate = bool(
        paired_events >= 100
        and pure_recall is not None
        and legacy_recall is not None
        and pure_recall + 0.02 >= legacy_recall
    )

    other_gates = dict(result.get("gates") or {})
    other_gates.pop("underdog_recall_nonworse_2pp_if_n100", None)
    corrected_status = "MODEL_CANDIDATE_PASSED" if all(checks.values()) and all(other_gates.values()) and paired_gate else "NOT_PROMOTED"

    pure_ece = float(result["calibration"]["pure"]["mean_class_ece"])
    legacy_ece = float(result["calibration"]["legacy_v500"]["mean_class_ece"])
    payload = {
        "schema_version": "football3-new-engine-v1-gate-reaudit-v1",
        "candidate_source_head": EXPECTED_CANDIDATE_HEAD,
        "original_scientific_status": result.get("scientific_status"),
        "corrected_scientific_status": corrected_status,
        "checks": checks,
        "same_match_underdog": {
            "paired_events": paired_events,
            "pure_hits": pure_hits,
            "legacy_hits": legacy_hits,
            "pure_top1_recall": pure_recall,
            "legacy_top1_recall": legacy_recall,
            "delta_pp": None if pure_recall is None or legacy_recall is None else 100.0 * (pure_recall - legacy_recall),
            "threshold": "pure_recall >= legacy_recall - 0.02 when n>=100",
            "passed": paired_gate,
        },
        "other_preregistered_gates": other_gates,
        "calibration_secondary_metric": {
            "pure_mean_class_ece": pure_ece,
            "legacy_mean_class_ece": legacy_ece,
            "pure_minus_legacy": pure_ece - legacy_ece,
            "promotion_threshold_preregistered": False,
        },
        "audit_note": "No parameters or predictions are changed. This re-audit only corrects the underdog gate to the preregistered same-match intersection and verifies the frozen blind prediction/score hashes.",
    }
    (EVIDENCE / "historical_gate_reaudit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if corrected_status == "MODEL_CANDIDATE_PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
