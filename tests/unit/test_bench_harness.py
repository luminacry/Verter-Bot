import pytest

from benchmarks import bench_dispatch_sim
from benchmarks.harness import Budget, best_ns_per_op, evaluate


@pytest.mark.parametrize(
    ("budget", "value", "violates"),
    [
        (Budget("m", max=1.1), 1.1, False),
        (Budget("m", max=1.1), 1.2, True),
        (Budget("m", min=50), 49.9, True),
        (Budget("m", min=50, max=60), 55, False),
    ],
)
def test_budget_bounds_are_inclusive(budget: Budget, value: float, violates: bool) -> None:
    assert (budget.violation(value) is not None) is violates


def test_evaluate_reports_violations_and_missing_metrics() -> None:
    budgets = [Budget("fast", max=1.0), Budget("absent", max=1.0), Budget("ok", min=0)]
    problems = evaluate({"fast": 2.0, "ok": 1.0}, budgets)
    assert problems == ["fast = 2.000 exceeds 1", "absent was not reported"]


def test_budget_descriptions() -> None:
    assert Budget("m", max=1.25).describe() == "<= 1.25"
    assert Budget("m", min=3).describe() == ">= 3"
    assert Budget("m", min=1, max=2).describe() == "1..2"
    assert Budget("m").describe() == "-"


def test_best_ns_per_op_uses_the_fastest_run() -> None:
    durations = iter([10, 1, 5])

    def work() -> int:
        return next(durations)

    assert best_ns_per_op(work, repeat=3) > 0


def test_dispatch_simulation_reports_budgeted_metrics() -> None:
    results = bench_dispatch_sim.run(scale=0.01)
    assert set(results) >= {b.metric for b in bench_dispatch_sim.BUDGETS}
    assert 0 < results["indexed_growth_100_to_5000"] < 5
