# C072-N11 — Dynamic Multi-Line O/U Zero-Label Source Gate

Project: football3 only
Scientific root: C072-C (`research/c072c-xg-total-scalar-20260818`, root HEAD `e3e73c998020beef585cc459a69ea5b73b44ddb3`)
Immediate parent: C072-N10 static closing multi-line P(T) development, terminal PARK
Classification: source feasibility / governance only; zero-label; no scientific score

## Motivation
C072-N10 PARKed the static closing five-line representation. This successor does not tune that model on viewed labels. It tests whether a genuinely different pre-match information family can be acquired: timestamped multi-time O/U dynamics across several totals lines.

## Scientific question reserved for a later preregistered experiment
Does the time evolution and deformation of the O/U probability surface contain incremental match-level P(T) resolution beyond a strong O/U2.5 level + O/U2.5 movement baseline?

N11 itself MUST NOT read, materialize, infer, score, join, or inspect outcome labels.

## Required market structure
Preferred thresholds: O/U 0.5, 1.5, 2.5, 3.5, 4.5.
Required time information: timestamped pre-kickoff snapshots or a reconstructable event stream with immutable event timestamps.
Target frozen evaluation snapshots for source feasibility: T-24h, T-6h, T-1h, chosen by last observation at or before each cutoff. These cutoffs may not be moved after any target access.

## PIT rules
1. Every quote used at cutoff c must have source timestamp <= scheduled kickoff - c.
2. No closing or post-cutoff field may be copied into an earlier cutoff.
3. If kickoff corrections exist, the source's contemporaneous scheduled kickoff metadata must be preserved and audited.
4. In-play observations are forbidden.
5. Source-native backfill that overwrites historical states is forbidden unless immutable historical snapshots can be reconstructed independently.

## Global-consumption audit
Before promotion, search football3 and the quarantined football-project history for prior use of the same repository/provider, revision/snapshot, competitions, match identities, market fields, timestamps, and scientific hypothesis. Quarantined results may be read only to establish consumption metadata; they must not guide model/feature/hyperparameter selection.

If identical outcome labels or the same dynamic-O/U hypothesis were previously viewed anywhere, later scoring is REPLICATION / REPRODUCTION, never independent confirmation.

## Zero-label source PASS gates
A candidate source may PASS N11 only if all are true:
- exact provider and revision/export identity can be frozen and hashed;
- timestamp semantics are documented well enough to establish PIT;
- at least O/U2.5 exists at >=2 pre-match timepoints for a large development pool;
- preferred multi-line coverage (0.5/1.5/2.5/3.5/4.5) is measured without labels;
- kickoff and market identity fields support deterministic match identity;
- no score/result/goal-label field is decoded or materialized;
- no C070-F Confirmation target is opened;
- C073-C077 scientific conclusions remain quarantined;
- global-consumption classification is recorded before any future scoring contract.

## Source ranking rule
Rank candidates by scientific fit, PIT quality, reproducibility, multi-line x multi-time coverage, historical depth, and acquisition feasibility. Do not choose a weaker source merely because it is free if it cannot test the dynamic-surface hypothesis.

## Stopping rule
N11 PASS authorizes only a new preregistration for modeling. It does not authorize target access by itself.
N11 STOP if no candidate can establish PIT plus adequate dynamic O/U coverage. Do not fall back to post-view N10 feature/model shopping.
