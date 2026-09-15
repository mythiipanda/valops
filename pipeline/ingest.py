"""vlrdevapi -> SQLite. Cached raw JSON, resumable, concurrent fetch + serial writes."""

import json
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vlrdevapi import VLRClient

from .config import EVENTS, RAW_DIR
from .db import connect

WORKERS, RPS = 4, 2.0
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
    data = fetch()  # client owns retry + backoff
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return json.loads(data)


def _dump(obj) -> str:
    try:
        return obj.model_dump_json()
    except AttributeError:
        return json.dumps(obj, default=str)


def _fetch_series(args):
    sid, m, t1, t2, raw = args
    time.sleep(random.uniform(0, 0.2))
    c = _client()
    info = _cache(raw / f"series_{sid}.json", lambda: _dump(c.series(sid).info()))
    games = [g for g in info["games"] if g.get("played")]
    if not games:
        return None
    out = []
    for order, g in enumerate(games, 1):
        pm = _cache(raw / f"players_{sid}_{order}.json",
                    lambda: _dump(c.series(sid).players(game_id=order)))
        out.append((g, pm))
    return (m, t1, t2, info, out)


def ingest_event(con: sqlite3.Connection, event_id: int) -> tuple[int, int]:
    raw = Path(RAW_DIR)
    with VLRClient(requests_per_second=RPS) as c:
        matches = _cache(raw / f"event_{event_id}.json", lambda: _dump(c.event(event_id).matches()))
    pending = []
    for m in matches["matches"]:
        if m.get("status") != "completed" or len(m.get("teams", [])) != 2:
            continue
        sid = m["match_id"]
        if con.execute("SELECT 1 FROM series WHERE id=?", (sid,)).fetchone():
            continue
        pending.append((sid, m, m["teams"][0], m["teams"][1], raw))
    n_series = n_maps = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for res in ex.map(_fetch_series, pending):
            if res is None:
                continue
            m, t1, t2, info, games = res
            sid = m["match_id"]
            if con.execute("SELECT 1 FROM series WHERE id=?", (sid,)).fetchone():
                continue
            dt = str(info.get("datetime") or m.get("datetime_utc") or m.get("match_date"))
            winner = "A" if info["score1"] > info["score2"] else "B"
            con.execute(
                "INSERT INTO series VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, event_id, dt, info.get("stage") or m.get("stage"),
                 t1["id"], t2["id"], info["score1"], info["score2"], winner))
            n_series += 1
            ra, rb = set(), set()
            for order, (g, pm) in enumerate(games, 1):
                con.execute(
                    "INSERT OR IGNORE INTO maps VALUES(?,?,?,?,?,?)",
                    (sid, order, g["map_name"], g["team1_score"], g["team2_score"],
                     "A" if g["team1_score"] > g["team2_score"] else "B"))
                n_maps += 1
                rnds = g["team1_score"] + g["team2_score"]
                for side, key in (("team1", ra), ("team2", rb)):
                    tp = pm[side]
                    for p in tp["players"]:
                        st = p["stats"]["overall"]
                        key.add(p["player_id"])
                        con.execute(
                            "INSERT OR IGNORE INTO player_map VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (sid, order, p["player_id"], p["name"], tp["team_id"],
                             ",".join(p.get("agents") or []),
                             st.get("rating"), st.get("acs"), st.get("kills"),
                             st.get("deaths"), st.get("assists"), st.get("kast"),
                             st.get("adr"), st.get("first_kills"), st.get("first_deaths"),
                             rnds))
            con.execute("INSERT OR REPLACE INTO team_last_roster VALUES(?,?)",
                        (t1["id"], ",".join(map(str, ra))))
            con.execute("INSERT OR REPLACE INTO team_last_roster VALUES(?,?)",
                        (t2["id"], ",".join(map(str, rb))))
            con.commit()
    return n_series, n_maps


def run(event_ids=None, db_path=None):
    con = connect(db_path) if db_path else connect()
    ids = event_ids or [e[0] for e in EVENTS]
    for eid in ids:
        ns, nm = ingest_event(con, eid)
        print(f"event {eid}: {ns} series, {nm} maps", flush=True)
    con.execute("INSERT OR REPLACE INTO meta VALUES('ingest_done','1')")
    con.commit()
    con.close()
