# C072-N15W — A-League Women structurally constrained O/U-tail P(T) development

## Lineage / evidence class
- Project: **football3** only.
- Scientific root: C072-C, root HEAD `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- Parent zero-label data plan: C072-N14WR1 authoritative run `32258495452`, terminal `C072N14WR1_ALEAGUE_WOMEN_DEV_PLAN_ZERO_LABEL_PASS`.
- N14W remains permanently STOP; N14WR1 created a new zero-label plan rather than changing N14W's gate.
- Development seasons fixed: 2021-2022, 2022-2023, 2023-2024, 2024-2025; zero-label all-five inventories 59/61/139/135 = 394.
- 2025-2026 fixed reserve inventory 116 remains **target-sealed** and its file must not be downloaded by N15W.
- 2020-2021 is excluded from this plan and must not be downloaded or scored.
- C073-C077 and descendants remain scientifically quarantined.
- C070-F Confirmation1597 and protected assets remain sealed.
- N15W is a **POST-VIEW HYPOTHESIS ON A NEW DATA PLAN**. The hypothesis was formulated only after N13 men's raw-15-logit representation PARKed; N13 labels may not be reused to select any N15W formula or threshold.

## Scientific question frozen before target access
Do five contemporaneous O/U half-goal lines, used according to their cumulative-tail probability meaning, provide materially better complete match-level `P(T=0,1,2,3,4,5,6,7+)` than a single O/U2.5 market line closed with a Poisson total-goal family?

This tests **representation structure**, not a learned model. There is no model fit, hyperparameter, regularization constant, feature subset, class weight or probability-calibration fit in N15W.

## Pinned source files
Repository/revision:
`betfair-datascientists/betfair-datascientists.github.io@9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`

N15W may download only:
- `A-League_Womens_2021-2022_All_Markets.csv`, SHA256 `f2e2a957c7674c5626eff71c7ab360e698c1ccb9f8054383e5c5c0eb6498a86f`
- `A-League_Womens_2022-2023_All_Markets.csv`, SHA256 `899992d57d65c4786b47057a8c009dbeea3ec170aadc29beb1fd47e4b12a7524`
- `A-League_Womens_2023-2024_All_Markets.csv`, SHA256 `85e3b6d3cf2f3e54e1adbe456e24ec5042946e5102c99501c412039279814792`
- `A-League_Womens_2024-2025_All_Markets.csv`, SHA256 `3daf737dd36ef95b4e2e0d354674842faec755016e43abb9f987d67b1d594060`

Forbidden downloads in N15W:
- 2020-2021
- 2025-2026
- any other competition/source.

## Eligibility must be frozen zero-label before targets
Use the exact N14W fail-closed O/U market recognition:
- preferred lines exactly 0.5,1.5,2.5,3.5,4.5;
- full-match market types `OVER_UNDER_05/15/25/35/45` or exact market-name equivalent;
- exact Over/Under runner naming and one selection ID per side;
- one market ID per event×line;
- finite best back>1 and best lay>1 with back<=lay;
- all five lines complete at T-60/T-30/T-1.

Before any `TOTAL_GOALS` value is decoded, N15W must reproduce exact eligible event counts:
59 / 61 / 139 / 135.
Any mismatch terminal-stops before target access.

## Allowed target access
Only after the exact eligibility inventory is frozen, N15W may decode `TOTAL_GOALS` on rows belonging to those already-eligible EVENT_IDs in the four development files.

For each eligible event:
- all repeated TOTAL_GOALS cells may be read solely to verify consistency;
- require one unique finite nonnegative integer value;
- materialize exactly one target per event;
- conflicting/missing/invalid target excludes that event with no replacement.

Target:
`T=min(TOTAL_GOALS,7)`, classes `[0,1,2,3,4,5,6,7+]`.

Forbidden from use or diagnostics:
- `IS_WINNER`, `HOME_SCORE`, `AWAY_SCORE`, `RUNNER_STATUS`;
- H/D/A outcome;
- exact score;
- any result-dependent subset.

2025-2026 target values read/materialized = exactly 0.

## Market probability extraction at T-1min
For each line L and each Over/Under side, use T-1min best back/lay only.

Runner midpoint implied probability:
`m = 0.5*(1/best_back + 1/best_lay)`.

Two-sided de-vig Over probability:
`q_L = m_over / (m_over + m_under)`.

Interpretation:
- `q_0.5 ≈ P(T>=1)`
- `q_1.5 ≈ P(T>=2)`
- `q_2.5 ≈ P(T>=3)`
- `q_3.5 ≈ P(T>=4)`
- `q_4.5 ≈ P(T>=5)`.

No T-60/T-30 feature enters the scientific representation. Those snapshots are used only by the already-frozen eligibility rule. This prevents post-view dynamic weighting and isolates the structural cross-line hypothesis.

## B0 — single-line Poisson baseline
Use `q_2.5` only.

Find the unique `lambda` in `[0.01,20]` satisfying:
`P_Poisson(lambda)(T>=3) = q_2.5`.

Solve by deterministic bisection:
- exactly 80 iterations;
- lower=0.01, upper=20;
- if survival(mid)>=q, set upper=mid, else lower=mid;
- lambda = midpoint after iteration 80.

B0 probabilities:
- p0..p6 = ordinary Poisson mass at k=0..6;
- p7+ = `1-sum(p0..p6)`.

No fitting to football labels occurs.

## C — structured five-line cumulative-tail reconstruction
### Step 1: monotone structural projection
Raw vector:
`q=(q0.5,q1.5,q2.5,q3.5,q4.5)`.

A valid cumulative tail must be non-increasing. Project q onto the closed set
`1 >= z0 >= z1 >= z2 >= z3 >= z4 >= 0`
using **unweighted least-squares isotonic regression / pool-adjacent-violators algorithm**, frozen as follows:
- start five singleton blocks with value qi and weight1;
- scan left-to-right;
- whenever previous block value < next block value, merge the two using their weight-average;
- after a merge, compare backward and continue merging until all block values are non-increasing;
- expand block values back to five positions;
- no label-derived weights or tolerance.

Call the projected tails `z0..z4`.

### Step 2: exact bins through four goals
- `p0 = 1-z0`
- `p1 = z0-z1`
- `p2 = z1-z2`
- `p3 = z2-z3`
- `p4 = z3-z4`
- total tail `R5 = z4 = P(T>=5)`.

### Step 3: frozen high-tail closure
Use the **same single-line lambda from B0**, derived only from raw q2.5, to split R5 across 5,6,7+.

Let under that Poisson:
- `a5=P(X=5)`
- `a6=P(X=6)`
- `a7p=P(X>=7)`
- `A=a5+a6+a7p=P(X>=5)`.

Then:
- `p5=R5*a5/A`
- `p6=R5*a6/A`
- `p7+=R5*a7p/A`.

This tail closure is fixed before labels and shares the baseline's q2.5-derived count-family shape; the candidate's new information is the five-line cumulative surface, not a separately tuned tail model.

### Numerical score floor
For both B0 and C only when scoring:
- replace each probability by `max(p,1e-12)`;
- renormalize to sum1.
This is numerical protection only and is identical for both representations.

## Proper-score evaluation
There is no training sample. Each eligible match produces B0 and C probabilities solely from its pre-match market snapshot.

Score all valid target events in each of the four development seasons independently and pooled.

Metrics:
- multiclass LogLoss — primary;
- multiclass Brier;
- RPS;
- Top1 accuracy;
- Top3 accuracy;
- fraction Top1 T=2;
- probability-conservation residual;
- fraction of events requiring any isotonic pooling;
- mean/max absolute isotonic tail adjustment — diagnostic only.

Paired bootstrap on pooled match rows:
- 5000 reps;
- seed `72018`;
- candidate-minus-baseline LogLoss;
- 90% CI and P(delta<0).

## Frozen development PASS gate
`C072N15W_STRUCTURED_PT_DEVELOPMENT_PASS` requires ALL:
1. exact zero-label eligibility counts 59/61/139/135 reproduce before targets;
2. valid scored targets >=380;
3. pooled C-B0 dLogLoss <0;
4. paired bootstrap90 upper dLogLoss <0;
5. at least 3/4 development seasons improve LogLoss;
6. pooled dBrier <=0;
7. pooled dRPS <=0;
8. pooled Top1 delta >=0;
9. pooled Top3 delta >=0;
10. max probability residual <=1e-12;
11. noneligible target cells read=0;
12. 2025-2026 target values read=0 and file not downloaded;
13. C070-F/protected remain sealed and C073-C077 scientific results unused.

## Breakthrough screen
`breakthrough_screen_pass=true` only if development PASS and:
- pooled C-B0 dLogLoss <= **-0.010**;
- pooled C-B0 dRPS <= **-0.001**;
- all 4/4 development seasons have dLogLoss <=0.

This is a strong development-screen definition, not confirmation.

## Terminal
- breakthrough screen: `C072N15W_STRUCTURED_PT_BREAKTHROUGH_SCREEN_PASS`
- ordinary development PASS: `C072N15W_STRUCTURED_PT_DEVELOPMENT_PASS`
- otherwise: `C072N15W_STRUCTURED_PT_PARK`
- eligibility mismatch before targets: `C072N15W_ZERO_LABEL_ELIGIBILITY_MISMATCH_STOP`.

## Confirmation rule
Only if development PASS may a separate N16 contract be frozen before opening 2025-2026.
N16 must use the exact same B0/C formulas, no modification, and score the fixed 116-event zero-label reserve once.

No post-view repair of N15W development labels is allowed: no alternate isotonic weighting, no line subset, no tail family change, no T-60/T-30 weighting, no threshold change, no score floor change and no smoothing parameter.
