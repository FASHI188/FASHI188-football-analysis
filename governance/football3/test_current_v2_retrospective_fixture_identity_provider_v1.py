from __future__ import annotations

import pytest

import current_v2_retrospective_fixture_identity_provider_v1 as provider

DOMAINS = [
    ("ENG_PremierLeague", "E0", "HTTP Error 503: Service Temporarily Unavailable"),
    ("ESP_LaLiga", "SP1", "HTTP Error 429: Too Many Requests"),
    ("GER_Bundesliga", "D1", "timed out"),
    ("ITA_SerieA", "I1", "HTTP Error 503: Service Temporarily Unavailable"),
    ("FRA_Ligue1", "F1", "HTTP Error 429: Too Many Requests"),
    ("JPN_J1", "J1", "timed out"),
    ("KOR_KLeague1", "K1", "HTTP Error 503: Service Temporarily Unavailable"),
]


def row(
    comp,
    *,
    fid="f1",
    home="HOME",
    away="AWAY",
    source="FALLBACK",
    sha="a"*64,
    kickoff="2026-09-05T10:00:00+00:00",
    observed_at="2026-09-01T00:00:00+00:00",
):
    return {
        "competition": comp,
        "fixture_identity": fid,
        "kickoff": kickoff,
        "home_identity": home,
        "away_identity": away,
        "source_identity": source,
        "observed_at": observed_at,
        "content_sha": sha,
    }


@pytest.mark.parametrize("comp,code,error", DOMAINS)
def test_domain_primary_failure_does_not_block_approved_score_blind_fallback(comp, code, error):
    audit = {}
    def fail():
        raise RuntimeError(f"{code}: {error}")
    rows = provider.resolve(comp, [
        provider.Provider("GOVERNED_MANIFEST", 1, lambda: []),
        provider.Provider("APPROVED_SCORE_BLIND_FALLBACK", 2, lambda: [row(comp)]),
        provider.Provider(f"PRIMARY_{code}", 3, fail),
    ], audit)
    assert rows == [provider.validate_record(row(comp), comp)]
    assert audit["fixture_identity_selected_provider"][comp] == "APPROVED_SCORE_BLIND_FALLBACK"
    assert audit.get("failed_domain") is None
    failed = [x for x in audit["fixture_identity_provider_attempts"] if x["provider"] == f"PRIMARY_{code}"][0]
    assert failed["outcome"] == "FAIL"
    if "503" in error:
        assert failed["http_status"] == 503
    if "429" in error:
        assert failed["http_status"] == 429


def test_ucl_existing_authority_path_has_no_csv_dependency():
    comp = "UEFA_ChampionsLeague"
    audit = {}
    rows = provider.resolve(comp, [
        provider.Provider("GOVERNED_UCL_36_TEAM_AUTHORITY_BRIDGE", 1, lambda: [row(comp, source="UCL_AUTHORITY")]),
    ], audit)
    assert rows
    assert audit["fixture_identity_provider_order"][comp] == ["GOVERNED_UCL_36_TEAM_AUTHORITY_BRIDGE"]
    assert all("FOOTBALL_DATA" not in x for x in audit["fixture_identity_provider_order"][comp])


def test_two_provider_same_canonical_fixture_with_distinct_source_fixture_ids_passes():
    comp = "ENG_PremierLeague"
    provider.resolve(comp, [
        provider.Provider("A", 1, lambda: [row(comp, fid="source-a-111", source="A", sha="a"*64)]),
        provider.Provider("B", 2, lambda: [row(comp, fid="source-b-999", source="B", sha="b"*64)]),
    ], {})


