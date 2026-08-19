#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

BASE = Path(__file__).with_name("run_c072n17_hda_increment_pt_development.py")
spec = importlib.util.spec_from_file_location("c072n17_frozen", BASE)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen N17 runner")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

_original_dumps = m.json.dumps


def _json_default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(obj, *args, **kwargs):
    kwargs.setdefault("default", _json_default)
    return _original_dumps(obj, *args, **kwargs)


# Execution-only correction. All scientific functions/constants remain those in the frozen module.
m.json.dumps = _dumps

if __name__ == "__main__":
    raise SystemExit(m.main())
