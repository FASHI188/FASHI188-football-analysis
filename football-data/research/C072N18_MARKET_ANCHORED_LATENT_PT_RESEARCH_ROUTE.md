# C072-N18 — Market-Anchored Latent P(T) research route

## Status

- project: `football3`
- lineage parent: C072-N17 adjudication HEAD `86c46cbab95c13f90661a1719cae155b3a05f6d5`
- branch: `football3/c072n18-market-anchored-latent-pt-route-20260819`
- status: **RESEARCH ROUTE ONLY — NO TARGET ACCESS / NO MODEL SCORE**
- scientific root remains C072-C.
- C073-C077 and descendants remain scientifically quarantined.
- C070-F Confirmation1597 remains sealed.
- all previously sealed A-League reserves and N17 later-reserve labels remain sealed unless a future separately frozen contract explicitly authorizes them; they are not development material for N18.

This document fixes the next scientific direction. It is **not** a preregistered experiment and does not authorize reading any new target label. Exact model family, source revision, cutoff, features and validation pool must be frozen later, after a zero-label source and global-consumption audit.

## Why the route changes here

Football3 has now accumulated direct counterevidence against continuing to search for incremental P(T) resolution by repeatedly re-encoding the same closing market information:

- C072-C: real shot-level xG carried ranking information, but a single total-intensity scalar did not pass stability gates.
- N10: static five-line closing O/U surface did not establish stable proper-score increment.
- N12: sparse single-line dynamic path PARK.
- N13: genuine Betfair five-line × three-time raw surface PARK.
- N15W: zero-parameter five-tail structural reconstruction PARK.
- N17: closing 1X2 composition on top of five-line O/U PARK; proper scores worsened despite Top1/Top3 diagnostics improving.

Therefore the next route must not be another closing-odds feature transformation. The market should be treated as a **strong prior/anchor**, while new non-market prematch information is asked only to explain residual match-level deviations and uncertainty.

## Core scientific hypothesis

For a target match i, let the prematch total-goal market imply a market intensity / market distribution state `M_i`.

The next model should not independently predict total goals from scratch. Instead:

1. **market level / anchor** supplies the central total-goal expectation;
2. **historical chance-generation / shot-quality state** predicts a residual shift away from that market level;
3. **latent uncertainty state** predicts how dispersed the match-specific goal intensity is around its central level;
4. integrate over the latent intensity to obtain the complete match-level `P(T=0,1,2,3,4,5,6,7+)`.

Preferred conceptual form:

`log(mu_i) = log(mu_market_i) + delta_i`

where `delta_i` is learned only from strictly prior prematch information.

A latent-intensity distribution then supplies uncertainty, for example

`lambda_i ~ D(mean = mu_i, dispersion = kappa_i)`

and

`P(T_i=t | X_i, market_i) = integral P(T_i=t | lambda_i) p(lambda_i | X_i, market_i) d lambda_i`.

The exact distribution family (`Gamma-Poisson/Negative-Binomial`, lognormal-Poisson, or another frozen family) is **not selected in this route document**. It must be chosen and preregistered before any target access on the new development pool. No family shopping on viewed labels is allowed.

## What “market anchored” means

The market is not blended 50/50 with a second model and no post-hoc blend weight may be tuned on viewed OOS.

The intended role separation is:

- market: central total-goal level / prior;
- chance/xG model: residual correction `delta_i`;
- latent-state model: match-specific uncertainty / dispersion;
- final output: one coherent `P(T)`.

If the new information has no incremental signal, the candidate should naturally shrink toward the market-only baseline rather than force a departure.

## Preferred market anchor

The primary anchor should be a **single frozen PIT total-goal market state**, ideally de-vigged O/U2.5 at a preregistered cutoff available for every match.

Reasons:
- O/U2.5 has the strongest cross-source coverage;
- it avoids reintroducing the already-PARKed static five-line raw representation as the main novelty;
- the purpose of N18 is to test non-market residual information, not to retest multi-line market encoding.

If a future source offers high-quality dynamic market history, a stronger market-only baseline may include a preregistered movement state. That must be frozen before target access and must be identical for baseline and candidate.

## New information state: first-priority feature family

N18 should prioritize **strictly historical, shot-level chance-generation and shot-quality information**, not coarse box-score counts.

Candidate information categories, all computed only from matches strictly before the target fixture:

### Opportunity generation
- shot-count intensity for / against;
- frequency of matches with very low / very high shot creation;
- high-quality chance frequency;
- open-play versus set-piece chance share when source supports it.

### Shot quality distribution
- total xG for / against;
- xG per shot;
- variance / dispersion of shot xG values;
- high-xG tail mass or high-quality-chance rate;
- concentration of xG in a few large chances versus many low-quality chances;
- frequency of zero/near-zero chance-production matches.

