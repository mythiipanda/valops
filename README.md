# VALOPS — Valorant Champions Shanghai 2026 predictor

One-page prediction site plus the Python pipeline that builds it. Event: Champions Shanghai, Sep 24 to Oct 18 2026. 16 teams, GSL groups, double-elimination playoffs.

## Headline numbers

Current best model, walk-forward tested:

| Train through | Test | Brier | Accuracy | n |
|---|---|---|---|---|
| 2024 | 2025 | 0.2203 | 64.3% | 504 |
| 2025 | 2026 | 0.2320 | 63.1% | 588 |

Coinflip Brier is 0.25. Known weakness: overconfidence at extremes (matchups priced at 0.77 win about 0.62). Toss-ups (Elo gap under 15) hit 54%.

## Reproduce

```bash
pip install -r requirements.txt
python3 run.py ingest   # pull VLR data into data/valops.db (cached in data/raw/)
python3 run.py elo      # roster-anchored player Elo ratings
python3 run.py train    # features, logistic model, walk-forward Brier gates
python3 run.py sim      # 10k Monte Carlo sims, writes site/public/data.json
cd site && npm install && npx astro build   # static dist/, ship to GitHub Pages
```

Data as shipped: 1,861 series, 4,718 maps, 47k player-maps, 100k rounds, LOCK//IN 2023 through Stage 2 2026. Tables live in `data/valops.db`; `data/status.json` is the manifest.

## Methodology

Roster-anchored player Elo. Players carry ratings across orgs and years, so a transfer moves skill with the player instead of resetting it. Team strength is the current five-man mean minus a chemistry dock for new lineups. Each offseason regresses ratings 30% toward the mean. Provisional players get a 1.5x K multiplier for their first 10 maps. Updates scale by stage (group/main/playoff), event tier, and each player's map share, so stars move more than passengers.

The match model is logistic regression on 18 A-minus-B diffs: team Elo gap, player rating stats (rating, ACS, KAST, ADR, FK-FD), form, winrate, head-to-head, LAN flag, duelist share, coverage, rest, strength of schedule, pistol and retake round-type skill, favorite-map edge, and a playoff-Elo interaction.

Evaluation is walk-forward: train on all data through year Y, test on year Y+1. Champions 2026 is excluded from tests. A change ships only if it improves walk-forward Brier on both test years without harming either. Tried and rejected under this gate: margin-scaled K, decay weighting, momentum, playoff form, map-level Elo (v2), round-level Elo (v3), GBM, comp matchup matrix, round-win roll-up. The gate notes live in HANDOFF.md.

Forecasting runs 10k Monte Carlo sims over the real GSL groups, then an 8-team double-elimination playoff with playoff-conditioned probabilities.

The site also shows SWING, a round-swing impact metric (display only; it failed the predictive gate twice and stays out of the model), and a merge of Plat Chat expert top-10 lists next to the model's own player ranks.
