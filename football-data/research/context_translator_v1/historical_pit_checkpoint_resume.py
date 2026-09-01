from __future__ import annotations

import argparse
import json
import pathlib
import shutil
from typing import Any

import historical_pit_replay as core
from historical_pit_sharded_common import ShardError, exact_files, sha_file, verify_file_set


def _usage_as_of(snapshot: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], int, str]:
    cutoff = core.dt(str(snapshot["cutoff_utc"]))
    usage: dict[str, list[dict[str, Any]]] = {}
    removed = 0
    for tid, rows in (snapshot.get("usage") or {}).items():
        kept = []
        for row in rows:
            known = core.dt(str(row["known_at"]))
            if known < cutoff:
                kept.append(row)
            else:
                removed += 1
        usage[str(tid)] = kept
    recovered = core.canon({"vectors": snapshot.get("vectors") or {}, "usage": usage})
    original = str(snapshot["state_sha256"])
    if recovered != original:
        raise ShardError(
            f"checkpoint state SHA cannot be recovered at fixture {snapshot.get('fixture_id')}: "
            f"original={original} recovered={recovered}"
        )
    return usage, removed, recovered


def repair_source(source: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.load(open(source / "source_freeze_manifest.json"))
    verify_file_set(source, manifest["payload"])
    if manifest.get("labels_read") != 0 or manifest.get("scorer_invoked") is not False:
        raise ShardError("source checkpoint is not pre-score")
    if manifest.get("raw_webpage_body_persisted") is not False:
        raise ShardError("source checkpoint persisted raw webpage body")
    for name in manifest["payload"]:
        if name != "offline_state_snapshots.jsonl":
            shutil.copy2(source / name, out / name)
    states = core.readjl(source / "offline_state_snapshots.jsonl")
    if len(states) != 50 or len({str(x["fixture_id"]) for x in states}) != 50:
        raise ShardError("source checkpoint does not contain exact 50 state snapshots")

    repaired = []
    audits = []
    removed_total = 0
    repaired_fixture_n = 0
    for snapshot in states:
        usage, removed, recovered = _usage_as_of(snapshot)
        row = dict(snapshot)
        row["usage"] = usage
        repaired.append(row)
        removed_total += removed
        repaired_fixture_n += int(removed > 0)
        audits.append({
            "fixture_id": str(snapshot["fixture_id"]),
            "cutoff_utc": str(snapshot["cutoff_utc"]),
            "original_state_sha256": str(snapshot["state_sha256"]),
            "recovered_state_sha256": recovered,
            "state_sha_match": True,
            "future_reference_rows_removed_n": removed,
            "vectors_changed": False,
        })

    core.writejl(out / "offline_state_snapshots.jsonl", repaired)
    core.writejl(out / "checkpoint_state_repair_audit.jsonl", audits)
    payload_names = [x for x in manifest["payload"].keys() if x != "offline_state_snapshots.jsonl"]
    payload_names.extend(["offline_state_snapshots.jsonl", "checkpoint_state_repair_audit.jsonl"])

    new_manifest = dict(manifest)
    new_manifest["schema_version"] = "football3-historical-pit-source-freeze-shard-v1-checkpoint-repaired"
    new_manifest["state_snapshot_sha256"] = sha_file(out / "offline_state_snapshots.jsonl")
    new_manifest["state_repair_audit_sha256"] = sha_file(out / "checkpoint_state_repair_audit.jsonl")
    new_manifest["payload"] = exact_files(out, payload_names)
    new_manifest["checkpoint_resume"] = {
        "status": "PIT_STATE_REFERENCE_ALIAS_REPAIRED_WITHOUT_SOURCE_REFETCH",
        "repair_rule": (
            "drop usage rows whose known_at is not strictly before that fixture T-15 cutoff; "
            "keep the originally frozen vectors byte-equivalent; require reconstructed vectors+usage "
            "SHA to equal the originally frozen per-fixture state_sha256"
        ),
        "source_network_requests": 0,
        "labels_read": 0,
        "scorer_invoked": False,
        "model_parameters_changed": False,
        "cohort_changed": False,
        "team_aliases_changed": False,
        "fixture_state_sha_checked_n": len(audits),
        "fixture_state_sha_matched_n": sum(int(x["state_sha_match"]) for x in audits),
        "fixture_state_sha_mismatch_n": 0,
        "future_reference_rows_removed_n": removed_total,
        "fixtures_with_future_reference_rows_removed_n": repaired_fixture_n,
    }
    if new_manifest["checkpoint_resume"]["fixture_state_sha_checked_n"] != 50:
        raise ShardError("checkpoint repair did not check exactly 50 fixture state SHAs")
    if new_manifest["checkpoint_resume"]["fixture_state_sha_matched_n"] != 50:
        raise ShardError("checkpoint repair fixture state SHA mismatch")
    core.dump(out / "source_freeze_manifest.json", new_manifest)
    return new_manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    result = repair_source(args.source, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
