#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R40C_DIR = HERE.parent / "top1_r40c_role_aware_expected_xi"
sys.path.insert(0, str(R40C_DIR))
import run_experiment_r40c as r40c  # noqa: E402

r9 = r40c.r9

# Frozen before looking at this audit's labels.
SEASON_GAP_DAYS = 45
MAX_TARGET_DAYS_AFTER_OPENER = 21
LOOKBACK_XI = r40c.LOOKBACK_XI
MIN_PRIOR_XI = r40c.MIN_PRIOR_XI
DECAY = r40c.DECAY
MIN_AUDIT_SIDES = 80
MIN_MEAN_OVERLAP_GAIN = 0.25
MIN_POSITIVE_BLOCKS = 3
MAX_NEGATIVE_BLOCKS = 1


def expected_from_xis(xis):
    xs = list(xis)[-LOOKBACK_XI:]
    if len(xs) < MIN_PRIOR_XI:
        return None
    raw = defaultdict(float)
    for lag, xi in enumerate(reversed(xs)):
        w = DECAY ** lag
        for pid in xi:
            raw[int(pid)] += w
    ranked = sorted(raw.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:11]
    if len(ranked) < 10:
        return None
    return tuple(int(pid) for pid, _ in ranked)


def lineup_ids(player_map, row, team_id):
    xs = player_map.get((str(row["game_id"]), str(team_id)), [])
    ids = tuple(dict.fromkeys(int(pid) for pid, _ in xs))
    return ids if len(ids) == 11 else None


def overlap(pred, actual):
    return len(set(pred) & set(actual))


def jaccard(pred, actual):
    a, b = set(pred), set(actual)
    return len(a & b) / len(a | b) if (a | b) else 0.0


def summarize(obs):
    if not obs:
        return {"n": 0}
    ds = np.asarray([x["delta_overlap"] for x in obs], dtype=float)
    return {
        "n": len(obs),
        "legacy_overlap_mean": float(np.mean([x["legacy_overlap"] for x in obs])),
        "bridge_overlap_mean": float(np.mean([x["bridge_overlap"] for x in obs])),
        "mean_overlap_gain": float(np.mean(ds)),
        "median_overlap_gain": float(np.median(ds)),
        "legacy_jaccard_mean": float(np.mean([x["legacy_jaccard"] for x in obs])),
        "bridge_jaccard_mean": float(np.mean([x["bridge_jaccard"] for x in obs])),
        "mean_jaccard_gain": float(np.mean([x["bridge_jaccard"] - x["legacy_jaccard"] for x in obs])),
        "improved_sides": int(np.sum(ds > 0)),
        "equal_sides": int(np.sum(ds == 0)),
        "worsened_sides": int(np.sum(ds < 0)),
        "improved_rate": float(np.mean(ds > 0)),
        "worsened_rate": float(np.mean(ds < 0)),
        "mean_roster_shock": float(np.mean([x["roster_shock"] for x in obs])),
        "mean_gap_days": float(np.mean([x["gap_days"] for x in obs])),
        "mean_target_days_after_opener": float(np.mean([x["target_days_after_opener"] for x in obs])),
    }


def time_blocks(obs, n=4):
    xs = sorted(obs, key=lambda x: (x["target_date"], x["fixture_id"], x["team_id"]))
    blocks = []
    for part in np.array_split(np.arange(len(xs)), n):
        chunk = [xs[int(i)] for i in part]
        if not chunk:
            continue
        s = summarize(chunk)
        blocks.append({
            "first_date": chunk[0]["target_date"],
            "last_date": chunk[-1]["target_date"],
            **s,
        })
    return blocks


