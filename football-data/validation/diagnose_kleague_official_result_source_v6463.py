#!/usr/bin/env python3
"""V6.46.3r4 reverse-engineer K League official audience-result AJAX contract.
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

def extract_function(raw:str,name:str)->str|None:
    start=raw.find(f"function {name}")
    if start<0:return None
    brace=raw.find("{",start)
    if brace<0:return None
    depth=0;quote=None;escape=False
    for i in range(brace,len(raw)):
        ch=raw[i]
        if quote:
            if escape:escape=False
            elif ch=="\\":escape=True
            elif ch==quote:quote=None
            continue
        if ch in ("'",'"','`'):quote=ch;continue
        if ch=="{":depth+=1
        elif ch=="}":
            depth-=1
            if depth==0:
                return re.sub(r"\s+"," ",html.unescape(raw[start:i+1])).strip()
    return None

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
    funcs={name:extract_function(raw,name) for name in ("updateAudience","goToPageAudience")}
    # Extract current HTML select defaults/values so the AJAX request can be reconstructed exactly.
    selects={}
    for sid in ("year","leagueId","teamId","selectType"):
        m=re.search(rf"<select\b[^>]*id=['\"]{re.escape(sid)}['\"][^>]*>(.*?)</select>",raw,re.I|re.S)
        if not m:
            # Some controls may be hidden inputs.
            im=re.search(rf"<input\b[^>]*id=['\"]{re.escape(sid)}['\"][^>]*>",raw,re.I|re.S)
            if im:
                vm=re.search(r"value=['\"]([^'\"]*)['\"]",im.group(0),re.I)
                selects[sid]={"kind":"input","value":vm.group(1) if vm else None}
            continue
        opts=[]
        for om in re.finditer(r"<option\b([^>]*)>(.*?)</option>",m.group(1),re.I|re.S):
            attrs,label=om.group(1),clean(om.group(2))
            vm=re.search(r"value=['\"]([^'\"]*)['\"]",attrs,re.I)
            opts.append({"value":vm.group(1) if vm else label,"label":label,"selected":bool(re.search(r"\bselected\b",attrs,re.I))})
        selects[sid]={"kind":"select","options":opts}
    payload={
      "schema_version":"V6.46.3-kleague-official-result-source-diag-r4",
      "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      "status":"PASS_DIAGNOSTIC",
      "official_source":URL,
      "base_table_matches":table,
      "ajax_urls":ajax_urls,
      "functions":funcs,
      "controls":selects,
      "governance":{"settlement_written":False,"third_party_result_fallback_used":False,"formal_weight_change":False,"runtime_probability_change":False,"current_rule_change":False}
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"ajax_urls":ajax_urls,"functions_found":{k:v is not None for k,v in funcs.items()},"controls":list(selects),"official_rows":sum(bool(x['rows']) for x in table)},ensure_ascii=False))
    return 0
if __name__=="__main__": raise SystemExit(main())
