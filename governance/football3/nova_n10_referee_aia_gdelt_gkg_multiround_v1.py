#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nova_n10_referee_aia_gdelt_gkg_daily_v1 import (
    build_daily_url,
    fetch,
    normalize_identity,
    scan_zip,
    sha256_bytes,
)

class GDELTMultiRoundError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise GDELTMultiRoundError(msg)

def validate_target(target: dict[str, Any]) -> None:
    req(len(target["date_sequence"]) == 8, "FROZEN_DAY_N")
    req(target["date_sequence"][0] == target["published_date"], "WINDOW_START")
    req(
        normalize_identity(target["official_url"]) ==
        (target["normalized_host"], target["normalized_path"]),
        "TARGET_NORMALIZATION",
    )

def audit_target(
    target: dict[str, Any],
    source: dict[str, Any],
    acquisition: dict[str, Any],
) -> dict[str, Any]:
    reports = []
    errors = []
    positive_days = []
    for day in target["date_sequence"]:
        u = build_daily_url(source["daily_url_template"], day)
        try:
            raw, final, headers = fetch(
                u,
                timeout=int(acquisition["request_timeout_seconds"]),
                limit=int(acquisition["max_zip_bytes"]),
                allowed_host=source["allowed_host"],
            )
            scan = scan_zip(raw, target, source["expected_zip_member_suffix"])
            report = {
                "date": day,
                "source_url": u,
                "final_url": final,
                "zip_sha256": sha256_bytes(raw),
                "zip_bytes": len(raw),
                "content_type": headers.get("content-type"),
                **scan,
            }
            reports.append(report)
            if scan["match_n"] > 0:
                positive_days.append(day)
        except Exception as exc:
            errors.append({
                "date": day,
                "source_url": u,
                "error": f"{type(exc).__name__}:{exc}"[:500],
            })
    return {
        "round": target["round"],
        "published_date": target["published_date"],
        "official_url": target["official_url"],
        "window_day_n": len(target["date_sequence"]),
        "successful_day_n": len(reports),
        "error_n": len(errors),
        "reports": reports,
        "errors": errors,
        "positive_day_n": len(positive_days),
        "positive_days": positive_days,
        "all_days_fetched": len(reports) == len(target["date_sequence"]),
    }

def run(registry: Path, out: Path) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY", "STATUS")
    req(p["exact_base"] == "23b2fc25297cb011e0c54fe4f4691a1eb8b9f94b", "EXACT_BASE")
    req(
        p["parent"]["canonical_ledger_sha256"] ==
        "0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b",
        "LEDGER_SHA",
    )
    req(p["parent"]["prior_round10_result"] == "ZERO_EXACT_URL_MATCH_8_OF_8_DAYS", "ROUND10_PARENT")
    req(p["parent"]["source_family_closed"] is False, "SOURCE_ALREADY_CLOSED")
    req([t["round"] for t in p["targets"]] == [1, 19, 38], "FROZEN_ROUNDS")
    req(sum(len(t["date_sequence"]) for t in p["targets"]) == 24, "TOTAL_FROZEN_DAYS")

    h = p["hard_rules"]
    req(h["switch_source_family_before_completion"] is False, "SOURCE_SWITCH_FORBIDDEN")
    req(h["result_labels_read"] is False and h["score_values_read"] is False, "ZERO_LABEL")
    req(
        h["match_payload_read"] is False and
        h["standings_payload_read"] is False and
        h["player_stats_payload_read"] is False,
        "NO_SPORT_PAYLOAD",
    )
    req(h["article_body_read"] is False and h["appointment_body_read"] is False, "NO_BODY")
    req(h["full_gkg_row_persisted"] is False, "NO_FULL_ROW")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(h["candidate_weight"] == 0 and h["matrix_delta"] == 0, "ZERO_WEIGHT")

    for target in p["targets"]:
        validate_target(target)

    source = p["source"]
    acquisition = p["acquisition_contract"]
    audits = [audit_target(t, source, acquisition) for t in p["targets"]]

    positive_rounds = sorted(a["round"] for a in audits if a["positive_day_n"] > 0)
    total_successful_days = sum(a["successful_day_n"] for a in audits)
    total_errors = sum(a["error_n"] for a in audits)
    all_days_fetched = total_successful_days == 24 and total_errors == 0
    all_zero = not positive_rounds
    source_family_closed = all_zero and all_days_fetched

    classification = (
        p["decision_contract"]["positive_classification"]
        if positive_rounds else
        p["decision_contract"]["all_zero_classification"]
    )
    if positive_rounds:
        reason = "EXACT_AIA_URL_PRESENT_IN_GDELT_DAILY_GKG_FOR_ONE_OR_MORE_FROZEN_ROUNDS"
    elif all_days_fetched:
        reason = "ZERO_EXACT_AIA_URL_MATCHES_ACROSS_ROUNDS_1_19_38_AND_PARENT_ROUND10"
    else:
        reason = "INCOMPLETE_GDELT_MULTIROUND_FETCH_NO_POSITIVE_MATCH"

    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "football3-nova-n10-referee-aia-gdelt-gkg-multiround-receipt-v1",
        "status": "N10_REFEREE_AIA_GDELT_DAILY_GKG_MULTIROUND_COMPLETE",
        "classification": classification,
        "reason": reason,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "target_rounds": [1, 19, 38],
        "parent_round10_zero_match_proven": True,
        "total_frozen_day_n": 24,
        "total_successful_day_n": total_successful_days,
        "total_error_n": total_errors,
        "all_days_fetched": all_days_fetched,
        "audits": audits,
        "positive_round_n": len(positive_rounds),
        "positive_rounds": positive_rounds,
        "source_family_closed": source_family_closed,
        "full_gkg_rows_persisted": False,
        "matching_full_lines_persisted": False,
        "article_body_read": False,
        "appointment_body_read": False,
        "match_payload_read": False,
        "standings_payload_read": False,
        "player_stats_payload_read": False,
        "exact_observation_time_proven": False,
        "formal_available_at_proven": False,
        "fixture_level_binding_complete": False,
        "full_big5_data_ready": False,
        "referee_oof_allowed": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "next_step": (
            p["decision_contract"]["next_if_positive"]
            if positive_rounds else
            (p["decision_contract"]["next_if_all_zero"]
             if source_family_closed else
             "RETRY_ONLY_FAILED_FROZEN_GDELT_DAYS; DO_NOT_SWITCH_SOURCE_OR_CLOSE_FAMILY")
        ),
    }
    (out / "aia_gdelt_gkg_multiround_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt

def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--registry", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    x = a.parse_args()
    run(x.registry, x.out)

if __name__ == "__main__":
    main()
