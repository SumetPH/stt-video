#!/usr/bin/env python3
"""Small launcher for the STT Video Pipeline Web UI."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
UV = shutil.which("uv") or "uv"


def run_command(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BASE_DIR, check=True)


def sync_environment() -> None:
    run_command([UV, "sync"])


def open_browser_later(url: str, delay: float) -> None:
    def open_browser() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the STT Video Pipeline Web UI without using make."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip uv sync and use the existing environment.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable uvicorn auto-reload.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.no_install:
        run_prefix = [UV, "run", "--no-sync"]
    else:
        sync_environment()
        run_prefix = [UV, "run", "--no-sync"]

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        open_browser_later(url, delay=1.5)

    print(f"Starting Web UI at {url}", flush=True)
    cmd = [
        *run_prefix,
        "uvicorn",
        "web_ui.server:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if not args.no_reload:
        cmd.append("--reload")

    try:
        run_command(cmd)
    except KeyboardInterrupt:
        print("\nStopped Web UI.", flush=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
