# C072-N18A2 FotMob static history5000 — postrun adjudication

## Verdict
`PASS_FOTMOB_STATIC_HISTORY5000`

This is a **history feature-source acquisition PASS only**. It is not a model/scientific-effect PASS and it does not authorize any target label access by itself.

## Execution evidence
- branch: `football3/c072n18a2-fotmob-static-history5000-20260819`
- execution HEAD: `9450b6ce2a6e774b33e70cb71cfa79c583949d74`
- PR: #312 Draft/Open at execution time
- run: `32275900148`
- job: `96143135518`
- run conclusion: SUCCESS
- engineering guard run: `32275900029` SUCCESS
- artifact: `9373984907`
- artifact digest: `sha256:3ff5c60ba58855fba183d1b901594cd623e921580c23abacfbacbd7be6869281`
- artifact size: `4,129,441` bytes

## Frozen upstream assets
Release id `79989708`, tag `fotmob_match_details`.

Downloaded and hash-verified exactly six frozen CSV assets:
- 47 EPL: sha256 `28450c923e6091fc04cd7f40ff1c25d94d4a213edaeec64c2f189d24ac440006`
- 87 LaLiga: `7c3b692ac84c9ff2535c2fcbf11af5a9fd91625e2d2fdd0762b13a1f757f89fa`
- 54 Bundesliga: `3480e08739337e8dabe41098c6c87b131853596d537f5f62a40046087f0f31f4`
- 55 Serie A: `fe5c42d2fe8feb3c76e95e4d07f30cfc8874d82e7575fcc92f1e71c6c1b17076`
- 53 Ligue 1: `793ac1e76ef69e0adbd0a3c90b9ec4d6d3800f6150219991fafdb2e3de0b1fd8`
- 130 MLS: `e1d9f2b1d711ae06c2e04137208a01fb1bd736010012f127b2229651f00d603b`

All six resolved the preregistered schema aliases to `expected_goals`, `min`, `min_added`, `shot_type` without post-view alias changes.

## Coverage
- source shot rows decoded: **234,535**
- distinct source match identities: **9,310**
- usable matches under frozen xG/identity gate: **9,014**
- selected matches: **5,000 exactly**
- selected shot rows: **125,290**
- retained time range: 2020-08-21T17:00:00Z through 2024-09-16T16:30:00Z

The selected 5,000 were chosen only by the frozen SHA-256 identity rule after the frozen usability gate. No outcome-driven replacement occurred.

## Processed artifact hashes
- matches: `sha256:a412dda3bf13768cf6598c9679e39ec45461524f48e5f5b6a13f7bee36463362`
- shots: `sha256:203688ae702c03b327a9d509cc268738bd2c098352e758628d3b9f1275e7a994`
- all 9,310 source match IDs: `sha256:39946b797f517beb3584fce7e9081ddfc5badea9389f5053e887a4b7ff1d1394`

Raw upstream CSV files were temporary and were not retained in the uploaded football3 artifact.

## Consumption boundary
Because the six upstream files are retrospective match-detail assets, **all 9,310 decoded source identities are now GLOBALLY CONSUMED as future target identities**, not just the retained 5,000. They may be used only as historical feature-source evidence.

The 5,000 selected history matches themselves are not and may never become the N18 development/confirmation target cohort.

## No-target assertions
- persisted outcome target fields: 0
- model fits: 0
- target scores: 0
- C070-F Confirmation1597 access: 0
- sealed reserve access: 0
- C073-C077 scientific use: 0

## Next legal step
N18-B must build a **separate, non-overlapping target identity pool** with a frozen PIT total-goal market anchor. Before any outcome access it must:
1. prove zero identity overlap with the 9,310 globally-consumed FotMob source identities;
2. freeze market source/cutoff and target identities;
3. construct historical chance-state features using only matches strictly before each target fixture;
4. freeze the count family, feature equations, temporal folds, development/confirmation split and proper-score gates;
5. only then open target labels once for N18-C development.
