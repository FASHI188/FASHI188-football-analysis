#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_combined_freeze_v1 import round_title_identity, sha256_bytes
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc

class BingNewsFeasibilityError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise BingNewsFeasibilityError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def parse_rfc822(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        x=email.utils.parsedate_to_datetime(v)
    except Exception:
        return None
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str | None) -> str:
    if not isinstance(url,str):
        return ""
    return (urllib.parse.urlparse(url).hostname or "").lower()

def host_allowed(h: str, allowed: list[str]) -> bool:
    h=h.lower()
    return any(h==a.lower() or h.endswith("."+a.lower()) for a in allowed)

def build_query_url(endpoint: str, title: str, params: dict[str,str]) -> str:
    q=f'"{title}" site:sport.sky.it'
    qs=[("q",q)]+[(str(k),str(v)) for k,v in params.items()]
    return endpoint+"?"+urllib.parse.urlencode(qs)

def fetch_rss(url: str, source: dict[str,Any]) -> tuple[bytes,str,dict[str,str]]:
    req(host_allowed(host(url),[str(x) for x in source["allowed_feed_hosts"]]),"FEED_REQUEST_HOST")
    rq=urllib.request.Request(
        url,
        headers={
            "User-Agent":source["user_agent"],
            "Accept":"application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(rq,timeout=int(source["request_timeout_seconds"]),context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(host_allowed(host(final),[str(x) for x in source["allowed_feed_hosts"]]),"FEED_REDIRECT_HOST")
        raw=r.read(int(source["max_feed_bytes"])+1)
        req(len(raw)<=int(source["max_feed_bytes"]),"FEED_TOO_LARGE")
        return raw,final,{k.lower():v for k,v in r.headers.items()}

def localname(tag: str) -> str:
    return tag.rsplit("}",1)[-1].rsplit(":",1)[-1]

def parse_feed(raw: bytes, max_items: int) -> list[dict[str,Any]]:
    root=ET.fromstring(raw)
    out=[]
    for item in root.iter():
        if localname(item.tag)!="item":
            continue
        fields={}
        for child in list(item):
            name=localname(child.tag)
            if name in {"title","link","guid","pubDate","Source","source"}:
                fields.setdefault(name,(child.text or "").strip())
        out.append({
            "title":fields.get("title",""),
            "link":fields.get("link",""),
            "guid":fields.get("guid",""),
            "pubDate":fields.get("pubDate",""),
            "source_text":fields.get("Source") or fields.get("source") or "",
        })
        if len(out)>=max_items:
            break
    return out

def decode_repeated(v: str, rounds: int=3) -> str:
    x=v
    for _ in range(rounds):
        y=urllib.parse.unquote(x)
        if y==x:
            break
        x=y
    return x

def extract_publisher_url(link: str, contract: dict[str,Any]) -> tuple[str | None,str]:
    h=host(link)
    if host_allowed(h,[str(x) for x in contract["allowed_final_hosts"]]):
        return link,"DIRECT"
    if not host_allowed(h,[str(x) for x in contract["allowed_intermediate_hosts"]]):
        return None,"UNSUPPORTED_LINK_HOST"
    parsed=urllib.parse.urlparse(link)
    qs=urllib.parse.parse_qs(parsed.query,keep_blank_values=False)
    for key in contract["apiclick_embedded_url_query_keys"]:
        vals=qs.get(key) or []
        for raw in vals:
            candidate=decode_repeated(raw)
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate,f"QUERY_PARAM:{key}"
    return None,"NO_EMBEDDED_URL"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req_obj, fp, code, msg, headers, newurl):
        return None

def head_redirect_identity(
    url: str,
    target_sky_url: str,
    contract: dict[str,Any],
    user_agent: str,
    timeout: int,
) -> dict[str,Any]:
    chain=[]
    current=url
    opener=urllib.request.build_opener(NoRedirect)
    for _ in range(int(contract["head_max_hops"])):
        h=host(current)
        if not (
            host_allowed(h,[str(x) for x in contract["allowed_intermediate_hosts"]])
            or host_allowed(h,[str(x) for x in contract["allowed_final_hosts"]])
        ):
            return {"status":"OUTSIDE_ALLOWED_HOST","final_url":current,"chain":chain,"identity_pass":False}
        rq=urllib.request.Request(current,method="HEAD",headers={"User-Agent":user_agent,"Accept":"*/*"})
        status=None
        location=None
        try:
            with opener.open(rq,timeout=timeout) as r:
                status=int(getattr(r,"status",200))
                location=r.headers.get("Location")
        except urllib.error.HTTPError as exc:
            status=int(exc.code)
            location=exc.headers.get("Location")
        except Exception as exc:
            return {
                "status":"HEAD_ERROR","final_url":current,"chain":chain,"identity_pass":False,
                "error":f"{type(exc).__name__}:{exc}"[:500],
            }
        chain.append({"url":current,"status":status,"location":location})
        if location and status in {301,302,303,307,308}:
            nxt=urllib.parse.urljoin(current,location)
            current=nxt
            continue
        break
    passed=(
        host_allowed(host(current),[str(x) for x in contract["allowed_final_hosts"]])
        and normalize_sky_identity(current)==normalize_sky_identity(target_sky_url)
    )
    return {
        "status":"IDENTITY_PASS" if passed else "IDENTITY_UNRESOLVED_NO_BODY",
        "final_url":current,"chain":chain,"identity_pass":passed,
    }

def candidate_time_title(
    item: dict[str,Any], rnd: int, lower: dt.datetime, upper: dt.datetime
) -> tuple[bool,dict[str,Any]]:
    published=parse_rfc822(item.get("pubDate"))
    title_ok=round_title_identity(item.get("title",""),rnd)
    time_ok=published is not None and lower<=published<upper
    return bool(title_ok and time_ok),{
        "title_ok":title_ok,
        "pubdate_ok":time_ok,
        "pubdate_utc":published.isoformat().replace("+00:00","Z") if published else None,
    }

def classify(identity_pass_n: int, feed_error_n: int, candidate_n: int, p: dict[str,Any]) -> tuple[str,str]:
    if identity_pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if feed_error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    if candidate_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_metadata_candidates_but_identity_unresolved"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="cb858ada17e22eaab415941ec4080028f88e41db","EXACT_BASE")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["bing_article_body_read"] is False and h["sky_article_body_read"] is False,"NO_BODY")
    req(h["rss_description_html_used_for_identity"] is False,"NO_DESCRIPTION")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(p["decision_contract"]["rss_pubdate_counts_as_formal_observed_at"] is False,"NO_PUBDATE_AS_OBSERVED_AT")

    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}
    samples=[int(x["round"]) for x in p["samples"]]
    req(samples==[8,24,34],"FROZEN_SAMPLES")

    src=p["source"]
    ic=p["identity_contract"]
    reports=[]
    feed_errors=[]
    candidate_n=0
    identity_pass_n=0
    identity_pass_rounds=[]

    for rnd in samples:
        req(rnd in pit_rows and rnd in sky_rows,f"PARENT_ROW:R{rnd}")
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        title=srow.get("page_title")
        target=srow.get("sky_url")
        req(isinstance(title,str) and round_title_identity(title,rnd),f"TITLE:R{rnd}")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        q=build_query_url(src["endpoint"],title,src["query_params"])
        lower=parse_z(prow["sky_visible_published_utc"])-dt.timedelta(seconds=300)
        upper=parse_z(prow["first_fixture_cutoff_utc"])
        report={
            "round":rnd,"frozen_title":title,"frozen_sky_url":target,
            "sky_visible_published_utc":prow["sky_visible_published_utc"],
            "first_fixture_cutoff_utc":prow["first_fixture_cutoff_utc"],
            "query_url":q,"feed_error":None,"feed_item_n":0,"metadata_candidate_n":0,
            "identity_pass_n":0,"candidates":[],
        }
        try:
            raw,final,headers=fetch_rss(q,src)
            items=parse_feed(raw,int(src["max_items_per_feed"]))
            report.update({
                "feed_final_url":final,"feed_response_sha256":sha256_bytes(raw),
                "feed_response_bytes":len(raw),"feed_content_type":headers.get("content-type"),
                "feed_item_n":len(items),
            })
            for item in items:
                ok,checks=candidate_time_title(item,rnd,lower,upper)
                if not ok:
                    continue
                candidate_n+=1
                report["metadata_candidate_n"]+=1
                extracted,method=extract_publisher_url(item["link"],ic)
                identity=None
                if extracted is not None:
                    passed=(
                        host_allowed(host(extracted),[str(x) for x in ic["allowed_final_hosts"]])
                        and normalize_sky_identity(extracted)==normalize_sky_identity(target)
                    )
                    identity={
                        "status":"IDENTITY_PASS" if passed else "EMBEDDED_URL_MISMATCH",
                        "method":method,"publisher_url":extracted,"identity_pass":passed,
                    }
                elif ic["head_only_fallback"]:
                    identity=head_redirect_identity(
                        item["link"],target,ic,src["user_agent"],int(src["request_timeout_seconds"])
                    )
                    identity["method"]="HEAD_ONLY_FALLBACK"
                else:
                    identity={"status":"IDENTITY_UNRESOLVED_NO_BODY","identity_pass":False,"method":method}
                report["candidates"].append({
                    "title":item["title"],"link":item["link"],"guid":item["guid"],
                    "pubDate":item["pubDate"],"source_text":item["source_text"],
                    "checks":checks,"identity":identity,
                })
                if identity["identity_pass"]:
                    identity_pass_n+=1
                    report["identity_pass_n"]+=1
                    identity_pass_rounds.append(rnd)
        except Exception as exc:
            err=f"{type(exc).__name__}:{exc}"[:800]
            report["feed_error"]=err
            feed_errors.append({"round":rnd,"query_url":q,"error":err})
        reports.append(report)

    identity_pass_rounds=sorted(set(identity_pass_rounds))
    classification,next_step=classify(identity_pass_n,len(feed_errors),candidate_n,p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-bingnews-feasibility-matrix-v1",
        "sample_rounds":samples,"sample_n":len(samples),
        "feed_error_n":len(feed_errors),"metadata_candidate_n":candidate_n,
        "identity_pass_n":identity_pass_n,"identity_pass_rounds":identity_pass_rounds,
        "reports":reports,"rss_description_html_used":False,
        "bing_article_body_read":False,"sky_article_body_read":False,
        "rss_pubdate_is_formal_observed_at":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_bingnews_feasibility_matrix.json").write_bytes(matrix_bytes)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-bingnews-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_BINGNEWS_FEASIBILITY_COMPLETE",
        "classification":classification,"exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),"parent_provenance":parent_prov,
        "sample_n":len(samples),"sample_rounds":samples,"feed_error_n":len(feed_errors),
        "feed_errors":feed_errors,"metadata_candidate_n":candidate_n,
        "identity_pass_n":identity_pass_n,"identity_pass_rounds":identity_pass_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),"rss_description_html_used":False,
        "bing_article_body_read":False,"sky_article_body_read":False,
        "head_only_fallback_allowed":True,"rss_pubdate_is_independent_publication_metadata_only":True,
        "rss_pubdate_is_formal_observed_at":False,"referee_assignment_body_parsed":False,
        "match_result_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"formal_available_at_proven":False,
        "fixture_level_binding_complete":False,"referee_oof_allowed":False,"next_step":next_step,
    }
    (out/"sky_bingnews_feasibility_receipt.json").write_text(
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
