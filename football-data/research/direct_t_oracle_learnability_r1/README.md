# Direct-T Oracle Learnability R1

This research follows PR #178 only because that retrospective diagnostic found a clean same-fit ex-post Direct-T oracle gap. It does not reuse the diagnostic 1,000 rows for selector fitting or primary evaluation.

## Chronology

1. Fit the three frozen experts on earlier `train` rows only and predict the `policy` rows.
2. Train three fixed Ridge loss forecasters on policy expert-probability geometry and policy outcomes.
3. Refit the same three experts on `train+policy` using fixed `C=0.01`.
4. Reconstruct the 2,584-row common target pool.
5. Exclude all 1,000 identities used by PR #178.
6. Freeze selector choices on the remaining 1,584 rows from expert probabilities only.
7. Use those 1,584 labels only for final retrospective evaluation.

## Comparators

- best static expert selected by policy LogLoss;
- equal-weight average of the three experts;
- fixed Ridge loss-forecast selector.

The selector is considered learnable under this frozen method only if, on all 1,584 disjoint rows, paired bootstrap LogLoss delta versus the policy-selected static expert has 90% p95 below zero, while point Brier and RPS are non-worse and the selector genuinely uses at least two experts.

## Limits

This is still VIEWED retrospective evidence. It is post-selection motivated by the PR #178 oracle finding. It cannot claim an untouched holdout, scientific component PASS, confirmation PASS, formal promotion, or formal PIT validity. No new data, Provider, paid API, R44 capture, blind label, protected fixed sample, formal asset, CURRENT, main, or PR #176 is touched.
