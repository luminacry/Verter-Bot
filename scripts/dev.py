"""Run the backend and the Vite dev server together; Ctrl+C stops both.

Usage: uv run python scripts/dev.py [verter run options]
Open http://localhost:5173; the dev server proxies /api to the backend on :6185.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("dev: pnpm not found in PATH", file=sys.stderr)
        return 1
    procs = [
        subprocess.Popen([sys.executable, "-m", "verter", "run", *sys.argv[1:]], cwd=ROOT),
        subprocess.Popen([pnpm, "--dir", "webui", "dev"], cwd=ROOT),
    ]
    try:
        while all(proc.poll() is None for proc in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
