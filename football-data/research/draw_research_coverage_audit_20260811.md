# Football Draw Research Coverage Audit — 2026-08-11

Status: research inventory only. `formal_weight=0`. No model fit, no provider request, no new data collection, no fixed200 consumption, no formal model/data/config/CURRENT/main mutation.

## Purpose

Stop repeated draw experiments that merely rename an already-tested information or model family. This audit separates:

1. scientific FAIL — enough data, frozen test executed, preregistered gate failed;
2. weak/non-robust signal — favorable point estimates that failed uncertainty or exact replication;
3. coverage/data BLOCKED — the scientific question is still unanswered because admissible inputs were insufficient or absent;
4. technical PASS only — a collector/parser/detector works, but no predictive evidence exists;
5. historical component PASS — useful conditional component, not a solved joint Draw prediction chain.

No item below is formal model authority. CURRENT remains the only formal rule source.

## Executive finding

The repository has already tested most obvious static historical draw-information families and most obvious probability/decision geometries. Repeating those families with another threshold, window, classifier, calibration transform, histogram, or one-parameter dependence shell is not justified.

The remaining high-value gaps are primarily **data-gated**, not algorithm-gated:

- genuinely timestamped synchronized multi-market pre-kickoff trajectories (Match Odds + AH + multiple OU lines) at adequate scale;
- authentic `observed_at` confirmed-XI / injury / availability evidence at adequate forward scale;
- true minute-level event / shot-quality / substitution / VAR / score-state sequence history with admissible pre-match aggregation;
- broader genuine referee-history coverage beyond the currently coverage-blocked domains.

A separate unresolved narrow question is static OU2.5 as a Direct-T increment: R42N did not fail scientifically; it stopped at 99 fresh eligible rows after prior fixed200 exclusions, below the frozen 200-row gate.

## Coverage matrix

