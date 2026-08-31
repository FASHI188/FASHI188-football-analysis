from __future__ import annotations

import argparse
import json
import math
import random
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Any

from identity_registry import IdentityError, IdentityRegistry
from lineup_scenarios import build_lineup_scenarios
from football_context_translator import LayerAdjustment, build_plan, team_state
from match_context import ScheduleTracker, fit_schedule_coefficients, schedule_adjustment
from translator_adversarial_probe import run_probes
from translator_schema import canonical_sha
from v2_translator_integration import integrate_plan, fit_independent_head, head_predict


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def matrix_mean(m: list[list[float]]) -> tuple[float, float]:
    s = sum(map(sum, m))
    return (
        sum(i * p for i, row in enumerate(m) for p in row) / s,
        sum(j * p for row in m for j, p in enumerate(row)) / s,
    )


def ece(probs: list[float], outcomes: list[int], bins: int = 10) -> float:
    if not probs:
        return 0.0
    total = 0.0
    n = len(probs)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if not idx:
            continue
        mp = sum(probs[i] for i in idx) / len(idx)
        ar = sum(outcomes[i] for i in idx) / len(idx)
        total += len(idx) / n * abs(mp - ar)
    return total


def binary_metrics(probs: list[float], outcomes: list[int], threshold: float | None = None) -> dict[str, float]:
    n = max(len(probs), 1)
    ll = -sum(y * math.log(max(p, 1e-15)) + (1 - y) * math.log(max(1 - p, 1e-15)) for p, y in zip(probs, outcomes)) / n
    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n
    out = {"logloss": ll, "brier": brier, "ece": ece(probs, outcomes), "actual_rate": sum(outcomes) / n, "mean_probability": sum(probs) / n}
    if threshold is not None:
        tp = sum(1 for p, y in zip(probs, outcomes) if p >= threshold and y == 1)
        fp = sum(1 for p, y in zip(probs, outcomes) if p >= threshold and y == 0)
        fn = sum(1 for p, y in zip(probs, outcomes) if p < threshold and y == 1)
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        out.update({"threshold": threshold, "precision": precision, "recall": recall, "f1": 2 * precision * recall / max(precision + recall, 1e-15), "tp": tp, "fp": fp, "fn": fn})
    return out


def tune_draw_threshold(dev_rows: list[dict[str, Any]], weights: list[list[float]]) -> float:
    probs, ys = [], []
    for r in dev_rows:
        p = head_predict(float(r["mu_home"]), float(r["mu_away"]), 0.0, float(r.get("uncertainty", 0.0)), weights)["draw"]
        probs.append(p); ys.append(1 if int(r["target_class"]) == 1 else 0)
    best = (0.25, -1.0)
    for k in range(20, 81):
        t = k / 200.0
        f1 = binary_metrics(probs, ys, t)["f1"]
        if f1 > best[1] + 1e-15:
            best = (t, f1)
    return best[0]


