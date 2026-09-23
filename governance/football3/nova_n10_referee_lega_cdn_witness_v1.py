#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

class LegaCDNWitnessError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise LegaCDNWitnessError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_iso(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    req(x.tzinfo is not None,"TZ_REQUIRED")
    return x.astimezone(dt.timezone.utc)

def token_from_url(url: str) -> str:
    m=re.search(r"/vimages/([0-9a-fA-F]{8})/",urllib.parse.urlparse(url).path)
    req(m is not None,"TOKEN_NOT_FOUND")
    return m.group(1).lower()

def token_time_utc(url: str) -> dt.datetime:
    return dt.datetime.fromtimestamp(int(token_from_url(url),16),tz=dt.timezone.utc)

def official_host_ok(url: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    return h=="img.legaseriea.it" or h.endswith(".legaseriea.it")

def fetch_pdf(url: str, timeout: int=25, limit: int=8_000_000) -> tuple[bytes,str,dict[str,str]]:
    req(official_host_ok(url),"NON_OFFICIAL_SOURCE")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-LegaCDNWitness/1.0",
        "Accept":"application/pdf,*/*;q=0.5",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        data=r.read(limit+1)
        req(len(data)<=limit,"PDF_TOO_LARGE")
        req(data.startswith(b"%PDF-"),"NOT_PDF")
        final=r.geturl()
        req(official_host_ok(final),"REDIRECT_OUTSIDE_LEGA")
        return data,final,{k.lower():v for k,v in r.headers.items()}

def first_page_text(pdf: bytes) -> str:
    exe=shutil.which("pdftotext")
    req(exe is not None,"PDFTOTEXT_NOT_AVAILABLE")
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/"doc.pdf"
        p.write_bytes(pdf)
        cp=subprocess.run(
            [exe,"-f","1","-l","1","-layout",str(p),"-"],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=20,
        )
        req(cp.returncode==0,"PDFTOTEXT_FAILED:"+cp.stderr.decode("utf-8","replace")[:200])
        return cp.stdout.decode("utf-8","replace")

CREATED_RE=re.compile(r"created\s+on\s+(\d{2}/\d{2}/\d{4})\s+on\s+(\d{2}:\d{2}:\d{2})",re.I)

def created_at_utc(text: str) -> dt.datetime | None:
    m=CREATED_RE.search(text)
    if not m:
        return None
    x=dt.datetime.strptime(m.group(1)+" "+m.group(2),"%d/%m/%Y %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Rome"))
    return x.astimezone(dt.timezone.utc)

def normalize_words(s: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+"," ",s.upper()).split())

def referee_found(text: str, expected: str) -> bool:
    t=normalize_words(text)
    e=normalize_words(expected)
    return e in t and "REFEREE" in t

def inspect_document(row: dict[str,Any], timeout: int) -> dict[str,Any]:
    pdf,final,headers=fetch_pdf(row["url"],timeout)
    text=first_page_text(pdf)
    created=created_at_utc(text)
    token=token_time_utc(row["url"])
    cutoff=parse_iso(row["match_cutoff_utc"])
    out={
        "id":row["id"],
        "source_url":row["url"],
        "final_url":final,
        "pdf_sha256":sha256_bytes(pdf),
        "pdf_bytes":len(pdf),
        "token_hex":token_from_url(row["url"]),
        "token_time_utc":token.isoformat(),
        "created_at_utc":created.isoformat() if created else None,
        "token_before_match":token < cutoff,
        "created_before_match":bool(created and created < cutoff),
        "content_type":headers.get("content-type"),
        "last_modified":headers.get("last-modified"),
        "target_result_labels_read":0,
        "target_score_values_read":0,
        "persisted_text":False,
        "parsed_fields":["created_at","referee_identity"] if "expected_referee" in row else ["created_at"],
    }
    if created:
        out["token_minus_created_hours"]=(token-created).total_seconds()/3600.0
        out["abs_token_vs_created_hours"]=abs(out["token_minus_created_hours"])
    if "expected_created_local" in row:
        exp=dt.datetime.strptime(row["expected_created_local"],"%d/%m/%Y %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Rome")).astimezone(dt.timezone.utc)
        out["expected_created_at_utc"]=exp.isoformat()
        out["created_expected_match"]=bool(created and abs((created-exp).total_seconds())<=1)
    if "expected_referee" in row:
        out["expected_referee"]=row["expected_referee"]
        out["referee_identity_found"]=referee_found(text,row["expected_referee"])
    return out

def run(registry: Path, out: Path, timeout: int=25) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="6497f8a0fcec25ac5d456e76291af53328c4904d","EXACT_BASE")
    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["target_score_values_read"] is False,"ZERO_TARGET_LABEL")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["only_metadata_and_referee_identity_may_be_parsed"] is True,"PARSE_SCOPE")
    req(h["target_result_or_postmatch_section_forbidden"] is True,"NO_POSTMATCH")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    max_hours=float(p["decision_contract"]["max_abs_token_vs_created_hours"])
    validations=[]
    errors=[]
    for row in p["validation_documents"]:
        try:
            validations.append(inspect_document(row,timeout))
        except Exception as e:
            errors.append({"id":row["id"],"error":f"{type(e).__name__}:{e}"[:400]})

    validation_pass=(
        len(validations)==len(p["validation_documents"])
        and all(x.get("created_expected_match") for x in validations)
        and all(x.get("token_before_match") for x in validations)
        and all(x.get("created_before_match") for x in validations)
        and all(x.get("abs_token_vs_created_hours",1e9)<=max_hours for x in validations)
    )

    target_cfg={**p["target_document"]}
    target_cfg["url"]=p["target_document"]["url"]
    target_cfg["id"]=p["target_document"]["id"]
    try:
        target=inspect_document(target_cfg,timeout)
        floor=parse_iso(p["target_document"]["aia_publication_floor_utc"])
        target["token_after_aia_publication_floor"]=token_time_utc(target_cfg["url"])>=floor
        target_pass=(
            target.get("token_before_match") is True
            and target.get("created_before_match") is True
            and target.get("referee_identity_found") is True
            and target.get("token_after_aia_publication_floor") is True
        )
    except Exception as e:
        target={"id":target_cfg["id"],"error":f"{type(e).__name__}:{e}"[:400]}
        target_pass=False

    source_feasibility=validation_pass and target_pass
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if source_feasibility else "STOP_DATA_COVERAGE"
    formal_available_at=False

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-lega-cdn-witness-receipt-v1",
        "status":"N10_REFEREE_LEGA_CDN_WITNESS_AUDIT_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "validation_document_n":len(p["validation_documents"]),
        "validation_success_n":len(validations),
        "validation_pass":validation_pass,
        "validation_reports":validations,
        "validation_errors":errors,
        "target_report":target,
        "target_candidate_pass":target_pass,
        "token_semantics_supported":source_feasibility,
        "formal_available_at_proven":formal_available_at,
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
            "IF_POSITIVE_SEEK_INDEPENDENT_PUBLICATION_OR_ARCHIVE_WITNESS_FOR_LEGA_PDF_URL_BEFORE_USING_AS_AVAILABLE_AT"
            if source_feasibility else
            "STOP_LEGA_CDN_TOKEN_ROUTE; CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        ),
    }
    (out/"lega_cdn_witness_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True))
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