| Information / structure family | Representative research | Terminal class | What was learned | Repeat status |
|---|---|---|---|---|
| Market draw probability / GOLD1000 ranking | PR #80 | Scientific/confirmation FAIL | Draw probability contains some ranking signal, but selective Draw gate did not confirm | SEALED for threshold/class-weight variants |
| Closing draw probability screen | PR #86 | FAIL | Simple closing draw screens insufficient | SEALED |
| Balance × Under geometry | PR #87 | FAIL | Low-score market geometry alone insufficient | SEALED |
| Market entropy / uncertainty geometry | PR #88 | no promotion | No robust Draw increment | SEALED |
| Markov / ordered-logit draw mispricing | PR #98 | no promotion | Alternative price-edge geometry did not establish robust signal | SEALED |
| Draw-risk execution expression | PR #99 | execution FAIL | Draw/Under/AH expressions lost together on frozen test | SEALED as execution shortcut |
| Learned market trajectory Draw boundary | PR #116 R39D | Scientific FAIL | Draw calls remained sparse; proper scores/AUC did not improve | SEALED |
| Full 72h trajectory representation | PR #117 R39E | Scientific FAIL | Full trajectory did not beat simpler benchmark | SEALED |
| Provider calibration / lead-lag | PR #118 R39F | Scientific FAIL | Microstructure lead effects did not translate to robust Draw gain | SEALED |
| Opening/closing 1X2+OU+AH cross-market | PR #119 R39G | Scientific FAIL | Cross-market static/trajectory combination worse | SEALED |
| Market draw calibration regimes | PR #122 R39J | Policy reversal / FAIL | Apparent development gain was nonstationary | SEALED |
| Causal online draw residual calibration | PR #123 R39K | Scientific FAIL | Online recalibration did not improve Draw proper score | SEALED |
| Cross-book 1X2 disagreement | PR #154 R41C | fixed200 FAIL | LL/Brier point estimates improved but bootstrap p95 crossed zero; Draw AUC fell | SEALED |
| HDA+AH+OU structural state | PR #154 R41D + PR #155 replication | fixed200 + exact replication FAIL | Parent looked mildly favorable; replication failed uncertainty gate and Draw LL worsened | SEALED |
| Market log-prob calibration | PR #154 R41E | fixed200 FAIL | LL/Brier and Draw diagnostics worse | SEALED |
| Dynamic draw-mass redistribution | PR #154 R41F | fixed200 FAIL | Tiny LL/Draw improvements, but uncertainty/RPS gate failed | SEALED |
| Static OU2.5 -> Direct-T single feature | PR #172 R42N | COVERAGE BLOCKED | Only 99 fresh eligible rows after prior3800; `sample=null`, `model_fits=0` | UNANSWERED; do not lower gate |
| Authentic synchronized Betfair MO+AH+multi-OU | PR #108 / PR #158 R43A | DATA BLOCKED | Historical stream/synchronization coverage inadequate or input absent | GENUINE GAP if authentic data obtained |
| Ordinary rolling form / goals / draws / shots / cards | PR #80 and earlier families | Scientific FAIL / unstable | No stable incremental Draw ranking | SEALED for ordinary rolling variants |
| Venue-specific history | PR #145 R40G | Rolling OOS FAIL | HDA LL, Draw LL, Draw AUC worse | SEALED |
| Absolute Elo level | PR #147 R40I | Rolling OOS FAIL | Small Draw AUC gain but proper scores/stability worse | SEALED |
| History maturity / cold-start | PR #148 R40J | Rolling OOS FAIL | No meaningful Draw gain | SEALED |
| Existing PIT feature inventory | PR #146 / #149 | inventory terminal | No obvious unused static football-state family remained in the 41-column source | SEALED within that source |
| Low-event / score-compression | PR #142 R40D | fixed100 FAIL | Suppressed rather than recovered Draw calls | SEALED |
| Team-specific recent draw propensity | PR #140 R40B | fixed100 FAIL | Draw calls increased slightly but proper scores/F1 worse | SEALED |
| Centrality / balanced-team band | PR #144 R40F | large rolling diagnostic FAIL | Central rows had real but modest/heterogeneous Draw-rate lift, below gate | SEALED for generic central bands |
| Nonlinear Draw regimes | PR #136 R39X + PR #137 exact replication | discovery then replication FAIL | Discovery AUC gain did not replicate; replication baseline stronger | SEALED |
| Ordinal central Draw band | PR #138 R39Z / PR #139 R40A | classification-only FAIL | More Draw calls in one screen but probabilistic version failed | SEALED |
| Pairwise H-D / D-A / H-A factorization | PR #143 R40E | fixed100 FAIL | Did not improve Draw F1/accuracy | SEALED |
| Favourite-win -> Draw/upset factorization | PR #157 R42C | fixed200 FAIL | Draw AUC +0.0017 only; Top1 Draw remained ineffective; HDA LL worse | SEALED |
| Dynamic diagonal `D=0|T` dependence | PR #156 R42A | fixed200 FAIL | Conditional component mildly learnable, but downstream HDA/Draw worsened; Top1 Draw 0->0 | SEALED |
| Bivariate Conditional Poisson dependence | PR #173 R42O | rolling OOS FAIL | Small negative dependence phi exists, but joint/HDA/Draw proper scores worsened | SEALED for dependence-only shell |
| Counterfactual standings / mutual draw utility | PR #159 R42D + PR #160 replication | parent weak + exact replication FAIL | Parent uniformly favorable; exact replication reversed all main metrics | SEALED |
| Competition round | PR #78 | blind holdout FAIL | Round did not improve holdout Draw F1 and slightly worsened proper scores | SEALED |
| Aggregate lagged xG / performance | PR #120 R39H | validation FAIL | Context/fundamentals did not improve Draw LL/AUC | SEALED for aggregate xG summary variants |
| Static / retrospective lineup continuity and quality | PR #81, #121 R39I, #124 R39L, #125 R39M, #126 R39N, #127 R39O | mixed weak / policy or rolling FAIL | Some attacking-XI/value signals appeared in isolated windows but did not stay stable | SEALED for retrospective static variants |
| Authentic `observed_at` lineup/injury evidence | PR #128 R39P | DATA BLOCKED | No admissible historical sample with original pre-kickoff observation timestamps | GENUINE GAP |
| No-API official public-web forward capture | PR #130 R39R | TECHNICAL PASS ONLY | Auditable pre-kickoff page capture works; no predictive labels/model | DATA-BUILD CAPABILITY, not signal evidence |
| Confirmed-XI transition detector | PR #131 R39S | TECHNICAL PASS ONLY | Source-native confirmed-XI detector works; no 200+ predictive forward sample | GENUINE GAP pending forward accumulation |
| Goal-time / score-state historical summaries | PR #82 | Scientific FAIL | Existing pre-match score-state summaries insufficient | SEALED for aggregate summaries |
| True minute-level equalizer / lead-loss event sequences | PR #95 R31 | DATA BLOCKED | Repository lacked qualifying minute+event+team/score sequences | GENUINE GAP |
| HT->FT prior response features | PR #163 R42F + PR #164 exact replication | weak non-robust | Both disjoint fixed200s had favorable Direct-T LL/Brier/RPS point signs, but neither passed LL uncertainty gate; decision behavior unstable | NO third fixed200; retain only for large viewed rolling complementarity audit |
| Two-half generative Direct-T | PR #167 R42I | fixed200 FAIL | LL point estimate improved, but Brier/RPS and Draw-subset behavior failed | SEALED |
| Cross-domain prior shot/SOT/corner process | PR #162 R42E | COVERAGE BLOCKED | 0 fresh cross-domain eligible target rows after exclusions; no model fit/sample | UNANSWERED cross-domain, data-gated |
| Team red-card/foul history | PR #166 R42H | fixed200 FAIL | LL/Brier and top-k worsened; no useful Direct-T increment | SEALED |
| Team+referee discipline | PR #165 R42G | COVERAGE BLOCKED | 172 eligible fresh rows only, referee metadata essentially EPL/SCO | UNANSWERED at adequate multi-domain coverage |
| All-history pair features recovered from namespace overwrite | PR #168 R42J | fixed200 FAIL | LL/Brier/RPS point estimates mildly favorable, but bootstrap failed and Top1/Top2 fell | SEALED as standalone increment |
| Historical total-shape moments/features | PR #169 R42K | fixed200 FAIL | LL/RPS tiny favorable signs, Brier worse, confidence gate failed | SEALED |
| Team total histogram | PR #170 R42L | fixed200 FAIL | LL/Brier/RPS and Draw-subset total LL worsened | SEALED |
| HTFT + all-pair weak-signal fusion | PR #171 R42M | fixed200 FAIL | Fusion worsened LL/Brier/RPS despite small top-k diagnostic gains | SEALED |
| Formal V5.1 Direct-T historical core | PR #84 | WEAK HISTORICAL COMPONENT | Rolling OOS LL/RPS improve; Brier uncertainty crosses zero | retain research-only, not solved |
| Formal V5.1 `P(D|T,X)` historical component | PR #84 | STRONG CONDITIONAL COMPONENT | Proper-score metrics improve strongly conditional on realized T | retain research-only; cannot create T or joint matrix by itself |
| Exact 7+ tail confirmation | PR #84 | confirmation FAIL | Complexity did not solve tail information scarcity/cross-domain instability | SEALED on same viewed tail labels |

