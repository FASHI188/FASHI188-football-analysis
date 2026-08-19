#!/usr/bin/env python3
from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

import evaluate_c072e2_ou25_movement_directt as e2

SCHEMA = "C072F2_OU25_MOVEMENT_FORWARD_CONFIRM_V1"
BOOT_REPS = 5000
BOOT_SEED = 72023
EXPECTED_ZERO_LABEL_IDENTITIES = 781


def load_confirmation_source():
    frames = []
    ident_2024 = 0
    parsed_2024 = 0
    missing_2024 = 0
    for path in e2.FILES:
        raw = urlopen(e2.RAW + path, timeout=90).read()
        ident = pd.read_csv(io.BytesIO(raw), usecols=e2.ID_ODDS)
        ident["source_file"] = path
        ident["season_start"] = ident["Season"].map(e2.season_start)
        ident["date"] = pd.to_datetime(ident["Date"], errors="coerce", utc=True)
        if ident["season_start"].isna().any() or ident["date"].isna().any():
            raise RuntimeError(f"identity parse failure {path}")
        ident["season_start"] = ident["season_start"].astype(int)
        ident_2024 += int((ident.season_start == 2024).sum())

        # First/only authorized target projection for the previously sealed 2024/25 window.
        lab = pd.read_csv(io.BytesIO(raw), usecols=["FTHG", "FTAG"])
        if len(lab) != len(ident):
            raise RuntimeError(f"label row drift {path}")
        ident["FTHG"] = pd.to_numeric(lab["FTHG"], errors="coerce")
        ident["FTAG"] = pd.to_numeric(lab["FTAG"], errors="coerce")
        m24 = ident.season_start == 2024
        parsed_2024 += int(m24.sum())
        missing_2024 += int(ident.loc[m24, ["FTHG", "FTAG"]].isna().any(axis=1).sum())
        # Earlier rows are training/history. 2024 rows with missing targets remain missing and are never replaced.
        frames.append(ident)

    frame = pd.concat(frames, ignore_index=True)
    if ident_2024 != EXPECTED_ZERO_LABEL_IDENTITIES:
        raise RuntimeError(f"sealed identity drift {ident_2024} != {EXPECTED_ZERO_LABEL_IDENTITIES}")
    frame = frame.dropna(subset=["FTHG", "FTAG"]).copy()
    frame["FTHG"] = frame.FTHG.astype(int); frame["FTAG"] = frame.FTAG.astype(int)
    frame = frame.sort_values(["date", "source_file", "HomeTeam", "AwayTeam"]).reset_index(drop=True)
    return frame, {
        "sealed_identity_count_expected": EXPECTED_ZERO_LABEL_IDENTITIES,
        "sealed_identity_count_observed": ident_2024,
        "confirmation_goal_rows_first_parsed": parsed_2024,
        "confirmation_missing_result_rows": missing_2024,
        "replacement_rows": 0,
    }


def paired_boot(y, pref, pcand):
    y = np.asarray(y, int); idx = np.arange(len(y))
    d = -np.log(np.clip(pcand[idx, y], 1e-15, 1.0)) + np.log(np.clip(pref[idx, y], 1e-15, 1.0))
    rng = np.random.default_rng(BOOT_SEED); n = len(d); sims = np.empty(BOOT_REPS)
    for i in range(BOOT_REPS):
        take = rng.integers(0, n, size=n); sims[i] = float(d[take].mean())
    return {
        "n": int(n), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, .05)),
        "ci90_high": float(np.quantile(sims, .95)),
        "p_delta_lt_zero": float(np.mean(sims < 0)),
    }


def subset_delta(y, pr, pc):
    return e2.delta(e2.metrics(y, pc), e2.metrics(y, pr))


