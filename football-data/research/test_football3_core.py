from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from football3_core import (
    Football3ContractError,
    GlobalIdentityRegistry,
    SealedAccessReceipt,
    SealedPool,
    SealedPoolReader,
    TemporalFoldManifest,
    assert_candidate_materially_distinct,
    assert_exact_one_to_one_join,
    assert_sealed_boundaries,
    build_confirmation_power_plan,
    cluster_bootstrap_vector,
    evaluate_frozen_experiment,
    file_sha256,
    key_set_sha256,
    load_label_table_after_identity_guard,
    ordered_identity_sha256,
    ordered_key_sha256,
    paired_bootstrap_vector,
    probability_fingerprint,
    source_row_identity,
    validate_confirmation_power_plan,
    validate_sealed_run_receipts,
)


def ids(n: int) -> list[str]:
    return [hashlib.sha256(f"global-{i}".encode()).hexdigest() for i in range(n)]


def make_probs(y: np.ndarray, correct: float = 0.55) -> np.ndarray:
    p = np.full((len(y), 8), (1.0 - correct) / 7.0)
    p[np.arange(len(y)), y] = correct
    return p


def make_temporal_manifest(tmp_path: Path, identities: list[str], folds: list[str], dates: list[str]) -> TemporalFoldManifest:
    rows = []
    for identity, fold, date in zip(identities, folds, dates):
        month = int(date[5:7])
        train_month = max(1, month - 1)
        rows.append({"identity_sha256": identity, "fold_id": fold, "test_time_utc": date, "train_max_utc": f"2026-{train_month:02d}-01T00:00:00Z"})
    payload = {"schema": "football3_temporal_fold_manifest_v1", "rows": rows}
    path = tmp_path / "temporal.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return TemporalFoldManifest.load(path)


def contract_for(identities: list[str], tm: TemporalFoldManifest, *, min_fold: float = 0.60, min_domain: float = 0.60) -> dict:
    return {
        "data_plan": {"identity_count": len(identities), "ordered_identity_sha256": ordered_identity_sha256(identities)},
        "oos_design": {"temporal_manifest_sha256": tm.sha256},
        "candidate_equivalence": {"max_abs_floor": 1e-9, "mean_abs_floor": 1e-11},
        "metrics": {"calibration": {"bins": 10}},
        "bootstrap": {"resamples": 1500, "seed": 72001, "ci": 0.90},
        "dependency_bootstrap": {"resamples": 1500, "seed": 72002, "ci": 0.90, "minimum_clusters": 8},
        "success_gates": {
            "primary": {},
            "secondary_noninferiority": {"Brier_delta_max": 0.0, "RPS_delta_max": 0.0},
            "temporal_consistency": {"minimum_fold_win_fraction": min_fold},
            "domain_consistency": {"minimum_win_fraction": min_domain},
        },
    }


def test_identical_candidate_is_fail_closed_before_scientific_pass(tmp_path: Path):
    n = 160
    y = np.arange(n) % 4
    b = make_probs(y, 0.55)
    identities = ids(n)
    folds = [f"f{i//40}" for i in range(n)]
    dates = [f"2026-{3+i//40:02d}-{1+i%20:02d}T12:00:00Z" for i in range(n)]
    tm = make_temporal_manifest(tmp_path, identities, folds, dates)
    with pytest.raises(Football3ContractError, match="identical or numerically equivalent"):
        evaluate_frozen_experiment(b, b.copy(), y, identity_sha256=identities, fold_ids=folds, domain_ids=[f"L{i//40}" for i in range(n)], scored_dates_utc=dates, cluster_ids=[f"C{i//20}" for i in range(n)], temporal_manifest=tm, contract=contract_for(identities, tm))


def test_numerical_noise_only_candidate_is_not_new_model():
    y = np.arange(80) % 4
    b = make_probs(y, 0.55)
    c = b.copy(); c[:, 0] += 1e-12; c[:, 1] -= 1e-12
    with pytest.raises(Football3ContractError, match="numerically equivalent"):
        assert_candidate_materially_distinct(b, c)


def test_constant_scale_then_normalization_remains_equivalent():
    y = np.arange(40) % 4
    b = make_probs(y, 0.55)
    c = b * 3.14159; c /= c.sum(axis=1, keepdims=True)
    assert probability_fingerprint(b) == probability_fingerprint(c)
    with pytest.raises(Football3ContractError): assert_candidate_materially_distinct(b, c)


