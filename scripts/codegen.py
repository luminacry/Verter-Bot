"""Regenerate cross-language bindings, or verify they are up to date.

Pipelines:
  1. FastAPI app      -> webui/src/lib/api/openapi.json -> webui/src/lib/api/schema.d.ts
  2. Rust protocol    -> sandbox/schema/protocol.schema.json -> verter/core/computer/protocol.py

Usage:
  uv run python scripts/codegen.py           # write generated files
  uv run python scripts/codegen.py --check   # fail if committed files are stale
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENAPI_JSON = ROOT / "webui/src/lib/api/openapi.json"
OPENAPI_TS = ROOT / "webui/src/lib/api/schema.d.ts"
PROTOCOL_SCHEMA = ROOT / "sandbox/schema/protocol.schema.json"
PROTOCOL_PY = ROOT / "verter/core/computer/protocol.py"

DATAMODEL_ARGS = [
    "--input-file-type",
    "jsonschema",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--target-python-version",
    "3.12",
    "--use-standard-collections",
    "--use-union-operator",
    "--use-schema-description",
    "--use-field-description",
    "--use-title-as-name",
    "--field-constraints",
    "--use-annotated",
    "--use-type-alias",
    "--enum-field-as-literal",
    "all",
    "--use-double-quotes",
    "--disable-timestamp",
    "--formatters",
    "ruff-format",
]


def run(cmd: list[str], *, cwd: Path = ROOT) -> str:
    exe = shutil.which(cmd[0])
    if exe is None:
        sys.exit(f"codegen: `{cmd[0]}` not found in PATH")
    env = {**os.environ, "PYTHONWARNINGS": "ignore"}
    result = subprocess.run(
        [exe, *cmd[1:]],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"codegen: {' '.join(cmd)} failed\n{result.stdout}{result.stderr}")
    return result.stdout


def export_openapi() -> str:
    sys.path.insert(0, str(ROOT))
    from verter.dashboard.app import openapi_schema

    return json.dumps(openapi_schema(), indent=2, ensure_ascii=False) + "\n"


def generate(out_dir: Path) -> dict[Path, str]:
    """Run every generator, writing intermediates into ``out_dir``.

    Returns:
        Mapping of committed path to freshly generated content.
    """
    openapi = export_openapi()
    openapi_path = out_dir / OPENAPI_JSON.name
    openapi_path.write_text(openapi, encoding="utf-8", newline="\n")
    ts_path = out_dir / OPENAPI_TS.name
    run(
        [
            "pnpm",
            "--dir",
            "webui",
            "exec",
            "openapi-typescript",
            str(openapi_path),
            "-o",
            str(ts_path),
        ]
    )

    schema = run(
        [
            "cargo",
            "run",
            "-q",
            "--locked",
            "--manifest-path",
            "sandbox/Cargo.toml",
            "-p",
            "verter-sandbox",
            "--",
            "schema",
        ]
    )
    schema_path = out_dir / PROTOCOL_SCHEMA.name
    schema_path.write_text(schema, encoding="utf-8", newline="\n")
    py_path = out_dir / PROTOCOL_PY.name
    run(
        [
            "uv",
            "run",
            "--no-sync",
            "datamodel-codegen",
            "--input",
            str(schema_path),
            "--output",
            str(py_path),
            *DATAMODEL_ARGS,
        ]
    )

    return {
        OPENAPI_JSON: openapi,
        OPENAPI_TS: ts_path.read_text(encoding="utf-8"),
        PROTOCOL_SCHEMA: schema,
        PROTOCOL_PY: py_path.read_text(encoding="utf-8"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate cross-language bindings")
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="verter-codegen-") as tmp:
        generated = generate(Path(tmp))

    stale: list[Path] = []
    for path, content in generated.items():
        content = content.replace("\r\n", "\n")
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
        if current == content:
            continue
        if args.check:
            stale.append(path)
            rel = path.relative_to(ROOT).as_posix()
            sys.stdout.writelines(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    f"a/{rel}",
                    f"b/{rel}",
                    n=2,
                )
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"codegen: wrote {path.relative_to(ROOT).as_posix()}")

    if stale:
        names = ", ".join(p.relative_to(ROOT).as_posix() for p in stale)
        print(f"\ncodegen: stale generated files: {names}\nRun `just codegen`.")
        return 1
    if args.check:
        print("codegen: generated files are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
