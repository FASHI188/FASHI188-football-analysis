# Gold-Standard Evaluation Contract V1

Status: research-only. Formal weight: 0. This contract does not modify CURRENT, formal models, formal configuration, model weights, data registry, or promotion status.

## Purpose

Candidate football models must be evaluated on two distinct layers:

1. **Development OOS layer** — the existing time-ordered rolling out-of-sample pool. It is used for model design, parameter selection, ablation, stability analysis, and cross-competition diagnostics.
2. **Sealed gold-standard layer** — an independently curated, evidence-complete holdout. It is never used for training, threshold selection, feature selection, architecture selection, calibration fitting, or repeated exploratory tuning.

Development OOS and gold-standard results must never be pooled into one headline metric.

## Scope

- 90 minutes including stoppage time only.
- Extra time, penalties, advancement, and aggregate qualification are excluded.
- MLS remains excluded while its registered state is `INACTIVE_STALE_BOUND_ARTIFACT`.
- Every accepted record must be traceable to a pre-match freeze point and a separate 90-minute result verification.

## Gold-standard tiers

### GS-CORE

GS-CORE is the minimum sealed holdout for structural model evaluation. Every record must contain:

- explicit gold-standard candidate flag;
- stable match ID and competition ID;
- round or stage;
- home team and away team;
- exact kickoff timestamp with timezone;
- match status;
- venue or neutral-site status;
- exact pre-match freeze timestamp with timezone;
- explicit 90-minute settlement scope;
- explicit two-leg status and first-leg status;
- verified 90-minute final score, excluding extra time and penalties;
- source evidence for identity and result;
- input/context integrity hash;
- explicit audit that post-freeze information was excluded;
- freeze timestamp not later than kickoff.

GS-CORE may be used only once as a sealed final comparison after a candidate architecture and all thresholds have been frozen from the development OOS layer.

### GS-FULL

GS-FULL contains every GS-CORE requirement plus:

- complete timestamped 1X2 prices: home, draw, away;
- complete timestamped Asian handicap line and both prices;
- complete timestamped total-goals line and both prices;
- explicit synchronization window across the market snapshot;
- verified market-source provenance and independence status;
- pre-freeze lineup/availability assessment, explicitly labelled as official or predicted;
- lineup/availability source and evidence timestamp;
- all market and lineup evidence timestamps not later than the freeze timestamp.

GS-FULL is the final promotion-grade layer for market-aware or lineup-aware candidates. A missing price, missing timestamp, retrospective odds page, copied source, or post-freeze evidence causes fail-closed rejection.

## Sealing rules

Before the first candidate is evaluated, the gold-standard manifest must be sealed with:

- manifest version;
- creation timestamp;
- selection cutoff;
- exact record count;
- ordered match IDs;
- SHA-256 content digest;
- repository commit that created the manifest.

After sealing:

- records cannot be added, removed, or replaced for the current evaluation cycle;
- failed or inconvenient matches cannot be removed;
- any correction creates a new manifest version and invalidates direct comparison with the old version;
- repeated inspection does not convert the set back into a blind holdout.

## Model-research protocol

1. Use development OOS only for architecture and parameter decisions.
2. Freeze candidate code, features, parameters, thresholds, and calibration.
3. Produce a candidate artifact hash.
4. Run the sealed GS-CORE layer once.
5. Run GS-FULL once when the candidate uses market, lineup, availability, or task-state inputs.
6. Report complete 1X2 separately from selector accuracy and coverage.
7. Report at least Accuracy, LogLoss, Brier, RPS, confusion matrix, H/D/A Precision/Recall/F1, Balanced Accuracy, Macro-F1, draw calibration, and matrix audit residuals.
8. A selector must additionally report the full risk-coverage curve and per-class retention.
9. No gold-standard result may automatically alter formal assets or CURRENT.
10. Promotion requires Codex review and explicit user approval.

## Current repository status

No existing file is assumed to be gold standard merely because it is processed, validated, historical, or previously used in research. The G0 inventory must find an explicit candidate declaration and all required evidence. If none exists, the correct result is zero eligible records and `NO_SEALED_GOLD_STANDARD_SET`.
