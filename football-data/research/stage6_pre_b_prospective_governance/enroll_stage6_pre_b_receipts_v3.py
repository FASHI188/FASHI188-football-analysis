from __future__ import annotations

import importlib.util
import pathlib
import sys

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


def install_prediction_helper(helper) -> None:
    fn = getattr(helper, "prediction_record", None)
    if not callable(fn):
        raise EnrollmentError("frozen V2 helper missing prediction_record")
    # V1 enrollment calls legacy.formal.prediction_record(...).  The historical
    # stress replay proved that helper comes from historical_xg_fusion_v2.py,
    # not from new_engine_v1.formal_fusion_v2.  Patch only this helper symbol;
    # the formal state object and all frozen state bytes remain untouched.
    base.legacy.formal.prediction_record = fn


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
    install_prediction_helper(helper)
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
