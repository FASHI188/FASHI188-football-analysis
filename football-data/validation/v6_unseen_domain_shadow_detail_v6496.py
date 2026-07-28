#!/usr/bin/env python3
"""Independent detail audit for V6.49.6 unseen-domain shadow decisions.

Recomputes every prospective unseen-domain shadow decision from the frozen V6.49.6 rule
and immutable V6.49.2 selector ledger, then cross-checks aggregate counts against the
main V6.49.6 receipt. Audit only: no pick/probability/threshold/weight mutation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import v6_unseen_domain_shadow_v6496 as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_unseen_domain_shadow_detail_v6496_status.json"


def load(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"not object: {path}")
    return x


def main() -> int:
    freeze = load(base.FREEZE)
    main_status = load(base.STATUS)
    source = load(base.SOURCE_FREEZE)
    ledger = load(base.SOURCE_LEDGER)
    epoch = base.parse_dt(freeze.get("freeze_timestamp_utc"))
    if epoch is None:
        raise RuntimeError("invalid shadow epoch")
    threshold = float((freeze.get("shadow_rule") or {}).get("shadow_threshold") or 0.55)
    unseen = set(str(x) for x in (freeze.get("unseen_target_domains") or []))

    predictions = [e for e in (ledger.get("events") or []) if e.get("event_type") == "SELECTOR_PREDICTION_FROZEN"]
    settlements = {
        str(e.get("match_id")): e for e in (ledger.get("events") or []) if e.get("event_type") == "RESULT_SETTLED"
    }

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    max_prob_sum_residual = 0.0
    for pe in predictions:
        payload = pe.get("payload") or {}
        fi = payload.get("fixture_identity") or {}
        cid = str(fi.get("competition_id") or "")
        if cid not in unseen:
            continue
        kickoff = base.parse_dt(fi.get("kickoff_at"))
        if kickoff is None or kickoff <= epoch:
            continue
        selector = payload.get("selector") or {}
        if selector.get("selected") is True:
            errors.append(f"active_selected_in_unseen:{pe.get('match_id')}")
            continue
        pred = payload.get("prediction") or {}
        pick = str(pred.get("pick") or "")
        probs0 = pred.get("probabilities") or {}
        if pick not in base.D or any(d not in probs0 for d in base.D):
            errors.append(f"bad_prediction:{pe.get('match_id')}")
            continue
        probs = {d: float(probs0[d]) for d in base.D}
        residual = abs(sum(probs.values()) - 1.0)
        max_prob_sum_residual = max(max_prob_sum_residual, residual)
        if residual > 1e-9:
            errors.append(f"probability_sum:{pe.get('match_id')}:{residual}")
        pmax = float(pred.get("pmax"))
        score, support, pbin = base.global_score(source, pick, pmax)
        selected = bool(score >= threshold)
        se = settlements.get(str(pe.get("match_id")))
        actual = None
        settled = False
        if se is not None:
            sp = se.get("payload") or {}
            if sp.get("prediction_event_hash") == pe.get("event_hash"):
                result = sp.get("result") or {}
                a = str(result.get("actual_result") or "")
                if a in base.D:
                    actual = a
                    settled = True
        rows.append({
            "match_id": str(pe.get("match_id")),
            "prediction_event_hash": pe.get("event_hash"),
            "competition_id": cid,
            "kickoff_at": str(fi.get("kickoff_at") or ""),
            "home_team": str(fi.get("home_team") or ""),
            "away_team": str(fi.get("away_team") or ""),
            "active_selector_selected": False,
            "pick": pick,
            "pmax": pmax,
            "probabilities": probs,
            "shadow_pmax_bin": pbin,
            "shadow_global_support": support,
            "shadow_score": score,
            "shadow_threshold": threshold,
            "shadow_selected": selected,
            "settled": settled,
            "actual": actual,
        })

    rows.sort(key=lambda r: (r["kickoff_at"], r["competition_id"], r["home_team"], r["away_team"]))
    selected_n = sum(int(r["shadow_selected"]) for r in rows)
    settled_n = sum(int(r["settled"]) for r in rows)
    selected_settled_n = sum(int(r["shadow_selected"] and r["settled"]) for r in rows)
    mp = main_status.get("population") or {}
    me = main_status.get("evaluation") or {}
    main_selected_settled = int(((me.get("shadow_selected_settled") or {}).get("count")) or 0)

    checks = {
        "main_status_pass": main_status.get("status") == "PASS",
        "population_count_matches_main": len(rows) == int(mp.get("eligible_unseen_prediction_count") or 0),
        "selected_count_matches_main": selected_n == int(mp.get("shadow_selected_count") or 0),
        "selected_settled_count_matches_main": selected_settled_n == main_selected_settled,
        "all_rows_are_unseen_domains": all(r["competition_id"] in unseen for r in rows),
        "all_rows_were_future_at_shadow_epoch": all((base.parse_dt(r["kickoff_at"]) or epoch) > epoch for r in rows),
        "all_active_selector_actions_remain_abstain": all(r["active_selector_selected"] is False for r in rows),
        "all_shadow_decisions_match_frozen_threshold": all(r["shadow_selected"] == (r["shadow_score"] >= threshold) for r in rows),
        "probability_sum_residual_within_1e_9": max_prob_sum_residual <= 1e-9,
        "no_recompute_errors": not errors,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    out = {
        "schema_version": "V6.49.6-unseen-domain-shadow-detail-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": status,
        "freeze_timestamp_utc": freeze.get("freeze_timestamp_utc"),
        "source_model_sha256": freeze.get("source_model_sha256"),
        "unseen_target_domains": sorted(unseen),
        "shadow_threshold": threshold,
        "counts": {
            "population": len(rows),
            "shadow_selected": selected_n,
            "settled": settled_n,
            "shadow_selected_settled": selected_settled_n,
        },
        "audit": {
            "checks": checks,
            "max_probability_sum_residual": max_prob_sum_residual,
            "errors": errors,
        },
        "shadow_predictions": rows,
        "governance": {
            "audit_only": True,
            "active_selector_change": False,
            "probability_mutation": False,
            "threshold_change": False,
            "historical_backfill": False,
            "formal_weight": 0,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "counts": out["counts"], "audit": out["audit"]}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
