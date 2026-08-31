from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

REGIME_DIMS = ("tempo","high_press","defensive_line_height","passing_directness","attacking_width",
               "transition_attack","set_piece_attack","set_piece_defence","leading_contraction",
               "trailing_risk","substitution_timing")
FORMATION_KEYS = ("4-3-3","4-2-3-1","4-4-2","3-5-2","other")
MATCHUP_DIMS = ("press_vs_buildup","wide_vs_fullback","aerial_vs_aerial","counter_vs_highline",
                "possession_vs_lowblock","setpiece_vs_setpiece","striker_vs_cb")


class TacticalError(RuntimeError):
    pass


def _dt(v: str) -> datetime:
    d=datetime.fromisoformat(v.replace("Z","+00:00"))
    if d.tzinfo is None:
        raise TacticalError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(v: Any) -> str:
    return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


@dataclass(frozen=True)
class CoachRegime:
    coach_id: str
    team_id: str
    regime_start: str
    regime_end_if_known: str | None
    vector: dict[str,float]
    formation_distribution: dict[str,float]
    evidence: float
    uncertainty: float
    source_sha256: str

    def to_dict(self)->dict[str,Any]:
        return asdict(self)


@dataclass(frozen=True)
class TeamStyle:
    team_id: str
    vector: dict[str,float]
    derived: dict[str,float]
    formation_distribution: dict[str,float]
    effective_matches: float
    uncertainty: float
    known_at_max: str | None
    source_sha256: str

    def to_dict(self)->dict[str,Any]:
        return asdict(self)


def _normalize_formation(raw: dict[str,float]) -> dict[str,float]:
    z={k:max(0.0,float(raw.get(k,0.0))) for k in FORMATION_KEYS}; s=sum(z.values())
    return ({k:(v/s) for k,v in z.items()} if s>0 else {k:(1.0 if k=="other" else 0.0) for k in FORMATION_KEYS})


def estimate_regime(coach_id: str, team_id: str, regime_start: str, historical_rows: list[dict[str,Any]], *,
                    cutoff: str, league_prior: dict[str,float] | None = None) -> CoachRegime:
    co=_dt(cutoff); start=_dt(regime_start); prior=league_prior or {d:0.0 for d in REGIME_DIMS}
    usable=[]
    for r in historical_rows:
        if r.get("coach_id")!=coach_id or r.get("team_id")!=team_id:
            continue
        k=_dt(r["known_at"])
        if not(start<=k<co):
            continue
        vals=r.get("tactical_features",{})
        if any(d not in REGIME_DIMS for d in vals):
            raise TacticalError("unknown tactical dimension")
        usable.append((k,vals,float(r.get("exposure",1.0)),str(r.get("source_sha256","")),r.get("formation_distribution",{})))
    sums=defaultdict(float); formations=defaultdict(float); ev=0.0; source=[]
    for k,vals,exposure,sha,form in usable:
        w=exposure*math.exp(-math.log(2)*max((co-k).days,0)/120.0); ev+=w; source.append(sha)
        for d in REGIME_DIMS:
            sums[d]+=w*float(vals.get(d,prior.get(d,0.0)))
        for f,p in _normalize_formation(form).items():
            formations[f]+=w*p
    shrink=ev/(ev+8.0)
    vec={d:shrink*(sums[d]/max(ev,1e-9))+(1-shrink)*float(prior.get(d,0.0)) for d in REGIME_DIMS}
    form=_normalize_formation({k:v/max(ev,1e-9) for k,v in formations.items()})
    return CoachRegime(coach_id,team_id,regime_start,None,vec,form,ev,min(1.5,1/math.sqrt(1+ev)+0.20),_sha(sorted(source)))