def test_different_team_combinations_same_slot_legally_coexist_across_sources():
    comp = "ENG_PremierLeague"
    audit = {}
    rows = provider.resolve(comp, [
        provider.Provider("A", 1, lambda: [
            row(comp, fid="a1", home="ALPHA", away="BRAVO", source="A"),
            row(comp, fid="a2", home="CHARLIE", away="DELTA", source="A", sha="b"*64),
        ]),
        provider.Provider("B", 2, lambda: [
            row(comp, fid="b1", home="ECHO", away="FOXTROT", source="B", sha="c"*64),
            row(comp, fid="b2", home="ALPHA", away="BRAVO", source="B", sha="d"*64),
        ]),
    ], audit)
    assert {(x["home_identity"], x["away_identity"]) for x in rows} == {
        ("ALPHA", "BRAVO"), ("CHARLIE", "DELTA")
    }
    assert audit.get("failed_domain") is None


def test_same_competition_kickoff_slot_is_explicit_multi_fixture_container_positive_case():
    comp = "GER_Bundesliga"
    rows = provider.stable_records([
        row(comp, fid="g1", home="TEAM_A", away="TEAM_B", source="G"),
        row(comp, fid="g2", home="TEAM_C", away="TEAM_D", source="G", sha="b"*64),
        row(comp, fid="g3", home="TEAM_E", away="TEAM_F", source="G", sha="c"*64),
        row(comp, fid="g4", home="TEAM_G", away="TEAM_H", source="G", sha="d"*64),
    ], comp)
    slots = provider._slot_map(rows)
    assert len(slots) == 1
    assert len(next(iter(slots.values()))) == 4


@pytest.mark.parametrize("comp", ["FRA_Ligue1", "GER_Bundesliga", "ENG_PremierLeague"])
def test_redacted_incident_regression_simultaneous_round_fixtures_do_not_trigger_slot_conflict(comp):
    left = [
        row(comp, fid="left-01", home="CANONICAL_HOME_01", away="CANONICAL_AWAY_01", source="LEFT", sha="1"*64),
        row(comp, fid="left-02", home="CANONICAL_HOME_02", away="CANONICAL_AWAY_02", source="LEFT", sha="2"*64),
        row(comp, fid="left-03", home="CANONICAL_HOME_03", away="CANONICAL_AWAY_03", source="LEFT", sha="3"*64),
    ]
    right = [
        row(comp, fid="right-903", home="CANONICAL_HOME_03", away="CANONICAL_AWAY_03", source="RIGHT", sha="6"*64),
        row(comp, fid="right-901", home="CANONICAL_HOME_01", away="CANONICAL_AWAY_01", source="RIGHT", sha="4"*64),
        row(comp, fid="right-902", home="CANONICAL_HOME_02", away="CANONICAL_AWAY_02", source="RIGHT", sha="5"*64),
    ]
    audit = {}
    selected = provider.resolve(comp, [
        provider.Provider("LEFT_PROVIDER", 1, lambda: left),
        provider.Provider("RIGHT_PROVIDER", 2, lambda: right),
    ], audit)
    assert len(selected) == 3
    assert audit["fixture_identity_selected_provider"][comp] == "LEFT_PROVIDER"
    assert not audit.get("fixture_identity_conflicts")


def test_same_source_specific_fixture_id_across_different_sources_never_binds_distinct_fixtures():
    comp = "FRA_Ligue1"
    provider.resolve(comp, [
        provider.Provider("A", 1, lambda: [row(comp, fid="42", home="A_HOME", away="A_AWAY", source="SOURCE_A")]),
        provider.Provider("B", 2, lambda: [row(comp, fid="42", home="B_HOME", away="B_AWAY", source="SOURCE_B", sha="b"*64)]),
    ], {})


def test_same_source_identity_conflict_fails_only_after_strict_canonical_fixture_bind_and_preserves_evidence():
    comp = "ENG_PremierLeague"
    audit = {}
    with pytest.raises(RuntimeError, match="SOURCE_ID_CONFLICT_AFTER_CANONICAL_BIND"):
        provider.resolve(comp, [
            provider.Provider("A", 1, lambda: [row(comp, fid="source-id-old", source="SAME_UPSTREAM")]),
            provider.Provider("B", 2, lambda: [row(comp, fid="source-id-new", source="SAME_UPSTREAM", sha="b"*64)]),
        ], audit, error_factory=RuntimeError)
    assert audit["failed_domain"] == comp
    conflict = audit["fixture_identity_conflicts"][0]
    assert conflict["error"] == "FIXTURE_IDENTITY_SOURCE_ID_CONFLICT_AFTER_CANONICAL_BIND"
    evidence = conflict["evidence"]
    assert evidence["canonical_fixture_key"] == [comp, "HOME", "AWAY", "2026-09-05T10:00:00+00:00"]
    assert evidence["source_identity"] == "SAME_UPSTREAM"
    assert evidence["fixture_identities"] == ["source-id-new", "source-id-old"]
    assert all(x.get("observed_at") and x.get("content_sha") for x in evidence["evidence"])


