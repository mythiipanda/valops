"""Champions kill-feed backfill: thespike timelines -> v4 Round Swing.

Daily entry point: update_swing(con). Discovers new Champions matches on
thespike, fetches their kill timelines, and extends data/v4_swing.pkl with
raw per-(series, game, player) credit sums using the SAME computation as
v4_swing_build.py (the script that built the original pickle):

  - empirical P-table P(att win | alive_att, alive_def, planted, pistol),
    a 2023-2024 mechanics prior (no 2026 team leakage); small-n states are
    shrunk toward the marginal by (a,d) with w = n/(n+50)
  - per kill, ordered by timeline: dV = P_after - P_before (attacker's
    perspective); killer credit = +dV if killer attacking else -dV
  - victim debit = -(killer credit); kills only (plants/defuses move state
    but earn no direct credit); zero-sum per round by construction

Idempotent: series already present in the pickle are skipped, and /stats
JSON is cached to data/raw/thespike/<id>.json. One events/4233 request per
run for discovery; fully network-free when nothing new is finished.
"""
import json
import pickle
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .config import TEAMS
from .elo import SWING_PKL

PTABLE_PKL = Path(__file__).parent.parent / "data" / "v4_ptable.pkl"
RAW_DIR = Path(__file__).parent.parent / "data" / "raw" / "thespike"
MAP_PATH = Path(__file__).parent.parent / "data" / "track" / "vlr_to_spike.json"

SPIKE_EVENT_ID = 4233
CHAMPS_EVENT_ID = 2766
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"}
WIN = {1: "Spike detonation", 2: "Defuse", 3: "Elimination", 4: "Time out"}


