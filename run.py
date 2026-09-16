"""CLI: python3 run.py ingest | elo | train | sim | export | all [--event ID]"""

import argparse
import json
import sys

from pipeline import elo as E
from pipeline import export as X
from pipeline import features as F
from pipeline import ingest as I
from pipeline import model as M
from pipeline import swing as S
from pipeline.config import EVENTS, FAST_K_MULT, FAST_TABLES
from pipeline.db import connect

META = {e: (y, t) for e, y, t, _ in EVENTS}
DATE = "2026-09-16"


def cmd_ingest(args):
    ids = [args.event] if args.event else None
    I.run(event_ids=ids)


def cmd_elo(_):
    con = connect()
    final = E.run(con, META)
    E.run(con, META, k_mult=FAST_K_MULT, offseason_keep=1.0, tables=FAST_TABLES)
    elos = sorted(final.values(), key=lambda v: -v[0])[:10]
    print(f"players tracked: {len(final)}")
    con.close()


def cmd_train(_):
    con = connect()
    df = F.build(con)
    df.to_pickle("data/features.pkl")
    for r in M.evaluate(df):
        print(r)
    con.close()


def cmd_sim(_):
    import pandas as pd
    con = connect()
    df = pd.read_pickle("data/features.pkl")
    clf, coefs = M.fit_all(df)
    print("coefs:", [(f, round(float(w), 3)) for f, w in coefs])
    p, factors = M.pairwise(con, clf, DATE, is_po=0)
    ppo, _ = M.pairwise(con, clf, DATE, is_po=1)
    sim = M.simulate(p, ppo)
    for t in sorted(sim["title"], key=lambda t: -sim["title"][t]):
        from pipeline.config import TEAMS
        print(f"{TEAMS[t]:20s} title {sim['title'][t]:.3f}  advance {sim['advance'][t]:.3f}")
    from pipeline import bracket as B
    boards = {
        "all": S.round_swing_board(con, "2026-01-01", half_life_days=60,
                                   tier_weight=True, as_of=DATE),
        "stage2": S.round_swing_board(con, "2026-06-01", as_of=DATE),
        "stage1": S.round_swing_board(con, "2026-03-01", "2026-06-01", as_of=DATE),
        "kickoff": S.round_swing_board(con, "2026-01-01", "2026-03-01", as_of=DATE),
    }
    for k, b in boards.items():
        print(f"swing board {k}: {len(b)} players")
    X.export(con, clf, coefs, M.evaluate(df), sim, p, factors,
             boards, B.simulate_all(p), DATE)
    print("wrote site/public/data.json")
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "elo", "train", "sim", "export", "all"])
    ap.add_argument("--event", type=int, default=None)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.cmd in ("ingest", "all"):
        cmd_ingest(args)
    if args.cmd in ("elo", "all"):
        cmd_elo(args)
    if args.cmd in ("train", "all"):
        cmd_train(args)
    if args.cmd in ("sim", "all"):
        cmd_sim(args)
    if args.cmd != "elo":
        from pipeline.db import connect as _connect, write_status
        try:
            _c = _connect()
            print(write_status(_c))
            _c.close()
        except Exception as e:
            print("status skipped:", e)


if __name__ == "__main__":
    sys.exit(main())
