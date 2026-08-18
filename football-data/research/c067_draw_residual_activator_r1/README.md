# C067 Draw Residual Activator R1

C067 is a **post-view research-development** mechanism built on the already-viewed R6 rolling OOS rows. It is not a scientific pass, confirmation pass, or formal model.

## Goal

Activate **natural H/D/A argmax Draw Top-1** without forced Draw labels, manual Top-1 overrides, a 1-1 bonus, class weighting, or an arbitrary additive pDraw reward.

## Mechanism

- Baseline: R6 candidate H/D/A probabilities.
- Fit a binary Draw-vs-nonDraw residual model using only globally earlier viewed R6 OOS rows.
- Fixed features combine R6 H/D/A geometry and the R6 even-total GD=0 component signals.
- On a fixed eligibility region only, blend residual `qDraw` into R6 `pDraw`.
- Preserve the R6 home:away odds ratio exactly while reallocating non-Draw mass.
- Final H/D/A call is the ordinary probability argmax.

Frozen R1 development parameters:

- `LogisticRegression(C=0.3)`, no class weight;
- blend weight `0.4`;
- eligible only when R6 `pDraw >= 0.20`, `abs(pHome-pAway) <= 0.20`, and `qDraw-pDraw >= 0.02`.

## Time discipline

For the position-3 evaluation, fitting uses only position-2 rows whose `date_key` is strictly earlier than the earliest position-3 evaluation date. For position-4, fitting may use position-2/3 rows only when their date is strictly earlier than the earliest position-4 evaluation date. Evaluation-fold labels are never used for that fold's fit.

## Claim boundary

The parameters above were selected after R6 labels were already viewed. Therefore:

- this experiment may establish only a reproducible **post-view development mechanism**;
- it cannot be labeled `SCIENTIFIC_COMPONENT_PASS`, `CONFIRMATION_PASS`, or `FORMAL_PROMOTION_PASS`;
- `formal_weight=0`;
- B05/B07 and any future unseen confirmation reserve remain unopened by this experiment;
- any future scientific claim must freeze the exact mechanism before a genuinely unseen evaluation.

This separation is intentional: first prove that natural Draw Top-1 activation is mechanically possible without damaging proper scores, then use new orthogonal information / unseen data for scientific confirmation.
