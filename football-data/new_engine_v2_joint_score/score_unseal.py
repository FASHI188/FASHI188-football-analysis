from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from engine import EngineState, Fixture, prediction_hash
from strict import (
    GovernanceError, canonical_json_bytes, sha256_file, strict_nonnegative_int,
    validate_probability_vector,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise GovernanceError(f"jsonl row must be object at {line_no}")
            rows.append(row)
    return rows


def fixture_from_feature(row: dict[str, Any]) -> Fixture:
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
    p.add_argument("--blind", required=True)
    p.add_argument("--blind-manifest", required=True)
    p.add_argument("--features", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--label-file", required=True)
    p.add_argument("--universe-manifest", required=True)
    p.add_argument("--next-state", required=True)
    p.add_argument("--score-out", required=True)
    args = p.parse_args()

    blind_path = Path(args.blind)
    blind_manifest_path = Path(args.blind_manifest)
    features_path = Path(args.features)
    state_path = Path(args.state)
    label_path = Path(args.label_file)
    universe_manifest_path = Path(args.universe_manifest)
    next_state_path = Path(args.next_state)
    score_out_path = Path(args.score_out)

    # Unseal starts by re-reading raw persisted blind bytes and proving the freeze.
    raw = blind_path.read_bytes()
    manifest = json.loads(blind_manifest_path.read_text(encoding="utf-8"))
    actual_sha = hashlib.sha256(raw).hexdigest()
    if manifest.get("blind_sha256") != actual_sha or int(manifest.get("blind_bytes", -1)) != len(raw):
        raise GovernanceError("blind raw bytes/hash mismatch")
    if manifest.get("state_sha256") != sha256_file(state_path):
        raise GovernanceError("blind state hash mismatch")
    if manifest.get("features_sha256") != sha256_file(features_path):
        raise GovernanceError("blind features hash mismatch")
    if manifest.get("labels_read") is not False:
        raise GovernanceError("blind manifest indicates labels were read")

    batch = json.loads(raw.decode("utf-8"))
    if batch.get("labels_read") is not False:
        raise GovernanceError("blind batch indicates labels were read")
    if batch.get("cutoff") != manifest.get("cutoff"):
        raise GovernanceError("blind cutoff mismatch")
    if batch.get("state_sha256") != manifest.get("state_sha256"):
        raise GovernanceError("blind batch/manifest state hash mismatch")
    if batch.get("features_sha256") != manifest.get("features_sha256"):
        raise GovernanceError("blind batch/manifest features hash mismatch")
    if batch.get("model_lock_sha256") != manifest.get("model_lock_sha256"):
        raise GovernanceError("blind batch/manifest model-lock hash mismatch")
    fixture_ids = [str(x) for x in batch.get("fixture_ids", [])]
    if fixture_ids != [str(x) for x in manifest.get("fixture_ids", [])]:
        raise GovernanceError("blind fixture order/set mismatch")
    fixture_set_sha = hashlib.sha256(canonical_json_bytes(sorted(fixture_ids))).hexdigest()
    if fixture_set_sha != manifest.get("fixture_set_sha256"):
        raise GovernanceError("blind fixture-set hash mismatch")
    if len(fixture_ids) != len(set(fixture_ids)) or len(fixture_ids) != int(manifest.get("prediction_count", -1)):
        raise GovernanceError("blind duplicate/count mismatch")

    predictions = batch.get("predictions")
    if not isinstance(predictions, list) or len(predictions) != len(fixture_ids):
        raise GovernanceError("blind prediction payload mismatch")
    prediction_ids = [str(pred.get("fixture_id") or "") if isinstance(pred, dict) else "" for pred in predictions]
    if prediction_ids != fixture_ids or len(prediction_ids) != len(set(prediction_ids)):
        raise GovernanceError("blind prediction identity/order/uniqueness mismatch")
    for pred in predictions:
        if not isinstance(pred, dict):
            raise GovernanceError("blind prediction row must be object")
        validate_probability_vector(pred.get("final_1x2"), "blind.final_1x2")
        stored_hash = pred.get("prediction_hash")
        payload = dict(pred)
        payload.pop("prediction_hash", None)
        if stored_hash != prediction_hash(payload):
            raise GovernanceError("blind prediction_hash mismatch")

    features = read_jsonl(features_path)
    if [str(r["fixture_id"]) for r in features] != fixture_ids:
        raise GovernanceError("feature fixture sequence does not match blind batch")
    if {str(r["cutoff"]) for r in features} != {str(manifest["cutoff"])}:
        raise GovernanceError("feature cutoff mismatch")

    universe_manifest = json.loads(universe_manifest_path.read_text(encoding="utf-8"))
    if sha256_file(label_path) != universe_manifest.get("final_labels_sha256"):
        raise GovernanceError("final label file digest drift")

    # Only after the freeze proof above do we open the isolated label file.
    labels_all = read_jsonl(label_path)
    label_map: dict[str, tuple[int, int]] = {}
    for row in labels_all:
        fid = str(row.get("fixture_id") or "")
        if not fid or fid in label_map:
            raise GovernanceError("duplicate/empty final label fixture")
        label_map[fid] = (
            strict_nonnegative_int(row.get("home_goals"), f"{fid}.home_goals"),
            strict_nonnegative_int(row.get("away_goals"), f"{fid}.away_goals"),
        )
    missing = [fid for fid in fixture_ids if fid not in label_map]
    if missing:
        raise GovernanceError(f"missing batch labels: {missing[:3]}")

    batch_labels = {fid: label_map[fid] for fid in fixture_ids}
    fixtures = [fixture_from_feature(row) for row in features]
    state = EngineState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
    state.apply_batch(fixtures, batch_labels)

    next_state_path.parent.mkdir(parents=True, exist_ok=True)
    next_state_path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    scored = {
        "schema_version": "football3-v2-scored-batch-v1",
        "blind_sha256": actual_sha,
        "cutoff": manifest["cutoff"],
        "fixture_ids": fixture_ids,
        "labels": [
            {"fixture_id": fid, "home_goals": batch_labels[fid][0], "away_goals": batch_labels[fid][1]}
            for fid in fixture_ids
        ],
        "next_state_sha256": sha256_file(next_state_path),
        "prediction_reopened_from_disk": True,
        "prediction_bytes_verified": True,
        "fixture_set_verified": True,
        "cutoff_verified": True,
        "manifest_verified": True,
        "labels_opened_after_freeze_verification": True,
    }
    score_out_path.write_bytes(canonical_json_bytes(scored) + b"\n")
    print(json.dumps({"cutoff": scored["cutoff"], "n": len(fixture_ids), "next_state_sha256": scored["next_state_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
