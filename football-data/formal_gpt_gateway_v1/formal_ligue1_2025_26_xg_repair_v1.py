#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import formal_state_integrity_full_rebuild_v1 as full_rebuild
import runtime as rt

SCHEMA = "football3-ligue1-2025-26-xg-repair-loader-v2"
COMP = "FRA_Ligue1"
BASE_REL = Path("football-data/evidence/xg/understat_2025_26_linked/FRA_Ligue1.jsonl")
REPAIR_REL = Path("football-data/evidence/xg/understat_2025_26_linked_repairs/FRA_Ligue1.jsonl")
MANIFEST_REL = Path("football-data/manifests/ligue1_2025_26_xg_repair_v1.json")
EXPECTED_BASE_SHA = "c15f1075b4cad209719a9a92a9863c51c658df1d"
EXPECTED_BASE_ROWS = 304
EXPECTED_REPAIR_ROWS = 3
EXPECTED_EXCLUDED_BASE_ROWS = 1
EXPECTED_COMBINED_ROWS = 306
_ORIGINAL_LOAD = full_rebuild._load_jsonl
_ORIGINAL_SHA_FILE = rt._sha_file
_ORIGINAL_AUGMENT = full_rebuild.augment_available_linked_xg
_ACTIVE_REPO_ROOT: Path | None = None


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        rt._normalize_team(str(row.get("official_home_team") or "")),
        rt._normalize_team(str(row.get("official_away_team") or "")),
    )


