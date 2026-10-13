"""Run every ``benchmarks/bench_*.py`` module and enforce its budgets.

Usage:
  uv run python -m benchmarks.run            # full scale
  uv run python -m benchmarks.run --quick    # reduced scale for CI smoke runs
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

from benchmarks.harness import Budget, evaluate

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results" / "latest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run benchmarks and check budgets")
    parser.add_argument("--quick", action="store_true", help="run at 20%% scale")
    parser.add_argument("-k", dest="pattern", default="", help="only modules containing this")
    args = parser.parse_args(argv)
    scale = 0.2 if args.quick else 1.0

    report: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scale": scale,
        "modules": {},
    }
    failures: list[str] = []
    for path in sorted(ROOT.glob("bench_*.py")):
        if args.pattern not in path.stem:
            continue
        module = importlib.import_module(f"benchmarks.{path.stem}")
        budgets: tuple[Budget, ...] = module.BUDGETS
        results: dict[str, float] = module.run(scale)
        problems = evaluate(results, budgets)
        failures.extend(f"{path.stem}: {p}" for p in problems)
        report["modules"][path.stem] = {"results": results, "violations": problems}
        limits = {b.metric: b.describe() for b in budgets}
        print(f"\n{path.stem}")
        for metric, value in results.items():
            print(f"  {metric:32} {value:14.3f}   {limits.get(metric, '')}")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nresults written to {RESULTS.relative_to(ROOT.parent).as_posix()}")
    if failures:
        print("\nbudget violations:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    print("all budgets met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
