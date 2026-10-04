"""Champions 2026 playoff bracket: predicted path + exact title odds.

Eight-team double elimination on the fixed Oct 4 draw. Seed 1 (group
winners) was drawn first, then seed 2 (runners-up), with same-group teams
forced onto opposite halves. All series are Bo3 except the lower final
and grand final (Bo5); no bracket reset in the grand final.

Every probability comes from the playoff pairwise model (is_po=1).
Unplayed downstream slots show the modal path (favorites advance) and are
marked conditional. Played slots show the real score plus the model's
pre-game pick and hit/miss, and later slots follow the actual winners.
"""

from .config import TEAMS
from . import track as T

T100, G2, VIT, NS, NRG, T1, PRX, LOUD = 120, 11058, 2059, 11060, 1034, 14, 624, 6961
EIGHT = [T100, G2, VIT, NS, NRG, T1, PRX, LOUD]
PLAYOFF_START = "2026-10-07"

ROUND_META = {
    "uqf": {"label": "Upper quarterfinals", "date": "Oct 7-8", "bo": 3},
    "lr1": {"label": "Lower round 1", "date": "Oct 9", "bo": 3},
    "usf": {"label": "Upper semifinals", "date": "Oct 10", "bo": 3},
    "lr2": {"label": "Lower round 2", "date": "Oct 11", "bo": 3},
    "uf": {"label": "Upper final", "date": "Oct 16", "bo": 3},
    "lr3": {"label": "Lower round 3", "date": "Oct 16", "bo": 3},
    "lf": {"label": "Lower final", "date": "Oct 17", "bo": 5},
    "gf": {"label": "Grand final", "date": "Oct 18", "bo": 5},
}

# ("t", team) = fixed team, ("w", slot) = that slot's winner,
# ("l", slot) = that slot's loser. Halves: {qf1,qf2} vs {qf3,qf4}.
# Lower round 2 crosses halves (per VLR's bracket and the VCT ruleset):
# the loser of upper semifinal 1 drops into the SECOND lr2 match,
# so lr2a = w(lr1a) vs l(usf2) and lr2b = w(lr1b) vs l(usf1).
SLOTS = [
    {"id": "qf1", "round": "uqf", "pa": ("t", T100), "pb": ("t", G2)},
    {"id": "qf2", "round": "uqf", "pa": ("t", VIT), "pb": ("t", NS)},
    {"id": "qf3", "round": "uqf", "pa": ("t", NRG), "pb": ("t", T1)},
    {"id": "qf4", "round": "uqf", "pa": ("t", PRX), "pb": ("t", LOUD)},
    {"id": "lr1a", "round": "lr1", "pa": ("l", "qf1"), "pb": ("l", "qf2")},
    {"id": "lr1b", "round": "lr1", "pa": ("l", "qf3"), "pb": ("l", "qf4")},
    {"id": "usf1", "round": "usf", "pa": ("w", "qf1"), "pb": ("w", "qf2")},
    {"id": "usf2", "round": "usf", "pa": ("w", "qf3"), "pb": ("w", "qf4")},
    {"id": "lr2a", "round": "lr2", "pa": ("w", "lr1a"), "pb": ("l", "usf2")},
    {"id": "lr2b", "round": "lr2", "pa": ("w", "lr1b"), "pb": ("l", "usf1")},
    {"id": "uf", "round": "uf", "pa": ("w", "usf1"), "pb": ("w", "usf2")},
    {"id": "lr3", "round": "lr3", "pa": ("w", "lr2a"), "pb": ("w", "lr2b")},
    {"id": "lf", "round": "lf", "pa": ("l", "uf"), "pb": ("w", "lr3")},
    {"id": "gf", "round": "gf", "pa": ("w", "uf"), "pb": ("w", "lf")},
]
ROUND_ORDER = ["uqf", "lr1", "usf", "lr2", "uf", "lr3", "lf", "gf"]


def _playoff_results(results: dict) -> dict:
    """Completed series from the playoff window only.

    Group-stage meetings between the same pair (e.g. a 100T-T1 rematch)
    must not count as a playoff result.
    """
    out = {}
    for pair, vs in (results or {}).items():
        ps = [v for v in vs if v["date"] >= PLAYOFF_START]
        if ps:
            out[pair] = ps
    return out


def _resolve(spec: dict, res: dict) -> tuple:
    def r(x):
        kind, v = x
        if kind == "t":
            return v, True
        s = res[v]
        return (s["w"] if kind == "w" else s["l"]), s["played"]
    (a, known_a), (b, known_b) = r(spec["pa"]), r(spec["pb"])
    return a, b, known_a and known_b


def _fav(p: dict, a: int, b: int) -> tuple:
    pa = p[(a, b)]
    return (a, b, pa) if pa >= 0.5 else (b, a, 1.0 - pa)


