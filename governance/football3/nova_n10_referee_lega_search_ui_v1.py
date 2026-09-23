#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, ssl, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

class DiscoveryError(RuntimeError): pass
def req(c: bool, m: str) -> None:
    if not c: raise DiscoveryError(m)
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def fetch(url: str, timeout: int, limit: int, accept: str, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_URL")
    rq=urllib.request.Request(url,headers={"User-Agent":"Football3-Nova-N10-LegaSearchUI/1.0","Accept":accept})
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl(); req(domain_ok(final,suffix),"REDIRECT_OUTSIDE_OFFICIAL")
        data=r.read(limit+1); req(len(data)<=limit,"RESPONSE_TOO_LARGE")
        return data,final,{k.lower():v for k,v in r.headers.items()}

class StructureParser(HTMLParser):
    def __init__(self, allowed_attrs: set[str]):
        super().__init__(convert_charrefs=True)
        self.allowed_attrs=allowed_attrs
        self.forms=[]
        self.inputs=[]
        self.buttons=[]
        self.scripts=[]
    def _attrs(self,attrs):
        return {k:v for k,v in attrs if k in self.allowed_attrs and v is not None}
    def handle_starttag(self,tag,attrs):
        a=self._attrs(attrs)
        if tag=="form": self.forms.append(a)
        elif tag=="input": self.inputs.append(a)
        elif tag=="button": self.buttons.append(a)
        elif tag=="script" and "src" in a: self.scripts.append(a)
    def handle_data(self,data):
        # Intentionally ignore all text nodes, including search-result text.
        return

def normalize_scripts(rows: list[dict[str,str]], base: str, suffix: str) -> list[str]:
    out=[]
    for row in rows:
        src=row.get("src")
        if not src: continue
        u=urllib.parse.urljoin(base,src)
        if domain_ok(u,suffix): out.append(u)
    return sorted(dict.fromkeys(out))

GET_RE=re.compile(r'''(?:searchParams|URLSearchParams|\.get)\s*[^;]{0,120}?\.get\(\s*["']([A-Za-z0-9_.-]{1,80})["']\s*\)|\.get\(\s*["']([A-Za-z0-9_.-]{1,80})["']\s*\)''')
SET_RE=re.compile(r'''\.set\(\s*["']([A-Za-z0-9_.-]{1,80})["']\s*,''')
SEARCH_Q_RE=re.compile(r'''/search\?([^"'\x60\s]{1,240})''',re.I)
QUERY_KEY_RE=re.compile(r'''(?:^|[?&])([A-Za-z0-9_.-]{1,80})=''')

def script_contract(raw: bytes) -> dict[str,Any]:
    s=raw.decode("utf-8","replace")
    params=set()
    for m in GET_RE.finditer(s):
        params.add(m.group(1) or m.group(2))
    for m in SET_RE.finditer(s):
        params.add(m.group(1))
    fragments=[]
    for m in SEARCH_Q_RE.finditer(s):
        f=m.group(1)[:240]
        fragments.append(f)
        for q in QUERY_KEY_RE.finditer("?"+f):
            params.add(q.group(1))
    relevant_context_n=sum(s.count(x) for x in ("URLSearchParams","searchParams","/search?"))
    return {
        "parameter_names":sorted(params),
        "search_query_fragments":sorted(dict.fromkeys(fragments))[:100],
        "relevant_context_marker_n":relevant_context_n,
    }

def select_route_scripts(scripts: list[str], preferred: list[str], max_n: int) -> list[str]:
    exact=[u for u in scripts if "/app/search/" in urllib.parse.unquote(u).casefold()]
    if exact: return exact[:max_n]
    fallback=[u for u in scripts if any(t.casefold() in urllib.parse.unquote(u).casefold() for t in preferred)]
    return fallback[:max_n]

def form_parameter_names(forms: list[dict[str,str]], inputs: list[dict[str,str]]) -> list[str]:
    # Attribute-only inference: any explicit input name is a mechanical candidate.
    return sorted(dict.fromkeys(x["name"] for x in inputs if x.get("name")))

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="cb6c94ea0ee13a1fb00820d31e1356ee83ea13f1","EXACT_BASE")
    h=p["hard_rules"]; hc=p["html_contract"]; sc=p["script_contract"]; src=p["source"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["search_result_text_read"] is False and h["search_result_links_read"] is False and h["article_body_read"] is False,"NO_RESULT_CONTENT")
    req(h["parameter_guessing_allowed"] is False and h["hidden_endpoint_bruteforce_allowed"] is False,"NO_GUESSING")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(hc["text_nodes_parsed"] is False and hc["search_result_text_read"] is False and hc["search_result_links_read"] is False,"TAG_ONLY")
    req(sc["common_bundle_scan_allowed"] is False and sc["network_calls_using_discovered_parameters"] is False,"STATIC_ONLY")

    html,final,headers=fetch(src["search_url"],timeout,int(hc["max_html_bytes"]),"text/html,application/xhtml+xml",src["allowed_domain_suffix"])
    parser=StructureParser(set(hc["allowed_attributes"]))
    parser.feed(html.decode("utf-8","replace"))
    scripts=normalize_scripts(parser.scripts,final,src["allowed_domain_suffix"])
    selected=select_route_scripts(scripts,sc["route_specific_script_preferred"],int(sc["max_script_n"]))

    form_params=form_parameter_names(parser.forms,parser.inputs)
    script_reports=[]; script_params=set(); errors=[]
    for u in selected:
        try:
            raw,sfinal,sheaders=fetch(u,timeout,int(sc["max_script_bytes_each"]),"application/javascript,text/javascript,*/*;q=0.2",src["allowed_domain_suffix"])
            c=script_contract(raw)
            script_params.update(c["parameter_names"])
            script_reports.append({
                "source_url":u,"final_url":sfinal,"sha256":sha256_bytes(raw),"bytes":len(raw),
                **c,"content_type":sheaders.get("content-type")
            })
        except Exception as e:
            errors.append({"stage":"script","url":u,"error":f"{type(e).__name__}:{e}"[:400]})

    mechanical_params=sorted(set(form_params)|script_params)
    form_contracts=[]
    for f in parser.forms:
        action=urllib.parse.urljoin(final,f.get("action","")) if f.get("action") else None
        if action and not domain_ok(action,src["allowed_domain_suffix"]): action=None
        form_contracts.append({"action":action,"method":(f.get("method") or "GET").upper()})
    positive=bool(mechanical_params or any(x.get("action") for x in form_contracts))
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if positive else "STOP_DATA_COVERAGE"

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-lega-search-ui-receipt-v1",
        "status":"N10_REFEREE_LEGA_SEARCH_UI_CONTRACT_COMPLETE","classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
        "search_url":src["search_url"],"final_url":final,"html_sha256":sha256_bytes(html),"html_bytes":len(html),"content_type":headers.get("content-type"),
        "form_n":len(parser.forms),"input_n":len(parser.inputs),"button_n":len(parser.buttons),"script_src_n":len(scripts),
        "form_contracts":form_contracts,"input_attribute_rows":parser.inputs,"mechanical_parameter_names":mechanical_params,
        "selected_route_script_n":len(selected),"selected_route_scripts":selected,"script_reports":script_reports,
        "error_n":len(errors),"errors":errors,
        "text_nodes_parsed":False,"search_result_text_read":False,"search_result_links_read":False,"article_body_read":False,
        "network_calls_using_discovered_parameters":False,"parameter_guessing":False,
        "match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"full_big5_data_ready":False,"referee_oof_allowed":False,
        "next_step":(
            "IF_QUERY_PARAMETER_MECHANICALLY_RESOLVED_FREEZE_EXACT_DESIGNAZIONI_QUERY_IN_NEW_BATCH"
            if positive else
            "STOP_SEARCH_UI_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        )
    }
    (out/"lega_search_ui_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True)); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument("--registry",type=Path,required=True); a.add_argument("--out",type=Path,required=True); a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args(); run(x.registry,x.out,x.timeout)
if __name__=="__main__": main()
