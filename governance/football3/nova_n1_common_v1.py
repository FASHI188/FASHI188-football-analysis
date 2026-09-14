#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXPECTED_ARTIFACT_ID = 9798682425
EXPECTED_ARTIFACT_NAME = "historical-pit-source-base-c5c2bec26c165680f7625a49b145719e0d0e7130-33503552079"
EXPECTED_PRODUCER_RUN_ID = 33503552079
EXPECTED_ARTIFACT_SHA256 = "01b655b2748b1e601d82c61e12d763ed50c2db40c3b3b8802aec807c6ac6acba"
EXPECTED_DB_SHA256 = "f102eae39b4036a4c24e5b75b9cee551064cf1e7d4fd028966cd62a5784d8681"
FORMAL_HEAD = "e12f5d1193be5d81f60301cf34ab2140e11712a9"
CURRENT_SHA256 = "71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731"
PRELABEL_FREEZE_HEAD = "812b30217092a7d1c1c6743eb237f474006ccee0"
ADOPTED_MECHANICS_HEAD = "5128b76a358b5c0ec5e02869be2f1e818fea3526"
LEAGUES = {
    "EPL": "ENG_PremierLeague",
    "La_liga": "ESP_LaLiga",
    "La liga": "ESP_LaLiga",
    "Bundesliga": "GER_Bundesliga",
    "Serie_A": "ITA_SerieA",
    "Serie A": "ITA_SerieA",
    "Ligue_1": "FRA_Ligue1",
    "Ligue 1": "FRA_Ligue1",
}
WINDOWS = (5, 10, 20)
ROUTES = ("D", "P", "DP", "DPI")
FIT_SEASONS = {2014, 2015, 2016, 2017}
DEV_SEASONS = {2018, 2019}
RELEASE_LAG = timedelta(minutes=180)
EPS = 1e-12
L2 = 1.0
MAX_ITER = 500
GRAD_TOL = 1e-8
ARMIJO_C = 1e-4
BACKTRACK = 0.5
MAX_BACKTRACKS = 40

class N1Error(RuntimeError):
    pass

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def canonical_sha(obj: Any) -> str:
    b = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(b).hexdigest()

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.casefold()).strip("-")

def season_label(y: int) -> str:
    return f"{y}/{str(y+1)[-2:]}"

def parse_kickoff(s: str) -> datetime:
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

@dataclass(frozen=True)
class IdentityRow:
    fid: int
    kickoff: datetime
    league: str
    competition_id: str
    season_key: int
    home: str
    away: str

@dataclass
class ReplayRow:
    fid: int
    kickoff: datetime
    competition_id: str
    league: str
    season_key: int
    home: str
    away: str
    baseline: dict[str, Any]
    raw_features: dict[int, dict[str, float] | None]
    y: int | None = None

def load_contract(path: Path) -> dict[str, Any]:
    c = json.loads(path.read_text(encoding="utf-8"))
    if c.get("status") != "DESIGN_LOCKED_PRELABEL":
        raise N1Error("design lock missing")
    if c.get("max_candidate_configurations") != 12:
        raise N1Error("candidate budget drift")
    if c["feature_state"]["time_scales_matches"] != [5, 10, 20]:
        raise N1Error("window drift")
    if c["optimizer_contract"]["feature_coefficients_l2_strength"] != 1.0:
        raise N1Error("L2 drift")
    if c["optimizer_contract"]["max_iterations"] != 500:
        raise N1Error("optimizer iteration drift")
    if c["data_lock"]["licensed_feature_and_development_source"]["artifact_id"] != EXPECTED_ARTIFACT_ID:
        raise N1Error("source artifact id drift")
    return c

def prepare_db(source: Path, out_dir: Path) -> tuple[Path, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "understat_frozen.db"
    audit = {
        "artifact_id": EXPECTED_ARTIFACT_ID,
        "artifact_name": EXPECTED_ARTIFACT_NAME,
        "producer_run_id": EXPECTED_PRODUCER_RUN_ID,
        "declared_artifact_sha256": EXPECTED_ARTIFACT_SHA256,
    }
    if source.is_file():
        if sha256_file(source) != EXPECTED_ARTIFACT_SHA256:
            raise N1Error("development artifact digest mismatch")
        with zipfile.ZipFile(source) as z:
            names=set(z.namelist())
            if "understat_frozen.db" not in names or "understat_freeze_receipt.json" not in names:
                raise N1Error("frozen artifact members missing")
            with z.open("understat_frozen.db") as src, dst.open("wb") as f:
                shutil.copyfileobj(src, f)
            receipt=json.loads(z.read("understat_freeze_receipt.json"))
        audit["outer_zip_digest_verified_in_this_run"] = True
    elif source.is_dir():
        db_src=source/"understat_frozen.db"
        rec_src=source/"understat_freeze_receipt.json"
        if not db_src.is_file() or not rec_src.is_file():
            raise N1Error("downloaded artifact members missing")
        shutil.copyfile(db_src,dst)
        receipt=json.loads(rec_src.read_text(encoding="utf-8"))
        audit["outer_zip_digest_verified_in_this_run"] = False
        audit["outer_zip_binding"] = "GitHub artifact id/name/run plus preregistered digest; extracted member SHA reverified"
    else:
        raise N1Error("development source missing")
    if sha256_file(dst) != EXPECTED_DB_SHA256:
        raise N1Error("frozen DB digest mismatch")
    if receipt.get("provider") != "Cody Tipton player stats per game - Understat":
        raise N1Error("source provider mismatch")
    if receipt.get("labels_read") != 0 or receipt.get("score_or_result_columns_read") is not False:
        raise N1Error("source acquisition was not label blind")
    if receipt.get("database_sha256") != EXPECTED_DB_SHA256:
        raise N1Error("source receipt database digest mismatch")
    audit["database_sha256_verified"] = EXPECTED_DB_SHA256
    audit["source_receipt_schema"] = receipt.get("schema_version")
    audit["source_retrieved_at"] = receipt.get("retrieved_at")
    return dst,audit
