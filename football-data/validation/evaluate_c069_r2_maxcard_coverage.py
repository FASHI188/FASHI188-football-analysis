from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

import run_c069_matched_pair_draw_state_r1 as r1run


SCHEMA_VERSION = "C069_MATCHED_PAIR_DRAW_STATE_R2_COVERAGE"
EXPECTED_SOURCE = {
    "source_matches": 800,
    "regular_matches": 795,
    "eligible_rows": 327,
    "eligible_draws": 127,
    "eligible_onegoal_wins": 200,
}
EXPECTED_GREEDY = {
    "fold_1": {"train": 27, "test": 18},
    "fold_2": {"train": 51, "test": 14},
    "fold_3": {"train": 81, "test": 12},
}


def _edge(draw, win, calipers: dict[str, float]):
    if int(draw["cid"]) != int(win["cid"]):
        return None
    diffs = {key: abs(float(draw[key]) - float(win[key])) for key in calipers}
    if any(diffs[key] > calipers[key] for key in calipers):
        return None
    distance = float(sum((diffs[key] / calipers[key]) ** 2 for key in calipers))
    return distance, diffs


def _maximum_cardinality(left: list[int], adjacency: dict[int, list[int]]) -> int:
    """Exact bipartite maximum cardinality via repeated augmenting paths.

    The graphs here are small (well below the 327 eligible-row pool), so the
    simple deterministic Kuhn augmenting-path algorithm is sufficient and acts
    as an independent cardinality certificate for the assignment solver.
    """
    match_right: dict[int, int] = {}

    def augment(u: int, seen: set[int]) -> bool:
        for v in adjacency.get(u, []):
            if v in seen:
                continue
            seen.add(v)
            if v not in match_right or augment(match_right[v], seen):
                match_right[v] = u
                return True
        return False

    matched = 0
    for u in left:
        if augment(u, set()):
            matched += 1
    return matched


def _optimal_pairs(frame, prefix: str, calipers: dict[str, float]):
    draws_all = frame[frame["target"] == "D"].sort_values(["cid", "dt", "match_id"])
    wins_all = frame[frame["target"] == "OW"].sort_values(["cid", "dt", "match_id"])

    selected: list[tuple[str, int, int, float, dict[str, float]]] = []
    certificate_total = 0

    competition_ids = sorted(set(draws_all["cid"].astype(int)) | set(wins_all["cid"].astype(int)))
    for cid in competition_ids:
        draws = draws_all[draws_all["cid"].astype(int) == cid]
        wins = wins_all[wins_all["cid"].astype(int) == cid]
        if draws.empty or wins.empty:
            continue

        draw_indices = list(draws.index)
        win_indices = list(wins.index)
        adjacency: dict[int, list[int]] = {u: [] for u in draw_indices}
        edge_data: dict[tuple[int, int], tuple[float, dict[str, float]]] = {}

        for draw_index in draw_indices:
            draw = frame.loc[draw_index]
            for win_index in win_indices:
                win = frame.loc[win_index]
                info = _edge(draw, win, calipers)
                if info is None:
                    continue
                adjacency[draw_index].append(win_index)
                edge_data[(draw_index, win_index)] = info
            adjacency[draw_index].sort(
                key=lambda win_index: (
                    edge_data[(draw_index, win_index)][0],
                    abs((frame.loc[draw_index, "dt"] - frame.loc[win_index, "dt"]).total_seconds()),
                    int(frame.loc[win_index, "match_id"]),
                )
            )

        certificate = _maximum_cardinality(draw_indices, adjacency)
        certificate_total += certificate

        n_draw = len(draw_indices)
        n_win = len(win_indices)
        max_pairs = min(n_draw, n_win)
        # Every legal edge has normalized squared distance <= 3. Therefore a
        # single dummy penalty larger than the maximum possible total legal-edge
        # cost makes cardinality the primary objective. Among equal-cardinality
        # assignments, the Hungarian objective minimizes total legal distance.
        unmatched_penalty = float(4 * (max_pairs + 1))
        disallowed_penalty = float(unmatched_penalty * 3)

        cost = np.full((n_draw, n_win + n_draw), unmatched_penalty, dtype=float)
        if n_win:
            cost[:, :n_win] = disallowed_penalty
        for i, draw_index in enumerate(draw_indices):
            for j, win_index in enumerate(win_indices):
                info = edge_data.get((draw_index, win_index))
                if info is not None:
                    cost[i, j] = info[0]

        row_ind, col_ind = linear_sum_assignment(cost)
        local = []
        for i, j in zip(row_ind.tolist(), col_ind.tolist()):
            if j >= n_win:
                continue
            draw_index = draw_indices[i]
            win_index = win_indices[j]
            info = edge_data.get((draw_index, win_index))
            if info is None:
                raise RuntimeError("assignment selected a disallowed real edge")
            distance, diffs = info
            local.append((draw_index, win_index, distance, diffs))

        if len(local) != certificate:
            raise RuntimeError(
                f"maximum-cardinality certificate mismatch cid={cid}: "
                f"assignment={len(local)} certificate={certificate}"
            )

        local.sort(key=lambda x: (x[2], int(frame.loc[x[0], "match_id"]), int(frame.loc[x[1], "match_id"])))
        for draw_index, win_index, distance, diffs in local:
            pair_id = f"{prefix}-{len(selected)+1:04d}"
            selected.append((pair_id, draw_index, win_index, distance, diffs))

    meta = []
    for pair_id, draw_index, win_index, distance, diffs in selected:
        draw = frame.loc[draw_index]
        win = frame.loc[win_index]
        meta.append(
            {
                "pair_id": pair_id,
                "draw_match_id": int(draw["match_id"]),
                "onegoal_match_id": int(win["match_id"]),
                "competition_id": int(draw["cid"]),
                "distance": float(distance),
                "abs_q_draw_cond_diff": float(diffs["q_draw_cond"]),
                "abs_ha_gap_diff": float(diffs["abs_ha_gap"]),
                "abs_lambda_total_diff": float(diffs["lambda_total"]),
            }
        )

    if len(meta) != certificate_total:
        raise RuntimeError(
            f"global maximum-cardinality certificate mismatch: selected={len(meta)} certificate={certificate_total}"
        )
    return meta, certificate_total