def test_same_source_specific_id_reused_for_distinct_canonical_fixtures_is_invalid_provider_evidence():
    comp = "ENG_PremierLeague"
    with pytest.raises(provider.FixtureIdentityProviderError, match="SOURCE_ID_REUSED_FOR_DISTINCT_CANONICAL_FIXTURES"):
        provider.stable_records([
            row(comp, fid="same-id", home="A", away="B", source="ONE_SOURCE"),
            row(comp, fid="same-id", home="C", away="D", source="ONE_SOURCE", sha="b"*64),
        ], comp)


def test_legal_single_kickoff_revision_selects_latest_and_records_audit_evidence():
    comp = "ENG_PremierLeague"
    old = row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64)
    new = row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-02T00:00:00Z", sha="2"*64)
    audit = {}
    rows = provider.resolve(comp, [provider.Provider("A", 1, lambda: [old, new])], audit)
    assert len(rows) == 1
    assert rows[0]["kickoff"] == "2026-09-05T12:00:00+00:00"
    assert rows[0]["content_sha"] == "2"*64
    revisions = audit["fixture_identity_kickoff_revisions"]
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision["source_identity"] == "SOURCE"
    assert revision["fixture_identity"] == "same-id"
    assert revision["old_kickoff"] == "2026-09-05T10:00:00+00:00"
    assert revision["new_kickoff"] == "2026-09-05T12:00:00+00:00"
    assert revision["old_observed_at"] == "2026-09-01T00:00:00+00:00"
    assert revision["new_observed_at"] == "2026-09-02T00:00:00+00:00"
    assert revision["old_content_sha"] == "1"*64
    assert revision["new_content_sha"] == "2"*64
    assert revision["resolution"] == "LATEST_PRE_FREEZE_OBSERVATION"


def test_two_consecutive_legal_kickoff_revisions_select_latest():
    comp = "ESP_LaLiga"
    versions = [
        row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
        row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-02T00:00:00Z", sha="2"*64),
        row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-06T14:00:00Z", observed_at="2026-09-03T00:00:00Z", sha="3"*64),
    ]
    revisions = []
    rows = provider.stable_records(versions, comp, revision_evidence=revisions)
    assert rows[0]["kickoff"] == "2026-09-06T14:00:00+00:00"
    assert rows[0]["content_sha"] == "3"*64
    assert [(x["old_kickoff"], x["new_kickoff"]) for x in revisions] == [
        ("2026-09-05T10:00:00+00:00", "2026-09-05T12:00:00+00:00"),
        ("2026-09-05T12:00:00+00:00", "2026-09-06T14:00:00+00:00"),
    ]


def test_reversed_input_selects_same_latest_version_and_inventory_sha():
    comp = "ITA_SerieA"
    versions = [
        row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
        row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-02T00:00:00Z", sha="2"*64),
        row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-06T14:00:00Z", observed_at="2026-09-03T00:00:00Z", sha="3"*64),
    ]
    forward_revisions, reverse_revisions = [], []
    forward = provider.stable_records(versions, comp, revision_evidence=forward_revisions)
    reverse = provider.stable_records(list(reversed(versions)), comp, revision_evidence=reverse_revisions)
    assert forward == reverse
    assert forward_revisions == reverse_revisions
    assert provider.inventory_sha(versions, comp) == provider.inventory_sha(list(reversed(versions)), comp)


