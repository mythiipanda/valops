"""FeatureRow builder. All features are A-minus-B diffs at match time."""

import math
import sqlite3

import pandas as pd

from . import config as C
from .elo import K_PLAYOFF, stage_k
from .mapcomp import ROLE

# ponytail: O(n^2) history scans, fine to ~10k series; vectorize if past that
PRIOR_MAPS, PRIOR_H2H, MIN_MAPS = 5, 3, 3
STATS = ("rating", "acs", "kast", "adr", "fkfd")
EXTRA = ("form", "winrate", "h2h", "lan", "duel", "cov", "rest", "sos")
RT = ("rt_pistol", "rt_retake")
ALL = ("elo",) + STATS + EXTRA + RT


def load(con: sqlite3.Connection):
    s = pd.read_sql("SELECT * FROM series ORDER BY date, id", con, parse_dates=["date"])
    m = pd.read_sql("SELECT * FROM maps", con)
    pm = pd.read_sql("SELECT * FROM player_map", con)
    ev = {e: (y, t, r) for e, y, t, r in C.EVENTS}
    s["tier"] = s["event_id"].map(lambda e: ev[e][1])
    sw = s.set_index("id")[["team_a", "team_b", "winner", "date", "event_id", "stage"]]
    m = m.rename(columns={"winner": "winner_map"})
    tm = pm.merge(m[["series_id", "game", "winner_map"]], on=["series_id", "game"])
    tm = tm.merge(sw, left_on="series_id", right_index=True)
    tm["map_win"] = (((tm["winner_map"] == "A") & (tm["team_id"] == tm["team_a"])) |
                     ((tm["winner_map"] == "B") & (tm["team_id"] == tm["team_b"]))).astype(int)
    tm["fkfd"] = tm["fk"] - tm["fd"]
    try:
        ms = pd.read_sql("SELECT * FROM map_sides", con)
        mc = pd.read_sql("SELECT * FROM map_comp", con)
        md = s.set_index("id")["date"]
        ms["date"] = ms["series_id"].map(md)
        mc["date"] = mc["series_id"].map(md)
    except Exception:
        ms = pd.DataFrame()
        mc = pd.DataFrame()
    try:
        me = pd.read_sql("SELECT * FROM map_eco", con)
        me["date"] = me["series_id"].map(md)
        cl = pd.read_sql("SELECT * FROM series_clutch", con)
        cl["date"] = cl["series_id"].map(md)
    except Exception:
        me = pd.DataFrame()
        cl = pd.DataFrame()
    try:
        meta = pd.read_sql("SELECT * FROM series_meta", con)
    except Exception:
        meta = pd.DataFrame()
    se0 = pd.read_sql("SELECT series_id, elo_a, elo_b FROM series_elo", con)
    ss = s[["id", "date", "team_a", "team_b"]].merge(se0, left_on="id", right_on="series_id")
    try:
        from .traits import load_rounds
        _r = load_rounds(con)
        _smap = s.set_index("id")[["team_a", "team_b"]]
        rdict = {}
        for tid in pd.concat([s["team_a"], s["team_b"]]).unique():
            sids = set(_smap[(_smap["team_a"] == tid) | (_smap["team_b"] == tid)].index)
            rdict[tid] = _r[_r["series_id"].isin(sids)]
    except Exception:
        rdict = {}
    return (s.sort_values(["date", "id"]).reset_index(drop=True),
            tm.sort_values(["date", "series_id"]).reset_index(drop=True), ev, ms, mc, me, cl,
            meta.set_index("series_id") if len(meta) else meta, ss, rdict)


