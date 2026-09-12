import importlib.util, unittest
from pathlib import Path
P=Path(__file__).with_name('validate_v3_young_player_schema_identity_v1.py')
s=importlib.util.spec_from_file_location('v',P); v=importlib.util.module_from_spec(s); s.loader.exec_module(v)
class T(unittest.TestCase):
    def test_allowlist_exact_and_appearance_blocked(self):
        a=v.load_allowlist(); self.assertEqual(len(a['objects']),4)
        enabled=[x for x in a['objects'] if x.get('download_allowed')]
        blocked=[x for x in a['objects'] if not x.get('download_allowed')]
        self.assertEqual({x['relpath'] for x in enabled},{'2025/players.json.gz','2025/clubs.json.gz','2025/transfers.json'})
        self.assertEqual([x['relpath'] for x in blocked],['2025/appearances.json.gz'])
    def test_token_extractors(self):
        self.assertEqual(v.token_after('/abc/profil/spieler/123','spieler'),'123')
        self.assertEqual(v.token_after('/abc/startseite/verein/456','verein'),'456')
        self.assertEqual(v.token_after('/abc/startseite/wettbewerb/GB1','wettbewerb'),'GB1')
    def test_player_projection(self):
        rows=[{'href':'/a/profil/spieler/12','parent':{'type':'club','href':'/x/startseite/verein/34'},'date_of_birth':'Jan 02, 2000','position':'Attack - Centre-Forward'}]
        p=v.project_players(rows)[0]; self.assertEqual(p['player_id'],'12'); self.assertEqual(p['current_club_id'],'34'); self.assertEqual(p['date_of_birth'].isoformat(),'2000-01-02')
    def test_club_projection(self):
        c=v.project_clubs([{'href':'/a/startseite/verein/34','parent':{'href':'/x/startseite/wettbewerb/GB1'}}])[0]
        self.assertEqual(c['club_id'],'34'); self.assertEqual(c['domestic_competition_id'],'GB1')
    def test_transfer_projection(self):
        rows=[{'player_id':12,'response':{'transfers':[{'dateUnformatted':'2025-07-01','season':'25/26','from':{'href':'/a/startseite/verein/1'},'to':{'href':'/b/startseite/verein/2'}}]}}]
        t=v.project_transfers(rows)[0]; self.assertEqual(str(t['player_id']),'12'); self.assertEqual(t['from_club_id'],'1'); self.assertEqual(t['to_club_id'],'2'); self.assertEqual(t['transfer_date'].isoformat(),'2025-07-01')
    def test_offline_zero_label(self):
        r=v.audit(False); self.assertEqual(r['labels_opened'],0); self.assertEqual(r['target_result_or_goal_values_read'],0); self.assertFalse(r['training']); self.assertFalse(r['tuning']); self.assertFalse(r['data_ready']); self.assertEqual(r['decision'],'OFFLINE_SYNTHETIC_ONLY'); self.assertEqual([x['relpath'] for x in r['blocked_objects']],['2025/appearances.json.gz'])
if __name__=='__main__': unittest.main()
