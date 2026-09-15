"""Logistic model on diffs + GSL/double-elim Monte Carlo for Shanghai groups."""

import itertools
import sqlite3

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

from . import features as F
from .config import GROUPS, TEAMS
from .elo import current_strengths

COLS = [f"{s}_diff" for s in F.ALL] + ["favlen_diff", "elopo_diff", "elo_fast_diff"]


def brier(y, p):
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    return float(np.mean((p - y) ** 2))


def evaluate(df: pd.DataFrame):
    """Walk-forward by year. Shanghai (2766) excluded from tests."""
    cuts = [(2024, 2025), (2025, 2026)]
    reps = []
    for tr_end, te in cuts:
        tr = df[(df["date"].dt.year <= tr_end) & (df["event_id"] != 2766)]
        te_df = df[(df["date"].dt.year == te) & (df["event_id"] != 2766)]
        if not len(tr) or not len(te_df):
            continue
        clf = LogisticRegression(max_iter=2000).fit(tr[COLS].fillna(0.0), tr["label"])
        p = clf.predict_proba(te_df[COLS].fillna(0.0))[:, 1]
        reps.append({"train_thru": tr_end, "test": te, "n_test": len(te_df),
                     "brier": round(brier(te_df["label"], p), 4),
                     "acc": round(float(accuracy_score(te_df["label"], p > 0.5)), 4)})
    base = df[df["event_id"] != 2766]["label"]
    reps.append({"baseline_coinflip_brier": round(brier(base, 0.5), 4)})
    return reps


def fit_all(df: pd.DataFrame):
    tr = df[df["event_id"] != 2766]
    clf = LogisticRegression(max_iter=2000).fit(tr[COLS].fillna(0.0), tr["label"])
    return clf, list(zip(COLS, clf.coef_[0]))


def pairwise(con: sqlite3.Connection, clf, date, is_po: int = 0) -> tuple[dict, dict]:
    """P(win) + top factors for every ordered Shanghai pair at date."""
    elos = current_strengths(con)
    elos_fast = current_strengths(con, "player_elo_fast", "team_last_roster_fast")
    coefs = dict(zip(COLS, clf.coef_[0]))
    date = pd.Timestamp(date, tz="UTC")
    probs, factors = {}, {}
    for a, b in itertools.permutations(TEAMS, 2):
        d = F.matchup(con, a, b, date,
                      elos.get(a, 1500.0) - elos.get(b, 1500.0),
                      elos_fast.get(a, 1500.0) - elos_fast.get(b, 1500.0), is_po)
        x = pd.DataFrame([{c: d.get(c, 0.0) for c in COLS}]).fillna(0.0)
        probs[(a, b)] = float(clf.predict_proba(x)[0, 1])
        contrib = sorted(((c, coefs[c] * d.get(c, 0.0)) for c in COLS),
                         key=lambda kv: -abs(kv[1]))[:3]
        factors[(a, b)] = [{"f": c, "v": round(float(v), 3)} for c, v in contrib]
    return probs, factors


def _gsl(group: list[int], p, rng) -> list[int]:
    """Double-elim 4-team group. Returns 2 advancers."""
    def win(a, b):
        return a if rng.random() < p[(a, b)] else b
    w1, w2 = win(group[0], group[1]), win(group[2], group[3])
    adv1 = win(w1, w2)  # winners final, adv1 advances
    los1 = w2 if adv1 == w1 else w1
    l_open1 = group[1] if w1 == group[0] else group[0]
    l_open2 = group[3] if w2 == group[2] else group[2]
    elim_loser = l_open1 if win(l_open1, l_open2) == l_open2 else l_open2
    decider = l_open2 if elim_loser == l_open1 else l_open1
    adv2 = win(los1, decider)
    return [adv1, adv2]


def _double_elim(teams: list[int], p, rng, bo5_final=True) -> int:
    """8-team double elim. Returns champion."""
    def win(a, b):
        return a if rng.random() < p[(a, b)] else b
    qf = [win(teams[i], teams[i + 1]) for i in (0, 2, 4, 6)]
    ql = [teams[i + 1] if qf[i // 2] == teams[i] else teams[i] for i in (0, 2, 4, 6)]
    sf = [win(qf[0], qf[1]), win(qf[2], qf[3])]
    sfl = [qf[1] if sf[0] == qf[0] else qf[0], qf[3] if sf[1] == qf[2] else qf[2]]
    # lower bracket: q losers + s losers filter down
    l1 = [win(ql[0], ql[1]), win(ql[2], ql[3])]
    l2 = [win(l1[0], sfl[1]), win(l1[1], sfl[0])]
    l3w = win(l2[0], l2[1])
    uf = win(sf[0], sf[1])
    ufl = sf[1] if uf == sf[0] else sf[0]
    lf = win(l3w, ufl)
    champ = win(uf, lf)
    if champ == lf:  # bracket reset
        champ = win(uf, lf)
    return champ


def simulate(p_group: dict, p_playoff: dict | None = None,
             n: int = 10000, seed: int = 7) -> dict:
    pp = p_playoff or p_group
    rng = np.random.default_rng(seed)
    titles = {t: 0 for t in TEAMS}
    adv = {t: 0 for t in TEAMS}
    for _ in range(n):
        po = []
        for g in GROUPS.values():
            a = _gsl(list(g), p_group, rng)
            po.extend(a)
            for t in a:
                adv[t] += 1
        titles[_double_elim(po, pp, rng)] += 1
    return {"title": {t: titles[t] / n for t in TEAMS},
            "advance": {t: adv[t] / n for t in TEAMS}}
