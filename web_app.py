#!/usr/bin/env python3
"""Small launcher for the STT Video Pipeline Web UI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
REQUIREMENTS_FILE = BASE_DIR / "requirements.txt"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run_command(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BASE_DIR, check=True)


def ensure_venv() -> Path:
    python_bin = venv_python()
    if not python_bin.exists():
        run_command([sys.executable, "-m", "venv", str(VENV_DIR)])
    return python_bin


def install_requirements(python_bin: Path) -> None:
    run_command([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"])
    run_command([str(python_bin), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)])


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
        help="Skip virtualenv creation and dependency installation.",
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
    python_bin = venv_python()

    if args.no_install:
        if not python_bin.exists():
            print(
                "Missing .venv. Run without --no-install first, or run make install.",
                file=sys.stderr,
            )
            return 1
    else:
        python_bin = ensure_venv()
        install_requirements(python_bin)

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        open_browser_later(url, delay=1.5)

    print(f"Starting Web UI at {url}", flush=True)
    cmd = [
        str(python_bin),
        "-m",
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
