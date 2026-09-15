# Research shortlist — valops model improvements

Baseline (reproduced 2026-09-15): walk-forward Brier train→2025 = 0.2203 (acc 64.3%, n=504),
train→2026 = 0.2320 (acc 63.1%, n=588). Coinflip 0.25. Known flaw: overconfident at extremes
(0.77 priced wins hit 0.62). Ranked by expected Brier gain per line of code.

## 1. Isotonic calibration on walk-forward outputs (do first)

What: after the logistic fit, fit sklearn IsotonicRegression (out_of_bounds='clip') mapping
the model's raw P(win) to empirical win rate, then apply it to every pairwise probability.
Where: one new function in pipeline/model.py, called at the end of fit_all / before pairwise.
Why: maximum-likelihood logistic regression is structurally overconfident; the bias scales as
Theta(d/n) and the sigmoid geometry pushes predictions toward extremes. Our flaw (0.77 priced
wins landing 0.62) is exactly this. Platt scaling cannot fix overconfidence (a sigmoid cannot
push probabilities back toward the middle), so isotonic regression is the standard fix; it
preserves ranking, so accuracy and AUROC are unchanged, and it only moves Brier.
Critical: fit the isotonic map ONLY on held-out walk-forward outputs (train-thru-2024 ->
predict 2025), never on the same data the logistic model trained on. With 504 + 588 test
samples we clear the ~500-sample minimum where isotonic stops overfitting.
Expected: -0.001 to -0.004 Brier, concentrated at the extremes. ~25 lines. Risk: low.
Gate: both test cuts must not worsen; pooled must improve.

## TESTED 2026-09-15: FAILED the gate, reverted.
- Test 2025 (n=504): calibrated 0.2252 vs uncalibrated 0.2203 (+0.0049 worse)
- Test 2026 (n=588): calibrated 0.2326 vs uncalibrated 0.2320 (+0.0006 worse)
- Pooled (n=1092): 0.2292 vs 0.2266 (+0.0026 worse)
Nested scheme (fit logistic thru Y-2, predict Y-1, fit isotonic, then fit thru Y-1 and
predict Y). 2025 calibration map fit on only ~436 rows transferred poorly. Reverted via
git checkout -- pipeline/, no commit. Possible retry variant: fit the isotonic map on
pooled out-of-fold predictions from cross-validation inside the training window instead
of the nested-year scheme - do not retry without that change.

## 2. Grid-search the Elo K table on walk-forward Brier

What: replace the hand-set constants (K_GROUP=20, K_MAIN=24, K_PLAYOFF=32, CHEM_PENALTY=50,
OFFSEASON_KEEP=0.7, provisional 1.5x) with values found by grid search minimizing
walk-forward Brier through the existing evaluate() cuts.
Where: new function in pipeline/model.py (or a standalone tune script) that calls
elo.run with overridden config values; the shipped config values live in pipeline/config.py.
Why: the config itself notes "fixed K table, tune only if London backtest Brier > 0.21".
Standard practice (SCOPE, AAAI esports Elo paper) is cross-validation over K, regression,
and carryover parameters with log-loss/Brier as the objective. Current values were set by
judgment, not by data.
Expected: -0.001 to -0.003. ~40 lines + compute (each grid point re-runs elo + train,
~5-10 min for a coarse grid of ~50 points). Risk: medium - two test years can be overfit;
mitigate by keeping the gate (neither cut worse than +0.002) and preferring small moves.

## 3. Per-player uncertainty weighting (Glicko-style) instead of the provisional 1.5x K

What: track a rating deviation per player. New players start wide, each map shrinks it,
long inactivity widens it. Scale each update by uncertainty so unknown players move fast
and established ones move slow - replacing the current PROV_MAPS 1.5x cliff at 10 maps.
Where: pipeline/elo.py, replacing the provisional branch in run(). Adds one dict and a
final write to player_elo.
Why: Glicko/Glicko-2 exist because fixed K overreacts for new players and underreacts for
established ones. The current 1.5x-until-10-maps is a step function; uncertainty weighting
is the smooth, standard version. Genuinely new mechanism - not on the rejected list.
Expected: -0.001 to -0.003. ~35 lines. Risk: medium - inactivity widening needs care for
players who change regions; gate decides.

## 4. Round-win roll-up with fixed side-strength fallbacks

What: the rejected roundmodel.py attempt died at 48% on a side-strength denominator bug -
its 120d window starves offseason matches. Retry with expanding fallback windows
(120d -> 365d -> career) in team_side_strength, gated at the map level (must beat 0.241).
Where: pipeline/roundmodel.py only.
Why: this is already the documented #1 open lever in HANDOFF.md. The mechanism is
genuinely new (the old failure was a data-starvation bug, not a theory failure), and
round-level data is 100k rounds - by far the richest signal in the database.
Expected: -0.002 to -0.006 if the bug was the whole problem; ~30 lines. Risk: medium -
round strengths are noisy; the map-level gate decides.

## Deliberately not proposed

- TrueSkill/Glicko-2 full rewrite: player-based TrueSkill is robust for 5v5, but published
  sensitivity analyses find defaults sit near optimum and gains over tuned Elo are small;
  a rewrite is hundreds of lines for ~0 gain. Proposal 3 takes the useful half (uncertainty)
  in 35 lines.
- Platt scaling: cannot fix our overconfidence direction; isotonic is the right tool.
- More features / GBM / comp matchup: all failed the gate already (see HANDOFF.md).
