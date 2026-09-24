#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from nova_n10_referee_aia_gdelt_gkg_daily_v1 import (
    build_daily_url,
    fetch,
    sha256_bytes,
    urls_from_line,
)
from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc

class SkyGDELTDailyError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyGDELTDailyError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def frozen_dates(pub_utc: str, cutoff_utc: str, max_days: int) -> list[str]:
    pub=parse_z(pub_utc)
    cutoff=parse_z(cutoff_utc)
    req(pub < cutoff,"PUB_NOT_PRE_CUTOFF")
    d=pub.date()
    end=cutoff.date()
    out=[]
    while d<=end:
        out.append(d.isoformat())
        d+=dt.timedelta(days=1)
    req(1<=len(out)<=max_days,f"WINDOW_DAY_N:{len(out)}")
    return out

def scan_zip_sky(
    zip_bytes: bytes,
    sky_url: str,
    expected_suffix: str,
) -> dict[str,Any]:
    target=normalize_sky_identity(sky_url)
    req(target is not None,"TARGET_IDENTITY")
    matches=[]
    member_reports=[]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names=zf.namelist()
        req(bool(names),"EMPTY_ZIP")
        members=[n for n in names if n.endswith(expected_suffix)]
        req(bool(members),"GKG_MEMBER_MISSING")
        for name in members:
            line_n=0
            byte_n=0
            matching_line_n=0
            with zf.open(name,"r") as fh:
                for line in fh:
                    line_n+=1
                    byte_n+=len(line)
                    matched=[]
                    for url in urls_from_line(line):
                        if normalize_sky_identity(url)==target:
                            matched.append(url)
                    if matched:
                        matching_line_n+=1
                        matches.append({
                            "member":name,
                            "line_sha256":sha256_bytes(line),
                            "matched_urls":sorted(set(matched)),
                        })
            member_reports.append({
                "member":name,
                "line_n":line_n,
                "decompressed_bytes_scanned":byte_n,
                "matching_line_n":matching_line_n,
            })
    return {
        "member_reports":member_reports,
        "match_n":len(matches),
        "matches":matches,
    }

def audit_round(
    rnd: int,
    pit_row: dict[str,Any],
    sky_url: str,
    registry: dict[str,Any],
) -> dict[str,Any]:
    src=registry["source"]
    dates=frozen_dates(
        pit_row["sky_visible_published_utc"],
        pit_row["first_fixture_cutoff_utc"],
        int(registry["window_contract"]["max_days_per_sample"]),
    )
    reports=[]
    errors=[]
    positive_days=[]
    for day in dates:
        url=build_daily_url(src["daily_url_template"],day)
        try:
            raw,final,headers=fetch(
                url,
                timeout=int(src["request_timeout_seconds"]),
                limit=int(src["max_zip_bytes"]),
                allowed_host=src["allowed_host"],
            )
            scan=scan_zip_sky(raw,sky_url,src["expected_zip_member_suffix"])
            reports.append({
                "date":day,
                "source_url":url,
                "final_url":final,
                "zip_sha256":sha256_bytes(raw),
                "zip_bytes":len(raw),
                "content_type":headers.get("content-type"),
                **scan,
            })
            if scan["match_n"]>0:
                positive_days.append(day)
        except Exception as exc:
            errors.append({
                "date":day,
                "source_url":url,
                "error":f"{type(exc).__name__}:{exc}"[:800],
            })
    return {
        "round":rnd,
        "sky_url":sky_url,
        "sky_visible_published_utc":pit_row["sky_visible_published_utc"],
        "first_fixture_cutoff_utc":pit_row["first_fixture_cutoff_utc"],
        "frozen_dates":dates,
        "frozen_day_n":len(dates),
        "successful_day_n":len(reports),
        "error_n":len(errors),
        "reports":reports,
        "errors":errors,
        "positive_day_n":len(positive_days),
        "positive_days":positive_days,
        "all_days_fetched":len(reports)==len(dates),
    }

def classify(
    audits: list[dict[str,Any]],
    registry: dict[str,Any],
) -> tuple[str,str]:
    d=registry["decision_contract"]
    positive=[x["round"] for x in audits if x["positive_day_n"]>0]
    errors=sum(x["error_n"] for x in audits)
    if positive:
        return d["positive_classification"],registry["reasonable_subroutes"]["if_positive"]
    if errors:
        return d["external_block_classification"],registry["reasonable_subroutes"]["if_external_error"]
    return d["zero_classification"],registry["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="348bfe7f2fffea8e580736f9594d7de7dc7a4e40","EXACT_BASE")
    h=p["hard_rules"]
    req(h["prior_source_requery_allowed"] is False,"NO_PRIOR_SOURCE_REQUERY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["article_body_read"] is False and h["referee_assignment_body_parsed"] is False,"NO_BODY")
    req(h["full_gkg_row_persisted"] is False,"NO_FULL_ROW")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}
    sample_rounds=[int(x["round"]) for x in p["samples"]]
    req(sample_rounds==[9,24,38],"FROZEN_SAMPLES")

    audits=[]
    for rnd in sample_rounds:
        req(rnd in pit_rows and rnd in sky_rows,f"PARENT_ROW:R{rnd}")
        req(pit_rows[rnd]["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        sky_url=sky_rows[rnd].get("sky_url")
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        audits.append(audit_round(rnd,pit_rows[rnd],sky_url,p))

    positive_rounds=sorted(x["round"] for x in audits if x["positive_day_n"]>0)
    total_days=sum(x["frozen_day_n"] for x in audits)
    success_days=sum(x["successful_day_n"] for x in audits)
    error_n=sum(x["error_n"] for x in audits)
    classification,next_step=classify(audits,p)

    matrix={
        "schema_version":"football3-nova-n10-referee-sky-gdelt-daily-feasibility-matrix-v1",
        "sample_rounds":sample_rounds,
        "sample_n":len(sample_rounds),
        "total_frozen_day_n":total_days,
        "successful_day_n":success_days,
        "error_n":error_n,
        "positive_rounds":positive_rounds,
        "positive_round_n":len(positive_rounds),
        "audits":audits,
        "day_level_observation_only":True,
        "exact_observation_time_proven":False,
        "full_gkg_rows_persisted":False,
        "matching_full_lines_persisted":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_gdelt_daily_feasibility_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-gdelt-daily-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_GDELT_DAILY_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "sample_rounds":sample_rounds,
        "sample_n":len(sample_rounds),
        "total_frozen_day_n":total_days,
        "successful_day_n":success_days,
        "error_n":error_n,
        "positive_rounds":positive_rounds,
        "positive_round_n":len(positive_rounds),
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "prior_source_requery_performed":False,
        "full_gkg_rows_persisted":False,
        "matching_full_lines_persisted":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
        "match_result_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "result_labels_read":0,
        "score_values_read":0,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "day_level_observation_only":True,
        "exact_observation_time_proven":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "referee_oof_allowed":False,
        "next_step":next_step,
    }
    (out/"sky_gdelt_daily_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
