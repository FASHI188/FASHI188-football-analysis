#!/usr/bin/env python3
"""Probability-preserving decomposition of independent team-favorite fragility.

This helper emits components only. It does not consume market probabilities, does
not reweight H/D/A, and deliberately emits no composite upset/"爆冷" score.
"""
from __future__ import annotations
import math
from typing import Any
class FavoriteFragilityError(ValueError): pass
EXPECTED=frozenset({"schema","home","draw","away","latent_margin","latent_margin_variance","home_advantage","draw_boundary","match_noise_variance","performance_mean","performance_variance","performance_sd","standardized_performance_mean","market_input_used","score_matrix_used","research_only","formal_weight","interpretation"})
def decompose_favorite_fragility(p:dict[str,Any])->dict[str,Any]:
    if not isinstance(p,dict): raise FavoriteFragilityError("input must be dict")
    if set(p)!=EXPECTED: raise FavoriteFragilityError("input shape must exactly match independent latent 1X2 v1")
    if p.get("schema")!="football3_independent_latent_1x2_v1" or p.get("interpretation")!="research_only_independent_1x2_not_formal_probability": raise FavoriteFragilityError("unapproved schema")
    if p.get("market_input_used") is not False or p.get("score_matrix_used") is not False or p.get("research_only") is not True or float(p.get("formal_weight"))!=0.0: raise FavoriteFragilityError("governance flags mismatch")
    vals={k:float(p[k]) for k in ("home","draw","away")}
    if any(not math.isfinite(v) or v<0 or v>1 for v in vals.values()) or abs(sum(vals.values())-1)>1e-10: raise FavoriteFragilityError("invalid probabilities")
    if vals["home"]>=vals["away"]: fav="HOME"; fp=vals["home"]; up=vals["away"]
    else: fav="AWAY"; fp=vals["away"]; up=vals["home"]
    pv=float(p["performance_variance"]); lv=float(p["latent_margin_variance"]); z=abs(float(p["standardized_performance_mean"]))
    if not math.isfinite(pv) or pv<=0 or not math.isfinite(lv) or lv<=0 or lv>pv+1e-12 or not math.isfinite(z): raise FavoriteFragilityError("invalid variance/signal")
    return {"schema":"football3_favorite_fragility_components_v1","favorite_side":fav,"favorite_win_probability":fp,"favorite_nonwin_probability":1-fp,"underdog_direct_win_probability":up,"draw_against_favorite_probability":vals["draw"],"team_probability_gap":fp-up,"absolute_signal_to_noise":z,"latent_uncertainty_fraction_of_total_performance_variance":lv/pv,"probabilities_reweighted":False,"composite_upset_score_emitted":False,"market_input_used":False,"research_only":True,"formal_weight":0.0}
