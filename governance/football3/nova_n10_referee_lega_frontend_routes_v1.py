#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, ssl, urllib.parse, urllib.request
from pathlib import Path
from typing import Any

class DiscoveryError(RuntimeError): pass
def req(c: bool, m: str) -> None:
    if not c: raise DiscoveryError(m)
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

HEAD_LIMIT=262144
HEAD_CLOSE=re.compile(br"</head\s*>",re.I)
SCRIPT_SRC_RE=re.compile(r'''<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>''',re.I)
URL_RE=re.compile(r'''https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+''')
PATH_RE=re.compile(r'''["']((?:/|https?://)[^"'\s]{3,240})["']''')

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def fetch_head_html(url: str, timeout: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_ENTRY")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-LegaFrontendRoutes/1.0",
        "Accept":"text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl(); req(domain_ok(final,suffix),"REDIRECT_OUTSIDE_OFFICIAL")
        buf=bytearray()
        while len(buf)<HEAD_LIMIT:
            chunk=r.read(min(8192,HEAD_LIMIT-len(buf)))
            if not chunk: break
            buf.extend(chunk)
            m=HEAD_CLOSE.search(buf)
            if m:
                return bytes(buf[:m.end()]),final,{k.lower():v for k,v in r.headers.items()}
    raise DiscoveryError("HEAD_BOUNDARY_NOT_FOUND")

def script_sources(head: bytes, base_url: str, suffix: str) -> list[str]:
    s=head.decode("utf-8","replace")
    out=[]
    for m in SCRIPT_SRC_RE.finditer(s):
        u=urllib.parse.urljoin(base_url,m.group(1))
        if domain_ok(u,suffix):
            out.append(u)
    return sorted(dict.fromkeys(out))

def fetch_script(url: str, timeout: int, limit: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_SCRIPT")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-LegaFrontendRoutes/1.0",
        "Accept":"application/javascript,text/javascript,*/*;q=0.2",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl(); req(domain_ok(final,suffix),"SCRIPT_REDIRECT_OUTSIDE_OFFICIAL")
        data=r.read(limit+1); req(len(data)<=limit,"SCRIPT_TOO_LARGE")
        return data,final,{k.lower():v for k,v in r.headers.items()}

def extract_routes(raw: bytes, preferred: list[str], forbidden: list[str]) -> list[str]:
    s=raw.decode("utf-8","replace")
    cands=set(URL_RE.findall(s))
    cands.update(m.group(1) for m in PATH_RE.finditer(s))
    out=[]
    for x in cands:
        low=x.casefold()
        if not any(t.casefold() in low for t in preferred): continue
        if any(t.casefold() in low for t in forbidden): continue
        if len(x)>300: continue
        out.append(x)
    return sorted(out)

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="78b6694b40c45f5aeaf18afcaf8ca8513908e2fc","EXACT_BASE")
    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False,"NO_ARTICLE_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False and h["hidden_endpoint_bruteforce_allowed"] is False,"NO_SECRET_NO_BRUTEFORCE")
    ac=p["acquisition_contract"]; ec=p["extraction_contract"]; src=p["source"]
    req(ac["html_scope"]=="HEAD_ONLY_THROUGH_FIRST_CLOSING_HEAD","HEAD_ONLY")
    req(ac["inline_script_read"] is False and ac["external_script_src_from_head_only"] is True,"SCRIPT_SCOPE")
    req(ec["static_strings_only"] is True and ec["network_calls_to_discovered_routes"] is False,"STATIC_ONLY")

    head_reports=[]; scripts=[]; errors=[]
    for entry in src["entry_urls"]:
        try:
            head,final,headers=fetch_head_html(entry,timeout,src["allowed_domain_suffix"])
            ss=script_sources(head,final,src["allowed_domain_suffix"])
            head_reports.append({
                "entry_url":entry,"final_url":final,"head_sha256":sha256_bytes(head),
                "head_bytes":len(head),"script_src_n":len(ss),"script_src":ss,
                "content_type":headers.get("content-type")
            })
            scripts.extend(ss)
        except Exception as e:
            errors.append({"stage":"html_head","url":entry,"error":f"{type(e).__name__}:{e}"[:400]})
    scripts=sorted(dict.fromkeys(scripts))[:int(ac["max_script_n"])]

    total=0; bundle_reports=[]; routes=set()
    for u in scripts:
        if total>=int(ac["max_total_script_bytes"]): break
        try:
            raw,final,headers=fetch_script(u,timeout,int(ac["max_script_bytes_each"]),src["allowed_domain_suffix"])
            total+=len(raw)
            found=extract_routes(raw,ec["preferred_terms"],ec["forbidden_terms"])
            routes.update(found)
            bundle_reports.append({
                "source_url":u,"final_url":final,"sha256":sha256_bytes(raw),"bytes":len(raw),
                "route_string_n":len(found),"route_strings":found,
                "content_type":headers.get("content-type")
            })
        except Exception as e:
            errors.append({"stage":"script","url":u,"error":f"{type(e).__name__}:{e}"[:400]})

    api_host=src["api_host"].casefold()
    strong_terms=("content","news","article","category","document","documentation","comunicat","publication")
    raw_routes=sorted(routes)
    usable_routes=[]
    for x in raw_routes:
        low=x.casefold()
        if x.startswith(("http://","https://")) and not domain_ok(x,src["allowed_domain_suffix"]):
            continue
        if api_host in low or any(t in low for t in strong_terms):
            usable_routes.append(x)
    usable_routes=sorted(dict.fromkeys(usable_routes))
    dapi_routes=sorted(x for x in usable_routes if api_host in x.casefold())
    positive=bool(usable_routes)
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if positive else "STOP_DATA_COVERAGE"

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-lega-frontend-routes-receipt-v1",
        "status":"N10_REFEREE_LEGA_FRONTEND_ROUTE_DISCOVERY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
        "entry_n":len(src["entry_urls"]),"head_report_n":len(head_reports),"script_candidate_n":len(scripts),
        "script_report_n":len(bundle_reports),"total_script_bytes":total,
        "head_reports":head_reports,"bundle_reports":bundle_reports,"errors":errors,
        "raw_route_strings":raw_routes,"discovered_route_strings":usable_routes,"dapi_route_strings":dapi_routes,
        "network_calls_to_discovered_routes":False,
        "html_body_read":False,"inline_script_read":False,"article_body_read":False,
        "match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"full_big5_data_ready":False,"referee_oof_allowed":False,
        "next_step":(
            "FREEZE_EXACT_DISCOVERED_PUBLICATION_ROUTE_AND_PROBE_METADATA_ONLY_IN_NEW_BATCH"
            if positive else
            "STOP_FRONTEND_ROUTE_DISCOVERY_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        )
    }
    (out/"lega_frontend_routes_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True)); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument("--registry",type=Path,required=True); a.add_argument("--out",type=Path,required=True); a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args(); run(x.registry,x.out,x.timeout)
if __name__=="__main__": main()
