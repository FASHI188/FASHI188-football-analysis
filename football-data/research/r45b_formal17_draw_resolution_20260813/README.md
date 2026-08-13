# R45B formal17 draw-resolution audit — 2026-08-13

Research-only evidence produced from the user-provided historical odds archive. Formal model, formal config, CURRENT and formal_weight are unchanged.

Scope: only the 17 project competition domains; historical development and rolling OOS diagnostics only.

Findings:
- Full 32-book / multi-timepoint trajectory residual model failed. Eligible formal-domain pool: 6,301 matches. Three pre-final contiguous 300-match OOS folds all worsened multiclass and draw LogLoss. The final 300 identities and predictions were frozen before opening their labels; that final block also worsened draw LogLoss and AUC.
- Locked final 300: market draw AUC 0.59118 vs trajectory challenger 0.53693; market draw LogLoss 0.60124 vs challenger 0.60951. Challenger Top-1 Draw: 11 selections, 3 hits.
- Fixed market selector no-vig pD >= 0.295 selected 56/300 in the locked final block and hit 21 draws (37.5%), versus overall draw rate 29.33%; preceding five contiguous 300-match blocks were unstable, so this is not a solved draw model.
- Domain calibration produced only a small aggregate Top-20% improvement (114/360 vs market 109/360) and was inconsistent.
- Long-history team/league priors failed: draw LogLoss improved 0/6 blocks and Top-20% hits fell from 110/360 to 95/360.
- Selecting historically stronger bookmakers also failed: draw LogLoss improved 1/6 blocks, AUC 1/6, Top-20% selection 0/6; aggregate hits 106/360 vs market 110/360.

Ruling: no challenger here is promotable. Formal weight remains 0. Do not retune these viewed blocks and call them independent OOS. R45B prospective forward track remains separate and unchanged.
