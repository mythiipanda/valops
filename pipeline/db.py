"""SQLite schema + helpers. One writer, stdlib only."""

import json
import sqlite3
import time
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS series(
  id INTEGER PRIMARY KEY, event_id INTEGER, date TEXT, stage TEXT,
  team_a INTEGER, team_b INTEGER, score_a INTEGER, score_b INTEGER, winner TEXT);
CREATE TABLE IF NOT EXISTS maps(
  series_id INTEGER, game INTEGER, map_name TEXT,
  a_rounds INTEGER, b_rounds INTEGER, winner TEXT,
  PRIMARY KEY(series_id, game));
CREATE TABLE IF NOT EXISTS player_map(
  series_id INTEGER, game INTEGER, player_id INTEGER, name TEXT, team_id INTEGER,
  agent TEXT, rating REAL, acs REAL, kills INTEGER, deaths INTEGER, assists INTEGER,
  kast REAL, adr REAL, fk INTEGER, fd INTEGER, rounds INTEGER,
  PRIMARY KEY(series_id, game, player_id));
CREATE TABLE IF NOT EXISTS player_elo(player_id INTEGER PRIMARY KEY, elo REAL, maps INTEGER);
CREATE TABLE IF NOT EXISTS team_last_roster(team_id INTEGER PRIMARY KEY, roster TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def write_status(con: sqlite3.Connection, path: str = "data/status.json"):
    """Manifest: table counts + event coverage + timestamp. Fast resume checks."""
    st: dict = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for t in ("series", "maps", "player_map", "map_eco", "series_clutch",
              "round_detail", "player_swing", "map_swing"):
        try:
            st[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            st[t] = 0
    try:
        st["events"] = [r[0] for r in
                        con.execute("SELECT DISTINCT event_id FROM series ORDER BY 1")]
    except Exception:
        st["events"] = []
    Path(path).write_text(json.dumps(st, indent=1))
    return st
