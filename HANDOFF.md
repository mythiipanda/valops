# VALOPS handoff — Valorant Champions Shanghai 2026 predictor

## What this is
One-page prediction site + Python pipeline. Event: Champions Shanghai, Sep 24 to Oct 18 2026, 16 teams, GSL groups + double-elim playoffs. Site: Astro + React islands, Apple-minimal light design. Live at http://127.0.0.1:4321 via `npx astro preview` in `site/`.

## Data (data/valops.db, SQLite + data/raw 16k cached JSON)
- 1,861 series, 4,718 maps, 47k player-maps, 100k rounds. LOCK//IN 2023 (event 1188) to Stage 2 2026. Champions 2026 event id 2766 (no completed matches yet).
- Tables: series, maps, player_map, player_elo, team_last_roster, series_elo, map_swing, player_swing, map_sides, map_comp, map_eco (2025-26 full + 2023-24 backfill running), series_clutch, round_detail, team_traits_monthly, series_meta (patch, best_of).
- 2 maps lack round data on VLR (series 180279, 459856 game 1).
- Status manifest: data/status.json. Write path: `python3 run.py <ingest|elo|train|sim>`.

## Model (current best, SHIPPED)
- Roster-anchored player Elo (players carry ratings across orgs; team = 5-man mean minus chemistry dock; offseason 30% regression; provisional fast K; performance-weighted updates). v2 (map-level) and v3 (round-level) both FAILED gates and are shelved but kept in pipeline/elo.py.
- Logistic regression, 18 features (all A-minus-B diffs): elo, rating, acs, kast, adr, fkfd, form, winrate, h2h, lan, duel, cov, rest, sos, rt_pistol, rt_retake, favlen, elopo (playoff-Elo interaction).
- Walk-forward Brier: 2025 test 0.2209 acc 63.7%, 2026 test 0.2323 acc 62.8%. Coinflip 0.25.
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
