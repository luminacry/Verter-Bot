"""Command-line entry point: ``verter init | run | doctor``."""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
from collections.abc import Callable, Sequence

from verter import __version__
from verter.core.computer.runner import probe_runner, resolve_runner_binary
from verter.core.config import (
    CONFIG_FILENAME,
    ENV_ADMIN_PASSWORD,
    ConfigError,
    InitResult,
    PlatformConfig,
    initialize_data_dir,
    load_config,
    resolve_data_dir,
)
from verter.core.core_lifecycle import serve
from verter.core.log import setup_logging
from verter.dashboard.app import resolve_webui_dist


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verter", description="Verter-Bot")
    parser.add_argument("--version", action="version", version=f"verter {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    data_dir_help = "data directory (default: $VERTER_DATA_DIR or ./data)"

    init = commands.add_parser("init", help="create the data directory and config")
    init.add_argument("--data-dir", help=data_dir_help)
    init.add_argument("--force", action="store_true", help="overwrite an existing config")
    init.set_defaults(handler=_cmd_init)

    run = commands.add_parser("run", help="start Verter-Bot")
    run.add_argument("--data-dir", help=data_dir_help)
    run.add_argument("--host", help="dashboard bind address (overrides config)")
    run.add_argument("--port", type=int, help="dashboard port (overrides config)")
    run.add_argument("--webui-dist", help="directory with built WebUI assets")
    run.add_argument(
        "--console",
        action="store_true",
        help="chat with the bot in this terminal (adds a console platform)",
    )
    run.set_defaults(handler=_cmd_run)

    doctor = commands.add_parser("doctor", help="check the environment and configuration")
    doctor.add_argument("--data-dir", help=data_dir_help)
    doctor.set_defaults(handler=_cmd_doctor)
    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    try:
        result = initialize_data_dir(data_dir, force=args.force)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Created {result.config_path}")
    _print_credentials(result)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    try:
        if not (data_dir / CONFIG_FILENAME).exists():
            result = initialize_data_dir(data_dir)
            print(f"First run: created {result.config_path}")
            _print_credentials(result)
        config = load_config(data_dir / CONFIG_FILENAME)
        if args.host:
            config.dashboard.host = args.host
        if args.port is not None:
            config.dashboard.port = args.port
        webui_dist = resolve_webui_dist(args.webui_dist)
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    extra = [PlatformConfig(id="console", type="console")] if args.console else []
    listener = setup_logging(config.log_level)
    try:
        asyncio.run(
            serve(config, webui_dist=webui_dist, extra_platforms=extra),
            loop_factory=_loop_factory(),
        )
    except KeyboardInterrupt:
        pass
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        listener.stop()
    return 0


def _loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """uvloop where available; Windows keeps the default proactor loop."""
    if sys.platform == "win32":
        return None
    import uvloop

    return uvloop.new_event_loop


def _cmd_doctor(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    config_path = data_dir / CONFIG_FILENAME
    rows: list[tuple[str, str]] = [
        ("Verter-Bot", __version__),
        ("Python", f"{platform.python_version()} ({sys.executable})"),
        ("Platform", platform.platform()),
        ("Data dir", str(data_dir)),
    ]
    exit_code = 0
    if not config_path.exists():
        rows.append(("Config", "missing (run `verter init`)"))
    else:
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            rows.append(("Config", f"invalid: {exc}"))
            exit_code = 1
        else:
            dash, policy = config.dashboard, config.computer
            rows.append(("Config", "ok"))
            rows.append(
                (
                    "Dashboard",
                    f"{'enabled' if dash.enable else 'disabled'} on {dash.host}:{dash.port}",
                )
            )
            rows.append(
                (
                    "Sandbox",
                    f"{'enabled' if policy.enable else 'disabled'}, backend={policy.backend}, "
                    f"allow={policy.allow}, network={policy.network}",
                )
            )
    status = asyncio.run(probe_runner(resolve_runner_binary()))
    if status.available:
        rows.append(("Runner", f"verter-sandbox {status.version} at {status.path}"))
        rows.extend(
            (f"  {b.kind}", f"{'available' if b.available else 'unavailable'} ({b.detail})")
            for b in status.backends
        )
    else:
        rows.append(("Runner", f"unavailable: {status.error}"))
    try:
        dist = resolve_webui_dist()
    except ConfigError as exc:
        rows.append(("WebUI", f"invalid: {exc}"))
        exit_code = 1
    else:
        rows.append(("WebUI", str(dist) if dist else "not built (API only)"))
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name.ljust(width)}  {value}")
    return exit_code


def _print_credentials(result: InitResult) -> None:
    print(f"Dashboard user: {result.username}")
    if result.password_generated:
        print(f"Dashboard password: {result.password}")
        print("Save this password now; only its hash is stored.")
    else:
        print(f"Dashboard password: taken from ${ENV_ADMIN_PASSWORD}")
