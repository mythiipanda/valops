"""Round-by-round ingest for all maps + economy backfill for 2023-2024."""

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vlrdevapi import VLRClient

from .db import connect
from .phase2 import _cache, _dump

WORKERS, RPS = 4, 2.0
_local = threading.local()


def _client():
    if not getattr(_local, "c", None):
        _local.c = VLRClient(requests_per_second=RPS)
    return _local.c


def _fetch_rounds(args):
    sid, order, raw = args
    c = _client()
    try:
        return sid, order, _cache(
            raw / f"rounds_{sid}_{order}.json",
            lambda: _dump(c.series(sid).rounds(game_id=order)))
    except Exception:
        return sid, order, None


def run_rounds(con: sqlite3.Connection):
    con.execute("CREATE TABLE IF NOT EXISTS round_detail(series_id INTEGER, game INTEGER,"
                " round_no INTEGER, winner_id INTEGER, win_type TEXT, side TEXT,"
                " PRIMARY KEY(series_id, game, round_no))")
    maps = con.execute(
        "SELECT m.series_id, m.game FROM maps m WHERE NOT EXISTS"
        " (SELECT 1 FROM round_detail r WHERE r.series_id=m.series_id AND r.game=m.game)").fetchall()
    print(f"rounds pending maps: {len(maps)}", flush=True)
    raw = Path("data/raw")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sid, order, res in ex.map(_fetch_rounds,
                                      [(s, g, raw) for s, g in maps]):
            if not res:
                continue
            for r in res.get("rounds", []):
                con.execute("INSERT OR IGNORE INTO round_detail VALUES(?,?,?,?,?,?)",
                            (sid, order, r.get("round_number"), (r.get("winner") or {}).get("id")
                             if isinstance(r.get("winner"), dict) else r.get("winner_team_id"),
                             r.get("win_type"), r.get("side")))
            con.commit()
    con.commit()


def run_eco_backfill(con: sqlite3.Connection):
    from . import phase2
    import pipeline.config as C
    ev = {e: (y, t) for e, y, t, _ in C.EVENTS}
    phase2.YEARS = (2023, 2024)
    phase2.run(con, ev)


if __name__ == "__main__":
    con = connect()
    run_rounds(con)
    run_eco_backfill(con)
    con.close()
