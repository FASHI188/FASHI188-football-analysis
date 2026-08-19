#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N7_FIVELEAGUE_METADATA_V1"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
OUT = Path("football-data/research/c072n7_metadata_result.json")
PAGES = {
    "EPL": "https://footiqo.com/database/leagues/england-premier-league/",
    "LL": "https://footiqo.com/database/leagues/spain-laliga/",
    "BL": "https://footiqo.com/database/leagues/germany-bundesliga/",
    "SA": "https://footiqo.com/database/leagues/italy-serie-a/",
    "L1": "https://footiqo.com/database/leagues/france-ligue-1/",
}
N3_EXPECTED = {"EPL":545,"LL":555,"BL":565,"SA":575,"L1":585}
FORBIDDEN_FIELD_RE = re.compile(
    r"(?:^|[_\-])(fthg|ftag|ftr|hthg|htag|htr|score|result|homegoals|awaygoals|1hhg|1hag|1hr|2hhg|2hag|2hr)(?:$|[_\-])",
    re.I,
)


def norm(x: str) -> str:
    return re.sub(r"\s+", " ", str(x).strip())


def odds_headers(table) -> list[str]:
    headers = [norm(x.get_text(" ", strip=True)) for x in table.find_all("th")]
    if not headers:
        first = table.find("tr")
        if first:
            headers = [norm(x.get_text(" ", strip=True)) for x in first.find_all(["th","td"])]
    return headers


def visible_seasons(table, headers: list[str]) -> list[str]:
    if "Season" not in headers:
        return []
    i = headers.index("Season")
    vals=[]
    for tr in table.find_all("tr")[1:]:
        cells=[norm(x.get_text(" ",strip=True)) for x in tr.find_all(["td","th"])]
        if len(cells)>i and cells[i] and cells[i] != "Season":
            vals.append(cells[i])
    return sorted(set(vals))


def resolve_last_seasons(html: str) -> tuple[object | None, list[str], list[str]]:
    marker=html.find(HEADING)
    if marker < 0:
        return None,[],[]
    soup=BeautifulSoup(html[marker:],"html.parser")
    candidates=[]
    for t in soup.find_all("table"):
        h=odds_headers(t)
        if not {"O15","U15","O25","U25","O35","U35"}.issubset(set(h)):
            continue
        seasons=visible_seasons(t,h)
        # Binding Last-seasons discriminator: visible historical season, not current/future-only.
        hist=[s for s in seasons if s not in {"2025/2026","2026/2027"}]
        if hist:
            candidates.append((t,h,seasons))
    if len(candidates) != 1:
        return None,[],[]
    return candidates[0]


def build_payload(headers: list[str], nonce: str) -> dict[str,str]:
    body={
        "draw":"1","start":"0","length":"1",
        "search[value]":"","search[regex]":"false",
        NONCE_FIELD:nonce,
    }
    for i,h in enumerate(headers):
        body[f"columns[{i}][data]"]=str(i)
        body[f"columns[{i}][name]"]=h
        body[f"columns[{i}][searchable]"]="true"
        body[f"columns[{i}][orderable]"]="true"
        body[f"columns[{i}][search][value]"]=""
        body[f"columns[{i}][search][regex]"]="false"
    return body


def int_or_none(x):
    try: return int(x)
    except (TypeError,ValueError): return None


def safe_shape(data) -> dict:
    out={"container_type":type(data).__name__,"returned_row_count":0}
    if not isinstance(data,list): return out
    out["returned_row_count"]=len(data)
    if not data: return out
    first=data[0]
    if isinstance(first,list):
        out.update({"first_row_type":"array","first_row_array_length":len(first),"forbidden_field_names":[]})
    elif isinstance(first,dict):
        names=sorted(str(k) for k in first)
        out.update({"first_row_type":"object","first_row_field_names":names,"first_row_field_count":len(names),
                    "forbidden_field_names":[k for k in names if FORBIDDEN_FIELD_RE.search(k)]})
    else:
        out.update({"first_row_type":type(first).__name__,"forbidden_field_names":[]})
    return out