def _combined_rows(repo_root: Path, base_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    repair_path = repo_root / REPAIR_REL
    manifest_path = repo_root / MANIFEST_REL
    if not repair_path.is_file() or not manifest_path.is_file():
        raise rt.RuntimeGateError("Ligue1 2025/26 xG repair file/manifest missing")
    if _ORIGINAL_SHA_FILE(base_path) != EXPECTED_BASE_SHA:
        raise rt.RuntimeGateError("Ligue1 2025/26 frozen base linked SHA drift")
    base = _ORIGINAL_LOAD(base_path); repair = _ORIGINAL_LOAD(repair_path)
    if len(base) != EXPECTED_BASE_ROWS or len(repair) != EXPECTED_REPAIR_ROWS:
        raise rt.RuntimeGateError("Ligue1 2025/26 repair cardinality mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if type(manifest) is not dict or manifest.get("schema_version") != "football3-ligue1-2025-26-xg-repair-v1":
        raise rt.RuntimeGateError("Ligue1 2025/26 repair manifest schema mismatch")
    repair_sha = _ORIGINAL_SHA_FILE(repair_path)
    if str(manifest.get("base_linked_sha256")) != EXPECTED_BASE_SHA or str(manifest.get("repair_sha256")) != repair_sha:
        raise rt.RuntimeGateError("Ligue1 2025/26 repair manifest SHA mismatch")
    if int(manifest.get("base_linked_rows", -1)) != EXPECTED_BASE_ROWS or int(manifest.get("repair_rows", -1)) != EXPECTED_REPAIR_ROWS or int(manifest.get("excluded_base_rows", -1)) != EXPECTED_EXCLUDED_BASE_ROWS or int(manifest.get("combined_rows", -1)) != EXPECTED_COMBINED_ROWS or int(manifest.get("formal_rows", -1)) != EXPECTED_COMBINED_ROWS:
        raise rt.RuntimeGateError("Ligue1 2025/26 repair manifest count mismatch")
    excluded = manifest.get("excluded_base_mappings") or []
    if type(excluded) is not list or len(excluded) != EXPECTED_EXCLUDED_BASE_ROWS:
        raise rt.RuntimeGateError("Ligue1 2025/26 excluded-base manifest mismatch")
    exclude_keys = set()
    for x in excluded:
        pair = x.get("pair") if type(x) is dict else None
        if type(pair) is not list or len(pair) != 2:
            raise rt.RuntimeGateError("Ligue1 2025/26 excluded pair malformed")
        exclude_keys.add((str(pair[0]), str(pair[1])))
    kept = [r for r in base if _row_key(r) not in exclude_keys]
    if len(kept) != EXPECTED_BASE_ROWS - EXPECTED_EXCLUDED_BASE_ROWS:
        raise rt.RuntimeGateError("Ligue1 2025/26 excluded base row not unique")
    rows = kept + repair
    keys = [_row_key(r) for r in rows]
    ids = [str(r.get("understat_match_id") or "") for r in rows]
    if len(rows) != EXPECTED_COMBINED_ROWS or len(set(keys)) != EXPECTED_COMBINED_ROWS or len(set(ids)) != EXPECTED_COMBINED_ROWS or any(not x for x in ids):
        raise rt.RuntimeGateError("Ligue1 2025/26 repaired identity not one-to-one")
    if any(r.get("target_match_xg_allowed_as_predictor") is not False for r in repair):
        raise rt.RuntimeGateError("Ligue1 2025/26 repair target predictor prohibition missing")
    return rows, {"repair_sha256": repair_sha, "manifest": manifest}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if _ACTIVE_REPO_ROOT is not None and path.resolve() == (_ACTIVE_REPO_ROOT / BASE_REL).resolve():
        rows, _ = _combined_rows(_ACTIVE_REPO_ROOT, path)
        return rows
    return _ORIGINAL_LOAD(path)


def _sha_file(path: Path) -> str:
    if _ACTIVE_REPO_ROOT is not None and path.resolve() == (_ACTIVE_REPO_ROOT / BASE_REL).resolve():
        _, meta = _combined_rows(_ACTIVE_REPO_ROOT, path)
        return rt._sha_bytes(rt._canon_bytes({
            "schema_version": SCHEMA,
            "base_sha256": EXPECTED_BASE_SHA,
            "repair_sha256": meta["repair_sha256"],
            "combined_rows": EXPECTED_COMBINED_ROWS,
        }))
    return _ORIGINAL_SHA_FILE(path)


def augment_available_linked_xg(repo_root: Path, history, base_labels: dict[str, Any], target_cutoff):
    global _ACTIVE_REPO_ROOT
    repair = repo_root / REPAIR_REL; manifest = repo_root / MANIFEST_REL
    if not repair.is_file() and not manifest.is_file():
        return _ORIGINAL_AUGMENT(repo_root, history, base_labels, target_cutoff)
    if not repair.is_file() or not manifest.is_file():
        raise rt.RuntimeGateError("partial Ligue1 2025/26 xG repair publication")
    _ACTIVE_REPO_ROOT = repo_root.resolve()
    old_load, old_sha = full_rebuild._load_jsonl, rt._sha_file
    try:
        full_rebuild._load_jsonl = _load_jsonl
        rt._sha_file = _sha_file
        labels, audit = _ORIGINAL_AUGMENT(repo_root, history, base_labels, target_cutoff)
    finally:
        full_rebuild._load_jsonl = old_load
        rt._sha_file = old_sha
        _ACTIVE_REPO_ROOT = None
    spec = (((audit.get("files") or {}).get(COMP)) or {})
    spec["repair_schema"] = SCHEMA
    spec["repair_base_path"] = str(BASE_REL)
    spec["repair_base_sha256"] = EXPECTED_BASE_SHA
    spec["repair_patch_path"] = str(REPAIR_REL)
    spec["repair_patch_sha256"] = _ORIGINAL_SHA_FILE(repo_root / REPAIR_REL)
    spec["repair_manifest_path"] = str(MANIFEST_REL)
    spec["repair_excluded_base_rows"] = EXPECTED_EXCLUDED_BASE_ROWS
    spec["repair_model_parameters_or_weights_changed"] = False
    return labels, audit


def install() -> dict[str, Any]:
    full_rebuild.augment_available_linked_xg = augment_available_linked_xg
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "competition_id": COMP,
        "season": "2025/26",
        "repair_path": str(REPAIR_REL),
        "manifest_path": str(MANIFEST_REL),
        "expected_base_rows": EXPECTED_BASE_ROWS,
        "expected_repair_rows": EXPECTED_REPAIR_ROWS,
        "expected_excluded_base_rows": EXPECTED_EXCLUDED_BASE_ROWS,
        "expected_combined_rows": EXPECTED_COMBINED_ROWS,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
