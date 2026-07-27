#!/usr/bin/env python3
"""Extract the official K League audience AJAX contract needed by the result resolver."""
from __future__ import annotations
import html, json, re, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_kleague_ajax_contract_v6463b_status.json"
URL = "https://www.kleague.com/record/audienceDetail.do"

def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent":"Mozilla/5.0","Accept-Language":"ko-KR,ko;q=0.9"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", raw, flags=re.I|re.S)
    hits=[]
    for s in scripts:
        if "goToPageAudience" in s or ("audience" in s.lower() and "ajax" in s.lower()):
            s=html.unescape(re.sub(r"\s+"," ",s)).strip()
            hits.append(s[:30000])
    urls=sorted(set(re.findall(r"url\s*:\s*['\"]([^'\"]+)['\"]", raw, flags=re.I)))
    payload={
      "schema_version":"V6.46.3b-kleague-ajax-contract-r1",
      "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      "status":"PASS_DIAGNOSTIC",
      "source":URL,
      "ajax_urls":urls,
      "matching_scripts":hits,
      "governance":{"settlement_written":False,"third_party_result_fallback_used":False,"formal_weight_change":False}
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"ajax_urls":urls,"script_count":len(hits)},ensure_ascii=False))
    return 0
if __name__ == "__main__": raise SystemExit(main())