def _distance_summary(meta: list[dict]):
    if not meta:
        return {"pairs": 0, "mean": None, "median": None, "max": None}
    values = np.asarray([x["distance"] for x in meta], dtype=float)
    return {
        "pairs": int(len(values)),
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "max": float(values.max()),
    }


def run(a01: Path, a02: Path, out: Path):
    evaluator = r1run._load_evaluator()
    evaluator._is_goal = r1run._score_event
    calipers = dict(evaluator.MATCH_CALIPERS)

    comp_file, matches, events, union_sha = evaluator._merge_sources(a01, a02)
    rows, skipped_nonregular = evaluator._build_rows(comp_file, matches, events)
    eligible = rows[
        (rows["hn"] >= evaluator.MIN_PRIOR_TEAM_MATCHES)
        & (rows["an"] >= evaluator.MIN_PRIOR_TEAM_MATCHES)
        & (rows["target"].isin(["D", "OW"]))
    ].copy()
    eligible = eligible.sort_values(["dt", "match_id"]).reset_index(drop=True)

    source = {
        "packages": ["A01", "A02"],
        "source_matches": 800,
        "union_ids_sha256_sorted": union_sha,
        "regular_matches": int(len(rows)),
        "skipped_nonregular_matches": int(skipped_nonregular),
        "eligible_rows": int(len(eligible)),
        "eligible_draws": int((eligible["target"] == "D").sum()),
        "eligible_onegoal_wins": int((eligible["target"] == "OW").sum()),
    }
    observed_source = {
        "source_matches": source["source_matches"],
        "regular_matches": source["regular_matches"],
        "eligible_rows": source["eligible_rows"],
        "eligible_draws": source["eligible_draws"],
        "eligible_onegoal_wins": source["eligible_onegoal_wins"],
    }
    if observed_source != EXPECTED_SOURCE:
        raise RuntimeError(f"R1 source/eligibility drift: {observed_source} != {EXPECTED_SOURCE}")

    unique_dates = sorted(eligible["date"].unique())
    cut_indices = [
        max(1, min(len(unique_dates) - 1, int(len(unique_dates) * q)))
        for q in (0.4, 0.6, 0.8)
    ]
    cut_dates = [unique_dates[i] for i in cut_indices]
    fold_specs = [
        ("fold_1", cut_dates[0], cut_dates[0], cut_dates[1]),
        ("fold_2", cut_dates[1], cut_dates[1], cut_dates[2]),
        ("fold_3", cut_dates[2], cut_dates[2], None),
    ]

    folds = {}
    failures = []
    greedy_reproduced = {}
    for name, train_end, test_start, test_end in fold_specs:
        train = eligible[eligible["date"] < train_end].copy()
        test = eligible[eligible["date"] >= test_start].copy()
        if test_end is not None:
            test = test[test["date"] < test_end].copy()

        _, greedy_train_meta = r1run._greedy_pairs(train, f"{name}-r1-train", calipers)
        _, greedy_test_meta = r1run._greedy_pairs(test, f"{name}-r1-test", calipers)
        greedy_train = len(greedy_train_meta)
        greedy_test = len(greedy_test_meta)
        greedy_reproduced[name] = {"train": greedy_train, "test": greedy_test}
        if greedy_reproduced[name] != EXPECTED_GREEDY[name]:
            raise RuntimeError(
                f"R1 greedy count drift {name}: {greedy_reproduced[name]} != {EXPECTED_GREEDY[name]}"
            )

        optimal_train_meta, train_certificate = _optimal_pairs(train, f"{name}-r2-train", calipers)
        optimal_test_meta, test_certificate = _optimal_pairs(test, f"{name}-r2-test", calipers)
        optimal_train = len(optimal_train_meta)
        optimal_test = len(optimal_test_meta)

        train_pass = optimal_train >= evaluator.MIN_TRAIN_PAIRS
        test_pass = optimal_test >= evaluator.MIN_TEST_PAIRS
        if not train_pass:
            failures.append(f"{name}:train")
        if not test_pass:
            failures.append(f"{name}:test")

        folds[name] = {
            "train_date_max_exclusive": str(train_end),
            "test_date_min_inclusive": str(test_start),
            "test_date_max_exclusive": str(test_end) if test_end is not None else None,
            "train_target_rows": int(len(train)),
            "test_target_rows": int(len(test)),
            "train_draws": int((train["target"] == "D").sum()),
            "train_onegoal_wins": int((train["target"] == "OW").sum()),
            "test_draws": int((test["target"] == "D").sum()),
            "test_onegoal_wins": int((test["target"] == "OW").sum()),
            "r1_greedy": {"train_pairs": greedy_train, "test_pairs": greedy_test},
            "r2_maxcard": {
                "train_pairs": optimal_train,
                "test_pairs": optimal_test,
                "train_maximum_cardinality_certificate": int(train_certificate),
                "test_maximum_cardinality_certificate": int(test_certificate),
                "train_distance": _distance_summary(optimal_train_meta),
                "test_distance": _distance_summary(optimal_test_meta),
            },
            "coverage_delta_vs_r1": {
                "train_pairs": int(optimal_train - greedy_train),
                "test_pairs": int(optimal_test - greedy_test),
            },
            "frozen_gate": {
                "minimum_train_pairs": int(evaluator.MIN_TRAIN_PAIRS),
                "minimum_test_pairs": int(evaluator.MIN_TEST_PAIRS),
                "train_pass": bool(train_pass),
                "test_pass": bool(test_pass),
                "fold_pass": bool(train_pass and test_pass),
            },
        }

    recovered = not failures
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "COVERAGE_RECOVERED" if recovered else "STOP_COVERAGE",
        "verdict": (
            "MAXCARD_RECOVERS_FROZEN_COVERAGE"
            if recovered
            else "MAXCARD_CANNOT_RECOVER_FROZEN_COVERAGE"
        ),
        "source": source,
        "r1_greedy_reproduction": greedy_reproduced,
        "contract": {
            "estimand": "draw_vs_one_goal_win_conditional",
            "exact_competition": True,
            "calipers": calipers,
            "without_replacement": True,
            "r2_objective": "lexicographic_maximum_cardinality_then_minimum_total_distance",
            "distance": "sum_squared_caliper_normalized",
            "maximum_cardinality_certificate": "independent_augmenting_path_bipartite_matching",
            "minimum_distance_solver": "scipy.optimize.linear_sum_assignment_with_dummy_unmatched_columns",
            "minimum_train_pairs_each_fold": int(evaluator.MIN_TRAIN_PAIRS),
            "minimum_test_pairs_each_fold": int(evaluator.MIN_TEST_PAIRS),
        },
        "folds": folds,
        "coverage_failures": failures,
        "boundary": {
            "coverage_only": True,
            "model_fitting_performed": False,
            "model_scoring_performed": False,
            "feature_effect_claim_allowed": False,
            "post_view_development_only": True,
            "protected_samples_used": False,
            "new_A_packages_opened": False,
            "formal_weight": 0,
            "scientific_pass": False,
            "confirmation_pass": False,
            "formal_promotion_allowed": False,
        },
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--a01", required=True)
    parser.add_argument("--a02", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    run(Path(args.a01), Path(args.a02), Path(args.out))
