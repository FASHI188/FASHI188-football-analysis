#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = "C075C_SIMPLE_TAIL_EXTERNAL_CONFIRMATION_V1R1"
SOURCE_COMMIT = "ea767ac28cf9a2d737bb3e4ce65aa4b1f4ac9361"
FROZEN_IDENTITY_N = 3407
FROZEN_IDENTITY_SHA = "6667088228846b992b1ec51d84347c31081a0e4eb86721a4a76b36ff421283d8"
FILES = [
    "2019/br.1.json", "2019/cn.1.json", "2019/jp.1.json",
    "2020/br.1.json", "2020/cn.1.json", "2020/jp.1.json",
    "2025/ar.1.json", "2025/br.1.json", "2025/br.2.json", "2025/cn.1.json",
    "2025/co.1.json", "2025/jp.1.json", "2025/mls.json",
]
CUTOFF_2025 = "2025-08-15"
R = 0.3158284023668639
MEAN_E = 0.4616216216216215
K = 15
TAIL_RESIDUAL = 9.799838862681768e-09
PARAM_SHA = "4d782338ea14288d608814c7f9f6b51044edbce0a224501b72bdbf2e7e24e411"
BOOT_REPS = 5000
BOOT_SEED = 75003
KS_REPS = 5000
KS_SEED = 75004
WILSON_Z = 1.6448536269514722
SCORE_COMPLETE_MIN = 0.95
TAIL_N_MIN = 50
FORWARD_2025_TAIL_N_MIN = 15
YEAR_ELIGIBLE_MIN = 10
YEAR_ELIGIBLE_COUNT_MIN = 2
FAMILY_ELIGIBLE_MIN = 8
FAMILY_ELIGIBLE_COUNT_MIN = 3

STR = r'"((?:\\.|[^"\\])*)"'
DATE_RE = re.compile(r'"date"\s*:\s*' + STR)
TEAM1_RE = re.compile(r'"team1"\s*:\s*' + STR)
TEAM2_RE = re.compile(r'"team2"\s*:\s*' + STR)


def decode_string(payload: str) -> str:
    return json.loads('"' + payload + '"')


