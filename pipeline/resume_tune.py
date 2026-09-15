"""Resume coordinate descent from best-so-far: K_GROUP=12, K_MAIN=16.

Run as: ../venv/bin/python pipeline/resume_tune.py  (from valo/)
Sweeps the remaining 3 params from the best point, then a second pass
over all 5. ~19 runs x ~2.5 min.
"""
import json
import sys
import time

sys.path.insert(0, ".")
from pipeline.tune_k import run_point, log, BASE, GATE_TOL  # noqa: E402

PARAMS = [
    ("K_PLAYOFF", [24.0, 32.0, 40.0]),
    ("CHEM_PENALTY", [30.0, 50.0, 70.0]),
    ("OFFSEASON_KEEP", [0.6, 0.7, 0.8]),
]
PARAMS_ALL = [
    ("K_GROUP", [8.0, 12.0, 16.0]),
    ("K_MAIN", [12.0, 16.0, 20.0]),
] + PARAMS

CUR = {"K_GROUP": 12.0, "K_MAIN": 16.0, "K_PLAYOFF": 32.0,
       "CHEM_PENALTY": 50.0, "OFFSEASON_KEEP": 0.7}


def sweep(cur, params, base, best, label):
    for name, grid in params:
        for v in grid:
            if cur[name] == v:
                continue
            trial = dict(cur)
            trial[name] = v
            r = run_point(trial)
            feas = (r["b2025"] <= base["b2025"] + GATE_TOL
                    and r["b2026"] <= base["b2026"] + GATE_TOL)
            tag = "feas" if feas else "INFEAS"
            log(f"{label} {name}={v}: 2025={r['b2025']:.4f} "
                f"2026={r['b2026']:.4f} pooled={r['pooled']:.4f} [{tag}]")
            if feas and r["pooled"] < best["pooled"] - 1e-9:
                best = r
                cur = dict(trial)
                log(f"  -> new best: {cur} pooled={best['pooled']:.4f}")
    return cur, best


def main() -> int:
    t0 = time.time()
    base = run_point(dict(BASE))
    log(f"baseline: 2025={base['b2025']:.4f} 2026={base['b2026']:.4f} "
        f"pooled={base['pooled']:.4f}")
    best = run_point(dict(CUR))
    log(f"resume point {CUR}: 2025={best['b2025']:.4f} "
        f"2026={best['b2026']:.4f} pooled={best['pooled']:.4f}")
    cur = dict(CUR)
    cur, best = sweep(cur, PARAMS, base, best, "resume1")
    cur, best = sweep(cur, PARAMS_ALL, base, best, "resume2")
    dt = (time.time() - t0) / 60
    log(f"done: {dt:.0f} min")
    log(f"BEST: {best['params']} 2025={best['b2025']:.4f} "
        f"2026={best['b2026']:.4f} pooled={best['pooled']:.4f}")
    improved = best["pooled"] < base["pooled"] - 1e-9
    no_worse = (best["b2025"] <= base["b2025"] + GATE_TOL
                and best["b2026"] <= base["b2026"] + GATE_TOL)
    verdict = "PASS" if (improved and no_worse) else "FAIL"
    log(f"VERDICT: {verdict}")
    import os
    os.makedirs(".tune_backup", exist_ok=True)
    with open(".tune_backup/resume_result.json", "w") as fh:
        json.dump({"base": base, "best": best, "cur": cur,
                   "verdict": verdict}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
