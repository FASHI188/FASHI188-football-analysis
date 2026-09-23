#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

class SkyCombinedFreezeError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyCombinedFreezeError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def stable_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

def download_artifact_zip(repo: str, artifact_id: int, token: str, limit: int = 5_000_000) -> bytes:
    req(bool(token), "GITHUB_TOKEN_REQUIRED")
    url=f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    request=urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Football3-Nova-N10-SkyCombinedFreeze/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data=response.read(limit+1)
    req(len(data) <= limit, "ARTIFACT_ZIP_TOO_LARGE")
    return data

def read_unique_suffix(zbytes: bytes, suffix: str) -> tuple[str, bytes, Any]:
    with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
        names=[n for n in zf.namelist() if n.endswith(suffix)]
        req(len(names)==1, f"ZIP_SUFFIX_NOT_UNIQUE:{suffix}:{len(names)}")
        raw=zf.read(names[0])
    return names[0], raw, json.loads(raw.decode("utf-8"))

def round_title_identity(title: str, round_no: int) -> bool:
    folded=" ".join((title or "").casefold().split())
    if "serie a" not in folded:
        return False
    if "arbitr" not in folded and "designaz" not in folded:
        return False
    n=str(round_no)
    patterns=[
        rf"(?<!\d){re.escape(n)}\s*(?:\^|ª|º|a)?\s*giornata",
        rf"giornata\s*{re.escape(n)}(?!\d)",
    ]
    if round_no==1:
        patterns.append(r"\bprima\s+giornata\b")
    return any(re.search(p, folded, re.I) for p in patterns)

def select_page_title(titles: list[str], round_no: int) -> str:
    clean=[]
    for title in titles or []:
        t=" ".join(str(title).split())
        if t and t not in clean:
            clean.append(t)
    exact=[t for t in clean if round_title_identity(t,round_no)]
    req(bool(exact), f"PAGE_TITLE_IDENTITY_MISSING:R{round_no}")
    return sorted(exact, key=lambda t:(len(t),t))[0]

def url_round_tokens(url: str) -> list[int]:
    return [int(x) for x in re.findall(r"giornata-(\d+)", url or "", re.I)]

def accepted_set(parent: dict[str,Any]) -> set[int]:
    return {int(x) for x in parent["accepted_rounds"]}

def extract_rows(parent: dict[str,Any], receipt: dict[str,Any], receipt_sha: str, evidence_sha: str) -> dict[int,dict[str,Any]]:
    req(receipt.get("classification")==parent["expected_classification"], f"PARENT_CLASSIFICATION:{parent['pr']}")
    accepted=accepted_set(parent)
    reports=receipt.get("round_reports")
    req(isinstance(reports,list), f"PARENT_ROUND_REPORTS_MISSING:{parent['pr']}")
    out={}
    for report in reports:
        rnd=int(report.get("round"))
        if rnd not in accepted:
            continue
        candidate=report.get("canonical_candidate")
        req(isinstance(candidate,dict), f"CANONICAL_CANDIDATE_MISSING:R{rnd}:PR{parent['pr']}")
        req(rnd not in out, f"DUPLICATE_ROUND_WITHIN_PARENT:R{rnd}:PR{parent['pr']}")
        page_title=select_page_title(candidate.get("title_candidates") or [],rnd)
        published_local=candidate.get("published_local")
        marker=candidate.get("visible_marker_context")
        prefix_sha=candidate.get("prefix_sha256")
        req(isinstance(published_local,str) and published_local, f"PUBLISHED_LOCAL_MISSING:R{rnd}")
        req(isinstance(marker,str) and marker, f"VISIBLE_MARKER_MISSING:R{rnd}")
        req(isinstance(prefix_sha,str) and re.fullmatch(r"[0-9a-f]{64}",prefix_sha), f"PREFIX_SHA_INVALID:R{rnd}")
        sky_url=(
            candidate.get("final_normalized_url")
            or candidate.get("normalized_url")
            or candidate.get("final_url")
            or candidate.get("url")
        )
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"), f"SKY_URL_INVALID:R{rnd}")
        out[rnd]={
            "round":rnd,
            "aia_anchor_published_date":report.get("aia_published_date"),
            "sky_published_local":published_local,
            "timezone":candidate.get("timezone") or "Europe/Rome",
            "sky_url":sky_url,
            "page_title":page_title,
            "visible_marker_context":marker,
            "archive_date":candidate.get("archive_date"),
            "archive_page":candidate.get("archive_page"),
            "prefix_sha256":prefix_sha,
            "prefix_boundary_bytes":candidate.get("prefix_boundary_bytes"),
            "network_bytes_read":candidate.get("network_bytes_read"),
            "overshoot_bytes_discarded":candidate.get("overshoot_bytes_discarded"),
            "identity_status":"PASS",
            "provenance":{
                "source_layer":parent["layer"],
                "source_pr":parent["pr"],
                "source_head":parent["head"],
                "source_run":parent["run"],
                "source_artifact":parent["artifact_id"],
                "source_artifact_digest":"sha256:"+parent["artifact_zip_sha256"],
                "source_receipt_sha256":receipt_sha,
                "source_evidence_file_sha256":evidence_sha,
            },
            "body_content_included":False,
            "referee_assignment_content_included":False,
        }
    req(set(out)==accepted, f"ACCEPTED_ROUND_SET_MISMATCH:PR{parent['pr']}")
    return out