## Fixed-sample conservation finding

The later R41/R42 chain deliberately used disjoint identity-hash samples. Several apparent point-estimate improvements failed exact replication or bootstrap uncertainty. This is strong evidence against spending more fresh fixed200s on minor transformations of the same families.

Examples:

- R42D standings/task utility: parent improved all point metrics; exact replication reversed them. Route closed.
- R42F HT->FT: two independent fixed200s both showed small favorable Direct-T proper-score point signs, but neither passed the frozen LL uncertainty gate and top-k/Draw behavior was unstable. No third fixed200 authorized.
- R41D market HDA+AH+OU: parent favorable point estimates; exact replication did not establish robust increment.
- R42N OU2.5 Direct-T: no fixed200 consumed because fresh coverage was only 99.
- R42G referee and R42E cross-domain shot process: coverage gates stopped before sample/model fitting.

## What is actually left

### 1. Authentic synchronized timestamped multi-market trajectories — highest-priority data gap

Needed evidence is original pre-kickoff timestamps and synchronized Match Odds + AH + several OU lines at repeated freezes. Existing closing/reference odds cannot answer this. Betfair strict-PIT work is coverage/input blocked, not a predictive FAIL.

Do not substitute retrospective closing odds and call them independent PIT evidence.

### 2. Authentic forward lineup / injury / availability state — genuine data gap

