#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "external_signal_final_adjudication_v521_status.json"


def _read(path: str) -> dict[str, Any] | None:
    target = ROOT / path
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _state(label: str, source: str, status: str, reason: str, *, formal_weight: int = 0, next_action: str = "") -> dict[str, Any]:
    return {
        "layer": label,
        "source_receipt": source,
        "adjudication": status,
        "reason": reason,
        "formal_weight": formal_weight,
        "probability_change_authorized": False,
        "next_action": next_action,
    }


def adjudicate_fpl() -> dict[str, Any]:
    path = "config/fpl_context_final_registry_v520.json"
    r = _read(path)
    if not r:
        return _state("EPL_FPL_CONTEXT", path, "EVIDENCE_INCOMPLETE", "Final strict-lag registry missing.")
    if r.get("status") == "RESEARCH_LAYER_CLOSED_KEEP_FORMAL_WEIGHT_0":
        return _state(
            "EPL_FPL_CONTEXT", path, "CLOSED_WEIGHT_0",
            "Strict previous-gameweek chronology selected the zero-effect projection; same-gameweek positive signal is superseded by timestamp-risk audit.",
            next_action="Reopen only with independently timestamped pre-deadline availability evidence or untouched future-season validation.",
        )
    return _state("EPL_FPL_CONTEXT", path, "REVIEW_REQUIRED", f"Unexpected registry status: {r.get('status')}")


def adjudicate_recent_xg() -> dict[str, Any]:
    path = "manifests/recent_xg_v513_governance_freeze.json"
    r = _read(path)
    if not r:
        return _state("RECENT_2025_26_XG", path, "EVIDENCE_INCOMPLETE", "Recent-season xG governance freeze missing.")
    return _state(
        "RECENT_2025_26_XG", path, "CLOSED_WEIGHT_0",
        "Five-league 2025/26 forward holdout was observed; no domain passed the frozen four-target joint gate and holdout reuse for tuning is prohibited.",
        next_action="Only a new untouched season or materially new independent input may reopen this route.",
    )


def adjudicate_player_xi() -> dict[str, Any]:
    path = "config/player_xi_layer_final_registry_v505.json"
    r = _read(path)
    if not r:
        return _state("PLAYER_XI_RESIDUAL", path, "EVIDENCE_INCOMPLETE", "Player-XI final registry missing.")
    return _state(
        "PLAYER_XI_RESIDUAL", path, "CLOSED_WEIGHT_0",
        "Player-XI residual/matrix projection research was closed after the four-target gate failed.",
        next_action="Do not retune on already observed seasons.",
    )


def adjudicate_shot_proxy() -> dict[str, Any]:
    path = "config/shot_quality_proxy_final_registry_v508.json"
    r = _read(path)
    if not r:
        return _state("SHOT_SOT_PROXY", path, "EVIDENCE_INCOMPLETE", "Shot/SOT proxy final registry missing.")
    return _state(
        "SHOT_SOT_PROXY", path, "CLOSED_WEIGHT_0",
        "Frozen discovery domains failed the strict joint promotion gate; replication was not authorized.",
        next_action="Do not relax the frozen other-axis noninferiority gate post hoc.",
    )


def adjudicate_transfermarkt_value() -> dict[str, Any]:
    path = "manifests/lineup_valuation_readiness_v518_status.json"
    r = _read(path)
    if not r:
        return _state(
            "TRANSFERMARKT_PLAYER_VALUE", path, "AUXILIARY_ONLY",
            "Historical valuation source is useful for player-importance context, but complete fresh 22-starter coverage is not established here.",
            next_action="Use only as an auxiliary player-importance feature when a strictly prior valuation exists for the relevant player.",
        )
    return _state(
        "TRANSFERMARKT_PLAYER_VALUE", path, "AUXILIARY_ONLY",
        "Starter-slot valuation coverage is high, but complete fresh 22-player match coverage is insufficient for a five-league primary model.",
        next_action="Keep as conditional player-importance evidence; no direct probability override.",
    )


