from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from player_strength import PlayerVector, lineup_components


class LineupError(RuntimeError):
    pass


def _dt(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise LineupError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class LineupScenario:
    scenario_id: str
    route: str
    probability: float
    home_player_ids: list[str]
    away_player_ids: list[str]
    known_at_max: str | None
    uncertainty: float
    scenario_sha256: str

    def schema_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchProfile:
    status: str
    log_mu_home_delta: float
    log_mu_away_delta: float
    uncertainty: float
    expected_sub_minutes_home: float
    expected_sub_minutes_away: float
    coverage_home: int
    coverage_away: int
    evidence_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_player_row(row: dict[str, Any], cutoff: str) -> None:
    required = {"player_id", "starting_probability", "availability_probability", "expected_minutes_distribution",
                "injury_status", "suspension_status", "return_status", "rotation_probability", "role_distribution",
                "replacement_quality", "uncertainty", "known_at"}
    if not required.issubset(row):
        raise LineupError("lineup player row missing fields")
    if _dt(row["known_at"]) >= _dt(cutoff):
        raise LineupError("lineup evidence not strictly pre-cutoff")
    for f in ("starting_probability", "availability_probability", "rotation_probability"):
        x = float(row[f])
        if not 0 <= x <= 1:
            raise LineupError(f"{f} outside [0,1]")
    if not isinstance(row["expected_minutes_distribution"], dict) or "mean" not in row["expected_minutes_distribution"]:
        raise LineupError("expected minutes distribution missing mean")
    if float(row["expected_minutes_distribution"]["mean"]) < 0:
        raise LineupError("negative expected minutes")


def _rank(players: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    for p in players:
        _validate_player_row(p, cutoff)
    return sorted(players, key=lambda p: (float(p["starting_probability"])*float(p["availability_probability"]), str(p["player_id"])), reverse=True)


def _xi_probability(rows: list[dict[str, Any]]) -> float:
    return math.prod(max(1e-8, min(1.0, float(p["starting_probability"])*float(p["availability_probability"]))) for p in rows)


def build_lineup_scenarios(home_players: list[dict[str, Any]] | None, away_players: list[dict[str, Any]] | None,
                           *, cutoff: str, confirmed: dict[str, Any] | None = None, max_scenarios: int = 4) -> list[LineupScenario]:
    co = _dt(cutoff)
    if confirmed is not None:
        required = {"published_at", "home_player_ids", "away_player_ids", "source_sha256"}
        if set(confirmed) != required or _dt(confirmed["published_at"]) >= co:
            raise LineupError("CONFIRMED_LINEUP requires exact pre-cutoff publication evidence")
        if len(set(confirmed["home_player_ids"])) != len(confirmed["home_player_ids"]) or len(set(confirmed["away_player_ids"])) != len(confirmed["away_player_ids"]):
            raise LineupError("confirmed lineup contains duplicate player")
        payload = {"route":"CONFIRMED_LINEUP","home":confirmed["home_player_ids"],"away":confirmed["away_player_ids"],
                   "known_at":confirmed["published_at"],"source":confirmed["source_sha256"]}
        sid = "confirmed_"+_sha(payload)[:16]
        return [LineupScenario(sid,"CONFIRMED_LINEUP",1.0,list(confirmed["home_player_ids"]),list(confirmed["away_player_ids"]),confirmed["published_at"],0.05,_sha(payload))]
    if not home_players or not away_players:
        payload={"route":"LINEUP_UNKNOWN","cutoff":cutoff}
        return [LineupScenario("unknown_"+_sha(payload)[:16],"LINEUP_UNKNOWN",1.0,[],[],None,1.0,_sha(payload))]
    hr=_rank(home_players,cutoff); ar=_rank(away_players,cutoff)
    if len(hr)<11 or len(ar)<11:
        payload={"route":"LINEUP_UNKNOWN","cutoff":cutoff,"reason":"lt11"}
        return [LineupScenario("unknown_"+_sha(payload)[:16],"LINEUP_UNKNOWN",1.0,[],[],None,1.0,_sha(payload))]
    hxi=[str(p["player_id"]) for p in hr[:11]]; axi=[str(p["player_id"]) for p in ar[:11]]
    base=_xi_probability(hr[:11])*_xi_probability(ar[:11]); candidates=[(hxi,axi,max(base,1e-12),0.20,hr[:11]+ar[:11])]
    if len(hr)>11:
        alt=hxi[:-1]+[str(hr[11]["player_id"])]
        ratio=max(1e-6,float(hr[11]["starting_probability"])*float(hr[11]["availability_probability"])) / max(1e-6,float(hr[10]["starting_probability"])*float(hr[10]["availability_probability"]))
        candidates.append((alt,axi,max(base*ratio,1e-12),0.32,hr[:12]+ar[:11]))
    if len(ar)>11:
        alt=axi[:-1]+[str(ar[11]["player_id"])]
        ratio=max(1e-6,float(ar[11]["starting_probability"])*float(ar[11]["availability_probability"])) / max(1e-6,float(ar[10]["starting_probability"])*float(ar[10]["availability_probability"]))
        candidates.append((hxi,alt,max(base*ratio,1e-12),0.32,hr[:11]+ar[:12]))
    if len(hr)>11 and len(ar)>11:
        h_alt=hxi[:-1]+[str(hr[11]["player_id"])]; a_alt=axi[:-1]+[str(ar[11]["player_id"])]
        hr_ratio=max(1e-6,float(hr[11]["starting_probability"])*float(hr[11]["availability_probability"])) / max(1e-6,float(hr[10]["starting_probability"])*float(hr[10]["availability_probability"]))
        ar_ratio=max(1e-6,float(ar[11]["starting_probability"])*float(ar[11]["availability_probability"])) / max(1e-6,float(ar[10]["starting_probability"])*float(ar[10]["availability_probability"]))
        candidates.append((h_alt,a_alt,max(base*hr_ratio*ar_ratio,1e-12),0.40,hr[:12]+ar[:12]))
    candidates=candidates[:max(1,max_scenarios)]; total=sum(x[2] for x in candidates)
    out=[]
    for home,away,w,unc,rows in candidates:
        known=max(str(x["known_at"]) for x in rows); payload={"route":"EXPECTED_LINEUP","home":home,"away":away,"known_at":known}
        out.append(LineupScenario("expected_"+_sha(payload)[:16],"EXPECTED_LINEUP",w/total,home,away,known,unc,_sha(payload)))
    if abs(sum(x.probability for x in out)-1.0)>1e-9:
        raise LineupError("scenario normalization failed")
    return out


def _bench_side(players: list[dict[str, Any]], vectors: dict[str, PlayerVector], cutoff: str, substitution_tendency: float) -> tuple[float,float,float,float,int,float,list[str]]:
    ranked=_rank(players,cutoff)
    if len(ranked)<12:
        return 0.0,0.0,0.0,0.0,0,1.0,[]
    starters=[str(x["player_id"]) for x in ranked[:11]]; base=lineup_components(vectors,starters)
    total_att=total_def=total_gk=total_minutes=0.0; coverage=0; uncs=[]; known=[]
    for row in ranked[11:18]:
        pid=str(row["player_id"])
        if pid not in vectors:
            continue
        role=vectors[pid].role
        same=[s for s in starters if s in vectors and vectors[s].role==role]
        if not same:
            same=[s for s in starters if s in vectors]
        if not same:
            continue
        replace=min(same,key=lambda p:vectors[p].effective_exposure)
        alt=[pid if x==replace else x for x in starters]; comp=lineup_components(vectors,alt)
        mean_minutes=min(45.0,max(0.0,float(row["expected_minutes_distribution"]["mean"])))
        entry=max(0.0,min(1.0,float(row["availability_probability"])*(1.0-float(row["starting_probability"]))*substitution_tendency))
        w=entry*mean_minutes/90.0
        total_att+=w*(comp[0]-base[0]); total_def+=w*(comp[1]-base[1]); total_gk+=w*(comp[2]-base[2]); total_minutes+=entry*mean_minutes
        coverage+=1; uncs.append(float(row["uncertainty"])); known.append(str(row["known_at"]))
    unc=1.0 if not uncs else min(1.0,sum(uncs)/len(uncs)+1.0/math.sqrt(1+coverage))
    return total_att,total_def,total_gk,total_minutes,coverage,unc,known


def bench_substitution_profile(home_players: list[dict[str, Any]] | None, away_players: list[dict[str, Any]] | None,
                               vectors: dict[str, PlayerVector] | None, *, cutoff: str,
                               home_substitution_tendency: float = 0.65, away_substitution_tendency: float = 0.65) -> BenchProfile:
    if not home_players or not away_players or not vectors:
        return BenchProfile("BLOCKED_DATA",0.0,0.0,1.0,0.0,0.0,0,0,None)
    hs=_bench_side(home_players,vectors,cutoff,max(0.0,min(1.0,home_substitution_tendency)))
    aw=_bench_side(away_players,vectors,cutoff,max(0.0,min(1.0,away_substitution_tendency)))
    if hs[4]==0 or aw[4]==0:
        return BenchProfile("BLOCKED_DATA",0.0,0.0,max(hs[5],aw[5]),hs[3],aw[3],hs[4],aw[4],None)
    dh=max(-0.18,min(0.18,hs[0]-aw[1]-aw[2])); da=max(-0.18,min(0.18,aw[0]-hs[1]-hs[2]))
    evidence={"home_known":sorted(hs[6]),"away_known":sorted(aw[6]),"home_minutes":hs[3],"away_minutes":aw[3],"home_coverage":hs[4],"away_coverage":aw[4]}
    return BenchProfile("IMPLEMENTED",dh,da,(hs[5]+aw[5])/2.0,hs[3],aw[3],hs[4],aw[4],_sha(evidence))