def identity_sha(keys: list[str]) -> str:
    payload = "\n".join(sorted(keys)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def identity_from_object_text(obj: str):
    dm = DATE_RE.search(obj); h = TEAM1_RE.search(obj); a = TEAM2_RE.search(obj)
    if not (dm and h and a):
        return None
    return decode_string(dm.group(1)), decode_string(h.group(1)), decode_string(a.group(1))


def iter_match_object_text(text: str):
    """Yield raw top-level objects inside the JSON `matches` array without decoding nested score objects."""
    m = re.search(r'"matches"\s*:\s*\[', text)
    if not m:
        raise RuntimeError("matches array not found")
    i = m.end()
    n = len(text)
    in_string = False
    escape = False
    array_depth = 1
    while i < n and array_depth > 0:
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == '[':
            array_depth += 1
            i += 1
            continue
        if c == ']':
            array_depth -= 1
            i += 1
            continue
        if c != '{' or array_depth != 1:
            i += 1
            continue
        start = i
        brace = 0
        obj_string = False
        obj_escape = False
        while i < n:
            ch = text[i]
            if obj_string:
                if obj_escape:
                    obj_escape = False
                elif ch == "\\":
                    obj_escape = True
                elif ch == '"':
                    obj_string = False
            else:
                if ch == '"':
                    obj_string = True
                elif ch == '{':
                    brace += 1
                elif ch == '}':
                    brace -= 1
                    if brace == 0:
                        i += 1
                        yield text[start:i]
                        break
            i += 1
        else:
            raise RuntimeError("unterminated match object")


def frozen_identity_keys(root: Path):
    keys = []
    per_file = {}
    for rel in FILES:
        text = (root / rel).read_text(encoding="utf-8")
        count = 0
        for obj in iter_match_object_text(text):
            ident = identity_from_object_text(obj)
            if ident is None:
                raise RuntimeError(f"missing identity fields in {rel}")
            date, team1, team2 = ident
            if rel.startswith("2025/") and date[:10] > CUTOFF_2025:
                continue
            key = f"{rel}|{date}|{team1}|{team2}"
            keys.append(key)
            count += 1
        per_file[rel] = count
    if len(keys) != FROZEN_IDENTITY_N:
        raise RuntimeError(f"identity count drift {len(keys)} != {FROZEN_IDENTITY_N}")
    if len(set(keys)) != len(keys):
        raise RuntimeError("duplicate frozen identities")
    sha = identity_sha(keys)
    if sha != FROZEN_IDENTITY_SHA:
        raise RuntimeError(f"identity sha drift {sha} != {FROZEN_IDENTITY_SHA}")
    return set(keys), per_file


def project_scores(root: Path, frozen_keys: set[str]):
    rows = []
    decoded_frozen_objects = 0
    decoded_nonfrozen_objects = 0
    for rel in FILES:
        text = (root / rel).read_text(encoding="utf-8")
        for obj in iter_match_object_text(text):
            ident = identity_from_object_text(obj)
            if ident is None:
                continue
            date, team1, team2 = ident
            key = f"{rel}|{date}|{team1}|{team2}"
            if key not in frozen_keys:
                # Critical label boundary: never JSON-decode a non-frozen match object,
                # so post-cutoff 2025 score values are not selected/materialized.
                continue
            decoded_frozen_objects += 1
            match = json.loads(obj)
            score = match.get("score")
            ft = score.get("ft") if isinstance(score, dict) else None
            valid = (
                isinstance(ft, list) and len(ft) == 2 and
                all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in ft)
            )
            year = rel.split("/", 1)[0]
            family = rel.split("/", 1)[1].rsplit(".json", 1)[0]
            rows.append({
                "identity_key": key, "source_file": rel, "year": year, "family": family,
                "date": date, "team1": team1, "team2": team2,
                "score_valid": bool(valid),
                "home_ft": int(ft[0]) if valid else None,
                "away_ft": int(ft[1]) if valid else None,
            })
    if decoded_frozen_objects != FROZEN_IDENTITY_N:
        raise RuntimeError(f"score projection identity count drift {decoded_frozen_objects}")
    return pd.DataFrame(rows), decoded_frozen_objects, decoded_nonfrozen_objects


def r_hat(e: np.ndarray) -> float:
    e = np.asarray(e, dtype=float)
    return float(e.sum() / (e.sum() + len(e)))


def bootstrap_ci(e: np.ndarray, seed: int):
    e = np.asarray(e, dtype=int)
    rng = np.random.default_rng(seed)
    br = np.empty(BOOT_REPS, dtype=float)
    bm = np.empty(BOOT_REPS, dtype=float)
    for i in range(BOOT_REPS):
        x = e[rng.integers(0, len(e), size=len(e))]
        br[i] = r_hat(x)
        bm[i] = float(np.mean(x))
    return {
        "reps": BOOT_REPS, "seed": int(seed),
        "r_hat": r_hat(e),
        "r_ci90_low": float(np.quantile(br, 0.05)),
        "r_ci90_high": float(np.quantile(br, 0.95)),
        "mean_excess": float(np.mean(e)),
        "mean_ci90_low": float(np.quantile(bm, 0.05)),
        "mean_ci90_high": float(np.quantile(bm, 0.95)),
    }


def ks_stat(e: np.ndarray, r: float) -> float:
    e = np.asarray(e, dtype=int)
    max_e = int(max(e.max(initial=0), K))
    grid = np.arange(max_e + 1)
    emp = np.asarray([(e <= j).mean() for j in grid], dtype=float)
    mod = 1.0 - np.power(r, grid + 1)
    return float(np.max(np.abs(emp - mod)))


