from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from strict import GovernanceError, canonical_json_bytes, sha256_file

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"
FINAL_RUN = EVIDENCE / "final_protocol"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    data = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    universe = json.loads((EVIDENCE / "universe_manifest.json").read_text(encoding="utf-8"))
    rule = json.loads((EVIDENCE / "final_rule_freeze.json").read_text(encoding="utf-8"))
    features_path = EVIDENCE / "final_features.jsonl"
    if sha256_file(features_path) != universe["final_features_sha256"]:
        raise GovernanceError("final feature hash drift")
    if rule.get("status") != "FINAL_RULE_FROZEN_BEFORE_HOLDOUT_LABEL_ACCESS":
        raise GovernanceError("final rule is not frozen")

    rows = read_jsonl(features_path)
    if len(rows) != int(rule["final_fixture_n"]):
        raise GovernanceError("final fixture count drift")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        cutoff = str(row["cutoff"])
        if cutoff not in grouped:
            order.append(cutoff)
        grouped[cutoff].append(row)

    if FINAL_RUN.exists():
        shutil.rmtree(FINAL_RUN)
    FINAL_RUN.mkdir(parents=True)
    batches_dir = FINAL_RUN / "batches"
    batches_dir.mkdir()

    initial_state = EVIDENCE / "final_state_initial.json"
    if not initial_state.exists():
        raise GovernanceError("initial final state missing")
    current_state = FINAL_RUN / "state_0000.json"
    shutil.copyfile(initial_state, current_state)

    receipts = []
    for idx, cutoff in enumerate(order, start=1):
        rows_batch = grouped[cutoff]
        feat_path = batches_dir / f"{idx:04d}_features.jsonl"
        write_jsonl(feat_path, rows_batch)
        blind_path = batches_dir / f"{idx:04d}_blind.json"
        blind_manifest = batches_dir / f"{idx:04d}_blind_manifest.json"
        next_state = FINAL_RUN / f"state_{idx:04d}.json"
        score_path = batches_dir / f"{idx:04d}_scored.json"

        # Predictor subprocess receives no final-holdout label path or object.
        subprocess.check_call([
            sys.executable, str(HERE / "blind_predict.py"),
            "--state", str(current_state),
            "--features", str(feat_path),
            "--lock", str(EVIDENCE / "final_model_lock.json"),
            "--out", str(blind_path),
            "--manifest", str(blind_manifest),
        ])
        # Independent scorer subprocess verifies persisted blind bytes before label unseal.
        subprocess.check_call([
            sys.executable, str(HERE / "score_unseal.py"),
            "--blind", str(blind_path),
            "--blind-manifest", str(blind_manifest),
            "--features", str(feat_path),
            "--state", str(current_state),
            "--label-file", str(EVIDENCE / "final_labels.jsonl"),
            "--universe-manifest", str(EVIDENCE / "universe_manifest.json"),
            "--next-state", str(next_state),
            "--score-out", str(score_path),
        ])

        bm = json.loads(blind_manifest.read_text(encoding="utf-8"))
        receipts.append({
            "batch_index": idx,
            "cutoff": cutoff,
            "fixture_n": len(rows_batch),
            "features_sha256": sha256_file(feat_path),
            "blind_sha256": sha256_file(blind_path),
            "blind_manifest_sha256": sha256_file(blind_manifest),
            "scored_sha256": sha256_file(score_path),
            "state_in_sha256": sha256_file(current_state),
            "state_out_sha256": sha256_file(next_state),
            "manifest_blind_sha256_match": bm["blind_sha256"] == sha256_file(blind_path),
        })
        current_state = next_state

    receipt = {
        "schema_version": "football3-v2-final-protocol-receipt-v1",
        "final_fixture_n": len(rows),
        "batch_n": len(order),
        "first_cutoff": order[0],
        "last_cutoff": order[-1],
        "final_rule_freeze_sha256": sha256_file(EVIDENCE / "final_rule_freeze.json"),
        "initial_state_sha256": sha256_file(EVIDENCE / "final_state_initial.json"),
        "final_state_sha256": sha256_file(current_state),
        "batches": receipts,
        "blind_predictor_received_final_label_path": False,
        "scorer_process_separate": True,
        "same_cutoff_predict_all_before_update": True,
    }
    (FINAL_RUN / "protocol_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"final_fixture_n": len(rows), "batch_n": len(order), "final_state_sha256": receipt["final_state_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
