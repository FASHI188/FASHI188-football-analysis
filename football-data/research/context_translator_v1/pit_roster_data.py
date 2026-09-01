from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import source_ingest as si

SPORTSMOLE_SITEMAPS = (
    "https://www.sportsmole.co.uk/sitemap-articles-2023.xml",
    "https://www.sportsmole.co.uk/sitemap-articles-2024.xml",
)
UA = {"User-Agent": "football3-research-pit-roster/1.0 (+public reproducible research)"}
MAX_ARTICLE_AGE_DAYS = 10.0

TEAM_ALIASES = {
    "1 fc koln": ("fc-koln", "koln"),
    "vfl bochum 1848": ("bochum",),
    "sv darmstadt 98": ("darmstadt",),
    "1 fc union berlin": ("union-berlin",),
    "vfb stuttgart": ("stuttgart",),
    "eintracht frankfurt": ("eintracht-frankfurt", "frankfurt"),
    "tsg 1899 hoffenheim": ("hoffenheim",),
    "1 fc heidenheim 1846": ("heidenheim",),
    "borussia dortmund": ("borussia-dortmund", "dortmund"),
    "fc augsburg": ("augsburg",),
    "vfl wolfsburg": ("wolfsburg",),
    "sc freiburg": ("freiburg",),
    "fc bayern munchen": ("bayern-munich", "bayern"),
    "borussia monchengladbach": ("borussia-monchengladbach", "monchengladbach"),
    "rb leipzig": ("rb-leipzig", "leipzig"),
    "sv werder bremen": ("werder-bremen", "bremen"),
    "1 fsv mainz 05": ("mainz-05", "mainz"),
    "bayer 04 leverkusen": ("bayer-leverkusen", "leverkusen"),
}

class PITRosterError(RuntimeError):
    pass

def dt(text: str) -> datetime:
    d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None: raise PITRosterError("timezone required")
    return d.astimezone(timezone.utc)
def iso(d: datetime) -> str: return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def canon(obj: Any) -> str: return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))

def fetch(url: str, timeout: int = 45) -> tuple[bytes, dict[str, str]]:
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.read(), dict(r.headers)
        except Exception as exc:
            last = exc; time.sleep(min(2 ** i, 8))
    raise PITRosterError(f"public source fetch failed: {url}: {last}")

def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</p>|</div>|</li>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    lines = [re.sub(r"\s+", " ", html.unescape(x)).strip() for x in fragment.splitlines()]
    return "\n".join(x for x in lines if x)

def publication_time(page: str) -> datetime | None:
    patterns = [
        r'(?is)"datePublished"\s*:\s*"([^"]+)"',
        r'(?is)<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
    ]
    for p in patterns:
        for value in re.findall(p, page):
            try: return dt(value)
            except Exception: pass
    return None

def last_updated_time(page: str) -> datetime | None:
    for p in [r'(?is)"dateModified"\s*:\s*"([^"]+)"',r'(?is)<meta[^>]+property=["\']article:modified_time["\'][^>]+content=["\']([^"\']+)']:
        for value in re.findall(p, page):
            try: return dt(value)
            except Exception: pass
    return None

def extract_team_news(page: str) -> tuple[str, str] | None:
    m = re.search(r"(?is)<h2[^>]*>\s*Team News\s*</h2>(.*?)(?=<h2[^>]*>)", page)
    if not m: return None
    raw = m.group(1); text = strip_tags(raw)
    return (raw, text) if text else None

def team_aliases(name: str) -> tuple[str, ...]:
    n = norm(name)
    if n in TEAM_ALIASES: return TEAM_ALIASES[n]
    toks = [x for x in n.split() if x not in {"1","fc","sc","sv","vfl","vfb","tsg","1899","1846","1848","05","04"}]
    return ("-".join(toks),) if toks else ()

