#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_pit_binding_v1 import (
    acquire_parent,
    acquire_resume,
    build_binding,
    req,
    sha256_bytes,
    stable_bytes,
)

class FinalWaybackRecoveryError(RuntimeError):
    pass

def run(registry_path: Path, aia_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_FINAL_WAYBACK_EXTERNAL_RECOVERY","STATUS")
    req(p["exact_base"]=="6c45c6629e0de3ccb486623df4a3fab8a109d8c0","EXACT_BASE")
    req(p["repository"]=="FASHI188/FASHI188-football-analysis","REPOSITORY")

    final=p["final_recovery_contract"]
    req(final["final_recovery_batch_number"]==1,"FINAL_BATCH_NUMBER")
    req(final["max_final_recovery_batches"]==1,"FINAL_BATCH_MAX")
    retry=[int(x) for x in final["retry_rounds_exact"]]
    success=[int(x) for x in final["successful_rounds_frozen"]]
    req(retry==[8,9,11,12,14,15,16,17,19,20,21,22,23,27,28,32,34,36,38],"FROZEN_RETRY_ROUNDS")
    req(success==[1,2,3,4,5,6,7,10,13,18,24,25,26,29,30,31,33,35,37],"FROZEN_SUCCESS_ROUNDS")
    req(set(retry).isdisjoint(success),"PARTITION_OVERLAP")
    req(set(retry)|set(success)==set(range(1,39)),"PARTITION_INCOMPLETE")
    req(set(final["genuine_post_cutoff_rounds_frozen"])=={24,25},"POST_CUTOFF_FROZEN")
    req(final["requery_successful_rounds_allowed"] is False,"NO_SUCCESS_REQUERY")
    req(final["requery_post_cutoff_rounds_allowed"] is False,"NO_POST_CUTOFF_REQUERY")

    hard=p["hard_rules"]
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(hard["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(hard["sky_article_body_reacquired"] is False,"NO_SKY_BODY")
    req(hard["archived_page_body_read"] is False,"NO_ARCHIVE_BODY")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    aia_raw=aia_path.read_bytes()
    req(sha256_bytes(aia_raw)==p["aia_ledger"]["sha256"],"AIA_LEDGER_SHA")
    aia=json.loads(aia_raw.decode("utf-8"))
    req(aia["competition"]=="Serie_A" and aia["season"]=="2022/23","AIA_IDENTITY")

    sky,anom,parent_prov=acquire_parent(p,token)
    fixture,resume_witness,resume_prov=acquire_resume(p,token)
    req(fixture.get("complete") is True and fixture.get("fixture_n")==380,"FROZEN_FIXTURE_LEDGER")
    prior_reports={int(x["round"]) for x in resume_witness.get("reports",[])}
    req(prior_reports==set(success),"PRIOR_SUCCESS_REPORTS")
    req(24 in prior_reports and 25 in prior_reports,"POST_CUTOFF_REPORTS_NOT_FROZEN")

    binding,witness=build_binding(p,sky,anom,aia,fixture,resume_witness)
    witness_errors={int(k):v for k,v in witness.get("errors",{}).items()}
    req(set(witness_errors).issubset(set(retry)),"NEW_ERRORS_OUTSIDE_RETRY_SET")
    new_reports={int(x["round"]) for x in witness.get("reports",[])}-set(success)
    req(new_reports.issubset(set(retry)),"NEW_REPORT_OUTSIDE_RETRY_SET")
    req(set(success).issubset({int(x["round"]) for x in witness.get("reports",[])}),"FROZEN_REPORT_LOSS")

    remaining_external=sorted(witness_errors)
    wayback_closed=bool(remaining_external)
    if remaining_external:
        next_step=final["next_if_external_errors_remain"]
    else:
        next_step=final["next_if_no_external_errors"]

    out.mkdir(parents=True,exist_ok=True)
    fixture_bytes=stable_bytes(fixture)
    witness_bytes=stable_bytes(witness)
    binding_bytes=stable_bytes(binding)
    (out/"zero_label_fixture_schedule_ledger.json").write_bytes(fixture_bytes)
    (out/"wayback_final_witness_ledger.json").write_bytes(witness_bytes)
    (out/"sky_publication_pit_binding_final_ledger.json").write_bytes(binding_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-wayback-final-recovery-receipt-v1",
        "status":"N10_REFEREE_WAYBACK_FINAL_RECOVERY_COMPLETE",
        "classification":binding["classification"],
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "aia_ledger_sha256":sha256_bytes(aia_raw),
        "parent_provenance":parent_prov,
        "resume_provenance":resume_prov,
        "final_recovery_batch_number":1,
        "max_final_recovery_batches":1,
        "frozen_success_round_n":len(success),
        "frozen_success_rounds":success,
        "retry_round_n":len(retry),
        "retry_rounds":retry,
        "new_success_round_n":len(new_reports),
        "new_success_rounds":sorted(new_reports),
        "remaining_external_error_n":len(remaining_external),
        "remaining_external_error_rounds":remaining_external,
        "wayback_closed_for_remaining_external_error_rounds":wayback_closed,
        "fixture_schedule_complete":fixture["complete"],
        "fixture_schedule_covered_round_n":fixture["covered_round_n"],
        "fixture_n":fixture["fixture_n"],
        "fixture_schedule_sha256":sha256_bytes(fixture_bytes),
        "wayback_witness_sha256":sha256_bytes(witness_bytes),
        "pit_binding_sha256":sha256_bytes(binding_bytes),
        "witness_report_n":witness["report_n"],
        "witness_error_n":witness["error_n"],
        "pit_pass_round_n":binding["pit_pass_round_n"],
        "pit_pass_rounds":binding["pit_pass_rounds"],
        "no_capture_rounds":binding["no_capture_rounds"],
        "post_cutoff_capture_rounds":binding["post_cutoff_capture_rounds"],
        "timestamp_conflict_rounds":binding["timestamp_conflict_rounds"],
        "publication_pit_binding_complete":binding["publication_pit_binding_complete"],
        "formal_available_at_proven_for_publication_pages":binding["formal_available_at_proven_for_publication_pages"],
        "formal_available_at_proven_for_referee_assignments":False,
        "referee_assignment_fixture_binding_complete":False,
        "raw_archived_page_body_read":False,
        "cdx_metadata_only":True,
        "result_labels_read":0,
        "score_values_read":0,
        "match_result_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "referee_assignment_body_parsed":False,
        "sky_article_body_reacquired":False,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "referee_oof_allowed":False,
        "next_step":next_step,
    }
    (out/"wayback_final_recovery_receipt.json").write_bytes(stable_bytes(receipt))
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--aia-ledger",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.aia_ledger,x.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
