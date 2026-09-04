from __future__ import annotations

import importlib.util
import pathlib
import sys
from collections.abc import Callable
from typing import Any

import enroll_stage6_pre_b_receipts_v2 as v2wrap


base = v2wrap.base
EnrollmentError = base.EnrollmentError


def loadmod(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise EnrollmentError(f"cannot load frozen V2 helper {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def frozen_prediction_callable(helper) -> Callable[..., dict[str, Any]]:
    fn = getattr(helper, "prediction_record", None)
    if not callable(fn):
        raise EnrollmentError("frozen V2 helper missing prediction_record")
    return fn


def wrap_reconstruct_cutoff_state(original, helper):
    """Bind the exact frozen Formal V2 helper only after V1 reconstructs legacy.

    The V1 runner keeps ``legacy`` local to reconstruct_cutoff_state/main, so a
    module-global patch cannot work.  This adapter leaves reconstruction bytes
    and state untouched; it only attaches the official frozen helper callable
    to the returned legacy.formal namespace expected by the unchanged V1 call.
    """
    fn = frozen_prediction_callable(helper)

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if not isinstance(result, tuple) or len(result) != 7:
            raise EnrollmentError("unexpected cutoff reconstruction return contract")
        legacy = result[5]
        formal = getattr(legacy, "formal", None)
        if formal is None:
            raise EnrollmentError("reconstructed legacy formal module missing")
        setattr(formal, "prediction_record", fn)
        if getattr(formal, "prediction_record", None) is not fn:
            raise EnrollmentError("frozen V2 prediction_record exact binding failed")
        return result

    return wrapped


def extract_helper_arg(argv: list[str]) -> pathlib.Path:
    try:
        i = argv.index("--v2-helper")
    except ValueError as exc:
        raise EnrollmentError("--v2-helper required") from exc
    if i + 1 >= len(argv):
        raise EnrollmentError("--v2-helper value missing")
    p = pathlib.Path(argv[i + 1])
    del argv[i:i + 2]
    return p


def main() -> int:
    helper_path = extract_helper_arg(sys.argv)
    helper = loadmod("stage6_frozen_historical_xg_fusion_v2", helper_path)
    original = base.reconstruct_cutoff_state
    base.reconstruct_cutoff_state = wrap_reconstruct_cutoff_state(original, helper)
    try:
        # Preserve the V2 frozen-queue-order adapter; calling base.main() here
        # would silently bypass the already-proven queue-order repair.
        return v2wrap.main()
    finally:
        base.reconstruct_cutoff_state = original


if __name__ == "__main__":
    raise SystemExit(main())
