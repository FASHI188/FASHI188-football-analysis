#!/usr/bin/env python3
"""Research-only multi-book 1X2 consensus accuracy diagnostic.

Compares preferred single-book retrospective market probabilities with a de-vigged
consensus across all available closing 1X2 triplets in the same processed match row.
All legacy prices remain retrospective references without original quote timestamps.
No formal probability/weight changes are authorized.
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/"validation"; ENGINE=ROOT/"engine"
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

from platform_core import canonical_team_name, load_aliases, parse_match_date
from diagnose_1x2_market_anchor_v697 import _load_model_rows, _market_probs, _model_probs, _pick_probs, _devig

OUT=ROOT/"manifests"/"v6_1x2_multibook_consensus_v6101_status.json"
DIRECTIONS=("home","draw","away")
SEED=20260724+6101
CLOSING=(
    ("PSCH","PSCD","PSCA","Pinnacle_closing"),
    ("B365CH","B365CD","B365CA","Bet365_closing"),
    ("AvgCH","AvgCD","AvgCA","Average_closing"),
    ("MaxCH","MaxCD","MaxCA","Maximum_closing"),
)
OPENING=(
    ("PSH","PSD","PSA","Pinnacle"),
    ("B365H","B365D","B365A","Bet365"),
    ("AvgH","AvgD","AvgA","Average"),
    ("WHH","WHD","WHA","WilliamHill"),
    ("MaxH","MaxD","MaxA","Maximum"),
)

def _f(v):
    try: x=float(str(v).strip())
    except Exception: return None
    return x if x>1.0 else None

def _triplet(raw, spec):
    h,d,a,label=spec
    hv,dv,av=_f(raw.get(h)),_f(raw.get(d)),_f(raw.get(a))
    return (_devig(hv,dv,av),label) if hv and dv and av else None

def _consensus(items):
    return {k:sum(p[k] for p,_ in items)/len(items) for k in DIRECTIONS}

def _key(cid,season,date_iso,home,away): return (cid,season,date_iso,home,away)

def _match():
    base=_load_model_rows(); aliases=load_aliases()
    lookup={_key(r["competition_id"],r["season"],r["date"],r["home_team"],r["away_team"]):r for r in base}
    out={}
    source_counts=Counter(); nbooks=Counter()
    for cid in sorted({r["competition_id"] for r in base}):
        d=ROOT/"processed"/cid
        if not d.exists(): continue
        for path in sorted(d.glob("*.csv")):
            with path.open("r",encoding="utf-8-sig",newline="") as f:
                for raw0 in csv.DictReader(f):
                    raw={str(k):"" if v is None else str(v) for k,v in raw0.items() if k}
                    season=str(raw.get("season") or raw.get("Season") or "").strip()
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"): continue
                    try: date_iso=parse_match_date(raw["Date"],season).isoformat()
                    except Exception: continue
                    home=canonical_team_name(cid,raw["HomeTeam"],aliases); away=canonical_team_name(cid,raw["AwayTeam"],aliases)
                    key=_key(cid,season,date_iso,home,away)
                    b=lookup.get(key)
                    if b is None or key in out: continue
                    closing=[x for spec in CLOSING if (x:=_triplet(raw,spec)) is not None]
                    opening=[x for spec in OPENING if (x:=_triplet(raw,spec)) is not None]
                    pool=closing if closing else opening
                    if not pool: continue
                    item=dict(b)
                    item["consensus_p"]=_consensus(pool)
                    item["book_count"]=len(pool)
                    item["market_phase"]="closing" if closing else "opening"
                    # preferred single: first in source priority order
                    item["preferred_p"]=pool[0][0]
                    item["preferred_source"]=pool[0][1]
                    out[key]=item
                    nbooks[len(pool)]+=1
                    for _,label in pool: source_counts[label]+=1
    return list(out.values()),dict(source_counts),dict(nbooks)

def _acc(rows,kind):
    def prob(r):
        if kind=="model": return _model_probs(r)
        if kind=="preferred": return r["preferred_p"]
        return r["consensus_p"]
    hits=sum(1 for r in rows if _pick_probs(prob(r))==r["actual"])
    return {"count":len(rows),"hits":hits,"accuracy":hits/len(rows) if rows else None}

def _disjoint(rows,block=100):
    idx=list(range(len(rows))); random.Random(SEED).shuffle(idx)
    full=len(idx)//block; blocks=[]; compare=Counter()
    for bi in range(full):
        sub=[rows[i] for i in idx[bi*block:(bi+1)*block]]
        p=_acc(sub,"preferred")["accuracy"]; c=_acc(sub,"consensus")["accuracy"]
        u=(c-p)*100.0
        compare["win" if u>0 else "tie" if u==0 else "loss"]+=1
        blocks.append({"block":bi+1,"preferred_accuracy":p,"consensus_accuracy":c,"uplift_pp":u})
    return {"full_block_count":full,"leftover_count":len(rows)-full*block,"consensus_vs_preferred":dict(compare),"uplift_mean_pp":statistics.mean(b["uplift_pp"] for b in blocks) if blocks else None,"blocks":blocks}

def main():
    rows,sources,nbooks=_match()
    preferred=_acc(rows,"preferred"); consensus=_acc(rows,"consensus"); model=_acc(rows,"model")
    multi=[r for r in rows if int(r["book_count"])>=2]
    pm=_acc(multi,"preferred"); cm=_acc(multi,"consensus")
    pair=Counter()
    for r in rows:
        p=_pick_probs(r["preferred_p"])==r["actual"]; c=_pick_probs(r["consensus_p"])==r["actual"]
        pair["both_correct" if p and c else "preferred_only" if p else "consensus_only" if c else "both_wrong"]+=1
    by_comp={}
    for cid in sorted({r["competition_id"] for r in rows}):
        sub=[r for r in rows if r["competition_id"]==cid]
        pa=_acc(sub,"preferred"); ca=_acc(sub,"consensus")
        by_comp[cid]={"count":len(sub),"preferred_accuracy":pa["accuracy"],"consensus_accuracy":ca["accuracy"],"uplift_pp":(ca["accuracy"]-pa["accuracy"])*100.0}
    payload={
        "schema_version":"V6.10.1-multibook-consensus-1x2-r1",
        "generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status":"PASS","formal_current_version":"V5.0.1",
        "market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "row_count":len(rows),"source_counts":sources,"book_count_distribution":nbooks,
        "overall":{"old_model":model,"preferred_single":preferred,"consensus":consensus,"consensus_vs_preferred_uplift_pp":(consensus["accuracy"]-preferred["accuracy"])*100.0},
        "multi_book_only":{"count":len(multi),"preferred":pm,"consensus":cm,"uplift_pp":(cm["accuracy"]-pm["accuracy"])*100.0 if multi else None},
        "paired_correctness":dict(pair),"disjoint_100":_disjoint(rows),"by_competition":by_comp,
        "governance":{"research_only":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"overall":payload["overall"],"multi_book_only":payload["multi_book_only"],"paired":dict(pair),"disjoint":{k:v for k,v in payload["disjoint_100"].items() if k!="blocks"}},ensure_ascii=False,indent=2))
    return 0
if __name__=="__main__": raise SystemExit(main())
