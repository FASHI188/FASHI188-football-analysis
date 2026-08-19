# C072-N11 public Betfair mirror — post-run adjudication

Project: football3 only
Official run: 32249441290
Official job: 96056966743
Artifact: `football3-c072n11-public-betfair-zero-label-audit-official`
Artifact id: 9363833379
Artifact digest: `sha256:85b9d5535717f3c278feca6282fc33c44be7d319d53ad26f3631ccb9a04bd091`
Source: `marcosf63/bet` at `90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff`

## Terminal
`STRUCTURAL_REPLAY_STOP`

## Exact zero-label result
- files scanned: 1401
- recognized preferred O/U markets (`OVER_UNDER_05/15/25/35/45`): 0
- unique events with preferred O/U: 0
- valid prematch preferred-O/U LTP updates: 0
- parser errors: 0
- target fields accessed: 0
- settlement fields accessed: 0
- model fits: 0
- C073-C077 scientific results used: false
- C070-F Confirmation1597 opened: false

## Ruling
This public mirror does not contain the dynamic preferred O/U stream family needed by C072-N11. It is therefore stopped as an N11 source candidate. Gates may not be relaxed on this same package to manufacture a PASS.

This STOP is a source-availability result only. It does not falsify the dynamic multi-line O/U scientific hypothesis, because no target labels were opened and no P(T) model was fitted.

The mirror was already globally consumed at the outcome-domain level by a quarantined project, so even a structural PASS would not have qualified it for fresh football3 confirmation.