def run():
    rows = r9.load()
    player_map, player_sha, matched_starters, player_path = r40c.download_player_rows(rows)

    team_matches = defaultdict(list)
    for row in rows:
        dt = pd.Timestamp(row["date"])
        for side in ("home", "away"):
            tid = str(row[f"{side}_team"])
            xi = lineup_ids(player_map, row, tid)
            if xi is None:
                continue
            team_matches[tid].append({
                "date": dt,
                "date_s": dt.date().isoformat(),
                "fixture_id": str(row["game_id"]),
                "competition_id": str(row["competition_id"]),
                "team_id": tid,
                "side": side,
                "xi": xi,
            })

    observations = []
    detected_openers = 0
    for tid, matches in team_matches.items():
        matches.sort(key=lambda x: (x["date"], x["fixture_id"]))
        hist = deque(maxlen=LOOKBACK_XI)
        prev_date = None
        pending = None
        for m in matches:
            # Evaluate only the immediate next observed match after a detected opener.
            if pending is not None:
                days = int((m["date"] - pending["opener_date"]).days)
                if 0 < days <= MAX_TARGET_DAYS_AFTER_OPENER:
                    actual = m["xi"]
                    legacy = pending["legacy_expected"]
                    bridge = pending["opener_xi"]  # exact R42C/D current-season prior-XI bridge principle
                    lo = overlap(legacy, actual)
                    bo = overlap(bridge, actual)
                    observations.append({
                        "team_id": tid,
                        "fixture_id": m["fixture_id"],
                        "competition_id": m["competition_id"],
                        "target_date": m["date_s"],
                        "opener_fixture_id": pending["opener_fixture_id"],
                        "opener_date": pending["opener_date"].date().isoformat(),
                        "gap_days": pending["gap_days"],
                        "target_days_after_opener": days,
                        "legacy_overlap": lo,
                        "bridge_overlap": bo,
                        "delta_overlap": bo - lo,
                        "legacy_jaccard": jaccard(legacy, actual),
                        "bridge_jaccard": jaccard(bridge, actual),
                        "roster_shock": 11 - overlap(legacy, bridge),
                    })
                pending = None

            if prev_date is not None:
                gap_days = int((m["date"] - prev_date).days)
                if gap_days >= SEASON_GAP_DAYS and len(hist) >= MIN_PRIOR_XI:
                    legacy = expected_from_xis(hist)
                    if legacy is not None:
                        detected_openers += 1
                        pending = {
                            "opener_date": m["date"],
                            "opener_fixture_id": m["fixture_id"],
                            "opener_xi": m["xi"],
                            "legacy_expected": legacy,
                            "gap_days": gap_days,
                        }
            hist.append(frozenset(m["xi"]))
            prev_date = m["date"]

    blocks = time_blocks(observations)
    positive_blocks = sum(1 for x in blocks if x.get("mean_overlap_gain", 0) > 0)
    negative_blocks = sum(1 for x in blocks if x.get("mean_overlap_gain", 0) < 0)
    overall = summarize(observations)
    by_shock = {
        "shock_0_2": summarize([x for x in observations if x["roster_shock"] <= 2]),
        "shock_3_4": summarize([x for x in observations if 3 <= x["roster_shock"] <= 4]),
        "shock_5_plus": summarize([x for x in observations if x["roster_shock"] >= 5]),
    }
    passed = bool(
        overall.get("n", 0) >= MIN_AUDIT_SIDES
        and overall.get("mean_overlap_gain", -999) >= MIN_MEAN_OVERLAP_GAIN
        and overall.get("improved_sides", 0) > overall.get("worsened_sides", 0)
        and positive_blocks >= MIN_POSITIVE_BLOCKS
        and negative_blocks <= MAX_NEGATIVE_BLOCKS
    )

    result = {
        "schema_version": "football3-r42f-historical-season-bridge-lineup-audit-v1",
        "status": "COMPLETE",
        "classification": "HISTORICAL_MECHANISM_AUDIT_TARGET_XI_USED_ONLY_AS_EVALUATION_LABEL",
        "formal_weight": 0,
        "question": "After a >=45-day observed team gap, does using the completed opener XI as the next-match expected XI improve next-match starter identification versus the stale pre-gap R40C expected XI?",
        "governance": {
            "target_result_used": False,
            "target_xi_used_for_candidate_construction": False,
            "target_xi_used_only_as_evaluation_label": True,
            "opener_score_xg_used": False,
            "opener_xi_membership_used": True,
            "parameter_search": False,
            "season_gap_days_frozen": SEASON_GAP_DAYS,
            "max_target_days_after_opener_frozen": MAX_TARGET_DAYS_AFTER_OPENER,
            "lookback_xi": LOOKBACK_XI,
            "decay": DECAY,
        },
        "source": {
            "r9b_snapshot_rows": len(rows),
            "r9b_first_date": rows[0]["date"],
            "r9b_last_date": rows[-1]["date"],
            "fixture_players_sha256": player_sha,
            "matched_starter_rows": matched_starters,
        },
        "cohort": {
            "teams_with_lineups": len(team_matches),
            "detected_gap_openers_with_stale_expected_xi": detected_openers,
            "auditable_next_match_sides": len(observations),
        },
        "overall": overall,
        "by_roster_shock": by_shock,
        "time_blocks": blocks,
        "gate": {
            "min_audit_sides": MIN_AUDIT_SIDES,
            "min_mean_overlap_gain": MIN_MEAN_OVERLAP_GAIN,
            "min_positive_blocks": MIN_POSITIVE_BLOCKS,
            "max_negative_blocks": MAX_NEGATIVE_BLOCKS,
            "positive_blocks": positive_blocks,
            "negative_blocks": negative_blocks,
            "passed": passed,
            "action": "PROCEED_TO_R42G_OUTCOME_IMPACT_AUDIT" if passed else "DO_NOT_CLAIM_GENERAL_SEASON_BRIDGE_VALUE",
        },
        "limitations": [
            "Season boundaries are inferred from an observed >=45-day team gap inside the frozen 20k xG snapshot; no season metadata is used.",
            "This audit measures expected-XI membership quality only, not 1X2 accuracy.",
            "The target XI is an evaluation label and is never used to construct either stale or bridge XI.",
            "The bridge deliberately uses opener XI membership only; opener result, score and xG are excluded.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42f_lineup_audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "observations_r42f.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in observations) + ("\n" if observations else ""), encoding="utf-8")
    try:
        Path(player_path).unlink()
    except Exception:
        pass
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42f_lineup_audit.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["target_result_used"] is False
    assert g["target_xi_used_for_candidate_construction"] is False
    assert g["target_xi_used_only_as_evaluation_label"] is True
    assert g["opener_score_xg_used"] is False and g["parameter_search"] is False
    assert d["overall"]["n"] == d["cohort"]["auditable_next_match_sides"]
    assert len(d["time_blocks"]) <= 4
    print("R42F_HISTORICAL_SEASON_BRIDGE_LINEUP_AUDIT_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42f_lineup_audit.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
