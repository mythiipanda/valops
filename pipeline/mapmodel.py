"""Map-by-map model. Weighted training prioritizes playoffs, internationals, recency."""

import sqlite3

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

from . import mapedge as G
from .config import EVENTS, TIER_W
from .elo import K_PLAYOFF, stage_k
from .model import COLS, brier

EV = {e: (y, t) for e, y, t, _ in EVENTS}
MCOLS = ["elo_diff", "map_off_diff", "pick_side"]


def build(con: sqlite3.Connection) -> pd.DataFrame:
    m = G.map_history(con)
    se = pd.read_sql("SELECT * FROM series_elo", con)
    mf = G.map_model_rows(m, se)
    mf["date"] = m["date"].values
    mf["series_id"] = m["series_id"].values
    mf["game"] = m["game"].values
    ev = pd.read_sql("SELECT id, event_id, stage, team_a, team_b, winner FROM series", con)
    ev = ev.set_index("id")
    mf["tier"] = mf["series_id"].map(lambda s: EV[ev.loc[s, "event_id"]][1])
    mf["stage"] = mf["series_id"].map(lambda s: ev.loc[s, "stage"])
    mf["is_playoff"] = mf["stage"].map(lambda s: stage_k(s or "") == K_PLAYOFF)
    mf["pick_side"] = mf.apply(
        lambda r: _picks.get((r["series_id"], r["game"]), 0), axis=1)
    return mf


_picks: dict = {}


def evaluate(df: pd.DataFrame):
    """Walk-forward map Brier with priority weights. Returns (plain, weighted) reports."""
    global _picks
    _picks = G.pick_sides()
    df = df.copy()
    df["pick_side"] = df.apply(lambda r: _picks.get((r["series_id"], r["game"]), 0), axis=1)
    reps = []
    for tr_end, te in [(2024, 2025), (2025, 2026)]:
        tr = df[df["date"].dt.year <= tr_end]
        t = df[df["date"].dt.year == te]
        w = (tr["tier"].map(TIER_W)
             * np.where(tr["is_playoff"], 1.5, 1.0)
             * 0.5 ** ((tr["date"].max() - tr["date"]).dt.total_seconds() / 86400 / 365.0))
        for name, sw in [("plain", None), ("weighted", w)]:
            clf = LogisticRegression(max_iter=2000)
            clf.fit(tr[MCOLS].fillna(0.0), tr["label"],
                    sample_weight=sw if sw is not None else None)
            p = clf.predict_proba(t[MCOLS].fillna(0.0))[:, 1]
            reps.append({"split": f"{tr_end}->{te}", "model": name,
                         "brier": round(brier(t["label"], p), 4),
                         "acc": round(float(accuracy_score(t["label"], p > 0.5)), 4)})
    return reps


def bo3(prob_list: list[float]) -> float:
    a, b, c = (list(prob_list) + [0.5, 0.5, 0.5])[:3]
    return a * b + a * (1 - b) * c + (1 - a) * b * c


def series_from_maps(df: pd.DataFrame, series_df: pd.DataFrame):
    """Walk-forward map probs aggregated to series via BO3 math. Returns Brier per split."""
    global _picks
    _picks = G.pick_sides()
    df = df.copy()
    df["pick_side"] = df.apply(lambda r: _picks.get((r["series_id"], r["game"]), 0), axis=1)
    out = []
    for tr_end, te in [(2024, 2025), (2025, 2026)]:
        tr = df[df["date"].dt.year <= tr_end]
        t = df[df["date"].dt.year == te]
        w = (tr["tier"].map(TIER_W)
             * np.where(tr["is_playoff"], 1.5, 1.0)
             * 0.5 ** ((tr["date"].max() - tr["date"]).dt.total_seconds() / 86400 / 365.0))
        clf = LogisticRegression(max_iter=2000)
        clf.fit(tr[MCOLS].fillna(0.0), tr["label"], sample_weight=w)
        sp = clf.predict_proba(t[MCOLS].fillna(0.0))[:, 1]
        t = t.copy()
        t["mp"] = sp
        ys, ps = [], []
        lab = series_df.set_index("series_id")["label"]
        for sid, g in t.groupby("series_id"):
            if sid not in lab.index:
                continue
            ys.append(lab.loc[sid])
            ps.append(bo3(list(g.sort_values("game")["mp"])))
        out.append({"split": f"{tr_end}->{te}", "series_from_maps_brier": round(brier(ys, ps), 4),
                    "n": len(ys)})
    return out
