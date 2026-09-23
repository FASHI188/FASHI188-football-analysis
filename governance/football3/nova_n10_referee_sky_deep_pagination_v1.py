#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_season_inventory_v1 import (
    archive_url,
    candidate_links,
    candidate_preference,
    fetch_bytes,
    fetch_candidate_header,
    parse_archive,
    same_archive_day_page,
    sha256_bytes,
)

class SkyDeepPaginationError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyDeepPaginationError(msg)

def fetch_archive_day_full(day: dt.date, source: dict[str,Any]) -> dict[str,Any]:
    timeout=int(source["request_timeout_seconds"])
    limit=int(source["archive_page_max_bytes"])
    cap=int(source["max_archive_pages_per_day"])
    suffix=source["domain_suffix"]
    first_url=archive_url(source["archive_url_template"],day,1)
    raw,final,headers=fetch_bytes(
        first_url,timeout=timeout,limit=limit,suffix=suffix,
        accept="text/html,application/xhtml+xml",
    )
    parsed=parse_archive(raw,final)
    advertised={1}
    for a in parsed.anchors:
        n=same_archive_day_page(a["href"],day)
        if n is not None:
            advertised.add(n)
    max_advertised=max(advertised)
    truncated=max_advertised>cap
    max_fetch=min(max_advertised,cap)

    page_reports=[{
        "page":1,
        "url":first_url,
        "final_url":final,
        "bytes":len(raw),
        "sha256":sha256_bytes(raw),
        "content_type":headers.get("content-type"),
        "anchors":parsed.anchors,
    }]
    for page in range(2,max_fetch+1):
        u=archive_url(source["archive_url_template"],day,page)
        raw2,final2,headers2=fetch_bytes(
            u,timeout=timeout,limit=limit,suffix=suffix,
            accept="text/html,application/xhtml+xml",
        )
        parsed2=parse_archive(raw2,final2)
        page_reports.append({
            "page":page,
            "url":u,
            "final_url":final2,
            "bytes":len(raw2),
            "sha256":sha256_bytes(raw2),
            "content_type":headers2.get("content-type"),
            "anchors":parsed2.anchors,
        })
    return {
        "date":day.isoformat(),
        "advertised_pages":sorted(advertised),
        "max_advertised_page":max_advertised,
        "page_cap":cap,
        "pagination_truncated":truncated,
        "page_n":len(page_reports),
        "pages":page_reports,
    }

