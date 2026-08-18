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


def _greedy_pairs(frame, prefix: str, calipers: dict[str, float]):
    """Exact copy of the frozen pairing rule with safe Series field access."""
    draws = frame[frame["target"] == "D"].sort_values(["dt", "match_id"])
    wins = frame[frame["target"] == "OW"].copy()
    available = set(wins.index.tolist())
    out = []
    for draw_index, draw in draws.iterrows():
        candidates = []
        for win_index in list(available):
            win = wins.loc[win_index]
            if int(win["cid"]) != int(draw["cid"]):
                continue
            diffs = {key: abs(float(draw[key]) - float(win[key])) for key in calipers}
            if any(diffs[key] > calipers[key] for key in calipers):
                continue
            distance = sum((diffs[key] / calipers[key]) ** 2 for key in calipers)
            candidates.append(
                (
                    distance,
                    abs((draw["dt"] - win["dt"]).total_seconds()),
                    int(win["match_id"]),
                    win_index,
                )
            )
        if not candidates:
            continue
        _, _, _, win_index = min(candidates)
        available.remove(win_index)
        pair_id = f"{prefix}-{len(out)+1:04d}"
        out.append((pair_id, draw_index, win_index))
    if not out:
        return frame.iloc[0:0].copy(), []

    rows = []
    pair_meta = []
    for pair_id, draw_index, win_index in out:
        draw = frame.loc[draw_index]
        win = frame.loc[win_index]
        for role, row in (("D", draw), ("OW", win)):
            item = row.to_dict()
            item["pair_id"] = pair_id
            item["pair_role"] = role
            rows.append(item)
        pair_meta.append(
            {
                "pair_id": pair_id,
                "draw_match_id": int(draw["match_id"]),
                "onegoal_match_id": int(win["match_id"]),
                "competition_id": int(draw["cid"]),
                "distance": float(
                    sum(
                        ((float(draw[key]) - float(win[key])) / calipers[key]) ** 2
                        for key in calipers
                    )
                ),
                "abs_q_draw_cond_diff": abs(float(draw["q_draw_cond"]) - float(win["q_draw_cond"])),
                "abs_ha_gap_diff": abs(float(draw["abs_ha_gap"]) - float(win["abs_ha_gap"])),
                "abs_lambda_total_diff": abs(float(draw["lambda_total"]) - float(win["lambda_total"])),
            }
        )
    return frame.__class__(rows), pair_meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a01", required=True)
    parser.add_argument("--a02", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    evaluator = _load_evaluator()
    evaluator._is_goal = _score_event
    frozen_calipers = dict(evaluator.MATCH_CALIPERS)
    evaluator._greedy_pairs = lambda frame, prefix: _greedy_pairs(frame, prefix, frozen_calipers)
    evaluator.run(Path(args.a01), Path(args.a02), Path(args.out))


if __name__ == "__main__":
    main()
