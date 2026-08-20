# C072-N19R1 — closing-anchor movement replication1000

Project: **football3 only**.

## Why R1 exists

The original N19 scientific contract was committed at `e061ae1823ec934ed261c9e2f4e05e6c480ee84b` before any N19 target label read. Its fixed direct-download seven-division data plan could not be executed in the available transport environment and the proposed GitHub mirror did not contain that exact seven-file set. Therefore the initial N19 source plan is terminal `STOP_SOURCE_TRANSPORT` with **target/result values read = 0** and **model fit = 0**.

N19R1 is a new zero-label data plan frozen before any N19/N19R1 target value is accessed. The scientific question, market transforms, model family, folds, metrics, and stopping rule are unchanged. The only data-plan change is the pre-label fixed source inventory.

## Evidence classification

`REPLICATION / REPRODUCTION ONLY`; never fresh, pristine, blind, or independent confirmation. `formal_weight=0` regardless of outcome.

## Pinned source

Transport mirror: `MestreAlex/elo-rating` pinned commit:

`383d5277fdaed48fd2d909e073e047350e71cb7f`

The mirror's own updater states that these files are downloaded from Football-Data.co.uk 2025/26 URLs. Fixed files and observed Git blob SHAs, verified from headers before target access:

- `data/E0_2526.csv` — `0134017ec2cdec9db8e47e72eabbd74af068a276`
- `data/E1_2526.csv` — `47b6539b5da4b319e82701d7c5f9bb234f758223`
- `data/D1_2526.csv` — `9224c84b9f7574461abc48e1119a52704994517d`
- `data/D2_2526.csv` — `18b5a44ad2fc98b4be9f5884e57d2cdef082cdc0`
- `data/I1_2526.csv` — `05fde602b37217ddce1da60bb02fb351b246e659`
- `data/I2_2526.csv` — `e4ac8e20e65af7c06e04a065b6bc0aa526cbfcc3`
- `data/F1_2526.csv` — `54051914fc311277ab495ec7de950daabfad271b`
- `data/F2_2526.csv` — `776d5648f1f3c915e34423fcd2a85c5384e5d8cf`
- `data/SP1_2526.csv` — `279bd1eee9759f114e89ecc38e32af9ee7c9cdac`
- `data/SP2_2526.csv` — `e8258f7c88b1b756b0cc9e728c84f52a83ca4072`

All ten headers contain `Div,Date,HomeTeam,AwayTeam,FTHG,FTAG` and the four required market columns `Avg>2.5,Avg<2.5,AvgC>2.5,AvgC<2.5`. Header verification did not read any row-level target value.

## Zero-label identity lock

At the lock stage only these columns may be projected: `Div,Date,HomeTeam,AwayTeam,Avg>2.5,Avg<2.5,AvgC>2.5,AvgC<2.5`. `FTHG`, `FTAG`, `FTR`, all score fields, and all post-match fields are forbidden.

Selection is deterministic:
1. read the ten pinned files in the exact order listed above;
2. retain rows with non-empty identity, parseable date, and all four required average O/U2.5 odds finite and >1;
3. attach immutable source code and zero-based source row index;
4. sort by `(date_iso, source_code, home_team, away_team, source_row_index)`;
5. freeze the first exactly **1000** rows;
6. identity string = `source_code|date_iso|home_team|away_team|source_row_index`;
7. identity SHA256 = SHA256 of newline-joined ordered identity strings with final newline.

No target/result number may be projected until a follow-up immutable contract receipt records `selected_n=1000` and the observed identity SHA256.

## Frozen scientific question

After conditioning directly on closing O/U2.5, does opening→closing O/U2.5 movement retain incremental information for the full prematch total-goal distribution?

For stage `s`:
`q_over_s=(1/O_s)/((1/O_s)+(1/U_s))`.

`movement_logit=logit(q_over_close)-logit(q_over_open)`.

`mu_market` is the unique Poisson mean in `[0.05,8]` satisfying `P(T>=3|mu_market)=q_over_close`.

No 1X2, BTTS, AH, Pinnacle-specific field, multi-line ladder, xG-state, Draw/0-0/1-1 manual term, or post-result information is allowed.

## Frozen models

Evaluation bins `0,1,2,3,4,5,6,7+`, same full-support NB2 family for both:

- B0: `log(mu_i)=log(mu_market_i)+beta0`.
- C: `log(mu_i)=log(mu_market_i)+beta0+gamma*z_i`, with movement standardized using training fold only.

scipy L-BFGS-B NB2 NLL; alpha `[0.0001,3]`; beta0 unpenalized; fixed L2 lambda `1.0` on gamma only. No search or alternate family.

## Frozen chronological OOS

Exactly 1000 locked identities:
- train 1–400 / test 401–550
- train 1–550 / test 551–700
- train 1–700 / test 701–850
- train 1–850 / test 851–1000

Pooled OOS = 600.

## Metrics / gate

Primary LogLoss; secondary multiclass Brier and RPS; Top1/Top3 diagnostic. Paired bootstrap 5000, seed `72019`, 90% CI.

Replication PASS requires all:
- pooled dLL < 0;
- bootstrap90 upper < 0;
- dBrier <= 0;
- dRPS <= 0;
- >=3/4 chronological fold LL wins;
- >=6/10 source-code LL wins among source codes represented in pooled OOS;
- valid normalized probabilities.

Strong replication screen: PASS + dLL <= -0.005 + dRPS <= -0.0005 + 4/4 fold wins.

## Hard stopping / boundaries

Once N19R1 labels are opened, no movement transform, lambda, folds, file set, row ordering, NB2 family, alpha bound, PASS gate, or subset may be changed on these 1000 labels.

C070-F Confirmation1597, N17 reserve266, N18 confirmation150 and all other football3 sealed pools remain unopened. C073-C077 scientific results are not used as design/tuning/stopping evidence.
