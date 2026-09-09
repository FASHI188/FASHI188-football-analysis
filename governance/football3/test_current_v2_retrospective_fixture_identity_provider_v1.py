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


def row(comp, *, fid="f1", home="HOME", away="AWAY", source="FALLBACK", sha="a"*64):
    return {
        "competition": comp,
        "fixture_identity": fid,
        "kickoff": "2026-09-05T10:00:00+00:00",
        "home_identity": home,
        "away_identity": away,
        "source_identity": source,
        "observed_at": "2026-09-01T00:00:00+00:00",
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
    assert rows == [row(comp)]
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


def test_two_provider_identity_agreement_passes():
    comp = "ENG_PremierLeague"
    provider.resolve(comp, [
        provider.Provider("A", 1, lambda: [row(comp, source="A", sha="a"*64)]),
        provider.Provider("B", 2, lambda: [row(comp, fid="f2", source="B", sha="b"*64)]),
    ], {})


def test_provider_identity_conflict_fails_closed():
    comp = "ENG_PremierLeague"
    with pytest.raises(RuntimeError, match="FIXTURE_IDENTITY_PROVIDER_CONFLICT"):
        provider.resolve(comp, [
            provider.Provider("A", 1, lambda: [row(comp, source="A")]),
            provider.Provider("B", 2, lambda: [row(comp, fid="f2", away="OTHER", source="B")]),
        ], {}, error_factory=RuntimeError)


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