def ks_monte_carlo(e: np.ndarray):
    e = np.asarray(e, dtype=int)
    observed = ks_stat(e, R)
    rng = np.random.default_rng(KS_SEED)
    sim = np.empty(KS_REPS, dtype=float)
    p_stop = 1.0 - R
    for i in range(KS_REPS):
        draw = rng.geometric(p_stop, size=len(e)) - 1
        sim[i] = ks_stat(draw, R)
    pvalue = float((1 + np.sum(sim >= observed - 1e-15)) / (KS_REPS + 1))
    return {
        "observed_D": observed, "reps": KS_REPS, "seed": KS_SEED,
        "simulated_D_95pct": float(np.quantile(sim, 0.95)),
        "monte_carlo_pvalue": pvalue,
    }


def wilson(success: int, n: int):
    z = WILSON_Z
    phat = success / n
    den = 1 + z*z/n
    center = (phat + z*z/(2*n)) / den
    half = z * math.sqrt(phat*(1-phat)/n + z*z/(4*n*n)) / den
    return {"success": int(success), "n": int(n), "observed": float(phat), "low90": float(center-half), "high90": float(center+half)}


def fixed_scores(e: np.ndarray):
    e = np.asarray(e, dtype=int)
    exact_prob = (1.0 - R) * np.power(R, e)
    ll = -np.log(np.clip(exact_prob, 1e-300, 1.0))
    probs = np.asarray([(1.0-R)*R**j for j in range(K+1)] + [R**(K+1)], dtype=float)
    prob_sum_residual = abs(float(probs.sum()) - 1.0)
    y = np.minimum(e, K+1)
    one = np.zeros((len(e), len(probs)), dtype=float)
    one[np.arange(len(e)), y] = 1.0
    pmat = np.repeat(probs[None, :], len(e), axis=0)
    brier = np.square(pmat-one).sum(axis=1)
    cp = np.cumsum(pmat, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp-cy).sum(axis=1) / (len(probs)-1)
    return {
        "logloss": float(ll.mean()), "brier": float(brier.mean()), "rps": float(rps.mean()),
        "probability_sum_abs_residual": float(prob_sum_residual),
        "unenumerated_tail_residual": float(R**(K+1)),
        "enumeration_K": K,
    }


