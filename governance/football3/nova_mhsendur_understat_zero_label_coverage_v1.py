#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,io,json,re,urllib.request,zipfile
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path
from nova_mhsendur_understat_header_audit_v1 import git_blob_sha1,validate_lock

TARGET_LEAGUES=("Bundesliga","EPL","La_liga","Ligue_1","Serie_A")
MEMBER_RE=re.compile(r"^football_data_csv/(Bundesliga|EPL|La_liga|Ligue_1|Serie_A|RFPL)_(20\d{2})_(.+)\.csv$")
SAFE_COLUMNS=("h_a","ppda","ppda_allowed","deep","deep_allowed","date")
class CoverageError(ValueError): pass

def canonical(x): return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def sha256(x): return hashlib.sha256(x).hexdigest()
def fetch_bytes(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Football3-Nova-N1-Zero-Label-Coverage/1.0"})
    with urllib.request.urlopen(req,timeout=60) as r:return r.read()
def decode(raw):
    for enc in ("utf-8-sig","cp1252","latin-1"):
        try:return raw.decode(enc)
        except UnicodeDecodeError:pass
    raise CoverageError("unable to decode CSV member")
def num(v):
    t=(v or "").strip()
    try:d=Decimal(t)
    except InvalidOperation as e:raise CoverageError(f"invalid numeric feature {t!r}") from e
    if not t or not d.is_finite():raise CoverageError(f"invalid numeric feature {t!r}")
    s=format(d.normalize(),"f")
    if "." in s:s=s.rstrip("0").rstrip(".")
    return "0" if s in ("","-0") else s
def ident(member):
    m=MEMBER_RE.match(member);return m.groups() if m else None
def project(zf,member,league,season,team):
    with zf.open(member) as f: reader=csv.DictReader(io.StringIO(decode(f.read())))
    if not reader.fieldnames:raise CoverageError(f"{member}: missing header")
    names={x.strip().lower():x for x in reader.fieldnames}
    missing=[x for x in SAFE_COLUMNS if x not in names]
    if missing:raise CoverageError(f"{member}: missing safe columns {missing}")
    out=[]
    for i,row in enumerate(reader,2):
        ha=(row[names["h_a"]] or "").strip().lower(); date=(row[names["date"]] or "").strip()
        if ha not in ("h","a") or not date:raise CoverageError(f"{member}:{i}: invalid identity")
        out.append({"league":league,"season_start":season,"team":team,"date":date,"h_a":ha,
                    "ppda":num(row[names["ppda"]]),"ppda_allowed":num(row[names["ppda_allowed"]]),
                    "deep":num(row[names["deep"]]),"deep_allowed":num(row[names["deep_allowed"]])})
    return out
def psha(rows):
    stripped=[{k:v for k,v in r.items() if k!="team"} for r in rows]
    return sha256(b"\n".join(canonical(r) for r in stripped)+b"\n")
def reciprocal(a,b):
    return a["date"]==b["date"] and a["h_a"]!=b["h_a"] and a["ppda"]==b["ppda_allowed"] and a["ppda_allowed"]==b["ppda"] and a["deep"]==b["deep_allowed"] and a["deep_allowed"]==b["deep"]

def audit(lock,archive):
    validate_lock(lock); blob=git_blob_sha1(archive)
    if blob!=lock["source"]["archive_blob_sha1"]:raise CoverageError(f"archive blob SHA drift: {blob}")
    member_rows={}; meta={}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in sorted(zf.namelist()):
            x=ident(member)
            if not x or x[0] not in TARGET_LEAGUES:continue
            member_rows[member]=project(zf,member,*x);meta[member]=x
    if not member_rows:raise CoverageError("no target-league CSV members")
    lookup={(l,s,t):m for m,(l,s,t) in meta.items()}; logical=[]; dups=[]; conflicts=[]
    for m in sorted(member_rows):
        l,s,t=meta[m]
        if t.endswith(" 2") and (base:=lookup.get((l,s,t[:-2]))):
            if psha(member_rows[m])==psha(member_rows[base]):dups.append({"duplicate":m,"canonical":base});continue
            conflicts.append({"duplicate":m,"candidate_canonical":base})
        logical.append(m)
    rows=[r for m in logical for r in member_rows[m]]; groups=defaultdict(list)
    for r in rows:groups[(r["league"],r["season_start"],r["date"])].append(r)
    pairs=[];unpaired=[];ambiguous=[]
    for key,group in sorted(groups.items()):
        homes=[r for r in group if r["h_a"]=="h"]; aways=[r for r in group if r["h_a"]=="a"]
        cand={i:[j for j,a in enumerate(aways) if a["team"]!=h["team"] and reciprocal(h,a)] for i,h in enumerate(homes)}
        rc=defaultdict(int)
        for js in cand.values():
            for j in js:rc[j]+=1
        used=set()
        for i,h in enumerate(homes):
            js=cand[i]
            if len(js)==1 and rc[js[0]]==1:
                a=aways[js[0]];used.update((id(h),id(a)))
                pairs.append({"league":h["league"],"season_start":h["season_start"],"date":h["date"],"home_team":h["team"],"away_team":a["team"],"home_ppda":h["ppda"],"away_ppda":a["ppda"],"home_deep":h["deep"],"away_deep":a["deep"]})
            elif len(js)>1:ambiguous.append({"league":h["league"],"season_start":h["season_start"],"date":h["date"],"team":h["team"],"candidate_count":len(js)})
        amb_keys={(x["league"],x["season_start"],x["date"],x["team"]) for x in ambiguous}
        for r in group:
            if id(r) not in used and (r["league"],r["season_start"],r["date"],r["team"]) not in amb_keys:
                unpaired.append({k:r[k] for k in ("league","season_start","date","team","h_a")})
    cr=defaultdict(int);cm=defaultdict(int);cu=defaultdict(int);ca=defaultdict(int)
    for r in rows:cr[(r["league"],r["season_start"])]+=1
    for m in pairs:cm[(m["league"],m["season_start"])]+=1
    for r in unpaired:cu[(r["league"],r["season_start"])]+=1
    for r in ambiguous:ca[(r["league"],r["season_start"])]+=1
    cohorts={f"{l}:{s}":{"perspective_rows":cr[(l,s)],"paired_matches":cm[(l,s)],"unpaired_rows":cu[(l,s)],"ambiguous_home_rows":ca[(l,s)]} for l,s in sorted(cr)}
    pairs.sort(key=lambda m:(m["season_start"],m["league"],m["date"],m["home_team"],m["away_team"]))
    clean=not conflicts and not unpaired and not ambiguous and len(rows)==2*len(pairs)
    return {"status":"ZERO_LABEL_COVERAGE_QUALIFIED" if clean else "ZERO_LABEL_COVERAGE_PARTIAL",
            "source_repository":lock["source"]["repository"],"source_revision":lock["source"]["revision"],"archive_blob_sha1":blob,
            "permission_class":lock["permission"]["class"],"production_eligible":False,"target_leagues":list(TARGET_LEAGUES),
            "logical_member_count":len(logical),"exact_duplicate_member_count":len(dups),"exact_duplicate_members":dups,
            "duplicate_conflict_count":len(conflicts),"duplicate_conflicts":conflicts,"safe_perspective_row_count":len(rows),"paired_match_count":len(pairs),
            "unpaired_row_count":len(unpaired),"ambiguous_home_row_count":len(ambiguous),"unpaired_rows_sample":unpaired[:100],"ambiguous_rows_sample":ambiguous[:100],
            "per_cohort":cohorts,"feature_projection_sha256":sha256(b"\n".join(canonical(m) for m in pairs)+b"\n"),"safe_columns_used":list(SAFE_COLUMNS),
            "forbidden_columns_used_for_identity_or_coverage":[],"result_values_used":0,"score_values_used":0,"xg_values_used":0,"candidate_confirmation_allowed":False,
            "allowed_research_roles":["TRAIN","DEVELOPMENT","REUSABLE_BENCHMARK"],"formal_v2_changed":False,"current_changed":False,"production_changed":False}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--lock",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
    lock=json.loads(Path(a.lock).read_text());Path(a.out).write_text(json.dumps(audit(lock,fetch_bytes(lock["source"]["raw_url"])),indent=2,sort_keys=True)+"\n")
if __name__=="__main__":main()