def side(tm, ev, tid, opp_a: int, opp_b: int, date, ms=None, mc=None,
         me=None, cl=None, meta=None, sid=None, cur_patch=None, ss=None,
         tr=None) -> dict:
    """Aggregate one team's history before date. opp pair scopes h2h."""
    h = tm[(tm["team_id"] == tid) & (tm["date"] < date)]
    h60 = h[h["date"] > date - pd.Timedelta(days=60)]
    h90 = h[h["date"] > date - pd.Timedelta(days=90)]
    nm = len(h60)
    d = {s: (h60[s].mean() if nm >= MIN_MAPS else None) for s in STATS}
    mw = h60["map_win"].sum() if nm else 0
    d["form"] = (mw + 0.5 * PRIOR_MAPS) / (nm + PRIOR_MAPS)
    sw90 = h90["series_id"].nunique()
    d["winrate"] = ((h90[h90["map_win"] == 1]["series_id"].nunique() + 0.5 * PRIOR_MAPS)
                    / (sw90 + PRIOR_MAPS))
    h2 = h[((h["team_a"] == opp_a) & (h["team_b"] == opp_b)) |
           ((h["team_a"] == opp_b) & (h["team_b"] == opp_a))]
    h2 = h2[h2["date"] > date - pd.Timedelta(days=365)]
    d["h2h"] = (h2["map_win"].sum() + 0.5 * PRIOR_H2H) / (len(h2) + PRIOR_H2H)
    if ms is not None and len(ms) and mc is not None and len(mc):
        mh = ms[(ms["team_id"] == tid) & (ms["date"] < date) &
                (ms["date"] > date - pd.Timedelta(days=60))]
        if len(mh):
            ch = mc[(mc["team_id"] == tid) & (mc["date"] < date) &
                    (mc["date"] > date - pd.Timedelta(days=60))]
            d["duel"] = ch["n_duel"].mean() if len(ch) else None
            if len(ch) >= 3:
                cov = ch["agents"].map(
                    lambda a: 1 if {"D", "C", "I", "S"} <=
                    {ROLE.get(x, "?") for x in a.split(",") if x} else 0)
                d["cov"] = (cov.sum() + 1.0) / (len(ch) + 2)
                roles = ch["agents"].map(
                    lambda a: {ROLE.get(x, "?") for x in a.split(",") if x})
                d["noctl"] = ((roles.map(lambda r: "C" not in r)).sum() + 0.5) / (len(ch) + 1)
                d["dduel"] = ((ch["n_duel"] >= 2).sum() + 0.5) / (len(ch) + 1)
            else:
                d["cov"] = d["noctl"] = d["dduel"] = None
        else:
            d["duel"] = d["cov"] = d["noctl"] = d["dduel"] = None
    else:
        d["duel"] = d["cov"] = d["noctl"] = d["dduel"] = None
    if me is not None and len(me):
        eh = me[(me["team_id"] == tid) & (me["date"] < date) &
                (me["date"] > date - pd.Timedelta(days=90))]
        pw, pn = eh["pistol_w"].sum(), eh["pistol_n"].sum()
        d["pistol"] = (pw + 1.0) / (pn + 2) if pn >= 6 else None
        fw, fn = eh["full_w"].sum(), eh["full_n"].sum()
        d["full"] = (fw + 11.0) / (fn + 20) if fn >= 20 else None
        lw, ln = eh["light_w"].sum(), eh["light_n"].sum()
        d["eco"] = (lw + 2.0) / (ln + 8) if ln >= 8 else None
    else:
        d["pistol"] = d["full"] = d["eco"] = None
    if cl is not None and len(cl):
        ch = cl[(cl["team_id"] == tid) & (cl["date"] < date) &
                (cl["date"] > date - pd.Timedelta(days=120))]
        ns = ch["series_id"].nunique()
        if ns >= 3:
            nmaps = tm[(tm["team_id"] == tid) & (tm["date"] < date) &
                       (tm["date"] > date - pd.Timedelta(days=120))]["series_id"].nunique()
            d["clutch"] = (ch["clutch"].sum() + 0.5) / (max(nmaps, 1) + 1)
        else:
            d["clutch"] = None
    else:
        d["clutch"] = None
    th = tm[(tm["team_id"] == tid) & (tm["date"] < date)]
    if len(th):
        last = th["date"].max()
        d["rest"] = min((date - last).total_seconds() / 86400.0, 21.0)
    else:
        d["rest"] = None
    if ss is not None and len(ss):
        sh = ss[((ss["team_a"] == tid) | (ss["team_b"] == tid)) & (ss["date"] < date) &
                (ss["date"] > date - pd.Timedelta(days=90))]
        if len(sh):
            opp = [r["elo_b"] if r["team_a"] == tid else r["elo_a"]
                   for _, r in sh.iterrows()]
            d["sos"] = sum(opp) / len(opp)
        else:
            d["sos"] = None
    else:
        d["sos"] = None
    if tr and tid in tr:
        try:
            from .traits import team_traits as _tt
            _td = _tt(tr[tid], tid, date)
            for _k, _v in _td.items():
                d["rt_" + _k] = _v
        except Exception:
            for _k in ("bounce", "snow", "retake", "ot", "half2", "force", "pistol"):
                d["rt_" + _k] = None
    else:
        for _k in ("bounce", "snow", "retake", "ot", "half2", "force", "pistol"):
            d["rt_" + _k] = None
    intl = h[h["date"] > date - pd.Timedelta(days=730)]
    mask = (intl["event_id"].map(lambda e: ev[e][1] != "regional")).astype(bool)
    intl = intl[mask]
    d["lan"] = math.log1p(intl["series_id"].nunique()) if len(intl) else 0.0
    return d


