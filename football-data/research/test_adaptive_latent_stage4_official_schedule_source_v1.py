#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path

from adaptive_latent_stage4_official_schedule_source_v1 import (
    OfficialScheduleError,
    canonical_source_projection,
    materialize_target_inventory,
)

ROOT = Path(__file__).resolve().parent
SOURCES = json.loads((ROOT / "official_schedule_projection_candidate_v1.json").read_text(encoding="utf-8"))


def expect_fail(mutator) -> None:
    obj = copy.deepcopy(SOURCES)
    mutator(obj)
    try:
        materialize_target_inventory(obj)
    except OfficialScheduleError:
        return
    raise AssertionError("expected fail-closed rejection")


def main() -> None:
    inventory, identity_csv, lock = materialize_target_inventory(copy.deepcopy(SOURCES))
    assert inventory["status"] == "PASS_ZERO_LABEL_OFFICIAL_SCHEDULE_IDENTITY_LOCK_PROVENANCE_CANDIDATE"
    assert inventory["target_row_count"] == 21
    assert set(inventory["required_competitions"]) == {
        "ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"
    }
    assert inventory["provider_event_id_present"] is False
    assert inventory["provider_mapping_status"] == "UNRESOLVED_SEPARATE_GATE"
    assert inventory["source_revision_status"] == "EXTRACTED_EXACT_FIELD_PROJECTION_ONLY"
    assert inventory["availability_proof_status"] == "PENDING_FORMAL_PIT_ADJUDICATION"
    assert inventory["formal_pit_eligible"] is False
    assert inventory["label_fields_persisted"] == 0
    assert inventory["real_target_values_read"] == 0
    assert inventory["formal_weight"] == 0.0
    assert identity_csv.startswith("identity_sha256\n")
    assert lock["identity_lock_sha256"] == inventory["identity_lock_sha256"]
    assert all("provider_event_id" not in row for row in inventory["targets"])
    assert all(row["prediction_cutoff"].endswith("Z") for row in inventory["targets"])

    # Contract / fail-closed.
    expect_fail(lambda x: x[0].__setitem__("harmless_extra", "x"))
    expect_fail(lambda x: x[0].pop("source_timezone"))
    expect_fail(lambda x: x[0]["fixtures"][0].__setitem__("score", None))
    expect_fail(lambda x: x[0]["fixtures"][0].pop("round_ref"))
    expect_fail(lambda x: x[0].__setitem__("source_url", "https://www.premierleague.com/en/results"))
    expect_fail(lambda x: x[0].__setitem__("source_url", " " + x[0]["source_url"]))
    expect_fail(lambda x: x[0].__setitem__("source_url", x[0]["source_url"].replace("www.premierleague.com", "www.premierleague.com:444")))
    expect_fail(lambda x: x[0].__setitem__("schema", 1))
    expect_fail(lambda x: x[0].__setitem__("authority", 1))
    expect_fail(lambda x: x[0].__setitem__("source_timezone", 1))
    expect_fail(lambda x: x[0]["fixtures"][0].__setitem__("home_team_name", "Brighton\u200b & Hove Albion"))
    expect_fail(lambda x: x[0].__setitem__("source_timezone", "UTC"))
    expect_fail(lambda x: x[0]["fixtures"][0].__setitem__("scheduled_kickoff", "2026-08-23T14:00:00+00:00"))
    expect_fail(lambda x: x[0].__setitem__("real_labels_read", True))
    expect_fail(lambda x: x[0].__setitem__("raw_source_payload_persisted", True))
    expect_fail(lambda x: x.append(copy.deepcopy(x[0])))
    expect_fail(lambda x: x.pop())
    expect_fail(lambda x: x[0]["fixtures"].append(copy.deepcopy(x[0]["fixtures"][0])))
    expect_fail(lambda x: x[0]["fixtures"][0].__setitem__("home_team_name", x[0]["fixtures"][0]["away_team_name"]))
    expect_fail(lambda x: x[0]["fixtures"][0].__setitem__("scheduled_kickoff", "2026-09-30T14:00:00+01:00"))

    # Single-source canonicalization is exact and does not infer labels/provider ids.
    c = canonical_source_projection(copy.deepcopy(SOURCES[0]))
    assert c["real_labels_read"] == 0
    assert set(c["fixtures"][0]) == {"round_ref", "scheduled_kickoff", "home_team_name", "away_team_name"}

    print("PASS official schedule Stage4 tests")


if __name__ == "__main__":
    main()
