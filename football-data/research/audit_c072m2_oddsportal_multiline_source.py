#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LEAGUES=[
    "england-premier-league",
    "spain-laliga",
    "germany-bundesliga",
    "italy-serie-a",
    "france-ligue-1",
]
PIN="5f6fc5e9768fcb089aa13c7d447aea8644a00b10"
OUT=Path("/tmp/football3_m2_oddsportal.json")
LINE_RE=re.compile(r"^over_under_(\d+)_(\d+)_market$")


def parse_dt(x):
    if not x:return None
    try:
        s=str(x).replace(" UTC","+00:00").replace("Z","+00:00")
        dt=datetime.fromisoformat(s)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:return None


def main():
    cmd=[
        "oddsharvester","upcoming","-s","football",
        "-l",",".join(LEAGUES),
        "-m","over_under","--preview-only","--headless",
        "--kickoff-within-hours","168",
        "-f","json","-o",str(OUT),
    ]
    proc=subprocess.run(cmd,text=True,capture_output=True,timeout=1200)
    candidates=[OUT,Path(str(OUT)+".json")]
    data_file=next((p for p in candidates if p.exists()),None)
    if proc.returncode!=0 or data_file is None:
        out={
            "schema":"C072M2_ODDSPORTAL_MULTILINE_ZERO_LABEL_SOURCE_V1",
            "provider_tool_commit":PIN,
            "terminal":"SOURCE_ACCESS_BLOCKED",
            "returncode":proc.returncode,
            "stdout_tail":proc.stdout[-3000:],
            "stderr_tail":proc.stderr[-3000:],
            "target_result_values_materialized":0,
            "model_fit":0,
            "C073_C077_quarantined":True,
            "C070F_confirmation1597_opened":False,
            "formal_weight":0,
        }
        Path("football-data/research/c072m2_source_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(out,ensure_ascii=False,indent=2));return

    raw=json.loads(data_file.read_text(encoding="utf-8"))
    if isinstance(raw,dict):
        # tolerate wrappers used by future storage versions
        for key in ["data","matches","records","items"]:
            if isinstance(raw.get(key),list):raw=raw[key];break
    if not isinstance(raw,list):raise RuntimeError("unexpected OddsHarvester JSON root")

    rows=[]; nonnull_score=0; league_counts=Counter()
    for r in raw:
        if not isinstance(r,dict):continue
        score_fields=[r.get("home_score"),r.get("away_score"),r.get("result"),r.get("partial_results")]
        if any(v not in [None,"",[],{}] for v in score_fields):nonnull_score+=1
        lines=[]
        for k,v in r.items():
            m=LINE_RE.match(str(k))
            if not m:continue
            populated=bool(v)
            if populated:lines.append(float(f"{m.group(1)}.{m.group(2)}"))
        league=str(r.get("league_name") or r.get("league") or r.get("country") or "UNKNOWN")
        league_counts[league]+=1
        match_dt=parse_dt(r.get("match_date") or r.get("kickoff") or r.get("kickoff_utc"))
        scrape_dt=parse_dt(r.get("scraped_date") or r.get("scraped_at") or r.get("scraped_at_utc"))
        future_ok=(match_dt is not None and scrape_dt is not None and match_dt>scrape_dt)
        rows.append({"lines":sorted(set(lines)),"future_ok":future_ok})

    n=len(rows)
    rates=lambda fn:float(sum(fn(r) for r in rows)/n) if n else 0.0
    two=rates(lambda r:len(r["lines"])>=2);three=rates(lambda r:len(r["lines"])>=3)
    has25=rates(lambda r:2.5 in r["lines"]);adj=rates(lambda r:2.5 in r["lines"] and (1.5 in r["lines"] or 3.5 in r["lines"]))
    future=rates(lambda r:r["future_ok"])
    multi_leagues=sum(v>=2 for v in league_counts.values())
    line_counts=Counter()
    for r in rows:
        for x in r["lines"]:line_counts[str(x)]+=1
    gates={
        "scraper_success_no_credentials":proc.returncode==0,
        "matches_ge_15":n>=15,
        "at_least_3_leagues_ge_2_matches":multi_leagues>=3,
        "future_unsettled_no_scores":nonnull_score==0 and future>=.95,
        "two_lines_rate_ge_0_80":two>=.80,
        "three_lines_rate_ge_0_60":three>=.60,
        "ou25_rate_ge_0_90":has25>=.90,
        "adjacent_with_25_rate_ge_0_70":adj>=.70,
        "target_result_parsing_zero":nonnull_score==0,
        "model_fit_zero":True,
    }
    terminal="MULTILINE_SOURCE_PASS" if all(gates.values()) else "STOP_MULTILINE_SOURCE_COVERAGE"
    out={
        "schema":"C072M2_ODDSPORTAL_MULTILINE_ZERO_LABEL_SOURCE_V1",
        "provider_tool_commit":PIN,
        "terminal":terminal,
        "matches":n,
        "league_counts":dict(league_counts),
        "distinct_line_counts":dict(sorted(line_counts.items(),key=lambda kv:float(kv[0]))),
        "two_plus_lines_rate":two,
        "three_plus_lines_rate":three,
        "ou25_rate":has25,
        "adjacent_15_or_35_with_25_rate":adj,
        "future_timestamp_rate":future,
        "nonnull_score_rows":nonnull_score,
        "target_result_values_materialized":0 if nonnull_score==0 else nonnull_score,
        "model_fit":0,
        "gates":gates,
        "C073_C077_quarantined":True,
        "C070F_confirmation1597_opened":False,
        "formal_weight":0,
    }
    Path("football-data/research/c072m2_source_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
