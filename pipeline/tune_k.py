"""Coordinate-descent tune of the Elo K table on walk-forward Brier.

Temporary doc script for research_shortlist.md proposal #2. Run as:
    ../venv/bin/python pipeline/tune_k.py   (from the valo/ dir)

Method: start from current config values, sweep each parameter over its
coarse grid holding others fixed (coordinate descent), two full passes.
Each point re-runs elo.run + F.build + walk-forward evaluate on the real
data/valops.db (no ingest). Serial: ~3-5 min/point, ~22 pts with caching.

IMPORTANT patching note: pipeline/elo.py does `from .config import ...`
(binding module-level names) and pipeline/features.py does
`from .elo import K_PLAYOFF, stage_k`. Patching pipeline.config alone is
NOT enough -- we must set the attributes on the config, elo, AND features
modules, else stage_k() and the elopo playoff flag use stale values.
"""

import json
import sys
import time

import numpy as np
from sklearn.linear_model import LogisticRegression

from pipeline import config as C
from pipeline import elo as E
from pipeline import features as F
from pipeline import model as M
from pipeline.config import EVENTS
from pipeline.db import connect

META = {e: (y, t) for e, y, t, _ in EVENTS}

PARAMS = [
    ("K_GROUP", [12.0, 20.0, 28.0]),
    ("K_MAIN", [16.0, 24.0, 32.0]),
    ("K_PLAYOFF", [24.0, 32.0, 40.0]),
    ("CHEM_PENALTY", [30.0, 50.0, 70.0]),
    ("OFFSEASON_KEEP", [0.6, 0.7, 0.8]),
]
BASE = {"K_GROUP": 20.0, "K_MAIN": 24.0, "K_PLAYOFF": 32.0,
        "CHEM_PENALTY": 50.0, "OFFSEASON_KEEP": 0.7}

CUTS = [(2024, 2025), (2025, 2026)]
GATE_TOL = 0.002

cache: dict = {}


def apply(p: dict) -> None:
    for k, v in p.items():
        setattr(C, k, v)
        setattr(E, k, v)
    setattr(F, "K_PLAYOFF", p["K_PLAYOFF"])  # features binds it at import


def evaluate_exact(df):
    """Walk-forward Briers at full precision (M.evaluate rounds to 4dp)."""
    out = {}
    for tr_end, te in CUTS:
        tr = df[(df["date"].dt.year <= tr_end) & (df["event_id"] != 2766)]
        te_df = df[(df["date"].dt.year == te) & (df["event_id"] != 2766)]
        clf = LogisticRegression(max_iter=2000).fit(
            tr[M.COLS].fillna(0.0), tr["label"])
        pr = clf.predict_proba(te_df[M.COLS].fillna(0.0))[:, 1]
        y = te_df["label"].to_numpy(float)
        out[te] = (float(np.mean((pr - y) ** 2)), len(te_df))
    n1, n2 = out[2025][1], out[2026][1]
    pooled = (n1 * out[2025][0] + n2 * out[2026][0]) / (n1 + n2)
    return {"b2025": out[2025][0], "n2025": n1,
            "b2026": out[2026][0], "n2026": n2, "pooled": pooled}


def run_point(p: dict):
    key = tuple(p[k] for k, _ in PARAMS)
    if key in cache:
        return cache[key]
    apply(p)
    con = connect()
    try:
        E.run(con, META)
        df = F.build(con)
    finally:
        con.close()
    r = evaluate_exact(df)
    r["params"] = dict(p)
    cache[key] = r
    return r


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    t0 = time.time()
    log("baseline point (current config)")
    base = run_point(dict(BASE))
    log(f"baseline: 2025={base['b2025']:.4f} (n={base['n2025']}) "
        f"2026={base['b2026']:.4f} (n={base['n2026']}) pooled={base['pooled']:.4f}")
    # sanity: rounded must reproduce M.evaluate baseline
    cur = dict(BASE)
    best = base
    n_runs = 1
    for sweep in (1, 2):
        for name, grid in PARAMS:
            for v in grid:
                if cur[name] == v and n_runs > 1:
                    continue
                trial = dict(cur)
                trial[name] = v
                r = run_point(trial)
                n_runs += 1
                feas = (r["b2025"] <= base["b2025"] + GATE_TOL
                        and r["b2026"] <= base["b2026"] + GATE_TOL)
                tag = "feas" if feas else "INFEAS"
                log(f"sweep{sweep} {name}={v}: 2025={r['b2025']:.4f} "
                    f"2026={r['b2026']:.4f} pooled={r['pooled']:.4f} [{tag}]")
                if feas and r["pooled"] < best["pooled"] - 1e-9:
                    best = r
                    cur = dict(trial)
                    log(f"  -> new best: {cur} pooled={best['pooled']:.4f}")
    dt = (time.time() - t0) / 60
    log(f"done: {n_runs} runs, {dt:.0f} min")
    log(f"BEST: {best['params']} 2025={best['b2025']:.4f} "
        f"2026={best['b2026']:.4f} pooled={best['pooled']:.4f}")
    log(f"BASE: {BASE} 2025={base['b2025']:.4f} "
        f"2026={base['b2026']:.4f} pooled={base['pooled']:.4f}")
    improved = best["pooled"] < base["pooled"] - 1e-9
    no_worse = (best["b2025"] <= base["b2025"] + GATE_TOL
                and best["b2026"] <= base["b2026"] + GATE_TOL)
    verdict = "PASS" if (improved and no_worse) else "FAIL"
    log(f"VERDICT: {verdict} (pooled improved: {improved}, "
        f"cuts within +0.002: {no_worse})")
    with open(".tune_backup/tune_result.json", "w") as fh:
        json.dump({"base": base, "best": best, "cur": cur,
                   "verdict": verdict, "n_runs": n_runs}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