def diffs(a: dict, b: dict, elo_diff: float, elo_fast_diff: float = 0.0) -> dict:
    d = {"elo_diff": elo_diff, "elo_fast_diff": elo_fast_diff}
    for stat in STATS + EXTRA + RT:
        x, y = a[stat], b[stat]
        d[f"{stat}_diff"] = 0.0 if x is None or y is None else x - y
    # conviction: elo edge and recent form pointing the same way
    d["elo_x_form_diff"] = d["elo_diff"] * d["form_diff"] / 100.0
    return d


def build(con: sqlite3.Connection) -> pd.DataFrame:
    s, tm, ev, ms, mc, me, cl, meta, ss, tr = load(con)
    se = pd.read_sql("SELECT * FROM series_elo", con).set_index("series_id")
    se_f = pd.read_sql("SELECT * FROM series_elo_fast", con).set_index("series_id")
    out = []
    for _, row in s.iterrows():
        sid, date = row["id"], row["date"]
        cur = None
        bo = 3
        if len(meta) and sid in meta.index:
            cur = meta.loc[sid, "patch"] or None
            try:
                bo = int(meta.loc[sid, "best_of"] or 3)
            except (TypeError, ValueError):
                bo = 3
        a = side(tm, ev, row["team_a"], row["team_a"], row["team_b"], date,
                 ms, mc, me, cl, meta, sid, cur, ss, tr)
        b = side(tm, ev, row["team_b"], row["team_a"], row["team_b"], date,
                 ms, mc, me, cl, meta, sid, cur, ss, tr)
        ed = se.loc[sid, "elo_a"] - se.loc[sid, "elo_b"] if sid in se.index else 0.0
        ef = se_f.loc[sid, "elo_a"] - se_f.loc[sid, "elo_b"] if sid in se_f.index else 0.0
        feats = diffs(a, b, ed, ef)
        feats["favlen_diff"] = (1 if ed > 0 else -1) * (bo - 3)
        ipo = 1 if stage_k(row.get("stage") or "") == K_PLAYOFF else 0
        feats["elopo_diff"] = ed * ipo
        out.append({"series_id": sid, "date": date, "event_id": row["event_id"],
                    "team_a": row["team_a"], "team_b": row["team_b"],
                    "label": 1 if row["winner"] == "A" else 0,
                    **feats})
    return pd.DataFrame(out).sort_values("series_id").reset_index(drop=True)


def matchup(con: sqlite3.Connection, ta: int, tb: int, date, elo_diff: float,
            elo_fast_diff: float = 0.0, is_po: int = 0) -> dict:
    """Feature diffs for a hypothetical fixture."""
    _, tm, ev, ms, mc, me, cl, meta, ss, tr = load(con)
    a = side(tm, ev, ta, ta, tb, date, ms, mc, me, cl, meta, None, None, ss, tr)
    b = side(tm, ev, tb, ta, tb, date, ms, mc, me, cl, meta, None, None, ss, tr)
    d = diffs(a, b, elo_diff, elo_fast_diff)
    d["favlen_diff"] = 0  # BO3 default pre-match
    d["elopo_diff"] = elo_diff * is_po
    return d
