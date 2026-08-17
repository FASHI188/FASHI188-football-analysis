# Direct-T Market-State Routing R3

## Question

PR #204 showed that geometry39, geometry+core86, and all122 historical/expert-input state do **not** explain the replicated per-match Direct-T expert complementarity. R3 tests one genuinely different information block: the current-match bookmaker market surface already present in historical processed files.

## Evidence class

This is retrospective post-view mechanism research only. The existing processed market columns do not carry an original quote timestamp, so they are closing/reference evidence and **not strict PIT pre-match snapshots**. A positive result cannot be called scientific confirmation, cannot authorize formal promotion, and cannot by itself authorize opening B05.

## Frozen market10 block

No feature is selected from outcomes. The ten fixed values are:

1. de-vig 1X2 home probability
2. de-vig 1X2 draw probability
3. de-vig 1X2 away probability
4. 1X2 overround
5. Asian handicap line
6. de-vig Asian home-side probability
7. Asian overround
8. total line
9. de-vig Over probability
10. OU overround

Field alias priority is frozen in `config/direct_t_market_state_routing_r3.json` and mirrors the existing market audit priority. De-vigging is normalized inverse decimal odds.

## Coverage gate before routing metrics

Market completeness is determined only from identity/source-row provenance and the ten frozen fields. It does not use match outcome, realized T, expert loss, or oracle winner.

Routing metrics are forbidden unless both policy and evaluation satisfy:

- complete-market rate >= 90%;
- complete policy rows >= 500;
- complete evaluation rows >= 1,000.

If the gate passes, only market10-complete rows are used, and the resulting evaluation identity set is hashed before reporting routing results. Missing market10 values are never imputed.

## Frozen router

The selector target and estimator are unchanged from R2:

- target: expert per-match LogLoss minus common-baseline per-match LogLoss;
- common reference: zero;
- estimator: StandardScaler + Ridge(alpha=10);
- no hyperparameter search;
- no threshold search;
- no competition identity;
- no realized total or result-derived input.

Families:

- `relative_geometry39`: coverage-matched comparator;
- `relative_geometry_market49`: geometry39 + market10, **primary mechanism family**;
- `relative_geometry_core_market96`: geometry39 + core47 + market10;
- `relative_all_market132`: all122 + market10.

Historical nullable blocks keep the established policy-fit median-imputation contract. Market10 itself must be complete.

## Development signal threshold

A descriptive signal for designing a future OOS contract requires the primary market49 family to satisfy all of:

- LogLoss gain vs common baseline >= 0.005;
- paired-bootstrap LogLoss p95 < 0;
- Brier non-worse;
- RPS non-worse;
- at least two experts selected.

Even if all pass, the verdict remains descriptive only because the market evidence lacks original PIT timestamps and the evaluation outcomes are already viewed.

## Hard boundaries

- B01-B04 are not reused as confirmatory data.
- B05-B07 labels opened = 0.
- Provider/paid API/new data collection = 0.
- formal_weight = 0.
- formal model/data/config/CURRENT/main mutation = 0.
- No automatic future OOS label opening is authorized.
