"""SWING: Share of Win from Individual Net Gains.

Round swing = map win-probability jump the winning side captures.
11-11 rounds carry ~256x the swing of 12-3 rounds. Stat padding dies here.

Credit weights are role-specific: duelists earn through kills, initiators
through first contact and assists, controllers and sentinels through
survival and support. Same pool, different splits.
"""

import sqlite3

import numpy as np
from scipy.special import betainc

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
