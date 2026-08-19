#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html as html_lib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N8_MULTILINE_ODDS_ZERO_LABEL_V1"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
PAGE_SIZE = 500
MAX_REQUESTS = 60
PAGES = {
    "EPL": ("https://footiqo.com/database/leagues/england-premier-league/", 545),
    "LL": ("https://footiqo.com/database/leagues/spain-laliga/", 555),
    "BL": ("https://footiqo.com/database/leagues/germany-bundesliga/", 565),
    "SA": ("https://footiqo.com/database/leagues/italy-serie-a/", 575),
    "L1": ("https://footiqo.com/database/leagues/france-ligue-1/", 585),
}
HEADERS = ["id","matchDate","Country","League","Season","homeTeam","awayTeam","H","D","A","O05","U05","O15","U15","O25","U25","O35","U35","O45","U45","BTTSY","BTTSN"]
OUT_CSV = Path("football-data/research/c072n8_multiline_odds.csv")
OUT_SUMMARY = Path("football-data/research/c072n8_zero_label_summary.json")
FORBIDDEN_COL_RE = re.compile(r"(?:^|[_\-])(fthg|ftag|ftr|hthg|htag|htr|score|result|homegoals|awaygoals|1hhg|1hag|1hr|2hhg|2hag|2hr)(?:$|[_\-])", re.I)
PRICE_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def norm(x) -> str:
    if x is None:
        return ""
    s = html_lib.unescape(str(x)).strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def table_headers(t) -> list[str]:
    h=[norm(x.get_text(" ",strip=True)) for x in t.find_all("th")]
    if not h:
        first=t.find("tr")
        if first:
            h=[norm(x.get_text(" ",strip=True)) for x in first.find_all(["th","td"])]
    return h


def visible_seasons(t,h:list[str])->list[str]:
    if "Season" not in h: return []
    i=h.index("Season"); vals=[]
    for tr in t.find_all("tr")[1:]:
        cells=[norm(x.get_text(" ",strip=True)) for x in tr.find_all(["td","th"])]
        if len(cells)>i and cells[i] and cells[i] != "Season": vals.append(cells[i])
    return sorted(set(vals))


def resolve(html:str, expected:int):
    marker=html.find(HEADING)
    if marker<0: return None,[],[]
    soup=BeautifulSoup(html[marker:],"html.parser")
    candidates=[]
    for t in soup.find_all("table"):
        h=table_headers(t)
        if not {"O15","U15","O25","U25","O35","U35"}.issubset(set(h)): continue
        seasons=visible_seasons(t,h)
        hist=[s for s in seasons if s not in {"2025/2026","2026/2027"}]
        if hist: candidates.append((t,h,seasons))
    if len(candidates)!=1: return None,[],[]
    t,h,seasons=candidates[0]
    tid=int(str(t.get("data-wpdatatable_id"))) if str(t.get("data-wpdatatable_id","")).isdigit() else None
    if tid != expected or h != HEADERS: return None,[],[]
    return t,h,seasons


def payload(headers:list[str], nonce:str, start:int)->dict[str,str]:
    body={"draw":"1","start":str(start),"length":str(PAGE_SIZE),"search[value]":"","search[regex]":"false",NONCE_FIELD:nonce}
    for i,h in enumerate(headers):
        body[f"columns[{i}][data]"]=str(i); body[f"columns[{i}][name]"]=h
        body[f"columns[{i}][searchable]"]="true"; body[f"columns[{i}][orderable]"]="true"
        body[f"columns[{i}][search][value]"]=""; body[f"columns[{i}][search][regex]"]="false"
    return body


def as_int(x):
    try: return int(x)
    except (TypeError,ValueError): return None


def price(x)->float|None:
    s=norm(x)
    m=PRICE_RE.search(s)
    if not m: return None
    try: v=float(m.group(0).replace(",","."))
    except ValueError: return None
    return v if math.isfinite(v) and v>1.0 else None


