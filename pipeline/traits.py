"""Round-trait features from round_detail. All shrunk, all rolling."""

import sqlite3

import pandas as pd

TRAITS = ("bounce", "snow", "retake", "ot", "half2", "force", "pistol")


def load_rounds(con):
    r = pd.read_sql("SELECT * FROM round_detail", con)
    s = pd.read_sql("SELECT id, date FROM series", con, parse_dates=["date"])
    md = s.set_index("id")["date"]
    r["date"] = r["series_id"].map(md)
    return r.sort_values(["date", "series_id", "game", "round_no"]).reset_index(drop=True)


def build_monthly(con: sqlite3.Connection):
    """Precompute traits per team per month-end. Fast lookups for features."""
    con.execute("CREATE TABLE IF NOT EXISTS team_traits_monthly(team_id INTEGER, ym TEXT,"
                + "".join(f" {t} REAL," for t in TRAITS) + " PRIMARY KEY(team_id, ym))")
    con.execute("DELETE FROM team_traits_monthly")
    r = load_rounds(con)
    months = pd.period_range(r["date"].min(), r["date"].max(), freq="M")
    rows = 0
    for tid in pd.read_sql(
            "SELECT DISTINCT team_id FROM (SELECT team_a AS team_id FROM series"
            " UNION SELECT team_b FROM series)", con)["team_id"]:
        for me in months:
            d = team_traits(r, int(tid), me.end_time.tz_localize("UTC"))
            if any(v is not None for v in d.values()):
                con.execute("INSERT INTO team_traits_monthly VALUES(?,?,?,?,?,?,?, ?,?)",
                            (int(tid), str(me),
                             *[d[t] if d[t] is not None else None for t in TRAITS]))
                rows += 1
    con.commit()
    return rows


def lookup(con: sqlite3.Connection) -> dict:
    """(team_id, ym) -> {trait: value}."""
    out = {}
    for row in con.execute("SELECT * FROM team_traits_monthly").fetchall():
        out[(row[0], row[1])] = dict(zip(TRAITS, row[2:]))
    return out
    r = pd.read_sql("SELECT * FROM round_detail", con)
    s = pd.read_sql("SELECT id, date FROM series", con, parse_dates=["date"])
    md = s.set_index("id")["date"]
    r["date"] = r["series_id"].map(md)
    m = pd.read_sql("SELECT series_id, game, a_rounds, b_rounds FROM maps", con)
    m = m.set_index(["series_id", "game"])
    return r.sort_values(["date", "series_id", "game", "round_no"]).reset_index(drop=True)


def team_traits(r: pd.DataFrame, tid: int, date, days: int = 120) -> dict:
    """Rolling round traits before date. None when sample thin."""
    h = r[(r["date"] < date) & (r["date"] > date - pd.Timedelta(days=days))]
    if not len(h):
        return {t: None for t in TRAITS}
    h = h.sort_values(["date", "series_id", "game", "round_no"])
    h["mine"] = (h["winner_id"] == tid).astype(int)
    h["prev_mine"] = h.groupby(["series_id", "game"])["mine"].shift(1)
    d = {}
    bb = h[h["prev_mine"] == 0]
    d["bounce"] = ((bb["mine"].sum() + 3.0) / (len(bb) + 10)) if len(bb) >= 30 else None
    sn = h[h["prev_mine"] == 1]
    d["snow"] = ((sn["mine"].sum() + 3.0) / (len(sn) + 10)) if len(sn) >= 30 else None
    h["wt"] = h["win_type"].str.lower()
    dret = h[(h["wt"] == "defuse") & (h["winner_id"] == tid)]
    dw = h[(h["side"] == "Defense") & (h["winner_id"] == tid)]
    d["retake"] = ((len(dret) + 1.0) / (len(dw) + 5)) if len(dw) >= 20 else None
    ot = h[h["round_no"] > 24]
    d["ot"] = ((ot["winner_id"] == tid).sum() + 1.0) / (len(ot) + 3) if len(ot) >= 9 else None
    h2 = h[h["round_no"].between(13, 24)]
    d["half2"] = ((h2["winner_id"] == tid).sum() + 4.0) / (len(h2) + 8) if len(h2) >= 24 else None
    pistols = {}
    for (s_, g_), g in h.groupby(["series_id", "game"]):
        r1 = g[g["round_no"] == 1]
        r13 = g[g["round_no"] == 13]
        if len(r1):
            pistols[(s_, g_, 1)] = r1.iloc[0]["winner_id"]
        if len(r13):
            pistols[(s_, g_, 13)] = r13.iloc[0]["winner_id"]
    f = h[h["round_no"].isin((2, 3, 14, 15))].copy()
    f["half"] = f["round_no"].map(lambda x: 1 if x < 13 else 13)
    f["pw"] = f.apply(lambda x: pistols.get((x["series_id"], x["game"], x["half"])), axis=1)
    f = f[f["pw"].notna() & (f["pw"] != tid)]
    d["force"] = ((f["winner_id"] == tid).sum() + 1.0) / (len(f) + 4) if len(f) >= 12 else None
    p = h[h["round_no"].isin((1, 13))]
    d["pistol"] = ((p["winner_id"] == tid).sum() + 1.0) / (len(p) + 2) if len(p) >= 10 else None
    return d
