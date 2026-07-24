#!/usr/bin/env python3
"""V6.14.1 research-only Fast100: O/U 2.5 multi-book consensus and disagreement.

Compare Pinnacle O/U2.5 de-vigged direction with Bet365 and market-average two-sided
O/U2.5 prices. The final 100 2025/26 matches with all three providers are fixed test.
No model fitting and no target-outcome-based threshold selection.
Historical prices lack original quote timestamps; research only.
"""
from __future__ import annotations
import csv,json,math
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from platform_core import canonical_team_name,load_aliases,parse_match_date

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_total25_multibook_fast100_v6141_status.json'
COMPS=['ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga']
SEASON='2025/26'
PROVIDERS={'Pinnacle':('P>2.5','P<2.5'),'Bet365':('B365>2.5','B365<2.5'),'Average':('Avg>2.5','Avg<2.5')}

def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 and math.isfinite(x) else None

def devig(o,u):
    qo,qu=1/o,1/u;s=qo+qu;return qo/s,qu/s

def load():
    aliases=load_aliases();out={};available={k:0 for k in PROVIDERS}
    for cid in COMPS:
        d=ROOT/'processed'/cid
        if not d.exists():continue
        for path in sorted(d.glob('*.csv')):
            with path.open('r',encoding='utf-8-sig',newline='') as fh:
                rd=csv.DictReader(fh);fields=set(rd.fieldnames or [])
                present={name:(o,u) for name,(o,u) in PROVIDERS.items() if o in fields and u in fields}
                for r0 in rd:
                    r={str(k):'' if v is None else str(v) for k,v in r0.items() if k}
                    season=str(r.get('season') or r.get('Season') or '').strip()
                    if season!=SEASON or not r.get('HomeTeam') or not r.get('AwayTeam') or not r.get('Date'):continue
                    try:hg=int(float(r.get('FTHG','')));ag=int(float(r.get('FTAG','')))
                    except:continue
                    ps={}
                    for name,(oc,uc) in present.items():
                        o=f(r.get(oc));u=f(r.get(uc))
                        if o is not None and u is not None:ps[name]=devig(o,u);available[name]+=1
                    if len(ps)<3:continue
                    try:dt=parse_match_date(r['Date'],season)
                    except:continue
                    home=canonical_team_name(cid,r['HomeTeam'],aliases);away=canonical_team_name(cid,r['AwayTeam'],aliases)
                    actual=1 if hg+ag>=3 else 0
                    probs=[ps[x][0] for x in ('Pinnacle','Bet365','Average')]
                    picks=[int(p>=0.5) for p in probs];cons=float(np.mean(probs));disp=max(probs)-min(probs)
                    out[(cid,dt.isoformat(),home,away)]={'competition_id':cid,'date':dt.isoformat(),'actual':actual,'pinnacle':probs[0],'bet365':probs[1],'average':probs[2],'consensus':cons,'dispersion':disp,'unanimous':len(set(picks))==1,'picks':picks}
    return sorted(out.values(),key=lambda r:(r['date'],r['competition_id'])),available

def pick_prob(p):return int(p>=0.5)
def stat(rows,picker,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(int(picker(r)==r['actual']) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None,'coverage':len(s)/len(rows) if rows else 0.0}
def main():
    rows,available=load()
    if len(rows)<100:raise RuntimeError(f'insufficient triple-provider rows: {len(rows)}')
    test=rows[-100:]
    result={
      'pinnacle':stat(test,lambda r:pick_prob(r['pinnacle'])),
      'bet365':stat(test,lambda r:pick_prob(r['bet365'])),
      'average':stat(test,lambda r:pick_prob(r['average'])),
      'mean_consensus':stat(test,lambda r:pick_prob(r['consensus'])),
      'pinnacle_unanimous':stat(test,lambda r:pick_prob(r['pinnacle']),lambda r:r['unanimous']),
      'consensus_unanimous':stat(test,lambda r:pick_prob(r['consensus']),lambda r:r['unanimous']),
      'consensus_low_disp_0.02':stat(test,lambda r:pick_prob(r['consensus']),lambda r:r['dispersion']<=0.02),
      'consensus_low_disp_0.03':stat(test,lambda r:pick_prob(r['consensus']),lambda r:r['dispersion']<=0.03),
      'consensus_conf_ge_0.56':stat(test,lambda r:pick_prob(r['consensus']),lambda r:max(r['consensus'],1-r['consensus'])>=0.56),
      'consensus_conf_ge_0.58':stat(test,lambda r:pick_prob(r['consensus']),lambda r:max(r['consensus'],1-r['consensus'])>=0.58),
    }
    payload={'schema_version':'V6.14.1-total25-multibook-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','governance':{'no_model_fit':True,'fixed_rules_only':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'triple_provider_rows':len(rows),'test_first':test[0]['date'],'test_last':test[-1]['date'],'provider_available_rows':available},'test':result}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
