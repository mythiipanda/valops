"""Champions prediction tracking: snapshot pre-game odds, score results.

Each night the daily job:
  1. scores completed Champions series against the most recent pre-game
     snapshot (nothing here has seen the future)
  2. snapshots fresh matchup probabilities for the games still to come
Ledger lives in data/track/ledger.jsonl; snapshots in data/track/preds/<date>.json.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import TEAMS

CHAMPS_EVENT = 2766
TRACK_DIR = Path(__file__).parent.parent / "data" / "track"
PREDS_DIR = TRACK_DIR / "preds"
LEDGER = TRACK_DIR / "ledger.jsonl"
PUB_DATA = Path(__file__).parent.parent / "site" / "public" / "data.json"
ET = ZoneInfo("America/New_York")


def _game_date(dt: str) -> str:
    """Calendar date of a game in ET.

    DB timestamps are UTC, but snapshots are stamped by the nightly job's
    local (ET) date and created ~11:30pm ET that day. Dating games in ET
    keeps the strictly-before rule honest: a game starting 10pm ET Sep 25
    is a Sep-25 game (scored vs a snapshot predating Sep 25), even though
    its UTC timestamp reads Sep 26 — where the Sep-25 snapshot (built after
    the game ended) would otherwise leak in.
    """
    try:
        d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(ET).date().isoformat()
    except Exception:
        return (dt or "")[:10]


def _seed_snapshot():
    """First-run seed: the published data.json (as_of 2026-09-17) holds
    genuine pre-tournament matchup probabilities."""
    d = json.loads(PUB_DATA.read_text())
    PREDS_DIR.mkdir(parents=True, exist_ok=True)
    out = {f"{m['a']}-{m['b']}": m["p"] for m in d["matchups"]}
    (PREDS_DIR / f"{d['as_of']}.json").write_text(
        json.dumps({"as_of": d["as_of"], "p": out}))


def _snapshots():
    if not PREDS_DIR.exists() or not list(PREDS_DIR.glob("*.json")):
        _seed_snapshot()
    return sorted(PREDS_DIR.glob("*.json"))


def snapshot(pairwise_p: dict, date: str):
    """Save tonight's matchup probabilities for the games still to come."""
    PREDS_DIR.mkdir(parents=True, exist_ok=True)
    out = {f"{a}-{b}": round(p, 4) for (a, b), p in pairwise_p.items()}
    (PREDS_DIR / f"{date}.json").write_text(json.dumps({"as_of": date, "p": out}))
    return date


def pred_for(team_a: int, team_b: int, game_date: str):
    """Most recent snapshot strictly before game_date. Returns (p_a, snap_date).

    Strictly-before keeps it honest: a snapshot stamped with the same date
    as the game may have been built after the game was played (the nightly
    job snapshots after scoring). A snapshot from an earlier date is
    guaranteed to predate the game.
    """
    best = None
    for f in _snapshots():
        d = f.stem
        if d < game_date and (best is None or d > best[0]):
            best = (d, json.loads(f.read_text())["p"])
    if best is None:
        return None, None
    snap_date, pmap = best
    p = pmap.get(f"{team_a}-{team_b}")
    if p is None:  # zero-sum model: p(b,a) = 1 - p(a,b), but store both anyway
        p = 1.0 - pmap.get(f"{team_b}-{team_a}", 0.5)
    return p, snap_date


def score(con: sqlite3.Connection) -> int:
    """Append ledger entries for completed Champions series not yet scored."""
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    done = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["series_id"])
    rows = con.execute(
        "SELECT id, date, team_a, team_b, winner FROM series "
        "WHERE event_id=? AND winner IN ('A','B') ORDER BY date, id",
        (CHAMPS_EVENT,)).fetchall()
    new = 0
    today = datetime.now(ET).date().isoformat()
    with LEDGER.open("a") as fh:
        for sid, dt, ta, tb, w in rows:
            if sid in done:
                continue
            gdate = _game_date(dt)
            # never score a game dated in the future: the source sometimes
            # marks unplayed bracket matches completed with placeholder scores
            if gdate > today:
                continue
            p, snap = pred_for(ta, tb, gdate)
            if p is None:
                continue
            actual = 1.0 if w == "A" else 0.0
            entry = {
                "series_id": sid, "date": gdate,
                "team_a": ta, "team_b": tb,
                "team_a_name": TEAMS.get(ta, str(ta)),
                "team_b_name": TEAMS.get(tb, str(tb)),
                "p_a": round(p, 4),
                "pred": "A" if p >= 0.5 else "B",
                "pred_name": TEAMS.get(ta) if p >= 0.5 else TEAMS.get(tb),
                "winner": w,
                "winner_name": TEAMS.get(ta) if w == "A" else TEAMS.get(tb),
                "correct": (p >= 0.5) == (w == "A"),
                "brier": round((p - actual) ** 2, 4),
                "snapshot": snap,
            }
            fh.write(json.dumps(entry) + "\n")
            new += 1
    return new


def results_map(con: sqlite3.Connection) -> dict:
    """Completed Champions series keyed by frozenset({team_a, team_b}).

    Each value is a LIST of series dicts in chronological order. A pair
    can meet twice in a GSL group (opener + decider rematch), so
    consumers must match the k-th bracket slot for a pair against the
    k-th series — never assume one pair means one series.

    Each series dict carries the actual result plus the model's pre-game
    probability (from the snapshot preceding the game), so the bracket
    can show 'model said X before, here's what happened' instead of a
    stale-looking prediction for a decided series.
    """
    out = {}
    for sid, dt, ta, tb, sa, sb, w in con.execute(
            "SELECT id, date, team_a, team_b, score_a, score_b, winner "
            "FROM series WHERE event_id=? AND winner IN ('A','B') "
            "ORDER BY date, id",
            (CHAMPS_EVENT,)).fetchall():
        gdate = _game_date(dt)
        pre_p, snap = pred_for(ta, tb, gdate)
        out.setdefault(frozenset((ta, tb)), []).append({
            "series_id": sid, "team_a": ta, "team_b": tb,
            "score_a": sa, "score_b": sb,
            "winner": ta if w == "A" else tb,
            "date": gdate, "pre_p": pre_p, "snapshot": snap,
        })
    return out


def summary() -> dict:
    """Rolling track record for the site."""
    games = []
    if LEDGER.exists():
        games = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    n = len(games)
    right = sum(1 for g in games if g["correct"])
    return {
        "n": n, "right": right, "wrong": n - right,
        "acc": round(right / n, 4) if n else None,
        "brier": round(sum(g["brier"] for g in games) / n, 4) if n else None,
        "games": games,
    }