def metrics(pred: list[dict[str, Any]], labels: dict[str, tuple[int, int]], draw_threshold: float) -> dict[str, Any]:
    ll = br = rps = es = 0.0; top = 0
    draw_p: list[float] = []; draw_y: list[int] = []
    error_p: list[float] = []; error_y: list[int] = []
    exact = {"0-0": ([], []), "1-1": ([], []), "2-2": ([], [])}
    weak_p: list[float] = []; weak_y: list[int] = []
    for r in pred:
        hg, ag = labels[r["fixture_id"]]
        y = "home" if hg > ag else "draw" if hg == ag else "away"
        p = r["one_x_two"]
        ll -= math.log(max(p[y], 1e-15))
        br += sum((p[k] - (1.0 if k == y else 0.0)) ** 2 for k in ("home", "draw", "away"))
        c1 = p["home"] - (1.0 if y == "home" else 0.0)
        c2 = (p["home"] + p["draw"]) - (1.0 if y in {"home", "draw"} else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2
        pred_cls = max(p, key=p.get); top += pred_cls == y
        error_p.append(1.0 - max(p.values())); error_y.append(0 if pred_cls == y else 1)
        m = r["matrix"]; prob = m[hg][ag] if hg < len(m) and ag < len(m[0]) else 1e-15; es -= math.log(max(prob, 1e-15))
        draw_p.append(p["draw"]); draw_y.append(1 if y == "draw" else 0)
        for score in exact:
            a, b = map(int, score.split("-")); pp = m[a][b] if a < len(m) and b < len(m[0]) else 0.0
            exact[score][0].append(pp); exact[score][1].append(1 if (hg, ag) == (a, b) else 0)
        weak = r["weak_side"]
        weak_p.append(p[weak]); weak_y.append(1 if y == weak else 0)
    n = len(pred)
    return {
        "n": n, "logloss": ll / n, "brier": br / n, "rps": rps / n, "top1": top / n,
        "exact_score_logloss": es / n,
        "draw": binary_metrics(draw_p, draw_y, draw_threshold),
        "exact_scores": {k: binary_metrics(v[0], v[1]) for k, v in exact.items()},
        "weak_team_win": binary_metrics(weak_p, weak_y),
        "uncertainty_calibration": {"top1_error_ece": ece(error_p, error_y), "mean_predicted_error": sum(error_p)/n, "actual_error_rate": sum(error_y)/n},
    }


def per_match_ll(pred: list[dict[str, Any]], labels: dict[str, tuple[int, int]]) -> list[float]:
    out = []
    for r in pred:
        hg, ag = labels[r["fixture_id"]]; y = "home" if hg > ag else "draw" if hg == ag else "away"
        out.append(-math.log(max(r["one_x_two"][y], 1e-15)))
    return out


def bootstrap_delta(a: list[float], b: list[float], seed: int = 20260831, reps: int | None = None) -> dict[str, float]:
    rng = random.Random(seed); n = len(a); reps = reps or (5000 if n >= 1000 else 2000)
    diffs = [x - y for x, y in zip(a, b)]; point = sum(diffs) / n; vals = []
    for _ in range(reps):
        vals.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    vals.sort(); return {"delta": point, "lo": vals[int(.025 * reps)], "hi": vals[min(reps - 1, int(.975 * reps))], "reps": reps}


def grouped(pred: list[dict[str, Any]], labels: dict[str, tuple[int, int]], threshold: float) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in pred:
        for key in (f"league:{r['competition_id']}", f"season:{r['season']}", f"cold:{r['cold_start_bucket']}", f"grade:{r['coverage_grade']}"):
            buckets[key].append(r)
    return {k: metrics(v, labels, threshold) for k, v in sorted(buckets.items()) if len(v) >= 20}


def group_gate(core: list[dict[str, Any]], layer: list[dict[str, Any]], labels: dict[str, tuple[int, int]], global_gain: float) -> dict[str, Any]:
    c = defaultdict(list); l = defaultdict(list)
    for a, b in zip(core, layer):
        if a["fixture_id"] != b["fixture_id"]: raise RuntimeError("paired group identity mismatch")
        keys = (f"league:{a['competition_id']}", f"season:{a['season']}", f"cold:{a['cold_start_bucket']}", f"grade:{a['coverage_grade']}")
        for k in keys: c[k].append(a); l[k].append(b)
    checks = {}; passed = True
    for k in sorted(c):
        if len(c[k]) < 100: continue
        ca = per_match_ll(c[k], labels); la = per_match_ll(l[k], labels); delta = sum(x-y for x,y in zip(la,ca))/len(ca)
        rec = {"n": len(ca), "delta_logloss": delta, "passes": True}
        if delta > 0.0100:
            ci = bootstrap_delta(la, ca); rec["bootstrap"] = ci
            exception = ci["lo"] <= 0 <= ci["hi"] and global_gain > 0.0030
            rec["passes"] = exception
            passed &= exception
        checks[k] = rec
    worst = max(checks.items(), key=lambda kv: kv[1]["delta_logloss"]) if checks else (None, None)
    return {"passed": passed, "groups": checks, "worst_delta_group": None if worst[0] is None else {"group": worst[0], **worst[1]}}


class TranslatorUnitTests(unittest.TestCase):
    def test_identity_ambiguity(self):
        r = IdentityRegistry(); r.register("player", "1", "Same Name"); r.register("player", "2", "Same Name")
        with self.assertRaises(IdentityError): r.resolve("player", "Same Name")
    def test_lineup_unknown(self):
        sc = build_lineup_scenarios(None, None, cutoff="2026-08-31T12:00:00+00:00"); self.assertEqual(sc[0].route, "LINEUP_UNKNOWN")
    def test_adversarial(self): self.assertTrue(run_probes()["passed"])


def historical_acceptance(artifact: Path, repo_root: Path) -> dict[str, Any]:
    manifest = json.loads((artifact / "artifact_manifest.json").read_text())
    if manifest["run_id"] != 33348991436 or manifest["prediction_sha256"] != "92dc38866e6e46b167ed6bf0bcfc6f6e0e8b85e57e68cb3a571d3c44fc9461a7":
        raise RuntimeError("sealed V2 Artifact identity mismatch")
    dev = read_jsonl(artifact / "dataset/development.jsonl"); feats = read_jsonl(artifact / "dataset/evaluation_features.jsonl")
    label_rows = read_jsonl(artifact / "dataset/evaluation_label_vault.jsonl"); labels = {r["fixture_id"]: (int(r["home_goals"]), int(r["away_goals"])) for r in label_rows}
    preds = read_jsonl(artifact / "replay/predictions.jsonl"); lock = json.loads((artifact / "locks/v2_lock.json").read_text()); pred_by = {r["fixture_id"]: r for r in preds}
    if set(pred_by) != {r["fixture_id"] for r in feats} or set(labels) != set(pred_by): raise RuntimeError("sealed cohort identity mismatch")

    import sys, importlib
    v2path = repo_root / "football-data" / "new_engine_v2_joint_score"; sys.path.insert(0, str(v2path)); eng = importlib.import_module("engine"); sys.path.pop(0)
    state = eng.EngineState(eng.Parameters(**lock["parameters"])); tracker = ScheduleTracker(); pending = []; dev_fit = []; head_fit = []
    for row in sorted(dev, key=lambda r: (r["cutoff"], r["fixture_id"])):
        cutoff = eng._dt(row["cutoff"])
        ready = [x for x in pending if eng._dt(x["available_at"]) <= cutoff]; pending = [x for x in pending if eng._dt(x["available_at"]) > cutoff]
        for item in sorted(ready, key=lambda x: (x["cutoff"], x["fixture_id"])):
            f = eng.Fixture(item["fixture_id"], item["competition_id"], item["season"], eng._dt(item["cutoff"]), item["home_team_id"], item["away_team_id"], item["round_index"])
            state.apply_batch([f], {f.fixture_id: (item["home_goals"], item["away_goals"])})
        fixture = eng.Fixture(row["fixture_id"], row["competition_id"], row["season"], cutoff, row["home_team_id"], row["away_team_id"], row["round_index"])
        bf = state.predict_features(fixture); sf = tracker.features(row["home_team_id"], row["away_team_id"], row["cutoff"], row.get("round_index"))
        dev_fit.append({"x": sf.vector(), "base_mu_home": bf["mu_home"], "base_mu_away": bf["mu_away"], "home_goals": row["home_goals"], "away_goals": row["away_goals"]})
        cls = 0 if row["home_goals"] > row["away_goals"] else 1 if row["home_goals"] == row["away_goals"] else 2
        head_fit.append({"mu_home": bf["mu_home"], "mu_away": bf["mu_away"], "context_delta": 0.0, "uncertainty": bf["uncertainty"], "target_class": cls})
        tracker.observe_fixture(row["home_team_id"], row["away_team_id"], row["cutoff"]); pending.append({**row, "available_at": row["result_available_at"]})
    beta = fit_schedule_coefficients(dev_fit); head_weights = fit_independent_head(head_fit); draw_threshold = tune_draw_threshold(head_fit, head_weights)

    tracker = ScheduleTracker()
    for row in sorted(dev, key=lambda r: (r["cutoff"], r["fixture_id"])): tracker.observe_fixture(row["home_team_id"], row["away_team_id"], row["cutoff"])
    raw_v2 = []; core = []; schedule = []; provenance = canonical_sha({"artifact": manifest["prediction_sha256"], "translator_v1": True})
    for row in sorted(feats, key=lambda r: (r["cutoff"], r["fixture_id"])):
        pr = pred_by[row["fixture_id"]]; raw_p = {"home": pr["v2_joint"]["p_home"], "draw": pr["v2_joint"]["p_draw"], "away": pr["v2_joint"]["p_away"]}
        weak = "home" if raw_p["home"] < raw_p["away"] else "away"
        meta = {"fixture_id": row["fixture_id"], "competition_id": row["competition_id"], "season": row["season"], "cold_start_bucket": pr["shared_cold_start_bucket"], "coverage_grade": "TEAM_ONLY", "weak_side": weak}
        raw_v2.append({**meta, "one_x_two": raw_p, "matrix": pr["v2_joint"]["score_matrix"]})
        bh, ba = matrix_mean(pr["v2_joint_off"]["score_matrix"]); sc = build_lineup_scenarios(None, None, cutoff=row["cutoff"])
        common = dict(match_id=row["fixture_id"], cutoff=row["cutoff"], base_mu_home=bh, base_mu_away=ba,
                      home_team_state=team_state(row["home_team_id"], 0, 0, float(pr.get("shared_home_prior_appearances", 0)), 0.5),
                      away_team_state=team_state(row["away_team_id"], 0, 0, float(pr.get("shared_away_prior_appearances", 0)), 0.5), scenarios=sc, player_vectors=None,
                      coach_tactical=LayerAdjustment("BLOCKED_DATA", uncertainty=.4), process_hazard=LayerAdjustment("BLOCKED_DATA", uncertainty=.4),
                      provenance_manifest_sha256=provenance, player_status="BLOCKED_DATA", coverage_grade="TEAM_ONLY")
        core_plan = build_plan(**common, match_context=LayerAdjustment("CONTRACT_ONLY", 0, 0, .15, None))
        core_i = integrate_plan(core_plan, lock, repo_root=repo_root, head_weights=head_weights)
        core.append({**meta, "one_x_two": core_i["final_1x2"], "matrix": core_i["final_matrix"]})
        sf = tracker.features(row["home_team_id"], row["away_team_id"], row["cutoff"], row.get("round_index")); dh, da, evsha = schedule_adjustment(sf, beta)
        sched_plan = build_plan(**common, match_context=LayerAdjustment("IMPLEMENTED", dh, da, .15, evsha))
        sched_i = integrate_plan(sched_plan, lock, repo_root=repo_root, head_weights=head_weights)
        schedule.append({**meta, "one_x_two": sched_i["final_1x2"], "matrix": sched_i["final_matrix"]})
        tracker.observe_fixture(row["home_team_id"], row["away_team_id"], row["cutoff"])

    mraw = metrics(raw_v2, labels, draw_threshold); m0 = metrics(core, labels, draw_threshold); m7 = metrics(schedule, labels, draw_threshold)
    boot = bootstrap_delta(per_match_ll(schedule, labels), per_match_ll(core, labels)); global_gain = m0["logloss"] - m7["logloss"]
    gg = group_gate(core, schedule, labels, global_gain)
    coverage_core = coverage_sched = 1.0
    gates = {
        "logloss_gain_ge_0_001": global_gain >= 0.0010,
        "paired_bootstrap_hi_lt_0": boot["hi"] < 0.0,
        "brier_nonharm": m7["brier"] <= m0["brier"] + 0.0010,
        "rps_nonharm": m7["rps"] <= m0["rps"] + 0.0010,
        "draw_logloss_nonharm": m7["draw"]["logloss"] <= m0["draw"]["logloss"] + 0.0020,
        "draw_ece_nonharm": m7["draw"]["ece"] <= m0["draw"]["ece"] + 0.010,
        "score_matrix_nonharm": m7["exact_score_logloss"] <= m0["exact_score_logloss"] + 0.0050,
        "worst_group_nonharm": gg["passed"],
        "coverage_nonharm": coverage_sched >= coverage_core - 0.02,
        "uncertainty_nonharm": m7["uncertainty_calibration"]["top1_error_ece"] <= m0["uncertainty_calibration"]["top1_error_ece"] + 0.010,
    }
    accepted = all(gates.values())
    layer_status = {
        "0_v2_team_core": "REFERENCE", "1_player_capability": "BLOCKED_DATA", "2_expected_lineup": "BLOCKED_DATA",
        "3_confirmed_lineup": "BLOCKED_DATA", "4_bench_substitution": "BLOCKED_DATA", "5_coach_regime": "BLOCKED_DATA",
        "6_tactical_matchup": "BLOCKED_DATA", "7_fitness_schedule_travel": "ACCEPTED" if accepted else "REJECTED_ABLATION",
        "8_referee_competition_environment": "BLOCKED_DATA", "9_pre_match_process_hazard": "BLOCKED_DATA",
    }
    return {
        "schema_version": "football3-context-translator-v1-validation-v1", "research_only": True, "strict_prospective": False, "post_view_research": True,
        "formal_promotion_eligible": False, "n": len(feats), "development_n": len(dev), "source_v2_run_id": manifest["run_id"], "source_v2_head": manifest["head"],
        "source_prediction_sha256": manifest["prediction_sha256"], "draw_threshold_dev_only": draw_threshold, "schedule_beta_dev_only": beta,
        "independent_head_weights_dev_only": head_weights, "raw_v2_reference_metrics": mraw, "baseline_metrics": m0, "schedule_layer_metrics": m7,
        "schedule_vs_core_logloss_bootstrap": boot, "group_gate": gg, "groups_core": grouped(core, labels, draw_threshold), "groups_schedule": grouped(schedule, labels, draw_threshold),
        "coverage": {"core": coverage_core, "schedule": coverage_sched, "grade_mix": {"TEAM_ONLY": len(feats)}}, "promotion_gates": gates, "layer_status": layer_status,
        "layer7_components": {"rest_7_14_28_density_season_phase": "IMPLEMENTED", "travel_distance_timezone": "BLOCKED_DATA", "player_minute_load_extra_time": "BLOCKED_DATA"},
        "blocked_data_reason": "No same-cohort approved PIT-valid player/target-lineup/coach/environment/minute-event source is present in the sealed 5256-match Artifact. Missing layers are not fabricated.",
        "adversarial_probe": run_probes(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--unit", action="store_true"); ap.add_argument("--artifact-dir"); ap.add_argument("--repo-root", default="."); ap.add_argument("--out")
    a = ap.parse_args()
    if a.unit:
        res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TranslatorUnitTests))
        if not res.wasSuccessful(): return 1
    if a.artifact_dir:
        report = historical_acceptance(Path(a.artifact_dir), Path(a.repo_root).resolve()); out = Path(a.out or "translator_validation.json"); out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"n": report["n"], "layer7": report["layer_status"]["7_fitness_schedule_travel"], "core_ll": report["baseline_metrics"]["logloss"],
                          "schedule_ll": report["schedule_layer_metrics"]["logloss"], "bootstrap": report["schedule_vs_core_logloss_bootstrap"], "gates": report["promotion_gates"]}, indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())
