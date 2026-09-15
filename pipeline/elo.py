"""Roster-aware Elo. Players carry ratings across orgs and years.

Team strength = mean(current roster player Elo) - CHEM*(1-continuity).
New lineups lose chemistry, not identity. Offseason regresses 30% to mean.
"""

import json
import math
import sqlite3

from .config import (BASE_ELO, CHEM_PENALTY, K_GROUP, K_MAIN, K_PLAYOFF,
                     OFFSEASON_KEEP, PROV_MAPS, TIER_W)

PLAYOFF_HINTS = ("playoff", "bracket", "knockout", "final", "semifinal",
                 "quarterfinal", "upper", "lower", "grand")


def stage_k(stage: str) -> float:
    s = (stage or "").lower()
    if any(h in s for h in PLAYOFF_HINTS):
        return K_PLAYOFF
    if "group" in s or "swiss" in s or "week" in s or "league" in s:
        return K_GROUP
    return K_MAIN


def expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def run(con: sqlite3.Connection, event_meta: dict[int, tuple]) -> dict:
    """event_meta: event_id -> (year, tier). Returns final {player_id: (elo, maps)}."""
    con.execute("CREATE TABLE IF NOT EXISTS series_elo("
                "series_id INTEGER PRIMARY KEY, elo_a REAL, elo_b REAL,"
                " cont_a REAL, cont_b REAL)")
    con.execute("DELETE FROM series_elo")
    rows = con.execute(
        "SELECT id, event_id, date, stage, team_a, team_b, score_a, score_b, winner"
        " FROM series ORDER BY date, id").fetchall()
    elo: dict[int, float] = {}
    played: dict[int, int] = {}
    last_roster: dict[int, set] = {}
    tenure: dict[int, float] = {}
    cur_year = None

    for sid, eid, date, stage, ta, tb, sa, sb, winner in rows:
        year, tier = event_meta[eid]
        if cur_year is None:
            cur_year = year
        if year != cur_year:  # offseason regression
            for p in elo:
                elo[p] = BASE_ELO + OFFSEASON_KEEP * (elo[p] - BASE_ELO)
            cur_year = year
        pm = con.execute(
            "SELECT DISTINCT player_id, team_id FROM player_map WHERE series_id=?",
            (sid,)).fetchall()
        ra = {p for p, t in pm if t == ta}
        rb = {p for p, t in pm if t == tb}
        if not ra or not rb:
            continue
        n_maps = sa + sb or 1
        presence = con.execute(
            "SELECT player_id, COUNT(DISTINCT game) FROM player_map"
            " WHERE series_id=? GROUP BY player_id", (sid,)).fetchall()
        share = {p: c / n_maps for p, c in presence}
        perf = con.execute(
            "SELECT player_id, team_id, SUM(kills), SUM(deaths), SUM(fk) FROM player_map"
            " WHERE series_id=? GROUP BY player_id, team_id", (sid,)).fetchall()
        pw: dict[int, float] = {}
        for team in (ta, tb):
            tot = sum(max((k or 0) - (d or 0), 0) + (f or 0)
                      for p, t, k, d, f in perf if t == team)
            for p, t, k, d, f in perf:
                if t == team:
                    s = max((k or 0) - (d or 0), 0) + (f or 0)
                    pw[p] = 0.5 + (s / tot if tot > 0 else 0.2) / 0.2 * 0.5

        def strength(roster: set, team: int) -> tuple[float, float]:
            m = sum(elo.get(p, BASE_ELO) for p in roster) / len(roster)
            prev = last_roster.get(team)
            cont = len(roster & prev) / 5.0 if prev else 0.0
            return m - CHEM_PENALTY * (1.0 - cont), cont

        ea, ca = strength(ra, ta)
        eb, cb = strength(rb, tb)
        exp_a = expected(ea, eb)
        res_a = 1.0 if winner == "A" else 0.0
        k = stage_k(stage) * TIER_W[tier]
        for p in ra:
            pk = k * (1.5 if played.get(p, 0) < PROV_MAPS else 1.0)
            elo[p] = elo.get(p, BASE_ELO) + pk * pw.get(p, 1.0) * (res_a - exp_a) * share.get(p, 1.0)
            played[p] = played.get(p, 0) + 1
        for p in rb:
            pk = k * (1.5 if played.get(p, 0) < PROV_MAPS else 1.0)
            elo[p] = elo.get(p, BASE_ELO) + pk * pw.get(p, 1.0) * ((1.0 - res_a) - (1.0 - exp_a)) * share.get(p, 1.0)
            played[p] = played.get(p, 0) + 1
        last_roster[ta], last_roster[tb] = ra, rb
        con.execute("INSERT INTO series_elo VALUES(?,?,?,?,?)", (sid, ea, eb, ca, cb))
    con.execute("DELETE FROM player_elo")
    con.executemany("INSERT INTO player_elo VALUES(?,?,?)",
                    [(p, e, played.get(p, 0)) for p, e in elo.items()])
    con.execute("DELETE FROM team_last_roster")
    con.executemany("INSERT INTO team_last_roster VALUES(?,?)",
                    [(t, ",".join(map(str, r))) for t, r in last_roster.items()])
    con.commit()
    return {p: (e, played.get(p, 0)) for p, e in elo.items()}


