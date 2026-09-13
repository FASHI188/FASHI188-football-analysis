#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

SOURCE_REPO = "openfootball/football.json"
SOURCE_COMMIT = "41a6eb96ba816757ff892387da5cb111390a7f9c"
FEATURE_SHA256 = "c9b14435062f59f1caaf4ffdbf83373d7aa50b858a48057f66b89cc0605ecea4"
FEATURE_ROWS = 30531
FROZEN_PREREG_HEAD = "836e985ced292fc6a13a12ac132075f8a83c5279"
ACCEPTED_CARRIER_HEAD = "2dd8be947b5922703bde7174ef6f6347295bfde8"
CARRIER_RECEIPT_SHA256 = "e474a65ef1a5f370e6e4990faa0f030bdf2f65b024b670a445512bfb0ce8a6fc"
CARRIER_HEAD_BINDING_SHA256 = "21e7c40566af4761f7b1b882c45ff0a18b55132b503bd6b12d7d8efb8604262"
PREREG_CONTRACT_SHA256 = "3566044d6144bab6beacf7b68e4b5f303cc7d66353ff6a3c2ad14fd96da6026e"
EXCLUDDED_PATH = "2023-24/en.1.json"
ALLOWED_SEASONS = [f"{y}-{str(y+1)[-2:]}" for y in range(2010, 2025)]
EXPECTED_LEAGUE_FILES = {"at.1.json":15,"de.1.json":15,"en.1.json":14,"es.1.json":13,"fr.1.json":11,"it.1.json":12,"nl.1.json":7,"pt.1.json":7}
EXPECTED_SOURCE_FILES = 94
TEST_SEASONS = ["2020-21","2021-22","2022-23","2023-24","2024-25"]
TRAIN_MAX = ["2019-20","2020-21","2021-22","2022-23","2023-24"]
CLASS_ORDER = ["H","D","A"]
CLASS_TO_INT = {"H":0,"D":1,"A":2}
NUMERIC_FEATURES = [
    "home_league_rest_days","away_league_rest_days",
    "home_league_congestion_7d","away_league_congestion_7d",
    "home_league_congestion_14d","away_league_congestion_14d",
    "home_league_congestion_21d","away_league_congestion_21d",
    "home_consecutive_away_before","away_consecutive_away_before",
]
REST_FEATURES = {"home_league_rest_days","away_league_rest_days"}
FEATURE_KEYS = {
    "source_path","row_index","date","round","round_stage","round_number","team1","team2",
    *NUMERIC_FEATURES,
}
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 20260914
EPS = 1e-15

class Stop(RuntimeError):
    pass

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def season_of(source_path: str) -> str:
    return source_path.split("/",1)[0]

def competition_of(source_path: str) -> str:
    return source_path.rsplit("/",1)[1].removesuffix(".json")

def validate_prereg(contract: dict[str,Any]) -> None:
    if contract.get("schema_version") != "football3-v3-schedule-importance-scientific-evaluation-prereg-v1":
        raise Stop("STOP_PREREG_SCHEMA")
    if contract.get("status") != "DESIGN_LOCKED":
        raise Stop("STOP_PREREG_NOT_LOCKED")
    if [x.get("test_season") for x in contract.get("chronological_outer_folds",[])] != TEST_SEASONS:
        raise Stop("STOP_PREREG_FOLDS")
    if [x.get("train_seasons_max") for x in contract.get("chronological_outer_folds",[])] != TRAIN_MAX:
        raise Stop("STOP_PREREG_TRAIN_MAX")
    if contract.get("baseline_model",{}).get("id") != "M0_TEAM_COMPETITION_RIDGE_MULTINOMIAL_V1":
        raise Stop("STOP_PREREG_BASELINE")
    if contract.get("candidate_model",{}).get("id") != "M1_BASELINE_PLUS_FROZEN_SCHEDULE_STAGE_V1":
        raise Stop("STOP_PREREG_CANDIDATE")
    if contract.get("candidate_model",{}).get("schedule_numeric_features") != NUMERIC_FEATURES:
        raise Stop("STOP_PREREG_NUMERIC_FEATURES")
    if contract.get("metrics",{}).get("primary") != "pooled_multiclass_logloss_delta_candidate_minus_baseline":
        raise Stop("STOP_PREREG_PRIMARY")
    boot=contract.get("metrics",{}).get("bootstrap",{})
    want={"unit":"source_path league-season block","paired":True,"replicates":5000,"seed":20260914,"ci_level":0.9,"statistic":"pooled logloss delta"}
    if boot != want:
        raise Stop("STOP_PREREG_BOOTSTRAP")
    gates=contract.get("acceptance_gates",{})
    expected=["G1_primary_pooled_logloss","G2_primary_bootstrap","G3_fold_direction","G4_fold_worst_case","G5_brier","G6_rps","G7_top1_non_degradation","G8_competition_stability"]
    if any(k not in gates for k in expected) or gates.get("all_required") is  not True:
        raise Stop("STOP_PREREG_GATESET")
    mul=contract.get("multiplicity_policy",{})
    if mul.get("primary_hypotheses") != 1 or mul.get("model_comparisons") != 1:
        raise Stop("STOP_PREREG_MULTIPLICITY")
    if not all(mul.get(k) is True for k in [
        "no_post_view_feature_selection","no_post_view_threshold_search","no_post_view_hyperparameter_search",
        "no_subgroup_rescue","secondary_metrics_cannot_rescue_primary_failure","diagnostic_subgroups_cannot_change_pass_fail"
    ]):
        raise Stop("STOP_PREREG_POST_VIEW_GUARD")

def validate_carrier(carrier_dir: Path) -> None:
    rp=carrier_dir/"independent-prereg-acceptance-receipt.json"
    hp=carrier_dir/"head-binding.json"
    if sha256_file(rp) != CARRIER_RECEIPT_SHA256:
        raise Stop("STOP_CARRIER_RECEIPT_SHA")
    if sha256_file(hp) != CARRIER_HE