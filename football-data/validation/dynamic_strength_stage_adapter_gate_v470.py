#!/usr/bin/env python3
"""Classify every audited special-domain round label before dynamic-strength OOF.

The adapter is fail-closed.  A competition is research-ready only when every raw
round label can be mapped to a declared stage and the requested research subset is
explicit.  Ambiguous MLS/Argentina labels and insufficient J1 history remain blocked.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"manifests"/"dynamic_strength_stage_route_audit_v470_status.json"
OUT=ROOT/"manifests"/"dynamic_strength_stage_adapter_v470_status.json"


def matchday(label:str)->int|None:
    m=re.fullmatch(r"\s*(\d+)\.\s*Matchday\s*",label,re.I)
    return int(m.group(1)) if m else None


def classify(cid:str,label:str)->str|None:
    md=matchday(label)
    if cid in {"SUI_SuperLeague","SCO_Premiership","KOR_KLeague1"}:
        if md is None:return None
        if 1<=md<=33:return "regular_1_33"
        if 34<=md<=38:return "post_split_34_38"
        return None
    if cid=="UEFA_ChampionsLeague":
        if re.fullmatch(r"Group\s+[A-H]",label,re.I) or label.casefold()=="group stage":return "group_or_league_stage"
        knockout_tokens=("last 16","quarter-finals","semi-finals","final","intermediate stage")
        if any(token in label.casefold() for token in knockout_tokens):return "knockout"
        return None
    if cid in {"ARG_Primera","USA_MLS"}:
        return "ambiguous_round_only"
    if cid=="JPN_J1":
        return "regular_round_but_insufficient_history" if md is not None else None
    return None


def main()->int:
    source=json.loads(SOURCE.read_text(encoding="utf-8"));reports={}
    for cid,raw in source.get("reports",{}).items():
        mapping={};unknown=[];counts={}
        for season,season_data in raw.get("seasons",{}).items():
            for label,count in season_data.get("round_labels",{}).items():
                stage=classify(cid,label)
                mapping[label]=stage
                if stage is None:unknown.append(label)
                else:counts[stage]=counts.get(stage,0)+int(count)
        if cid=="SCO_Premiership":
            status="ADAPTER_READY_REGULAR_1_33" if not unknown else "ADAPTER_BLOCKED_UNKNOWN_LABEL"
            research_subset="regular_1_33"
        elif cid=="UEFA_ChampionsLeague":
            status="ADAPTER_READY_GROUP_OR_LEAGUE_STAGE_RESEARCH_ONLY" if not unknown else "ADAPTER_BLOCKED_UNKNOWN_LABEL"
            research_subset="group_or_league_stage"
        elif cid in {"SUI_SuperLeague","KOR_KLeague1"}:
            status="ADAPTER_VALID_BUT_INSUFFICIENT_HISTORY" if not unknown else "ADAPTER_BLOCKED_UNKNOWN_LABEL"
            research_subset="regular_1_33"
        elif cid in {"ARG_Primera","USA_MLS"}:
            status="ADAPTER_BLOCKED_AMBIGUOUS_STAGE_LABELS"
            research_subset=None
        elif cid=="JPN_J1":
            status="ADAPTER_BLOCKED_INSUFFICIENT_HISTORY_AND_TRANSITION_BARRIER"
            research_subset=None
        else:
            status="ADAPTER_BLOCKED"
            research_subset=None
        reports[cid]={"competition_id":cid,"status":status,"research_subset":research_subset,"unknown_labels":sorted(set(unknown)),"classification_counts":counts,"label_mapping":mapping,"formal_weight":0,"probability_change":False,"formal_promotion_additional_gate":"current market anchor required" if cid=="UEFA_ChampionsLeague" else None}
    ready=[cid for cid,r in reports.items() if r["status"].startswith("ADAPTER_READY")]
    out={"schema_version":"V4.7.0-dynamic-strength-stage-adapter-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","research_stage_ready":ready,"formal_weight_change":False,"probability_change":False,"reports":reports,"policy":"Only explicitly mapped stage subsets may enter special-domain research. MLS and Argentina remain fail-closed on ambiguous round-only labels; J1 remains blocked by history/transition rules. UCL research never removes its current-market-anchor formal gate."}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"research_stage_ready":ready,"statuses":{k:v["status"] for k,v in reports.items()}},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
