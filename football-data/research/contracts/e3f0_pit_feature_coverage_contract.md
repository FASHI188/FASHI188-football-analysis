# E3f-0 PIT Feature Coverage and Leakage Audit Contract

- Contract version: `E3F0-CONTRACT-1.0`
- Rule authority: `足球项目_CURRENT_唯一正式规则_V5.0.2_纯1X2隔离研究轨与联合门控边界维护版.docx`
- Research branch: `research/e3f0-pit-feature-coverage`
- Base research HEAD: `d82edfc5c275d40d64d803b425d4b579fbc698c2`
- formal_weight: `0`
- Automatic promotion: `false`
- Merge authorization: `false`

## Scope

E3f-0 audits only whether new pre-match PIT feature families are available, safely derivable, absent or leakage-prone for pure 90-minute H/D/A research.

It does not:

- fit a new candidate model;
- tune a threshold;
- use class weights;
- create exact-score, total-goal or BTTS outputs;
- modify the shared score matrix;
- modify formal models, data, configuration, CURRENT or formal weight;
- issue a promotion receipt.

## Fixed identities

- fixed full sample: `6,251` matches;
- fixed B100: `100` matches;
- Big Five only;
- no sample reselection;
- no outcome-based exclusion.

The existing Champion OOS chain may be replayed only to reconstruct exact frozen match identities. This replay is not a new E3f candidate-model fit.

## Feature families

The audit covers:

1. expected/confirmed lineup, injury, suspension and availability;
2. pre-match standings, points gaps and task state;
3. rest days, congestion, travel and rotation;
4. manager, formation, pressing, possession and tactical style;
5. xG and chance quality;
6. historical leading/drawing/trailing response;
7. referee availability as an optional auxiliary field.

## PIT and leakage rules

- same-date matches must be frozen before any same-date result update;
- current-match shots, shots on target, corners, cards, half-time score and full-time result are post-match fields and are forbidden as same-match inputs;
- those fields may be used only as prior-match history after completion;
- standings, rest and congestion may be derived only from prior dates;
- no current-match result, half-time state or post-match statistic may enter its own features;
- original source observation timestamps must be reported; absence of such timestamps prevents formal PIT readiness for externally sourced fields;
- repository keyword hits do not count as sample coverage without row-level joins.

## Status vocabulary

Each family must be assigned one of:

- `DERIVABLE_PIT_READY_FOR_FEATURE_PROTOTYPE`;
- `PARTIALLY_DERIVABLE_PIT`;
- `DERIVABLE_COARSE_PROXY`;
- `PROXY_ONLY`;
- `PARTIAL_OPTIONAL`;
- `ABSENT_FROM_FIXED_SAMPLE`;
- `ABSENT_XG_PROXY_AVAILABLE`;
- `LEAKAGE_RISK`;
- `UNAVAILABLE`.

## Required reporting

For full 6,251 and fixed B100, report:

- direct row-level coverage;
- safely derivable coverage;
- raw source join coverage;
- repository evidence paths;
- original timestamp availability;
- leakage risk;
- exact freeze rule;
- whether an external PIT source is required;
- model/data/config/CURRENT/formal-weight mutation counts.

## Stop condition

E3f-0 does not authorize model training.

After the audit:

- safely derivable features may proceed only to a separate feature-construction contract;
- proxy-only fields require a separate ablation contract;
- absent families require a source contract, timestamp contract and coverage plan before any model fit;
- no threshold or class weighting may be introduced.
