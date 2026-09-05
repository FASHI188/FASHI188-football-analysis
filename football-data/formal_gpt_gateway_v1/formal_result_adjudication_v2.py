#!/usr/bin/env python3
from __future__ import annotations

# Compatibility shim for the in-flight PR. The governed identity implementation
# stays in formal_frozen_xg_identity_adjudication_v2. After that contract is
# installed, add the isolated V1 release-order replay adapter so delayed formal
# settlements cannot reverse Frozen V1 kickoff chronology. No model, source score,
# sample, CURRENT, or production pointer is rewritten here.
import formal_frozen_xg_identity_adjudication_v2 as identity
import formal_v1_release_order_replay_v1 as release_order

SCHEMA = identity.SCHEMA
adjudication_entries = identity.adjudication_entries


def install():
    receipt = dict(identity.install())
    receipt["v1_release_order_replay"] = release_order.install()
    return receipt
