# Direct-T + P(GD|T,X) Fixed200 R1

Research-only exploratory run on already-viewed historical data.

- Sample size: 200.
- Sample selection: deterministic SHA-256 identity ordering from the latest rolling test fold; labels do not select rows.
- Architecture: `P(T|X) * P(GD|T,X)`.
- Exact legal score mapping is explicit for `T=0..6`; `T=7+` is retained only as an H/D/A sign reference in this quick test.
- No forced draw, no manual threshold, no class-weight override.
- `formal_weight=0`; no formal model/data/config/CURRENT/main mutation.
