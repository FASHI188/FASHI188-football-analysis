from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any
FORMAL_HEAD="e12f5d1193be5d81f60301cf34ab2140e11712a9"
W=.75
EXPECTED=1826
LEAGUE={"EPL":"EPL","Bundesliga":"Bundesliga","La liga":"La_liga","La_liga":"La_liga","Ligue 1":"Ligue_1","Ligue_1":"Ligue_1","Serie A":"Serie_A","Serie_A":"Serie_A"}
class N6SealError(RuntimeError): pass
def req(c,m):
    if not c: raise N6SealError(m)
def canon(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def readl(p:Path):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def mix(v1,xg):
    keys=("p_home","p_draw","p_away"); fb=bool((xg.get("dynamic") or {}).get("fallback_exact_v1",False))
    if fb:
        for k in keys:req(abs(float(v1[k])-float(xg[k]))<=1e-15,f"FALLBACK_NOT_EXACT:{k}")
        return [float(v1[k]) for k in keys], True
    q=[(1-W)*float(v1[k])+W*float(xg[k]) for k in keys]; z=sum(q);req(z>0,"BAD_Z");return [v/z for v in q],False
def run(prereg:Path,xg_path:Path,cross_path:Path,out:Path):
    p=json.loads(prereg.read_text());req(p["status"]=="DESIGN_LOCKED_PRELABEL","PREREG");req(p["formal_v2"]["head"]==FORMAL_HEAD,"FORMAL_HEAD")
    xg=readl(xg_path); s20=[r for r in xg if int(r["season"])==2020 and r["league"] in LEAGUE];s22=[r for r in xg if int(r["season"])==2022 and r["league"] in LEAGUE]
    req(len(s20)==EXPECTED and len(s22)==EXPECTED,f"XG_COUNTS:{len(s20)}:{len(s22)}")
    cross=readl(cross_path);req(len(cross)==EXPECTED,"CROSS_N");by_formal={str(r["formal_fixture_id"]):r for r in cross};req(len(by_formal)==EXPECTED,"CROSS_DUP")
    maxdiff=0.0
    for r in s22:
        fid=str(r["fixture_id"]);req(fid in by_formal,f"CROSS_MISSING:{fid}"); c=by_formal[fid];prob,_=mix(r["v1"],r["challenger"])
        req(str(c["home_team_id"])==str(r["home_team_id"]) and str(c["away_team_id"])==str(r["away_team_id"]),f"CROSS_TEAM:{fid}")
        for a,b in zip(prob,c["formal_v2_1x2"]):maxdiff=max(maxdiff,abs(float(a)-float(b)))
    req(maxdiff<=1e-12,f"CROSS_REPRO_DRIFT:{maxdiff}")
    rows=[]
    for r in sorted(s20,key=lambda z:(z["kickoff"],z["fixture_id"])):
        prob,fb=mix(r["v1"],r["challenger"]);rows.append({"n6_fixture_id":str(r["fixture_id"]),"formal_fixture_id":str(r["fixture_id"]),"league":LEAGUE[str(r["league"])],"season_start":2020,"kickoff":str(r["kickoff"]),"home_team_id":str(r["home_team_id"]),"away_team_id":str(r["away_team_id"]),"formal_v2_1x2":prob,"fallback_exact_v1":fb,"model_head":FORMAL_HEAD,"target_label_read":False})
    req(len(rows)==EXPECTED and len({r["n6_fixture_id"] for r in rows})==EXPECTED,"OUT_N")
    out.mkdir(parents=True,exist_ok=True);fp=out/"formal_v2_baseline_2020_label_free.jsonl";fp.write_text("".join(canon(r).decode()+"\n" for r in rows))
    rec={"schema_version":"football3-nova-n6-formal-v2-baseline-seal-v1","status":"N6_FORMAL_V2_2020_LABEL_FREE_SEAL_PASS","formal_v2_head":FORMAL_HEAD,"formal_v2_weight":W,"development_season":2020,"development_n":EXPECTED,"crosscheck_season":2022,"crosscheck_n":EXPECTED,"max_abs_2022_formal_reproduction_diff":maxdiff,"development_prediction_sha256":sha(fp.read_bytes()),"xg_frozen_input_sha256":sha(xg_path.read_bytes()),"crosscheck_input_sha256":sha(cross_path.read_bytes()),"result_labels_read":0,"score_values_read":0,"isolated_2021_labels_read":0,"isolated_2023_labels_read":0,"isolated_2025_labels_read":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0}
    (out/"baseline_seal_receipt.json").write_text(json.dumps(rec,indent=2,sort_keys=True)+"\n");return rec
def main():
    a=argparse.ArgumentParser();a.add_argument("--prereg",type=Path,required=True);a.add_argument("--xg",type=Path,required=True);a.add_argument("--crosscheck",type=Path,required=True);a.add_argument("--out",type=Path,required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.xg,x.crosscheck,x.out),sort_keys=True))
if __name__=="__main__":main()
