"""Export data.json for the one-page frontend."""

import json
import sqlite3
from pathlib import Path

import pandas as pd

from .config import GROUPS, TEAMS

STAGE2_EVENTS = [2977, 2976, 2776, 2978]


def team_dna(con: sqlite3.Connection) -> list:
    """Stage-2 round-level fingerprints for the Champions field."""
    rd = pd.read_sql("SELECT * FROM round_detail", con)
    s = pd.read_sql("SELECT id, team_a, team_b, event_id FROM series", con)
    rd = rd[rd["series_id"].isin(s[s["event_id"].isin(STAGE2_EVENTS)]["id"])]
    rd = rd.merge(s[["id", "team_a", "team_b"]], left_on="series_id", right_on="id")
    rd["loser_id"] = rd.apply(
        lambda r: r["team_b"] if r["winner_id"] == r["team_a"] else r["team_a"], axis=1)
    w = rd[["series_id", "game", "round_no", "side", "winner_id"]].copy()
    w.columns = ["series_id", "game", "round_no", "side", "team"]
    w["won"] = 1
    l = rd[["series_id", "game", "round_no", "side", "loser_id"]].copy()
    l.columns = ["series_id", "game", "round_no", "side", "team"]
    l["won"] = 0
    l["side"] = l["side"].map({"Attack": "Defense", "Defense": "Attack"})
    tr = pd.concat([w, l], ignore_index=True)

    out = []
    for tid in TEAMS:
        d = tr[tr["team"] == tid].sort_values(["series_id", "game", "round_no"])
        if len(d) < 100:
            continue
        d = d.copy()
        d["opp_won"] = 1 - d["won"]
        d["us"] = d.groupby(["series_id", "game"])["won"].cumsum()
        d["them"] = d.groupby(["series_id", "game"])["opp_won"].cumsum()
        d["margin"] = d["us"] - d["them"]
        g = d.groupby(["series_id", "game"])
        trailed4 = g["margin"].min() <= -4
        led4 = g["margin"].max() >= 4
        last = g[["us", "them"]].last()
        map_won = last["us"] > last["them"]
        out.append({
            "id": tid, "name": TEAMS[tid], "rounds": len(d),
            "atk": round(d[d["side"] == "Attack"]["won"].mean() * 100, 1),
            "dfn": round(d[d["side"] == "Defense"]["won"].mean() * 100, 1),
            "pistol": round(d[d["round_no"].isin([1, 13])]["won"].mean() * 100, 1),
            "h1": round(d[d["round_no"] <= 12]["won"].mean() * 100, 1),
            "h2": round(d[(d["round_no"] > 12) & (d["round_no"] <= 24)]["won"].mean() * 100, 1),
            "ot": round((d["round_no"] > 24).mean() * 100, 1),
            "comeback": round((trailed4 & map_won).mean() * 100, 1),
            "choke": round((led4 & ~map_won).mean() * 100, 1),
        })
    return out


def export(con: sqlite3.Connection, clf, coefs, reports, sim, pairwise_p,
           factors, swing_boards, bracket_view, date: str, out: str = "site/public/data.json"):
    elos = dict(con.execute("SELECT player_id, elo FROM player_elo").fetchall())
    elos_fast = dict(con.execute("SELECT player_id, elo FROM player_elo_fast").fetchall())
    team_group = {t: g for g, ts in GROUPS.items() for t in ts}
    champs = set()
    for tid, r in con.execute("SELECT team_id, roster FROM team_last_roster").fetchall():
        if tid in TEAMS:
            champs.update(int(x) for x in r.split(",") if x)
    rosters = {}
    for tid, r in con.execute("SELECT team_id, roster FROM team_last_roster").fetchall():
        if tid in TEAMS:
            ps = [int(x) for x in r.split(",") if x]
            rosters[tid] = [{"id": p, "elo": round(elos.get(p, 1500.0), 1),
                             "champs": p in champs}
                            for p in ps]
    names = con.execute(
        "SELECT player_id, name FROM player_map GROUP BY player_id").fetchall()
    nm = dict(names)
    for tid, ps in rosters.items():
        for p in ps:
            p["name"] = nm.get(p["id"], "?")
    teams = []
    for t in TEAMS:
        ps = rosters.get(t, [])
        slow = sum(p["elo"] for p in ps) / len(ps) if ps else 1500.0
        fast = sum(elos_fast.get(p["id"], 1500.0) for p in ps) / len(ps) if ps else 1500.0
        teams.append({"id": t, "name": TEAMS[t],
                      "title": round(sim["title"][t], 4),
                      "advance": round(sim["advance"][t], 4),
                      "elo": round(slow, 1), "fast": round(fast, 1),
                      "group": team_group.get(t), "roster": ps})
    teams.sort(key=lambda t: -t["title"])
    matchups = [{"a": a, "b": b, "p": round(p, 4),
                 "factors": factors.get((a, b), [])}
                for (a, b), p in sorted(pairwise_p.items())]
    for _b in swing_boards.values():
        for _s in _b:
            _s["champs"] = _s["player_id"] in champs
    payload = {"as_of": date,
               "groups": {g: [{"id": t, "name": TEAMS[t]} for t in ts]
                          for g, ts in GROUPS.items()},
               "teams": teams, "matchups": matchups,
               "coefs": [{"f": f, "w": round(float(w), 4)} for f, w in coefs],
               "validation": reports,
               "swing": swing_boards.get("all", []),
               "swing_boards": swing_boards,
               "bracket": bracket_view,
               "dna": team_dna(con)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(payload))
    return payload
