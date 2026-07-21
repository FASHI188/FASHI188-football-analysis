#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from clubelo_history_ingest_v515 import _clubelo_history_slug, _map_team


def test_history_slugs_match_clubelo_contract():
    assert _clubelo_history_slug("Man City") == "ManCity"
    assert _clubelo_history_slug("Real Madrid") == "RealMadrid"
    assert _clubelo_history_slug("Paris SG") == "ParisSG"
    assert _clubelo_history_slug("Nott'm Forest") == "NottmForest"
    assert _clubelo_history_slug("St. Pauli") == "StPauli"


def test_audited_aliases_cannot_cross_map_rivals():
    esp = {"Atletico", "Real Madrid", "Bilbao", "Rayo Vallecano", "Valencia"}
    ath_madrid = _map_team("Ath Madrid", esp, 0.88, 0.15)
    assert ath_madrid["status"] == "PASS"
    assert ath_madrid["clubelo_name"] == "Atletico"
    assert ath_madrid["method"] == "EXPLICIT_AUDITED_CLUBELO_ALIAS"

    ath_bilbao = _map_team("Ath Bilbao", esp, 0.88, 0.15)
    assert ath_bilbao["clubelo_name"] == "Bilbao"

    vallecano = _map_team("Vallecano", esp, 0.88, 0.15)
    assert vallecano["clubelo_name"] == "Rayo Vallecano"


def test_audited_aliases_cover_known_provider_abbreviations():
    eng = {"Forest", "Arsenal", "Liverpool"}
    ger = {"Bayern", "Werder", "Frankfurt", "Koeln", "Gladbach"}
    assert _map_team("Nott'm Forest", eng, 0.88, 0.15)["clubelo_name"] == "Forest"
    assert _map_team("Bayern Munich", ger, 0.88, 0.15)["clubelo_name"] == "Bayern"
    assert _map_team("Werder Bremen", ger, 0.88, 0.15)["clubelo_name"] == "Werder"
    assert _map_team("Ein Frankfurt", ger, 0.88, 0.15)["clubelo_name"] == "Frankfurt"
    assert _map_team("FC Koln", ger, 0.88, 0.15)["clubelo_name"] == "Koeln"
    assert _map_team("M'gladbach", ger, 0.88, 0.15)["clubelo_name"] == "Gladbach"


def test_low_similarity_unknown_fails_closed():
    candidates = {"Real Madrid", "Atletico", "Barcelona"}
    result = _map_team("Completely Different", candidates, 0.88, 0.15)
    assert result["status"] == "FAIL"
