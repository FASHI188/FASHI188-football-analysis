from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def _load_evaluator():
    path = Path(__file__).with_name("evaluate_c069_matched_pair_draw_state_r1.py")
    spec = importlib.util.spec_from_file_location("c069_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _score_event(event: dict) -> bool:
    """Return True only for a score-changing Wyscout event.

    In the public Wyscout event stream, a scored shot/free-kick is followed by a
    goalkeeper Save attempt/Reflexes event that can also carry tag 101. Counting
    every tag-101 event therefore double-counts goals. Own-goal tag 102 is kept
    independently because the action can be attached to a non-shot event.
    """
    tags = {int(t["id"]) for t in event.get("tags", []) if "id" in t}
    if 102 in tags:
        return True
    return 101 in tags and event.get("eventName") in {"Shot", "Free Kick"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a01", required=True)
    parser.add_argument("--a02", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    evaluator = _load_evaluator()
    evaluator._is_goal = _score_event
    evaluator.run(Path(args.a01), Path(args.a02), Path(args.out))


if __name__ == "__main__":
    main()
