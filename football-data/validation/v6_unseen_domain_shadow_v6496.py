#!/usr/bin/env python3
"""V6.49.6 prospective unseen-domain reliability shadow audit.

This is a diagnostic sidecar of the current V6.49.2 forward chain. It does not create
an active pick, change probabilities, lower the selector threshold, or alter CURRENT.

Purpose
-------
V6.49.2 correctly abstains when a competition was not present in the frozen V6.47.5
hierarchical-selector training domains. That is safe, but it also means current UCL / K
League fixtures cannot contribute evidence about whether the already-frozen GLOBAL
(direction x pmax-bin) reliability cells transfer to unseen domains.

This sidecar pre-declares a shadow rule while fixtures are still future:
  shadow_select iff source domain is unseen AND frozen global reliability >= the same
  frozen 0.55 selector threshold.

The active selector remains ABSTAIN. The shadow is evaluated only after the immutable
V6.49.2 prediction receives a matching 90-minute RESULT_SETTLED event.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FREEZE = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_freeze.json"
SOURCE_LEDGER = ROOT / "forward" / "v6_fresh_selector_events_v6492.json"
UNIFIED_STATUS = ROOT / "manifests" / "v6_unified_forward_pipeline_v6482_status.json"
FREEZE = ROOT / "manifests" / "v6_unseen_domain_shadow_v6496_freeze.json"
STATUS = ROOT / "manifests" / "v6_unseen_domain_shadow_v6496_status.json"

SCHEMA_FREEZE = "V6.49.6-unseen-domain-shadow-freeze-r1"
SCHEMA_STATUS = "V6.49.6-unseen-domain-shadow-status-r1"
Z90 = 1.6448536269514722
D = ("home", "draw", "away")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(value: object) -> datetime | None:
    try:
        x = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(hits: int, n: int) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = hits / n
    z2 = Z90 * Z90
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    rad = Z90 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / den
    return max(0.0, center - rad), min(1.0, center + rad)


def ensure_freeze(now: datetime) -> dict[str, Any]:
    if FREEZE.exists():
        x = load(FREEZE)
        if x.get("schema_version") != SCHEMA_FREEZE or x.get("status") != "FROZEN":
            raise RuntimeError("invalid V6.49.6 freeze")
        return x

    source = load(SOURCE_FREEZE)
    if source.get("status") != "FROZEN":
        raise RuntimeError("source selector freeze is not frozen")
    unified = load(UNIFIED_STATUS) if UNIFIED_STATUS.exists() else {}
    target_domains = [str(x) for x in (unified.get("target_domains") or [])]
    trained = [str(x) for x in (source.get("trained_domains") or [])]
    unseen = sorted(set(target_domains) - set(trained))
    if not unseen:
        raise RuntimeError("no unseen target domains to audit")

    x = {
        "schema_version": SCHEMA_FREEZE,
        "status": "FROZEN",
        "freeze_timestamp_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "source_chain": "V6.49.2_fresh_selector_events",
        "source_selector_freeze_sha256": file_sha(SOURCE_FREEZE),
        "source_model_sha256": source.get("model_sha256"),
        "trained_domains": trained,
        "unseen_target_domains": unseen,
        "shadow_rule": {
            "active_selector_action": "ABSTAIN_UNCHANGED",
            "shadow_score": "frozen_global_direction_x_pmax_bin_posterior_mean",
            "shadow_threshold": float(source.get("selector_threshold") or 0.55),
            "shadow_select_rule": "unseen domain AND global reliability score >= frozen selector threshold",
            "fixture_must_still_be_future_at_shadow_freeze": True,
            "matching_v6492_prediction_event_required": True,
            "matching_v6492_result_settlement_required_for_evaluation": True,
        },
        "review_gate": {
            "minimum_shadow_selected_settled": 100,
            "minimum_accuracy": 0.60,
            "minimum_wilson90_lower": 0.55,
            "minimum_domain_count": len(unseen),
            "minimum_selected_settled_per_unseen_domain": 20,
        },
        "governance": {
            "research_diagnostic_only": True,
            "active_selector_change": False,
            "probability_mutation": False,
            "threshold_change": False,
            "historical_backfill": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    FREEZE.parent.mkdir(parents=True, exist_ok=True)
    FREEZE.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return x


def global_score(source: dict[str, Any], pick: str, pmax: float) -> tuple[float, int, int]:
    model = source.get("model") or {}
    edges = [float(x) for x in (model.get("pmax_edges") or [])]
    if len(edges) < 2:
        raise RuntimeError("source pmax edges missing")
    b = len(edges) - 2
    for i in range(len(edges) - 1):
        if edges[i] <= pmax < edges[i + 1]:
            b = i
            break
    cell = ((model.get("reliability_model") or {}).get("global") or {}).get(f"{pick}|{b}")
    if not isinstance(cell, dict):
        raise RuntimeError(f"source global reliability cell missing for {pick}|{b}")
    return float(cell.get("posterior_mean")), int(cell.get("n") or 0), b


def metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    hits = sum(int(r["pick"] == r["actual"]) for r in rows)
    lo, hi = wilson(hits, n)
    by = Counter(str(r["competition_id"]) for r in rows)
    ll = br = rps = shadow_brier = 0.0
    for r in rows:
        p = r["probabilities"]
        y = {d: 1.0 if d == r["actual"] else 0.0 for d in D}
        ll -= math.log(max(1e-15, float(p[r["actual"]])))
        br += sum((float(p[d]) - y[d]) ** 2 for d in D)
        rps += ((float(p["home"]) - y["home"]) ** 2 + ((float(p["home"]) + float(p["draw"])) - (y["home"] + y["draw"])) ** 2) / 2.0
        shadow_brier += (float(r["shadow_score"]) - float(r["pick"] == r["actual"])) ** 2
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n,
        "wilson90": [lo, hi],
        "log_loss": ll / n,
        "brier": br / n,
        "rps": rps / n,
        "shadow_reliability_brier": shadow_brier / n,
        "domain_count": len(by),
        "by_competition_n": dict(sorted(by.items())),
    }


def main() -> int:
    now = now_utc()
    freeze = ensure_freeze(now)
    source = load(SOURCE_FREEZE)
    ledger = load(SOURCE_LEDGER)
    events = ledger.get("events") or []
    predictions = {str(e.get("match_id")): e for e in events if e.get("event_type") == "SELECTOR_PREDICTION_FROZEN"}
    settlements = {str(e.get("match_id")): e for e in events if e.get("event_type") == "RESULT_SETTLED"}
    unseen = set(str(x) for x in (freeze.get("unseen_target_domains") or []))
    epoch = parse_dt(freeze.get("freeze_timestamp_utc"))
    if epoch is None:
        raise RuntimeError("invalid shadow freeze timestamp")
    threshold = float((freeze.get("shadow_rule") or {}).get("shadow_threshold") or 0.55)

    population: list[dict[str, Any]] = []
    excluded = Counter()
    for mid, pe in sorted(predictions.items()):
        payload = pe.get("payload") or {}
        fi = payload.get("fixture_identity") or {}
        cid = str(fi.get("competition_id") or "")
        if cid not in unseen:
            continue
        kickoff = parse_dt(fi.get("kickoff_at"))
        if kickoff is None or kickoff <= epoch:
            excluded["not_future_at_shadow_freeze"] += 1
            continue
        selector = payload.get("selector") or {}
        if selector.get("selected") is True:
            excluded["unexpected_active_selected"] += 1
            continue
        pred = payload.get("prediction") or {}
        pick = str(pred.get("pick") or "")
        probs = pred.get("probabilities") or {}
        if pick not in D or any(d not in probs for d in D):
            excluded["bad_prediction_payload"] += 1
            continue
        score, support, pbin = global_score(source, pick, float(pred.get("pmax")))
        row = {
            "match_id": mid,
            "prediction_event_hash": pe.get("event_hash"),
            "competition_id": cid,
            "kickoff_at": str(fi.get("kickoff_at") or ""),
            "home_team": str(fi.get("home_team") or ""),
            "away_team": str(fi.get("away_team") or ""),
            "pick": pick,
            "pmax": float(pred.get("pmax")),
            "probabilities": {d: float(probs[d]) for d in D},
            "shadow_score": score,
            "shadow_support": support,
            "shadow_pmax_bin": pbin,
            "shadow_selected": bool(score >= threshold),
        }
        se = settlements.get(mid)
        if se and (se.get("payload") or {}).get("prediction_event_hash") == pe.get("event_hash"):
            result = (se.get("payload") or {}).get("result") or {}
            row["actual"] = str(result.get("actual_result") or "")
            row["settled"] = row["actual"] in D
        else:
            row["settled"] = False
        population.append(row)

    selected_population = [r for r in population if r["shadow_selected"]]
    settled = [r for r in population if r.get("settled")]
    selected_settled = [r for r in selected_population if r.get("settled")]
    by_pop = Counter(r["competition_id"] for r in population)
    by_sel = Counter(r["competition_id"] for r in selected_population)
    by_sel_set = Counter(r["competition_id"] for r in selected_settled)

    metrics_all = metric(settled)
    metrics_selected = metric(selected_settled)
    gate = freeze.get("review_gate") or {}
    unseen_list = list(freeze.get("unseen_target_domains") or [])
    per_domain_ok = all(by_sel_set.get(cid, 0) >= int(gate.get("minimum_selected_settled_per_unseen_domain") or 20) for cid in unseen_list)
    results = {
        "selected_n": metrics_selected.get("count", 0) >= int(gate.get("minimum_shadow_selected_settled") or 100),
        "accuracy": metrics_selected.get("accuracy") is not None and metrics_selected.get("accuracy") >= float(gate.get("minimum_accuracy") or 0.60),
        "wilson90_lower": bool(metrics_selected.get("wilson90")) and metrics_selected["wilson90"][0] is not None and metrics_selected["wilson90"][0] >= float(gate.get("minimum_wilson90_lower") or 0.55),
        "domain_count": metrics_selected.get("domain_count", 0) >= int(gate.get("minimum_domain_count") or len(unseen_list)),
        "per_domain_minimum": per_domain_ok,
    }
    all_pass = all(results.values())

    status = {
        "schema_version": SCHEMA_STATUS,
        "generated_at_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS",
        "freeze": freeze,
        "population": {
            "eligible_unseen_prediction_count": len(population),
            "shadow_selected_count": len(selected_population),
            "shadow_abstained_count": len(population) - len(selected_population),
            "by_competition": dict(sorted(by_pop.items())),
            "shadow_selected_by_competition": dict(sorted(by_sel.items())),
            "excluded": dict(sorted(excluded.items())),
        },
        "evaluation": {
            "all_unseen_settled": metrics_all,
            "shadow_selected_settled": metrics_selected,
            "shadow_selected_settled_by_competition": dict(sorted(by_sel_set.items())),
            "review_gate": {"results": results, "all_pass": all_pass},
            "decision": "SHADOW_TRANSFER_REVIEW_REQUIRED" if all_pass else "PENDING_UNSEEN_DOMAIN_FORWARD_SAMPLE_OR_QUALITY",
        },
        "governance": {
            "active_selector_change": False,
            "probability_mutation": False,
            "threshold_change": False,
            "historical_backfill": False,
            "formal_weight": 0,
            "automatic_promotion": False,
            "current_rule_change": False,
        },
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status["status"], "population": status["population"], "evaluation": status["evaluation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