def cluster_report(tail: pd.DataFrame, field: str, min_n: int, seed_base: int):
    result = {}
    eligible = 0
    contains = 0
    for idx, (name, g) in enumerate(sorted(tail.groupby(field), key=lambda kv: str(kv[0]))):
        e = g["excess"].to_numpy(int)
        rec = {"n": int(len(e)), "eligible": len(e) >= min_n, "r_hat": r_hat(e), "mean_excess": float(np.mean(e))}
        if len(e) >= min_n:
            eligible += 1
            ci = bootstrap_ci(e, seed_base + idx + 1)
            inside = ci["r_ci90_low"] <= R <= ci["r_ci90_high"]
            rec.update(ci)
            rec["frozen_r_inside_ci90"] = bool(inside)
            contains += int(inside)
        result[str(name)] = rec
    return result, eligible, contains, float(contains/eligible) if eligible else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    root = Path(a.source_root); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    # Pass 1 is identity-only and must reproduce the zero-label frozen manifest before any score object is decoded.
    frozen_keys, per_file_identity = frozen_identity_keys(root)

    # Pass 2 opens exactly score.ft for the already-frozen identities, once.
    scores, decoded_frozen, decoded_nonfrozen = project_scores(root, frozen_keys)
    if len(scores) != FROZEN_IDENTITY_N or scores.identity_key.nunique() != FROZEN_IDENTITY_N:
        raise RuntimeError("score projection did not return exactly frozen identities")
    complete = scores[scores.score_valid].copy()
    score_complete_fraction = float(len(complete) / FROZEN_IDENTITY_N)
    complete["exact_total"] = complete.home_ft.astype(int) + complete.away_ft.astype(int)
    tail = complete[complete.exact_total >= 7].copy().reset_index(drop=True)
    tail["excess"] = tail.exact_total.astype(int) - 7

    year_counts = tail.groupby("year").size().to_dict()
    family_counts = tail.groupby("family").size().to_dict()
    eligible_year_count = sum(int(v) >= YEAR_ELIGIBLE_MIN for v in year_counts.values())
    eligible_family_count = sum(int(v) >= FAMILY_ELIGIBLE_MIN for v in family_counts.values())
    forward_n = int(year_counts.get("2025", 0))
    coverage = {
        "frozen_identity_count": FROZEN_IDENTITY_N,
        "score_complete_n": int(len(complete)),
        "score_invalid_or_missing_n": int(FROZEN_IDENTITY_N-len(complete)),
        "score_complete_fraction": score_complete_fraction,
        "pooled_tail_n": int(len(tail)),
        "forward_2025_tail_n": forward_n,
        "eligible_year_blocks_n_ge_10": int(eligible_year_count),
        "eligible_family_blocks_n_ge_8": int(eligible_family_count),
        "year_tail_counts": {str(k): int(v) for k,v in year_counts.items()},
        "family_tail_counts": {str(k): int(v) for k,v in family_counts.items()},
        "requirements": {
            "score_complete_fraction_min": SCORE_COMPLETE_MIN,
            "pooled_tail_n_min": TAIL_N_MIN,
            "forward_2025_tail_n_min": FORWARD_2025_TAIL_N_MIN,
            "year_eligible_count_min": YEAR_ELIGIBLE_COUNT_MIN,
            "family_eligible_count_min": FAMILY_ELIGIBLE_COUNT_MIN,
        }
    }
    coverage_gate = {
        "score_complete_fraction": score_complete_fraction >= SCORE_COMPLETE_MIN,
        "pooled_tail_n": len(tail) >= TAIL_N_MIN,
        "forward_2025_tail_n": forward_n >= FORWARD_2025_TAIL_N_MIN,
        "eligible_year_blocks": eligible_year_count >= YEAR_ELIGIBLE_COUNT_MIN,
        "eligible_family_blocks": eligible_family_count >= FAMILY_ELIGIBLE_COUNT_MIN,
    }
    coverage_pass = all(coverage_gate.values())

    base_summary = {
        "schema_version": SCHEMA,
        "formal_weight": 0,
        "frozen_tail_law": {"r": R, "predicted_mean_excess": MEAN_E, "K": K, "residual_after_K": TAIL_RESIDUAL, "parameter_sha256": PARAM_SHA},
        "external_identity": {"count": FROZEN_IDENTITY_N, "sha256": FROZEN_IDENTITY_SHA, "per_file_identity_count": per_file_identity},
        "score_projection_boundary": {
            "decoded_frozen_match_objects": int(decoded_frozen),
            "decoded_nonfrozen_match_objects": int(decoded_nonfrozen),
            "post_cutoff_2025_score_objects_decoded": 0,
            "score_field_used": "score.ft only",
            "missing_replacement_count": 0,
            "external_parameter_refit": False,
            "external_recalibration": False,
        },
        "coverage": coverage,
        "coverage_gate": coverage_gate,
        "protected_boundaries": {
            "C071_reserve_52180_opened": False, "C070F_confirmation1597_opened": False,
            "A05_opened": False, "protected_opened": False,
            "T_ge_7_D_given_T_tested": False, "unified_matrix_generated": False,
        },
    }

    if not coverage_pass:
        base_summary.update({"status": "STOP_COVERAGE_EXTERNAL_LABELS_CONSUMED", "scientific_effect_evaluated": False})
        (out/"summary.json").write_text(json.dumps(base_summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        scores.to_csv(out/"score_projection_accounting.csv", index=False)
        print(json.dumps(base_summary, ensure_ascii=False, indent=2))
        return 0

    e = tail.excess.to_numpy(int)
    pooled_boot = bootstrap_ci(e, BOOT_SEED)
    ks = ks_monte_carlo(e)
    threshold = {}
    for n in (1,2,3):
        rec = wilson(int((e>=n).sum()), len(e))
        rec["frozen_probability"] = float(R**n)
        rec["frozen_probability_inside_wilson90"] = bool(rec["low90"] <= R**n <= rec["high90"])
        threshold[f"E_ge_{n}"] = rec
    years, year_eligible, year_contains, year_fraction = cluster_report(tail, "year", YEAR_ELIGIBLE_MIN, 76000)
    families, fam_eligible, fam_contains, fam_fraction = cluster_report(tail, "family", FAMILY_ELIGIBLE_MIN, 77000)
    forward = years.get("2025", {})
    metrics = fixed_scores(e)

    gate = {
        "coverage_pass": True,
        "pooled_frozen_r_inside_bootstrap90": pooled_boot["r_ci90_low"] <= R <= pooled_boot["r_ci90_high"],
        "pooled_frozen_mean_inside_bootstrap90": pooled_boot["mean_ci90_low"] <= MEAN_E <= pooled_boot["mean_ci90_high"],
        "discrete_ks_p_ge_0_05": ks["monte_carlo_pvalue"] >= 0.05,
        "E_ge_1_probability_inside_wilson90": threshold["E_ge_1"]["frozen_probability_inside_wilson90"],
        "E_ge_2_probability_inside_wilson90": threshold["E_ge_2"]["frozen_probability_inside_wilson90"],
        "forward_2025_n_ge_15": int(forward.get("n",0)) >= FORWARD_2025_TAIL_N_MIN,
        "forward_2025_frozen_r_inside_bootstrap90": bool(forward.get("frozen_r_inside_ci90", False)),
        "year_eligible_ge_2": year_eligible >= YEAR_ELIGIBLE_COUNT_MIN,
        "year_ci_containment_fraction_ge_half": year_fraction >= 0.5,
        "family_eligible_ge_3": fam_eligible >= FAMILY_ELIGIBLE_COUNT_MIN,
        "family_ci_containment_fraction_ge_half": fam_fraction >= 0.5,
        "tail_residual_le_1e_8": metrics["unenumerated_tail_residual"] <= 1e-8,
        "probability_sum_residual_le_1e_10": metrics["probability_sum_abs_residual"] <= 1e-10,
    }
    passed = all(gate.values())
    status = "CONFIRMATION_PASS" if passed else "CONFIRMATION_FAIL_PARK"

    tail_out = tail[["identity_key","source_file","year","family","date","exact_total","excess"]].copy()
    tail_out.to_csv(out/"tail_confirmation_rows.csv", index=False)
    scores[["identity_key","source_file","year","family","date","score_valid"]].to_csv(out/"score_projection_accounting.csv", index=False)
    summary = {
        **base_summary,
        "status": status,
        "scientific_effect_evaluated": True,
        "estimand": "q(T=7+e | T>=7), fixed unconditional geometric tail law",
        "pooled_external": {**pooled_boot, "frozen_r": R, "frozen_mean_excess": MEAN_E, "exact_tail_scores": metrics},
        "shape_gof": ks,
        "threshold_calibration": threshold,
        "calendar_year_stability": {"eligible": year_eligible, "contains_frozen_r": year_contains, "containment_fraction": year_fraction, "blocks": years},
        "league_family_stability": {"eligible": fam_eligible, "contains_frozen_r": fam_contains, "containment_fraction": fam_fraction, "blocks": families},
        "forward_2025": forward,
        "gate": gate,
        "claim_boundary": "exact-tail research component only; even PASS remains formal_weight=0 and cannot open unified score matrix because T>=7 D|T is still missing",
        "stopping_rule": "external labels consumed once; no retuning/refit/reselection permitted on this domain",
    }
    (out/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