def test_zero_improvement_fold_or_domain_never_promotes(tmp_path: Path):
    n = 160; y = np.arange(n) % 4; b = make_probs(y, 0.55); c = b.copy()
    for i in range(n):
        yi = y[i]
        c[i] = make_probs(np.array([yi]), 0.60 if i < 80 else 0.50)[0]
    identities = ids(n); folds = [f"f{i//40}" for i in range(n)]; domains = [f"L{i//40}" for i in range(n)]
    dates = [f"2026-{3+i//40:02d}-{1+i%20:02d}T12:00:00Z" for i in range(n)]
    tm = make_temporal_manifest(tmp_path, identities, folds, dates)
    out = evaluate_frozen_experiment(b, c, y, identity_sha256=identities, fold_ids=folds, domain_ids=domains, scored_dates_utc=dates, cluster_ids=[f"C{i//20}" for i in range(n)], temporal_manifest=tm, contract=contract_for(identities, tm, min_fold=0.60, min_domain=0.60))
    assert out["terminal"] == "PARK_NO_PROMOTION"
    assert out["fold_win_fraction"] < 0.60 or out["domain_win_fraction"] < 0.60


def registry_manifest(tmp_path: Path) -> Path:
    payload = {
        "schema": "football3_global_fixture_registry_v1",
        "alias_version": "aliases-20260821-v1",
        "kickoff_tolerance_seconds": 180,
        "competition_aliases": {"Premier League": "ENG1", "premier-league": "ENG1"},
        "team_aliases": {"Manchester United": "MAN_UTD", "Man Utd": "MAN_UTD", "Liverpool": "LIVERPOOL", "Everton": "EVERTON"},
        "fixtures": [
            {"competition": "ENG1", "kickoff_utc": "2026-08-21T19:00:00Z", "home_team": "MAN_UTD", "away_team": "LIVERPOOL", "season": "2026-27"},
            {"competition": "ENG1", "kickoff_utc": "2026-08-21T21:00:00Z", "home_team": "EVERTON", "away_team": "LIVERPOOL", "season": "2026-27"},
        ],
    }
    path = tmp_path / "global_registry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def test_same_fixture_different_source_and_source_id_resolves_same_global_identity(tmp_path: Path):
    reg = GlobalIdentityRegistry.load(registry_manifest(tmp_path))
    a = {"sourceCode": "A", "id": "11", "League": "Premier League", "matchDate": "2026-08-21T18:59:00Z", "Season": "2026-27", "homeTeam": "Manchester United", "awayTeam": "Liverpool"}
    b = {"sourceCode": "B", "id": "XYZ", "League": "premier-league", "matchDate": "2026-08-21T19:02:00Z", "Season": "2026-27", "homeTeam": "Man Utd", "awayTeam": "Liverpool"}
    assert source_row_identity(a) != source_row_identity(b)
    assert reg.resolve(a) == reg.resolve(b)


def test_different_fixture_not_merged_and_home_away_not_silently_swapped(tmp_path: Path):
    reg = GlobalIdentityRegistry.load(registry_manifest(tmp_path))
    first = {"League": "Premier League", "matchDate": "2026-08-21T19:00:00Z", "Season": "2026-27", "homeTeam": "Manchester United", "awayTeam": "Liverpool"}
    second = {"League": "Premier League", "matchDate": "2026-08-21T21:00:00Z", "Season": "2026-27", "homeTeam": "Everton", "awayTeam": "Liverpool"}
    assert reg.resolve(first) != reg.resolve(second)
    swapped = {**first, "homeTeam": "Liverpool", "awayTeam": "Manchester United"}
    with pytest.raises(Football3ContractError, match="UNRESOLVED"): reg.resolve(swapped)


def test_unresolved_alias_blocks_identity(tmp_path: Path):
    reg = GlobalIdentityRegistry.load(registry_manifest(tmp_path))
    row = {"League": "Premier League", "matchDate": "2026-08-21T19:00:00Z", "Season": "2026-27", "homeTeam": "Mystery Club", "awayTeam": "Liverpool"}
    with pytest.raises(Football3ContractError, match="UNRESOLVED"): reg.resolve(row)