def run(registry: Path, ledger: Path, out: Path) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_GAP_RECOVERY","STATUS")
    req(p["exact_base"]=="9d5c164230d250a446e882d0c40bb7198b5dafb1","EXACT_BASE")
    parent=p["parent"]
    gap_rounds=[int(x) for x in parent["gap_rounds"]]
    covered_parent=[int(x) for x in parent["covered_rounds"]]
    req(len(gap_rounds)==26 and len(covered_parent)==12,"PARENT_COVERAGE_COUNTS")
    req(sorted(set(gap_rounds+covered_parent))==list(range(1,39)),"PARENT_PARTITION")

    source=p["source"]
    pagination=p["pagination_contract"]
    discovery=p["discovery_contract"]
    header=p["header_contract"]
    hard=p["hard_rules"]

    req(int(source["max_archive_pages_per_day"])==20,"PAGE_CAP")
    req(int(pagination["cap"])==20,"PAGINATION_CAP")
    req(pagination["fetch_all_integer_pages_1_through_max_advertised"] is True,"FULL_RANGE")
    req(pagination["max_advertised_page_from_same_day_links_only"] is True,"MECHANICAL_MAX")
    req(pagination["stop_and_mark_truncated_if_max_advertised_exceeds_cap"] is True,"TRUNCATION_RULE")
    req(discovery["only_parent_gap_rounds"] is True,"GAP_ONLY")
    req(discovery["day_offsets"]==[0,1,2],"FROZEN_OFFSETS")
    req(discovery["no_guessed_article_urls"] is True,"NO_GUESSED_URLS")
    req(header["stop_immediately_at_first_valid_timestamp_after_title"] is True,"STOP_AT_TIMESTAMP")
    req(hard["repeat_parent_covered_rounds"] is False,"NO_REPEAT_COVERED")
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["match_payload_read"] is False and hard["standings_payload_read"] is False and hard["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(hard["article_body_read"] is False and hard["summary_text_read"] is False,"NO_ARTICLE_BODY")
    req(hard["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    l=json.loads(ledger.read_text(encoding="utf-8"))
    req(l["schema_version"]=="football3-nova-n10-aia-canonical-round-ledger-v1","LEDGER_SCHEMA")
    req(l["competition"]=="Serie_A" and l["season"]=="2022/23","LEDGER_IDENTITY")
    req(l["canonical_round_n"]==38 and len(l["rows"])==38,"LEDGER_COVERAGE")
    rows={int(x["round"]):x for x in l["rows"]}
    req(sorted(rows)==list(range(1,39)),"LEDGER_ROUNDS")

    target_days: dict[int,list[dt.date]]={}
    unique_days=set()
    for rnd in gap_rounds:
        row=rows[rnd]
        base=dt.date.fromisoformat(row["published_date"])
        days=[base+dt.timedelta(days=int(o)) for o in discovery["day_offsets"]]
        target_days[rnd]=days
        unique_days.update(days)

    day_reports={}
    day_errors={}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(fetch_archive_day_full,d,source):d for d in sorted(unique_days)}
        for fut in as_completed(futs):
            d=futs[fut]
            try:
                day_reports[d.isoformat()]=fut.result()
            except Exception as exc:
                day_errors[d.isoformat()]=f"{type(exc).__name__}:{exc}"[:500]

    truncated_days=sorted(
        day for day,rep in day_reports.items()
        if rep["pagination_truncated"]
    )

    round_reports=[]
    recovered=[]
    conflict_rounds=[]
    for rnd in gap_rounds:
        row=rows[rnd]
        discovered=[]
        for d in target_days[rnd]:
            rep=day_reports.get(d.isoformat())
            if rep is not None:
                discovered.extend(candidate_links(rep,row,discovery,source))
        uniq={}
        for c in discovered:
            uniq[c["normalized_url"]]=c
        discovered=sorted(uniq.values(),key=lambda x:(x["archive_date"],x["archive_page"],x["normalized_url"]))

        validated=[]
        validation_errors=[]
        for c in discovered:
            try:
                validated.append(fetch_candidate_header(c,row,source,discovery))
            except Exception as exc:
                validation_errors.append({"url":c["url"],"error":f"{type(exc).__name__}:{exc}"[:500]})
        by_final={}
        for c in validated:
            by_final[c["final_normalized_url"]]=c
        validated=sorted(by_final.values(),key=candidate_preference)
        canonical=validated[0] if validated else None
        if canonical is not None:
            recovered.append(rnd)
        if len(validated)>1:
            conflict_rounds.append(rnd)

        round_reports.append({
            "round":rnd,
            "aia_published_date":row["published_date"],
            "archive_days":[d.isoformat() for d in target_days[rnd]],
            "archive_day_success_n":sum(1 for d in target_days[rnd] if d.isoformat() in day_reports),
            "archive_day_error_n":sum(1 for d in target_days[rnd] if d.isoformat() in day_errors),
            "pagination_truncated_day_n":sum(1 for d in target_days[rnd] if d.isoformat() in truncated_days),
            "discovered_candidate_n":len(discovered),
            "valid_candidate_n":len(validated),
            "valid_candidates":validated,
            "validation_errors":validation_errors,
            "canonical_candidate":canonical,
            "status":"RECOVERED" if canonical else "REMAINING_GAP",
            "conflict":len(validated)>1,
        })

    recovered=sorted(recovered)
    combined=sorted(set(covered_parent+recovered))
    remaining=sorted(set(range(1,39))-set(combined))
    external_block=bool(day_errors or truncated_days)

    if not external_block and len(combined)==38:
        classification=p["decision_contract"]["positive_classification"]
        next_step=p["decision_contract"]["next_if_positive"]
    elif external_block:
        classification=p["decision_contract"]["blocked_classification"]
        next_step=p["decision_contract"]["next_if_blocked"]
    else:
        classification=p["decision_contract"]["partial_classification"]
        next_step=p["decision_contract"]["next_if_partial"]

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-deep-pagination-receipt-v1",
        "status":"N10_REFEREE_SKY_DEEP_PAGINATION_GAP_RECOVERY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "ledger_file_sha256":sha256_bytes(ledger.read_bytes()),
        "parent_inventory_sha256":parent["inventory_sha256"],
        "parent_covered_rounds":covered_parent,
        "parent_gap_rounds":gap_rounds,
        "parent_covered_round_n":len(covered_parent),
        "gap_target_round_n":len(gap_rounds),
        "unique_archive_day_n":len(unique_days),
        "archive_day_success_n":len(day_reports),
        "archive_day_error_n":len(day_errors),
        "archive_day_errors":day_errors,
        "pagination_truncated_day_n":len(truncated_days),
        "pagination_truncated_days":truncated_days,
        "page_cap":20,
        "recovered_round_n":len(recovered),
        "recovered_rounds":recovered,
        "conflict_rounds":sorted(conflict_rounds),
        "combined_covered_round_n":len(combined),
        "combined_coverage":len(combined)/38.0,
        "combined_covered_rounds":combined,
        "remaining_gap_rounds":remaining,
        "round_reports":round_reports,
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

    recovery={
        "schema_version":"football3-nova-n10-referee-sky-deep-pagination-recovery-v1",
        "parent_inventory_sha256":parent["inventory_sha256"],
        "parent_covered_rounds":covered_parent,
        "recovered_rounds":recovered,
        "remaining_gap_rounds":remaining,
        "combined_covered_rounds":combined,
        "combined_coverage":len(combined)/38.0,
        "rows":[{
            "round":r["round"],
            "status":r["status"],
            "conflict":r["conflict"],
            "canonical_candidate":r["canonical_candidate"],
            "valid_candidate_n":r["valid_candidate_n"],
        } for r in round_reports],
    }

    out.mkdir(parents=True,exist_ok=True)
    recovery_bytes=(json.dumps(recovery,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    receipt["recovery_file_sha256"]=sha256_bytes(recovery_bytes)
    (out/"sky_deep_pagination_recovery.json").write_bytes(recovery_bytes)
    (out/"sky_deep_pagination_receipt.json").write_text(
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
