from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from lineup_scenarios import LineupScenario
from player_strength import PlayerVector, lineup_components
from translator_schema import canonical_sha

STATUSES={"IMPLEMENTED","REJECTED_ABLATION","BLOCKED_DATA","CONTRACT_ONLY"}


@dataclass(frozen=True)
class LayerAdjustment:
    status:str
    log_mu_home_delta:float=0.0
    log_mu_away_delta:float=0.0
    uncertainty:float=0.0
    evidence_sha256:str|None=None

    def schema_dict(self)->dict[str,Any]:
        if self.status not in STATUSES: raise ValueError("invalid layer status")
        return {"status":self.status,"log_mu_home_delta":float(self.log_mu_home_delta),"log_mu_away_delta":float(self.log_mu_away_delta),
                "uncertainty":max(0.0,float(self.uncertainty)),"evidence_sha256":self.evidence_sha256}


def team_state(team_id:str, attack:float, defence:float, sample_size:float, uncertainty:float)->dict[str,Any]:
    payload={"team_id":team_id,"attack":float(attack),"defence":float(defence),"sample_size":max(0.0,float(sample_size)),"uncertainty":max(0.0,float(uncertainty))}
    payload["state_sha256"]=canonical_sha(payload)
    return payload


def build_plan(*,match_id:str,cutoff:str,base_mu_home:float,base_mu_away:float,home_team_state:dict[str,Any],away_team_state:dict[str,Any],
               scenarios:list[LineupScenario],player_vectors:dict[str,PlayerVector]|None,coach_tactical:LayerAdjustment,
               match_context:LayerAdjustment,process_hazard:LayerAdjustment,provenance_manifest_sha256:str,
               player_status:str="BLOCKED_DATA",coverage_grade:str="TEAM_ONLY")->dict[str,Any]:
    if base_mu_home<=0 or base_mu_away<=0: raise ValueError("base mus must be positive")
    psha=None if not player_vectors else canonical_sha({k:v.to_dict() for k,v in sorted(player_vectors.items())})
    internal=[]
    for sc in scenarios:
        h_att=h_def=h_gk=a_att=a_def=a_gk=0.0; lineup_unc=sc.uncertainty
        if player_vectors and sc.route!="LINEUP_UNKNOWN":
            h_att,h_def,h_gk,h_unc=lineup_components(player_vectors,sc.home_player_ids)
            a_att,a_def,a_gk,a_unc=lineup_components(player_vectors,sc.away_player_ids)
            lineup_unc=max(lineup_unc,(h_unc+a_unc)/2)
        player_home=h_att-a_def-a_gk; player_away=a_att-h_def-h_gk
        dh=player_home+coach_tactical.log_mu_home_delta+match_context.log_mu_home_delta+process_hazard.log_mu_home_delta
        da=player_away+coach_tactical.log_mu_away_delta+match_context.log_mu_away_delta+process_hazard.log_mu_away_delta
        dh=max(-0.70,min(0.70,dh)); da=max(-0.70,min(0.70,da))
        internal.append({"scenario":sc,"base_mu_home":base_mu_home,"base_mu_away":base_mu_away,
                         "translated_mu_home":base_mu_home*math.exp(dh),"translated_mu_away":base_mu_away*math.exp(da),
                         "lineup_uncertainty":lineup_unc,"deltas":{"player_home":player_home,"player_away":player_away,
                         "coach_home":coach_tactical.log_mu_home_delta,"coach_away":coach_tactical.log_mu_away_delta,
                         "context_home":match_context.log_mu_home_delta,"context_away":match_context.log_mu_away_delta,
                         "process_home":process_hazard.log_mu_home_delta,"process_away":process_hazard.log_mu_away_delta}})
    return {"match_id":match_id,"cutoff":cutoff,"base_mu_home":base_mu_home,"base_mu_away":base_mu_away,
            "team_state":{"home":home_team_state,"away":away_team_state},"scenarios":internal,"player_state":{"status":player_status,"players_sha256":psha},
            "coach_tactical":coach_tactical,"match_context":match_context,"process_hazard":process_hazard,
            "provenance_manifest_sha256":provenance_manifest_sha256,"coverage_grade":coverage_grade}
