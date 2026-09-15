"""Export data.json for the one-page frontend."""

import json
import sqlite3
from pathlib import Path

from .config import GROUPS, TEAMS


def export(con: sqlite3.Connection, clf, coefs, reports, sim, pairwise_p,
           factors, swing_board, bracket_view, date: str, out: str = "site/public/data.json"):
    elos = dict(con.execute("SELECT player_id, elo FROM player_elo").fetchall())
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
    teams = [{"id": t, "name": TEAMS[t],
              "title": round(sim["title"][t], 4), "advance": round(sim["advance"][t], 4),
              "roster": rosters.get(t, [])} for t in TEAMS]
    teams.sort(key=lambda t: -t["title"])
    matchups = [{"a": a, "b": b, "p": round(p, 4),
                 "factors": factors.get((a, b), [])}
                for (a, b), p in sorted(pairwise_p.items())]
    for _s in swing_board:
        _s["champs"] = _s["player_id"] in champs
    payload = {"as_of": date,
               "groups": {g: [{"id": t, "name": TEAMS[t]} for t in ts]
                          for g, ts in GROUPS.items()},
               "teams": teams, "matchups": matchups,
               "coefs": [{"f": f, "w": round(float(w), 4)} for f, w in coefs],
               "validation": reports,
               "swing": swing_board,
               "bracket": bracket_view}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(payload))
    return payload