def adjudicate_market_ceiling() -> dict[str, Any]:
    path = "manifests/retrospective_market_outcome_ceiling_v522_status.json"
    r = _read(path)
    if not r or r.get("status") != "PASS":
        return _state("RETROSPECTIVE_MARKET_CEILING", path, "EVIDENCE_INCOMPLETE", "Five-league retrospective market ceiling receipt is missing or incomplete.")
    reports = r.get("reports") or {}
    improved = []
    for cid, report in reports.items():
        formal = report.get("formal") or {}
        closing = report.get("market_closing") or {}
        if (
            float(closing.get("brier") or 1e9) < float(formal.get("brier") or -1e9)
            and float(closing.get("rps") or 1e9) < float(formal.get("rps") or -1e9)
        ):
            improved.append(cid)
    return _state(
        "RETROSPECTIVE_MARKET_CEILING", path, "HIGHEST_RESEARCH_PRIORITY_REFERENCE_ONLY",
        f"Retrospective synchronized-looking market surfaces improve both Brier and RPS over the formal baseline in {len(improved)}/5 audited leagues: {improved}. Historical quote timestamps are unavailable, so this is priority evidence only, not PIT evidence.",
        next_action="Prioritize real question-time synchronized 1X2+AH+OU capture and prospective evidence accumulation; never relabel 2025/26 retrospective prices as PIT.",
    )


def adjudicate_prospective_market() -> dict[str, Any]:
    contract_path = "config/prospective_market_snapshot_contract_v523.json"
    audit_path = "manifests/prospective_market_snapshot_v523_status.json"
    contract = _read(contract_path)
    audit = _read(audit_path)
    if not contract:
        return _state("PROSPECTIVE_MARKET_EVIDENCE", contract_path, "EVIDENCE_INCOMPLETE", "Prospective market evidence contract missing.")
    if not audit:
        return _state("PROSPECTIVE_MARKET_EVIDENCE", audit_path, "ACTIVE_COLLECTION_AUDIT_PENDING", "Contract exists but repository audit has not run yet.")
    status = str(audit.get("status") or "")
    valid = int(audit.get("valid_snapshot_count") or 0)
    if status == "FAIL":
        return _state(
            "PROSPECTIVE_MARKET_EVIDENCE", audit_path, "EVIDENCE_REPOSITORY_FAIL_CLOSED",
            f"Prospective snapshot repository contains invalid/duplicate evidence; valid count={valid}.",
            next_action="Repair or quarantine invalid snapshots before any future OOF use.",
        )
    if status == "NO_SNAPSHOTS_YET":
        return _state(
            "PROSPECTIVE_MARKET_EVIDENCE", audit_path, "ACTIVE_COLLECTION_NO_SNAPSHOTS_YET",
            "The timestamp/hash/synchronization contract and validator are active, but no genuine future PIT market snapshots have been accumulated yet.",
            next_action="For future pre-match analyses, persist validated question-time synchronized 1X2+AH+OU snapshots before kickoff.",
        )
    return _state(
        "PROSPECTIVE_MARKET_EVIDENCE", audit_path, "ACTIVE_COLLECTION_WITH_VALID_PIT_EVIDENCE",
        f"Prospective repository currently contains {valid} validated PIT market snapshots.",
        next_action="Continue prospective accumulation; only later chronological OOF may determine whether a market residual configuration is promotable.",
    )


