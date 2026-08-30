#!/usr/bin/env python3
"""Evidence-only utilities for the V2 final-holdout protocol.

This module must not train, tune, predict, unseal labels, score a final holdout,
or alter model/rule bytes. It only freezes and independently verifies evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

FORBIDDEN_LABEL_KEYS = {
    "actual_outcome",
    "home_goals",
    "away_goals",
    "result_label",
    "target_label",
    "final_label",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def file_record(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    raw = p.read_bytes()
    return {"sha256": sha256_bytes(raw), "bytes": len(raw)}


def canonical_json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def assert_label_free_json(path: Path | str) -> None:
    """Reject explicit outcome-label keys in JSON or JSONL evidence inputs."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        values = [json.loads(text)]
    found = sorted({k for v in values for k in _walk_keys(v) if k.casefold() in FORBIDDEN_LABEL_KEYS})
    if found:
        raise ValueError(f"label-bearing keys forbidden in blind-freeze input {p}: {found}")


def write_manifest(root: Path | str, metadata: dict[str, Any], manifest_name: str = "artifact_manifest.json") -> Path:
    """Seal every file under root except the root manifest itself.

    A nested artifact_manifest.json is payload of the outer artifact and MUST be
    included; excluding by basename would create an unsealed extra file.
    """
    root = Path(root)
    files: dict[str, Any] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel != manifest_name:
            files[rel] = file_record(p)
    manifest = dict(metadata)
    manifest["files"] = files
    out = root / manifest_name
    out.write_bytes(canonical_json_bytes(manifest))
    return out


def verify_manifest_tree(root: Path | str, manifest_name: str = "artifact_manifest.json") -> dict[str, Any]:
    root = Path(root)
    manifest_path = root / manifest_name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("files", {})
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).as_posix() != manifest_name
    }
    expected_set = set(expected)
    missing = sorted(expected_set - actual)
    extra = sorted(actual - expected_set)
    mismatch: list[str] = []
    for rel in sorted(expected_set & actual):
        got = file_record(root / rel)
        want = expected[rel]
        if got.get("sha256") != want.get("sha256") or got.get("bytes") != want.get("bytes"):
            mismatch.append(rel)
    return {
        "ok": not missing and not extra and not mismatch,
        "manifest_sha256": sha256_file(manifest_path),
        "payload_count": len(actual),
        "missing": missing,
        "mismatch": mismatch,
        "extra": extra,
        "manifest": manifest,
    }