def persist(result:dict)->int:
    text=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)
    assert "nonce_value" not in text.lower()
    OUT.write_text(text,encoding="utf-8")
    print(text)
    return 0


def main()->int:
    result={
        "schema":SCHEMA,"project_line":"football3",
        "football_table_data_requests_made":0,
        "nonce_values_persisted_or_logged":0,
        "football_row_values_persisted":0,
        "target_result_values_materialized":0,
        "model_fit":0,"model_score":0,
        "C073_C077_quarantined":True,
        "C070F_confirmation1597_opened":False,
        "protected_opened":False,
        "formal_weight":0,
    }
    s=requests.Session()
    s.headers.update({
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research",
        "Accept-Language":"en-US,en;q=0.9",
    })
    league_results={}
    any_access_block=False
    any_protocol_drift=False

    for code,url in PAGES.items():
        lr={"page_url":url,"request_made":False,"nonce_persisted_or_logged":0,"football_row_values_persisted":0}
        try:
            page=s.get(url,timeout=45,allow_redirects=True)
        except Exception:
            lr["page_request_error"]=True; any_access_block=True; league_results[code]=lr; continue
        lr["page_status_code"]=page.status_code; lr["page_bytes"]=len(page.content)
        if not (200 <= page.status_code < 300):
            any_access_block=True; league_results[code]=lr; continue

        table,headers,seasons=resolve_last_seasons(page.text)
        lr["visible_historical_seasons"]=seasons
        if table is None:
            lr["protocol_drift_reason"]="last_seasons_table_not_unique"; any_protocol_drift=True; league_results[code]=lr; continue
        table_id=int(str(table.get("data-wpdatatable_id"))) if str(table.get("data-wpdatatable_id","")).isdigit() else None
        dom_id=str(table.get("id", ""))
        lr["resolved_table_id"]=table_id; lr["resolved_dom_id"]=dom_id
        lr["n3_expected_table_id"]=N3_EXPECTED[code]
        lr["n3_mapping_consistent"]=table_id==N3_EXPECTED[code]
        lr["header_count"]=len(headers); lr["headers"]=headers
        forbidden_headers=[h for h in headers if FORBIDDEN_FIELD_RE.search(h)]
        lr["forbidden_visible_header_names"]=forbidden_headers
        if table_id is None or not lr["n3_mapping_consistent"] or len(headers)!=22 or forbidden_headers:
            lr["protocol_drift_reason"]="table_id_or_schema_drift"; any_protocol_drift=True; league_results[code]=lr; continue

        nonce_dom=f"wdtNonceFrontendServerSide_{table_id}"
        soup=BeautifulSoup(page.text,"html.parser")
        nodes=[el for el in soup.find_all("input") if str(el.get("id",""))==nonce_dom and str(el.get("name",""))==nonce_dom]
        lr["nonce_dom_name"]=nonce_dom; lr["nonce_element_count"]=len(nodes); lr["nonce_element_unique"]=len(nodes)==1
        if len(nodes)!=1 or str(nodes[0].get("type","")).lower()!="hidden":
            lr["protocol_drift_reason"]="nonce_dom_drift"; any_protocol_drift=True; league_results[code]=lr; continue
        raw=nodes[0].get("value"); nonce=str(raw) if raw is not None else ""
        lr["nonce_nonempty"]=bool(nonce)
        if not nonce:
            lr["protocol_drift_reason"]="nonce_empty"; any_protocol_drift=True; league_results[code]=lr; continue

        body=build_payload(headers,nonce)
        req_headers={
            "Accept":"application/json, text/javascript, */*; q=0.01",
            "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With":"XMLHttpRequest",
            "Origin":"https://footiqo.com","Referer":url,
        }
        result["football_table_data_requests_made"] += 1
        lr["request_made"]=True; lr["nonce_sent"]=True
        try:
            rr=s.post(AJAX,params={"action":ACTION,"table_id":str(table_id)},data=body,headers=req_headers,timeout=45,allow_redirects=True)
        except Exception:
            lr["table_request_error"]=True; any_access_block=True
            body[NONCE_FIELD]="<redacted>"; nonce=""; raw=None
            league_results[code]=lr; continue
        finally:
            body[NONCE_FIELD]="<redacted>"; nonce=""; raw=None

        lr["response_status_code"]=rr.status_code; lr["response_bytes"]=len(rr.content); lr["response_content_type"]=rr.headers.get("content-type","")
        if not (200 <= rr.status_code < 300):
            any_access_block=True; league_results[code]=lr; continue
        try:
            x=rr.json()
        except Exception:
            lr["json_valid"]=False; any_protocol_drift=True; league_results[code]=lr; continue
        lr["json_valid"]=isinstance(x,dict)
        if not isinstance(x,dict):
            any_protocol_drift=True; league_results[code]=lr; continue
        lr["json_top_level_keys"]=sorted(str(k) for k in x)
        lr["records_total"]=int_or_none(x.get("recordsTotal",x.get("iTotalRecords")))
        lr["records_filtered"]=int_or_none(x.get("recordsFiltered",x.get("iTotalDisplayRecords")))
        lr["draw"]=int_or_none(x.get("draw"))
        shape=safe_shape(x.get("data",x.get("aaData",[])))
        lr["response_data_shape"]=shape
        league_results[code]=lr

    result["league_results"]=league_results
    filtered=[v.get("records_filtered") for v in league_results.values() if isinstance(v.get("records_filtered"),int)]
    result["pooled_records_filtered"]=sum(filtered)
    result["leagues_records_filtered_ge_500"]=sum(1 for x in filtered if x>=500)
    result["leagues_records_filtered_ge_1000"]=sum(1 for x in filtered if x>=1000)

    if any_access_block:
        result["terminal"]="C072N7_ACCESS_BLOCKED"
        return persist(result)
    if any_protocol_drift:
        result["terminal"]="C072N7_PROTOCOL_DRIFT_STOP"
        return persist(result)

    vals=list(league_results.values())
    gates={
        "all_five_pages_and_tables_resolved":len(vals)==5 and all(200<=v.get("page_status_code",0)<300 and v.get("resolved_table_id") for v in vals),
        "all_five_unique_nonempty_nonce_inputs":all(v.get("nonce_element_unique") and v.get("nonce_nonempty") for v in vals),
        "exactly_five_data_requests":result["football_table_data_requests_made"]==5 and all(v.get("request_made") for v in vals),
        "all_five_valid_json_2xx":all(200<=v.get("response_status_code",0)<300 and v.get("json_valid") for v in vals),
        "all_five_exactly_one_row":all(v.get("response_data_shape",{}).get("returned_row_count")==1 for v in vals),
        "all_five_22field_clean_schema":all(v.get("header_count")==22 and not v.get("forbidden_visible_header_names") and v.get("response_data_shape",{}).get("first_row_array_length")==22 for v in vals),
        "all_five_filtered_ge_500":len(filtered)==5 and all(x>=500 for x in filtered),
        "four_leagues_filtered_ge_1000":sum(1 for x in filtered if x>=1000)>=4,
        "pooled_filtered_ge_4000":sum(filtered)>=4000,
        "all_five_positive_records_total":all(isinstance(v.get("records_total"),int) and v.get("records_total")>0 for v in vals),
        "zero_nonce_persistence":result["nonce_values_persisted_or_logged"]==0,
        "zero_row_value_persistence":result["football_row_values_persisted"]==0,
        "zero_target_materialization":result["target_result_values_materialized"]==0,
        "zero_model":result["model_fit"]==0 and result["model_score"]==0,
    }
    result["gates"]=gates
    result["terminal"]="C072N7_FIVELEAGUE_METADATA_PASS" if all(gates.values()) else "C072N7_METADATA_COVERAGE_FAIL"
    return persist(result)

if __name__=="__main__":
    raise SystemExit(main())