def build_freeze(registry: dict[str,Any], parent_payloads: list[dict[str,Any]]) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    expected={int(x) for x in registry["merge_contract"]["expected_rounds"]}
    req(expected==set(range(1,39)), "EXPECTED_ROUNDS_NOT_1_TO_38")
    merged={}
    parent_manifest=[]
    for payload in parent_payloads:
        parent=payload["parent"]
        receipt=payload["receipt"]
        rows=extract_rows(parent,receipt,payload["receipt_sha256"],payload["evidence_sha256"])
        for rnd,row in rows.items():
            req(rnd not in merged, f"DUPLICATE_ROUND_ACROSS_PARENTS:R{rnd}")
            merged[rnd]=row
        parent_manifest.append({
            "layer":parent["layer"],
            "pr":parent["pr"],
            "head":parent["head"],
            "run":parent["run"],
            "artifact":parent["artifact_id"],
            "artifact_zip_sha256":payload["artifact_zip_sha256"],
            "receipt_sha256":payload["receipt_sha256"],
            "evidence_sha256":payload["evidence_sha256"],
            "accepted_rounds":sorted(accepted_set(parent)),
            "classification":receipt["classification"],
        })
    req(set(merged)==expected, "MERGED_ROUND_PARTITION_NOT_COMPLETE")

    anomalies=[]
    for rnd in sorted(merged):
        row=merged[rnd]
        tokens=url_round_tokens(row["sky_url"])
        mismatched=[x for x in tokens if x!=rnd]
        if mismatched:
            row["identity_status"]="PASS_WITH_ANOMALY"
            anomalies.append({
                "round":rnd,
                "anomaly_type":"URL_SLUG_ROUND_MISMATCH",
                "expected_round":rnd,
                "url_round_tokens":tokens,
                "sky_url":row["sky_url"],
                "page_title":row["page_title"],
                "resolution":"RETAIN_WITH_EXPLICIT_ANOMALY: archive-index candidate and page title mechanically identify the target round; URL slug is not treated as the primary round identity.",
                "requires_assignment_body":False,
            })

    observed={a["round"] for a in anomalies}
    required_known={int(x) for x in registry["anomaly_contract"]["known_expected_anomaly_rounds"]}
    req(required_known.issubset(observed), "KNOWN_IDENTITY_ANOMALY_NOT_RECORDED")

    ledger={
        "schema_version":"football3-nova-n10-referee-sky-combined-publication-ledger-v1",
        "status":"FROZEN_SOURCE_COVERAGE_NOT_PIT_BOUND",
        "project_id":"football3",
        "research_project":"N10_DISCIPLINE_AND_REFEREE",
        "competition":"Serie_A",
        "season":"2022/23",
        "round_n":38,
        "coverage_round_n":38,
        "coverage":1.0,
        "source_family":"CONTEMPORANEOUS_NEWS_PUBLICATION_WITNESS",
        "source_semantics":{
            "current_publisher_visible_timestamp":True,
            "independent_immutable_archive_witness":False,
            "formal_available_at_proven":False,
            "fixture_level_binding_complete":False,
            "referee_oof_allowed":False,
        },
        "parent_artifacts":parent_manifest,
        "identity_anomaly_count":len(anomalies),
        "identity_anomaly_rounds":sorted(observed),
        "rows":[merged[i] for i in range(1,39)],
        "hard_safety":{
            "result_labels_read":0,
            "score_values_read":0,
            "article_body_read":False,
            "referee_assignment_body_parsed":False,
            "training_performed":False,
            "scoring_performed":False,
            "formal_v2_changed":False,
            "current_changed":False,
            "production_changed":False,
            "candidate_weight":0,
            "matrix_delta":0,
        },
    }
    ledger_sha=sha256_bytes(stable_bytes(ledger))
    anomaly_ledger={
        "schema_version":"football3-nova-n10-referee-sky-identity-anomaly-ledger-v1",
        "status":"FROZEN",
        "combined_ledger_sha256":ledger_sha,
        "anomaly_n":len(anomalies),
        "anomaly_rounds":sorted(observed),
        "anomalies":anomalies,
        "body_content_used":False,
        "referee_assignment_body_used":False,
    }
    anomaly_sha=sha256_bytes(stable_bytes(anomaly_ledger))
    provenance={
        "schema_version":"football3-nova-n10-referee-sky-combined-freeze-provenance-v1",
        "status":"FROZEN",
        "exact_base":registry["exact_base"],
        "parents":parent_manifest,
        "combined_ledger_sha256":ledger_sha,
        "identity_anomaly_ledger_sha256":anomaly_sha,
        "round_n":38,
        "coverage_round_n":38,
        "identity_anomaly_n":len(anomalies),
        "identity_anomaly_rounds":sorted(observed),
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "referee_oof_allowed":False,
        "result_labels_read":0,
        "score_values_read":0,
    }
    return ledger, anomaly_ledger, provenance

