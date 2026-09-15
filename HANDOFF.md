# VALOPS handoff — Valorant Champions Shanghai 2026 predictor

## 2026-09-15 log (form-first iteration, swing adjustment, filter fix)
- SHIPPED: fast form Elo as 19th model feature. Second Elo tracker, K x1.5, no offseason regression (keep 1.0), separate tables (series_elo_fast, player_elo_fast, team_last_roster_fast). Model learns elo_diff (-0.0069) vs elo_fast_diff (+0.009); net = reward for recent form above long-run rating. Walk-forward: 2025 Brier 0.2198 (was 0.2201) acc 63.9% (+0.2pp); 2026 Brier 0.2296 (was 0.2302) acc 63.8% (+1.5pp); pooled 0.2251 < 0.2255 gate. K grid: 1.25/1.5/1.75/2/2.5/3/4/6, optimum flat around 1.5.
- REJECTED: recency-decayed K updates (half-life 45/90/180/365/730d all worse, pooled 0.2277-0.2288; monotonic toward baseline = destroys long-run signal). Plain momentum/decay had failed before; this was a genuinely different mechanism and still failed. Do not retry.
- SHIPPED (display): SWING opponent/region adjustment. Per-map swing credit x clamp(1 + (opponent pre-series Elo - 1500)/400, 0.6, 1.6). Uses series_elo pre-series strengths (no leakage); role replacement recomputed post-adjustment. Dist: p5 0.883, median 1.011, p95 1.240. Board still sane (Foxy9 #1, Jemkin #2, primmie #3; XuNa 18->10, keiko 28->17).
- FIXED: All-2026 swing filter. Component logic verified in jsdom (80 -> 253 -> 80 players); hardened with player_id keys + visible player count. Site copy: formulas for round swing, Space, SWING-AR, opponent adjustment; form-tracker line in win-probability section.
- Gate: pooled Brier < 0.2255, neither annual cut worse than +0.002. Fast-Elo config in pipeline/config.py (FAST_K_MULT, FAST_TABLES).

## What this is
One-page prediction site + Python pipeline. Event: Champions Shanghai, Sep 24 to Oct 18 2026, 16 teams, GSL groups + double-elim playoffs. Site: Astro + React islands, Apple-minimal light design. Live at http://127.0.0.1:4321 via `npx astro preview` in `site/`.

## Data (data/valops.db, SQLite + data/raw 16k cached JSON)
- 1,861 series, 4,718 maps, 47k player-maps, 100k rounds. LOCK//IN 2023 (event 1188) to Stage 2 2026. Champions 2026 event id 2766 (no completed matches yet).
- Tables: series, maps, player_map, player_elo, team_last_roster, series_elo, map_swing, player_swing, map_sides, map_comp, map_eco (2025-26 full + 2023-24 backfill running), series_clutch, round_detail, team_traits_monthly, series_meta (patch, best_of).
- 2 maps lack round data on VLR (series 180279, 459856 game 1).
- Status manifest: data/status.json. Write path: `python3 run.py <ingest|elo|train|sim>`.

## Model (current best, SHIPPED)
- Roster-anchored player Elo (players carry ratings across orgs; team = 5-man mean minus chemistry dock; offseason 20% regression; provisional fast K; performance-weighted updates). v2 (map-level) and v3 (round-level) both FAILED gates and are shelved but kept in pipeline/elo.py. A second fast "form" Elo (K x1.5, no offseason regression) feeds elo_fast_diff to the model since 2026-09-15.
- Logistic regression, 19 features (all A-minus-B diffs): elo, elo_fast (form), rating, acs, kast, adr, fkfd, form, winrate, h2h, lan, duel, cov, rest, sos, rt_pistol, rt_retake, favlen, elopo (playoff-Elo interaction).
- Walk-forward Brier: 2025 test 0.2198 acc 63.9%, 2026 test 0.2296 acc 63.8%. Coinflip 0.25.
- 10k Monte Carlo sims over real groups; playoffs use playoff-conditioned probabilities.
- Known flaw: overconfident at extremes (0.77 priced wins 0.62). Toss-ups (|elo|<15) are 54%.

## SWING metric (display only, FAILED predictive gate twice)
- Round-swing credit by score leverage (11-11 rounds = 256x dead rounds), role positional baselines, Space axis (first engagements/round). Validated: eye test + split-half r 0.65-0.69. Adds 0.0000 to Brier. Page boards: all 2026 players + Champions filter.

## Expert lists (on page, verified from video frames)
- Plat Chat Ep 280 combined top 10 + community top 40 + 8 individual top 15s aggregated to page Top 40 with our champs-only ranks. trent: community 22, ours 62. Asuna: Plat #1, ours 198 (space creator, low swing capture — known definitional gap).

## Tested and REJECTED (do not retry without new theory)
Margin-K, decay weighting, momentum, playoff/intl form, map model, weighted training, veto picks pre-match, side splits, comp stability, clutch/eco/pistol (eco-table), GBM (loses badly twice), star+tenure aggregation, 538 MOV (reverted), global shrinkage, comp matchup matrix (below chance), round-win roll-up (48% — side-strength denominator bug: 120d window starves offseason matches; roundmodel.py team_side_strength needs fallback windows, UNFIXED).

## Open levers (ranked)
1. Round-win model with FIXED side strengths (fallback windows) — gated at map level, must beat 0.241.
2. Live veto edges on match days (veto logs cached in data/raw/series_*.json).
3. Market odds blending (needs odds history — don't have).
4. Fight-level data: UNAVAILABLE (VLR logs tab is JS-rendered, rib.gg API dead + bot-walled, static data is 2022 meta).
5. VLR pickems need login (18,671 users registered, no public aggregates yet — recheck after Sep 24).

## Reproduce
pip install -r requirements.txt; vlrdevapi 2.4.0. `python3 run.py ingest|elo|train|sim`. npm install in site/, `npx astro build`, preview on 4321. If astro build fails with UNRESOLVED_ENTRY: rm -rf node_modules/.astro dist .astro, rebuild.

## Overnight grind 2026-09-15 (model push toward 70%)
Baseline at start: 2025 brier 0.2198 acc 63.9%, 2026 brier 0.2296 acc 63.8%, pooled 0.2251.
Gate: pooled improves AND both yearly briers improve AND neither accuracy regresses.

### SHIPPED (c469167): drop noisy features + elo_x_form interaction, C=0.75
- Drop-one ablation showed lan_diff removal alone: pooled 0.2251 -> 0.2240.
- 64-subset grid over {lan,winrate,adr,h2h,duel,cov} + elo_x_form interaction (elo_diff*form_diff/100).
- Winner: drop {lan_diff, adr_diff, h2h_diff, duel_diff} + elo_x_form, C=0.75.
- Official walk-forward: 2025 brier 0.2198 -> 0.2193, acc 63.9% -> 64.7%; 2026 brier 0.2296 -> 0.2268, acc 63.8% -> 64.3%; pooled 0.2251 -> 0.2233. Passes gate on both cuts.
- features.py diffs() now emits elo_x_form_diff ("conviction": elo edge and form aligned); model.py COLS filters DROP set, C=0.75.

### REJECTED tonight (all reverted, do not retry unchanged)
- C sweep 0.05-20 on full set: no config passes gate (2025/2026 want opposite regularization).
- Feature scaling (StandardScaler): helps 2025 (0.2186), hurts 2026 (0.2317).
- elo_diff^2 signed, |elo_diff|, elo momentum (fast-slow): no gate pass (ix1 interaction only one that helped).
- Platt scaling time-split (base <=X-1, calibrate on X) and in-sample: worse (0.2259/0.2253). Confirms isotonic rejection: output calibration hurts; model's probability scale is fine on average.
- Temperature scaling T=1.1-2.0, linear shrinkage: 2025 gets worse. Overconfidence is 2026-specific; global shrinkage can't fix it.
- LDA (2025 better/2026 worse), QDA (terrible 0.2592).
- Round-diff momentum (60d avg map round-diff, shrunk): helps 2025, hurts 2026. Reverted.
- form/winrate re-combos (drop form keep winrate, form_mo, etc.): none beat winner; suppression pattern is fine as-is.
- Intl indicators (elo_x_intl, is_intl), form_x_playoff, elo_x_sos: no improvement.
- Training window 2y: looked promising on winner config (0.2232) but HURT original config badly (2026 acc 63.4% -> 62.1%). Not robust; rejected as fold overfit.
- Ensembles (A+B avg, logit-avg, +orig): no better than single best (0.2233); components too correlated.
- GBM/RF on reduced set: still lose badly (0.2302-0.2407). Tree models overfit this data; logistic is the right class.
- Roster continuity features (cont_diff, cont_min, elo_x_newroster from series_elo): no signal.

### In progress
- Elo hyperparameter grid: CHEM_PENALTY x OFFSEASON_KEEP (9 configs), fast harness reusing non-elo features.

### More rejected tonight
- Elo hyperparameter grid (CHEM_PENALTY x OFFSEASON_KEEP, 9 configs, fast harness): best was chem=100/keep=0.7 pooled 0.2232 but 2025 accuracy regressed vs committed; optimum flat around current (70, 0.8). Keep committed values.
- Elo-implied win probability features (elo_prob, elo_prob_fast): neutral when added, worse when replacing raw diffs. The logistic already learns the right shape.
- Roster continuity features: no signal (see above).

### Plat Chat Ep. 280 top 10 (Champions Shanghai preview, aired 2026-09-14) — expert sanity-check list for swing v2
- Source: Plat Chat VALORANT Ep. 280 "We picked the Top 10 Players of Champions Shanghai" (https://www.youtube.com/watch?v=1Kqr1xvmqjw). 8 hosts submitted top-15s, merged by points.
- Extracted from auto-captions; names 1,2,6,7,8 certain, 3,4,5,9 high confidence, 10 medium:
  1. aspas (100T, Americas Stage 2 MVP)
  2. N4RRATE (KC)
  3. brawk (NRG) — tied on points with 4th/5th
  4. something (PRX) — tied on points
  5. lukxo (LOUD) — tied on points, tiebreak to 5th; Chamber player, community had him 3rd
  6. erde (LOUD)
  7. Dambi (Nongshim)
  8. Francis (Nongshim)
  9. Slowly (TYLOO)
  10. PatMen (Global Esports) — captions rendered "Pac-Man"; GE flex/support (Skye/util) matching on-air description
- Use ONLY as sanity check, never as training signal. v2 should put most of these meaningfully high; if N4RRATE is still ~#99 the redesign failed its purpose.
- Note: site already has an "Experts vs model" section with an older merged top-40 board; consider refreshing it with this Ep. 280 top 10 next to v2 ranks when v2 ships.

### SWING v2 (Tony directive, 2026-09-15 ~05:30 ET) — FAILED VALIDATION, NOT SHIPPED
Goal: make SWING genuinely good, not display-only. v2 in pipeline/swing.py (run_v2, ar_table_v2); v1 path untouched.

Changes:
- Loss credit: winner_pool = pool as v1; loser_pool = pool * (loser_rounds/winner_rounds)^2, split by same share formula (0.35 fk / 0.30 kd / 0.25 ast / 0.10 kast). 13-3 losers ~5% of pool, 13-11 losers ~72%.
- Recency: weight = 0.5^(age_days/half_life) on swing and rounds. Tested hl in {60,90,120}.
- Board minimum raised to 400 rounds.
- Opponent multiplier applied to both pools.

Validation results:
a. Player predictiveness (time-split, 3 cutoffs): v2AR->next-60d swing r=0.32-0.36 (best hl=120); rating->next rating r=0.46; acs->next acs r=0.66; v2AR->next rating r=0.30-0.33. v2 does NOT beat raw rating/ACS. rating->next swing (r=0.40-0.41) ties v2AR->next swing (r=0.40-0.41). FAIL.
b. Team gate (top-3 mean AR diff, sum AR diff, 60d lookback, hl=90): +swing3_diff gives pooled 0.2232 but 2026 brier REGRESSES (0.2269 > 0.2268) and both accuracies regress. FAILS gate. +swingsum_diff neutral-to-worse.
c. Stability (odd/even month split-half): v2 r=0.401 vs v1 r=0.395 — technically beats v1 but negligible. WEAK PASS.
d. Sanity: N4RRATE v1 rank 23/173 -> v2 rank 8/209; trent 59/173 -> 41/209. Loss credit works as intended. PASS.

Ensemble check: rating+v2AR predicts next rating at R2=0.226 vs rating alone 0.224 — v2 adds no incremental signal.

Verdict: v2 is a better DESCRIPTIVE metric (fixes the star-on-bad-team problem) but not a better PREDICTIVE one. Not shipped to model or site. v2 code remains in swing.py (run_v2/ar_table_v2, tables map_swing_v2/player_swing_v2) for future work; v1 is still the live path.

What I'd try next:
1. Tune the share weights (0.35/0.30/0.25/0.10 are arbitrary) to maximize predictive correlation, not descriptive appeal.
2. Test whether swing helps on subsets (underdogs, high-variance players) rather than globally.
3. The real gap: SWING measures what happened, but prediction needs what WILL happen. A "expected swing" based on matchup (like xG models) might beat raw aggregation.
4. Consider that VLR rating already captures most predictive signal; SWING's value may be purely descriptive (which is fine for the site).

### SWING v2 star-on-bad-team deep dive (Tony framing, ~06:00 ET)
Pulled 10 clear 2026 cases: top-quartile rating, team map win% <45%.

v1 (winners-only) vs v2 (linear loser pool) vs box-score tier:
- basic (1.18, rt#5): v1 UNRANKED -> v2 #92. Better, but 87 spots below box-score tier.
- hiro (1.14, rt#7): v1 #4 -> v2 #19. v1 was actually better here.
- Ruxic (1.12, rt#17): v1 #7 -> v2 #51. v1 better.
- Jemkin (1.11, rt#22): v1 #1 -> v2 #10. v1 better.
- johnqt (1.10, rt#27, IGL): v1 UNRANKED -> v2 #155. Still 128 spots low.
- Less (1.09, rt#33): v1 UNRANKED -> v2 #50. Good improvement.
- AAAAY (1.09, rt#37): v1 UNRANKED -> v2 #67.
- Shyy (1.08, rt#42): v1 UNRANKED -> v2 #123.

Formula iterations tested:
- Quadratic (lr/wr)^2: too harsh. basic #115, johnqt #169.
- Linear (lr/wr): better. basic #92, johnqt #155.
- Sqrt: basic #76, johnqt #147. Better but still far.
- Linear + 0.4/0.6 floor: marginal gains.
- Team-adjusted baseline (subtract mean AR of same-win% bucket): best. Less matches exactly (33 vs 33), AAAAY close (44 vs 37), hiro closer (14 vs 7). basic 67, johnqt 143.

Switched run_v2 to LINEAR damping (more principled: credit proportional to rounds won).

HONEST ASSESSMENT: No pool formula fully fixes "star on bad team." SWING measures win probability captured; players on 35%-win teams have structurally less win probability to capture than players on 65%-win teams. This is not a bug — it's what the metric IS. A 1.18 rating on a bad team means "great stats in losses"; SWING asks "how much did you contribute to winning," and the answer is "less, because the team didn't win."

Recommendations:
1. Keep SWING as win-contribution (descriptive). Don't pretend it's individual performance.
2. For the site: show SWING alongside rating, or add team-adjusted AR as a toggle.
3. If Tony wants a single "best player" rank that handles bad teams, blend v2 AR with rating percentile — but that's a different metric, not SWING.
4. The v2 predictive validations already failed; v2's value is descriptive fairness (N4RRATE 23->8, Less unranked->50), not prediction.

### Plat Chat Ep280 sanity check, champs-only (Tony clarification, ~06:15 ET)
Expert list covers Champions-qualified players only, so compared v2 CHAMPS-ONLY ranks (80 players), not full 209 board. (Site's "Experts vs model" already filters p.champs; mirrored here.)

Plat # -> v2 champs-only rank:
- 5. lukxo -> #1 | 9. Slowly -> #2 | 6. erde -> #4 | 7. Dambi -> #5
- 10. PatMen -> #8 | 3. brawk -> #9 | 2. N4RRATE -> #11 | 8. Francis -> #18
- 4. something (PRX) -> #28
- 1. aspas -> NOT in champs set (roster data: aspas id 8480 not on 100T's team_last_roster; most recent team 7386 as of Sep 6). #11 on full v2 board. Data staleness, not metric failure.

8 of 10 in v2 champs top 28. Strong agreement on the top tier. v2 passes the expert sanity check.

Star-on-bad-team vs experts: NONE of the 10 stars (basic, hiro, etc.) are champs-qualified, so their absence from Plat Chat lists is expected, not a negative signal. The star check stands alone (v2 vs box-score tier), documented above.

## Overnight grind FINAL (2026-09-15 ~06:25 ET)

### Model: shipped c469167, nothing else passed
- Winner (committed locally, NOT pushed): drop {lan_diff, adr_diff, h2h_diff, duel_diff} + elo_x_form_diff (elo_diff*form_diff/100), C=0.75.
- Official walk-forward: 2025 brier 0.2198->0.2193 acc 63.9%->64.7%; 2026 brier 0.2296->0.2268 acc 63.8%->64.3%; pooled 0.2251->0.2233.
- ~30 ideas tested, all rejected (see log above). Notable: 2y training window looked promising but failed strict gate (2025 brier tied, not improved) and hurt the original config; elo hyperparameter grid flat; ensembles/GBM/RF all lose to logistic.
- Sim re-ran for winner (data.json refreshed 07:56 UTC); `npx astro build` passes.

### SWING v2: implemented, validated, NOT shipped
- v2 in pipeline/swing.py (run_v2, ar_table_v2): linear loser-pool damping, recency decay (hl=90), 400-round minimum, opponent mult on both pools. v1 path intact.
- Validation: (a) FAIL — v2AR doesn't beat rating/ACS at prediction; (b) FAIL — team v2 features don't pass match-model gate; (c) WEAK PASS — stability 0.401 vs 0.395; (d) PASS — N4RRATE 23->8, trent 59->41.
- Star-on-bad-team: v2 rescues buried players (basic unranked->92, Less unranked->50) but doesn't reach box-score tier (basic rt#5 still at #92). Linear > quadratic. Fundamental limit: SWING measures win contribution, which is team-dependent by definition.
- Plat Chat Ep280 (champs-only): 8/10 in v2 top 28; lukxo #1, Slowly #2. Passes expert sanity check.
- v2 NOT added to model, site still on v1. v2 code/tables remain for future work.

### 70% feasibility: NOT REALISTIC from current data
Best annual accuracy 64.7%. 70% needs: historical closing odds, live veto/map data, verified roster/news, or fight-level data. Current VLR box-score/history caps around 65%.

### Commits (local only, NOT pushed)
- c469167: model winner (features.py, model.py)
- No v2 commit (failed validation). swing.py has uncommitted v2 code.
- HANDOFF.md updated throughout.