Retrospective lineup engineering has been tried extensively and is unstable. The untested object is different: what was **actually known before kickoff**, with original/source observation timestamps. The no-API official Premier League capture and confirmed-XI detector technically work, but there is not yet an adequate labeled forward sample.

### 3. Minute-level event / shot-quality / substitution / VAR sequence history — genuine data gap

The repository audit found no qualifying minute-level historical event sequence for equalizer/lead-loss hazard work. Aggregate HT scores, final scores, shots/SOT/corners or aggregate xG are not equivalent. A real event-sequence family therefore remains untested.

### 4. Referee-conditioned discipline at adequate scale — narrow data gap

The referee experiment stopped at 172 fresh eligible rows and only two practical domains. Team-only red/foul history then failed. Referee-conditioned effects remain unanswered unless coverage becomes materially broader.

### 5. Direct-T OU2.5 increment — narrow coverage gap

R42N stopped at 99 fresh eligible rows after prior exclusions. The frozen 200-row gate must not be lowered. The question can only be reopened with new genuinely independent OU-eligible rows/domains, not recycled viewed identities.

## What should NOT be run next

Do not spend another fresh fixed200 on:

- another Draw threshold, class weight, manual Draw bonus, 1-1 boost, central band or ordinal width;
- another pairwise/favourite/nonwin factorization;
- another static closing-odds calibration or HDA/AH/OU transformation;
- another rolling recent-draw / venue / Elo / maturity feature;
- another total histogram/moment/window variant;
- another one-parameter Poisson/BCP dependence shell;
- another retrospective lineup transformation using the same static evidence;
- another HTFT variant selected after seeing the two R42F samples;
- another fusion of already-viewed weak signals followed immediately by fixed200 testing.

## Best next zero-new-sample action

Before any new data collection or fresh fixed200 use, perform a **viewed historical OOS complementarity / information-ceiling audit** across the strongest surviving weak signals.

The audit should not fit a new production challenger and should not claim confirmation. It should answer only:

1. Do the strongest weak signals make errors on the same matches or genuinely different matches?
2. Are their Draw-probability residuals highly correlated after conditioning on the baseline market/history probability?
3. Is there any meaningful oracle upper envelope when choosing the better existing frozen prediction per row?
4. Does complementarity persist across competition-season blocks, or is it isolated to one domain/window?
5. If the oracle gain itself is tiny, stop algorithm search on current data and wait for new information families.

Suggested included families, only where row-level historical OOS predictions are already available without opening fresh labels:

- baseline market/history Draw probability;
- strongest historical centrality/nonlinear ranking diagnostics;
- R42D standings/task utility parent and replication where identities permit diagnostic comparison;
- R42F HT->FT weak Direct-T signal;
- R42J all-history pair weak Direct-T signal;
- any existing rolling V5.1 Direct-T / conditional-D historical outputs with auditable identity alignment.

This is a diagnostic ceiling exercise on already-viewed evidence. It must not tune a new fixed formula and then reuse the same labels as confirmation.

## Final audit decision

**Current static-data algorithm search is near saturation.** The evidence does not justify another minor model/feature variant on a fresh fixed200.

The next rational branch is:

1. zero-new-sample complementarity / ceiling audit;
2. if meaningful complementary residual exists, preregister one genuinely new architecture before any fresh sample;
3. if oracle/complementarity is weak, stop model search and focus only on new admissible information: timestamped markets, forward confirmed XI/injuries, event sequences, or wider referee history.

This audit is descriptive research governance only and changes no formal football rule or model.
