from __future__ import annotations
import argparse, json, math, pathlib, hashlib
from collections import defaultdict
from datetime import datetime

TOL=1e-12
EPS=1e-15

class DiagnosticError(RuntimeError): pass

def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

def read_jsonl(path):
    out=[]
    with pathlib.Path(path).open("r",encoding="utf-8") as f:
        for line in f:
            if line.strip(): out.append(json.loads(line))
    return out

def write_json(path,obj):
    p=pathlib.Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")

def sha_file(path):
    h=hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def outcome(hg,ag): return 0 if hg>ag else 1 if hg==ag else 2

def one_metrics(rows,prefix):
    n=len(rows)
    if not n: return None
    ll=br=rps=top=0.0
    for r in rows:
        p=r[prefix]; y=r["y"]
        ll += -math.log(max(p[y],EPS))
        br += sum((p[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
        c1=p[0]-(1.0 if y==0 else 0.0)
        c2=p[0]+p[1]-(1.0 if y in (0,1) else 0.0)
        rps += (c1*c1+c2*c2)/2.0
        top += int(max(range(3),key=lambda i:p[i])==y)
    return {"n":n,"logloss":ll/n,"brier":br/n,"rps":rps/n,"top1":top/n}

def auc_rank(y,p):
    pairs=sorted(zip(p,y),key=lambda z:z[0])
    n1=sum(y); n0=len(y)-n1
    if n1==0 or n0==0: return None
    rank_sum=0.0; i=0; rank=1
    while i<len(pairs):
        j=i+1
        while j<len(pairs) and pairs[j][0]==pairs[i][0]: j+=1
        avg=(rank+(rank+(j-i)-1))/2.0
        rank_sum += avg*sum(v for _,v in pairs[i:j])
        rank += j-i; i=j
    return (rank_sum-n1*(n1+1)/2.0)/(n1*n0)

def average_precision(y,p):
    order=sorted(range(len(y)),key=lambda i:(-p[i],i))
    pos=sum(y)
    if pos==0: return None
    tp=0; acc=0.0
    for k,i in enumerate(order,1):
        if y[i]:
            tp+=1; acc += tp/k
    return acc/pos

def binary_metrics(y,p):
    n=len(y)
    if not n: return None
    pp=[min(1-EPS,max(EPS,float(x))) for x in p]
    yy=[int(v) for v in y]
    ll=sum(-(a*math.log(b)+(1-a)*math.log(1-b)) for a,b in zip(yy,pp))/n
    br=sum((a-b)**2 for a,b in zip(yy,pp))/n
    return {"n":n,"event_rate":sum(yy)/n,"mean_probability":sum(pp)/n,
            "logloss":ll,"brier":br,"calibration_abs_error":abs(sum(pp)/n-sum(yy)/n),
            "roc_auc":auc_rank(yy,pp),"average_precision":average_precision(yy,pp)}

def expected_total(m):
    return sum((i+j)*float(m[i][j]) for i in range(15) for j in range(15))

def region_cells(region):
    if region==0: return [(i,j) for i in range(15) for j in range(15) if i>j]
    if region==1: return [(i,i) for i in range(15)]
    return [(i,j) for i in range(15) for j in range(15) if i<j]

REG={k:region_cells(k) for k in range(3)}

def region_mass(m,reg): return sum(float(m[i][j]) for i,j in REG[reg])

def conditional_shape_diff(a,b):
    mx=0.0
    for reg in range(3):
        ma=region_mass(a,reg); mb=region_mass(b,reg)
        if ma<=0 or mb<=0: raise DiagnosticError("zero region mass")
        for i,j in REG[reg]:
            mx=max(mx,abs(float(a[i][j])/ma-float(b[i][j])/mb))
    return mx

def score_rank(m,hg,ag):
    if hg>14 or ag>14: return 999
    true=float(m[hg][ag])
    return 1+sum(1 for i in range(15) for j in range(15) if float(m[i][j])>true)

def subgroup(rows,mask):
    rs=[r for r in rows if mask(r)]
    if not rs: return {"n":0}
    b=one_metrics(rs,"formal"); c=one_metrics(rs,"candidate")
    return {"n":len(rs),"formal_1x2":b,"candidate_1x2":c,
            "one_x_two_logloss_gain":b["logloss"]-c["logloss"],
            "exact_score_logloss_gain":sum(r["score_gain"] for r in rs)/len(rs),
            "conditional_score_logloss_gain":sum(r["cond_gain"] for r in rs)/len(rs),
            "draw_event_rate":sum(r["y"]==1 for r in rs)/len(rs),
            "weak_win_rate":sum(r["weak_win"] for r in rs)/len(rs),
            "strong_win_rate":sum(r["strong_win"] for r in rs)/len(rs)}

def calibr_bins(rows,edges):
    out=[]
    for lo,hi in zip(edges[:-1],edges[1:]):
        rs=[r for r in rows if r["weak_prob_formal"]>=lo and (r["weak_prob_formal"]<hi or hi==1.0 and r["weak_prob_formal"]<=hi)]
        if not rs: continue
        out.append({"lo":lo,"hi":hi,"n":len(rs),"mean_p":sum(r["weak_prob_formal"] for r in rs)/len(rs),
                    "observed_rate":sum(r["weak_win"] for r in rs)/len(rs)})
    return out

def false_positive(rows,thresholds,pkey):
    out=[]; non=[r for r in rows if not r["weak_win"]]
    for t in thresholds:
        alerts=[r for r in rows if r[pkey]>=t]
        fp=[r for r in non if r[pkey]>=t]
        tp=[r for r in rows if r["weak_win"] and r[pkey]>=t]
        out.append({"threshold":t,"alert_n":len(alerts),"true_upset_alert_n":len(tp),"false_alert_n":len(fp),
                    "false_positive_rate":len(fp)/len(non),"precision":(len(tp)/len(alerts) if alerts else None)})
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--contract",type=pathlib.Path,required=True)
    ap.add_argument("--stress-dir",type=pathlib.Path,required=True)
    ap.add_argument("--data-dir",type=pathlib.Path,required=True)
    ap.add_argument("--out",type=pathlib.Path,required=True)
    a=ap.parse_args(); c=read_json(a.contract)
    if c["status"]!="FROZEN_DIAGNOSTIC_DEFINITIONS_POST_VIEW": raise DiagnosticError("contract status")
    sr=read_json(a.stress_dir/"historical_stress_result.json")
    if sr["status"]!="V3_1_1_HISTORICAL_STRESS_TEST_PASSED" or sr["n"]!=c["source"]["fixture_n"]: raise DiagnosticError("stress result identity")
    preds=read_jsonl(a.stress_dir/"predictions_label_free.jsonl"); fixtures=read_jsonl(a.data_dir/"fixtures.jsonl")
    labels=read_jsonl(a.data_dir/"label_vault.jsonl"); updates=read_jsonl(a.data_dir/"state_updates.jsonl"); dm=read_json(a.data_dir/"data_manifest.json")
    if len(preds)!=3504 or len(fixtures)!=3504 or len(labels)!=3504 or len(updates)!=3504: raise DiagnosticError("count mismatch")
    if dm["fixture_identity_sha256"]!=c["source"]["fixture_identity_sha256"]: raise DiagnosticError("fixture identity")
    fm={r["fixture_id"]:r for r in fixtures}; lm={r["fixture_id"]:r for r in labels}; um={r["fixture_id"]:r for r in updates}
    rows=[]; max_shape=0.0; max_weak_delta=0.0; matrix_equal_n=0; p_equal_n=0
    for p in preds:
        fid=p["fixture_id"]
        if fid not in fm or fid not in lm or fid not in um: raise DiagnosticError("join identity")
        f=fm[fid]; lab=lm[fid]; bm=p["formal_matrix"]; cm=p["v3_1_1_matrix"]
        bp=[float(x) for x in p["formal_v2_1x2"]]; cp=[float(x) for x in p["v3_1_1_1x2"]]
        if abs(sum(bp)-1)>TOL or abs(sum(cp)-1)>TOL: raise DiagnosticError("1x2 normalization")
        for reg in range(3):
            if abs(region_mass(bm,reg)-bp[reg])>TOL or abs(region_mass(cm,reg)-cp[reg])>TOL: raise DiagnosticError("matrix integration")
        max_shape=max(max_shape,conditional_shape_diff(bm,cm))
        md=max(abs(float(bm[i][j])-float(cm[i][j])) for i in range(15) for j in range(15))
        matrix_equal_n += int(md<=1e-15); p_equal_n += int(max(abs(bp[i]-cp[i]) for i in range(3))<=1e-15)
        hg=int(lab["home_goals"]); ag=int(lab["away_goals"]); y=outcome(hg,ag)
        strong=0 if bp[0]>=bp[2] else 2; weak=2 if strong==0 else 0; max_weak_delta=max(max_weak_delta,abs(cp[weak]-bp[weak]))
        bscore=float(bm[hg][ag]) if hg<15 and ag<15 else EPS; cscore=float(cm[hg][ag]) if hg<15 and ag<15 else EPS
        bll=-math.log(max(bp[y],EPS)); cll=-math.log(max(cp[y],EPS)); bsll=-math.log(max(bscore,EPS)); csll=-math.log(max(cscore,EPS))
        ent=-sum(x*math.log(max(x,EPS)) for x in bp); ko=datetime.fromisoformat(f["kickoff"])
        rows.append({"fixture_id":fid,"league":f["league"],"season":int(f["season"]),"kickoff":f["kickoff"],
                     "calendar_half":"H1_AugDec" if 8<=ko.month<=12 else "H2_JanJul","formal":bp,"candidate":cp,"y":y,"hg":hg,"ag":ag,
                     "strong_idx":strong,"weak_idx":weak,"strong_win":y==strong,"weak_win":y==weak,"weak_home":weak==0,"strong_home":strong==0,
                     "weak_prob_formal":bp[weak],"weak_prob_candidate":cp[weak],"strong_prob_formal":bp[strong],"strong_prob_candidate":cp[strong],
                     "gap":abs(bp[0]-bp[2]),"entropy":ent,"maxp":max(bp),"pred_total":expected_total(bm),
                     "score_gain":bsll-csll,"cond_gain":(bsll-bll)-(csll-cll),
                     "score_top1_formal":score_rank(bm,hg,ag)<=1,"score_top3_formal":score_rank(bm,hg,ag)<=3,
                     "score_top1_candidate":score_rank(cm,hg,ag)<=1,"score_top3_candidate":score_rank(cm,hg,ag)<=3,
                     "process_update_eligible":bool(um[fid]["process_update_eligible"])})
    base=one_metrics(rows,"formal"); cand=one_metrics(rows,"candidate")
    exact_base=sr["pooled"]["score_baseline"]["exact_score_logloss"]; exact_cand=sr["pooled"]["score_candidate"]["exact_score_logloss"]
    cond_base=exact_base-base["logloss"]; cond_cand=exact_cand-cand["logloss"]
    strong_y=[int(r["strong_win"]) for r in rows]; weak_y=[int(r["weak_win"]) for r in rows]; draw_y=[int(r["y"]==1) for r in rows]
    strong_formal=binary_metrics(strong_y,[r["strong_prob_formal"] for r in rows]); strong_cand=binary_metrics(strong_y,[r["strong_prob_candidate"] for r in rows])
    weak_formal=binary_metrics(weak_y,[r["weak_prob_formal"] for r in rows]); weak_cand=binary_metrics(weak_y,[r["weak_prob_candidate"] for r in rows])
    draw_formal=binary_metrics(draw_y,[r["formal"][1] for r in rows]); draw_cand=binary_metrics(draw_y,[r["candidate"][1] for r in rows])
    edges=c["definitions"]["weak_probability_bins"]; thresholds=c["definitions"]["weak_false_positive_thresholds"]
    gapdefs=[("0-.10",0,.10),(".10-.20",.10,.20),(".20-.35",.20,.35),(">=.35",.35,9)]
    uncdefs=[("low",lambda r:r["entropy"]<.90),("mid",lambda r:.90<=r["entropy"]<1.03),("high",lambda r:r["entropy"]>=1.03)]
    report={
      "schema_version":"football3-v3-strength-upset-balance-score-diagnostic-v1","status":"DIAGNOSTIC_COMPLETE_POST_VIEW","classification":"POST_VIEW_DIAGNOSTIC_ONLY",
      "fresh_confirmation":False,"promotion_allowed":False,"n":len(rows),"source":c["source"],
      "overall":{"formal_1x2":base,"v3_1_1_1x2":cand,"one_x_two_logloss_gain":base["logloss"]-cand["logloss"],
                 "formal_exact_score_logloss":exact_base,"v3_1_1_exact_score_logloss":exact_cand,"exact_score_logloss_gain":exact_base-exact_cand,
                 "formal_conditional_score_logloss":cond_base,"v3_1_1_conditional_score_logloss":cond_cand,"conditional_score_logloss_gain":cond_base-cond_cand,
                 "conditional_score_row_max_abs_gain":max(abs(r["cond_gain"]) for r in rows),
                 "score_top1":{"formal":sum(r["score_top1_formal"] for r in rows)/len(rows),"v3_1_1":sum(r["score_top1_candidate"] for r in rows)/len(rows)},
                 "score_top3":{"formal":sum(r["score_top3_formal"] for r in rows)/len(rows),"v3_1_1":sum(r["score_top3_candidate"] for r in rows)/len(rows)}},
      "strong_win":{"formal":strong_formal,"v3_1_1":strong_cand,"logloss_gain":strong_formal["logloss"]-strong_cand["logloss"],"brier_gain":strong_formal["brier"]-strong_cand["brier"]},
      "weak_upset":{"formal":weak_formal,"v3_1_1":weak_cand,"max_probability_abs_delta":max_weak_delta,"calibration_bins_formal":calibr_bins(rows,edges),
                    "false_positive_formal":false_positive(rows,thresholds,"weak_prob_formal"),"false_positive_v3_1_1":false_positive(rows,thresholds,"weak_prob_candidate")},
      "weak_home":subgroup(rows,lambda r:r["weak_home"]),"weak_away":subgroup(rows,lambda r:not r["weak_home"]),
      "draw":{"formal":draw_formal,"v3_1_1":draw_cand,"logloss_gain":draw_formal["logloss"]-draw_cand["logloss"]},
      "balanced":{"all":subgroup(rows,lambda r:r["gap"]<.10),"low_goal":subgroup(rows,lambda r:r["gap"]<.10 and r["pred_total"]<2.5),"open":subgroup(rows,lambda r:r["gap"]<.10 and r["pred_total"]>=2.5)},
      "strength_gap":{name:subgroup(rows,lambda r,lo=lo,hi=hi:lo<=r["gap"]<hi) for name,lo,hi in gapdefs},
      "uncertainty":{name:subgroup(rows,fn) for name,fn in uncdefs},"league_season":{},"calendar_half":{},
      "score_calibration_from_frozen_stress":{"formal":sr["pooled"]["score_baseline"],"v3_1_1":sr["pooled"]["score_candidate"],"gates":sr["pooled"]["score_gates"]},
      "fallback_active":{"matrix_exact_fallback_n":matrix_equal_n,"candidate_probability_exact_equal_n":p_equal_n,"process_coverage":sr["process_coverage"],
                         "xg_coverage_groups":{k:v for k,v in sr["pooled"]["one_x_two"]["groups"].items() if k.startswith("xg_coverage|")}},
      "matrix_audit":{"conditional_shape_max_abs_diff":max_shape,"weak_probability_max_abs_delta":max_weak_delta,"one_x_two_source":"INTEGRATE_FINAL_MATRIX_ONLY"},"diagnosis":{}}
    for key in sorted({(r["league"],r["season"]) for r in rows}):
        report["league_season"][f"{key[0]}|{key[1]}"]=subgroup(rows,lambda r,k=key:r["league"]==k[0] and r["season"]==k[1])
    for key in sorted({(r["season"],r["calendar_half"]) for r in rows}):
        report["calendar_half"][f"{key[0]}|{key[1]}"]=subgroup(rows,lambda r,k=key:r["season"]==k[0] and r["calendar_half"]==k[1])
    report["diagnosis"]={
      "v3_1_1_internal_score_structure_changed":max_shape>1e-12 or abs(cond_base-cond_cand)>1e-12,
      "v3_1_1_upset_probability_changed":max_weak_delta>1e-12,
      "exact_score_gain_is_region_gain_only":abs((exact_base-exact_cand)-(base["logloss"]-cand["logloss"]))<=1e-12 and abs(cond_base-cond_cand)<=1e-12,
      "weak_probability_overprediction_formal":weak_formal["mean_probability"]-weak_formal["event_rate"],
      "strong_probability_underprediction_formal":strong_formal["event_rate"]-strong_formal["mean_probability"],
      "primary_v3_1_1_gain_axis":"strong-vs-draw outcome-region redistribution; weak side is invariant; conditional exact-score distribution is invariant"}
    write_json(a.out/"diagnostic_report.json",report)
    (a.out/"artifact_slug.txt").write_text(f"DIAGNOSTIC_COMPLETE__n_{len(rows)}__x1gain_{base['logloss']-cand['logloss']:.6f}__condscore_{cond_base-cond_cand:.6f}__weakdelta_{max_weak_delta:.1e}\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"n":len(rows),"diagnosis":report["diagnosis"]},sort_keys=True))

if __name__=="__main__": main()
