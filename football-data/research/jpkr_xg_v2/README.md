# Football3 Japan / Korea xG Source Audit V1

Status: **research-only**. This directory does not alter production wiring, CURRENT, formal model parameters, the 75/25 Fusion V2 weight, the production pointer, or any production runner.

## Question

`JPN_J1` and `KOR_KLeague1` already belong to the formal Football3 scope. The production historical xG source path, however, is Understat/confirmation data for the European Big-5. When the frozen xG challenger has insufficient evidence for J1/K1, the formal model must remain on `FROZEN_V1_EXACT_FALLBACK`.

This audit asks whether a no-key, no-login, license/provenance-acceptable match-level xG source exists with enough historical coverage to justify a separate Japan/Korea research adapter and rolling OOS test of the already-frozen 75% xG + 25% Frozen V1 formula.

## Non-negotiable boundaries

- Do not add J1/K1 to scope; they are already in scope.
- Do not modify the Understat Big-5 channel.
- Do not modify CURRENT, formal model code/parameters, the frozen 75/25 weight, production runner, production pointer, or formal activation.
- Do not use paid APIs, credentials, login-gated datasets, or automated access prohibited by source terms.
- Do not derive or substitute xG from goals.
- Source/coverage audit is zero-label. If it fails, no target labels are materialized and no OOS scoring is run.
- If a source later passes, each identical-kickoff batch must be predicted in full before any result/xG labels from that batch are released.
- 2026 Japan special competition semantics must preserve the 90-minute score/result separately from penalty shootout outcomes and preserve regional/play-off stage identity.
- K League 1 must preserve regular-stage and Final A/Final B stage identity and schedule changes.

## Frozen pre-evaluation decision rule

A league may advance from source audit to an adapter/OOS candidate only if at least one source provides genuine match-level `home_xg` and `away_xg`, stable fixture identity and kickoff provenance, usable observed/available timing, acceptable no-key/no-login access, and enough seasons to support the pre-registered rolling OOS split. A partial one-season source cannot silently stand in for the required multi-season evidence.

If either league lacks a qualifying source, that league is `STOP_DATA_COVERAGE`; its OOS metrics are `NOT_RUN_ZERO_LABEL_GATE_FAILED`. Fixed 75/25 is never retuned as a rescue.

See `frozen_research_contract_v1.json` and `source_registry_v1.json` for the frozen identities and decisions. `source_audit_v1.py` mechanically audits only permitted public GitHub sources and emits an immutable zero-label audit receipt.