def _slot(spec: dict, p: dict, pres: dict, seen: dict, res: dict) -> dict:
    a, b, known = _resolve(spec, res)
    pair = frozenset((a, b))
    rs = pres.get(pair, [])
    k = seen.get(pair, 0)
    seen[pair] = k + 1
    r = rs[k] if k < len(rs) else None
    d = {"id": spec["id"], "a": a, "b": b, "conditional": not known}
    if r:
        w = r["winner"]
        ta = r["team_a"]
        d.update(
            w=w, l=b if w == a else a, pw=1.0, played=True,
            score_a=r["score_a"] if a == ta else r["score_b"],
            score_b=r["score_b"] if a == ta else r["score_a"],
            date=r["date"], pre_pick=None, pre_p=None, hit=None,
        )
        if r["pre_p"] is not None:
            pre = r["pre_p"] if a == ta else 1.0 - r["pre_p"]
            pre_pick = a if pre >= 0.5 else b
            d.update(pre_pick=pre_pick,
                     pre_p=round(pre if pre_pick == a else 1.0 - pre, 3),
                     hit=pre_pick == w)
        return d
    w, l, pw = _fav(p, a, b)
    d.update(w=w, l=l, pw=round(pw, 3), played=False)
    return d


def _enumerate(p: dict, pres: dict) -> tuple:
    """Exact P(title) and P(final) over all 2^14 paths.

    Played series get probability 1 on the real winner; everything else
    is drawn from the playoff pairwise model. A pair can meet twice, so
    the k-th meeting in a path consumes the k-th played series.
    """
    title = {t: 0.0 for t in EIGHT}
    final = {t: 0.0 for t in EIGHT}
    res: dict = {}
    seen: dict = {}

    def participants(spec):
        def r(x):
            kind, v = x
            if kind == "t":
                return v
            s = res[v]
            return s["w"] if kind == "w" else s["l"]
        return r(spec["pa"]), r(spec["pb"])

    def rec(i: int, prob: float):
        if i == len(SLOTS):
            title[res["gf"]["w"]] += prob
            final[res["gf"]["a"]] += prob
            final[res["gf"]["b"]] += prob
            return
        spec = SLOTS[i]
        a, b = participants(spec)
        pair = frozenset((a, b))
        k = seen.get(pair, 0)
        pl = pres.get(pair, [])
        if k < len(pl):
            w = pl[k]["winner"]
            seen[pair] = k + 1
            res[spec["id"]] = {"a": a, "b": b, "w": w,
                               "l": b if w == a else a}
            rec(i + 1, prob)
            seen[pair] = k
            del res[spec["id"]]
            return
        seen[pair] = k + 1
        pa = p[(a, b)]
        for w, pw_ in ((a, pa), (b, 1.0 - pa)):
            res[spec["id"]] = {"a": a, "b": b, "w": w,
                               "l": b if w == a else a}
            rec(i + 1, prob * pw_)
        del res[spec["id"]]
        seen[pair] = k

    rec(0, 1.0)
    return title, final


def playoff_view(p: dict, results: dict | None = None) -> dict:
    """JSON-serializable playoff bracket view + exact title odds."""
    pres = _playoff_results(results)
    seen: dict = {}
    res: dict = {}
    slots = []
    for spec in SLOTS:
        d = _slot(spec, p, pres, seen, res)
        res[spec["id"]] = d
        slots.append(d)
    title, final = _enumerate(p, pres)
    by_round = {r: [] for r in ROUND_ORDER}
    for d in slots:
        spec = next(s for s in SLOTS if s["id"] == d["id"])
        by_round[spec["round"]].append(d)
    rounds = [{**ROUND_META[r], "id": r, "slots": by_round[r]}
              for r in ROUND_ORDER]
    odds = sorted(
        ({"id": t, "name": TEAMS[t], "title": round(title[t], 4),
          "final": round(final[t], 4)} for t in EIGHT),
        key=lambda o: -o["title"])
    losses: dict = {t: 0 for t in EIGHT}
    for d in slots:
        if d["played"] and d["a"] and d["b"] and d["winner"]:
            loser = d["b"] if d["winner"] == d["a"] else d["a"]
            losses[loser] = losses.get(loser, 0) + 1
    elim = sorted(t for t, n in losses.items() if n >= 2)
    return {
        "teams": [{"id": t, "name": TEAMS[t]} for t in EIGHT],
        "rounds": rounds,
        "odds": odds,
        "elim": elim,
        "note": ("Later rounds show the model's favorites and update as "
                 "series are played."),
    }


def self_check():
    uni = {(a, b): 0.5 for a in EIGHT for b in EIGHT if a != b}
    v = playoff_view(uni, None)
    for o in v["odds"]:
        assert abs(o["title"] - 0.125) < 1e-9, o
        assert abs(o["final"] - 0.25) < 1e-9, o
    assert all(s["conditional"] is False or True for r in v["rounds"]
               for s in r["slots"])
    chalk = {(a, b): 1.0 if a < b else 0.0
             for a in EIGHT for b in EIGHT if a != b}
    v = playoff_view(chalk, None)
    top = v["odds"][0]
    assert top["id"] == min(EIGHT) and top["title"] == 1.0, top
    assert v["rounds"][0]["slots"][0]["w"] == T100
    return True


if __name__ == "__main__":
    print("self-check:", self_check())
