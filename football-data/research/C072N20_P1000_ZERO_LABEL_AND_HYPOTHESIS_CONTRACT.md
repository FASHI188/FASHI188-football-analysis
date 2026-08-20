# C072-N20 — latent market-measurement P(T) P1000 contract

Status: preregistered POST-VIEW DEVELOPMENT / REPLICATION pilot. formal_weight=0.

## Lineage
- football3 root: C072-C @ `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- parent correction HEAD: `d47503538bc2f7207bfd59d0ca7b21142e24addc`.
- C073-C077 and later football-project scientific conclusions remain quarantined.
- Cross-project information may be used only to exclude globally consumed identities.

## Why this is materially different
Prior football3 multi-line attempts treated O/U lines either as ordinary classifier features or as direct/noiseless CDF constraints. N20 tests a different measurement architecture:

`latent P(T) -> line-specific noisy/calibrated market tail observations`.

The scientific candidate is frozen conceptually before any N20 target access:
1. use already-consumed N17 development labels only as a fixed training/calibration set;
2. fit one low-DF line-specific calibration map for each market tail O/U0.5..4.5;
3. enforce monotone tail coherence;
4. reconstruct the latent exact-T distribution, with the >=5 continuation inherited from the same calibrated O/U2.5 Poisson anchor;
5. compare on a new exact 1000-match target cohort against a same-training calibrated single-line O/U2.5 Poisson baseline.

No model-family, C, line-subset, feature, threshold, league-subset or smoothing search is permitted after N20 labels are viewed.

## Immutable zero-label source assets
Use exact GitHub Actions artifacts, not live page selection:
- football3 N16R1 artifact `9368768296`: contains the immutable 14,250-row Footiqo full zero-label inventory and the exact prior 2,000 selected identities;
- football-project C079-P1000R1 artifact `9390121069`: contains the exact market-only 1,000 identities later consumed by C079-B.

The second artifact is used ONLY as a global-consumption exclusion list. Its scientific result, model family, metrics and stopping decision are not inputs to N20.

## New 1000 identity selection
From the N16R1 full inventory:
- require sourceCode in {BR, GR, MLS, TR};
- require complete identity metadata;
- require all ten O/U prices O05/U05/O15/U15/O25/U25/O35/U35/O45/U45 numeric and >1;
- exclude every exact identity in the N16R1 selected-2000 set (including its unread reserve);
- exclude every identity matching the exact C079 consumed-1000 set on mapped domain+id+matchDate+homeTeam+awayTeam;
- sort remaining rows by frozen `identity_sha256`, then take exactly the first 1000.

Selection must not use prices beyond the predeclared completeness gate, outcomes, score fields, target totals or any model score.

## Zero-label PASS gate
`C072N20_P1000_ZERO_LABEL_PASS` requires ALL:
1. N16R1 full inventory rows exactly 14,250;
2. N16R1 selected old cohort exactly 2,000 and its ordered identity hash reproduces `65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559`;
3. C079 exclusion cohort exactly 1,000 and its identity hash reproduces `ce2af86f206077255ea489242a3e8473e34b89f140cc9528f2ad9594593c3413`;
4. eligible after exclusions >=1,000;
5. selected rows exactly 1,000;
6. overlap with N16R1 selected2000 = 0;
7. overlap with C079 consumed1000 = 0;
8. duplicate selected identities = 0;
9. all-five-line two-sided O/U completeness = 100%;
10. target/result labels materialized = 0; model fit/scoring = 0.

If any gate fails, STOP before target access. No replacement by relaxing exclusions or coverage.

## Evaluation contract to freeze after zero-label PASS
After the exact selected-1000 identity SHA is known, a separate commit must freeze it before target access. The evaluation must use:
- training/calibration: only the previously consumed N17 development subset from the N16R1 old2000; N17 reserve266 remains unread;
- test: all and only the new N20 selected1000;
- target: T=min(FTHG+FTAG,7), classes 0..6,7+;
- baseline B0: calibrated O/U2.5 tail -> Poisson full-support -> 0..6,7+;
- candidate C: five line-specific calibrated tails + monotone projection + fixed Poisson continuation beyond >=5;
- primary: paired exact-T multiclass LogLoss; secondary: Brier/RPS; Top1/Top3 diagnostic only;
- paired bootstrap 5000, seed 72020;
- source-league consistency required;
- this 1000 is a pilot/development screen, not confirmation. A positive pilot must be followed by a separate power/precision calculation and new confirmation plan before any confirmation labels are opened.

## Hard boundaries
- C070-F Confirmation1597 remains sealed.
- N17 reserve266 remains sealed.
- N18 confirmation150 remains sealed.
- No Draw/0-0/1-1/T=2 boost or threshold tuning.
- No reuse of the new1000 for neighboring representation/model searches after labels are viewed.
- No C073-C077 scientific result may be imported as football3 evidence.
