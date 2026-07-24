#!/usr/bin/env python3
"""V6.18.1c strict daily-PIT rebuild and controlled total-goals rerun.

Reason for this repair:
The original V6.18.1 formal-row builder appended each completed match to history
inside a same-date loop. The processed MatchRow carries a date at UTC midnight, not a
verified kickoff timestamp, so alphabetical same-date ordering cannot establish which
result was available before another match. This is a PIT leak risk.

Repair:
- all predictions on a calendar date see only history from strictly earlier dates;
- warmup/team counts are also frozen at the start of the date;
- all matches from that date are appended to history only after every prediction for
  that date has been produced;
- shot/SOT/corner features retain the existing same-date freeze.

The V6.18.1b four-arm design is otherwise unchanged:
A formal P(T)
B calibration = log formal P(T)
C competition = B + competition one-hot
D shot = C + lagged shots/SOT/corners

Candidate C grid, chronological train/validation/test split, proper-score hard gates,
and matched-C=0.01 comparison are unchanged. 2025/26 remains retrospective OOS only.
formal_weight=0; no CURRENT/runtime probability change.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as datefix
import v6_total_shot_increment_control_v6181b as control

OUT = base.ROOT / "manifests" / "v6_strict_daily_pit_total_v6181c_status.json"
TRAIN_SEASONS = {"2022/23", "2023/24"}
VALID_SEASON = "2024/25"
TEST_SEASON = "2025/26"
MATCHED_C = 0.01


def strict_formal_score_rows(shot_lookup: dict[tuple[Any, ...], dict[str, float]]):
    """Build formal score-matrix rows with history frozen for the entire date."""
    cfg = base.load_config()
    rows = []
    meta = {}
    for cid in base.COMPS:
        params_map = base.ou.params_by_season(cid)
        matches = [m for m in base.read_processed_matches(cid) if str(m.season) in base.SEASONS]
        byseason = defaultdict(list)
        for m in matches:
            byseason[str(m.season)].append(m)
        meta[cid] = {}
        for season in base.SEASONS:
            sm = byseason.get(season, [])
            params = params_map.get(season)
            if not sm or not params:
                meta[cid][season] = {
                    "matches": len(sm), "rows": 0, "prediction_failures": 0,
                    "reason": "NO_MATCHES_OR_PARAMS",
                }
                continue
            temp = base.ou.calibrator(cid, season)
            bydate = defaultdict(list)
            for m in sm:
                bydate[m.date].append(m)
            hist = []
            hc = Counter()
            ac = Counter()
            count = 0
            failures = 0
            warmc = int(cfg["validation"]["warmup_competition_matches"])
            warmt = int(cfg["validation"]["warmup_team_matches"])
            for dt in sorted(bydate):
                todays = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
                # All rows on this date see identical start-of-date history/count state.
                for m in todays:
                    key = (cid, season, m.date.isoformat(), m.home_team, m.away_team)
                    feat = shot_lookup.get(key)
                    if (
                        feat is not None
                        and len(hist) >= warmc
                        and hc[m.home_team] >= warmt
                        and ac[m.away_team] >= warmt
                    ):
                        try:
                            pred = base.predict_from_history(
                                hist,
                                cid,
                                season,
                                m.home_team,
                                m.away_team,
                                m.date,
                                selected_parameters=params,
                                use_team_effects=True,
                            )
                            matrix = base.temperature_scale_matrix(
                                pred["probabilities"]["score_matrix"], temp
                            )
                            rows.append({
                                "competition_id": cid,
                                "season": season,
                                "date": m.date.isoformat(),
                                "home_team": m.home_team,
                                "away_team": m.away_team,
                                "home_goals": int(m.home_goals),
                                "away_goals": int(m.away_goals),
                                "actual_total_raw": int(m.home_goals) + int(m.away_goals),
                                "formal_matrix": matrix,
                                "shots": feat,
                            })
                            count += 1
                        except Exception:
                            failures += 1
                # Only now may final results from this date enter the state.
                for m in todays:
                    hist.append(m)
                    hc[m.home_team] += 1
                    ac[m.away_team] += 1
            meta[cid][season] = {
                "matches": len(sm),
                "rows": count,
                "prediction_failures": failures,
                "same_date_history_frozen": True,
            }
    rows.sort(
        key=lambda r: (
            r["season"], r["date"], r["competition_id"], r["home_team"], r["away_team"]
        )
    )
    return rows, meta


def strict_total_rows(shot_lookup):
    score_rows, meta = strict_formal_score_rows(shot_lookup)
    out = []
    for r in score_rows:
        out.append({
            "competition_id": r["competition_id"],
            "season": r["season"],
            "date": r["date"],
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "actual": min(base.TOTAL_CAP, int(r["actual_total_raw"])),
            "formal": base.total_vec(r["formal_matrix"]),
            "shots": r["shots"],
        })
    return out, meta


def metrics(rows, getter):
    return base.metrics(rows, getter)


def fit(rows, mode, c_value, shot_names, comps):
    return control.fit(rows, mode, c_value, shot_names, comps)


def probs(model, row, mode, shot_names, comps):
    return control.probs(model, row, mode, shot_names, comps)


def select(train, valid, mode, shot_names, comps):
    baseline = metrics(valid, lambda r: r["formal"])
    board = []
    for c in base.CANDIDATE_C:
        model = fit(train, mode, c, shot_names, comps)
        m = metrics(valid, lambda r, model=model: probs(model, r, mode, shot_names, comps))
        m["C"] = c
        m["proper_noninferior"] = bool(
            m["rps"] <= baseline["rps"] and m["logloss"] <= baseline["logloss"]
        )
        board.append(m)
    eligible = [m for m in board if m["proper_noninferior"]]
    chosen = max(
        eligible,
        key=lambda m: (m["top1_rate"], -m["rps"], -m["logloss"], -m["C"]),
    ) if eligible else None
    return baseline, board, chosen


def evaluate_selected(train, valid, test, mode, shot_names, comps):
    vb, board, chosen = select(train, valid, mode, shot_names, comps)
    result = {"validation_baseline": vb, "leaderboard": board, "selected": chosen}
    if chosen is None:
        result["status"] = "NO_PROPER_NONINFERIOR_CANDIDATE"
        return result
    model = fit(train + valid, mode, chosen["C"], shot_names, comps)
    result.update({
        "status": "PASS",
        "test": metrics(test, lambda r: probs(model, r, mode, shot_names, comps)),
    })
    return result


def evaluate_matched(train, valid, test, mode, shot_names, comps):
    model = fit(train + valid, mode, MATCHED_C, shot_names, comps)
    return metrics(test, lambda r: probs(model, r, mode, shot_names, comps))


def delta(a, b):
    return {
        "top1_rate": a["top1_rate"] - b["top1_rate"],
        "rps": a["rps"] - b["rps"],
        "logloss": a["logloss"] - b["logloss"],
    }


def main():
    raw, _ = base.raw_stat_matches()
    lookup, _ = datefix.lagged_shot_lookup_fixed(raw)
    rows, strict_meta = strict_total_rows(lookup)
    if not rows:
        raise RuntimeError("strict PIT rebuild produced zero rows")
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == VALID_SEASON]
    test = [r for r in rows if r["season"] == TEST_SEASON]
    shot_names, comps = base.feature_names(rows)
    if min(len(train), len(valid), len(test)) < 1000:
        raise RuntimeError(
            f"insufficient strict rows train={len(train)} valid={len(valid)} test={len(test)}"
        )

    formal_test = metrics(test, lambda r: r["formal"])
    selected = {}
    matched = {}
    for mode in ("calibration", "competition", "shot"):
        selected[mode] = evaluate_selected(train, valid, test, mode, shot_names, comps)
        matched[mode] = evaluate_matched(train, valid, test, mode, shot_names, comps)

    matched_deltas = {
        "competition_minus_calibration": delta(matched["competition"], matched["calibration"]),
        "shot_minus_competition": delta(matched["shot"], matched["competition"]),
        "shot_minus_calibration": delta(matched["shot"], matched["calibration"]),
        "shot_minus_formal": delta(matched["shot"], formal_test),
    }
    selected_deltas = {}
    if selected["competition"].get("test") and selected["shot"].get("test"):
        selected_deltas["shot_minus_competition"] = delta(
            selected["shot"]["test"], selected["competition"]["test"]
        )

    payload = {
        "schema_version": "V6.18.1c-strict-daily-pit-total-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "STRICT_DAILY_PIT_RETROSPECTIVE_OOS_REPAIR",
        "invalidates_for_inference": [
            "V6.18.1-shot-informed-direct-total-r2",
            "V6.18.1b-shot-increment-control-r1",
            "V6.18.2-frozen-domain-diagnostic-r1",
            "V6.18.3-prospective-shot-total-freeze-r1",
            "V6.18.3-prospective-shot-total-freeze-r2-if-produced",
        ],
        "repair": {
            "issue": "same-date final results could enter formal prediction history because only date, not verified kickoff time, is available",
            "policy": "freeze formal history and warmup counts at start of date; update after entire date",
            "parameter_changes": False,
            "feature_changes": False,
            "candidate_grid_changes": False,
            "test_based_tuning": False,
        },
        "rows": {
            "all": len(rows), "train": len(train), "validation": len(valid), "test": len(test)
        },
        "strict_formal_meta": strict_meta,
        "formal_test": formal_test,
        "selected_arms": selected,
        "selected_deltas": selected_deltas,
        "matched_C_0_01": matched,
        "matched_deltas": matched_deltas,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "old_leaky_research_receipts_may_not_support_promotion": True,
            "prospective_freeze_requires_rebuild_after_this_receipt": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "rows": payload["rows"],
        "formal_test": formal_test,
        "selected": {
            k: {"status": v["status"], "selected": v.get("selected"), "test": v.get("test")}
            for k, v in selected.items()
        },
        "matched": matched,
        "matched_deltas": matched_deltas,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
