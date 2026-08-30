# V2 Expanded Historical PIT Research

Status: **RESEARCH_ONLY**. This directory is for historical development/evaluation only. It does not freeze a formal V2 candidate, create 30/100/300 prospective validation, activate production, or alter `main`, `CURRENT`, Airtable, PR #334, or R5.

## Phase-1 source and license

The first batch uses only OpenFootball repositories locked to exact commits in `sources.lock.json`. The repository license notice is required to contain Public Domain and CC0/Creative Commons Zero language. Raw source files are fetched at run time and are not committed here.

Coverage is the top division of England, Spain, Germany, Italy and France for 2022/23 through 2025/26. The expected raw universe is about 7,082 completed matches. `2022/23` is development-only. `2023/24`–`2025/26` is the evaluation period and must contain at least 3,000 usable matches after strict kickoff parsing; otherwise the run fails.

## PIT boundary

- Match cutoff is kickoff in UTC.
- The prediction-safe evaluation file contains identity, competition, season, kickoff, teams and round metadata only.
- Full-time goals are stored in a separate label vault.
- A completed match is allowed to update model state only when `prior kickoff + 3h <= current kickoff`.
- Matches sharing an exact UTC kickoff are predicted and frozen as one batch before any label from that batch can update state.
- The predictor is a separate process and has no label-vault argument.
- The replay driver may release only already-predicted, already-matured past results for state updates. It does not calculate evaluation metrics.
- The scorer imports no V1/V2 model code and reads evaluation labels only after the combined prediction file and SHA are frozen.

## Development lock

`fit_locks.py` receives only the 2022/23 development file. It selects:
- a frozen V1 parameter configuration from the V1 historical parameter grid using development-period 1X2 LogLoss;
- a V2 joint-score research configuration using development-period exact-score LogLoss.

No 2023/24–2025/26 evaluation label file is an input to the lock-fitting step. The resulting V2 lock is explicitly `formal_candidate=false`.

## Same-match comparison and ablation

V1, V2 joint-score, and V2 joint-score-OFF use the same fixture IDs, kickoff batches, historical result availability and team identities. `v2_joint_off` replaces only the joint-score family with independent Poisson while retaining the same V2 state and pre-match features.

Phase-1 layer status:
- joint score core: tested by V2 joint vs V2 independent-Poisson ablation;
- player: `DATA_UNAVAILABLE`;
- starting lineup: `DATA_UNAVAILABLE`;
- substitutes: `DATA_UNAVAILABLE`;
- coach: `DATA_UNAVAILABLE`;
- match process: `DATA_UNAVAILABLE`.

Unavailable layers are not credited with improvement.

## Metrics

The independent scorer reports:
- 1X2 LogLoss, multiclass Brier, RPS and Top1;
- draw Brier, calibration/ECE and Draw F1;
- exact-score LogLoss plus 0-0, 1-1 and 2-2 probability calibration;
- underdog-win detection using a shared past-only Elo grouping rule;
- league, season and shared cold-start groups;
- lineup completeness as `DATA_UNAVAILABLE`;
- V2 joint-score ON/OFF deltas and V2-vs-V1 deltas.

All outputs bind exact repository HEAD, source/data/alias/identity SHAs, lock SHAs, per-batch prediction freeze receipts, final prediction SHA, scorer SHA, result SHA and Artifact manifest/readback receipt.