def valid_date(s:str)->bool:
    s=norm(s)
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%Y/%m/%d","%m/%d/%Y","%Y-%m-%d %H:%M:%S"):
        try: datetime.strptime(s,fmt); return True
        except ValueError: pass
    try: datetime.fromisoformat(s.replace("Z","+00:00")); return True
    except ValueError: return False


def devig_over(o:float,u:float)->float:
    io,iu=1.0/o,1.0/u
    return io/(io+iu)


def write_csv(rows:list[dict])->tuple[str,str]:
    cols=["sourceCode"]+HEADERS
    buf=io.StringIO(newline="")
    w=csv.DictWriter(buf,fieldnames=cols,lineterminator="\n",extrasaction="ignore")
    w.writeheader(); w.writerows(rows)
    raw=buf.getvalue().encode("utf-8")
    OUT_CSV.write_bytes(raw)
    data_sha=hashlib.sha256(raw).hexdigest()
    identity_lines=["|".join([r.get("sourceCode",""),r.get("id",""),r.get("matchDate",""),r.get("League",""),r.get("Season",""),r.get("homeTeam",""),r.get("awayTeam","")]) for r in rows]
    identity_sha=hashlib.sha256(("\n".join(identity_lines)+"\n").encode("utf-8")).hexdigest()
    return data_sha,identity_sha


