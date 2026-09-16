"""SWING: Share of Win from Individual Net Gains.

Round swing = map win-probability jump the winning side captures.
11-11 rounds carry ~256x the swing of 12-3 rounds. Stat padding dies here.

Credit weights are role-specific: duelists earn through kills, initiators
through first contact and assists, controllers and sentinels through
survival and support. Same pool, different splits.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import betainc

from .elo import SWING_PKL
from .mapcomp import ROLE

SEED = 7


def p_win(a: int, b: int) -> float:
    if a == 12 and b == 12:
        return 0.5
    if a >= 13 or b >= 13:
        if a - b >= 2:
            return 1.0
        if b - a >= 2:
            return 0.0
        # non-terminal OT: win-by-2 from +1 at q=0.5 wins 75% exactly
        return 0.5 + 0.25 * (1 if a > b else (-1 if b > a else 0))
    return float(betainc(13 - a, 13 - b, 0.5))


def swing(a: int, b: int) -> float:
    return p_win(a + 1, b) - p_win(a, b)


def expected_swing_table(n: int = 200000) -> dict:
    """E[total round swing | final score]. Fixed seed, computed once."""
    rng = np.random.default_rng(SEED)
    tot, cnt = {}, {}
    for _ in range(n):
        a = b = 0
        sw = 0.0
        while True:
            if (a >= 13 or b >= 13) and abs(a - b) >= 2:
                break
            if a == 12 and b == 12:
                sw += 0.5  # winner takes half the remaining pot
                if rng.random() < 0.5:
                    a += 2
                else:
                    b += 2
                break
            if rng.random() < 0.5:
                sw += swing(a, b)
                a += 1
            else:
                sw += swing(b, a)
                b += 1
        k = (a, b) if a > b else (b, a)
        tot[k] = tot.get(k, 0.0) + sw
        cnt[k] = cnt.get(k, 0) + 1
    return {k: tot[k] / cnt[k] for k in tot}


def _share(vals: list[float]) -> list[float]:
    s = sum(vals)
    if s <= 0:
        return [1.0 / len(vals)] * len(vals)
    return [v / s for v in vals]


def credit_map(rows: list[dict], pool: float) -> dict:
    """Split a map's swing pool across the winning team's 5. Returns {player_id: swing}."""
    for r in rows:
        for k in ("kills", "deaths", "assists", "fk", "kast"):
            r[k] = r[k] or 0
    fk = _share([max(r["fk"], 0) for r in rows])
    kd = _share([max(r["kills"] - r["deaths"], 0) for r in rows])
    if sum(max(r["kills"] - r["deaths"], 0) for r in rows) == 0:
        kd = _share([r["kills"] for r in rows])
    ap = _share([r["assists"] for r in rows])
    sv = _share([r["kast"] for r in rows])
    return {r["player_id"]: pool * (0.35 * f + 0.30 * k + 0.25 * a + 0.10 * s)
            for r, f, k, a, s in zip(rows, fk, kd, ap, sv)}


def opponent_mult(con: sqlite3.Connection, lo: float = 0.6,
                    hi: float = 1.6) -> dict:
    """Per-map credit multiplier from opponent pre-series Elo.

    mult = clamp(1 + (opp_elo - 1500) / 400, lo, hi). 400 is the Elo scale:
    a 100-point stronger opponent pays 25% more credit, a 100-point weaker
    one 25% less. Opponent Elo bakes in region strength, so farming a weak
    region pays ~0.65x while beating elite INTL opposition pays ~1.4x.
    series_elo holds pre-series strengths (no leakage).
    """
    se = {sid: (ea, eb) for sid, ea, eb
          in con.execute("SELECT series_id, elo_a, elo_b FROM series_elo")}
    tm = {sid: (ta, tb) for sid, ta, tb
          in con.execute("SELECT id, team_a, team_b FROM series")}
    out = {}
    maps = con.execute(
        "SELECT DISTINCT ps.series_id, ps.game FROM player_swing ps").fetchall()
    pteam = {}
    for sid, game, pid, tid in con.execute(
            "SELECT ps.series_id, ps.game, ps.player_id, pm.team_id"
            " FROM player_swing ps JOIN player_map pm ON ps.series_id=pm.series_id"
            " AND ps.game=pm.game AND ps.player_id=pm.player_id"):
        pteam[(sid, game)] = tid
    for sid, game in maps:
        ta, tb = tm.get(sid, (None, None))
        ea, eb = se.get(sid, (1500.0, 1500.0))
        wt = pteam.get((sid, game))
        opp_elo = eb if wt == ta else ea
        out[(sid, game)] = min(hi, max(lo, 1.0 + (opp_elo - 1500.0) / 400.0))
    return out


def run(con: sqlite3.Connection):
    con.execute("CREATE TABLE IF NOT EXISTS map_swing(series_id INTEGER, game INTEGER,"
                " pool REAL, PRIMARY KEY(series_id, game))")
    con.execute("CREATE TABLE IF NOT EXISTS player_swing(series_id INTEGER, game INTEGER,"
                " player_id INTEGER, swing REAL, PRIMARY KEY(series_id, game, player_id))")
    con.execute("DELETE FROM map_swing")
    con.execute("DELETE FROM player_swing")
    exp = expected_swing_table()
    maps = con.execute("SELECT series_id, game, a_rounds, b_rounds, winner FROM maps").fetchall()
    n = 0
    for sid, game, ar, br, w in maps:
        key = (max(ar, br), min(ar, br))
        pool = exp.get(key, 0.5)
        con.execute("INSERT INTO map_swing VALUES(?,?,?)", (sid, game, pool))
        tid = con.execute("SELECT team_a, team_b FROM series WHERE id=?", (sid,)).fetchone()
        wtid = tid[0] if w == "A" else tid[1]
        prows = con.execute(
            "SELECT player_id, kills, deaths, assists, fk, kast FROM player_map"
            " WHERE series_id=? AND game=? AND team_id=?", (sid, game, wtid)).fetchall()
        if not prows:
            continue
        rows = [dict(zip(("player_id", "kills", "deaths", "assists", "fk", "kast"), r))
                for r in prows]
        for pid, sw in credit_map(rows, pool).items():
            con.execute("INSERT INTO player_swing VALUES(?,?,?,?)", (sid, game, pid, sw))
            n += 1
    con.commit()
    return n


def ar_table_v2(con: sqlite3.Connection, half_life: float = 90.0,
                min_rounds: int = 400, since: str = "2025-01-01",
                ref_date: str | None = None) -> list:
    """SWING v2 AR per 100 rounds, with exponential recency decay.

    weight = 0.5^(age_days / half_life) applied to both swing and rounds.
    Loser credit is baked into player_swing_v2 by run_v2; opponent
    multipliers too. Replacement baselines use decayed values per role.
    ref_date defaults to the latest series date in the db.
    """
    import datetime
    if ref_date is None:
        ref_date = con.execute("SELECT MAX(date) FROM series").fetchone()[0]
    ref = datetime.datetime.fromisoformat(ref_date.replace("Z", "+00:00"))
    rows = con.execute(
        "SELECT ps.player_id, ps.swing, pm.rounds, pm.name, s.date,"
        " pm.fk, pm.fd"
        " FROM player_swing_v2 ps JOIN player_map pm"
        " ON ps.series_id=pm.series_id AND ps.game=pm.game AND ps.player_id=pm.player_id"
        " JOIN series s ON ps.series_id=s.id WHERE s.date >= ?",
        (since,)).fetchall()
    ag = con.execute(
        "SELECT player_id, agent, COUNT(*) FROM player_map pm JOIN series s"
        " ON pm.series_id=s.id WHERE s.date >= ? GROUP BY player_id, agent",
        (since,)).fetchall()
    primary = {}
    for pid, a, c in ag:
        primary.setdefault(pid, []).append((a, c))
    role_of = {}
    for pid, agents in primary.items():
        w: dict[str, int] = {}
        for a, c in agents:
            r = ROLE.get(a.split(",")[0], "?")
            w[r] = w.get(r, 0) + c
        role_of[pid] = max(w, key=lambda k: w[k])
    # decayed aggregates per player
    agg: dict[int, list] = {}  # pid -> [wswing, wrounds, weng, name]
    for pid, sw, rnd, name, date, fk, fd in rows:
        d = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
        age = max((ref - d).days, 0)
        wt = 0.5 ** (age / half_life)
        a = agg.setdefault(pid, [0.0, 0.0, 0.0, name])
        a[0] += (sw or 0.0) * wt
        a[1] += (rnd or 0) * wt
        a[2] += ((fk or 0) + (fd or 0)) * wt
    by_role: dict[str, list] = {}
    for pid, (wsw, wrnd, _, _) in agg.items():
        if wrnd >= min_rounds:
            by_role.setdefault(role_of.get(pid, "?"), []).append(wsw / wrnd)
    repl = {r: float(np.percentile(v, 20)) for r, v in by_role.items() if v}
    out = []
    for pid, (wsw, wrnd, weng, name) in agg.items():
        if wrnd < min_rounds:
            continue
        base = repl.get(role_of.get(pid, "?"), 0.0)
        out.append({"player_id": pid, "name": name, "rounds": round(wrnd, 1),
                    "role": role_of.get(pid, "?"),
                    "space": round(weng / max(wrnd, 1), 3),
                    "swing": round(wsw, 3),
                    "ar100": round((wsw - base * wrnd) / wrnd * 100.0, 3)})
    out.sort(key=lambda d: -d["ar100"])
    return out


def ar_table(con: sqlite3.Connection, min_rounds: int = 100,
             since: str = "2026-01-01", elo_adjust: bool = False) -> list:
    """Positional replacement SWING-AR per 100 rounds.

    NBA VORP idea: a replacement Controller is not a replacement Duelist.
    Each role gets its own 20th-percentile baseline. Subtraction keeps it stable.
    """
    from .mapcomp import ROLE
    mult = opponent_mult(con) if elo_adjust else {}
    if mult:
        con.execute("CREATE TEMP TABLE IF NOT EXISTS _mapmult("
                    "series_id INTEGER, game INTEGER, mult REAL,"
                    " PRIMARY KEY(series_id, game))")
        con.execute("DELETE FROM _mapmult")
        con.executemany("INSERT INTO _mapmult VALUES(?,?,?)",
                        [(s, g, m) for (s, g), m in mult.items()])
        swexpr = "SUM(ps.swing * COALESCE(mm.mult, 1.0))"
        mmjoin = "LEFT JOIN _mapmult mm ON ps.series_id=mm.series_id AND ps.game=mm.game"
    else:
        swexpr, mmjoin = "SUM(ps.swing)", ""
    rows = con.execute(
        f"SELECT ps.player_id, {swexpr}, SUM(pm.rounds), pm.name"
        " FROM player_swing ps JOIN player_map pm"
        " ON ps.series_id=pm.series_id AND ps.game=pm.game AND ps.player_id=pm.player_id"
        f" {mmjoin}"
        " JOIN series s ON ps.series_id=s.id WHERE s.date >= ?"
        " GROUP BY ps.player_id", (since,)).fetchall()
    eng = con.execute(
        "SELECT player_id, SUM(fk), SUM(fd), SUM(rounds) FROM player_map pm"
        " JOIN series s ON pm.series_id=s.id WHERE s.date >= ? GROUP BY player_id",
        (since,)).fetchall()
    eng = {p: ((f or 0) + (d or 0)) / max(r or 1, 1) for p, f, d, r in eng}
    ag = con.execute(
        "SELECT player_id, agent, COUNT(*) FROM player_map pm JOIN series s"
        " ON pm.series_id=s.id WHERE s.date >= ? GROUP BY player_id, agent",
        (since,)).fetchall()
    primary = {}
    for pid, a, c in ag:
        primary.setdefault(pid, []).append((a, c))
    role_of = {}
    for pid, agents in primary.items():
        w: dict[str, int] = {}
        for a, c in agents:
            r = ROLE.get(a.split(",")[0], "?")
            w[r] = w.get(r, 0) + c
        role_of[pid] = max(w, key=lambda k: w[k])
    by_role: dict[str, list] = {}
    for pid, sw, rnd, _ in rows:
        if rnd >= min_rounds:
            by_role.setdefault(role_of.get(pid, "?"), []).append(sw / rnd)
    repl = {r: float(np.percentile(v, 20)) for r, v in by_role.items() if v}
    out = []
    for pid, sw, rnd, name in rows:
        if rnd < min_rounds:
            continue
        base = repl.get(role_of.get(pid, "?"), 0.0)
        out.append({"player_id": pid, "name": name, "rounds": rnd,
                    "role": role_of.get(pid, "?"),
                    "space": round(eng.get(pid, 0.0), 3),
                    "swing": round(sw, 3), "ar100": round((sw - base * rnd) / rnd * 100.0, 3)})
    out.sort(key=lambda d: -d["ar100"])
    return out


# ---------------------------------------------------------------------------
# Round Swing ratings (v4, 2026-09-16). Display board for the site.
# ---------------------------------------------------------------------------

def round_swing_board(con: sqlite3.Connection, since: str = "2026-01-01",
                      until: str | None = None, min_rounds: int = 200,
                      half_life_days: float | None = None,
                      tier_weight: bool = False,
                      as_of: str = "2026-09-24",
                      event_ids: list | None = None) -> list:
    """Per-player Round Swing ratings: role(agent)-residualized, shrunk.

    Each kill moves the round's win probability; a player's Round Swing is
    the sum of those moves minus the prior-year mean for their agent, scaled
    per 100 rounds, then shrunk toward 0 by rounds played (20-map scale).
    Zero-sum per round: every point of credit to a killer is a point taken
    from the victim. Plants and defuses move the state but earn no direct
    credit. Agent baselines come from seasons before `since`. When
    half_life_days is set, maps are weighted by recency
    (2**(-age_days / half_life_days)) so recent splits count more; when
    tier_weight is set, maps are also scaled by event tier (masters 1.2x,
    regional 0.7x), so international results carry more weight.
    Returns [{player_id, name, rounds, role, rating}] sorted by rating.
    """
    import pickle
    from datetime import date
    try:
        swing = pickle.load(open(SWING_PKL, "rb"))
    except (OSError, pickle.PickleError):
        return []
    rows = con.execute(
        "SELECT s.date, pm.series_id, pm.game, pm.player_id, pm.name, pm.agent,"
        " pm.rounds FROM player_map pm JOIN series s ON s.id=pm.series_id"
        " WHERE s.date < ?", (since,)).fetchall()
    # agent baselines from seasons before the window
    by_ya: dict[tuple[str], list] = {}
    for date_s, sid, game, pid, name, agent, rnd in rows:
        sw = swing.get((sid, game, pid))
        if sw is not None:
            by_ya.setdefault((agent or "").split(",")[0], []).append(sw)
    base: dict[str, float] = {}
    for ag, vals in by_ya.items():
        if len(vals) >= 30:
            base[ag] = sum(vals) / len(vals)
    asof = date.fromisoformat(as_of[:10])
    tier_of: dict[int, float] = {}
    if tier_weight:
        from .config import EVENTS, TIER_W
        tier_of = {eid: TIER_W.get(tier, 1.0) for eid, _y, tier, _r in EVENTS}
    if event_ids is not None:
        marks = ",".join("?" * len(event_ids))
        wrows = con.execute(
            "SELECT s.date, s.event_id, pm.series_id, pm.game, pm.player_id, pm.name,"
            " pm.agent, pm.rounds FROM player_map pm JOIN series s ON s.id=pm.series_id"
            f" WHERE s.event_id IN ({marks})", tuple(event_ids)).fetchall()
    else:
        wrows = con.execute(
            "SELECT s.date, s.event_id, pm.series_id, pm.game, pm.player_id, pm.name,"
            " pm.agent, pm.rounds FROM player_map pm JOIN series s ON s.id=pm.series_id"
            " WHERE s.date >= ?" + ("" if until is None else " AND s.date < ?"),
            (since,) if until is None else (since, until)).fetchall()
    tot: dict[int, list] = {}  # pid -> [wresid_sum, wrounds, rounds, name, agent_counts]
    for date_s, eid, sid, game, pid, name, agent, rnd in wrows:
        sw = swing.get((sid, game, pid))
        if sw is None:
            continue
        ag = (agent or "").split(",")[0]
        r = sw - base.get(ag, 0.0)
        w = 1.0
        if half_life_days:
            age = (asof - date.fromisoformat(date_s[:10])).days
            w *= 2.0 ** (-max(age, 0) / half_life_days)
        if tier_weight:
            w *= tier_of.get(eid, 1.0)
        t = tot.setdefault(pid, [0.0, 0.0, 0, name, {}])
        t[0] += r * w
        t[1] += (rnd or 0) * w
        t[2] += rnd or 0
        t[4][ag] = t[4].get(ag, 0) + (rnd or 0)
    out = []
    for pid, (wrsum, wrnd, rnd, name, agents) in tot.items():
        if rnd < min_rounds:
            continue
        raw = wrsum / wrnd * 100.0 if wrnd else 0.0
        wsh = wrnd / (wrnd + 20 * 24)  # ~20 maps of shrinkage on effective rounds
        role = ROLE.get(max(agents, key=lambda a: agents[a]), "?") if agents else "?"
        out.append({"player_id": pid, "name": name, "rounds": rnd, "role": role,
                    "rating": round(raw * wsh, 2)})
    out.sort(key=lambda d: -d["rating"])
    return out


# ---------------------------------------------------------------------------
# SWING v2: loss credit + recency decay
# ---------------------------------------------------------------------------

def run_v2(con: sqlite3.Connection):
    """SWING v2. Winners get pool as in v1; losers get
    pool * (loser_rounds / winner_rounds), split by the same share formula.

    Linear (not quadratic) damping: 13-3 losers get 23% of the pool,
    13-11 losers get 85%. Quadratic proved too harsh on stars on bad teams
    (see HANDOFF.md 2026-09-15: basic rating-rank 5 buried at v2 rank 115).
    Opponent multiplier applies to both pools. Writes map_swing_v2 /
    player_swing_v2 (side 'W'/'L'); v1 tables untouched.
    """
    con.execute("CREATE TABLE IF NOT EXISTS map_swing_v2(series_id INTEGER, game INTEGER,"
                " pool_w REAL, pool_l REAL, PRIMARY KEY(series_id, game))")
    con.execute("CREATE TABLE IF NOT EXISTS player_swing_v2(series_id INTEGER, game INTEGER,"
                " player_id INTEGER, swing REAL, side TEXT,"
                " PRIMARY KEY(series_id, game, player_id))")
    con.execute("DELETE FROM map_swing_v2")
    con.execute("DELETE FROM player_swing_v2")
    exp = expected_swing_table()
    se = {sid: (ea, eb) for sid, ea, eb
          in con.execute("SELECT series_id, elo_a, elo_b FROM series_elo")}
    tm = {sid: (ta, tb) for sid, ta, tb
          in con.execute("SELECT id, team_a, team_b FROM series")}
    maps = con.execute("SELECT series_id, game, a_rounds, b_rounds, winner FROM maps").fetchall()
    n = 0
    for sid, game, ar, br, w in maps:
        wr, lr = max(ar, br), min(ar, br)
        pool_w = exp.get((wr, lr), 0.5)
        pool_l = pool_w * (lr / wr) if wr > 0 else 0.0
        con.execute("INSERT INTO map_swing_v2 VALUES(?,?,?,?)", (sid, game, pool_w, pool_l))
        ta, tb = tm.get(sid, (None, None))
        ea, eb = se.get(sid, (1500.0, 1500.0))
        wtid = ta if w == "A" else tb
        ltid = tb if w == "A" else ta

        def mult(tid):
            opp_elo = eb if tid == ta else ea
            return min(1.6, max(0.6, 1.0 + (opp_elo - 1500.0) / 400.0))

        for tid, pool, side in ((wtid, pool_w, "W"), (ltid, pool_l, "L")):
            prows = con.execute(
                "SELECT player_id, kills, deaths, assists, fk, kast FROM player_map"
                " WHERE series_id=? AND game=? AND team_id=?", (sid, game, tid)).fetchall()
            if not prows:
                continue
            rows = [dict(zip(("player_id", "kills", "deaths", "assists", "fk", "kast"), r))
                    for r in prows]
            for pid, sw in credit_map(rows, pool * mult(tid)).items():
                con.execute("INSERT INTO player_swing_v2 VALUES(?,?,?,?,?)",
                            (sid, game, pid, sw, side))
                n += 1
    con.commit()
    return n
