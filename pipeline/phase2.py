"""Phase 2: rounds-adjacent data for 2025-2026. economy per map + performance per series."""

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vlrdevapi import VLRClient

from .db import connect

WORKERS, RPS = 4, 2.0
YEARS = (2025, 2026)
_local = threading.local()


def _client():
    if not getattr(_local, "c", None):
        _local.c = VLRClient(requests_per_second=RPS)
    return _local.c


def _cache(path: Path, fetch):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            path.unlink(missing_ok=True)
    data = fetch()
    try:
        obj = json.loads(data)
    except TypeError:
        obj = json.loads(data.model_dump_json())
        data = data.model_dump_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return obj


def _dump(obj) -> str:
    try:
        return obj.model_dump_json()
    except AttributeError:
        return json.dumps(obj, default=str)


def _fetch(args):
    sid, ngames, raw = args
    out = {"eco": [], "perf": None}
    c = _client()
    for order in range(1, ngames + 1):
        try:
            out["eco"].append(_cache(
                raw / f"economy_{sid}_{order}.json",
                lambda: _dump(c.series(sid).economy(game_id=order))))
        except Exception:
            pass
        try:
            out["perf"] = _cache(raw / f"perf_{sid}.json",
                                 lambda: _dump(c.series(sid).performance()))
        except Exception:
            pass
    return sid, out


def run(con: sqlite3.Connection, db_events=None):
    con.execute("CREATE TABLE IF NOT EXISTS map_eco(series_id INTEGER, game INTEGER,"
                " team_id INTEGER, pistol_w INTEGER, pistol_n INTEGER,"
                " full_w INTEGER, full_n INTEGER, light_w INTEGER, light_n INTEGER,"
                " PRIMARY KEY(series_id, game, team_id))")
    con.execute("CREATE TABLE IF NOT EXISTS series_clutch(series_id INTEGER, player_id INTEGER,"
                " team_id INTEGER, clutch INTEGER, multi INTEGER,"
                " PRIMARY KEY(series_id, player_id))")
    if db_events is None:
        import pipeline.config as C
        ev = {e: (y, t) for e, y, t, _ in C.EVENTS}
    else:
        ev = db_events
    rows = con.execute(
        "SELECT s.id, s.event_id, s.team_a, s.team_b, COUNT(m.game)"
        " FROM series s JOIN maps m ON m.series_id=s.id"
        " GROUP BY s.id").fetchall()
    raw = Path("data/raw")
    pending = [(sid, n, raw) for sid, eid, _, _, n in rows
               if ev.get(eid, (0, ""))[0] in YEARS
               and not con.execute("SELECT 1 FROM series_clutch WHERE series_id=?",
                                   (sid,)).fetchone()]
    print(f"phase2 pending series: {len(pending)}", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sid, res in ex.map(_fetch, pending):
            t = con.execute("SELECT team_a, team_b FROM series WHERE id=?", (sid,)).fetchone()
            if not t:
                continue
            ta, tb = t
            ids = {}
            for e in res["eco"]:
                g = e.get("game_id", 0)
                try:
                    order = int(g) if str(g).isdigit() else 0
                except Exception:
                    order = 0
                teams = {ta: [0, 0, 0, 0, 0, 0], tb: [0, 0, 0, 0, 0, 0]}
                for r in e.get("rounds", []):
                    w = (r.get("winner") or {}).get("id")
                    b1, b2 = r.get("buy_type_team1", ""), r.get("buy_type_team2", "")
                    # map buy types to each team by id match on round? eco payload has
                    # team1/team2 ids at top level
                    t1, t2 = e.get("team1_id"), e.get("team2_id")
                    for tid, bt in ((t1, b1), (t2, b2)):
                        if tid not in teams:
                            continue
                        v = teams[tid]
                        if r.get("is_pistol_round"):
                            v[1] += 1
                            v[0] += (w == tid)
                        elif (bt or "").lower().startswith("full"):
                            v[3] += 1
                            v[2] += (w == tid)
                        else:
                            v[5] += 1
                            v[4] += (w == tid)
                    ids[(order, tid)] = True
                for tid, v in teams.items():
                    if tid in (ta, tb):
                        con.execute("INSERT OR IGNORE INTO map_eco VALUES(?,?,?,?,?,?,?,?,?)",
                                    (sid, order, tid, v[0], v[1], v[2], v[3], v[4], v[5]))
            pf = res["perf"]
            if pf:
                for a in (pf.get("adv_stats") or []):
                    pid = a.get("player_id")
                    cl = sum(a.get(f"one_v{i}") or 0 for i in (1, 2))
                    mu = sum(a.get(f"{k}_k") or 0 for k in ("two", "three", "four", "five"))
                    tm = con.execute(
                        "SELECT team_id FROM player_map WHERE series_id=? AND player_id=? LIMIT 1",
                        (sid, pid)).fetchone()
                    if tm:
                        con.execute("INSERT OR IGNORE INTO series_clutch VALUES(?,?,?,?,?)",
                                    (sid, pid, tm[0], cl, mu))
            con.commit()
    con.commit()
