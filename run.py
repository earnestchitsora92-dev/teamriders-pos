"""Launch the Team Riders POS server."""
from __future__ import annotations

import argparse
import socket

import uvicorn


def free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Team Riders point of sale")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--reload", action="store_true", help="auto-reload during development")
    args = parser.parse_args()
    port = args.port if args.reload else free_port(args.port)
    print(f"\n  Team Riders POS running at http://{args.host}:{port}\n")
    uvicorn.run("app.main:app", host=args.host, port=port, reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
