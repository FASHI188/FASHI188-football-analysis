from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

import pandas as pd

from football3_core import Football3ContractError, assert_exact_one_to_one_join, key_set_sha256, ordered_key_sha256

LABEL_MANIFEST_SCHEMA = "football3_label_identity_manifest_v2"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _left_keys(left: pd.DataFrame, keys: Sequence[str]) -> list[tuple[str, ...]]:
    if not keys:
        raise Football3ContractError("label identity guard requires at least one key")
    for key in keys:
        if key not in left.columns:
            raise Football3ContractError(f"left identity key missing: {key}")
    if left.duplicated(list(keys)).any():
        raise Football3ContractError("left identity keys contain duplicates")
    rows: list[tuple[str, ...]] = []
    for row in left[list(keys)].itertuples(index=False, name=None):
        if any(type(value) is not str or not value for value in row):
            raise Football3ContractError("frozen identity keys must be nonempty strings")
        rows.append(tuple(row))
    return rows


def load_labels_with_frozen_manifest(
    left: pd.DataFrame,
    label_path: str | Path,
    manifest_path: str | Path,
    *,
    expected_manifest_sha256: str,
    keys: Sequence[str],
    target_columns: Sequence[str],
    expected_rows: int,
) -> pd.DataFrame:
    """Fail closed before target deserialization unless the immutable identity manifest matches.

    The manifest SHA is frozen outside the label table. Any later extra/missing label row
    requires either changing the label file (caught by the frozen label-file SHA) or changing
    the identity manifest (caught before the label file is opened). Only after both immutable
    checks pass is pandas allowed to deserialize target columns.
    """
    mp = Path(manifest_path)
    manifest_raw = mp.read_bytes()
    manifest_sha = _sha256_bytes(manifest_raw)
    if manifest_sha != expected_manifest_sha256:
        raise Football3ContractError("frozen label identity manifest SHA mismatch before label file access")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except Exception as exc:
        raise Football3ContractError(f"label identity manifest unreadable: {exc}") from exc
    if manifest.get("schema") != LABEL_MANIFEST_SCHEMA:
        raise Football3ContractError("invalid frozen label identity manifest schema")
    if manifest.get("keys") != list(keys) or manifest.get("key_types") != ["string"] * len(keys):
        raise Football3ContractError("frozen label identity key contract mismatch")
    if type(expected_rows) is not int or expected_rows < 0 or manifest.get("row_count") != expected_rows:
        raise Football3ContractError("frozen label identity row count mismatch")
    left_keys = _left_keys(left, keys)
    if len(left_keys) != expected_rows:
        raise Football3ContractError("left row count differs from frozen label identity manifest")
    if manifest.get("ordered_keys_sha256") != ordered_key_sha256(left_keys):
        raise Football3ContractError("frozen label ordered identity digest mismatch")
    if manifest.get("key_set_sha256") != key_set_sha256(left_keys):
        raise Football3ContractError("frozen label identity key-set digest mismatch")
    lp = Path(label_path)
    # Raw-byte integrity verification occurs only after the immutable key manifest is proven.
    # It does not deserialize targets; a changed extra/missing row necessarily changes this SHA.
    if manifest.get("label_file_sha256") != _file_sha256(lp):
        raise Football3ContractError("label file SHA mismatch before target deserialization")
    dtype = {key: "string" for key in keys}
    frame = pd.read_csv(lp, dtype=dtype)
    for col in target_columns:
        if col not in frame.columns:
            raise Football3ContractError(f"target column missing: {col}")
    return assert_exact_one_to_one_join(left, frame[list(keys) + list(target_columns)], keys=keys, expected_rows=expected_rows)