def test_strict_join_rejects_right_extra_and_missing_and_duplicate_and_type_drift():
    left = pd.DataFrame({"gid": ["a", "b"], "x": [1, 2]})
    for right in (
        pd.DataFrame({"gid": ["a", "b", "c"], "T": [1, 2, 3]}),
        pd.DataFrame({"gid": ["a"], "T": [1]}),
        pd.DataFrame({"gid": ["a", "a"], "T": [1, 2]}),
        pd.DataFrame({"gid": [1, 2], "T": [1, 2]}),
    ):
        with pytest.raises(Football3ContractError): assert_exact_one_to_one_join(left, right, keys=["gid"], expected_rows=2)


def test_extra_target_row_is_blocked_before_target_decode(tmp_path: Path):
    left = pd.DataFrame({"gid": ["a", "b"], "x": [1, 2]})
    label = tmp_path / "labels.csv"; label.write_text("gid,T\na,1\nb,2\nc,7\n", encoding="utf-8")
    frozen_keys = [("a",), ("b",)]
    manifest = {"schema": "football3_label_identity_manifest_v1", "label_file_sha256": "0" * 64, "keys": ["gid"], "key_types": ["string"], "row_count": 2, "ordered_keys_sha256": ordered_key_sha256(frozen_keys), "key_set_sha256": key_set_sha256(frozen_keys)}
    mp = tmp_path / "manifest.json"; mp.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Football3ContractError, match="SHA mismatch before target decode"):
        load_label_table_after_identity_guard(left, label, mp, keys=["gid"], target_columns=["T"], expected_rows=2)


def test_label_order_change_is_fail_closed_by_frozen_order(tmp_path: Path):
    left = pd.DataFrame({"gid": ["a", "b"]})
    label = tmp_path / "labels.csv"; label.write_text("gid,T\nb,2\na,1\n", encoding="utf-8")
    manifest = {"schema": "football3_label_identity_manifest_v1", "label_file_sha256": file_sha256(label), "keys": ["gid"], "key_types": ["string"], "row_count": 2, "ordered_keys_sha256": ordered_key_sha256([("b",), ("a",)]), "key_set_sha256": key_set_sha256([("a",), ("b",)])}
    mp = tmp_path / "manifest.json"; mp.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Football3ContractError, match="identity mismatch before target decode"):
        load_label_table_after_identity_guard(left, label, mp, keys=["gid"], target_columns=["T"], expected_rows=2)


def test_worse_and_zero_delta_cannot_generate_confirmation_plan():
    clusters = [f"c{i//10}" for i in range(100)]
    with pytest.raises(Football3ContractError, match="NO_CONFIRMATION_PLAN"): build_confirmation_power_plan(np.full(100, +0.01), clusters)
    with pytest.raises(Football3ContractError, match="NO_CONFIRMATION_PLAN"): build_confirmation_power_plan(np.zeros(100), clusters)


def test_power_plan_direction_missing_or_fake_positive_effect_rejected():
    d = np.linspace(-0.03, -0.01, 100); clusters = [f"c{i//10}" for i in range(100)]
    plan = build_confirmation_power_plan(d, clusters)
    bad = dict(plan); bad.pop("direction_required")
    with pytest.raises(Football3ContractError): validate_confirmation_power_plan(bad, d, clusters)
    bad = dict(plan); bad["effect"] = plan["effect"] * 10
    with pytest.raises(Football3ContractError): validate_confirmation_power_plan(bad, d, clusters)


def test_temporal_dummy_array_cannot_authorize_different_scoring_rows(tmp_path: Path):
    identities = ids(80); folds = [f"f{i//20}" for i in range(80)]; dates = [f"2026-{3+i//20:02d}-{1+i%20:02d}T12:00:00Z" for i in range(80)]
    tm = make_temporal_manifest(tmp_path, identities, folds, dates); wrong_dates = list(dates); wrong_dates[0] = "2026-12-31T12:00:00Z"
    with pytest.raises(Football3ContractError, match="scoring date mismatch"): tm.bind_scoring_rows(identities, folds, wrong_dates)


