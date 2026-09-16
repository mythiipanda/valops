"""Side splits + comp roles from cached JSON. Zero new fetching."""

import json
import sqlite3
from pathlib import Path

ROLE = {
    "Jett": "D", "Reyna": "D", "Raze": "D", "Yoru": "D", "Phoenix": "D",
    "Neon": "D", "Iso": "D", "Waylay": "D",
    "Omen": "C", "Brimstone": "C", "Viper": "C", "Astra": "C",
    "Harbor": "C", "Clove": "C", "Miks": "C",
    "Sova": "I", "Breach": "I", "Skye": "I", "KAYO": "I", "Kayo": "I",
    "Fade": "I", "Gekko": "I", "Tejo": "I",
    "Killjoy": "S", "Cypher": "S", "Sage": "S", "Chamber": "S",
    "Deadlock": "S", "Vyse": "S", "Veto": "S",
}


def run(con: sqlite3.Connection):
    con.execute("CREATE TABLE IF NOT EXISTS map_sides(series_id INTEGER, game INTEGER,"
                " team_id INTEGER, atk_w INTEGER, atk_n INTEGER,"
                " def_w INTEGER, def_n INTEGER, PRIMARY KEY(series_id, game, team_id))")
    con.execute("CREATE TABLE IF NOT EXISTS map_comp(series_id INTEGER, game INTEGER,"
                " team_id INTEGER, agents TEXT, n_duel INTEGER,"
                " PRIMARY KEY(series_id, game, team_id))")
    con.execute("DELETE FROM map_sides")
    con.execute("DELETE FROM map_comp")
    n = 0
    for p in Path("data/raw").glob("series_*.json"):
        try:
            info = json.loads(p.read_text())
        except Exception:
            continue
        t1, t2 = info["team1"]["id"], info["team2"]["id"]
        for g in info["games"]:
            if not g.get("played"):
                continue
            o = g["order"]
            a1, d1 = g.get("team1_attack_rounds") or 0, g.get("team1_defense_rounds") or 0
            a2, d2 = g.get("team2_attack_rounds") or 0, g.get("team2_defense_rounds") or 0
            con.execute("INSERT OR IGNORE INTO map_sides VALUES(?,?,?,?,?,?,?)",
                        (info["series_id"], o, t1, a1, a1 + d2, d1, d1 + a2))
            con.execute("INSERT OR IGNORE INTO map_sides VALUES(?,?,?,?,?,?,?)",
                        (info["series_id"], o, t2, a2, a2 + d1, d2, d2 + a1))
            n += 1
    for p in Path("data/raw").glob("players_*.json"):
        try:
            parts = p.stem.split("_")
            sid, order = int(parts[1]), int(parts[2])
            d = json.loads(p.read_text())
        except Exception:
            continue
        for side in ("team1", "team2"):
            tp = d[side]
            ags = sorted(a for pl in tp["players"] for a in (pl.get("agents") or []))
            con.execute("INSERT OR IGNORE INTO map_comp VALUES(?,?,?,?,?)",
                        (sid, order, tp["team_id"], ",".join(ags),
                         sum(1 for a in ags if ROLE.get(a) == "D")))
    con.commit()
    return n