def test_same_observed_at_with_conflicting_kickoffs_fails_closed():
    comp = "GER_Bundesliga"
    with pytest.raises(provider.FixtureIdentityProviderError, match="SOURCE_REVISION_CONFLICT_SAME_OBSERVED_AT"):
        provider.stable_records([
            row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
            row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="2"*64),
        ], comp)


def test_same_observed_at_same_kickoff_but_different_content_is_ambiguous_and_fails_closed():
    comp = "GER_Bundesliga"
    with pytest.raises(provider.FixtureIdentityProviderError, match="SOURCE_REVISION_ORDER_AMBIGUOUS"):
        provider.stable_records([
            row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
            row(comp, fid="same-id", source="SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="2"*64),
        ], comp)


def test_kickoff_revision_that_changes_teams_fails_closed():
    comp = "FRA_Ligue1"
    with pytest.raises(provider.FixtureIdentityProviderError, match="SOURCE_ID_REUSED_FOR_DISTINCT_CANONICAL_FIXTURES"):
        provider.stable_records([
            row(comp, fid="same-id", source="SOURCE", home="HOME_A", away="AWAY_A", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
            row(comp, fid="same-id", source="SOURCE", home="HOME_B", away="AWAY_A", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-02T00:00:00Z", sha="2"*64),
        ], comp)


def test_cross_source_stale_kickoff_can_bind_only_through_governed_revision_lineage():
    comp = "ENG_PremierLeague"
    old = row(comp, fid="left-id", source="LEFT_SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64)
    new = row(comp, fid="left-id", source="LEFT_SOURCE", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-02T00:00:00Z", sha="2"*64)
    stale = row(comp, fid="right-id", source="RIGHT_SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T12:00:00Z", sha="3"*64)
    audit = {}
    provider.resolve(comp, [
        provider.Provider("LEFT", 1, lambda: [old, new]),
        provider.Provider("RIGHT", 2, lambda: [stale]),
    ], audit)
    binding = audit["fixture_identity_cross_source_revision_bindings"][0]
    assert binding["basis"] == "GOVERNED_SOURCE_REVISION_LINEAGE"
    assert binding["resolved_kickoff"] == "2026-09-05T12:00:00+00:00"


def test_cross_source_different_fixture_is_not_merged_when_governed_identity_basis_is_insufficient():
    comp = "ENG_PremierLeague"
    audit = {}
    selected = provider.resolve(comp, [
        provider.Provider("LEFT", 1, lambda: [
            row(comp, fid="left-id", source="LEFT_SOURCE", home="HOME", away="AWAY", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
        ]),
        provider.Provider("RIGHT", 2, lambda: [
            row(comp, fid="right-id", source="RIGHT_SOURCE", home="HOME", away="AWAY", kickoff="2026-10-05T10:00:00Z", observed_at="2026-09-20T00:00:00Z", sha="2"*64),
        ]),
    ], audit)
    assert selected[0]["kickoff"] == "2026-09-05T10:00:00+00:00"
    assert not audit.get("fixture_identity_cross_source_revision_bindings")
    separation = audit["fixture_identity_cross_source_separations"][0]
    assert separation["resolution"] == "KEEP_SEPARATE"
    assert separation["reason"] == "INSUFFICIENT_GOVERNED_IDENTITY_BASIS"


def test_divergent_cross_source_revisions_of_proven_same_fixture_fail_closed():
    comp = "ENG_PremierLeague"
    audit = {}
    left = [
        row(comp, fid="left-id", source="LEFT_SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T00:00:00Z", sha="1"*64),
        row(comp, fid="left-id", source="LEFT_SOURCE", kickoff="2026-09-05T12:00:00Z", observed_at="2026-09-02T00:00:00Z", sha="2"*64),
    ]
    right = [
        row(comp, fid="right-id", source="RIGHT_SOURCE", kickoff="2026-09-05T10:00:00Z", observed_at="2026-09-01T06:00:00Z", sha="3"*64),
        row(comp, fid="right-id", source="RIGHT_SOURCE", kickoff="2026-09-05T14:00:00Z", observed_at="2026-09-02T06:00:00Z", sha="4"*64),
    ]
    with pytest.raises(RuntimeError, match="CROSS_SOURCE_KICKOFF_CONFLICT_AFTER_CANONICAL_BIND"):
        provider.resolve(comp, [
            provider.Provider("LEFT", 1, lambda: left),
            provider.Provider("RIGHT", 2, lambda: right),
        ], audit, error_factory=RuntimeError)


def test_governed_kickoff_identity_is_exact_utc_not_fuzzy():
    comp = "ITA_SerieA"
    a = provider.validate_record(row(comp, kickoff="2026-09-05T10:00:00Z"), comp)
    b = provider.validate_record(row(comp, kickoff="2026-09-05T12:00:00+02:00", fid="f2", source="B", sha="b"*64), comp)
    assert a["kickoff"] == b["kickoff"] == "2026-09-05T10:00:00+00:00"
    provider.assert_provider_compatible([a], [b])


def test_kickoff_and_observed_at_require_timezone():
    comp = "ITA_SerieA"
    with pytest.raises(provider.FixtureIdentityProviderError, match="KICKOFF_TIMEZONE_REQUIRED"):
        provider.validate_record(row(comp, kickoff="2026-09-05T10:00:00"), comp)
    with pytest.raises(provider.FixtureIdentityProviderError, match="OBSERVED_AT_TIMEZONE_REQUIRED"):
        provider.validate_record(row(comp, observed_at="2026-09-01T00:00:00"), comp)


def test_invalid_kickoff_and_observed_at_fail_closed():
    comp = "ITA_SerieA"
    with pytest.raises(provider.FixtureIdentityProviderError, match="KICKOFF_INVALID"):
        provider.validate_record(row(comp, kickoff="not-a-time"), comp)
    with pytest.raises(provider.FixtureIdentityProviderError, match="OBSERVED_AT_INVALID"):
        provider.validate_record(row(comp, observed_at="not-a-time"), comp)


def test_all_providers_unavailable_fails_closed():
    comp = "JPN_J1"
    def fail503():
        raise RuntimeError("HTTP Error 503")
    def timeout():
        raise TimeoutError("timed out")
    with pytest.raises(RuntimeError, match="ALL_PROVIDERS_UNAVAILABLE"):
        provider.resolve(comp, [provider.Provider("A", 1, fail503), provider.Provider("B", 2, timeout)], {}, error_factory=RuntimeError)


def test_provider_contract_rejects_score_result_status_outcome_fields():
    comp = "ENG_PremierLeague"
    for forbidden in ("score", "result", "winner", "points", "outcome", "status"):
        bad = row(comp)
        bad[forbidden] = "DO_NOT_READ"
        with pytest.raises(provider.FixtureIdentityProviderError, match="FIELD_FORBIDDEN"):
            provider.validate_record(bad, comp)


def test_inventory_sort_dedupe_and_sha_are_deterministic():
    comp = "ESP_LaLiga"
    a = row(comp, fid="a", home="A", away="B")
    b = row(comp, fid="b", home="C", away="D", sha="b"*64)
    assert provider.stable_records([b, a, a], comp) == provider.stable_records([a, b], comp)
    assert provider.inventory_sha([b, a, a], comp) == provider.inventory_sha([a, b], comp)


def test_one_domain_failure_does_not_pollute_other_domain():
    audit = {}
    def fail():
        raise RuntimeError("HTTP Error 503")
    provider.resolve("ENG_PremierLeague", [provider.Provider("fallback", 1, lambda:[row("ENG_PremierLeague")]), provider.Provider("E0", 2, fail)], audit)
    provider.resolve("ESP_LaLiga", [provider.Provider("fallback", 1, lambda:[row("ESP_LaLiga")])], audit)
    assert audit.get("failed_domain") is None
    assert set(audit["fixture_identity_selected_provider"]) == {"ENG_PremierLeague", "ESP_LaLiga"}
