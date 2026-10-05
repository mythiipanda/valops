"""CLI: python3 run.py ingest | elo | train | sim | export | daily | all [--event ID]"""

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
from datetime import date
# "now" for the whole run: data.json as_of, swing recency, pairwise cutoff
DATE = date.today().isoformat()

KICKOFF_2026 = [2682, 2684, 2683, 2685]
STAGE1_2026 = [2760, 2860, 2863, 2775, 2864]
STAGE2_2026 = [2765, 2977, 2976, 2776, 2978]
SEASON_2026 = KICKOFF_2026 + STAGE1_2026 + STAGE2_2026 + [2766]  # +Champions, no data yet


def _map_data(con):
    """Fresh comp edges + team map strength for export()."""
    from pipeline import compedge as CE
    names, _ = X.team_meta()
    md = CE.build(con, names, DATE)
    return {"map_comps": md["map_comps"],
            "team_map_strength": md["team_map_strength"]}


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
                                   tier_weight=True, as_of=DATE,
                                   event_ids=SEASON_2026),
        "champions": S.round_swing_board(con, "2026-01-01", min_rounds=60,
                                         as_of=DATE, event_ids=[2766]),
        "stage2": S.round_swing_board(con, "2026-01-01", as_of=DATE,
                                      event_ids=STAGE2_2026),
        "stage1": S.round_swing_board(con, "2026-01-01", as_of=DATE,
                                      event_ids=STAGE1_2026),
        "kickoff": S.round_swing_board(con, "2026-01-01", as_of=DATE,
                                       event_ids=KICKOFF_2026),
    }
    for k, b in boards.items():
        print(f"swing board {k}: {len(b)} players")
    from pipeline import mapcomp as MC
    MC.run(con)
    md = _map_data(con)
    X.export(con, clf, coefs, M.evaluate(df), sim, p, factors,
             boards, B.simulate_all(p), DATE,
             map_comps=md["map_comps"],
             team_map_strength=md["team_map_strength"])
    print("wrote site/public/data.json")
    con.close()


def cmd_daily(_):
    """Nightly Champions update: score finished games against pre-game
    snapshots, refresh the model with new results, snapshot fresh
    predictions for the games still to come, rebuild data.json."""
    import pandas as pd
    from pipeline import archive as AR
    from pipeline import bracket as B
    from pipeline import playoff as PO
    from pipeline import track as T
    from pipeline.config import TEAMS  # noqa: F401  (used by track)

    con = connect()
    # 1. pull today's results (refresh the event listing: it lags)
    I.run(event_ids=[2766], refresh_event=True)
    # 1b. rebuild per-map comps from the refreshed raw cache (was stale:
    #     Champions rows were missing entirely) so comp edges, team map
    #     strength, and map-comp features all see today's games
    from pipeline import mapcomp as MC
    MC.run(con)
    # 2. score completed Champions series vs the most recent pre-game snapshot.
    #    Scoring runs before tonight's snapshot is created, so every snapshot
    #    used here predates the game it scores — nothing has seen the future.
    scored = T.score(con)
    print(f"scored {scored} new champions games", flush=True)
    results = T.results_map(con)
    fixed = {k: [v["winner"] for v in vs] for k, vs in results.items()}
    # 2b. kill-feed backfill -> v4 swing pickle stays current, so both the
    #     "all" swing board and the champions board update as matches are played
    from pipeline import killfeed as KF
    sw = KF.update_swing(con)
    print(f"swing backfill: {sw['new_matches']} new matches,"
          f" {sw['new_entries']} new entries,"
          f" pickle now {sw['entries']} entries", flush=True)
    # 3. ratings + features + model (now including today's games)
    final = E.run(con, META)
    E.run(con, META, k_mult=FAST_K_MULT, offseason_keep=1.0, tables=FAST_TABLES)
    print(f"players tracked: {len(final)}", flush=True)
    df = F.build(con)
    df.to_pickle("data/features.pkl")
    reps = M.evaluate(df)
    for r in reps:
        print(r, flush=True)
    clf, coefs = M.fit_all(df)
    # 4. tonight's predictions, for the games still to come
    p, factors = M.pairwise(con, clf, DATE, is_po=0)
    T.snapshot(p, DATE)
    print(f"snapshot {DATE}: {len(p)} ordered pairs", flush=True)
    # 5. full export, track record included; sims and bracket condition on
    #    series already played instead of re-drawing decided matches
    ppo, _ = M.pairwise(con, clf, DATE, is_po=1)
    sim = M.simulate(p, ppo, fixed=fixed)
    for t in sorted(sim["title"], key=lambda t: -sim["title"][t]):
        from pipeline.config import TEAMS
        print(f"{TEAMS[t]:20s} title {sim['title'][t]:.3f}  advance {sim['advance'][t]:.3f}",
              flush=True)
    boards = {
        "all": S.round_swing_board(con, "2026-01-01", half_life_days=60,
                                   tier_weight=True, as_of=DATE,
                                   event_ids=SEASON_2026),
        "champions": S.round_swing_board(con, "2026-01-01", min_rounds=60,
                                         as_of=DATE, event_ids=[2766]),
        "stage2": S.round_swing_board(con, "2026-01-01", as_of=DATE,
                                      event_ids=STAGE2_2026),
        "stage1": S.round_swing_board(con, "2026-01-01", as_of=DATE,
                                      event_ids=STAGE1_2026),
        "kickoff": S.round_swing_board(con, "2026-01-01", as_of=DATE,
                                       event_ids=KICKOFF_2026),
    }
    X.export(con, clf, coefs, reps, sim, p, factors,
             boards, B.simulate_all(p, results), DATE, track=T.summary(),
             playoff_bracket=PO.playoff_view(ppo, results),
             **_map_data(con))
    print("wrote site/public/data.json")
    # 6. timestamp the model in git: snapshot + ledger -> main branch
    AR.archive_run(DATE, T.summary())
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "elo", "train", "sim", "export", "all", "daily"])
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
    if args.cmd == "daily":
        cmd_daily(args)
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
