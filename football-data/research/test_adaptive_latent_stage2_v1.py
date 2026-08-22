#!/usr/bin/env python3
from __future__ import annotations
import json, math, unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from adaptive_latent_strength_v1 import AdaptiveLatentStrengthV1
from latent_observation_adapter_v1 import ObservationAdapterError,PITIntensityEvidence,adapt_completed_match_intensity
HERE=Path(__file__).resolve().parent; UTC=timezone.utc; T0=datetime(2026,1,1,12,tzinfo=UTC); SHA="a"*64
class T(unittest.TestCase):
 def ev(self,**kw):
  b=dict(team="A",fixture_id="f1",competition="SYNTH",source_kind="completed_match_xg",source_identity="s",source_url="https://example.invalid",payload_sha256=SHA,collector_run_id="r",attack_intensity=1.8,defence_intensity=.9,attack_reference=1.5,defence_reference=1.5,attack_observation_variance=.3,defence_observation_variance=.4,event_completed_at=T0,source_published_at=T0+timedelta(minutes=2),source_published_at_trusted=True,collector_first_observed_at=T0+timedelta(minutes=5),retrieved_at=T0+timedelta(minutes=6),ingested_at=T0+timedelta(minutes=7),prediction_cutoff=T0+timedelta(days=3)); b.update(kw); return PITIntensityEvidence(**b)
 def test_attack_orientation(self): self.assertGreater(adapt_completed_match_intensity(self.ev())["attack_observation"],0)
 def test_better_defence_is_positive_resistance(self): self.assertGreater(adapt_completed_match_intensity(self.ev(defence_intensity=.75))["defence_observation"],0)
 def test_worse_defence_is_negative_resistance(self): self.assertLess(adapt_completed_match_intensity(self.ev(defence_intensity=2.25))["defence_observation"],0)
 def test_reference_is_zero(self):
  o=adapt_completed_match_intensity(self.ev(attack_intensity=1.5,defence_intensity=1.5)); self.assertAlmostEqual(o["attack_observation"],0); self.assertAlmostEqual(o["defence_observation"],0)
 def test_integration_sign_matches_core(self):
  h=adapt_completed_match_intensity(self.ev(team="H",fixture_id="h",attack_intensity=2.2,defence_intensity=.7)); a=adapt_completed_match_intensity(self.ev(team="A",fixture_id="a",attack_intensity=1.0,defence_intensity=2.0)); m=AdaptiveLatentStrengthV1(); m.update_team("H",attack_observation=h["attack_observation"],defence_observation=h["defence_observation"],observed_at=T0); m.update_team("A",attack_observation=a["attack_observation"],defence_observation=a["defence_observation"],observed_at=T0); self.assertGreater(m.compare("H","A",at=T0+timedelta(days=1))["latent_margin"],0)
 def test_unknown_source_rejected(self):
  with self.assertRaises(ObservationAdapterError): adapt_completed_match_intensity(self.ev(source_kind="market_odds"))
 def test_equal_cutoff_rejected(self):
  c=self.ev().prediction_cutoff
  with self.assertRaises(ObservationAdapterError): adapt_completed_match_intensity(self.ev(source_published_at=c,collector_first_observed_at=c))
 def test_naive_time_rejected(self):
  with self.assertRaises(ObservationAdapterError): adapt_completed_match_intensity(self.ev(prediction_cutoff=datetime(2026,1,2)))
 def test_bad_hash_rejected(self):
  with self.assertRaises(ObservationAdapterError): adapt_completed_match_intensity(self.ev(payload_sha256="abc"))
 def test_contract_boundaries(self):
  c=json.loads((HERE/"adaptive_latent_direct_1x2_prelabel_contract_v1.json").read_text()); self.assertEqual(c["formal_primary_target_unchanged"],"P(T=0,1,2,3,4,5,6,7+)"); self.assertFalse(c["direct_formal_promotion_allowed"]); self.assertEqual(c["target_access"]["real_target_rows_read"],0); self.assertEqual(c["formal_weight"],0)
if __name__=="__main__": unittest.main(verbosity=2)
