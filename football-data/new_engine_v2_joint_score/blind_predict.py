from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from engine import (
    EngineState, Fixture, apply_fitness, head_predict, joint_matrix, kl_project_to_1x2,
    matrix_1x2, matrix_to_cells, prediction_hash,
)
from strict import GovernanceError, canonical_json_bytes, sha256_file


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def make_fixture(row: dict[str, Any]) -> Fixture:
    cutoff = datetime.fromisoformat(str(row["cutoff"]).replace("Z", "+00:00"))
    return Fixture(
        fixture_id=str(row["fixture_id"]),
        competition_id=str(row["competition_id"]),
        season=str(row["season"]),
        kickoff=cutoff,
        home_team_id=str(row["home_team_id"]),
        away_team_id=str(row["away_team_id"]),
        round_index=int(row["round_index"]),
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--features", required=True)
    p.add_argument("--lock", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--manifest", required=True)
    args = p.parse_args()
    state_path, features_path, lock_path = map(Path, (args.state, args.features, args.lock))
    out_path, manifest_path = Path(args.out), Path(args.manifest)

    state = EngineState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    rows = read_jsonl(features_path)
    if not rows:
        raise GovernanceError("blind batch empty")
    cutoffs = {str(r["cutoff"]) for r in rows}
    if len(cutoffs) != 1:
        raise GovernanceError("blind batch must contain one exact cutoff")
    if any("home_goals" in r or "away_goals" in r or "result" in r for r in rows):
        raise GovernanceError("label-like field reached blind predictor")

    predictions = []
    for row in rows:
        fixture = make_fixture(row)
        feat = state.predict_features(fixture)
        if lock.get("fitness_retained"):
            feat_model = apply_fitness(feat, *lock["fitness"])
        else:
            feat_model = feat
        dispersion = lock["competition_dispersion"].get(fixture.competition_id, [20.0, 20.0])
        base_matrix = joint_matrix(
            lock["joint_family"],
            feat_model,
            dispersion_home=float(dispersion[0]),
            dispersion_away=float(dispersion[1]),
            dependence=float(lock["dependence"]),
            max_goals=int(lock["max_goals"]),
        )
        base_1x2 = matrix_1x2(base_matrix)
        head = None
        matrix = base_matrix
        if lock.get("dual_head_retained"):
            head = head_predict(feat_model, lock["dual_head_weights"])
            matrix = kl_project_to_1x2(base_matrix, head)
        final_1x2 = matrix_1x2(matrix)
        payload = {
            "fixture_id": fixture.fixture_id,
            "competition_id": fixture.competition_id,
            "season": fixture.season,
            "cutoff": fixture.kickoff.isoformat(),
            "home_team_id": fixture.home_team_id,
            "away_team_id": fixture.away_team_id,
            "round_index": fixture.round_index,
            "model_features": feat_model,
            "base_1x2": base_1x2,
            "head_1x2": head,
            "final_1x2": final_1x2,
            "matrix": matrix_to_cells(matrix),
            "uncertainty": feat["uncertainty"],
            "cold_start_bucket": feat["cold_start_bucket"],
        }
        payload["prediction_hash"] = prediction_hash(payload)
        predictions.append(payload)

    batch = {
        "schema_version": "football3-v2-blind-batch-v1",
        "cutoff": next(iter(cutoffs)),
        "fixture_ids": [str(r["fixture_id"]) for r in rows],
        "state_sha256": sha256_file(state_path),
        "features_sha256": sha256_file(features_path),
        "model_lock_sha256": sha256_file(lock_path),
        "prediction_count": len(predictions),
        "predictions": predictions,
        "labels_read": False,
    }
    raw = canonical_json_bytes(batch) + b"\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    fixture_set_sha = hashlib.sha256(canonical_json_bytes(sorted(batch["fixture_ids"]))).hexdigest()
    manifest = {
        "schema_version": "football3-v2-blind-batch-manifest-v1",
        "blind_sha256": hashlib.sha256(raw).hexdigest(),
        "blind_bytes": len(raw),
        "fixture_set_sha256": fixture_set_sha,
        "fixture_ids": batch["fixture_ids"],
        "cutoff": batch["cutoff"],
        "state_sha256": batch["state_sha256"],
        "features_sha256": batch["features_sha256"],
        "model_lock_sha256": batch["model_lock_sha256"],
        "prediction_count": len(predictions),
        "labels_read": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cutoff": batch["cutoff"], "n": len(predictions), "blind_sha256": manifest["blind_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
