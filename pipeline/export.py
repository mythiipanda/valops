"""Export data.json for the one-page frontend."""

import glob
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from .config import EVENTS, GROUPS, TEAMS

STAGE2_EVENTS = [2977, 2976, 2776, 2978]
REGIONS = ("AM", "EMEA", "PAC", "CN")
RAW = Path(__file__).parent.parent / "data" / "raw"


def team_meta() -> tuple[dict, dict]:
    """team_id -> name, team_id -> region (most common 2026 regional league)."""
    ev_region = {e[0]: e[3] for e in EVENTS}
    ev_year = {e[0]: e[1] for e in EVENTS}
    names: dict = {}
    regs: dict = defaultdict(list)
    for f in glob.glob(str(RAW / "event_*.json")):
        try:
            d = json.load(open(f))
        except OSError:
            continue
        for m in d.get("matches", []):
            if m.get("status") != "completed" or len(m.get("teams", [])) != 2:
                continue
            eid = m["event_id"]
            for t in m["teams"]:
                names[t["id"]] = t["name"]
                regs[t["id"]].append((ev_year.get(eid), ev_region.get(eid)))
    regions = {}
    for tid, evs in regs.items():
        r26 = [rg for (yr, rg) in evs if yr == 2026 and rg in REGIONS]
        pool = r26 or [rg for (_, rg) in evs if rg in REGIONS]
        if pool:
            regions[tid] = Counter(pool).most_common(1)[0][0]
    return names, regions


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


def stage2_form(con) -> dict:
    """Stage-2-only team Elo: pre-Stage-2 slow rating, updated on Stage 2
    series alone with the fast (1.5x) K. Answers 'how good lately', not
    'how good all year'."""
    from .elo import expected, stage_k
    s2 = ",".join(map(str, STAGE2_EVENTS))
    priors: dict = {}
    for ta, tb, ea, eb in con.execute(
            "SELECT s.team_a, s.team_b, e.elo_a, e.elo_b FROM series s "
            "JOIN series_elo e ON e.series_id = s.id "
            f"WHERE s.event_id IN ({s2}) ORDER BY s.date, s.id"):
        priors.setdefault(ta, ea)
        priors.setdefault(tb, eb)
    form = dict(priors)
    for ta, tb, sa, sb, stage in con.execute(
            "SELECT team_a, team_b, score_a, score_b, stage FROM series "
            f"WHERE event_id IN ({s2}) ORDER BY date, id"):
        if sa == sb:
            continue
        fa, fb = form.get(ta, 1500.0), form.get(tb, 1500.0)
        exp_a = expected(fa, fb)
        res_a = 1.0 if sa > sb else 0.0
        k = stage_k(stage) * 1.5
        form[ta] = fa + k * (res_a - exp_a)
        form[tb] = fb + k * ((1.0 - res_a) - (1.0 - exp_a))
    return form


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
    team_names, team_region = team_meta()
    player_region = {}
    rosters_all = dict(con.execute(
        "SELECT team_id, roster FROM team_last_roster").fetchall())
    for tid, r in rosters_all.items():
        rg = team_region.get(tid)
        if rg:
            for p in r.split(","):
                if p.strip():
                    player_region[int(p)] = rg
    for _b in swing_boards.values():
        for _s in _b:
            _s["region"] = player_region.get(_s["player_id"])
    # all 2026 regional-league teams, for the full rankings table
    ev_year = {e[0]: e[1] for e in EVENTS}
    ev_region = {e[0]: e[3] for e in EVENTS}
    teams_2026 = set()
    for ta, tb, eid in con.execute("SELECT team_a, team_b, event_id FROM series"):
        if ev_year.get(eid) == 2026 and ev_region.get(eid) in REGIONS:
            teams_2026.add(ta)
            teams_2026.add(tb)
    s2form = stage2_form(con)
    all_teams = []
    for tid in teams_2026:
        if tid not in team_region:
            continue
        ps = [int(x) for x in (rosters_all.get(tid) or "").split(",") if x]
        if not ps:
            continue
        slow = sum(elos.get(p, 1500.0) for p in ps) / len(ps)
        form = s2form.get(tid, slow)
        all_teams.append({"id": tid, "name": team_names.get(tid, f"team {tid}"),
                          "region": team_region[tid], "group": team_group.get(tid),
                          "elo": round(slow, 1), "form": round(form, 1)})
    all_teams.sort(key=lambda t: -t["elo"])
    payload = {"as_of": date,
               "groups": {g: [{"id": t, "name": TEAMS[t]} for t in ts]
                          for g, ts in GROUPS.items()},
               "teams": teams, "matchups": matchups,
               "all_teams": all_teams,
               "coefs": [{"f": f, "w": round(float(w), 4)} for f, w in coefs],
               "validation": reports,
               "swing": swing_boards.get("all", []),
               "swing_boards": swing_boards,
               "bracket": bracket_view,
               "dna": team_dna(con)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(payload))
    return payload
