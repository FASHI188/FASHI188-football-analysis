#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
from pathlib import Path

R39U_PATH = Path("football-data/research/existing_data_analog_r39u/evaluate_analog_r39u.py")
R39U_EXPECTED_BLOB = "16bdecdd508b780318628ec58224168dfa849e99"
R39U_SEED = "R39U_FIXED100_20260810"
LABELS = ("H", "D", "A")


def load_r39u_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39U_PATH.as_posix()}"], text=True).strip()
    if got != R39U_EXPECTED_BLOB:
        raise RuntimeError(f"R39U_EVALUATOR_BLOB_DRIFT:{got}:{R39U_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39u_eval", R39U_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39U_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def auc_binary(scores: list[float], labels: list[bool]) -> float:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        raise RuntimeError("AUC_CLASS_MISSING")
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def mean(xs: list[float]) -> float:
    if not xs:
        raise RuntimeError("EMPTY_MEAN")
    return sum(xs) / len(xs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R39V-EXISTING-DATA-DRAW-RESIDUAL-1.0"
    assert pre["hard_boundaries"]["external_network_requests"] == 0
    assert pre["hard_boundaries"]["football_api_requests"] == 0
    assert pre["hard_boundaries"]["new_data_collection"] is False
    assert pre["algorithm"]["post_discovery_tuning_on_r39v"] is False

    u = load_r39u_module()
    rows = u.load_rows(args.root)
    by_comp: dict[str, list] = {}
    for r in rows:
        by_comp.setdefault(r.competition, []).append(r)
    for comp in by_comp:
        by_comp[comp].sort(key=lambda r: (r.dt, r.key))

    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    eligible: list[tuple[object, int]] = []
    for comp, rs in sorted(by_comp.items()):
        valid_prior: list = []
        i = 0
        while i < len(rs):
            d = rs[i].dt
            j = i
            while j < len(rs) and rs[j].dt == d:
                j += 1
            for t in rs[i:j]:
                if u.finite_vec(t.x) and len(valid_prior) >= min_prior:
                    eligible.append((t, len(valid_prior)))
            valid_prior.extend(r for r in rs[i:j] if u.finite_vec(r.x))
            i = j

    r39u_ranked = sorted(
        eligible,
        key=lambda z: (hashlib.sha256(f"{R39U_SEED}|{z[0].key}".encode()).hexdigest(), z[0].key),
    )
    r39u_sample = r39u_ranked[:100]
    r39u_keys = [t.key for t, _ in r39u_sample]
    r39u_sha = u.sample_sha(r39u_keys)
    want_r39u_sha = pre["discovery_source"]["r39u_fixed100_identity_sha256"]
    if r39u_sha != want_r39u_sha:
        raise RuntimeError(f"R39U_SAMPLE_IDENTITY_DRIFT:{r39u_sha}:{want_r39u_sha}")
    excluded = set(r39u_keys)

    seed = str(pre["sample"]["seed"])
    sample_n = int(pre["sample"]["size"])
    remaining = [(t, n) for t, n in eligible if t.key not in excluded]
    ranked = sorted(
        remaining,
        key=lambda z: (hashlib.sha256(f"{seed}|{z[0].key}".encode()).hexdigest(), z[0].key),
    )
    sample = ranked[:sample_n]
    if len(sample) != sample_n:
        raise RuntimeError(f"INSUFFICIENT_DISJOINT_SAMPLE:{len(sample)}")
    keys = [t.key for t, _ in sample]
    overlap = len(set(keys) & excluded)
    if overlap != int(pre["sample"]["required_overlap_with_r39u"]):
        raise RuntimeError(f"R39U_OVERLAP_INVALID:{overlap}")

    ks = [int(x) for x in pre["algorithm"]["candidate_k"]]
    results: dict[str, list[dict]] = {str(k): [] for k in ks}
    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        if len(prior) < max(max(ks), min_prior):
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")
        for k in ks:
            p = u.probs_for(t, prior, k)
            pred = u.choose(p)
            results[str(k)].append({
                "key": t.key,
                "competition": t.competition,
                "season": t.season,
                "date": t.dt.isoformat(),
                "home": t.home,
                "away": t.away,
                "actual": t.label,
                "prediction": pred,
                "probabilities": {lab: round(p[lab], 12) for lab in LABELS},
                "strictly_prior_pool_n": len(prior),
            })

    diagnostics: dict[str, dict] = {}
    for k in map(str, ks):
        recs = results[k]
        scores = [float(r["probabilities"]["D"]) for r in recs]
        labels = [r["actual"] == "D" for r in recs]
        base_auc = auc_binary(scores, labels)
        inverse_auc = auc_binary([1.0 - s for s in scores], labels)
        draw_scores = [s for s, y in zip(scores, labels) if y]
        nondraw_scores = [s for s, y in zip(scores, labels) if not y]
        diagnostics[k] = {
            "base_draw_auc": base_auc,
            "inverse_draw_auc": inverse_auc,
            "mean_base_pD_actual_draw": mean(draw_scores),
            "mean_base_pD_actual_non_draw": mean(nondraw_scores),
            "base_metrics": u.metrics(recs),
        }

    d25 = diagnostics["25"]
    gate_primary = d25["inverse_draw_auc"] > 0.60
    gate_mean = d25["mean_base_pD_actual_draw"] < d25["mean_base_pD_actual_non_draw"]
    gate_cross_k = sum(diagnostics[str(k)]["inverse_draw_auc"] > 0.55 for k in ks) >= 2
    replicated = bool(gate_primary and gate_mean and gate_cross_k)
    terminal = "PASS_R39V_INVERSE_DRAW_RANKING_REPLICATED" if replicated else "FAIL_R39V_INVERSE_DRAW_RANKING_NOT_REPLICATED"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "replicated": replicated,
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "r39u_reconstructed_identity_sha256": r39u_sha,
        "r39v_fixed100_identity_sha256": u.sample_sha(keys),
        "r39u_overlap": overlap,
        "sample_keys": keys,
        "diagnostics": diagnostics,
        "gate": {
            "inverse_draw_auc_k25_gt_0_60": gate_primary,
            "mean_base_pD_draw_lt_non_draw_k25": gate_mean,
            "at_least_two_of_three_inverse_auc_gt_0_55": gate_cross_k,
        },
        "candidate_results": {k: {"predictions": v} for k, v in results.items()},
        "hard_boundaries": pre["hard_boundaries"],
        "interpretation_boundary": "Retrospective disjoint replication only. R39V labels exist in repository data, so even a PASS authorizes only a separately preregistered residual-model experiment; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r39v_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r39v_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal,
        "replicated": replicated,
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "r39u_overlap": overlap,
        "r39v_fixed100_identity_sha256": out["r39v_fixed100_identity_sha256"],
        "diagnostics": diagnostics,
        "gate": out["gate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
