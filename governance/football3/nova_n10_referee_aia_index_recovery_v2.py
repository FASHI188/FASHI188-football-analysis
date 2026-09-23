#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

class AIAIndexRecoveryError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise AIAIndexRecoveryError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def fold_text(s: str) -> str:
    x=unicodedata.normalize("NFKD",s or "")
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=re.sub(r"\s+"," ",x)
    return x.casefold().strip()

def fetch(url: str, timeout: int, limit: int) -> tuple[bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIAIndexRecovery/2.0",
        "Accept":"text/html,application/xhtml+xml",
        "Accept-Language":"it-IT,it;q=0.9,en;q=0.7",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        data=r.read(limit+1)
        req(len(data)<=limit,"RESPONSE_TOO_LARGE")
        return data,r.geturl(),{k.lower():v for k,v in r.headers.items()}

def metadata_identity(raw: bytes, target: dict[str,Any]) -> dict[str,Any]:
    text=raw.decode("utf-8","replace")
    folded=fold_text(text)
    title_ok=fold_text(target["expected_title"]) in folded
    date_ok=target["expected_date"] in text
    slug_ok=target["expected_slug"].casefold() in text.casefold()
    return {
        "title_found":title_ok,
        "date_found":date_ok,
        "slug_found":slug_ok,
        "identity_pass":title_ok and date_ok and slug_ok,
    }

def run(registry: Path, out: Path, timeout: int=25) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="bcf22301e3b0700e3724a86cccd924e9f5b498cb","EXACT_BASE")

    trigger=p["recovery_trigger"]
    req(trigger["prior_pr"]==452 and trigger["prior_run"]==35818082497,"PRIOR_EVIDENCE")
    req(trigger["new_availability_pr"]==459 and trigger["new_availability_run"]==35828281347,"NEW_AVAILABILITY_EVIDENCE")
    req(trigger["max_recovery_attempts"]==1,"BOUNDED_RECOVERY")

    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["article_body_fetch_allowed"] is False,"NO_ARTICLE_BODY")
    req(h["official_domain_required"] is True,"OFFICIAL_DOMAIN")
    req(h["additional_index_variants_allowed"] is False,"NO_VARIANT_EXPANSION")
    req(h["rerun_after_this_recovery_if_fail"] is False,"NO_REPEAT_ON_FAIL")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    cfg=p["official_index"]
    target=p["target"]
    expected_variants=[
        "https://www.aia-figc.it/news/?c=9&p=10",
        "https://www.aia-figc.it/news/?0=&c=9&p=10",
        "https://www.aia-figc.it/news/?0=&1=&2=&4=&c=9&p=10",
    ]
    req(cfg["page_variants"]==expected_variants,"FROZEN_VARIANTS_CHANGED")
    req(cfg["frozen_variant_count"]==3 and len(cfg["page_variants"])==3,"VARIANT_COUNT")
    req(cfg["page_expansion_allowed"] is False,"PAGE_EXPANSION_FORBIDDEN")

    reports=[]
    errors=[]
    for url in cfg["page_variants"]:
        try:
            raw,final,headers=fetch(url,timeout,int(cfg["max_bytes"]))
            req(domain_ok(final,cfg["domain_suffix"]),"REDIRECT_OUTSIDE_OFFICIAL_DOMAIN")
            identity=metadata_identity(raw,target)
            reports.append({
                "source_url":url,
                "final_url":final,
                "content_sha256":sha256_bytes(raw),
                "content_bytes":len(raw),
                "content_type":headers.get("content-type"),
                **identity,
            })
        except Exception as e:
            errors.append({"source_url":url,"error":f"{type(e).__name__}:{e}"[:400]})

    passed=[r for r in reports if r["identity_pass"]]
    classification="DATA_COVERAGE_FEASIBILITY_PASS" if passed else "STOP_DATA_COVERAGE"
    reason="OFFICIAL_INDEX_TARGET_METADATA_FOUND" if passed else "OFFICIAL_INDEX_TARGET_METADATA_NOT_OBTAINED"

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-index-recovery-receipt-v2",
        "status":"N10_REFEREE_AIA_INDEX_RECOVERY_COMPLETE",
        "classification":classification,
        "reason":reason,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "recovery_attempt_n":1,
        "recovery_trigger_prior_run":trigger["prior_run"],
        "recovery_trigger_new_availability_run":trigger["new_availability_run"],
        "page_variant_n":len(cfg["page_variants"]),
        "successful_fetch_n":len(reports),
        "identity_pass_n":len(passed),
        "reports":reports,
        "errors":errors,
        "article_body_fetched":False,
        "page_expansion_performed":False,
        "match_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
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
            "IF_PASS_BUILD_TARGET_SEASON_AIA_DESIGNAZIONI_INDEX_INVENTORY_WITH_SAME_METADATA_ONLY_CONTRACT"
            if passed else
            "CLOSE_AIA_INDEX_ROUTE_AGAIN; DO_NOT_REPEAT; CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        ),
    }
    (out/"aia_index_recovery_receipt.json").write_text(
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
