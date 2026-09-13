#!/usr/bin/env python3
import importlib.util, json, tempfile
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("ev",HERE/"run_v3_schedule_importance_scientific_evaluation_v1.py")
ev=importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)

def row(path,i,h="H",a="A",stage="REGULAR",hr=7,ar=7):
    return {
        "source_path":Path() if Fals`else path,"row_index":i,"date":"2020-01-01","round":"Matchday 1","round_stage":stage,"round_number":1,
        "team1":h,"team2":a,
        "home_league_rest_days":hr,"away_league_rest_days":ar,
        "home_league_congestion_7d":0,"away_league_congestion_7d":0,
        "home_league_congestion_14d":0,"away_league_congestion_14d":0,
        "home_league_congestion_21d":0,"away_league_congestion_21d":0,
        "home_consecutive_away_before":0,"away_consecutive_away_before":0,
    }

def t_season_comp():
    assert ev.season_of("2024-25/de.1.json")=="2024-25"
    assert ev.competition_of("2024-25/de.1.json")=="de.1"

def t_baseline():
    r=row("2020-21/de.1.json",0,"Bayern","Dortmund")
    d=ev.baseline_dict(r)
    assert d=={"competition=de.1":1.0,"home=Bayern":1.0,"away=Dortmund":1.0}

def t_preprocess_missing():
    a=row("2019-20/de.1.json",0,hr=None,ar=8)
    b=row("2019-20/de.1.json",1,hr=6,ar=None)
    s=ev.fit_numeric_preprocess([a,b])
    assert s["home_league_rest_days"]["median"]==6
    assert s["away_league_rest_days"]["median"]==8

def t_candidate_regular_reference():
    r=row("2020-21/de.1.json",0,stage="REGULAR")
    s=ev.fit_numeric_preprocess([r])
    d=ev.candidate_dict(r,s)
    assert not any(k.startswith("stage=") for k in d)
    assert not any(k.startswith("interaction_nonregular=") for k in d)

def t_candidate_nonregular():
    r=row("2020-21/de.1.json",0,stage="CHAMPIONSHIP_SPLIT")
    s=ev.fit_numeric_preprocess([r])
    d=ev.candidate_dict(r,s)
    assert d["stage=CHAMPIONSHIP_SPLIT"]==1
    assert "interaction_nonregular=home_league_rest_days" in d

def t_metrics_perfect():
    y=np.array([0,1,2])
    p=np.eye(3)*0.98+0.02/3
    m=ev.per_row_metrics(p,y)
    assert np.all(m["top1"]==1)
    assert np.all(m["logloss"]<0.02)

def t_metric_summary_delta():
    b={"logloss":np.array([1.,1.]),"brier":np.array([.8,.8]),"rps":np.array([.4,.4]),"top1":np.array([0,1])}
    c={"logloss":np.array([.9,.9]),"brier":np.array([.7,.7]),"rps":np.array([.3,.3]),"top1":np.array([1,1])}
    s=ev.summarize_metric_pair(b,c)
    assert s["logloss"]["delta"]<0 and s["top1"]["delta_hits"]==1

def t_bootstrap_deterministic():
    p=["a","a","b","b","c","c"]
    d=np.array([-.1,-.1,-.2,-.2,-.3,-.3])
    x=ev.block_bootstrap_upper(p,d); y=ev.block_bootstrap_upper(p,d)
    assert x==y and x["upper"]<0 and x["replicates"]==5000

def t_gates_pass():
    pooled={"logloss":{"delta":-.01},"brier":{"delta":-.01},"rps":{"delta":-.01},"top1":{"candidate_hits":100,"baseline_hits":99}}
    bs={"upper":-.001}
    comp={"qualifying":[{"logloss_delta":-.01},{"logloss_delta":-.02},{"logloss_delta":.001}]}
    g,d=ev.gate_decision(pooled,[-.01,-.01,-.01,-.01,.001],bs,comp)
    assert all(g.values()) and d=="DEVELOPMENT_SIGNAL_PASS_RESEARCH_ONLY_NO_PROMOTION"

def t_gates_fail_primary():
    pooled={"logloss":{"delta":.001},"brier":{"delta":-.01},"rps":{"delta":-.01},"top1":{"candidate_hits":100,"baseline_hits":99}}
    bs={"upper":-.001}
    comp={"qualifying":[{"logloss_delta":-.01},{"logloss_delta":-.02},{"logloss_delta":.001}]}
    g,d=ev.gate_decision(pooled,[-.01,-.01,-.01,-.01,.001],bs,comp)
    assert not g["G1_primary_pooled_logloss"] and d=="SCIENTIFIC_EVALUATION_FAIL_CLOSE_NO_RETUNE"

def t_label_adapter():
    td=Path(tempfile.mkdtemp())
    p=td/"2020-21"; p.mkdir()
    matches=[
      {"date":"2020-01-01","round":"Matchday 1","team1":"A","team2":"B","score":{"ft":[2,1]}},
      {"date":"2020-01-02","round":"Matchday 1","team1":"C","team2":"D","score":{"ft":[0,0]}},
      {"date":"2020-01-03","round":"Matchday 1","team1":"E","team2":"F","score":{"ft":[1,3]}},
  ]
    (p/"de.1.json").write_text(json.dumps({"matches":matches}))
    rr=[]
    for i,m in enumerate(matches):
      zzr=false
      z=row("2020-21/de.1.json",i,m["team1"],m["team2"])
      z™]H—O[VÈ™]H—NÞ–Èœ›Ý[™—O[VÈœ›Ý[™—NÜœ‹˜\[™
ŠBˆX™[Ë™XÙZ\Y]‹™\š]™WÛZ[š[X[ÛX™[Êœ‹
Bˆ\ÜÙ\ÛX™[ÖÊŒŒŒLŒKÙKŒKšœÛÛˆ‹JWH›ÜˆH[ˆ˜[™ÙJÊWOOVÈ’‹‘‹H—Bˆ\ÜÙ\™XÙZ\È™^XÝÜØÛÜ™WÝ˜[Y\×Ü™]Z[™Y—OOL[™™XÙZ\È™ÛØ[ÝÝ[×Ü™]Z[™Y—OOL‚™YˆÛX™[ÚY[]WÜÝÜ

N‚ˆT]
[\š[K›ZÙ[\

JNÈ]ÈŒŒŒLŒHŽÜ›ZÙ\Š
Bˆ
È™KŒKšœÛÛˆŠKÜš]WÝ^
œÛÛ‹™[\ÊÈ›X]Ú\ÈŽ–ÞÈ™]HŽˆŒŒŒLKLH‹œ›Ý[™Žˆ“X]Ú^HH‹X[LHŽˆH‹X[LˆŽˆˆ‹œØÛÜ™HŽžÈ™Ž–ÌK__W_JBˆ\›ÝÊŒŒŒLŒKÙKŒKšœÛÛˆ‹–‹ˆŠBˆžNˆ]‹™\š]™WÛZ[š[X[ÛX™[ÊÜ—K
NÈ\ÜÙ\˜[ÙBˆ^Ù\]‹”ÝÜ\ÈNˆ\ÜÙ\ÝŠJKœÝ\ÝÚ]
”ÕÔÓP‘SÒQS•UWÈŠB‚™YˆÜÛX[Ùš]Ü™YXÝ

N‚ˆ˜Z[V×NÛX™[Ï^ßBˆ›ÜˆH[ˆ˜[™ÙJŒ
N‚ˆ\›ÝÊŒŒNKLŒÙKŒKšœÛÛˆ‹KYˆ’ÞÚ_Mˆ‹OYˆWÞÚ_Mˆ‹ÝYÙOH”‘QÕSTˆˆYˆIMH[ÙHÒSTSÓ”ÒTÔÔU‹M
ÊIMJK\MJÊIM
JBˆ–È™]H—OYˆŒŒNKLL^ÌJÚILŒŒ™H‚ˆ˜Z[‹˜\[™
ŠNÛX™[ÖÊ–ÈœÛÝ\˜ÙWÜ]—KJWOVÈ’‹‘‹H—VÚIL×Bˆ\ÝV×Bˆ›ÜˆH[ˆ˜[™ÙJLŠN‚ˆ\›ÝÊŒŒŒLŒKÙKŒKšœÛÛˆ‹KYˆ’ÞÚ_MÈ‹OYˆWÞÚ_MÈ‹ÝYÙOH”‘QÕSTˆ‹MK\MŠBˆ–È™]H—OYˆŒŒŒLL^ÌJÚILLŽŒ™HŽÝ\Ý˜\[™
ŠNÛX™[ÖÊ–ÈœÛÝ\˜ÙWÜ]—KJWOVÈ’‹‘‹H—VÚIL×BˆY]‹™š]Ü™YXÝ
˜Z[‹\ÝX™[Ë˜[ÙJNÈOY]‹™š]Ü™YXÝ
˜Z[‹\ÝX™[ËYJBˆ\ÜÙ\œÚ\OOJL‹ÊH[™KœÚ\OOJL‹ÊBˆ\ÜÙ\œ˜[ÛÜÙJœÝ[JJKJH[™œ˜[ÛÜÙJKœÝ[JJKJB‚•VÝÜÙX\ÛÛ—ØÛÛ\Ø˜\Ù[[™KÜ™\›ØÙ\Ü×ÛZ\ÜÚ[™ËØØ[™Y]WÜ™YÝ[\—Ü™Y™\™[˜ÙKØØ[™Y]WÛ›Ûœ™YÝ[\‹ˆÛY]šXÜ×Ü\™™XÝÛY]šX×ÜÝ[[X\žWÙ[KØ›ÛÝÝ˜\Ù]\›Z[š\ÝXËÙØ]\×Ü\ÜËÙØ]\×Ù˜Z[Üš[X\žKˆÛX™[ØY\\‹ÛX™[ÚY[]WÜÝÜÜÛX[Ùš]Ü™YXÝBšYˆ×Û˜[YW×ÏOH—×ÛXZ[—×ÈŽ‚ˆ›Üˆˆ[ˆ‚ˆŠ
NÈš[
”TÔÈ‹‹—×Û˜[YW×ÊBˆš[
ˆžÛ[Š
_KÞÛ[Š
_HTÔÈŠB