def norm(s):
    if not isinstance(s, str):
        return ""
    # NFKD strips diacritics first ("LêwN" -> "LewN"), so unicode nicknames
    # match their ascii player_map spellings
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _get_json(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = Request(url, headers=UA)
            with urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as ex:
            last = ex
            time.sleep(2.0 * (i + 1))
    print(f"  !! GET {url}: {last}", flush=True)
    return None


def fetch_stats(mid):
    """thespike /stats JSON for a match id, cached to data/raw/thespike/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{mid}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            path.unlink()
    d = _get_json(f"https://api.thespike.gg/match/{mid}/stats")
    if d is not None:
        path.write_text(json.dumps(d))
    return d


def _iter_spike_matches(ev):
    """Yield finished match dicts from groups + brackets."""
    for g in ev.get("groups", []):
        for sec in ("winners", "losers"):
            for st in (g.get(sec) or {}).get("stages", []):
                yield from st.get("matches", [])
    for b in ev.get("brackets", []):
        for sec in ("winners", "middle", "losers"):
            for st in (b.get(sec) or {}).get("stages", []):
                yield from st.get("matches", [])


def discover_mappings(con):
    """Match finished thespike matches to our Champions series.

    Normalized unordered team-name pair + score multiset + start within
    25h of our series date. Persists new mappings into vlr_to_spike.json.
    Returns (mapping, report) where mapping is {vlr_sid: spike_id}.
    """
    mapping = {}
    if MAP_PATH.exists():
        mapping = {int(k): int(v) for k, v in json.loads(MAP_PATH.read_text()).items()}
    report = {"new": [], "ambiguous": [], "unmapped": []}

    ours = {}
    for sid, date, ta, tb, sa, sb in con.execute(
            "SELECT id, date, team_a, team_b, score_a, score_b FROM series"
            " WHERE event_id=? AND winner IN ('A','B')", (CHAMPS_EVENT_ID,)).fetchall():
        try:
            dt = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        ours[sid] = (dt, frozenset((norm(TEAMS[ta]), norm(TEAMS[tb]))),
                     tuple(sorted((sa, sb))))

    todo = [sid for sid in ours if sid not in mapping]
    if not todo:
        return mapping, report
    ev = _get_json(f"https://api.thespike.gg/events/{SPIKE_EVENT_ID}")
    if ev is None:
        report["unmapped"] = todo
        return mapping, report

    cands = []
    for m in _iter_spike_matches(ev):
        if not m.get("isFinished"):
            continue
        teams = m.get("teams") or []
        if len(teams) != 2 or any(not t.get("title") or t["title"] == "TBD"
                                 for t in teams):
            continue
        try:
            dt = datetime.fromisoformat(m["startTime"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        cands.append((m["id"], dt,
                      frozenset((norm(teams[0]["title"]), norm(teams[1]["title"]))),
                      tuple(sorted((teams[0].get("score") or 0,
                                    teams[1].get("score") or 0)))))

    for sid in todo:
        odt, opair, oscores = ours[sid]
        hits = [mid for mid, dt, pair, scores in cands
                if pair == opair and scores == oscores
                and abs((dt - odt).total_seconds()) <= 25 * 3600
                and mid not in mapping.values()]
        if len(hits) == 1:
            mapping[sid] = hits[0]
            report["new"].append((sid, hits[0]))
        elif len(hits) > 1:
            report["ambiguous"].append((sid, hits))
        else:
            report["unmapped"].append(sid)
    if report["new"]:
        MAP_PATH.write_text(json.dumps({str(k): v for k, v in sorted(mapping.items())}))
    return mapping, report


def _events_of_match(stats):
    """Flatten a /stats payload into timeline events.

    Yields (map_no, round_no, seq, killer, victim, event_type, side) in
    timeline order, mirroring spike_extract.py. map_no is 1-based.
    """
    for mi, m in enumerate(stats.get("maps", []), start=1):
        if not m.get("hasJson"):
            continue
        for rnd in m.get("rounds", []):
            rno = rnd.get("roundNo")
            for seq, e in enumerate(rnd.get("timeline", [])):
                killer = e.get("killer") or {}
                victim = e.get("victim") or {}
                planter = e.get("planter") or {}
                defuser = e.get("defuser") or {}
                if killer and victim:
                    yield (mi, rno, seq, killer.get("nickname"),
                           victim.get("nickname"), "kill", killer.get("team"))
                elif planter:
                    yield (mi, rno, seq, planter.get("nickname"), None,
                           "plant", planter.get("team"))
                elif defuser:
                    yield (mi, rno, seq, defuser.get("nickname"), None,
                           "defuse", defuser.get("team"))


def _load_ptable():
    pt = pickle.load(open(PTABLE_PKL, "rb"))
    P, marg = pt["P"], pt["marg"]

    def pwin(a, d, pl, pistol):
        return P.get((a, d, pl, pistol), marg.get((a, d), 0.5))

    return pwin


def compute_match_swing(vlr_sid, stats, pid_of, pwin):
    """v4 swing for one match. Returns ({(sid, game, pid): credit}, diag).

    pid_of: {(sid, game): {norm_name: player_id}} from player_map.
    A kill counts only when BOTH killer and victim map to a player_id;
    this keeps every round exactly zero-sum.
    """
    swing = {}
    diag = {"kills": 0, "skipped_unmapped": 0, "rounds": 0, "maps": 0}
    # group events by round, preserving timeline order
    rounds = {}
    for mi, rno, seq, k, v, etype, side in _events_of_match(stats):
        rounds.setdefault((mi, rno), []).append((seq, k, v, etype, side))
    diag["maps"] = len({mi for mi, _ in rounds})
    diag["rounds"] = len(rounds)
    for (mi, rno), evs in rounds.items():
        evs.sort(key=lambda e: e[0])
        aa, ad = 5, 5
        planted = False
        pistol = rno in (1, 13)
        pmap = pid_of.get((vlr_sid, mi), {})
        for _, k, v, etype, side in evs:
            pb = pwin(aa, ad, planted, pistol)
            if etype == "kill":
                if side == "attacking":
                    ad -= 1
                else:
                    aa -= 1
            elif etype == "plant":
                planted = True
            pa = pwin(aa, ad, planted, pistol)
            if etype != "kill":
                continue
            diag["kills"] += 1
            dv = pa - pb
            credit = dv if side == "attacking" else -dv
            pk = pmap.get(norm(k))
            pv = pmap.get(norm(v)) if v else None
            if pk is None or pv is None:
                diag["skipped_unmapped"] += 1
                continue
            swing[(vlr_sid, mi, pk)] = swing.get((vlr_sid, mi, pk), 0.0) + credit
            swing[(vlr_sid, mi, pv)] = swing.get((vlr_sid, mi, pv), 0.0) - credit
    return swing, diag


def _pid_lookup(con, sids):
    pid_of = {}
    for sid, game, pid, name in con.execute(
            "SELECT series_id, game, player_id, name FROM player_map"
            f" WHERE series_id IN ({','.join('?' * len(sids))})", tuple(sids)):
        pid_of.setdefault((sid, game), {})[norm(name)] = pid
    return pid_of


def update_swing(con):
    """Backfill v4 swing for Champions series missing from the pickle.

    Returns a stats dict. Backs up the pickle before writing.
    """
    stats = {"new_matches": 0, "new_entries": 0, "entries": 0,
             "skipped": [], "unmapped_kill_rate": None,
             "discovery": None}
    try:
        swing = pickle.load(open(SWING_PKL, "rb"))
    except (OSError, pickle.PickleError):
        swing = {}
    have = {(s, g) for s, g, _ in swing}
    sids = [r[0] for r in con.execute(
        "SELECT id FROM series WHERE event_id=? AND winner IN ('A','B')"
        " ORDER BY date, id", (CHAMPS_EVENT_ID,)).fetchall()]
    todo = [s for s in sids
            if not any((s, g) in have for g in (1, 2, 3, 4, 5))]
    mapping, dreport = discover_mappings(con)
    stats["discovery"] = {k: len(v) for k, v in dreport.items()}
    if not todo:
        stats["entries"] = len(swing)
        return stats

    pwin = _load_ptable()
    pid_of = _pid_lookup(con, todo)
    kills = skipped = 0
    for sid in todo:
        mid = mapping.get(sid)
        if mid is None:
            stats["skipped"].append((sid, "no thespike mapping"))
            continue
        d = fetch_stats(mid)
        if d is None:
            stats["skipped"].append((sid, f"fetch failed thespike {mid}"))
            continue
        ms, diag = compute_match_swing(sid, d, pid_of, pwin)
        kills += diag["kills"]
        skipped += diag["skipped_unmapped"]
        if not ms:
            stats["skipped"].append((sid, f"no kill events thespike {mid}"))
            continue
        # per-map zero-sum assert before merging
        zc = {}
        for (s, g, _p), v in ms.items():
            zc[(s, g)] = zc.get((s, g), 0.0) + v
        mx = max((abs(v) for v in zc.values()), default=0.0)
        assert mx < 1e-9, f"zero-sum violated sid={sid}: {mx}"
        swing.update(ms)
        stats["new_matches"] += 1
        stats["new_entries"] += len(ms)
        time.sleep(0.5)
    if stats["new_matches"]:
        bak = Path(str(SWING_PKL) + ".bak")
        if Path(SWING_PKL).exists():
            bak.write_bytes(Path(SWING_PKL).read_bytes())
        with open(SWING_PKL, "wb") as fh:
            pickle.dump(swing, fh)
    stats["entries"] = len(swing)
    stats["unmapped_kill_rate"] = (skipped / kills) if kills else 0.0
    return stats