def estimate_team_style(team_id: str, historical_rows: list[dict[str,Any]], *, cutoff: str, half_life_days: float = 150.0) -> TeamStyle:
    co=_dt(cutoff); sums=defaultdict(float); forms=defaultdict(float); ev=0.0; shas=[]; known=[]
    for r in historical_rows:
        if str(r.get("team_id"))!=str(team_id):
            continue
        k=_dt(str(r["known_at"]))
        if k>=co:
            raise TacticalError("future/current-target style row reached estimator")
        vals=r.get("style_features",{})
        if any(d not in REGIME_DIMS for d in vals):
            raise TacticalError("unknown team style dimension")
        w=max(0.0,float(r.get("exposure",1.0)))*math.exp(-math.log(2)*max((co-k).total_seconds()/86400.0,0)/half_life_days)
        ev+=w; known.append(str(r["known_at"])); shas.append(str(r.get("source_sha256","")))
        for d in REGIME_DIMS:
            sums[d]+=w*float(vals.get(d,0.0))
        for f,p in _normalize_formation(r.get("formation_distribution",{})).items():
            forms[f]+=w*p
    shrink=ev/(ev+6.0)
    vec={d:shrink*sums[d]/max(ev,1e-9) for d in REGIME_DIMS}
    form=_normalize_formation({k:v/max(ev,1e-9) for k,v in forms.items()})
    derived={
        "possession":max(-2.0,min(2.0,-0.55*vec["passing_directness"]+0.25*vec["tempo"])),
        "counterattack":vec["transition_attack"],
        "crossing":max(-2.0,min(2.0,0.65*vec["attacking_width"]+0.35*vec["passing_directness"])),
        "pressing":vec["high_press"],
        "set_piece":vec["set_piece_attack"],
        "leading_contraction":vec["leading_contraction"],
        "trailing_risk":vec["trailing_risk"],
    }
    return TeamStyle(str(team_id),vec,derived,form,ev,min(1.5,1/math.sqrt(1+ev)+0.15),max(known) if known else None,_sha(sorted(shas)))


def _raw_matchup(home: TeamStyle|CoachRegime, away: TeamStyle|CoachRegime) -> dict[str,float]:
    hv=home.vector; av=away.vector
    return {
        "press_vs_buildup":hv["high_press"]+0.5*av["passing_directness"],
        "wide_vs_fullback":hv["attacking_width"]-0.5*av["defensive_line_height"],
        "aerial_vs_aerial":hv["set_piece_attack"]-av["set_piece_defence"],
        "counter_vs_highline":hv["transition_attack"]+av["defensive_line_height"],
        "possession_vs_lowblock":-hv["passing_directness"]+av["leading_contraction"],
        "setpiece_vs_setpiece":hv["set_piece_attack"]-av["set_piece_defence"],
        "striker_vs_cb":0.0,
    }


def fit_matchup_coefficients(rows: list[dict[str,Any]], *, ridge: float = 30.0) -> dict[str,float]:
    out={}
    for d in MATCHUP_DIMS:
        xx=xy=0.0
        for r in rows:
            x=float(r.get("matchup",{}).get(d,0.0)); y=float(r.get("target_log_mu_residual",0.0)); w=max(0.0,float(r.get("weight",1.0)))
            xx+=w*x*x; xy+=w*x*y
        out[d]=max(-0.12,min(0.12,xy/(xx+ridge)))
    return out


def tactical_matchup(home: CoachRegime|TeamStyle, away: CoachRegime|TeamStyle, coeffs: dict[str,float]) -> tuple[float,float,str]:
    hraw=_raw_matchup(home,away); araw=_raw_matchup(away,home)
    he=sum(float(coeffs.get(k,0.0))*v for k,v in hraw.items()); ae=sum(float(coeffs.get(k,0.0))*v for k,v in araw.items())
    delta=max(-0.25,min(0.25,(he-ae)/2.0))
    return delta,-delta,_sha({"home_raw":hraw,"away_raw":araw,"coeffs":coeffs})


def matchup_features(home: TeamStyle, away: TeamStyle) -> dict[str,float]:
    hraw=_raw_matchup(home,away); araw=_raw_matchup(away,home)
    return {k:(hraw[k]-araw[k])/2.0 for k in MATCHUP_DIMS}