def discover_urls() -> tuple[list[str], list[dict[str, Any]]]:
    urls=[]; receipts=[]
    for u in SPORTSMOLE_SITEMAPS:
        b,_=fetch(u); root=ET.fromstring(b); locs=[e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        previews=[x for x in locs if "/football/" in x and "/preview/" in x and x.endswith(".html")]
        urls.extend(previews); receipts.append({"url":u,"sha256":sha_bytes(b),"bytes":len(b),"all_urls":len(locs),"preview_urls":len(previews)})
    return sorted(set(urls)),receipts

def candidate_urls(urls: list[str], home: str, away: str) -> list[str]:
    ha,aa=team_aliases(home),team_aliases(away); out=[]
    for u in urls:
        base=u.rsplit("/",1)[-1].lower()
        if any(x in base for x in ha) and any(x in base for x in aa): out.append(u)
    return out

def parse_lineups(text: str, home: str, away: str) -> tuple[list[str], list[str]] | None:
    matches=list(re.finditer(r"(?i)([A-ZÀ-ÖØ-öø-ÿ0-9 .&'’\-]{2,50})\s+possible starting lineup:\s*",text))
    if len(matches)<2:return None
    parsed=[]
    for i,m in enumerate(matches[:2]):
        end=matches[i+1].start() if i+1<len(matches) else len(text);chunk=text[m.end():end].strip();chunk=re.split(r"(?i)\bWe say:|\bData Analysis\b|\bWhat is your prediction\b",chunk)[0]
        names=[re.sub(r"\s+"," ",x).strip(" .") for x in re.split(r"[;,]",chunk) if re.sub(r"\s+"," ",x).strip(" .")]
        if len(names)<11:return None
        parsed.append(names[:11])
    return parsed[0],parsed[1]

def build_identity_index(safe_index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ger=[x for x in safe_index if x.get("v2_competition_id")=="GER1" and x.get("v2_season")=="2023/24"]
    by_team=defaultdict(lambda:{"players":{},"aliases":defaultdict(set),"source_team_ids":set(),"source_team_names":set()})
    for s in ger:
        lineups=json.loads(si._get(f"data/lineups/{int(s['match_id'])}.json"))
        for team in lineups:
            tname=str(team.get("team_name") or "");key=si._canon_team(tname);z=by_team[key];z["source_team_ids"].add(int(team["team_id"]));z["source_team_names"].add(tname)
            for p in team.get("lineup") or []:
                pid=str(p["player_id"]);pname=str(p.get("player_name") or p.get("player_nickname") or "")
                if not pname:continue
                z["players"][pid]=pname;nn=norm(pname);z["aliases"][nn].add(pid);toks=nn.split()
                if toks:z["aliases"][toks[-1]].add(pid)
                if p.get("player_nickname"):z["aliases"][norm(str(p["player_nickname"]))].add(pid)
    return by_team

def resolve_team_key(v2_team_name: str, identity_index: dict[str, dict[str, Any]]) -> str | None:
    target=si._canon_team(v2_team_name)
    if target in identity_index:return target
    candidates=[k for k in identity_index if target and (target in k or k in target)]
    return candidates[0] if len(candidates)==1 else None

def resolve_player(name: str, team_key: str, identity_index: dict[str, dict[str, Any]]) -> tuple[str | None, str]:
    z=identity_index.get(team_key)
    if not z:return None,"TEAM_IDENTITY_MISSING"
    n=norm(name);candidates=set(z["aliases"].get(n,set()));toks=n.split()
    if not candidates and toks:candidates=set(z["aliases"].get(toks[-1],set()))
    if len(candidates)==1:return next(iter(candidates)),"MATCHED"
    if len(candidates)>1:return None,"AMBIGUOUS_PLAYER_IDENTITY"
    return None,"PLAYER_IDENTITY_MISSING"

def status_records(text: str, home_key: str, away_key: str, identity_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out=[]
    for sent in re.split(r"(?<=[.!?])\s+",text):
        low=sent.lower()
        if not any(k in low for k in ("injur","sidelined","miss out","ruled out","without","illness","suspens","suspended","doubt")):continue
        stype="SUSPENSION" if ("suspens" in low or "suspended" in low) else "INJURY_OR_AVAILABILITY"
        for team_key in (home_key,away_key):
            z=identity_index.get(team_key)
            if not z:continue
            for pid,pname in z["players"].items():
                nn=norm(pname);surname=nn.split()[-1] if nn.split() else ""
                if len(surname)>=4 and re.search(rf"\b{re.escape(surname)}\b",norm(sent)):out.append({"player_id":pid,"player_name":pname,"team_key":team_key,"status_type":stype,"source_semantics":"PREMATCH_TEAM_NEWS_TEXT"})
    return list({(x["player_id"],x["status_type"]):x for x in out}.values())

def collect(v2_artifact: Path, candidate_artifact: Path, statsbomb_source: Path, out: Path, max_fixtures: int=100) -> dict[str, Any]:
    out.mkdir(parents=True,exist_ok=True);preds=[json.loads(x) for x in (candidate_artifact/"candidate_b_predictions.jsonl").read_text().splitlines() if x.strip()];ids=sorted(str(x["fixture_id"]) for x in preds)[:max_fixtures]
    if len({str(x["fixture_id"]) for x in preds})!=272 or len(ids)!=min(max_fixtures,272):raise PITRosterError("mechanical GER1-272 inventory identity failed")
    ev=[json.loads(x) for x in (v2_artifact/"dataset/evaluation_features.jsonl").read_text().splitlines() if x.strip()];evmap={str(x["fixture_id"]):x for x in ev}
    if not set(ids)<=set(evmap):raise PITRosterError("mechanical inventory missing evaluation features")
    safe=json.loads((statsbomb_source/"safe_match_index.json").read_text());identity_index=build_identity_index(safe);urls,sitemap_receipts=discover_urls();collected_at=datetime.now(timezone.utc);packets=[];missing=defaultdict(int);total_names=matched_names=status_n=0
    for fid in ids:
        r=evmap[fid];kickoff=dt(r["cutoff"]);home=str(r["home_team"]);away=str(r["away_team"]);hkey=resolve_team_key(home,identity_index);akey=resolve_team_key(away,identity_index);base={"fixture_id":fid,"competition_id":str(r["competition_id"]),"season":str(r["season"]),"home_team_id":str(r["home_team_id"]),"away_team_id":str(r["away_team_id"]),"home_team":home,"away_team":away,"kickoff_utc":iso(kickoff),"selection_rule":"FIRST_100_FIXTURE_ID_ASC_FROM_ALREADY_POST_VIEW_GER1_272"}
        if not hkey or not akey:packets.append({**base,"pit_legal":False,"missing_reason":"TEAM_IDENTITY_CONFLICT"});missing["TEAM_IDENTITY_CONFLICT"]+=1;continue
        cands=candidate_urls(urls,home,away);eligible=[]
        for u in cands:
            try:
                b,_=fetch(u);page=b.decode("utf-8","replace");pub=publication_time(page)
                if pub is None or not pub<kickoff:continue
                age=(kickoff-pub).total_seconds()/86400.0
                if age<=0 or age>MAX_ARTICLE_AGE_DAYS:continue
                sec=extract_team_news(page)
                if sec is None:continue
                raw_fragment,text=sec;lu=parse_lineups(text,home,away)
                if lu is None:continue
                eligible.append((pub,u,page,raw_fragment,text,lu))
            except Exception:continue
        if not eligible:reason="NO_PRE_KICKOFF_PREVIEW_WITH_PARSEABLE_MODAL_XI";packets.append({**base,"pit_legal":False,"missing_reason":reason,"candidate_url_n":len(cands)});missing[reason]+=1;continue
        eligible.sort(key=lambda x:(x[0],x[1]),reverse=True);pub,u,page,raw_fragment,text,(hnames,anames)=eligible[0];home_players=[];away_players=[];identity_ok=True
        for side_names,team_key,dest in ((hnames,hkey,home_players),(anames,akey,away_players)):
            for name in side_names:
                total_names+=1;pid,state=resolve_player(name,team_key,identity_index);matched_names+=int(pid is not None);identity_ok &= pid is not None;dest.append({"source_name":name,"player_id":pid,"identity_status":state,"lineup_type":"PREDICTED_POSSIBLE_XI","starting_probability":None,"expected_minutes":None,"position":None})
        statuses=status_records(text,hkey,akey,identity_index);status_n+=len(statuses);packet={**base,"pit_legal":True,"source":{"provider":"Sports Mole","source_url":u,"source_id":u.rsplit("_",1)[-1].replace(".html",""),"source_observed_at":iso(collected_at),"collected_at":iso(collected_at),"available_at":iso(pub),"article_last_updated_at":iso(last_updated_time(page)) if last_updated_time(page) else None,"raw_content_scope":"EXACT_H2_TEAM_NEWS_SECTION_ONLY","raw_content_sha256":sha_bytes(raw_fragment.encode()),"full_page_not_persisted":True,"source_tier":"TIER_3_PUBLIC_PREMATCH_ARTICLE"},"predicted_lineups":{"home":home_players,"away":away_players},"confirmed_lineups":None,"bench":None,"status_records":statuses,"coach":None,"formation":None,"probability_contract":"NO_SOURCE_PLAYER_START_PROBABILITIES_DO_NOT_INVENT","identity_complete":identity_ok};packet["packet_sha256"]=canon(packet);packets.append(packet)
        if not identity_ok:missing["PREDICTED_XI_PLAYER_IDENTITY_INCOMPLETE"]+=1
    pf=out/"pit_roster_packets.jsonl";pf.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in packets));legal=[x for x in packets if x.get("pit_legal")];predicted=[x for x in legal if x.get("predicted_lineups")];complete=[x for x in legal if x.get("identity_complete")]
    audit={"schema_version":"football3-pit-roster-inventory-audit-v1","status":"RESEARCH_ONLY_POST_VIEW_DATA_ACQUISITION","selection_rule":"FIRST_100_FIXTURE_ID_ASC_FROM_ALREADY_POST_VIEW_GER1_272","inventory_n":len(ids),"inventory_fixture_ids":ids,"inventory_fixture_set_sha256":canon(ids),"pit_legal_roster_packet_n":len(legal),"predicted_lineup_fixture_n":len(predicted),"predicted_lineup_side_n":2*len(predicted),"confirmed_lineup_fixture_n":0,"injury_suspension_status_record_n":status_n,"bench_fixture_n":0,"player_identity_attempt_n":total_names,"player_identity_matched_n":matched_names,"player_identity_match_rate":None if not total_names else matched_names/total_names,"identity_complete_packet_n":len(complete),"missing_reasons":dict(sorted(missing.items())),"source_url_complete_rate":None if not legal else sum(bool(x["source"]["source_url"]) for x in legal)/len(legal),"timestamp_complete_rate":None if not legal else sum(bool(all(x["source"].get(k) for k in ("source_observed_at","collected_at","available_at")) and x.get("kickoff_utc")) for x in legal)/len(legal),"confirmed_lineup_source_used":False,"postmatch_result_fields_persisted":False,"t15_cutoff_used":False,"pit_rule":"available_at < kickoff_utc; no T-15 claim","sitemap_receipts":sitemap_receipts,"packet_payload_sha256":sha_bytes(pf.read_bytes())};(out/"pit_roster_inventory_audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2,sort_keys=True)+"\n");return audit
