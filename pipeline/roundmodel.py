"""Round-win model: per-round probs -> Markov map sim -> BO3 series prob."""

import sqlite3

import numpy as np
import pandas as pd

PRIOR_W, PRIOR_N = 0.5, 60


def load(con):
    r = pd.read_sql("SELECT * FROM round_detail", con)
    m = pd.read_sql("SELECT series_id, game, map_name FROM maps", con)
    s = pd.read_sql("SELECT id, date, team_a, team_b, event_id FROM series",
                    con, parse_dates=["date"])
    r = r.merge(m, on=["series_id", "game"]).merge(
        s, left_on="series_id", right_on="id")
    return r.sort_values(["date", "series_id", "game", "round_no"]).reset_index(drop=True)


def half_sides(g):
    """Infer which team attacks half 1 from R1 winner+side. Returns {team: 'atk_first'}."""
    r1 = g[g["round_no"] == 1]
    r13 = g[g["round_no"] == 13]
    out = {}
    ta, tb = g.iloc[0][["team_a", "team_b"]]
    if len(r1):
        w, side = r1.iloc[0][["winner_id", "side"]]
        out["h1_atk"] = w if side == "Attack" else (tb if w == ta else ta)
    if len(r13):
        w, side = r13.iloc[0][["winner_id", "side"]]
        out["h2_atk"] = w if side == "Attack" else (tb if w == ta else ta)
    return out


def team_side_strength(hist, tid, date, map_name=None, days=120):
    """Attack/defense round win% prior. Shrunk.

    Fallback windows: 120d -> 365d -> all history -> map prior -> 0.5.
    Never returns None (the old None-on-<12-rounds starved offseason matches
    and zeroed round_prob via falsy checks).
    """
    for d in (days, 365, 10_000):
        h = hist[((hist["team_a"] == tid) | (hist["team_b"] == tid)) &
                 (hist["date"] < date) & (hist["date"] > date - pd.Timedelta(days=d))]
        res = {}
        for side in ("Attack", "Defense"):
            hs = h[h["side"] == side]
            w = len(hs[hs["winner_id"] == tid])
            n = len(hs)
            res[side] = (w + PRIOR_W * PRIOR_N) / (n + PRIOR_N) if n >= 12 else None
        if all(v is not None for v in res.values()):
            return res
    # last resort: map-side prior, then coin flip
    prior = 0.5 + (map_side_prior(hist, date, map_name) if map_name else 0.0)
    return {"Attack": prior, "Defense": 1.0 - prior}


def map_side_prior(hist, date, map_name, days=365):
    h = hist[(hist["map_name"] == map_name) & (hist["date"] < date) &
             (hist["date"] > date - pd.Timedelta(days=days))]
    if len(h) < 100:
        return 0.0
    return float((h["side"] == "Attack").mean() - 0.5)


def round_prob(p_atk_a, p_def_a, p_atk_b, p_def_b, map_edge, a_attacks):
    """Log-odds: attack edge vs defense edge + map prior."""
    import math
    la = math.log(p_atk_a / (1 - p_atk_a)) - math.log(p_def_b / (1 - p_def_b)) if (
        p_atk_a and p_def_b) else 0.0
    ld = math.log(p_def_a / (1 - p_def_a)) - math.log(p_atk_b / (1 - p_atk_b)) if (
        p_def_a and p_atk_b) else 0.0
    base = la if a_attacks else ld
    return 1.0 / (1.0 + math.exp(-(base + map_edge * 4.0)))


def sim_map(p_round_fn, n=1000, seed=7):
    """Simulate a map given per-round win prob fn(round_no, a_score, b_score)."""
    rng = np.random.default_rng(seed)
    wins = 0
    for _ in range(n):
        a = b = 0
        for no in range(1, 60):
            if (a >= 13 or b >= 13) and abs(a - b) >= 2:
                break
            if rng.random() < p_round_fn(no, a, b):
                a += 1
            else:
                b += 1
        wins += a > b
    return wins / n