def current_strengths(con: sqlite3.Connection) -> dict[int, float]:
    """Mean player Elo per team from last seen roster. Unchanged roster = no chem dock."""
    elos = dict(con.execute("SELECT player_id, elo FROM player_elo").fetchall())
    out = {}
    for tid, r in con.execute("SELECT team_id, roster FROM team_last_roster").fetchall():
        roster = [int(x) for x in r.split(",") if x]
        out[tid] = (sum(elos.get(p, BASE_ELO) for p in roster) / len(roster)) if roster else BASE_ELO
    return out


def run_v3(con: sqlite3.Connection, event_meta: dict[int, tuple]) -> dict:
    """Round-level Elo. Every round updates ratings.

    Round expectation from team gap (divisor 800). Update scaled by score
    leverage (11-11 counts ~6x average, dead rounds ~0). Update shared among
    the 5 starters by map stat-share, so stars move more than passengers.
    series_elo stores pre-series strengths (no leakage).
    """
    from .swing import swing as _swing
    con.execute("CREATE TABLE IF NOT EXISTS series_elo("
                "series_id INTEGER PRIMARY KEY, elo_a REAL, elo_b REAL,"
                " cont_a REAL, cont_b REAL)")
    con.execute("DELETE FROM series_elo")
    rows = con.execute(
        "SELECT id, event_id, date, stage, team_a, team_b, score_a, score_b, winner"
        " FROM series ORDER BY date, id").fetchall()
    elo: dict[int, float] = {}
    played: dict[int, int] = {}
    last_roster: dict[int, set] = {}
    cur_year = None

    def strength(roster: set, team: int) -> tuple[float, float]:
        m = sum(elo.get(p, BASE_ELO) for p in roster) / len(roster)
        prev = last_roster.get(team)
        cont = len(roster & prev) / 5.0 if prev else 0.0
        return m - CHEM_PENALTY * (1.0 - cont), cont

    for sid, eid, date, stage, ta, tb, sa, sb, winner in rows:
        year, tier = event_meta[eid]
        if year != cur_year:
            if elo:
                for p in elo:
                    elo[p] = BASE_ELO + OFFSEASON_KEEP * (elo[p] - BASE_ELO)
            cur_year = year
        pm = con.execute(
            "SELECT DISTINCT player_id, team_id FROM player_map WHERE series_id=?",
            (sid,)).fetchall()
        ra = {p for p, t in pm if t == ta}
        rb = {p for p, t in pm if t == tb}
        if not ra or not rb:
            continue
        # per-map stat shares for both teams (indexed lookups)
        shares: dict[int, float] = {}
        for (game,) in con.execute("SELECT game FROM maps WHERE series_id=?", (sid,)):
            st = con.execute(
                "SELECT player_id, team_id, kills, deaths, fk FROM player_map"
                " WHERE series_id=? AND game=?", (sid, game)).fetchall()
            for team in (ta, tb):
                tot = sum(max((k or 0) - (d or 0), 0) + (f or 0)
                          for p, t, k, d, f in st if t == team)
                for p, t, k, d, f in st:
                    if t == team:
                        s = max((k or 0) - (d or 0), 0) + (f or 0)
                        shares[(game, p)] = (s / tot) if tot > 0 else 0.2
        pre_a, pre_ca = strength(ra, ta)
        pre_b, pre_cb = strength(rb, tb)
        k_base = stage_k(stage) * TIER_W[tier] / 24.0
        rds = con.execute(
            "SELECT game, round_no, winner_id FROM round_detail"
            " WHERE series_id=? ORDER BY game, round_no", (sid,)).fetchall()
        if not rds:  # no round data (2 maps): fall back to series update
            exp_a = expected(pre_a, pre_b)
            res_a = 1.0 if winner == "A" else 0.0
            k = stage_k(stage) * TIER_W[tier]
            for p in ra:
                pk = k * (1.5 if played.get(p, 0) < PROV_MAPS else 1.0)
                elo[p] = elo.get(p, BASE_ELO) + pk * (res_a - exp_a) / 5.0
            for p in rb:
                pk = k * (1.5 if played.get(p, 0) < PROV_MAPS else 1.0)
                elo[p] = elo.get(p, BASE_ELO) + pk * ((1.0 - res_a) - (1.0 - exp_a)) / 5.0
        else:
            score: dict[int, dict[int, int]] = {}
            for game, no, wid in rds:
                sc = score.setdefault(game, {ta: 0, tb: 0})
                ea, _ = strength(ra, ta)
                eb, _ = strength(rb, tb)
                p_a = 1.0 / (1.0 + 10 ** ((eb - ea) / 800.0))
                lev = _swing(sc[ta], sc[tb]) * 24.0
                res = 1.0 if wid == ta else 0.0
                upd = k_base * lev * (res - p_a)
                for p in ra:
                    w = 0.5 + 2.5 * shares.get((game, p), 0.2)
                    w *= 1.5 if played.get(p, 0) < PROV_MAPS else 1.0
                    elo[p] = elo.get(p, BASE_ELO) + upd * w
                for p in rb:
                    w = 0.5 + 2.5 * shares.get((game, p), 0.2)
                    w *= 1.5 if played.get(p, 0) < PROV_MAPS else 1.0
                    elo[p] = elo.get(p, BASE_ELO) - upd * w
                sc[ta if wid == ta else tb] += 1
        for p in ra | rb:
            played[p] = played.get(p, 0) + 1
        last_roster[ta], last_roster[tb] = ra, rb
        con.execute("INSERT INTO series_elo VALUES(?,?,?,?,?)",
                    (sid, pre_a, pre_b, pre_ca, pre_cb))
    con.execute("DELETE FROM player_elo")
    con.executemany("INSERT INTO player_elo VALUES(?,?,?)",
                    [(p, e, played.get(p, 0)) for p, e in elo.items()])
    con.execute("DELETE FROM team_last_roster")
    con.executemany("INSERT INTO team_last_roster VALUES(?,?)",
                    [(t, ",".join(map(str, r))) for t, r in last_roster.items()])
    con.commit()
    return {p: (e, played.get(p, 0)) for p, e in elo.items()}


