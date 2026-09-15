"""Map-level edge from cached info JSON. No new fetching.

Per-team per-map shrunk winrates + pick flags + side splits feed both a
map-outcome model and three new series features (depth, edge, pick).
"""

import json
import sqlite3
from pathlib import Path

import pandas as pd

PRIOR_W, PRIOR_N = 0.5, 6


def map_history(con: sqlite3.Connection) -> pd.DataFrame:
    """One row per map: date, teams, map, winner, pick, sides. From DB + raw cache."""
    s = pd.read_sql("SELECT id, date, team_a, team_b FROM series ORDER BY date, id",
                    con, parse_dates=["date"])
    m = pd.read_sql("SELECT * FROM maps", con)
    s = s.set_index("id")
    m = m.join(s, on="series_id")
    picks, atk = {}, {}
    for sid in m["series_id"].unique():
        p = Path(f"data/raw/series_{sid}.json")
        if not p.exists():
            continue
        info = json.loads(p.read_text())
        t1, t2 = info["team1"]["id"], info["team2"]["id"]
        for g in info["games"]:
            if not g.get("played"):
                continue
            picks[(sid, g["order"])] = g.get("picked_by") or ""
            a_tot = (g.get("team1_attack_rounds") or 0) + (g.get("team1_defense_rounds") or 0)
            atk[(sid, g["order"])] = (g.get("team1_attack_rounds") or 0,
                                      g.get("team1_defense_rounds") or 0,
                                      a_tot)
    m["picked_by"] = [picks.get((s, g), "") for s, g in zip(m["series_id"], m["game"])]
    m["t1_atk"] = [atk.get((s, g), (0, 0, 1))[0] for s, g in zip(m["series_id"], m["game"])]
    m["t1_def"] = [atk.get((s, g), (0, 0, 1))[1] for s, g in zip(m["series_id"], m["game"])]
    return m.sort_values(["date", "series_id", "game"]).reset_index(drop=True)


def pick_sides() -> dict:
    """(series_id, game_order) -> +1 team1 picked, -1 team2 picked, 0 decider/unknown."""
    out = {}
    for p in Path("data/raw/series_*.json").parent.glob("series_*.json"):
        try:
            info = json.loads(p.read_text())
        except Exception:
            continue
        t1, t2 = info["team1"]["tag"], info["team2"]["tag"]
        for g in info["games"]:
            if not g.get("played"):
                continue
            pk = (g.get("picked_by") or "").strip()
            out[(info["series_id"], g["order"])] = (
                1 if pk == t1 else (-1 if pk == t2 else 0))
    return out


def _off(w, n):
    return (w + PRIOR_W * PRIOR_N) / (n + PRIOR_N) - 0.5


def series_map_features(m: pd.DataFrame, se: pd.DataFrame) -> pd.DataFrame:
    """Three series diffs: depth (maps owned), edge (mean offset), pick (picks won)."""
    se = se.set_index("series_id")
    out = []
    for sid, g in m.groupby("series_id", sort=False):
        row = g.iloc[0]
        past = m[(m["date"] < row["date"]) |
                 ((m["date"] == row["date"]) & (m["series_id"] < sid))]
        vals = {}
        for prefix, tid in (("a", row["team_a"]), ("b", row["team_b"])):
            h = past[past["team_a"].eq(tid) | past["team_b"].eq(tid)]
            hm = past[((past["team_a"] == tid) | (past["team_b"] == tid))]
            offs, depth = [], 0
            for mp, gh in hm.groupby("map_name"):
                w = len(gh[((gh["winner"] == "A") & (gh["team_a"] == tid)) |
                           ((gh["winner"] == "B") & (gh["team_b"] == tid))])
                o = _off(w, len(gh))
                offs.append(o)
                if o > 0.05:
                    depth += 1
            vals[f"{prefix}_edge"] = sum(offs) / len(offs) if offs else 0.0
            vals[f"{prefix}_depth"] = depth
        out.append({"series_id": sid, "edge_diff": vals["a_edge"] - vals["b_edge"],
                    "depth_diff": vals["a_depth"] - vals["b_depth"]})
    return pd.DataFrame(out)


def map_model_rows(m: pd.DataFrame, se: pd.DataFrame) -> pd.DataFrame:
    """Per-map training rows: elo + map offset + pick + label."""
    se = se.set_index("series_id")
    out = []
    for _, r in m.iterrows():
        past = m[(m["date"] < r["date"]) |
                 ((m["date"] == r["date"]) & (m["series_id"] < r["series_id"]))]
        oo = []
        for tid in (r["team_a"], r["team_b"]):
            hm = past[((past["team_a"] == tid) | (past["team_b"] == tid)) &
                      (past["map_name"] == r["map_name"])]
            w = len(hm[((hm["winner"] == "A") & (hm["team_a"] == tid)) |
                       ((hm["winner"] == "B") & (hm["team_b"] == tid))])
            oo.append(_off(w, len(hm)))
        ed = 0.0
        if r["series_id"] in se.index:
            ed = se.loc[r["series_id"], "elo_a"] - se.loc[r["series_id"], "elo_b"]
        out.append({"elo_diff": ed, "map_off_diff": oo[0] - oo[1],
                    "label": 1 if r["winner"] == "A" else 0})
    return pd.DataFrame(out)
