#!/usr/bin/env python3
"""Full-volume 18,464-row migration acceptance: old V4.7 entry vs unified entry.

No sampling or selective threshold is permitted. The old side calls the exact V4.7
formal numerical function directly. The rebuilt side calls the same calculator only
through TeamIdentityResolver -> PIT store -> FeatureAssembler ->
UnifiedInferenceEngine -> UnifiedDatasetGenerator. Every eligible row must match in
score-matrix hash and 1X2 probabilities.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ROOT, ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from assembly.feature_assembler import FeatureAssembler
from backtest_last_complete_season_all_domains_v470 import (
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _requested_last_complete_season,
    _target_season_temperature,
)
from identity.team_identity import TeamIdentityResolver
from oof_matrix_calibration import temperature_scale_matrix
from pipeline.formal_v470_replay_baseline import FormalV470ReplayBaseline
from pipeline.unified_dataset import PredictionCase, SettledOutcome, UnifiedDatasetGenerator, dataset_fingerprint
from pipeline.unified_inference import FixtureRequest, UnifiedInferenceEngine, canonical_matrix, matrix_hash, one_x_two, top1
from pit.feature_store import PointInTimeFeatureStore
from platform_core import PlatformError, load_json, read_processed_matches

OUT = ROOT / "validation" / "reports" / "r43gov0_full18464_entry_diff.json"
EXPECTED_ROWS = 18464


def season_year(season: str) -> int:
    token = str(season).strip()
    if len(token) < 4 or not token[:4].isdigit():
        raise RuntimeError(f"cannot parse season {season!r}")
    return int(token[:4])


def completed_seasons(cid: str, report: dict) -> list[str]:
    cap = season_year(_requested_last_complete_season(cid))
    out = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "").strip()
        if season and season_year(season) <= cap and season not in out:
            out.append(season)
    return sorted(out, key=season_year)


def aware(dt):
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else dt.replace(tzinfo=timezone.utc)


def stable_hash(items) -> str:
    raw = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def build_old_side():
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    expected = {}
    cases = []
    identity_records = []
    per_comp = Counter()
    skipped = Counter()
    seen_identity = set()

    for cid in competitions:
        report = load_json(REPORT_ROOT / f"{cid}.json")
        all_matches = read_processed_matches(cid)
        for team in sorted({m.home_team for m in all_matches} | {m.away_team for m in all_matches}):
            key = (cid, str(team))
            if key in seen_identity:
                continue
            seen_identity.add(key)
            identity_records.append({
                "source_namespace": f"v470:{cid}",
                "source_team_id": str(team),
                "canonical_team_id": str(team),
                "mapping_method": "v470_processed_match_exact_team_key",
                "provenance_hash": stable_hash([cid, str(team)]),
            })
        for season in completed_seasons(cid, report):
            fold = _fold_for_season(report, season)
            params = fold.get("selected_parameters")
            if not isinstance(params, dict):
                raise RuntimeError(f"invalid parameters {cid} {season}")
            temperature, _mode = _target_season_temperature(cid, season)
            matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
            for match in matches:
                try:
                    direct = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
                    if abs(float(temperature) - 1.0) > 1e-15:
                        direct = temperature_scale_matrix(direct, float(temperature))
                    direct = canonical_matrix(direct)
                except PlatformError:
                    skipped[cid] += 1
                    continue
                kickoff = aware(match.date)
                fixture_id = f"{cid}|{season}|{kickoff.isoformat()}|{match.home_team}|{match.away_team}"
                if fixture_id in expected:
                    raise RuntimeError(f"duplicate fixture key {fixture_id}")
                probs = one_x_two(direct)
                expected[fixture_id] = {
                    "competition_id": cid,
                    "season": season,
                    "matrix_hash": matrix_hash(direct),
                    "probabilities": probs,
                    "top1": top1(probs),
                    "home": str(match.home_team),
                    "away": str(match.away_team),
                }
                request = FixtureRequest(
                    fixture_id=fixture_id,
                    as_of=kickoff - timedelta(seconds=1),
                    home_source_namespace=f"v470:{cid}",
                    home_source_team_id=str(match.home_team),
                    home_source_name=str(match.home_team),
                    away_source_namespace=f"v470:{cid}",
                    away_source_team_id=str(match.away_team),
                    away_source_name=str(match.away_team),
                )
                cases.append(PredictionCase(
                    request=request,
                    kickoff_at=kickoff,
                    baseline_payload={"competition_id": cid, "season": season, "target_datetime": match.date},
                    outcome=SettledOutcome(int(match.home_goals), int(match.away_goals)),
                    competition_id=cid,
                ))
                per_comp[cid] += 1
    return competitions, expected, cases, identity_records, per_comp, skipped


def main() -> int:
    competitions, expected, cases, identity_records, per_comp, skipped = build_old_side()
    if len(cases) != EXPECTED_ROWS or len(expected) != EXPECTED_ROWS:
        raise RuntimeError(f"full-volume cohort drift: {len(cases)} != {EXPECTED_ROWS}")

    resolver = TeamIdentityResolver(identity_records)
    pit_store = PointInTimeFeatureStore()
    assembler = FeatureAssembler()
    baseline = FormalV470ReplayBaseline()
    engine = UnifiedInferenceEngine(resolver, pit_store, assembler, baseline)
    rows = UnifiedDatasetGenerator(engine).generate("dataset", cases)
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"unified row count drift: {len(rows)}")

    mismatches = []
    max_abs_1x2 = 0.0
    top1_mismatches = 0
    receipt_missing = 0
    numerical_receipt_missing = 0
    new_ids = set()
    for row in rows:
        new_ids.add(row.fixture_id)
        old = expected[row.fixture_id]
        if not row.feature_activation_receipt.get("receipt_hash"):
            receipt_missing += 1
        chain0 = row.component_chain[0]
        if not chain0.get("numerical_receipt"):
            numerical_receipt_missing += 1
        diffs = {k: abs(float(row.probabilities[k]) - float(old["probabilities"][k])) for k in ("home", "draw", "away")}
        local = max(diffs.values())
        max_abs_1x2 = max(max_abs_1x2, local)
        bad_hash = row.score_matrix_hash != old["matrix_hash"]
        bad_top1 = row.top1 != old["top1"]
        if bad_top1:
            top1_mismatches += 1
        if bad_hash or local != 0.0 or bad_top1 or row.canonical_home_team_id != old["home"] or row.canonical_away_team_id != old["away"]:
            if len(mismatches) < 100:
                mismatches.append({
                    "fixture_id": row.fixture_id,
                    "matrix_hash_match": not bad_hash,
                    "1x2_abs_diff": diffs,
                    "old_top1": old["top1"],
                    "new_top1": row.top1,
                    "old_matrix_hash": old["matrix_hash"],
                    "new_matrix_hash": row.score_matrix_hash,
                })

    old_ids = set(expected)
    fixture_set_match = old_ids == new_ids
    status = "PASS" if (
        fixture_set_match and len(rows) == EXPECTED_ROWS and not mismatches and max_abs_1x2 == 0.0
        and top1_mismatches == 0 and receipt_missing == 0 and numerical_receipt_missing == 0
    ) else "FAIL"
    payload = {
        "schema_version": "football3-r43gov0-full18464-entry-diff-v1",
        "status": status,
        "scope": "full_volume_no_sampling",
        "old_entry": "V4.7 direct _predict_from_loaded_matches + frozen OOF temperature",
        "rebuilt_entry": "TeamIdentityResolver -> PIT -> FeatureAssembler -> UnifiedInferenceEngine -> UnifiedDatasetGenerator -> FormalV470ReplayBaseline",
        "operational_runtime_baseline_changed": False,
        "operational_runtime_baseline_remains": "S60",
        "expected_rows": EXPECTED_ROWS,
        "old_rows": len(expected),
        "new_rows": len(rows),
        "competition_count": len(competitions),
        "per_competition_rows": dict(sorted(per_comp.items())),
        "direct_skipped_platform_errors": dict(sorted(skipped.items())),
        "fixture_set_match": fixture_set_match,
        "fixture_set_sha256": stable_hash(sorted(old_ids)),
        "dataset_fingerprint": dataset_fingerprint(rows),
        "matrix_hash_mismatch_count": len(mismatches),
        "top1_mismatch_count": top1_mismatches,
        "max_abs_1x2_difference": max_abs_1x2,
        "activation_receipt_missing_count": receipt_missing,
        "baseline_numerical_receipt_missing_count": numerical_receipt_missing,
        "first_mismatches": mismatches,
        "lineup_numeric_1x2_enabled": False,
        "player_technical_numeric_1x2_enabled": False,
        "head_coach_numeric_1x2_enabled": False,
        "availability_numeric_1x2_enabled": False,
        "u0_y0_modified": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "status", "old_rows", "new_rows", "competition_count", "fixture_set_match",
        "matrix_hash_mismatch_count", "top1_mismatch_count", "max_abs_1x2_difference",
        "activation_receipt_missing_count", "baseline_numerical_receipt_missing_count")}, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