### Time state
- exponentially or otherwise preregistered recency weighting;
- attack and defence states updated only from past matches;
- no future match statistics, no same-match events, no post-kickoff information.

### Player / availability layer — later, only if PIT provenance is provable
- expected available attacking/defensive player strength;
- prior minutes / prior xG contribution;
- lineup continuity / absence probability.

Actual final-lineup information is forbidden unless the prediction cutoff is explicitly after the official lineup publication and the publication timestamp is provably PIT.

## Minimal first scientific test

The first real N18 experiment should be deliberately narrower than a full complex generative system.

### B0 — strong market-only baseline
- same frozen PIT market anchor as candidate;
- same count-distribution family as candidate;
- no shot/xG residual state;
- train-only calibration parameters permitted only if explicitly frozen in the preregistration.

### C — market-anchored latent candidate
- identical market anchor;
- identical count-distribution family;
- strictly prior shot/xG chance-state predicts the residual mean shift `delta_i`;
- strictly prior chance-state may predict dispersion only if the exact dispersion equation is frozen before target access;
- candidate must be regularized/shrunk so that absence of residual signal returns toward B0.

The comparison must isolate **incremental information beyond the market**, rather than comparing unrelated model families.

## Validation doctrine

The real experiment may start only after a new data plan is frozen.

Required evaluation:
- strict chronological rolling OOS;
- no random split;
- primary: exact-T multiclass LogLoss;
- Brier and RPS mandatory;
- paired bootstrap CI;
- temporal-fold consistency;
- cross-league / cross-competition consistency when multiple competitions exist;
- baseline sanity and probability conservation;
- Top1/Top3 are diagnostic only and cannot override proper-score deterioration.

A future preregistration should require stable proper-score improvement, not merely AUC or Top1 improvement.

## Required source gate before any N18 scoring

A source is eligible only if all are established zero-label first:

1. exact repository/provider and immutable revision;
2. deterministic match identity;
3. strictly historical shot-level xG/chance information, or a different materially new prematch information axis;
4. enough chronological history before each target match to construct prior state;
5. a PIT total-goal market anchor at the same/frozen target cutoff, or a deterministic zero-label join to one;
6. no target/result field decoded during source audit;
7. global-consumption audit across GitHub + shared Airtable + both football projects;
8. development and confirmation pools separated before target access.

If the same source revision / matches / labels / materially same scientific hypothesis were already viewed anywhere, classify the new run as `REPLICATION / REPRODUCTION`, never fresh confirmation.

## Known source boundary as of 2026-08-19

The current StatsBomb Open Data revision `b0bc9f22...` is **not a new football3 evidence pool**. Football3 C072 already audited that revision and used its shot-level xG source structure. A newer-looking dataset claim cannot make those already-viewed observations fresh again.

Therefore N18 must either:
- find a genuinely different immutable event/xG source or genuinely new unconsumed match identities/revision with clean global-consumption boundaries; or
- explicitly run as reproduction and reserve a separate globally unconsumed confirmation source.

## Stopping / anti-method-shopping rules

Once the first N18 development labels are opened, do NOT on that same pool:
- change the latent distribution family;
- change market cutoff;
- change residual feature subset;
- change recency windows / half-life;
- toggle mean-only versus mean+dispersion after seeing scores;
- change regularization / priors to chase PASS;
- add BTTS/1X2/O/U surface encodings because the first result is weak;
- change league subset;
- tune a market/model blend weight;
- open confirmation data to rescue a failed development gate.

Any material change after viewing labels is a new post-view hypothesis requiring a new data plan.

## Route sequence

### N18-A — new-source discovery and global-consumption audit
Goal: locate a genuinely new PIT chance/xG or equivalent orthogonal information source and a compatible market anchor. Zero labels only.

### N18-B — immutable zero-label identity / PIT join
Goal: freeze source revision, match identities, target cutoff, market anchor and development/confirmation split. Zero labels only.

### N18-C — one-shot market-anchored latent P(T) development
Goal: compare B0 market-only versus C market-anchor + latent residual/uncertainty under one frozen model family and rolling OOS contract.

### N18-D — confirmation only if N18-C passes
Goal: one-shot score on a separately sealed, globally unconsumed pool. No modifications after N18-C.

## Success interpretation

A meaningful N18 success would be stronger than the prior market-feature experiments because it would establish that **non-market prematch chance state contains incremental match-level P(T) resolution after conditioning on the total-goal market**.

That is the scientific target.

If N18-C fails cleanly under a genuinely new, high-quality source, the evidence for a large remaining exploitable P(T) edge becomes substantially weaker and football3 should return to PARK until a materially new information source appears.