def verify_zip(zip_path: Path | str, manifest_name: str = "artifact_manifest.json") -> dict[str, Any]:
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        crc_bad = zf.testzip()
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if manifest_name not in names:
            raise ValueError(f"missing {manifest_name} in ZIP")
        with tempfile.TemporaryDirectory() as td:
            zf.extractall(td)
            tree = verify_manifest_tree(Path(td), manifest_name)
    return {
        "ok": crc_bad is None and tree["ok"],
        "zip_sha256": sha256_file(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "crc_ok": crc_bad is None,
        "crc_bad_member": crc_bad,
        "zip_file_count": len(names),
        "manifest_sha256": tree["manifest_sha256"],
        "payload_count": tree["payload_count"],
        "missing": tree["missing"],
        "mismatch": tree["mismatch"],
        "extra": tree["extra"],
        "manifest": tree["manifest"],
    }


def build_blind_freeze_package(
    out_dir: Path | str,
    *,
    head: str,
    parent: str,
    model_rule_files: list[Path],
    holdout_identity: Path,
    blind_predictions: Path,
    predictor_input_contract: Path,
    scorer_input_boundary: Path,
    final_rules_and_gates: Path,
) -> dict[str, Any]:
    """Build a label-free evidence package from already-frozen inputs only."""
    if not HEX40.fullmatch(head) or not HEX40.fullmatch(parent):
        raise ValueError("head and parent must be exact 40-character lowercase git SHAs")
    assert_label_free_json(holdout_identity)
    assert_label_free_json(blind_predictions)

    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "payload").mkdir(parents=True)

    copies = {
        "final_holdout_identity.jsonl": holdout_identity,
        "blind_predictions.jsonl": blind_predictions,
        "predictor_input_contract.json": predictor_input_contract,
        "scorer_input_boundary.json": scorer_input_boundary,
        "final_rules_and_promotion_gates.json": final_rules_and_gates,
    }
    for dst, src in copies.items():
        shutil.copyfile(src, out / "payload" / dst)

    model_hashes: dict[str, Any] = {}
    for src in sorted(model_rule_files, key=lambda p: p.as_posix()):
        model_hashes[src.as_posix()] = file_record(src)
    model_hash_path = out / "payload" / "v2_model_rule_file_hashes.json"
    model_hash_path.write_bytes(canonical_json_bytes(model_hashes))

    freeze = {
        "schema_version": "football3-v2-blind-freeze-v1",
        "status": "BLIND_FREEZE_LABELS_STILL_SEALED",
        "exact_head": head,
        "direct_parent": parent,
        "model_rule_hashes_sha256": sha256_file(model_hash_path),
        "final_holdout_identity_sha256": sha256_file(out / "payload" / "final_holdout_identity.jsonl"),
        "blind_prediction_sha256": sha256_file(out / "payload" / "blind_predictions.jsonl"),
        "predictor_input_contract_sha256": sha256_file(out / "payload" / "predictor_input_contract.json"),
        "scorer_input_boundary_sha256": sha256_file(out / "payload" / "scorer_input_boundary.json"),
        "final_rules_and_promotion_gates_sha256": sha256_file(out / "payload" / "final_rules_and_promotion_gates.json"),
        "label_file_opened": False,
        "scorer_invoked": False,
    }
    (out / "blind_freeze.json").write_bytes(canonical_json_bytes(freeze))
    manifest = write_manifest(
        out,
        {
            "schema_version": "football3-v2-blind-freeze-artifact-v1",
            "head": head,
            "parent": parent,
            "labels_unsealed": False,
            "scorer_invoked": False,
        },
    )
    verified = verify_manifest_tree(out)
    if not verified["ok"]:
        raise RuntimeError(f"blind-freeze package failed self-verification: {verified}")
    return {
        "freeze": freeze,
        "manifest_path": str(manifest),
        "manifest_sha256": verified["manifest_sha256"],
        "payload_count": verified["payload_count"],
    }


def record_v1_artifact_audit(
    *,
    raw_metadata_file: Path | str,
    expected_digest: str,
    zip_path: Path | str,
    output_file: Path | str,
) -> dict[str, Any]:
    """Persist raw API evidence and exact, unnormalized digest comparison."""
    raw_path = Path(raw_metadata_file)
    raw_bytes = raw_path.read_bytes()
    metadata = json.loads(raw_bytes.decode("utf-8"))
    actual = metadata.get("digest")
    if not isinstance(actual, str):
        raise ValueError("artifact metadata digest is absent or not a string")
    report = {
        "schema_version": "football3-v1-reference-audit-v1",
        "raw_metadata_file": raw_path.name,
        "raw_metadata_sha256": sha256_bytes(raw_bytes),
        "raw_metadata_bytes": len(raw_bytes),
        "artifact_id": metadata.get("id"),
        "artifact_name": metadata.get("name"),
        "artifact_size_in_bytes": metadata.get("size_in_bytes"),
        "artifact_created_at": metadata.get("created_at"),
        "artifact_expires_at": metadata.get("expires_at"),
        "artifact_expired": metadata.get("expired"),
        "expected_digest": expected_digest,
        "actual_digest": actual,
        "expected_digest_length": len(expected_digest),
        "actual_digest_length": len(actual),
        "digest_exact_string_match": expected_digest == actual,
        "downloaded_zip_sha256": "sha256:" + sha256_file(zip_path),
        "downloaded_zip_bytes": Path(zip_path).stat().st_size,
    }
    Path(output_file).write_bytes(canonical_json_bytes(report))
    return report


def _cli() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    vz = sub.add_parser("verify-zip")
    vz.add_argument("zip")
    vz.add_argument("--output")
    va = sub.add_parser("record-v1-audit")
    va.add_argument("--raw-metadata-file", required=True)
    va.add_argument("--expected-digest", required=True)
    va.add_argument("--zip", required=True)
    va.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.cmd == "verify-zip":
        result = verify_zip(args.zip)
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        print(text, end="")
        return 0 if result["ok"] else 2
    if args.cmd == "record-v1-audit":
        result = record_v1_artifact_audit(
            raw_metadata_file=args.raw_metadata_file,
            expected_digest=args.expected_digest,
            zip_path=args.zip,
            output_file=args.output,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
