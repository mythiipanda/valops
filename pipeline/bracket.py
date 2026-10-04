"""GSL group simulator. Exact advancement odds from pairwise probabilities."""

from .config import GROUPS, TEAMS


def _p(p, a, b):
    return p[(a, b)]


def group_probs(teams: list[int], p: dict, fixed: dict | None = None) -> dict:
    """Exact P(advance), P(advance 2-0), P(advance 2-1) for a 4-team GSL group.

    fixed maps frozenset({a, b}) -> list of winners in chronological order
    for series already played; those branches get probability 1 instead of
    being re-drawn, so the odds condition on real results. A pair can meet
    twice (opener + decider rematch): the k-th meeting in a bracket path
    consumes the k-th fixed winner, and meetings past the fixed list are
    simulated fresh.
    """
    fixed = fixed or {}
    t0, t1, t2, t3 = teams
    openers = (frozenset((t0, t1)), frozenset((t2, t3)))
    out = {t: {"adv": 0.0, "first": 0.0, "second": 0.0} for t in teams}

    def opts(a: int, b: int, occ: int = 0):
        fl = fixed.get(frozenset((a, b)))
        if fl is not None and occ < len(fl):
            return ((fl[occ], 1.0),)
        return ((a, _p(p, a, b)), (b, _p(p, b, a)))

    # openers: t0 vs t1, t2 vs t3
    for w1, pw1 in opts(t0, t1):
        l1 = t1 if w1 == t0 else t0
        for w2, pw2 in opts(t2, t3):
            l2 = t3 if w2 == t2 else t2
            base = pw1 * pw2
            # winners final
            for adv1, paw_ in opts(w1, w2):
                paw = paw_ * base
                out[adv1]["adv"] += paw
                out[adv1]["first"] += paw
                los = w2 if adv1 == w1 else w1
                # elimination: l1 vs l2, winner faces los in decider
                for elim_win, pel_ in opts(l1, l2):
                    pel = pel_ * paw
                    # decider: los vs elim_win. A decider can rematch an
                    # opener pair; only treat it as decided when a second
                    # series for the pair was actually played.
                    dec_pair = frozenset((los, elim_win))
                    occ = 1 if dec_pair in openers else 0
                    for adv2, pad_ in opts(los, elim_win, occ=occ):
                        pad = pad_ * pel
                        out[adv2]["adv"] += pad
                        out[adv2]["second"] += pad
    return out


def _fav(p: dict, a: int, b: int) -> tuple:
    """Modal winner of a vs b: (winner, loser, P(winner beats loser))."""
    pa = _p(p, a, b)
    return (a, b, pa) if pa >= 0.5 else (b, a, 1.0 - pa)


def bracket_view(teams: list[int], p: dict, results: dict | None = None) -> dict:
    """Human-readable bracket: openers with probs + advancement odds,
    plus the modal GSL path (favorites win every match).

    results maps frozenset({a, b}) -> list of completed-series infos in
    chronological order (see track.results_map). Slots whose series has
    been played are annotated
    with the actual score and the model's pre-game call, and later slots
    use the actual winners/losers as participants instead of the modal
    favorites — so the bracket follows reality where it's known.
    """
    results = results or {}
    t0, t1, t2, t3 = teams
    fixed = {k: [v["winner"] for v in vs] for k, vs in results.items()}
    probs = group_probs(teams, p, fixed)
    seen: dict = {}

    def slot(a: int, b: int) -> dict:
        # A pair can appear twice in the bracket (opener + decider
        # rematch). The k-th slot for a pair shows the k-th played series;
        # slots past the played list render as predictions, never as a
        # replay of an earlier series.
        pair = frozenset((a, b))
        rs = results.get(pair, [])
        k = seen.get(pair, 0)
        seen[pair] = k + 1
        r = rs[k] if k < len(rs) else None
        if r:
            ta = r["team_a"]
            w = r["winner"]
            sa = r["score_a"] if a == ta else r["score_b"]
            sb = r["score_b"] if a == ta else r["score_a"]
            d = {"a": a, "b": b, "w": w, "l": b if w == a else a,
                 "pw": 1.0, "played": True,
                 "score_a": sa, "score_b": sb, "date": r["date"],
                 "pre_pick": None, "pre_p": None, "hit": None}
            if r["pre_p"] is not None:
                pre = r["pre_p"] if a == ta else 1.0 - r["pre_p"]
                pre_pick = a if pre >= 0.5 else b
                d.update(pre_pick=pre_pick,
                         pre_p=round(pre if pre_pick == a else 1.0 - pre, 3),
                         hit=pre_pick == w)
            return d
        w, l, pw = _fav(p, a, b)
        return {"a": a, "b": b, "w": w, "l": l, "pw": round(pw, 3),
                "played": False}

    o1, o2 = slot(t0, t1), slot(t2, t3)
    winners = slot(o1["w"], o2["w"])
    elim = slot(o1["l"], o2["l"])
    decider = slot(winners["l"], elim["w"])
    gsl = {"openers": [o1, o2], "winners": winners, "elim": elim,
           "decider": decider}
    return {
        "teams": [{"id": t, "name": TEAMS[t]} for t in teams],
        "openers": [
            {"a": t0, "b": t1, "p": round(_p(p, t0, t1), 4)},
            {"a": t2, "b": t3, "p": round(_p(p, t2, t3), 4)},
        ],
        "odds": [{"id": t, "name": TEAMS[t],
                  "adv": round(probs[t]["adv"], 4),
                  "first": round(probs[t]["first"], 4),
                  "second": round(probs[t]["second"], 4)} for t in teams],
        "gsl": gsl,
    }


def simulate_all(p: dict, results: dict | None = None) -> dict:
    return {g: bracket_view(ts, p, results) for g, ts in GROUPS.items()}


def self_check():
    """Uniform probs -> every team advances exactly 0.5. Chalk check included."""
    uni = {(a, b): 0.5 for a in TEAMS for b in TEAMS if a != b}
    for g, ts in GROUPS.items():
        for t, o in group_probs(list(ts), uni).items():
            assert abs(o["adv"] - 0.5) < 1e-9, (g, t, o)
            assert abs(o["first"] - 0.25) < 1e-9, (g, t, o)
    ids = sorted(TEAMS)
    chalk = {(a, b): 1.0 if a < b else 0.0 for a in TEAMS for b in TEAMS if a != b}
    o = group_probs(ids[:4], chalk)
    assert o[ids[0]]["first"] == 1.0 and o[ids[1]]["second"] == 1.0, o
    return True


if __name__ == "__main__":
    print("self-check:", self_check())
