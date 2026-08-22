#!/usr/bin/env python3
from __future__ import annotations
import json,random,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from adaptive_latent_identity_lock_v1 import IdentityLockError,build_identity_lock
from favorite_fragility_v1 import FavoriteFragilityError,decompose_favorite_fragility
from fit_independent_latent_1x2_link_v1 import LinkFitError,fit_independent_latent_1x2_link
from independent_latent_1x2_v1 import Independent1X2LinkConfig,probabilities_from_latent_comparison
from run_adaptive_latent_stage3_synthetic_smoke_v1 import run
HERE=Path(__file__).resolve().parent; UTC=timezone.utc; K=datetime(2026,8,1,12,tzinfo=UTC)
def ident(i=1,tz=UTC):
 ko=K.astimezone(tz)+timedelta(days=i); return {"competition_id":"S","fixture_id":f"f{i}","kickoff_at":ko,"home_team_id":f"h{i}","away_team_id":f"a{i}","prediction_cutoff":ko-timedelta(minutes=15)}
def prob(m=.2,v=.6): return probabilities_from_latent_comparison({"latent_margin":m,"latent_margin_variance":v,"interpretation":"synthetic_latent_comparison_v1"},config=Independent1X2LinkConfig(.1,.35,1.0))
def train():
 out=[]
 for i in range(60):
  m=-1.5+3*i/59; out.append({"latent_margin":m,"latent_margin_variance":.5+(i%3)*.1,"outcome":"HOME" if m>.4 else ("AWAY" if m<-.4 else "DRAW")})
 return out
class Identity(unittest.TestCase):
 def test_order_independent(self):
  a=[ident(i) for i in range(1,8)]; b=list(reversed(a)); self.assertEqual(build_identity_lock(a)["identity_lock_sha256"],build_identity_lock(b)["identity_lock_sha256"])
 def test_unknown_key_denied(self):
  r=ident(); r["odds"]=2.0
  with self.assertRaises(IdentityLockError): build_identity_lock([r])
 def test_missing_key_denied(self):
  r=ident(); r.pop("fixture_id")
  with self.assertRaises(IdentityLockError): build_identity_lock([r])
 def test_bad_cutoff_denied(self):
  r=ident(); r["prediction_cutoff"]=r["kickoff_at"]-timedelta(minutes=14)
  with self.assertRaises(IdentityLockError): build_identity_lock([r])
 def test_duplicate_denied(self):
  r=ident()
  with self.assertRaises(IdentityLockError): build_identity_lock([r,dict(r)])
 def test_naive_denied(self):
  r=ident(); r["kickoff_at"]=datetime(2026,1,1)
  with self.assertRaises(IdentityLockError): build_identity_lock([r])
class Fit(unittest.TestCase):
 def test_deterministic(self): self.assertEqual(fit_independent_latent_1x2_link(train()),fit_independent_latent_1x2_link(train()))
 def test_test_rows_zero(self): self.assertEqual(fit_independent_latent_1x2_link(train())["test_rows_consumed"],0)
 def test_smuggled_fields_denied(self):
  for field in ("market_probability","test_fold","odds","actual_score","weight"):
   r=train(); r[0][field]=1
   with self.assertRaises(LinkFitError): fit_independent_latent_1x2_link(r)
 def test_bad_outcome_denied(self):
  r=train(); r[0]["outcome"]="home"
  with self.assertRaises(LinkFitError): fit_independent_latent_1x2_link(r)
class Frag(unittest.TestCase):
 def test_no_reweight(self):
  p=prob(); before=(p["home"],p["draw"],p["away"]); o=decompose_favorite_fragility(p); self.assertEqual(before,(p["home"],p["draw"],p["away"])); self.assertFalse(o["probabilities_reweighted"]); self.assertFalse(o["composite_upset_score_emitted"])
 def test_decomposition(self):
  p=prob(); o=decompose_favorite_fragility(p); self.assertAlmostEqual(o["favorite_nonwin_probability"],o["draw_against_favorite_probability"]+o["underdog_direct_win_probability"])
 def test_market_flag_denied(self):
  p=prob(); p["market_input_used"]=True
  with self.assertRaises(FavoriteFragilityError): decompose_favorite_fragility(p)
 def test_unknown_shape_denied(self):
  p=prob(); p["mystery"]=1
  with self.assertRaises(FavoriteFragilityError): decompose_favorite_fragility(p)
class Gov(unittest.TestCase):
 def test_prematerialization(self):
  c=json.loads((HERE/"adaptive_latent_stage3_prematerialization_v1.json").read_text()); self.assertEqual(c["status"],"PREMATERIALIZATION_ONLY_ZERO_REAL_ROWS"); self.assertEqual(c["real_target_rows"],0); self.assertEqual(c["formal_weight"],0)
 def test_history_freshness_unknown(self):
  c=json.loads((HERE/"adaptive_latent_global_history_audit_v1.json").read_text()); self.assertEqual(c["freshness"],"UNKNOWN_PENDING_SOURCE_REVISION_AND_TARGET_IDENTITY_LOCK"); self.assertFalse(c["verified_zero_label"])
 def test_smoke(self): self.assertEqual(run()["status"],"PASS")
class Adversarial(unittest.TestCase):
 def test_shuffle_100(self):
  base=[ident(i) for i in range(1,20)]; h=build_identity_lock(base)["identity_lock_sha256"]; rng=random.Random(7)
  for _ in range(100):
   x=list(base); rng.shuffle(x); self.assertEqual(build_identity_lock(x)["identity_lock_sha256"],h)
 def test_fragility_10000(self):
  rng=random.Random(11)
  for _ in range(10000):
   p=prob(rng.uniform(-5,5),rng.uniform(.05,5)); b=(p["home"],p["draw"],p["away"]); o=decompose_favorite_fragility(p); self.assertEqual(b,(p["home"],p["draw"],p["away"])); self.assertAlmostEqual(o["favorite_nonwin_probability"],o["draw_against_favorite_probability"]+o["underdog_direct_win_probability"],places=12)
if __name__=="__main__": unittest.main(verbosity=2)
