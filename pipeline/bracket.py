"""GSL group simulator. Exact advancement odds from pairwise probabilities."""

import itertools

from .config import GROUPS, TEAMS


def _p(p, a, b):
    return p[(a, b)]


def group_probs(teams: list[int], p: dict) -> dict:
    """Exact P(advance), P(advance 2-0), P(advance 2-1) for a 4-team GSL group."""
    t0, t1, t2, t3 = teams
    out = {t: {"adv": 0.0, "first": 0.0, "second": 0.0} for t in teams}
    # openers: t0v t1, t2 vs t3
    for w1 in (t0, t1):
        pw1 = _p(p, w1, t1 if w1 == t0 else t0)
        l1 = t1 if w1 == t0 else t0
        for w2 in (t2, t3):
            pw2 = _p(p, w2, t3 if w2 == t2 else t2)
            l2 = t3 if w2 == t2 else t2
            base = pw1 * pw2
            # winners final
            for adv1 in (w1, w2):
                paw = _p(p, adv1, w2 if adv1 == w1 else w1) * base
                out[adv1]["adv"] += paw
                out[adv1]["first"] += paw
                los = w2 if adv1 == w1 else w1
                # elimination: l1 vs l2, winner faces los in decider
                for elim_win in (l1, l2):
                    pel = _p(p, elim_win, l2 if elim_win == l1 else l1) * paw
                    # decider: los vs elim_win
                    for adv2 in (los, elim_win):
                        pad = _p(p, adv2, elim_win if adv2 == los else los) * pel
                        out[adv2]["adv"] += pad
                        out[adv2]["second"] += pad
    return out


def bracket_view(teams: list[int], p: dict) -> dict:
    """Human-readable bracket: openers with probs + advancement odds."""
    t0, t1, t2, t3 = teams
    probs = group_probs(teams, p)
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
    }


def simulate_all(p: dict) -> dict:
    return {g: bracket_view(ts, p) for g, ts in GROUPS.items()}


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
