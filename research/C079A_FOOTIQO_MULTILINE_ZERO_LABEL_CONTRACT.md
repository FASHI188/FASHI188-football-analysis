# C079-A Frozen Contract — Footiqo Multi-Total-Market Zero-Label Source Gate

Status: research-only / `formal_weight=0` / zero-label source audit. Frozen before execution.

## Scientific purpose
C078-A/B showed that changing full-support distribution family on the viewed development domain did not solve the 7+ calibration problem. C078-E2/E4 then showed that a single O/U2.5 closing line plus one global thin-tail gamma is not stable enough. C079 changes the information set rather than shopping another tail family.

The only question in C079-A is whether an external historical source provides enough same-match closing O/U 2.5, 3.5 and 4.5 market information, with coherent nested probabilities and sufficient volume, to justify a later preregistered multi-market full-support development experiment.

## Fixed source and domains
Source: Footiqo historical league database, whose Odds section states closing odds are sourced from 1xBet.

Exactly five frozen URLs:
1. https://footiqo.com/database/leagues/argentina-liga-profesional/
2. https://footiqo.com/database/leagues/brazil-serie-a/
3. https://footiqo.com/database/leagues/australia-a-league/
4. https://footiqo.com/database/leagues/saudi-professional-league/
5. https://footiqo.com/database/leagues/usa-mls/

No replacement league after seeing the gate result.

## Hard label boundary
C079-A must not read, parse, hash, store or score FTHG/FTAG/FTR or any result-derived quantity. The HTTP parser streams and discards all page content before the `Historical Odds` section marker; only the post-marker Odds section may be parsed. Durable output columns are restricted to identity/season plus O25,U25,O35,U35,O45,U45.

No total-goals target, no T>=7 membership, no model fit, no metric against realized outcomes.

## Frozen market transforms
For each over/under pair at threshold K in {2.5,3.5,4.5}, de-vig by multiplicative normalization:
`p_over_K = (1/O_K) / ((1/O_K)+(1/U_K))`.

Nested market coherence is checked as:
`p_over_2.5 >= p_over_3.5 >= p_over_4.5`, tolerance 1e-9.

## PASS gates
All must pass:
1. all five frozen URLs HTTP-success and expose an Odds table containing O25,U25,O35,U35,O45,U45;
2. total unique Odds identities >= 3,000;
3. at least four of five domains each contribute >= 300 unique Odds identities;
4. duplicate identity count = 0 after domain-prefixed identity construction;
5. joint numeric/valid decimal-odds coverage for all six O/U fields >= 85%;
6. among rows with all six valid prices, nested de-vig coherence rate >= 98%;
7. at least five distinct season labels across the pooled source;
8. result/score fields materialized = 0;
9. goal totals / tail membership / model fit = 0.

PASS terminal: `PASS_MULTILINE_ZERO_LABEL_SOURCE`.
Otherwise: `STOP_MULTILINE_SOURCE`.

## If PASS
A later named C079-B contract may open only a preregistered development subset of result labels and compare one fixed multi-market constrained full-support estimator against the single-line O/U2.5 baseline. C079-A itself authorizes no label opening.

## Boundaries
- C078-D late 2,119 confirmation identities remain sealed.
- C077-B 6,943 consumed labels remain quarantined from model selection.
- C071 reserve 52,180, C070-F 1,597, A05/protected remain unopened.
- CURRENT/main/formal_weight/unified matrix unchanged.
