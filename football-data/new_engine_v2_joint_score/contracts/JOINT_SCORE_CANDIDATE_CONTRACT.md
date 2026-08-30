# V2 Joint Score Candidate Contract

Status: FROZEN BEFORE IMPLEMENTATION

All candidates use the same fixture universe, cutoff, dynamic team state, split boundaries and label policy. No candidate receives extra target-time data.

## Candidate set
C1 `INDEPENDENT_POISSON_FROZEN`: independent Poisson score matrix from dynamic means; reference only.
C2 `DIXON_COLES_LOW_SCORE`: frozen DC low-score correction with finite constrained rho.
C3 `DIAGONAL_INFLATION_BIVARIATE`: learned finite diagonal dependence distributed across equal-score cells; no fixed draw or 1-1 multiplier.
C4 `DYNAMIC_NB_DIAGONAL`: hierarchical negative-binomial marginals plus learned diagonal dependence.
C5 `DYNAMIC_NB_MARCO`: negative-binomial marginal/conditional Mar-Co dependence based on the published marginal-conditional construction (Petretta, Schiavon, Diquigiovanni; arXiv:2103.07272), implemented as a normalized symmetric conditional mixture with a bounded learned dependence parameter.
C6 `DYNAMIC_NB_SARMANOV`: negative-binomial marginals joined by a Sarmanov family kernel with parameter bounds guaranteeing nonnegative support.
C7 `JOINT_PLUS_1X2_KL`: winning joint-score family plus an independently fitted pure-football 1X2 head; reconcile by minimum-KL projection under exact home/draw/away mass constraints.

## Candidate safety
Every raw matrix cell finite and >=0; finite truncation tail is accounted for and matrix renormalized. Parameters have explicit finite bounds. Invalid support or optimizer state is a hard failure. Candidate-specific parameters are fitted only on development/tuning folds.

## Natural draws and exact-score structure
Dependence parameters may be functions of prematch strength gap, expected total goals, defensive stability, competition prior, sample evidence, lawful lineup mixture and lawful regime state. No fixed draw multiplier and no fixed 1-1 multiplier. Evaluate draw binary quality and separate 0-0, 1-1, 2-2 calibration plus full exact-score log loss.

## Selection
C1-C7 compete on pre-final outer folds. Selection is lexicographic: primary multiclass LogLoss, guard Brier/RPS, then exact-score log loss and draw binary log loss, subject to calibration/coverage/worst-group gates. Complexity is retained only if its median fold gain is positive and at least 6/8 evaluable outer folds do not show material regression. C7 may use only the winning pre-final joint family.