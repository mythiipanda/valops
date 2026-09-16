"""Roster-aware Elo. Players carry ratings across orgs and years.

Team strength = mean(current roster player Elo) - CHEM*(1-continuity).
New lineups lose chemistry, not identity. Offseason regresses 30% to mean.

v4 (2026-09-16, gated: walk-forward pooled Brier 0.2213 -> 0.2210):
- Round-Swing performance weight: per-series credit split uses each player's
  role(agent)-residualized Round Swing share (change in round-win probability
  caused by their kills, zero-sum per round) instead of max(K-D,0)+FK.
  Falls back to K-D+FK for series without kill-event data.
- Empirical-Bayes shrinkage on strength: a player with n prior maps
  contributes BASE + (elo-BASE)*n/(n+20), replacing the binary provisional
  cutoff with smooth sample-size shrinkage.
"""

import json
import math
import pickle
import sqlite3
from pathlib import Path

from .config import (BASE_ELO, CHEM_PENALTY, K_GROUP, K_MAIN, K_PLAYOFF,
                     OFFSEASON_KEEP, PROV_MAPS, TIER_W)

# v4: per-(series, game, player) Round Swing, zero-sum per round.
# Built from kill events (see zen-scratch/v4_swing_build.py). Missing file or
# missing series -> falls back to the old K-D+FK credit split.
SWING_PKL = Path(__file__).parent.parent / "data" / "v4_swing.pkl"
# v4: shrinkage half-life in maps (ARIA-style: ~50% own signal at 20 maps).
SHRINK_K = 20.0
# v4: provisional K cutoff, now counted in maps (10 series ~= 25 maps).
PROV_MAPS_V4 = 25

PLAYOFF_HINTS = ("playoff", "bracket", "knockout", "final", "semifinal",
                 "quarterfinal", "upper", "lower", "grand")
GROUP_HINTS = ("group", "swiss", "regular season", "league play", "main event",
               "preliminary")

# Dead-rubber discount: late group-stage matches get scaled K because
# favorites systematically underperform late in groups (coasting).
# 1.0 = off. Tested values in HANDOFF log.
LATE_K_MULT = 1.0  # disabled: failed gate at 0.5 and 0.3 (2025 brier regresses)
LATE_FRAC = 0.25

# Standings-based stakes discount: matches where BOTH teams are already
# decided (clinched a playoff spot or eliminated) get scaled K.
# 1.0 = off. See low_stakes_series() for the tagger rules.
STAKES_K_MULT = 1.0  # disabled: failed gate at 0.5 and 0.3 (2025 acc regresses)
SWISS_QUALIFY_WINS = 2  # 8-team/10-match swiss: 2-0 qualifies ...
SWISS_ELIM_LOSSES = 2  # ... and 0-2 is eliminated (verified trace, event 1921)


def is_group_stage(stage: str) -> bool:
    s = (stage or "").lower()
    return any(h in s for h in GROUP_HINTS) and not any(
        h in s for h in PLAYOFF_HINTS)


def stage_k(stage: str) -> float:
    s = (stage or "").lower()
    if any(h in s for h in PLAYOFF_HINTS):
        return K_PLAYOFF
    if "group" in s or "swiss" in s or "week" in s or "league" in s:
        return K_GROUP
    return K_MAIN


def expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def _connected_subgroups(pairs: list[tuple[int, int]]) -> list[set[int]]:
    """Connected components of the team-vs-team matchup graph.

    An (event_id, stage) block may hold several groups (e.g. Group A and B
    both labeled "Group Stage"); teams that never share an opponent path are
    separate standings tables.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
    for a, b in pairs:
        parent[find(a)] = find(b)
    groups: dict[int, set[int]] = {}
    for t in parent:
        groups.setdefault(find(t), set()).add(t)
    return list(groups.values())


def low_stakes_series(con: sqlite3.Connection) -> tuple[set[int], dict]:
    """Tag group matches where NEITHER team has anything left to play for.

    Knowable pre-match: W-L records use only block matches strictly before
    the current one (same (date, id) order as run()); remaining counts use the
    known block schedule. The cut line k (top-k advance) is a format constant
    inferred ex post: # of sub-group teams that appear in a later playoff-hint
    stage of the same event (Play-Ins excluded: those teams have not advanced).

    Rules per format:
    - Swiss blocks ("swiss" in stage): team at 2-0 has qualified (seeding only),
      team at 0-2 is eliminated. Tag 2-0-vs-2-0 and 0-2-vs-0-2.
    - Round-robin / GSL blocks: team is ELIMINATED if k other teams are locked
      strictly above its best case (W + remaining); CLINCHED if at most k-1
      other teams can even tie its floor (W with 0 remaining wins) — both
      tiebreak-proof. Tag when both teams are clinched-or-eliminated. GSL and
      double-elim brackets naturally yield ~0 tags: decided teams stop playing,
      so no match ever fields two decided teams.
    - Blocks with no in-event playoff cut (k == 0: Main Event Kickoff/LCQ
      brackets) or degenerate cuts (k >= group size) are skipped (0 tags).

    Returns (low_stakes series ids, diagnostics dict).
    """
    rows = con.execute(
        "SELECT id, event_id, date, stage, team_a, team_b, winner"
        " FROM series ORDER BY date, id").fetchall()
    blocks: dict[tuple, list] = {}
    for r in rows:
        if is_group_stage(r[3]):
            blocks.setdefault((r[1], r[3]), []).append(r)
    # later playoff-hint stages per event (by block start date)
    stage_min: dict[tuple[int, str], str] = {}
    for eid, stage, mn in con.execute(
            "SELECT event_id, stage, MIN(date) FROM series GROUP BY event_id, stage"):
        stage_min[(eid, stage)] = mn
    diag: dict = {"blocks": [], "n_group_matches": 0, "n_low_stakes": 0,
                  "n_swiss_tags": 0, "n_rr_tags": 0}
    low: set[int] = set()
    for (eid, stage), ms in sorted(blocks.items()):
        diag["n_group_matches"] += len(ms)
        pairs = [(m[4], m[5]) for m in ms]
        subs = _connected_subgroups(pairs)
        bmin = stage_min[(eid, stage)]
        po_teams: set[int] = set()
        for (e2, s2), mn in stage_min.items():
            if e2 == eid and mn > bmin and any(
                    h in (s2 or "").lower() for h in PLAYOFF_HINTS):
                for (t,) in con.execute(
                        "SELECT team_a FROM series WHERE event_id=? AND stage=?"
                        " UNION SELECT team_b FROM series WHERE event_id=? AND stage=?",
                        (eid, s2, eid, s2)):
                    po_teams.add(t)
        binfo = {"event": eid, "stage": stage, "n": len(ms),
                 "subgroups": [], "tags": 0}
        is_swiss = "swiss" in (stage or "").lower()
        # suffix remaining-match counts per team (schedule knowable pre-match)
        rem_after: list[dict[int, int]] = [{} for _ in ms]
        future: dict[int, int] = {}
        for i in range(len(ms) - 1, -1, -1):
            rem_after[i] = dict(future)
            future[ms[i][4]] = future.get(ms[i][4], 0) + 1
            future[ms[i][5]] = future.get(ms[i][5], 0) + 1
        w: dict[int, int] = {}
        for i, m in enumerate(ms):
            sid, _, _, _, ta, tb, winner = m
            if is_swiss:
                wta = w.get(ta, 0)
                lta = sum(1 for j in range(i) if (
                    (ms[j][4] == ta or ms[j][5] == ta)))
                lta -= wta
                wtb = w.get(tb, 0)
                ltb = sum(1 for j in range(i) if (
                    (ms[j][4] == tb or ms[j][5] == tb)))
                ltb -= wtb
                qa = wta >= SWISS_QUALIFY_WINS or lta >= SWISS_ELIM_LOSSES
                qb = wtb >= SWISS_QUALIFY_WINS or ltb >= SWISS_ELIM_LOSSES
                if qa and qb:
                    low.add(sid)
                    binfo["tags"] += 1
                    diag["n_swiss_tags"] += 1
            else:
                # find sub-group + cut for these teams
                for sub in subs:
                    if ta in sub and tb in sub:
                        k = len(sub & po_teams)
                        if 0 < k < len(sub):
                            rem = rem_after[i]
                            best = {t: w.get(t, 0) + rem.get(t, 0) for t in sub}
                            floor = {t: w.get(t, 0) for t in sub}

                            def decided(t: int) -> bool:
                                # t is playing this match: its best case
                                # includes winning it (1 + later matches).
                                best_t = floor[t] + 1 + rem.get(t, 0)
                                elim = sum(1 for u in sub if u != t and floor.get(u, 0) > best_t) >= k
                                clinch = sum(1 for u in sub if u != t and best.get(u, 0) >= floor[t]) <= k - 1
                                return elim or clinch

                            if decided(ta) and decided(tb):
                                low.add(sid)
                                binfo["tags"] += 1
                                diag["n_rr_tags"] += 1
                        break
            # update records with this match's result (strictly-past for later)
            if winner == "A":
                w[ta] = w.get(ta, 0) + 1
            elif winner == "B":
                w[tb] = w.get(tb, 0) + 1
        for sub in subs:
            binfo["subgroups"].append(
                {"size": len(sub), "k": len(sub & po_teams)})
        diag["blocks"].append(binfo)
    diag["n_low_stakes"] = len(low)
    return low, diag


def run(con: sqlite3.Connection, event_meta: dict[int, tuple],
        *, k_mult: float = 1.0, offseason_keep: float | None = None,
        tables: tuple[str, str, str] = ("series_elo", "player_elo",
                                        "team_last_roster")) -> dict:
    """event_meta: event_id -> (year, tier). Returns final {player_id: (elo, maps)}.

    k_mult: scale all K (fast "form" Elo uses >1).
    offseason_keep: None -> config OFFSEASON_KEEP.
    tables: (series_elo, player_elo, team_last_roster) table names to write.
    """
    keep = OFFSEASON_KEEP if offseason_keep is None else offseason_keep
    se_tbl, pe_tbl, tr_tbl = tables
    con.execute(f"CREATE TABLE IF NOT EXISTS {se_tbl}("
                "series_id INTEGER PRIMARY KEY, elo_a REAL, elo_b REAL,"
                " cont_a REAL, cont_b REAL)")
    con.execute(f"DELETE FROM {se_tbl}")
    con.execute(f"CREATE TABLE IF NOT EXISTS {pe_tbl}("
                "player_id INTEGER PRIMARY KEY, elo REAL, maps INTEGER)")
    con.execute(f"CREATE TABLE IF NOT EXISTS {tr_tbl}("
                "team_id INTEGER PRIMARY KEY, roster TEXT)")
    rows = con.execute(
        "SELECT id, event_id, date, stage, team_a, team_b, score_a, score_b, winner"
        " FROM series ORDER BY date, id").fetchall()
    # Late-group flags: last LATE_FRAC of matches by date within each
    # (event_id, group-ish stage). Knowable pre-match; no leakage.
    order: dict[int, int] = {}
    total: dict[tuple, int] = {}
    for sid, eid, date, stage, *_ in rows:
        if is_group_stage(stage):
            total[(eid, stage)] = total.get((eid, stage), 0) + 1
    seen: dict[tuple, int] = {}
    late: set[int] = set()
    for sid, eid, date, stage, *_ in rows:
        if is_group_stage(stage):
            key = (eid, stage)
            i = seen.get(key, 0)
            seen[key] = i + 1
            if i >= total[key] * (1.0 - LATE_FRAC):
                late.add(sid)
    stakes: set[int] = set()
    if STAKES_K_MULT != 1.0:
        stakes, _ = low_stakes_series(con)
    elo: dict[int, float] = {}
    played: dict[int, int] = {}
    last_roster: dict[int, set] = {}
    tenure: dict[int, float] = {}
    cur_year = None

    # ---- v4: Round Swing credit + agent baselines (prior years only) ----
    try:
        SWING = pickle.load(open(SWING_PKL, "rb"))
    except (OSError, pickle.PickleError):
        SWING = {}
    agent_base: dict[tuple[int, str], float] = {}
    if SWING:
        pag = con.execute(
            "SELECT s.date, pm.series_id, pm.game, pm.player_id, pm.agent"
            " FROM player_map pm JOIN series s ON s.id=pm.series_id").fetchall()
        by_ya: dict[tuple[int, str], list] = {}
        for date, sid0, game0, pid0, agent in pag:
            y0 = int(date[:4])
            ag0 = (agent or "").split(",")[0]
            sw0 = SWING.get((sid0, game0, pid0))
            if sw0 is not None:
                by_ya.setdefault((y0, ag0), []).append(sw0)
        years = sorted({y for y, _ in by_ya})
        for i, y in enumerate(years):
            agg: dict[str, list] = {}
            for (yy, ag), vals in by_ya.items():
                if yy < y:
                    agg.setdefault(ag, []).extend(vals)
            for ag, vals in agg.items():
                if len(vals) >= 30:
                    agent_base[(y, ag)] = sum(vals) / len(vals)

    def resid(sid: int, game: int, pid: int, agent: str, year: int) -> float:
        ag = (agent or "").split(",")[0]
        return SWING.get((sid, game, pid), 0.0) - agent_base.get((year, ag), 0.0)

    for sid, eid, date, stage, ta, tb, sa, sb, winner in rows:
        year, tier = event_meta[eid]
        if cur_year is None:
            cur_year = year
        if year != cur_year:  # offseason regression
            for p in elo:
                elo[p] = BASE_ELO + keep * (elo[p] - BASE_ELO)
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
        # ---- v4 performance weight: Round-Swing share (fallback: K-D+FK) ----
        pw: dict[int, float] = {}
        game_ids = [g for (g,) in con.execute(
            "SELECT DISTINCT game FROM player_map WHERE series_id=?", (sid,)).fetchall()]
        ag_of = {}
        for p, a, c in con.execute(
                "SELECT player_id, agent, COUNT(*) FROM player_map"
                " WHERE series_id=? GROUP BY player_id, agent", (sid,)).fetchall():
            if p not in ag_of or c > ag_of[p][1]:
                ag_of[p] = ((a or "").split(",")[0], c)
        ag_of = {p: a for p, (a, c) in ag_of.items()}
        has_swing = bool(SWING) and any(
            (sid, g, p) in SWING for p in ra | rb for g in game_ids)
        if has_swing:
            rs: dict[int, float] = {}
            for g in game_ids:
                for p in ra | rb:
                    rs[p] = rs.get(p, 0.0) + resid(sid, g, p, ag_of.get(p), year)
            for team, roster in ((ta, ra), (tb, rb)):
                vals = {p: rs.get(p, 0.0) for p in roster}
                mn = min(vals.values())
                sh = {p: v - min(0.0, mn) for p, v in vals.items()}
                tot = sum(sh.values())
                for p in roster:
                    s = (sh[p] / tot) if tot > 0 else 0.2
                    pw[p] = 0.5 + (s / 0.2) * 0.5
        else:
            perf = con.execute(
                "SELECT player_id, team_id, SUM(kills), SUM(deaths), SUM(fk) FROM player_map"
                " WHERE series_id=? GROUP BY player_id, team_id", (sid,)).fetchall()
            for team in (ta, tb):
                tot = sum(max((k or 0) - (d or 0), 0) + (f or 0)
                          for p, t, k, d, f in perf if t == team)
                for p, t, k, d, f in perf:
                    if t == team:
                        s = max((k or 0) - (d or 0), 0) + (f or 0)
                        pw[p] = 0.5 + (s / tot if tot > 0 else 0.2) / 0.2 * 0.5

        def strength(roster: set, team: int) -> tuple[float, float]:
            # v4: empirical-Bayes shrinkage by prior maps played
            tot = 0.0
            for p in roster:
                n = played.get(p, 0)
                w = n / (n + SHRINK_K)
                tot += BASE_ELO + (elo.get(p, BASE_ELO) - BASE_ELO) * w
            m = tot / len(roster)
            prev = last_roster.get(team)
            cont = len(roster & prev) / 5.0 if prev else 0.0
            return m - CHEM_PENALTY * (1.0 - cont), cont

        ea, ca = strength(ra, ta)
        eb, cb = strength(rb, tb)
        exp_a = expected(ea, eb)
        res_a = 1.0 if winner == "A" else 0.0
        k = stage_k(stage) * TIER_W[tier] * k_mult
        if sid in late:
            k *= LATE_K_MULT
        if sid in stakes:
            k *= STAKES_K_MULT
        for p in ra:
            pk = k * (1.5 if played.get(p, 0) < PROV_MAPS_V4 else 1.0)
            elo[p] = elo.get(p, BASE_ELO) + pk * pw.get(p, 1.0) * (res_a - exp_a) * share.get(p, 1.0)
            played[p] = played.get(p, 0) + n_maps
        for p in rb:
            pk = k * (1.5 if played.get(p, 0) < PROV_MAPS_V4 else 1.0)
            elo[p] = elo.get(p, BASE_ELO) + pk * pw.get(p, 1.0) * ((1.0 - res_a) - (1.0 - exp_a)) * share.get(p, 1.0)
            played[p] = played.get(p, 0) + n_maps
        last_roster[ta], last_roster[tb] = ra, rb
        con.execute(f"INSERT INTO {se_tbl} VALUES(?,?,?,?,?)", (sid, ea, eb, ca, cb))
    con.execute(f"DELETE FROM {pe_tbl}")
    con.executemany(f"INSERT INTO {pe_tbl} VALUES(?,?,?)",
                    [(p, e, played.get(p, 0)) for p, e in elo.items()])
    con.execute(f"DELETE FROM {tr_tbl}")
    con.executemany(f"INSERT INTO {tr_tbl} VALUES(?,?)",
                    [(t, ",".join(map(str, r))) for t, r in last_roster.items()])
    con.commit()
    return {p: (e, played.get(p, 0)) for p, e in elo.items()}


def current_strengths(con: sqlite3.Connection,
                      pe_tbl: str = "player_elo",
                      tr_tbl: str = "team_last_roster") -> dict[int, float]:
    """Mean player Elo per team from last seen roster. Unchanged roster = no chem dock."""
    elos = dict(con.execute(f"SELECT player_id, elo FROM {pe_tbl}").fetchall())
    out = {}
    for tid, r in con.execute(f"SELECT team_id, roster FROM {tr_tbl}").fetchall():
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
