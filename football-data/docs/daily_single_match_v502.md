# Daily Single Match V5.0.2

This is an offline-first orchestration layer over the existing formal prediction engine. It does not train a model, add a probability source, fetch fixtures, query a provider, or alter formal weights.

## Modes

### `live_user_supplied`

Use for an actual future match only when the caller supplies:

- verified match identity evidence observed no later than the freeze time;
- explicit season;
- official question-time data freshness evidence matching the repository history;
- optional market and lineup evidence frozen before kickoff.

No network lookup is performed. Missing synchronized tradable prices produces `No Bet`/`价格不可用`.

### `offline_repository_snapshot_demo`

Used only for deterministic CI and end-to-end proof. The match scenario is not schedule-verified, the report must formally abstain, and the repository snapshot timestamp must be before the supplied freeze time. This mode cannot be presented as historical PIT validation or a live recommendation.

## Run

```bash
PYTHONPATH=football-data/engine:football-data/validation \
python football-data/engine/daily_single_match_v502.py \
  --input football-data/examples/daily_single_match_v502_offline_demo.json \
  --output-dir /tmp/daily-single-match \
  --print-summary
```

## Evidence bundle

The output directory contains:

- `normalized_input.json`
- `context.json`
- `calculation.json`
- `validation.json`
- `report.json`
- `report.md`
- `receipt.json`
- sanitized formal-runner stdout/stderr

`report.json` and `report.md` always use the CURRENT V5.0.2 A-H structure. `receipt.json` records the exact Git HEAD, hashes of generated artifacts, zero provider requests, zero API-key access, zero training, and zero formal-weight changes.

## Fail-closed boundaries

The command refuses:

- target-result or postmatch fields in the target input;
- unknown execution modes;
- a freeze at or after kickoff;
- missing/late match-identity evidence;
- unverified identity in live mode;
- missing live data-freshness evidence;
- repository snapshot evidence observed after the freeze;
- non-empty output directories;
- formal-runner or matrix-validation failure.

The existing formal runner remains the only probability-producing component.
