#!/usr/bin/env python3
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from adaptive_latent_identity_lock_v1 import build_identity_lock
from adaptive_latent_strength_v1 import AdaptiveLatentStrengthV1
from latent_observation_adapter_v1 import PITIntensityEvidence,adapt_completed_match_intensity
from independent_latent_1x2_v1 import Independent1X2LinkConfig,probabilities_from_latent_comparison
from favorite_fragility_v1 import decompose_favorite_fragility
from fit_independent_latent_1x2_link_v1 import fit_independent_latent_1x2_link
UTC=timezone.utc; K=datetime(2026,8,1,15,tzinfo=UTC); SHA="b"*64
def evidence(team,fixture,attack,concession):
 t=K-timedelta(days=10)
 return PITIntensityEvidence(team=team,fixture_id=fixture,competition="SYNTH",source_kind="completed_match_xg",source_identity="synthetic",source_url="https://example.invalid",payload_sha256=SHA,collector_run_id="synth",attack_intensity=attack,defence_intensity=concession,attack_reference=1.5,defence_reference=1.5,attack_observation_variance=.25,defence_observation_variance=.25,event_completed_at=t,source_published_at=t+timedelta(minutes=1),source_published_at_trusted=True,collector_first_observed_at=t+timedelta(minutes=2),retrieved_at=t+timedelta(minutes=3),ingested_at=t+timedelta(minutes=4),prediction_cutoff=K-timedelta(minutes=15))
def run():
 lock=build_identity_lock([{"competition_id":"SYNTH","fixture_id":"target-1","kickoff_at":K,"home_team_id":"H","away_team_id":"A","prediction_cutoff":K-timedelta(minutes=15)}])
 h=adapt_completed_match_intensity(evidence("H","hist-h",2.2,.7)); a=adapt_completed_match_intensity(evidence("A","hist-a",1.0,2.0)); core=AdaptiveLatentStrengthV1(); at=K-timedelta(minutes=15); core.update_team("H",attack_observation=h["attack_observation"],defence_observation=h["defence_observation"],observed_at=K-timedelta(days=9)); core.update_team("A",attack_observation=a["attack_observation"],defence_observation=a["defence_observation"],observed_at=K-timedelta(days=9)); cmp=core.compare("H","A",at=at); probs=probabilities_from_latent_comparison(cmp,config=Independent1X2LinkConfig(.15,.35,1.0)); frag=decompose_favorite_fragility(probs)
 rows=[]
 for i in range(60):
  m=-1.5+3.0*i/59; o="HOME" if m>.45 else ("AWAY" if m<-.45 else "DRAW"); rows.append({"latent_margin":m,"latent_margin_variance":.5+(i%5)*.05,"outcome":o})
 fit=fit_independent_latent_1x2_link(rows)
 ok=lock["row_count"]==1 and cmp["latent_margin"]>0 and abs(probs["home"]+probs["draw"]+probs["away"]-1)<1e-10 and frag["probabilities_reweighted"] is False and fit["test_rows_consumed"]==0
 return {"status":"PASS" if ok else "FAIL","real_target_rows":0,"real_labels":0,"provider_access":0,"market_rows":0,"formal_weight":0.0,"latent_margin":cmp["latent_margin"],"identity_lock_sha256":lock["identity_lock_sha256"],"fit_train_logloss":fit["train_logloss"]}
if __name__=="__main__": print(run())