def acquire_parents(registry: dict[str,Any], token: str) -> list[dict[str,Any]]:
    repo=registry["repository"]
    payloads=[]
    for parent in registry["parents"]:
        zbytes=download_artifact_zip(repo,int(parent["artifact_id"]),token)
        zsha=sha256_bytes(zbytes)
        req(zsha==parent["artifact_zip_sha256"], f"ARTIFACT_SHA_MISMATCH:PR{parent['pr']}")
        _,receipt_raw,receipt=read_unique_suffix(zbytes,parent["receipt_suffix"])
        _,evidence_raw,_=read_unique_suffix(zbytes,parent["evidence_suffix"])
        rsha=sha256_bytes(receipt_raw)
        esha=sha256_bytes(evidence_raw)
        req(rsha==parent["receipt_sha256"], f"RECEIPT_SHA_MISMATCH:PR{parent['pr']}")
        req(esha==parent["evidence_sha256"], f"EVIDENCE_SHA_MISMATCH:PR{parent['pr']}")
        payloads.append({
            "parent":parent,
            "receipt":receipt,
            "artifact_zip_sha256":zsha,
            "receipt_sha256":rsha,
            "evidence_sha256":esha,
        })
    return payloads

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    registry=json.loads(registry_path.read_text(encoding="utf-8"))
    req(registry["status"]=="DESIGN_LOCKED_ARTIFACT_FREEZE_ZERO_LABEL","STATUS")
    req(registry["exact_base"]=="b20c3cdab6c61d788672fecf434ecc9fe94c750e","EXACT_BASE")
    hard=registry["hard_rules"]
    req(hard["network_source_reacquisition_allowed"] is False,"NO_SOURCE_REACQUISITION")
    req(hard["search_allowed"] is False,"NO_SEARCH")
    req(hard["article_body_read"] is False and hard["referee_assignment_body_parsed"] is False,"NO_BODY")
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    payloads=acquire_parents(registry,token)
    ledger,anomalies,provenance=build_freeze(registry,payloads)
    ledger_bytes=stable_bytes(ledger)
    anomaly_bytes=stable_bytes(anomalies)
    provenance_bytes=stable_bytes(provenance)

    out.mkdir(parents=True,exist_ok=True)
    (out/"sky_combined_publication_ledger.json").write_bytes(ledger_bytes)
    (out/"sky_identity_anomaly_ledger.json").write_bytes(anomaly_bytes)
    (out/"sky_combined_freeze_provenance.json").write_bytes(provenance_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-combined-freeze-receipt-v1",
        "status":"N10_REFEREE_SKY_COMBINED_38_ROUND_FREEZE_COMPLETE",
        "classification":"POSITIVE_SIGNAL_SOURCE_COVERAGE_FROZEN",
        "exact_base":registry["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_artifact_n":len(payloads),
        "artifact_digest_verified_n":len(payloads),
        "parent_receipt_sha_verified_n":len(payloads),
        "parent_evidence_sha_verified_n":len(payloads),
        "round_n":38,
        "coverage_round_n":38,
        "coverage":1.0,
        "combined_ledger_sha256":sha256_bytes(ledger_bytes),
        "identity_anomaly_ledger_sha256":sha256_bytes(anomaly_bytes),
        "provenance_sha256":sha256_bytes(provenance_bytes),
        "identity_anomaly_n":anomalies["anomaly_n"],
        "identity_anomaly_rounds":anomalies["anomaly_rounds"],
        "parent_manifest":provenance["parents"],
        "network_source_reacquisition":False,
        "search_performed":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
        "match_payload_read":False,
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
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "next_step":registry["freeze_semantics"]["next_step"],
    }
    (out/"sky_combined_freeze_receipt.json").write_bytes(stable_bytes(receipt))
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--registry",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    token=os.environ.get("GITHUB_TOKEN","")
    run(args.registry,args.out,token)

if __name__=="__main__":
    main()