def main():
    frame, boundary = load_confirmation_source()
    feat = e2.build_rows(frame)
    elig = feat[feat.eligible].copy().sort_values(["date", "source_file", "home_team", "away_team"]).reset_index(drop=True)
    tr = elig[elig.season_start < 2024].copy()
    te = elig[elig.season_start == 2024].copy().reset_index(drop=True)
    classes = sorted(tr.target.unique().astype(int).tolist())
    leagues = int(te.source_file.nunique())
    coverage = bool(len(te) >= 600 and len(tr) >= 30000 and leagues >= 6 and classes == list(range(e2.K)))

    if not coverage:
        result = {
            "schema": SCHEMA,
            "terminal": "STOP_CONFIRMATION_COVERAGE",
            "coverage_pass": False,
            "train_rows": int(len(tr)), "test_rows": int(len(te)), "test_leagues": leagues, "train_classes": classes,
            "boundary": boundary | {"formal_weight": 0, "C073_C077_quarantined": True, "C070F_confirmation1597_opened": False},
        }
    else:
        ytr = tr.target.to_numpy(int); yte = te.target.to_numpy(int)
        mr = e2.pipeline(); mc = e2.pipeline()
        mr.fit(tr[e2.REF], ytr); mc.fit(tr[e2.CAND], ytr)
        pr = e2.predict8(mr, te[e2.REF]); pc = e2.predict8(mc, te[e2.CAND])
        xr, xc = e2.metrics(yte, pr), e2.metrics(yte, pc)
        d = e2.delta(xc, xr); bt = paired_boot(yte, pr, pc)
        max_resid = float(max(np.max(np.abs(pr.sum(1)-1)), np.max(np.abs(pc.sum(1)-1))))

        cut = len(te) // 2
        halves = {
            "early": {"n": int(cut), "candidate_minus_reference": subset_delta(yte[:cut], pr[:cut], pc[:cut])},
            "late": {"n": int(len(te)-cut), "candidate_minus_reference": subset_delta(yte[cut:], pr[cut:], pc[cut:])},
        }
        league_results = {}
        cluster_wins = 0; eligible_clusters = 0
        for league, idxs in te.groupby("source_file").groups.items():
            idx = np.asarray(sorted(idxs), int)
            if len(idx) < 50:
                continue
            dd = subset_delta(yte[idx], pr[idx], pc[idx])
            eligible_clusters += 1; cluster_wins += int(dd["log_loss"] < 0)
            league_results[str(league)] = {"n": int(len(idx)), "candidate_minus_reference": dd}
        majority = eligible_clusters > 0 and cluster_wins > eligible_clusters / 2

        gate = bool(
            d["log_loss"] < 0 and bt["ci90_high"] < 0 and d["brier"] <= 0 and d["rps"] <= 0
            and halves["early"]["candidate_minus_reference"]["log_loss"] < 0
            and halves["late"]["candidate_minus_reference"]["log_loss"] < 0
            and majority and max_resid <= 1e-10
        )
        result = {
            "schema": SCHEMA,
            "terminal": "C072F2_FORWARD_CONFIRMATION_PASS" if gate else "C072F2_CONFIRMATION_FAIL_PARK",
            "coverage_pass": True,
            "source": {"repo": "nm2890/football-data", "revision": e2.REV, "window": "partial 2024/25 forward", "pit_classification": "COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_IMMUTABLE_QUOTE_TIMESTAMPS"},
            "frozen_recipe": {"reference": e2.REF, "candidate": e2.CAND, "C": e2.C_FIXED, "transform_search": False, "feature_search": False},
            "train_rows": int(len(tr)), "confirmation_rows": int(len(te)), "confirmation_leagues": leagues,
            "reference": xr, "candidate": xc, "candidate_minus_reference": d,
            "bootstrap": bt,
            "chronological_halves": halves,
            "league_clusters_ge50": league_results,
            "league_cluster_wins": int(cluster_wins), "eligible_league_clusters": int(eligible_clusters), "strict_majority_league_wins": bool(majority),
            "max_probability_sum_residual": max_resid,
            "confirmation_gate": gate,
            "stopping_rule": "one-shot: no repair/tuning/recalibration/subset deletion on opened 2024/25 labels",
            "boundary": boundary | {
                "formal_weight": 0,
                "C073_C077_quarantined": True,
                "C070F_confirmation1597_opened": False,
                "protected_opened": False,
                "exact_score_matrix_generated": False,
            },
        }

    out = Path("football-data/research/c072f2_summary.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
