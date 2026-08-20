# C079-B P1000 Market-Implied COM-Poisson Development Contract

Status: preregistered one-shot DEVELOPMENT pilot; formal_weight=0.

Prerequisite immutable source receipt:
- C079-P1000R1 status: PASS_PILOT1000R1_SOURCE
- selected market-only rows: exactly 1,000
- identity SHA256: `ce2af86f206077255ea489242a3e8473e34b89f140cc9528f2ad9594593c3413`
- 25 source domains
- six-price O/U2.5/3.5/4.5 completeness: 100%
- nested de-vig coherence: 100%
- result labels read before this contract: 0

## Question
Does same-match closing O/U2.5, 3.5 and 4.5 surface contain enough shape information to improve a full-support total-goal distribution, especially the 7+/8+ region, versus a strong single-line O/U2.5 Poisson market anchor?

## Frozen baseline B0
For each match:
1. de-vig O/U2.5 to obtain q3 = P(T>=3);
2. solve unique Poisson mean mu0 such that Poisson(mu0).sf(2) = q3;
3. use the resulting full-support Poisson distribution.
No result-fitted parameters.

## Frozen candidate C
Conway-Maxwell-Poisson (COM-Poisson), independently fit per match from market probabilities only:
- q3=P(T>=3), q4=P(T>=4), q5=P(T>=5), each de-vigged from O/U2.5/3.5/4.5;
- pmf p(k) proportional to lambda^k/(k!)^nu, k>=0;
- Poisson is nested at nu=1;
- fixed bounds: lambda in [0.1,8.0], nu in [0.6,3.0];
- fixed support calculation k=0..120 with residual-tail audit;
- objective: equal-weight squared error of logits of model tails (>=3,>=4,>=5) versus market q3/q4/q5;
- optimizer: L-BFGS-B initialized at (lambda=mu0, nu=1), one start only;
- no result-based fit, no hyperparameter/model/family search.

If this candidate fails, no alternate count family, bounds, objective weights or market-line subset may be tried on these 1,000 labels.

## Target opening boundary
- Rebuild the exact market-only 1,000 pack and assert the frozen identity SHA before opening outcomes.
- Result access is restricted to those 1,000 frozen identities only.
- For nonselected rows, result cells must not be numerically decoded/materialized.
- Require 1,000/1,000 FTHG+FTAG joins; otherwise STOP_COVERAGE before scientific scoring.
- No C078-D late2119, C076-D, C071 reserve52180, C070-F1597, A05/protected or other sealed pools may be opened.

## Frozen metrics
Primary:
- exact realized-T LogLoss over full-support pmf;
- paired bootstrap 3,000, seed 79001, 90% CI for dLogLoss=C-B0.

Mandatory supporting:
- formal collapsed 0,1,2,3,4,5,6,7+ LogLoss, Brier, RPS;
- Brier and mean calibration for events T>=7 and T>=8;
- exact conditional-tail LogLoss on realized T>=7, reported when n>=15;
- per-domain exact-T LogLoss win count;
- optimizer success, bound-hit and residual-tail audits;
- probability conservation.

## Pilot signal gate
`PILOT_SIGNAL` requires simultaneously:
1. exact-T dLogLoss < 0;
2. bootstrap 90% upper bound < 0;
3. collapsed-8-bin Brier and RPS nonworse;
4. T>=7 and T>=8 Brier nonworse;
5. candidate exact-T LogLoss better in at least 13 of 25 domains;
6. optimizer success >=99%, probability/residual audits pass.

Anything else is `PILOT_NO_SIGNAL` (or engineering/coverage STOP). Conditional exact-tail metrics are mandatory diagnostics but, due the expected small T>=7 count in only 1,000 matches, are not allowed to override the frozen gate.

## Governance
This is DEVELOPMENT only. Even PILOT_SIGNAL does not promote a model, does not satisfy the unchanged formal >=3,000 C079 source gate, does not open exact-score formal use, and does not change CURRENT V5.2. Exact-tail/unified matrix remain formally blocked pending larger independent confirmation and CURRENT promotion.
