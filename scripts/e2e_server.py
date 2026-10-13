"""Disposable backend for Playwright: fresh data dir, known password, built WebUI.

Environment:
  E2E_PORT      dashboard port (default 6186)
  E2E_PASSWORD  admin password (default verter-e2e-password)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from verter.cli.main import main as cli

    dist = ROOT / "webui" / "dist"
    if not (dist / "index.html").is_file():
        print("e2e_server: webui/dist is missing; run `pnpm --dir webui build`", file=sys.stderr)
        return 1
    port = os.environ.get("E2E_PORT", "6186")
    os.environ["VERTER_ADMIN_PASSWORD"] = os.environ.get("E2E_PASSWORD", "verter-e2e-password")
    data_dir = Path(tempfile.mkdtemp(prefix="verter-e2e-"))
    try:
        if cli(["init", "--data-dir", str(data_dir)]) != 0:
            return 1
        from verter.core.config import CONFIG_FILENAME, PlatformConfig, load_config, write_config

        config_path = data_dir / CONFIG_FILENAME
        config = load_config(config_path)
        config.platform.append(PlatformConfig(id="mem", type="inmemory"))
        write_config(config_path, config)
        return cli(["run", "--data-dir", str(data_dir), "--port", port, "--webui-dist", str(dist)])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