def run_v2(con: sqlite3.Connection, event_meta: dict[int, tuple],
           role_adj: bool = True, k_div: float = 2.5) -> dict:
    """RAPTOR-style Elo. Map-level updates, swing-weighted players,
    leverage-scaled K, role-adjusted team strength.

    Team strength = mean(player Elo vs role baseline) - CHEM*(1-continuity).
    Update per map: K * leverage * (0.5 + 2.5*swing_share) * (result - expected).
    """
    from .mapcomp import ROLE
    con.execute("CREATE TABLE IF NOT EXISTS series_elo("
                "series_id INTEGER PRIMARY KEY, elo_a REAL, elo_b REAL,"
                " cont_a REAL, cont_b REAL)")
    con.execute("DELETE FROM series_elo")
    rows = con.execute(
        "SELECT id, event_id, date, stage, team_a, team_b, score_a, score_b, winner"
        " FROM series ORDER BY date, id").fetchall()
    pool = dict(con.execute("SELECT series_id || ':' || game, pool FROM map_swing").fetchall())
    avg_pool = sum(pool.values()) / max(len(pool), 1)
    elo: dict[int, float] = {}
    played: dict[int, int] = {}
    last_roster: dict[int, set] = {}
    role_mean: dict[str, float] = {}
    cur_year = None

    def roles_before(date: str, pids: set) -> dict[int, str]:
        if not pids:
            return {}
        q = (f"SELECT player_id, agent, COUNT(*) FROM player_map pm JOIN series s"
             f" ON pm.series_id=s.id WHERE s.date < ? AND player_id IN ("
             + ",".join("?" * len(pids)) + ") GROUP BY player_id, agent")
        ag: dict[int, list] = {}
        for pid, a, c in con.execute(q, [date, *pids]).fetchall():
            ag.setdefault(pid, []).append((a.split(",")[0], c))
        out = {}
        for pid, lst in ag.items():
            w: dict[str, int] = {}
            for a, c in lst:
                r = ROLE.get(a, "?")
                w[r] = w.get(r, 0) + c
            out[pid] = max(w, key=lambda k: w[k])
        return out

    for sid, eid, date, stage, ta, tb, sa, sb, winner in rows:
        year, tier = event_meta[eid]
        if year != cur_year:
            if elo:  # refresh role baselines + regress at offseason
                by_role: dict[str, list] = {}
                rm = roles_before(date, set(elo))
                for p, e in elo.items():
                    if played.get(p, 0) >= 20:
                        by_role.setdefault(rm.get(p, "?"), []).append(e)
                for r, v in by_role.items():
                    if len(v) >= 10:
                        role_mean[r] = sum(v) / len(v)
                for p in elo:
                    elo[p] = BASE_ELO + OFFSEASON_KEEP * (elo[p] - BASE_ELO)
            cur_year = year
        pm = con.execute(
            "SELECT player_id, team_id FROM player_map WHERE series_id=?", (sid,)).fetchall()
        ra = {p for p, t in pm if t == ta}
        rb = {p for p, t in pm if t == tb}
        if not ra or not rb:
            continue
        rm = roles_before(date, ra | rb)
        gmean = BASE_ELO

        def strength(roster: set, team: int) -> tuple[float, float]:
            if role_adj:
                adj = [elo.get(p, BASE_ELO) - role_mean.get(rm.get(p, "?"), BASE_ELO) + gmean
                       for p in roster]
            else:
                adj = [elo.get(p, BASE_ELO) for p in roster]
            m = sum(adj) / len(adj)
            prev = last_roster.get(team)
            cont = len(roster & prev) / 5.0 if prev else 0.0
            return m - CHEM_PENALTY * (1.0 - cont), cont

        games = con.execute(
            "SELECT game FROM maps WHERE series_id=? ORDER BY game", (sid,)).fetchall()
        pre_a, pre_ca = strength(ra, ta)
        pre_b, pre_cb = strength(rb, tb)
        k_base = stage_k(stage) * TIER_W[tier] / k_div
        for (game,) in games:
            ea, _ = strength(ra, ta)
            eb, _ = strength(rb, tb)
            wm = con.execute("SELECT winner FROM maps WHERE series_id=? AND game=?",
                             (sid, game)).fetchone()[0]
            exp_a = expected(ea, eb)
            res_a = 1.0 if wm == "A" else 0.0
            lev = pool.get(f"{sid}:{game}", avg_pool) / avg_pool
            stats = con.execute(
                "SELECT player_id, team_id, kills, deaths, fk FROM player_map"
                " WHERE series_id=? AND game=?", (sid, game)).fetchall()
            share: dict[int, float] = {}
            for team, cond in ((ta, lambda t: t == ta), (tb, lambda t: t == tb)):
                tot = sum(max((k or 0) - (d or 0), 0) + (f or 0)
                          for p, t, k, d, f in stats if cond(t))
                for p, t, k, d, f in stats:
                    if cond(t):
                        s = max((k or 0) - (d or 0), 0) + (f or 0)
                        share[p] = (s / tot) if tot > 0 else 0.2
            for p in ra | rb:
                is_a = p in ra
                kk = k_base * lev * (0.5 + 2.5 * share.get(p, 0.2))
                kk *= 1.5 if played.get(p, 0) < PROV_MAPS else 1.0
                r = (res_a - exp_a) if is_a else ((1.0 - res_a) - (1.0 - exp_a))
                elo[p] = elo.get(p, BASE_ELO) + kk * r
                played[p] = played.get(p, 0) + 1
        last_roster[ta], last_roster[tb] = ra, rb
        con.execute("INSERT INTO series_elo VALUES(?,?,?,?,?)",
                    (sid, pre_a, pre_b, pre_ca, pre_cb))
    con.execute("DELETE FROM player_elo")
    con.executemany("INSERT INTO player_elo VALUES(?,?,?)",
                    [(p, e, played.get(p, 0)) for p, e in elo.items()])
    con.execute("DELETE FROM team_last_roster")
    con.executemany("INSERT INTO team_last_roster VALUES(?,?)",
                    [(t, ",".join(map(str, r))) for t, r in last_roster.items()])
    con.commit()
    return {p: (e, played.get(p, 0)) for p, e in elo.items()}
