from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_jsonl(path: Path) -> list[dict[str,Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def dt(text: str) -> datetime:
    x=datetime.fromisoformat(text.replace("Z","+00:00"))
    if x.tzinfo is None: raise RuntimeError("naive datetime")
    return x.astimezone(timezone.utc)


def grouped(rows):
    out=[]; cur=[]; key=None
    for r in sorted(rows,key=lambda x:(x["cutoff"],x["competition_id"],x["fixture_id"])):
        k=r["cutoff"]
        if key is None or k==key:
            cur.append(r); key=k
        else:
            out.append(cur); cur=[r]; key=k
    if cur: out.append(cur)
    return out


def import_engines(v1_dir: Path,v2_dir: Path):
    sys.path.insert(0,str(v1_dir)); v1=importlib.import_module("pure_engine")
    sys.path.insert(0,str(v2_dir)); sys.modules.pop("engine",None); v2=importlib.import_module("engine")
    return v1,v2


def v1_fixture(v1,r):
    return v1.Fixture(r["fixture_id"],r["competition_id"],r["season"],dt(r["cutoff"]),r["home_team_id"],r["away_team_id"])


def v2_fixture(v2,r):
    return v2.Fixture(
        fixture_id=r["fixture_id"],competition_id=r["competition_id"],season=r["season"],
        kickoff=dt(r["cutoff"]),home_team_id=r["home_team_id"],away_team_id=r["away_team_id"],
        round_index=r.get("round_index"),
    )


class SharedHistory:
    def __init__(self):
        self.elo=defaultdict(lambda:1500.0)
        self.appear=defaultdict(int)
        self.k=20.0
        self.home_adv=60.0

    def snapshot(self,r):
        h,a=r["home_team_id"],r["away_team_id"]
        nh,na=self.appear[h],self.appear[a]
        bucket="zero" if min(nh,na)==0 else "sparse" if min(nh,na)<5 else "established"
        return {
            "shared_home_elo":self.elo[h],"shared_away_elo":self.elo[a],
            "shared_home_prior_appearances":nh,"shared_away_prior_appearances":na,
            "shared_cold_start_bucket":bucket,
        }

    def apply(self,r,hg,ag):
        h,a=r["home_team_id"],r["away_team_id"]
        rh,ra=self.elo[h],self.elo[a]
        expected=1.0/(1.0+10.0**(-((rh+self.home_adv)-ra)/400.0))
        actual=1.0 if hg>ag else 0.5 if hg==ag else 0.0
        delta=self.k*(actual-expected)
        self.elo[h]=rh+delta; self.elo[a]=ra-delta
        self.appear[h]+=1; self.appear[a]+=1


def apply_rows(v1,v2,s1,s2,history,batch,label_map):
    f1=[]; f2=[]; labels={}
    for r in batch:
        lab=label_map[r["fixture_id"]]
        hg,ag=int(lab["home_goals"]),int(lab["away_goals"])
        if dt(lab["cutoff"]) != dt(r["cutoff"]):
            raise RuntimeError("feature/label cutoff mismatch")
        f1.append(v1_fixture(v1,r)); f2.append(v2_fixture(v2,r)); labels[r["fixture_id"]]=(hg,ag)
    s1.apply_batch(f1,labels); s2.apply_batch(f2,labels)
    for r in batch:
        hg,ag=labels[r["fixture_id"]]; history.apply(r,hg,ag)


def initialize_from_development(v1,v2,s1,s2,history,development):
    label_map={r["fixture_id"]:r for r in development}
    for batch in grouped(development):
        apply_rows(v1,v2,s1,s2,history,batch,label_map)