def test_temporal_fold_overlap_timezone_identity_order_and_manifest_sha_fail_closed(tmp_path: Path):
    identities = ids(16); folds = [f"f{i//4}" for i in range(16)]; dates = [f"2026-{3+i//4:02d}-{1+i%4:02d}T12:00:00Z" for i in range(16)]
    tm = make_temporal_manifest(tmp_path, identities, folds, dates)
    with pytest.raises(Football3ContractError): tm.bind_scoring_rows(list(reversed(identities)), folds, dates)
    naive = list(dates); naive[0] = "2026-03-01 12:00:00"
    with pytest.raises(Football3ContractError): tm.bind_scoring_rows(identities, folds, naive)
    with pytest.raises(Football3ContractError): TemporalFoldManifest.load(tm.path, expected_sha256="0" * 64)


def synthetic_sealed_manifest(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "synthetic_sealed.csv"; data.write_text("gid,T\na,1\nb,2\n", encoding="utf-8")
    manifest = {"schema": "football3_sealed_pool_manifest_v1", "pool_id": "SYNTHETIC_TEST_POOL", "status": "SEALED", "file_path": str(data), "file_sha256": file_sha256(data), "identity_sha256": hashlib.sha256(b"synthetic identities").hexdigest(), "target_columns": ["T"]}
    mp = tmp_path / "sealed.manifest.json"; mp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return data, mp


def test_sealed_missing_name_and_weird_counts_fail_closed():
    pools = [SealedPool("A"), SealedPool("B")]
    with pytest.raises(Football3ContractError): assert_sealed_boundaries({"A": 0}, pools)
    for bad in (0.9, False, "0", -1):
        with pytest.raises(Football3ContractError): assert_sealed_boundaries({"A": bad, "B": 0}, pools)


def test_actual_sealed_open_cannot_be_overridden_by_self_reported_zero(tmp_path: Path):
    _, mp = synthetic_sealed_manifest(tmp_path)
    reader = SealedPoolReader([mp], authorized_pool_ids=["SYNTHETIC_TEST_POOL"])
    frame = reader.read_csv("SYNTHETIC_TEST_POOL", target_columns=["T"]); assert len(frame) == 2
    receipts = reader.receipts(); assert receipts[0].access_count == 1 and receipts[0].target_column_reads == 1
    with pytest.raises(Football3ContractError, match="unattested"): validate_sealed_run_receipts(["SYNTHETIC_TEST_POOL"], [{"pool_id": "SYNTHETIC_TEST_POOL", "access_count": 0}])
    with pytest.raises(Football3ContractError): SealedAccessReceipt(pool_id="SYNTHETIC_TEST_POOL", manifest_sha256="0" * 64, file_sha256="0" * 64, identity_sha256="0" * 64, access_count=0, target_column_reads=0, rows_materialized=0, authorized=True, _attestation=object())


def test_unauthorized_sealed_target_read_is_blocked_before_file_materialization(tmp_path: Path):
    _, mp = synthetic_sealed_manifest(tmp_path)
    reader = SealedPoolReader([mp], authorized_pool_ids=[])
    with pytest.raises(Football3ContractError, match="not authorized"): reader.read_csv("SYNTHETIC_TEST_POOL", target_columns=["T"])
    receipt = reader.receipts()[0]; assert receipt.access_count == 0 and receipt.target_column_reads == 0 and receipt.rows_materialized == 0


def test_sealed_file_sha_mismatch_fails(tmp_path: Path):
    data, mp = synthetic_sealed_manifest(tmp_path); reader = SealedPoolReader([mp], authorized_pool_ids=["SYNTHETIC_TEST_POOL"])
    data.write_text("gid,T\na,7\n", encoding="utf-8")
    with pytest.raises(Football3ContractError, match="SHA mismatch"): reader.read_csv("SYNTHETIC_TEST_POOL", target_columns=["T"])


def test_correlated_synthetic_iid_passes_but_cluster_gate_rejects():
    effects = np.array([-0.10] * 11 + [0.09] * 9)
    delta = np.repeat(effects, 30); clusters = np.repeat([f"c{i}" for i in range(20)], 30)
    iid = paired_bootstrap_vector(delta, n_resamples=5000, seed=11, ci=0.90)
    cluster = cluster_bootstrap_vector(delta, clusters, n_resamples=5000, seed=12, ci=0.90, minimum_clusters=8)
    assert iid["ci_high"] < 0.0
    assert cluster["ci_high"] >= 0.0
