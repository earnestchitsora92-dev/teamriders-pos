"""Push this project to a GitHub repository using only the GitHub REST API.

No git installation required. Creates the repository if it does not exist, then
uploads a snapshot of the current source tree as a single commit on `main`.

Usage:
    set GH_TOKEN=ghp_xxx
    python scripts/push_to_github.py --repo teamriders-pos [--owner my-org] [--public] [--message "Update"]

Create the token at https://github.com/settings/tokens with the `repo` scope
(`repo` also covers creating private repositories).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".github-ignore", "data", "__pycache__", ".venv", "venv", "node_modules",
             ".pytest_cache", ".mypy_cache", "backups", "dist", "build"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".zip"}
SKIP_FILES = {".env", "gh_token.txt", "render_api_key.txt"}
API = "https://api.github.com"


def call(method: str, path: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "team-riders-pos",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"GitHub API {method} {path} failed ({exc.code}): {detail}") from None


def collect_files() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if relative.name in SKIP_FILES or relative.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append((relative.as_posix(), path.read_bytes()))
    return files


def create_tree(owner: str, repo: str, token: str, items: list[dict]) -> tuple[str | None, list[str]]:
    """Create the git tree, dropping workflow files if the token lacks the `workflow` scope.

    GitHub answers 404 (not 403) when a classic token without `workflow` tries to write
    `.github/workflows/*`, so the failure is detected and retried without those paths.
    """
    try:
        tree = call("POST", f"/repos/{owner}/{repo}/git/trees", token, {"tree": items})
        return tree["sha"], []
    except SystemExit:
        skipped = [item["path"] for item in items if item["path"].startswith(".github/workflows/")]
        if not skipped:
            raise
    remaining = [item for item in items if not item["path"].startswith(".github/workflows/")]
    tree = call("POST", f"/repos/{owner}/{repo}/git/trees", token, {"tree": remaining})
    return tree["sha"], skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Push the Team Riders POS source to GitHub")
    parser.add_argument("--repo", default="teamriders-pos")
    parser.add_argument("--owner", default="", help="user or organisation name (defaults to the token owner)")
    parser.add_argument("--public", action="store_true", help="create a public repository")
    parser.add_argument("--message", default="Deploy Team Riders POS")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set GH_TOKEN first (GitHub → Settings → Developer settings → Personal access tokens).")

    user = call("GET", "/user", token)
    owner = args.owner or user["login"]
    print(f"Authenticated as {user['login']}, target repository: {owner}/{args.repo}")

    existing = None
    try:
        existing = call("GET", f"/repos/{owner}/{args.repo}", token)
        print("Repository exists — pushing a new commit.")
    except SystemExit:
        existing = None

    if existing is None:
        created = call(
            "POST",
            f"/user/repos" if not args.owner else f"/orgs/{args.owner}/repos",
            token,
            {
                "name": args.repo,
                "private": not args.public,
                "description": "Team Riders motorbike spares POS",
                "auto_init": True,
            },
        )
        print("Created repository:", created["html_url"])
    else:
        print("Reusing repository:", existing["html_url"])
        try:
            call("GET", f"/repos/{owner}/{args.repo}/git/ref/heads/{args.branch}", token)
        except SystemExit:
            # GitHub refuses blob/tree writes to a repository with no commits yet.
            call(
                "PUT",
                f"/repos/{owner}/{args.repo}/contents/README.md",
                token,
                {
                    "message": "Initialise repository",
                    "content": base64.b64encode(b"# Team Riders POS\n").decode(),
                    "branch": args.branch,
                },
            )
            print("Repository was empty — created the first commit.")

    files = collect_files()
    print(f"Uploading {len(files)} file(s)...")
    tree_items = []
    for name, content in files:
        blob = call(
            "POST",
            f"/repos/{owner}/{args.repo}/git/blobs",
            token,
            {"content": base64.b64encode(content).decode(), "encoding": "base64"},
        )
        tree_items.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    tree_sha, skipped = create_tree(owner, args.repo, token, tree_items)
    if tree_sha is None:
        raise SystemExit("Could not create the git tree on GitHub.")
    if skipped:
        print(
            "Note: skipped %d workflow file(s) — creating .github/workflows needs a token with the "
            "'workflow' scope. Add that scope and re-run to publish them." % len(skipped)
        )
        for path in skipped:
            print("   -", path)

    parent = None
    try:
        ref = call("GET", f"/repos/{owner}/{args.repo}/git/ref/heads/{args.branch}", token)
        parent = ref["object"]["sha"]
    except SystemExit:
        parent = None

    commit_payload = {"message": args.message, "tree": tree_sha}
    if parent:
        commit_payload["parents"] = [parent]
    commit = call("POST", f"/repos/{owner}/{args.repo}/git/commits", token, commit_payload)

    if parent:
        call("PATCH", f"/repos/{owner}/{args.repo}/git/refs/heads/{args.branch}", token,
             {"sha": commit["sha"], "force": True})
    else:
        call("POST", f"/repos/{owner}/{args.repo}/git/refs", token,
             {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})

    print("Pushed commit", commit["sha"][:8], "to", f"{owner}/{args.repo}@{args.branch}")
    print("Repository URL: https://github.com/%s/%s" % (owner, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
