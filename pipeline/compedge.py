"""Per-map comp edges + team map strength. Re-runnable, auditable. No new fetching.

Pool A (map_comps): per map, per exact-5-agent comp: W-L, Elo-expected wins
from pre-series team Elo (series_elo, no leakage), edge = wins - expected,
shrunk by maps played. Mirrors (both teams same comp) excluded.
Pool B (team_map_strength): per team x map W-L + shrunk offset vs 50%,
reusing mapedge._off.

Two pools per stage: full 2026 season plus kickoff / stage1 / stage2 /
Champions. Output goes into data.json via export(); no stray files.
"""

import sqlite3

from .config import EVENTS, STAGE_POOLS
from .elo import expected
from .mapedge import _off

POOLS = STAGE_POOLS
POOL_LABELS = {
    "full": "Full 2026 season",
    "kickoff": "Kickoff 2026",
    "stage1": "Stage 1 2026",
    "stage2": "Stage 2 2026",
    "champions": "Champions Shanghai",
}

# Tier-1, data-driven: any 2026 event whose tier in EVENTS is masters-level
# or better counts; a team is tier-1 if it played at least one series there.
TIER1_EVENTS = [eid for eid, year, tier, _ in EVENTS
                if year == 2026 and tier in ("masters", "champions")]


def min_maps(n_pool_maps: int) -> int:
    return max(2, min(8, n_pool_maps // 150))


def shrunk(wins: float, exp: float, n: int, k: int = 6) -> float:
    return (wins - exp) * n / (n + k) if n else 0.0


def img_agents(agents: str) -> list[dict]:
    return [{"name": a, "img": f"/valops/img/agents/{a.lower()}.png"}
            for a in agents.split(",") if a]


def map_img(map_name: str) -> str:
    return f"/valops/img/maps/{map_name.lower()}.jpg"


def pool_rows(con: sqlite3.Connection, event_ids: list[int]) -> list:
    q = ",".join(map(str, event_ids))
    return con.execute(
        "SELECT s.id, s.date, s.team_a, s.team_b, m.game, m.map_name, m.winner,"
        " e.elo_a, e.elo_b, ca.agents, cb.agents"
        " FROM series s JOIN maps m ON m.series_id = s.id"
        " LEFT JOIN series_elo e ON e.series_id = s.id"
        " LEFT JOIN map_comp ca ON ca.series_id = s.id AND ca.game = m.game"
        " AND ca.team_id = s.team_a"
        " LEFT JOIN map_comp cb ON cb.series_id = s.id AND cb.game = m.game"
        " AND cb.team_id = s.team_b"
        f" WHERE s.event_id IN ({q}) ORDER BY s.date, s.id, m.game").fetchall()


def build_map_comps(con: sqlite3.Connection, names: dict) -> dict:
    pools = {}
    for pool, eids in POOLS.items():
        rows = pool_rows(con, eids)
        n_pool_maps = len(rows)
        cutoff = min_maps(n_pool_maps)
        audit = {"maps": n_pool_maps, "no_comp": 0, "no_elo": 0, "mirrors": 0}
        comps: dict = {}
        for sid, date, ta, tb, game, mp, winner, ea, eb, aga, agb in rows:
            if not aga or not agb:
                audit["no_comp"] += 1
                continue
            if ea is None or eb is None:
                audit["no_elo"] += 1
                continue
            if aga == agb:
                audit["mirrors"] += 1
                continue
            pa = expected(ea, eb)
            for tid, is_a, ag in ((ta, True, aga), (tb, False, agb)):
                won = (winner == "A") == is_a
                exp = pa if is_a else 1.0 - pa
                c = comps.setdefault((mp, ag), {"w": 0, "exp": 0.0,
                                                "teams": {}})
                c["w"] += 1 if won else 0
                c["exp"] += exp
                t = c["teams"].setdefault(tid, [0, 0])
                t[0 if won else 1] += 1
        by_map: dict = {}
        for (mp, ag), c in comps.items():
            n = sum(sum(v) for v in c["teams"].values())
            l = n - c["w"]
            if n < cutoff:
                continue
            edge = shrunk(c["w"], c["exp"], n)
            top = sorted(c["teams"].items(), key=lambda kv: (-kv[1][0], kv[1][1]))[:3]
            by_map.setdefault(mp, []).append({
                "map": mp, "map_img": map_img(mp),
                "agents": img_agents(ag),
                "edge": round(edge, 4), "edge_rate": round(edge / n, 4),
                "w": c["w"], "l": l, "maps": n,
                "expected": round(c["exp"], 2),
                "pick_rate": 0.0,
                "top_teams": [{"team": names.get(tid, f"team {tid}"),
                               "w": t[0], "l": t[1]} for tid, t in top],
            })
        analyzed = sum(len(v) for v in by_map.values())
        for mp, cards in by_map.items():
            maps_on_map = max(sum(1 for r in rows if r[5] == mp), 1)
            for card in cards:
                card["pick_rate"] = round(card["maps"] / maps_on_map, 4)
            cards.sort(key=lambda c: -c["edge"])
            by_map[mp] = [cards[0]]
        pools[pool] = {"label": POOL_LABELS[pool],
                       "n_series": con.execute(
                           f"SELECT COUNT(*) FROM series WHERE event_id IN "
                           f"({','.join(map(str, eids))})").fetchone()[0],
                       "n_maps": n_pool_maps, "min_maps": cutoff,
                       "analyzed": analyzed, "audit": audit,
                       "maps": [by_map[mp][0] for mp in sorted(by_map)]}
    return pools


def tier1_teams(con: sqlite3.Connection) -> set:
    """Team ids that played at least one series in a 2026 masters/champions event."""
    q = ",".join(map(str, TIER1_EVENTS))
    rows = con.execute(
        "SELECT team_a, team_b FROM series"
        f" WHERE event_id IN ({q})").fetchall()
    return {tid for ta, tb in rows for tid in (ta, tb)}


def build_team_strength(con: sqlite3.Connection, names: dict) -> dict:
    tier1 = tier1_teams(con)
    pools = {}
    for pool, eids in POOLS.items():
        q = ",".join(map(str, eids))
        rows = con.execute(
            "SELECT s.team_a, s.team_b, m.map_name, m.winner"
            " FROM series s JOIN maps m ON m.series_id = s.id"
            f" WHERE s.event_id IN ({q})").fetchall()
        wl: dict = {}
        for ta, tb, mp, winner in rows:
            for tid, is_a in ((ta, True), (tb, False)):
                won = (winner == "A") == is_a
                t = wl.setdefault((tid, mp), [0, 0])
                t[0 if won else 1] += 1
        maps = sorted({mp for _, mp, _ in [(r[0], r[2], r[3]) for r in rows]})
        tids = sorted({tid for tid, _ in wl})
        teams = []
        for tid in tids:
            cards = []
            for mp in maps:
                w, l = wl.get((tid, mp), [0, 0])
                cards.append({"map": mp, "w": w, "l": l, "n": w + l,
                              "off": round(_off(w, w + l), 4)})
            teams.append({"id": tid, "name": names.get(tid, f"team {tid}"),
                          "tier1": tid in tier1,
                          "maps": cards})
        teams.sort(key=lambda t: t["name"])
        pools[pool] = {"label": POOL_LABELS[pool],
                       "n_series": con.execute(
                           "SELECT COUNT(*) FROM series WHERE event_id IN "
                           f"({q})").fetchone()[0],
                       "n_maps": len(rows), "maps": maps, "teams": teams}
    return pools


def build(con: sqlite3.Connection, names: dict, as_of: str) -> dict:
    return {"map_comps": {"as_of": as_of,
                          "pools": build_map_comps(con, names)},
            "team_map_strength": {"as_of": as_of,
                                  "pools": build_team_strength(con, names)}}