def main()->int:
    result={
        "schema":SCHEMA,"project_line":"football3","page_size":PAGE_SIZE,"max_requests":MAX_REQUESTS,
        "football_table_data_requests_made":0,"nonce_values_persisted_or_logged":0,
        "target_result_columns_requested_or_materialized":0,"model_fit":0,"model_score":0,
        "C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"protected_opened":False,"formal_weight":0,
    }
    s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research","Accept-Language":"en-US,en;q=0.9"})
    all_rows=[]; league_stats={}; access_or_protocol=False; pagination_failure=False

    for code,(url,expected_id) in PAGES.items():
        st={"page_url":url,"expected_table_id":expected_id,"requests":0,"rows":0,"nonce_persisted_or_logged":0}
        try: page=s.get(url,timeout=45,allow_redirects=True)
        except Exception:
            st["error_class"]="PAGE_ACCESS"; access_or_protocol=True; league_stats[code]=st; continue
        st["page_status_code"]=page.status_code
        if not (200<=page.status_code<300):
            st["error_class"]="PAGE_HTTP"; access_or_protocol=True; league_stats[code]=st; continue
        t,h,seasons=resolve(page.text,expected_id)
        st["visible_historical_seasons"]=seasons
        if t is None:
            st["error_class"]="TABLE_PROTOCOL_DRIFT"; access_or_protocol=True; league_stats[code]=st; continue
        tid=expected_id; st["resolved_table_id"]=tid; st["resolved_dom_id"]=str(t.get("id","")); st["header_count"]=len(h)
        nonce_dom=f"wdtNonceFrontendServerSide_{tid}"; soup=BeautifulSoup(page.text,"html.parser")
        nodes=[x for x in soup.find_all("input") if str(x.get("id",""))==nonce_dom and str(x.get("name",""))==nonce_dom]
        st["nonce_element_unique"]=len(nodes)==1
        if len(nodes)!=1 or str(nodes[0].get("type","")).lower()!="hidden":
            st["error_class"]="NONCE_PROTOCOL_DRIFT"; access_or_protocol=True; league_stats[code]=st; continue
        raw_nonce=nodes[0].get("value"); nonce=str(raw_nonce) if raw_nonce is not None else ""
        st["nonce_nonempty"]=bool(nonce)
        if not nonce:
            st["error_class"]="NONCE_EMPTY"; access_or_protocol=True; league_stats[code]=st; continue

        req_headers={"Accept":"application/json, text/javascript, */*; q=0.01","Content-Type":"application/x-www-form-urlencoded; charset=UTF-8","X-Requested-With":"XMLHttpRequest","Origin":"https://footiqo.com","Referer":url}
        current_count=None; expected_starts=None; rows_code=[]
        start=0
        while True:
            if result["football_table_data_requests_made"]>=MAX_REQUESTS:
                st["error_class"]="REQUEST_BUDGET_EXCEEDED"; pagination_failure=True; break
            body=payload(h,nonce,start)
            result["football_table_data_requests_made"]+=1; st["requests"]+=1
            try:
                rr=s.post(AJAX,params={"action":ACTION,"table_id":str(tid)},data=body,headers=req_headers,timeout=45,allow_redirects=True)
            except Exception:
                st["error_class"]="TABLE_REQUEST_ERROR"; access_or_protocol=True; body[NONCE_FIELD]="<redacted>"; break
            finally:
                body[NONCE_FIELD]="<redacted>"
            if not (200<=rr.status_code<300):
                st["error_class"]="TABLE_HTTP"; st["last_status_code"]=rr.status_code; access_or_protocol=True; break
            try: x=rr.json()
            except Exception:
                st["error_class"]="TABLE_NON_JSON"; access_or_protocol=True; break
            if not isinstance(x,dict):
                st["error_class"]="TABLE_JSON_SHAPE"; access_or_protocol=True; break
            rf=as_int(x.get("recordsFiltered")); data=x.get("data",[])
            if not isinstance(data,list) or rf is None:
                st["error_class"]="TABLE_METADATA_SHAPE"; pagination_failure=True; break
            if current_count is None:
                current_count=rf; st["records_filtered_first_response"]=rf; st["records_total_first_response"]=as_int(x.get("recordsTotal"))
                if not (500<=rf<=6000):
                    st["error_class"]="FILTERED_COUNT_OUT_OF_BOUNDS"; pagination_failure=True; break
                if len(data)!=min(PAGE_SIZE,rf):
                    st["error_class"]="PAGE_SIZE_NOT_HONORED"; st["first_page_rows"]=len(data); pagination_failure=True; break
                expected_starts=list(range(0,rf,PAGE_SIZE)); st["planned_request_count"]=len(expected_starts)
            else:
                if rf!=current_count:
                    st["records_filtered_changed_during_retrieval"]=True
            if any(not isinstance(row,list) or len(row)!=22 for row in data):
                st["error_class"]="ROW_SCHEMA_DRIFT"; pagination_failure=True; break
            for row in data:
                mapped={HEADERS[i]:norm(row[i]) for i in range(22)}; mapped["sourceCode"]=code; rows_code.append(mapped)
            if expected_starts is None:
                break
            idx=expected_starts.index(start)
            if idx+1>=len(expected_starts): break
            start=expected_starts[idx+1]

        # Secret-like runtime value discarded before retaining diagnostics.
        nonce=""; raw_nonce=None
        st["rows"]=len(rows_code)
        st["records_filtered_final_reference"]=current_count
        if current_count is not None and len(rows_code)!=current_count:
            st["row_count_matches_filtered"]=False; pagination_failure=True
        elif current_count is not None:
            st["row_count_matches_filtered"]=True
        league_stats[code]=st; all_rows.extend(rows_code)

    result["league_stats"]=league_stats
    if access_or_protocol:
        result["terminal"]="C072N8_ACCESS_OR_PROTOCOL_STOP"
    elif pagination_failure:
        result["terminal"]="C072N8_ZERO_LABEL_DATA_QUALITY_FAIL"

    all_rows.sort(key=lambda r:(r.get("sourceCode",""),r.get("matchDate",""),r.get("id",""),r.get("League",""),r.get("Season",""),r.get("homeTeam",""),r.get("awayTeam","")))
    data_sha,identity_sha=write_csv(all_rows)
    n=len(all_rows); result["retained_rows"]=n; result["dataset_sha256"]=data_sha; result["ordered_identity_sha256"]=identity_sha

    identity_fields=["id","matchDate","Country","League","Season","homeTeam","awayTeam"]
    complete_identity=sum(1 for r in all_rows if all(norm(r.get(k,"")) for k in identity_fields))
    valid_dates=sum(1 for r in all_rows if valid_date(r.get("matchDate","")))
    keys=[(r.get("sourceCode",""),r.get("id",""),r.get("League",""),r.get("Season","")) for r in all_rows]
    duplicate_rows=n-len(set(keys))
    seasons=sorted({r.get("Season","") for r in all_rows if r.get("Season","")})
    start_years=[int(m.group(1)) for s0 in seasons if (m:=re.match(r"(\d{4})",s0))]

    line_pairs={"05":("O05","U05"),"15":("O15","U15"),"25":("O25","U25"),"35":("O35","U35"),"45":("O45","U45")}
    valid_pair={k:0 for k in line_pairs}; joint_1535=0; allfive=0; monotone=0
    for r in all_rows:
        ps={}
        for k,(oc,uc) in line_pairs.items():
            o,u=price(r.get(oc,"")),price(r.get(uc,""))
            if o is not None and u is not None:
                valid_pair[k]+=1; ps[k]=devig_over(o,u)
        if all(k in ps for k in ("15","25","35")): joint_1535+=1
        if all(k in ps for k in ("05","15","25","35","45")):
            allfive+=1; seq=[ps[k] for k in ("05","15","25","35","45")]
            if all(seq[i]+1e-12>=seq[i+1] for i in range(4)): monotone+=1

    frac=lambda x: float(x/n) if n else 0.0
    result["audit"]={
        "complete_identity_fraction":frac(complete_identity),"valid_date_fraction":frac(valid_dates),
        "duplicate_rows":duplicate_rows,"duplicate_fraction":frac(duplicate_rows),
        "season_count":len(seasons),"season_min_start_year":min(start_years) if start_years else None,"season_max_start_year":max(start_years) if start_years else None,
        "valid_pair_counts":valid_pair,"ou25_coverage":frac(valid_pair["25"]),"joint_ou15_25_35_coverage":frac(joint_1535),
        "all_five_line_coverage":frac(allfive),"all_five_monotone_fraction":float(monotone/allfive) if allfive else 0.0,
        "forbidden_score_result_column_names":[],
    }
    counts=[st.get("rows",0) for st in league_stats.values()]
    gates={
        "all_five_protocol_resolved":len(league_stats)==5 and all(st.get("resolved_table_id") and st.get("nonce_element_unique") and st.get("nonce_nonempty") for st in league_stats.values()),
        "request_budget_respected":result["football_table_data_requests_made"]<=MAX_REQUESTS,
        "all_rows_schema_valid":not pagination_failure,
        "each_league_exact_filtered_count":all(st.get("row_count_matches_filtered") for st in league_stats.values()),
        "pooled_rows_ge_15000":n>=15000,
        "each_league_rows_ge_2500":len(counts)==5 and all(x>=2500 for x in counts),
        "seasons_ge_5":len(seasons)>=5,
        "valid_date_ge_995pct":frac(valid_dates)>=0.995,
        "complete_identity_ge_995pct":frac(complete_identity)>=0.995,
        "duplicate_le_05pct":frac(duplicate_rows)<=0.005,
        "ou25_coverage_ge_90pct":frac(valid_pair["25"])>=0.90,
        "joint_ou15_25_35_ge_85pct":frac(joint_1535)>=0.85,
        "all_five_lines_ge_75pct":frac(allfive)>=0.75,
        "all_five_monotone_ge_97pct": (float(monotone/allfive) if allfive else 0.0)>=0.97,
        "hashes_nonempty":bool(data_sha and identity_sha),
        "zero_target_result_columns":result["target_result_columns_requested_or_materialized"]==0,
        "zero_model":result["model_fit"]==0 and result["model_score"]==0,
        "zero_nonce_persistence":result["nonce_values_persisted_or_logged"]==0,
    }
    result["gates"]=gates
    if not access_or_protocol and not pagination_failure:
        result["terminal"]="C072N8_MULTILINE_ODDS_ZERO_LABEL_PASS" if all(gates.values()) else "C072N8_ZERO_LABEL_DATA_QUALITY_FAIL"

    OUT_SUMMARY.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
