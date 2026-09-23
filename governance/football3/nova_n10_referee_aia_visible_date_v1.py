#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, html, json, re, ssl, urllib.parse, urllib.request
from pathlib import Path
from typing import Any

class VisibleDateError(RuntimeError): pass
def req(c: bool,m: str)->None:
    if not c: raise VisibleDateError(m)
def sha256_bytes(b:bytes)->str:
    return hashlib.sha256(b).hexdigest()
def domain_ok(url:str,suffix:str)->bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

H1_RE=re.compile(r"<h1\b[^>]*>(.*?)</h1>",re.I|re.S)
TAG_RE=re.compile(r"<[^>]+>",re.S)
DATE_RE=re.compile(r"\b([0-3]\d/[01]\d/20\d{2})\b")

def clean_text(s:str)->str:
    return " ".join(html.unescape(TAG_RE.sub(" ",s)).split())

def target_h1_ok(h1:str,terms:list[str])->bool:
    low=clean_text(h1).casefold()
    return all(t.casefold() in low for t in terms)

def locate_marker(buf:bytes,terms:list[str])->dict[str,Any]|None:
    s=buf.decode("utf-8","replace")
    h=H1_RE.search(s)
    if not h:
        return None
    if not target_h1_ok(h.group(1),terms):
        raise VisibleDateError("TARGET_H1_IDENTITY_FAIL")
    tail=s[h.end():]
    d=DATE_RE.search(tail)
    if not d:
        return None
    date_text=d.group(1)
    char_end=h.end()+d.end()
    persisted=s[:char_end].encode("utf-8")
    return {
        "h1":clean_text(h.group(1)),
        "date_text":date_text,
        "persisted_prefix":persisted,
        "char_end":char_end,
    }

def stream_to_visible_date(url:str,suffix:str,terms:list[str],timeout:int,chunk_bytes:int,max_bytes:int)->dict[str,Any]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_URL")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIAVisibleDate/1.0",
        "Accept":"text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(domain_ok(final,suffix),"REDIRECT_OUTSIDE_OFFICIAL")
        buf=bytearray()
        found=None
        while len(buf)<max_bytes:
            chunk=r.read(min(chunk_bytes,max_bytes-len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
            found=locate_marker(bytes(buf),terms)
            if found:
                break
        req(found is not None,"VISIBLE_DATE_MARKER_NOT_FOUND_WITHIN_BOUND")
        persisted=found.pop("persisted_prefix")
        return {
            **found,
            "source_url":url,
            "final_url":final,
            "network_bytes_read":len(buf),
            "persisted_prefix_bytes":len(persisted),
            "overshoot_bytes_discarded":max(0,len(buf)-len(persisted)),
            "persisted_prefix_sha256":sha256_bytes(persisted),
            "content_type":r.headers.get("Content-Type"),
        }

def date_is_safe(date_text:str,expected:str,cutoff_utc:str)->bool:
    if date_text!=expected:
        return False
    d=dt.datetime.strptime(date_text,"%d/%m/%Y").date()
    cutoff=dt.datetime.fromisoformat(cutoff_utc.replace("Z","+00:00")).astimezone(dt.timezone.utc)
    return d < cutoff.date()

def run(registry:Path,out:Path,timeout:int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="80fa4008f8e6c908d353637117a36a7d3634aa93","EXACT_BASE")
    h=p["hard_rules"]; ac=p["acquisition_contract"]; ev=p["evidence_contract"]; t=p["target"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["appointment_names_parsed"] is False and h["appointment_content_persisted"] is False,"NO_APPOINTMENT_CONTENT")
    req(h["article_content_after_date_parsed"] is False,"STOP_AT_DATE")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(ac["stop_condition"]=="FIRST_DDMMYYYY_AFTER_TARGET_H1","STOP_CONTRACT")
    req(ac["persisted_prefix_ends_at_date_marker"] is True and ac["parse_after_date_marker"] is False,"BOUNDARY")
    req(ac["persist_raw_prefix"] is False and ac["only_hash_truncated_prefix"] is True,"NO_RAW_PERSIST")
    req(ev["independent_immutable_archive_witness"] is False and ev["formal_available_at_proven"] is False,"NO_OVERCLAIM")

    errors=[]
    report=None
    positive=False
    try:
        report=stream_to_visible_date(
            t["official_url"],t["allowed_domain_suffix"],t["expected_h1_terms"],
            timeout,int(ac["chunk_bytes"]),int(ac["max_network_bytes"])
        )
        report["publication_date_iso"]=dt.datetime.strptime(report["date_text"],"%d/%m/%Y").date().isoformat()
        report["date_matches_expected"]=report["date_text"]==t["expected_publication_date"]
        report["date_before_safe_cutoff"]=date_is_safe(report["date_text"],t["expected_publication_date"],t["safe_cutoff_utc"])
        report["parsed_fields"]=["h1_title","visible_publication_date"]
        report["appointment_names_parsed"]=False
        report["content_after_date_parsed"]=False
        positive=bool(report["date_matches_expected"] and report["date_before_safe_cutoff"])
    except Exception as e:
        errors.append({"stage":"visible_date","url":t["official_url"],"error":f"{type(e).__name__}:{e}"[:400]})

    classification=p["success_contract"]["positive_classification"] if positive else p["success_contract"]["failure_classification"]
    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-visible-date-receipt-v1",
        "status":"N10_REFEREE_AIA_VISIBLE_DATE_AUDIT_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "target_report":report,
        "official_self_declared_publication_date_pass":positive,
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "target_season_inventory_ready":False,
        "error_n":len(errors),"errors":errors,
        "appointment_names_parsed":False,
        "appointment_content_persisted":False,
        "article_content_after_date_parsed":False,
        "raw_prefix_persisted":False,
        "match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,
        "training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,
        "full_big5_data_ready":False,"referee_oof_allowed":False,
        "next_step":(
            "PRESERVE_POSITIVE_SIGNAL_AND_REOPEN_AIA_TARGET_SEASON_INDEX_INVENTORY_ONLY_BECAUSE_CURRENT_AIA_REACHABILITY_IS_RESTORED"
            if positive else
            "STOP_VISIBLE_DATE_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        ),
    }
    (out/"aia_visible_date_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True))
    return receipt

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args(); run(x.registry,x.out,x.timeout)
if __name__=="__main__": main()
