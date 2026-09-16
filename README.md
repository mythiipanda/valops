# VALOPS

Predicting Valorant Champions Shanghai 2026 (Sep 24 to Oct 18). A one-page site with the Python pipeline that builds it. Live at https://mythiipanda.github.io/valops/.

## How good is it

Walk-forward tested: train on everything through year Y, test on year Y+1. Lower Brier is better. A coinflip scores 0.25.

| Train through | Test | Brier | Accuracy | Series |
|---|---|---|---|---|
| 2024 | 2025 | 0.219 | 64.5% | 504 |
| 2025 | 2026 | 0.228 | 63.8% | 588 |

Known flaw: it is overconfident at the extremes. Matchups priced at 77% win about 62% of the time.

## Run it

```bash
pip install -r requirements.txt
python3 run.py ingest   # pull VLR data into data/valops.db (cached in data/raw/)
python3 run.py elo      # player Elo ratings
python3 run.py train    # features, logistic model, walk-forward gates
python3 run.py sim      # 10k Monte Carlo sims, writes site/public/data.json
cd site && npm install && npx astro build   # static dist/, ships to GitHub Pages
```

The data as shipped: 1,861 series, 4,718 maps, 47k player-maps, 100k rounds, from LOCK//IN 2023 through Stage 2 2026. Tables live in `data/valops.db`. `data/v4_swing.pkl` holds the per-kill Round Swing table the Elo build loads.

## How it works

Ratings live on players, not teams, so a transfer moves the rating with the player. Team strength is the five-man mean minus a chemistry dock for new lineups. Every January ratings drift 20% back toward average. Rookies get a 1.5x K multiplier for their first 25 maps.

The v4 update changed how series credit is split. Every kill is worth the round-win probability it added, measured from 2023-2024 rounds by attackers alive, defenders alive, and plant state. A first blood in a 1v1 pays far more than a cleanup kill in a 5v2. Credit is zero-sum per round: what the killer gains, the victim loses. Each player is scored against the prior-year average for their agent, then shrunk toward zero by sample size. A player with n maps counts as n/(n+20) themselves.

A second, faster tracker (1.5x K, no January drift) runs alongside and reads as form.

The match model is logistic regression on 17 A-minus-B gaps: fast Elo, recent form, last-60-day stats (rating, ACS, KAST, first-kill differential), win rate, rest, schedule strength, pistol and retake skill, map edges, and a playoff interaction.

The site also ranks players by Round Swing and sets the board next to a merged Plat Chat expert top-40.

## What didn't make it

A change ships only if walk-forward Brier improves on both test years. These failed the gate: margin-scaled K, decay weighting, recency-decayed K, momentum, playoff form, map-level Elo, round-level Elo, GBM, comp matchup matrix, round-win roll-up.
