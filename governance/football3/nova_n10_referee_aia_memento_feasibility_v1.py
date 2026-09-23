#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

class MementoFeasibilityError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise MementoFeasibilityError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_iso_date(v: str) -> dt.datetime:
    return dt.datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)

def parse_memento_datetime(v: str) -> dt.datetime | None:
    s=(v or "").strip()
    if not s:
        return None
    try:
        x=dt.datetime.fromisoformat(s.replace("Z","+00:00"))
        if x.tzinfo is None:
            x=x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(dt.timezone.utc)
    except Exception:
        pass
    try:
        x=email.utils.parsedate_to_datetime(s)
        if x.tzinfo is None:
            x=x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(dt.timezone.utc)
    except Exception:
        return None

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def host_is(url: str, expected: str) -> bool:
    return host(url)==expected.lower()

def excluded_provider(provider_host: str, suffixes: list[str]) -> bool:
    h=provider_host.lower()
    return any(h==s.lower() or h.endswith("."+s.lower()) for s in suffixes)

def timemap_url(template: str, original_url: str) -> str:
    encoded=urllib.parse.quote(original_url, safe=":/?=&%")
    return template.replace("{original_url}",encoded)

def fetch_json(url: str, timeout: int=25, limit: int=4_000_000) -> tuple[bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-MementoFeasibility/1.0",
        "Accept":"application/json,text/json;q=0.9,*/*;q=0.1",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        data=r.read(limit+1)
        req(len(data)<=limit,"RESPONSE_TOO_LARGE")
        return data,r.geturl(),{k.lower():v for k,v in r.headers.items()}

def uri_values(v: Any) -> list[str]:
    if isinstance(v,str):
        return [v]
    if isinstance(v,list):
        return [x for x in v if isinstance(x,str)]
    return []

def walk_memento_records(obj: Any) -> Iterable[dict[str,str]]:
    if isinstance(obj,dict):
        if "datetime" in obj and "uri" in obj:
            d=obj.get("datetime")
            for u in uri_values(obj.get("uri")):
                if isinstance(d,str):
                    yield {"datetime":d,"uri":u}
        for v in obj.values():
            yield from walk_memento_records(v)
    elif isinstance(obj,list):
        for v in obj:
            yield from walk_memento_records(v)

def parse_timemap(raw: bytes) -> list[dict[str,str]]:
    x=json.loads(raw.decode("utf-8"))
    out=[]
    seen=set()
    for r in walk_memento_records(x):
        key=(r["datetime"],r["uri"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out

def eligible_records(records: list[dict[str,str]], sample: dict[str,Any], excluded_suffixes: list[str]) -> list[dict[str,Any]]:
    start=parse_iso_date(sample["published_date"])
    end=start+dt.timedelta(days=8)
    out=[]
    for r in records:
        when=parse_memento_datetime(r.get("datetime",""))
        if when is None or not (start <= when < end):
            continue
        ph=host(r.get("uri",""))
        if not ph:
            continue
        if excluded_provider(ph,excluded_suffixes):
            continue
        out.append({
            "datetime":when.isoformat(),
            "uri":r["uri"],
            "provider_host":ph,
        })
    return sorted(out,key=lambda x:(x["datetime"],x["provider_host"],x["uri"]))

def run(registry: Path, out: Path, timeout: int=25) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="8e0561b74ca960b5828b6c43b84f06043599eac0","EXACT_BASE")
    req(p["parent"]["parent_inventory_sha256"]=="1710d04197405eb718b114ae43feba8de85ddfb32c527f712480764eaf246cbe","PARENT_INVENTORY_SHA")
    req(p["parent"]["canonical_ledger_sha256"]=="0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b","LEDGER_SHA")

    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False and h["archived_page_content_fetched"] is False,"NO_CONTENT")
    req(h["appointment_names_parsed"] is False,"NO_APPOINTMENT_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["provider_reuse_wayback_or_arquivo_as_new_signal"] is False,"NO_PROVIDER_REUSE")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(len(p["samples"])==5,"FROZEN_SAMPLE_N")

    source=p["source"]
    wc=p["witness_contract"]
    excluded=wc["excluded_provider_host_suffixes"]
    reports=[]
    errors=[]
    positive_samples=[]

    for sample in p["samples"]:
        q=timemap_url(source["timemap_json_template"],sample["url"])
        try:
            raw,final,headers=fetch_json(q,timeout)
            req(host_is(final,source["allowed_aggregator_host"]),"REDIRECT_OUTSIDE_AGGREGATOR")
            records=parse_timemap(raw)
            eligible=eligible_records(records,sample,excluded)
            report={
                "round":sample["round"],
                "published_date":sample["published_date"],
                "official_url":sample["url"],
                "timemap_url":q,
                "final_url":final,
                "response_sha256":sha256_bytes(raw),
                "response_bytes":len(raw),
                "content_type":headers.get("content-type"),
                "memento_record_n":len(records),
                "eligible_non_wayback_non_arquivo_n":len(eligible),
                "eligible_records":eligible,
            }
            reports.append(report)
            if eligible:
                positive_samples.append(sample["round"])
        except Exception as e:
            errors.append({
                "round":sample["round"],
                "official_url":sample["url"],
                "timemap_url":q,
                "error":f"{type(e).__name__}:{e}"[:500],
            })

    positive=bool(positive_samples)
    classification=(
        p["decision_contract"]["positive_classification"]
        if positive else p["decision_contract"]["fail_classification"]
    )

    provider_counts={}
    for r in reports:
        for x in r["eligible_records"]:
            provider_counts[x["provider_host"]]=provider_counts.get(x["provider_host"],0)+1

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-memento-feasibility-receipt-v1",
        "status":"N10_REFEREE_AIA_MEMENTO_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "sample_n":len(p["samples"]),
        "successful_timemap_n":len(reports),
        "error_n":len(errors),
        "reports":reports,
        "errors":errors,
        "positive_sample_rounds":sorted(positive_samples),
        "positive_sample_n":len(positive_samples),
        "provider_counts":dict(sorted(provider_counts.items())),
        "wayback_or_arquivo_counted_as_new_signal":False,
        "archive_content_fetched":False,
        "article_body_read":False,
        "appointment_names_parsed":False,
        "match_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "result_labels_read":0,
        "score_values_read":0,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "next_step":(
            "IF_POSITIVE_EXPAND_MEMENTO_AGGREGATOR_TO_ALL_38_CANONICAL_AIA_ROUNDS_METADATA_ONLY"
            if positive else
            "STOP_MEMENTO_AGGREGATOR_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_ARCHIVE_SOURCE"
        ),
    }
    (out/"aia_memento_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=25)
    x=a.parse_args()
    run(x.registry,x.out,x.timeout)

if __name__=="__main__":
    main()
