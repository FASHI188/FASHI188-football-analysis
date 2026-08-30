from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import EngineState, Fixture, Parameters, joint_matrix, matrix_1x2

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"


def pred(engine: EngineState, fixture: Fixture):
    f = engine.predict_features(fixture)
    m = joint_matrix("DYNAMIC_NB_DIAGONAL", f, dispersion_home=8.0, dispersion_away=8.0, dependence=0.25)
    return f, matrix_1x2(m), sum(sum(r) for r in m)


def main() -> int:
    params = Parameters(); t0 = datetime(2026, 8, 1, tzinfo=timezone.utc); e = EngineState(params)
    zero_fixture = Fixture("zero", "NEW_LEAGUE", "2026/27", t0, "team_new_a", "team_new_b", 1)
    zero_f, zero_p, zero_sum = pred(e, zero_fixture)
    hist = [Fixture("h1", "NEW_LEAGUE", "2026/27", t0, "team_new_a", "team_c", 1), Fixture("h2", "NEW_LEAGUE", "2026/27", t0, "team_new_b", "team_d", 1)]
    e.apply_batch(hist, {"h1": (2, 0), "h2": (1, 1)})
    sparse_fixture = Fixture("sparse", "NEW_LEAGUE", "2026/27", t0 + timedelta(days=7), "team_new_a", "team_new_b", 2)
    sparse_f, sparse_p, sparse_sum = pred(e, sparse_fixture)
    before = sparse_f["home_evidence"]
    next_season = Fixture("next", "NEW_LEAGUE", "2027/28", t0 + timedelta(days=370), "team_new_a", "team_new_b", 1)
    next_f, next_p, next_sum = pred(e, next_season)
    checks = {
        "zero_bucket": zero_f["cold_start_bucket"] == "zero",
        "sparse_or_established_after_history": sparse_f["cold_start_bucket"] in {"sparse", "established"},
        "matrix_normalized_zero": abs(zero_sum - 1.0) < 1e-10,
        "matrix_normalized_sparse": abs(sparse_sum - 1.0) < 1e-10,
        "matrix_normalized_cross_season": abs(next_sum - 1.0) < 1e-10,
        "all_1x2_normalized": all(abs(sum(p.values()) - 1.0) < 1e-10 for p in (zero_p, sparse_p, next_p)),
        "cross_season_evidence_shrunk": float(next_f["home_evidence"]) < float(before),
        "zero_uncertainty_explicit": float(zero_f["uncertainty"]) > 0.0,
        "new_league_prior_available_without_market": all(v > 0.0 for v in zero_p.values()),
    }
    result = {"schema_version": "football3-v2-cold-start-audit-v1", "passed": all(checks.values()), "checks": checks,
              "zero": {"features": zero_f, "one_x_two": zero_p}, "sparse": {"features": sparse_f, "one_x_two": sparse_p},
              "cross_season": {"features": next_f, "one_x_two": next_p}, "scope": "synthetic structural audit only; not empirical promoted-team/new-league validation"}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "cold_start_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
