#!/usr/bin/env python3
"""V6.46.3r3 reverse-engineer K League official audience-result AJAX contract.
Research diagnostic only; no settlement is written here.
"""
from __future__ import annotations
import html, json, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"manifests"/"v6_kleague_official_result_source_diag_v6463_status.json"
URL="https://www.kleague.com/record/audienceDetail.do"
TARGETS=[("2026/07/25","김천","대전"),("2026/07/25","포항","전북"),("2026/07/26","안양","강원"),("2026/07/26","광주","제주"),("2026/07/26","인천","부천"),("2026/07/26","서울","울산")]

def fetch()->str:
    req=urllib.request.Request(URL,headers={"User-Agent":"Mozilla/5.0 (compatible; FASHI188-football-audit/1.0)","Accept-Language":"ko-KR,ko;q=0.9"})
    return urllib.request.urlopen(req,timeout=20).read().decode("utf-8",errors="replace")

def clean(s:str)->str:
    s=re.sub(r"<[^>]+>"," ",s)
    return re.sub(r"\s+"," ",html.unescape(s)).strip()

def main()->int:
    raw=fetch()
    rows=[clean(x) for x in re.findall(r"<tr\b[^>]*>(.*?)</tr>",raw,re.I|re.S)]
    table=[]
    for date,home,away in TARGETS:
        hit=[]
        for row in rows:
            if date in row and home in row and away in row:
                m=re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)",row)
                hit.append({"row":row[:1000],"score":[int(m.group(1)),int(m.group(2))] if m else None})
        table.append({"date":date,"home":home,"away":away,"rows":hit})
    ajax_urls=sorted(set(re.findall(r"url\s*:\s*['\"]([^'\"]+)['\"]",raw,re.I)))
    function_contexts=[]
    for token in ("function goToPageAudience","goToPageAudience =","goToPageAudience:"):
        pos=raw.find(token)
        if pos>=0:
            function_contexts.append(re.sub(r"\s+"," ",html.unescape(raw[max(0,pos-300):min(len(raw),pos+9000)])))
    if not function_contexts:
        # Fall back to first AJAX block that renders audience-table-body.
        pos=raw.find('$("#audience-table-body")')
        if pos>=0:
            function_contexts.append(re.sub(r"\s+"," ",html.unescape(raw[max(0,pos-5000):min(len(raw),pos+7000)])))
    payload={
      "schema_version":"V6.46.3-kleague-official-result-source-diag-r3",
      "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      "status":"PASS_DIAGNOSTIC",
      "official_source":URL,
      "base_table_matches":table,
      "ajax_urls":ajax_urls,
      "function_contexts":function_contexts[:3],
      "governance":{"settlement_written":False,"third_party_result_fallback_used":False,"formal_weight_change":False,"runtime_probability_change":False,"current_rule_change":False}
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"ajax_urls":ajax_urls,"function_context_count":len(function_contexts),"official_rows":sum(bool(x['rows']) for x in table)},ensure_ascii=False))
    return 0
if __name__=="__main__": raise SystemExit(main())
