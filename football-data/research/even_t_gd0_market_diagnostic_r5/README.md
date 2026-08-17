# Even-T GD0 Market Diagnostic R5

## Narrow question

Earlier evidence already established three things:

1. A0/PR #58: the existing conditional score model systematically under-allocates central `GD=0` mass for even totals, especially T=2/4/6.
2. R42A/PR #156: dynamic diagonal reallocation from strict historical state did not land downstream and still produced zero natural Top-1 Draw calls.
3. PR #119 and PR #206: global cross-market Draw modeling and Draw-subtype→GD0 bridging did not establish a usable signal.

R5 therefore does **not** ask whether Draw needs more probability, and it does not modify any HDA probability. It asks a more specific component question:

> Once the realized total is known to be T=2,4,6, does current-match market balance discriminate `GD=0` beyond historical state?

If the answer is no, the current stored information is unlikely to make the large oracle parity ceiling deployable by another shell/calibration trick.

## Data and rolling design

R5 uses the existing 26,873-row audited historical ledger and the existing three rolling test positions `[2,3,4]`. For each window and each T in `{2,4,6}`:

- fit rows are chronological train+policy only;
- test rows are the untouched rolling test season for that window;
- evaluation is restricted to market6-complete test rows, selected without using outcomes;
- conditioning on realized T is explicitly retrospective mechanism analysis and is not a deployable input.

No B-package label is opened. B05 remains sealed.

## Fixed market-balance6

The six fixed, interpretable values are:

1. de-vig 1X2 Draw probability;
2. absolute de-vig Home–Away 1X2 probability gap;
3. absolute Asian handicap line;
4. absolute de-vig Asian home-side probability distance from 0.5;
5. OU line;
6. de-vig Over probability.

Alias priority is frozen in the config. Existing processed market values lack original quote timestamps, so they are retrospective closing/reference evidence, **not strict PIT**.

## Fixed comparators

Every reported comparator is evaluated on the same market-complete test rows:

- `parent_multinomial_p0`: the existing conditional multinomial `P(GD=0|T,X)`;
- `core47_binary_full`: binary `GD=0` head from core47, fit on all pre-test rows;
- `core47_binary_marketmatched`: same binary core47 head fit only on market-complete pre-test rows;
- `market6_binary`: market-balance6 only;
- `core47_market6_binary`: core47 + market-balance6.

The binary heads use one frozen estimator: median imputation → StandardScaler → LogisticRegression(C=0.1). No C grid, class weights, threshold search, or feature search.

The primary incremental comparison is `core47_market6_binary` against `core47_binary_marketmatched`. This keeps both the training cohort and evaluation cohort matched, so a market effect cannot be attributed to coverage differences.

## Metrics

Pooled, per-fold, and per-total:

- binary LogLoss;
- Brier;
- AUC;
- observed GD0 rate;
- mean predicted GD0 probability.

R5 also reports raw AUCs for each fixed market-balance signal and competition-season cluster bootstrap LogLoss deltas.

## Descriptive development gate

A market parity signal is only flagged if all pre-frozen conditions hold:

- pooled market-complete even-T test rows >= 2,000;
- each T has >=150 test rows;
- every fold×T has >=100 market-complete fit rows;
- combined LogLoss gain vs market-matched core >=0.003;
- cluster-bootstrap LogLoss delta p95 <0;
- combined AUC gain >=0.01;
- Brier non-worse;
- combined LogLoss wins in at least 2 of 3 totals.

The 100-row fit threshold was fixed before any R5 outcome metric was run because T=6 has only 1,027 rows in the entire audited 26,873-row ledger; a 300-row threshold would mechanically block the earliest window.

Even a gate PASS is descriptive only and does not authorize opening B05.

## Hard boundaries

- no HDA/score probability mutation;
- no forced Draw or threshold;
- no new provider/data request;
- no formal model/data/config/CURRENT/main mutation;
- formal_weight=0;
- B05-B07 labels opened=0;
- future OOS label opening not authorized.
