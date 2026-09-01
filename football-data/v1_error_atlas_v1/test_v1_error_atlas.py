from __future__ import annotations
import math, unittest
import v1_error_atlas as a


def row(actual='home', ph=.5, pd=.25, pa=.25, hg=1, ag=0, total=2.5, gap=.2, fav=.5, cold='established', comp='ENG1', season='2022-23'):
    top=max((('home',ph),('draw',pd),('away',pa)),key=lambda x:x[1])[0]
    return {'actual_class':actual,'p_home':ph,'p_draw':pd,'p_away':pa,'home_goals':hg,'away_goals':ag,'mu_home':1.4,'mu_away':1.1,'p_0_0':.08,'p_1_1':.12,'p_2_2':.025,'pred_total':total,'strength_gap':gap,'favorite_p':fav,'cold_start_bucket':cold,'competition_id':comp,'season':season,'top1':top}

class AtlasTests(unittest.TestCase):
    def test_metrics_finite(self):
        z=[row('home'),row('draw',.35,.35,.30,1,1),row('away',.3,.25,.45,0,1)]
        m=a.base_metrics(z)
        self.assertEqual(m['n'],3); self.assertTrue(math.isfinite(m['logloss'])); self.assertTrue(0<=m['top1']<=1)

    def test_calibration_bins_cover_rows(self):
        z=[row('home',.55,.25,.20),row('away',.15,.25,.60,0,1)]
        c=a.calibration(z,'home'); self.assertEqual(sum(x['n'] for x in c['bins']),2)

    def test_group_logloss_share(self):
        z=[row(comp='ENG1'),row('away',.25,.25,.50,0,1,comp='ESP1')]
        g=a.group_report(z,lambda r:r['competition_id']); self.assertAlmostEqual(sum(x['share_total_logloss'] for x in g.values()),1.0,places=12)

    def test_joint_rule_requires_both_datasets(self):
        base={'draw':{'gap_actual_minus_predicted':.02},'low_score_draw_cells':{'gap_actual_minus_predicted':.02},'calibration':{'mean_class_ece':.01,'classes':{c:{'bias_pred_minus_actual':0} for c in ('home','draw','away')}},'diagnostic_ll_opportunity_proxies_nonadditive':{'draw_total_structure_ll_proxy':.002,'multiclass_calibration_ll_proxy':.0,'team_strength_ll_proxy':0,'competition_season_ll_proxy':0}}
        d=a.decision(base,base); self.assertEqual(d['recommendation'],'V1_JOINT_SCORE_CHALLENGER')

    def test_calibration_rule_only_after_joint_fails(self):
        x={'draw':{'gap_actual_minus_predicted':0},'low_score_draw_cells':{'gap_actual_minus_predicted':0},'calibration':{'mean_class_ece':.03,'classes':{'home':{'bias_pred_minus_actual':.02},'draw':{'bias_pred_minus_actual':-.02},'away':{'bias_pred_minus_actual':0}}},'diagnostic_ll_opportunity_proxies_nonadditive':{'draw_total_structure_ll_proxy':0,'multiclass_calibration_ll_proxy':.003,'team_strength_ll_proxy':0,'competition_season_ll_proxy':0}}
        d=a.decision(x,x); self.assertEqual(d['recommendation'],'V1_2_MULTICLASS_CALIBRATION_LAYER')

    def test_stop_when_no_stable_target(self):
        x={'draw':{'gap_actual_minus_predicted':.005},'low_score_draw_cells':{'gap_actual_minus_predicted':.004},'calibration':{'mean_class_ece':.015,'classes':{c:{'bias_pred_minus_actual':0} for c in ('home','draw','away')}},'diagnostic_ll_opportunity_proxies_nonadditive':{'draw_total_structure_ll_proxy':0,'multiclass_calibration_ll_proxy':0,'team_strength_ll_proxy':0,'competition_season_ll_proxy':0}}
        self.assertEqual(a.decision(x,x)['recommendation'],'STOP_NO_STABLE_ERROR_TARGET')

if __name__=='__main__': unittest.main()
