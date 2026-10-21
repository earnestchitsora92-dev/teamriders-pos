"""Create/update the Team Riders web service on Render and wire in the database.

Everything here goes through the public Render REST API, so no CLI is needed.

Usage:
    set RENDER_API_KEY=rnd_xxx
    python scripts/deploy_render.py --repo-url https://github.com/<you>/teamriders-pos \
        --database-url "postgresql://user:pass@host/db?sslmode=require" \
        [--name teamriders-pos] [--domain www.teamriders.bid]

Get an API key at https://dashboard.render.com/u/settings#api-keys
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.render.com/v1"
BUILD = "pip install --upgrade pip && pip install -r requirements.txt"
START = (
    "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 "
    "--proxy-headers --forwarded-allow-ips '*'"
)


def call(method: str, path: str, key: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"Render API {method} {path} failed ({exc.code}): {detail}") from None


def find_service(key: str, name: str):
    services = call("GET", f"/services?name={name}&limit=20", key)
    for item in services:
        service = item.get("service", item)
        if service.get("name") == name:
            return service
    return None


def trigger_deploy(key: str, service_id: str, wait: bool = False) -> None:
    deploy = call("POST", f"/services/{service_id}/deploys", key, {"clearCache": "do_not_clear"})
    deploy_id = deploy.get("id", "")
    print("Deploy triggered:", deploy_id)
    if wait and deploy_id:
        for _ in range(60):
            time.sleep(10)
            status = call("GET", f"/services/{service_id}/deploys/{deploy_id}", key)
            state = status.get("status", "unknown")
            print("  status:", state)
            if state in ("live", "build_failed", "canceled", "update_failed"):
                break


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the Team Riders POS to Render")
    parser.add_argument("--repo-url", required=True, help="GitHub repository URL (private or public)")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--database-url", default="", help="PostgreSQL connection string (Neon)")
    parser.add_argument("--name", default="teamriders-pos")
    parser.add_argument("--region", default="frankfurt")
    parser.add_argument("--domain", default="", help="custom domain to attach, e.g. www.teamriders.bid")
    parser.add_argument("--wait", action="store_true", help="wait until the deploy is live")
    args = parser.parse_args()

    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set RENDER_API_KEY first (Render dashboard → Account settings → API keys).")

    owners = call("GET", "/owners?limit=1", key)
    if not owners:
        raise SystemExit("No Render workspace found for this API key.")
    owner = owners[0].get("owner", owners[0])
    owner_id = owner["id"]
    print(f"Workspace: {owner.get('name', owner_id)}")

    service = find_service(key, args.name)
    if service is None:
        print("Creating web service…")
        payload = {
            "type": "web_service",
            "name": args.name,
            "ownerId": owner_id,
            "repo": args.repo_url,
            "branch": args.branch,
            "autoDeploy": "yes",
            "plan": "free",
            "region": args.region,
            "env": "python",
            "serviceDetails": {
                "env": "python",
                "plan": "free",
                "region": args.region,
                "healthCheckPath": "/healthz",
                "envSpecificDetails": {
                    "buildCommand": BUILD,
                    "startCommand": START,
                    "pythonVersion": "3.12.7",
                },
            },
        }
        created = call("POST", "/services", key, payload)
        service = created.get("service", created)
        print("Service created:", service["id"])
    else:
        print("Service exists:", service["id"])

    service_id = service["id"]

    # Merge with the variables already on the service: this endpoint replaces the whole list.
    existing = call("GET", f"/services/{service_id}/env-vars?limit=50", key)
    merged: dict[str, str] = {}
    for item in existing:
        entry = item.get("envVar", item)
        if entry.get("key"):
            merged[entry["key"]] = entry.get("value", "")
    merged.update({
        "PYTHON_VERSION": "3.12.7",
        "PYTHONUNBUFFERED": "1",
        "MX_SQLITE_JOURNAL": "delete",
    })
    if args.database_url:
        merged["DATABASE_URL"] = args.database_url
    if not merged.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set on the service — pass --database-url so the app keeps using PostgreSQL.")
    call("PUT", f"/services/{service_id}/env-vars", key,
         [{"key": k, "value": v} for k, v in merged.items()])
    print(f"Environment variables set ({len(merged)} total, DATABASE_URL present).")

    if args.domain:
        try:
            call("POST", f"/services/{service_id}/custom-domains", key, {"name": args.domain})
            print("Custom domain attached:", args.domain)
        except SystemExit as exc:
            print("Custom domain step skipped:", exc)

    trigger_deploy(key, service_id, wait=args.wait)

    detail = call("GET", f"/services/{service_id}", key)
    service = detail.get("service", detail)
    url = service.get("serviceDetails", {}).get("url", "")
    print("\nService URL:", url)
    print("Health check:", url.rstrip("/") + "/healthz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
