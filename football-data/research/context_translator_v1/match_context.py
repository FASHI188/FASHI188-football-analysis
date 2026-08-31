from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class MatchContextError(RuntimeError):
    pass


def _dt(v: str) -> datetime:
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise MatchContextError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ScheduleFeatures:
    home_rest_days: float | None
    away_rest_days: float | None
    density7_diff: int
    density14_diff: int
    density28_diff: int
    consecutive_away_diff: int
    season_phase: float

    def vector(self) -> list[float]:
        rest = 0.0 if self.home_rest_days is None or self.away_rest_days is None else max(-14,min(14,self.home_rest_days-self.away_rest_days))/7.0
        return [rest, max(-4,min(4,self.density7_diff)), max(-6,min(6,self.density14_diff))/2.0,
                max(-10,min(10,self.density28_diff))/3.0, max(-4,min(4,self.consecutive_away_diff))/2.0,
                max(-1.0,min(1.0,(self.season_phase-0.5)*2.0))]


class ScheduleTracker:
    def __init__(self) -> None:
        self.times: dict[str, list[datetime]] = defaultdict(list)
        self.away_streak: dict[str, int] = defaultdict(int)

    def features(self, home: str, away: str, cutoff: str, round_index: int | None = None, season_matches: int = 38) -> ScheduleFeatures:
        co = _dt(cutoff)
        def side(team: str) -> tuple[float|None,int,int,int]:
            prior = [t for t in self.times[team] if t < co]
            rest = None if not prior else (co-max(prior)).total_seconds()/86400.0
            def n(days:int)->int: return sum(1 for t in prior if 0 < (co-t).total_seconds() <= days*86400)
            return rest,n(7),n(14),n(28)
        hr,h7,h14,h28=side(home); ar,a7,a14,a28=side(away)
        phase = 0.0 if round_index is None else max(0.0,min(1.0,float(round_index)/max(season_matches,1)))
        return ScheduleFeatures(hr,ar,h7-a7,h14-a14,h28-a28,self.away_streak[home]-self.away_streak[away],phase)

    def observe_fixture(self, home: str, away: str, cutoff: str) -> None:
        co = _dt(cutoff)
        self.times[home].append(co); self.times[away].append(co)
        self.away_streak[home] = 0
        self.away_streak[away] += 1


def fit_schedule_coefficients(rows: list[dict[str, Any]], *, iterations: int = 400, learning_rate: float = 0.003, ridge: float = 30.0) -> list[float]:
    beta = [0.0]*6
    n = max(len(rows),1)
    for _ in range(iterations):
        grad=[0.0]*6
        for r in rows:
            x=list(map(float,r["x"])); bh=max(float(r["base_mu_home"]),1e-6); ba=max(float(r["base_mu_away"]),1e-6)
            y_h=float(r["home_goals"]); y_a=float(r["away_goals"])
            tilt=sum(b*v for b,v in zip(beta,x)); tilt=max(-0.6,min(0.6,tilt))
            mh=bh*math.exp(tilt); ma=ba*math.exp(-tilt)
            scalar=(mh-y_h) - (ma-y_a)
            for j in range(6): grad[j]+=scalar*x[j]
        for j in range(6):
            grad[j]=grad[j]/n + ridge*beta[j]/n
            beta[j]-=learning_rate*grad[j]
            beta[j]=max(-0.20,min(0.20,beta[j]))
    return beta


def schedule_adjustment(features: ScheduleFeatures, beta: list[float]) -> tuple[float,float,str]:
    if len(beta)!=6: raise MatchContextError("schedule beta shape")
    x=features.vector(); tilt=max(-0.35,min(0.35,sum(float(b)*v for b,v in zip(beta,x))))
    return tilt,-tilt,_sha({"x":x,"beta":beta})


def environment_adjustment(facts: list[dict[str, Any]] | None, cutoff: str) -> dict[str, Any]:
    if not facts:
        return {"status":"BLOCKED_DATA","log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":0.35,"evidence_sha256":None}
    co=_dt(cutoff); usable=[]
    allowed={"neutral_venue","surface","altitude","temperature","humidity","wind","cooling_rule","referee_red_prior","referee_penalty_prior","competition_format","aggregate_score","extra_time_available","standings_state"}
    for f in facts:
        if set(f)-{"known_at","name","value","source_sha256"}: raise MatchContextError("environment fact schema")
        if f["name"] not in allowed: raise MatchContextError("environment field default-denied")
        if _dt(f["known_at"])>=co: raise MatchContextError("environment fact not pre-cutoff")
        usable.append(f)
    return {"status":"CONTRACT_ONLY","log_mu_home_delta":0.0,"log_mu_away_delta":0.0,"uncertainty":0.25,
            "evidence_sha256":_sha(usable)}
