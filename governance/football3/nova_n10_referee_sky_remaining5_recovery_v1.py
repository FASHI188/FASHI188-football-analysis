#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_deep_pagination_v1 import fetch_archive_day_full
from nova_n10_referee_sky_season_inventory_v1 import (
    candidate_preference,
    fetch_candidate_header,
    host_ok,
    normalized_url,
    referee_signal,
    round_signal,
    serie_a_signal,
    sha256_bytes,
)

class SkyRemaining5Error(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyRemaining5Error(msg)

def presentation_signal(text: str, url: str, terms: list[str]) -> bool:
    combined=(text+" "+url).casefold()
    return any(t.casefold() in combined for t in terms)

def candidate_links_relaxed(
    day_report: dict[str,Any],
    target: dict[str,Any],
    discovery: dict[str,Any],
    source: dict[str,Any],
) -> list[dict[str,Any]]:
    out=[]
    seen=set()
    for page in day_report["pages"]:
        for a in page["anchors"]:
            u=a["href"]
            if not host_ok(u,source["domain_suffix"]):
                continue
            if "/archivio/" in u.casefold():
                continue
            if not serie_a_signal(a["text"],u):
                continue
            if not round_signal(a["text"],u,int(target["round"])):
                continue
            if not (
                referee_signal(a["text"],u,discovery["referee_terms"])
                or presentation_signal(a["text"],u,discovery["presentation_terms"])
            ):
                continue
            nu=normalized_url(u)
            if nu in seen:
                continue
            seen.add(nu)
            out.append({
                "round":target["round"],
                "archive_date":day_report["date"],
                "archive_page":page["page"],
                "url":u,
                "normalized_url":nu,
                "archive_anchor_text":a["text"][:500],
            })
    return sorted(out,key=lambda x:(x["archive_date"],x["archive_page"],x["normalized_url"]))

def run(registry: Path, ledger: Path, out: Path) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_REMAINING5","STATUS")
    req(p["exact_base"]=="d1b33d74df0331b2f9850629803268b50f3b223d","EXACT_BASE")
    parent=p["parent"]
    targets=[int(x) for x in parent["remaining_gap_rounds"]]
    parent_covered=[int(x) for x in parent["combined_covered_rounds"]]
    req(targets==[6,8,23,27,34],"FROZEN_REMAINING5")
    req(len(parent_covered)==33,"PARENT_COVERAGE")
    req(sorted(set(parent_covered+targets))==list(range(1,39)),"PARENT_PARTITION")

    source=p["source"]
    discovery=p["discovery_contract"]
    header=p["header_contract"]
    hard=p["hard_rules"]

    req(discovery["day_offsets"]==[-1,0,1,2],"FROZEN_OFFSETS")
    req(discovery["only_remaining_gap_rounds"] is True,"REMAINING_ONLY")
    req(discovery["candidate_page_title_still_requires_referee_signal"] is True,"PAGE_REFEREE_IDENTITY")
    req(discovery["no_guessed_article_urls"] is True,"NO_GUESSED_URLS")
    req(discovery["use_full_exposed_pagination"] is True,"FULL_PAGINATION")
    req(int(source["max_archive_pages_per_day"])==20,"PAGE_CAP")
    req(header["referee_signal_in_title_required"] is True,"HEADER_REFEREE_IDENTITY")
    req(header["stop_immediately_at_first_valid_timestamp_after_title"] is True,"STOP_AT_TIMESTAMP")
    req(hard["repeat_parent_covered_rounds"] is False,"NO_REPEAT_COVERED")
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["article_body_read"] is False and hard["summary_text_read"] is False,"NO_BODY")
    req(hard["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    l=json.loads(ledger.read_text(encoding="utf-8"))
    req(l["canonical_round_n"]==38 and len(l["rows"])==38,"LEDGER")
    rows={int(x["round"]):x for x in l["rows"]}

    target_days={}
    unique_days=set()
    for rnd in targets:
        base=dt.date.fromisoformat(rows[rnd]["published_date"])
        ds=[base+dt.timedelta(days=int(o)) for o in discovery["day_offsets"]]
        target_days[rnd]=ds
        unique_days.update(ds)

    day_reports={}
    day_errors={}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(fetch_archive_day_full,d,source):d for d in sorted(unique_days)}
        for fut in as_completed(futs):
            d=futs[fut]
            try:
                day_reports[d.isoformat()]=fut.result()
            except Exception as exc:
                day_errors[d.isoformat()]=f"{type(exc).__name__}:{exc}"[:500]

    truncated_days=sorted(
        d for d,r in day_reports.items() if r["pagination_truncated"]
    )

    round_reports=[]
    recovered=[]
    conflicts=[]
    for rnd in targets:
        row=rows[rnd]
        discovered=[]
        for d in target_days[rnd]:
            rep=day_reports.get(d.isoformat())
            if rep is not None:
                discovered.extend(candidate_links_relaxed(rep,row,discovery,source))
        uniq={}
        for c in discovered:
            uniq[c["normalized_url"]]=c
        discovered=sorted(uniq.values(),key=lambda x:(x["archive_date"],x["archive_page"],x["normalized_url"]))

        valid=[]
        validation_errors=[]
        for c in discovered:
            try:
                valid.append(fetch_candidate_header(c,row,source,discovery))
            except Exception as exc:
                validation_errors.append({"url":c["url"],"error":f"{type(exc).__name__}:{exc}"[:500]})
        by_final={}
        for c in valid:
            by_final[c["final_normalized_url"]]=c
        valid=sorted(by_final.values(),key=candidate_preference)
        canonical=valid[0] if valid else None
        if canonical:
            recovered.append(rnd)
        if len(valid)>1:
            conflicts.append(rnd)
        round_reports.append({
            "round":rnd,
            "aia_published_date":row["published_date"],
            "archive_days":[d.isoformat() for d in target_days[rnd]],
            "archive_day_success_n":sum(1 for d in target_days[rnd] if d.isoformat() in day_reports),
            "archive_day_error_n":sum(1 for d in target_days[rnd] if d.isoformat() in day_errors),
            "pagination_truncated_day_n":sum(1 for d in target_days[rnd] if d.isoformat() in truncated_days),
            "discovered_candidate_n":len(discovered),
            "valid_candidate_n":len(valid),
            "valid_candidates":valid,
            "validation_errors":validation_errors,
            "canonical_candidate":canonical,
            "status":"RECOVERED" if canonical else "REMAINING_GAP",
            "conflict":len(valid)>1,
        })

    recovered=sorted(recovered)
    combined=sorted(set(parent_covered+recovered))
    remaining=sorted(set(range(1,39))-set(combined))
    blocked=bool(day_errors or truncated_days)
    if not blocked and len(combined)==38:
        classification=p["decision_contract"]["positive_classification"]
        next_step=p["decision_contract"]["next_if_positive"]
    elif blocked:
        classification=p["decision_contract"]["blocked_classification"]
        next_step=p["decision_contract"]["next_if_blocked"]
    else:
        classification=p["decision_contract"]["partial_classification"]
        next_step=p["decision_contract"]["next_if_partial"]

    combined_inventory={
        "schema_version":"football3-nova-n10-referee-sky-combined-coverage-pointer-v1",
        "parent_recovery_sha256":parent["recovery_file_sha256"],
        "parent_covered_rounds":parent_covered,
        "recovered_rounds":recovered,
        "combined_covered_rounds":combined,
        "remaining_gap_rounds":remaining,
        "combined_coverage":len(combined)/38.0,
        "remaining5_rows":[{
            "round":r["round"],
            "status":r["status"],
            "conflict":r["conflict"],
            "canonical_candidate":r["canonical_candidate"],
            "valid_candidate_n":r["valid_candidate_n"],
        } for r in round_reports],
    }
    out.mkdir(parents=True,exist_ok=True)
    combined_bytes=(json.dumps(combined_inventory,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode()
    (out/"sky_remaining5_recovery.json").write_bytes(combined_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-remaining5-recovery-receipt-v1",
        "status":"N10_REFEREE_SKY_REMAINING5_RECOVERY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "ledger_file_sha256":sha256_bytes(ledger.read_bytes()),
        "parent_recovery_sha256":parent["recovery_file_sha256"],
        "target_rounds":targets,
        "target_round_n":5,
        "unique_archive_day_n":len(unique_days),
        "archive_day_success_n":len(day_reports),
        "archive_day_error_n":len(day_errors),
        "archive_day_errors":day_errors,
        "pagination_truncated_day_n":len(truncated_days),
        "pagination_truncated_days":truncated_days,
        "page_cap":20,
        "recovered_round_n":len(recovered),
        "recovered_rounds":recovered,
        "conflict_rounds":sorted(conflicts),
        "combined_covered_round_n":len(combined),
        "combined_coverage":len(combined)/38.0,
        "combined_covered_rounds":combined,
        "remaining_gap_rounds":remaining,
        "round_reports":round_reports,
        "recovery_file_sha256":sha256_bytes(combined_bytes),
        "raw_archive_html_persisted":False,
        "raw_article_prefix_persisted":False,
        "summary_text_read":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "result_labels_read":0,
        "score_values_read":0,
        "match_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "next_step":next_step,
    }
    (out/"sky_remaining5_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--ledger",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.ledger,x.out)

if __name__=="__main__":
    main()
