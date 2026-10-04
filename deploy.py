"""Push site/dist to the gh-pages branch via the Git Data API.

Only changed files are uploaded (compared by git blob sha). Files in the
gh-pages tree but not in dist (e.g. .nojekyll) are left alone.
Usage: python3 deploy.py [--message MSG]
"""

import argparse
import base64
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response  # noqa: E402

OWNER, REPO, BRANCH = "mythiipanda", "valops", "gh-pages"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
DIST = Path(__file__).parent / "site" / "dist"
CRED = "custom.github"


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github+json")
    add_surrogate_to_request(req, CRED, allowed_hosts=["api.github.com"])
    with urllib.request.urlopen(req, timeout=60) as resp:
        return read_json_response(resp)


def blob_sha(path: Path) -> str:
    return subprocess.run(
        ["git", "hash-object", str(path)], capture_output=True, text=True,
        check=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", default="site: daily champions update")
    args = ap.parse_args()

    files = sorted(p for p in DIST.rglob("*") if p.is_file())
    print(f"{len(files)} files in dist", flush=True)

    ref = api("GET", f"/git/refs/heads/{BRANCH}")
    old_sha = ref["object"]["sha"]
    commit = api("GET", f"/git/commits/{old_sha}")
    base_tree = commit["tree"]["sha"]
    tree = api("GET", f"/git/trees/{base_tree}?recursive=1")
    remote = {t["path"]: t["sha"] for t in tree.get("tree", []) if t["type"] == "blob"}

    entries = []
    uploaded = 0
    for f in files:
        rel = f.relative_to(DIST).as_posix()
        sha = blob_sha(f)
        if remote.get(rel) == sha:
            entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
            continue
        raw = f.read_bytes()
        b = api("POST", "/git/blobs",
                {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": b["sha"]})
        uploaded += 1
    print(f"uploaded {uploaded} changed blobs", flush=True)

    new_tree = api("POST", "/git/trees", {"base_tree": base_tree, "tree": entries})
    new_commit = api("POST", "/git/commits",
                     {"message": args.message, "tree": new_tree["sha"],
                      "parents": [old_sha]})
    api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_commit["sha"]})
    print(f"gh-pages -> {new_commit['sha'][:7]}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
