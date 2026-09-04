from __future__ import annotations

import enroll_stage6_pre_b_receipts as base


EnrollmentError = base.EnrollmentError


def map_queue_to_future(fixtures: list[dict], future: list[dict]) -> list[tuple[dict, dict]]:
    """Map the frozen queue onto current future discovery without reordering it.

    Queue order is already part of the frozen ordered_queue_identity_sha256.
    Future discovery is used only as an identity/kickoff lookup.  In particular,
    same-kickoff atomic groups must retain the exact preflight queue order rather
    than being re-sorted by Understat match id.
    """
    by_mid = {int(r["mid"]): r for r in future}
    if len(by_mid) != len(future):
        raise EnrollmentError("future match id collision")
    out = []
    for q in fixtures:
        mid = int(q["understat_match_id"])
        r = by_mid.get(mid)
        if r is None:
            raise EnrollmentError(f"locked fixture absent from current future discovery {mid}")
        league = base.COMP_TO_LEAGUE.get(str(q["competition"]))
        if league != r["league"]:
            raise EnrollmentError(f"league identity drift {mid}")
        if str(q["home_team"]) != str(r["home_team"]) or str(q["away_team"]) != str(r["away_team"]):
            raise EnrollmentError(f"team identity drift {mid}")
        if base.parse_iso(q["scheduled_kickoff_utc"]) != r["kickoff"]:
            raise EnrollmentError(f"locked kickoff drift {mid}")
        out.append((q, r))
    if [x[0]["fixture_identity_sha256"] for x in out] != [x["fixture_identity_sha256"] for x in fixtures]:
        raise EnrollmentError("queue order drift after future mapping")
    return out


base.map_queue_to_future = map_queue_to_future


if __name__ == "__main__":
    raise SystemExit(base.main())
