# V2 Match Process Contract

Status: FROZEN BEFORE IMPLEMENTATION

## Segments
If and only if lawful minute-level history exists with provenance and PIT semantics, model prematch latent event intensity over `0-30`, `31-60`, `61-75`, `76-90`, and stoppage-time segments.

Prematch covariates may include verified rest days, schedule density, travel/continuous-away indicators, pre-known weather, expected substitution timing distributions, bench strength, red-card risk prior, VAR intervention prior, injury-interruption prior, and stoppage-time distribution. Pre-known heat/humidity and documented competition hydration/cooling rules may alter priors only if known before cutoff.

## Live separation
Actual substitutions, red cards, VAR decisions, injuries, hydration breaks and announced/observed stoppage time become inputs only after they occur in a separately named live mode. They are forbidden in prematch training examples for their own target fixture.

## Data gate
Minute-level process code cannot be marked validated from score-only data. Required source manifest must include event timestamps, event semantics, URL/source file hash, retrieval time and known_at policy. If absent, V2 writes `MATCH_PROCESS_STATUS=BLOCKED_DATA`; no synthetic pseudo-events are fabricated.

## Ablation
If data gate passes, process layer must pass the same strict-time multi-fold ablation gates before retention. Otherwise it is excluded from final candidate.