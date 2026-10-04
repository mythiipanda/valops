"""Commit model artifacts to the main branch after each run.

Every nightly model gets a git timestamp: the predictions snapshot and the
results ledger. The commit message carries the date and track record, so
`git log main` is the full history of what the model said and how it did.
Usage: archive_run("2026-09-25", summary)  — called at the end of cmd_daily.
"""

import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from deploy import api  # noqa: E402  (Git Data API helpers)

BRANCH = "main"


def archive_run(run_date: str, summary: dict) -> str | None:
    """Commit tonight's track files to main. Returns the commit sha."""
    paths = [Path(f"data/track/preds/{run_date}.json"),
             Path("data/track/ledger.jsonl")]
    missing = [p for p in paths if not (ROOT / p).exists()]
    if missing:
        print(f"archive skipped, missing: {[m.as_posix() for m in missing]}",
              flush=True)
        return None

    ref = api("GET", f"/git/refs/heads/{BRANCH}")
    old_sha = ref["object"]["sha"]
    commit = api("GET", f"/git/commits/{old_sha}")
    base_tree = commit["tree"]["sha"]
    tree = api("GET", f"/git/trees/{base_tree}?recursive=1")
    remote = {t["path"]: t["sha"] for t in tree.get("tree", [])
              if t["type"] == "blob"}

    entries, changed = [], 0
    for p in paths:
        rel = p.as_posix()
        sha = subprocess.run(["git", "hash-object", str(ROOT / p)],
                             capture_output=True, text=True,
                             check=True).stdout.strip()
        if remote.get(rel) != sha:
            raw = (ROOT / p).read_bytes()
            b = api("POST", "/git/blobs",
                    {"content": base64.b64encode(raw).decode(),
                     "encoding": "base64"})
            sha = b["sha"]
            changed += 1
        entries.append({"path": rel, "mode": "100644", "type": "blob",
                        "sha": sha})
    if changed == 0:
        print("archive: nothing changed", flush=True)
        return old_sha

    n = summary.get("n") or 0
    right = summary.get("right") or 0
    brier = summary.get("brier")
    record = f"{right}-{n - right} picks" if n else "no games yet"
    tail = f", brier {brier}" if brier is not None else ""
    msg = f"track: {run_date} — {record}{tail}"
    new_tree = api("POST", "/git/trees",
                   {"base_tree": base_tree, "tree": entries})
    new_commit = api("POST", "/git/commits",
                     {"message": msg, "tree": new_tree["sha"],
                      "parents": [old_sha]})
    api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_commit["sha"]})
    print(f"archived to main -> {new_commit['sha'][:7]}", flush=True)
    return new_commit["sha"]


if __name__ == "__main__":
    import json
    from pipeline import track as T
    print(archive_run(sys.argv[1], T.summary()))