def adjudicate_clubelo() -> dict[str, Any]:
    ingest_path = "manifests/clubelo_history_ingest_v515_status.json"
    residual_path = "manifests/clubelo_residual_oof_v515_status.json"
    ingest = _read(ingest_path)
    residual = _read(residual_path)
    if not ingest or ingest.get("schema_version") != "V5.1.5-clubelo-history-ingest-r2":
        return _state(
            "CLUBELO_RESIDUAL", ingest_path, "EVIDENCE_INCOMPLETE",
            "Corrected r2 identity/history receipt has not completed. Old r1 evidence is explicitly superseded.",
            next_action="Complete per-domain r2 histories before any residual result is admissible.",
        )
    if not residual:
        return _state("CLUBELO_RESIDUAL", residual_path, "EVIDENCE_INCOMPLETE", "r2 history exists but residual aggregate is missing.")
    if residual.get("execution_failure_domains"):
        return _state(
            "CLUBELO_RESIDUAL", residual_path, "EXECUTION_BLOCKED_WEIGHT_0",
            f"Residual aggregate still has execution failures: {residual.get('execution_failure_domains')}",
            next_action="Resolve execution/data coverage failure without changing frozen beta profiles or outcome gates.",
        )
    passed = list(residual.get("signal_pass_domains") or [])
    if passed:
        return _state(
            "CLUBELO_RESIDUAL", residual_path, "SHADOW_SIGNAL_WAIT_FUTURE",
            f"Shadow signal gate passed in: {passed}. 2025/26 has already been observed elsewhere, so this cannot authorize formal promotion.",
            next_action="Freeze the successful competition-specific configuration and validate prospectively on untouched 2026/27 data.",
        )
    return _state(
        "CLUBELO_RESIDUAL", residual_path, "CLOSED_WEIGHT_0",
        "Corrected PIT ClubElo residual test completed without a domain passing the frozen signal gate.",
        next_action="Do not retune observed folds; reopen only for new independent evidence or future season.",
    )


def adjudicate_gdelt() -> dict[str, Any]:
    path = "manifests/gdelt_recent_context_coverage_v517_status.json"
    r = _read(path)
    if not r or r.get("schema_version") != "V5.1.7-gdelt-recent-context-coverage-aggregate-r2":
        return _state(
            "GDELT_PREMATCH_CONTEXT", path, "EVIDENCE_INCOMPLETE",
            "Exact-kickoff, rate-limit-aware, non-truncated aggregate r2 has not completed; old 429/cap-contaminated coverage is superseded.",
            next_action="Complete r2 coverage before deciding whether historical news can support a model or only an exception layer.",
        )
    passed = list(r.get("passed_domains") or [])
    if len(passed) == 5:
        return _state(
            "GDELT_PREMATCH_CONTEXT", path, "DISCOVERY_LAYER_READY_ONLY",
            "All five domains passed metadata discovery and exact-kickoff gates, but GDELT seendate is observation time, not publisher time/content truth; metadata alone cannot mutate probabilities.",
            next_action="Fetch and freeze source article content, verify publisher timestamps where available, then extract structured injury/rotation/manager/task features in a new shadow model.",
        )
    return _state(
        "GDELT_PREMATCH_CONTEXT", path, "EXCEPTION_LAYER_ONLY",
        f"Only {len(passed)}/5 domains passed the strict discovery/kickoff gate: {passed}.",
        next_action="Use for current-time/major-event discovery only; do not build a broad historical probability layer from incomplete coverage.",
    )


def main() -> int:
    layers = [
        adjudicate_fpl(),
        adjudicate_recent_xg(),
        adjudicate_player_xi(),
        adjudicate_shot_proxy(),
        adjudicate_transfermarkt_value(),
        adjudicate_market_ceiling(),
        adjudicate_prospective_market(),
        adjudicate_clubelo(),
        adjudicate_gdelt(),
    ]
    formal_promotions = [x for x in layers if int(x.get("formal_weight") or 0) > 0]
    payload = {
        "schema_version": "V5.2.1-external-signal-final-adjudication-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "layers": layers,
        "formal_promotion_count": len(formal_promotions),
        "formal_probability_change_authorized": False,
        "current_formal_rule_change": False,
        "research_priority": "PROSPECTIVE_SYNCHRONIZED_MARKET_EVIDENCE_FIRST",
        "status": "COMPLETE" if all(x["adjudication"] != "EVIDENCE_INCOMPLETE" for x in layers) else "PARTIAL_WAITING_EVIDENCE",
        "governance": (
            "This is a read-only research adjudicator. It cannot grant nonzero formal weight or modify CURRENT. "
            "Any future formal promotion still requires the unique CURRENT hard gates, untouched chronological validation, "
            "one unified score matrix and full audit residuals."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
