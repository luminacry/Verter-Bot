import time

from verter.dashboard.auth import LoginRateLimiter, SessionInfo, issue_token, read_session


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_blocks_after_max_failures_then_recovers() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=3, window_s=60, clock=clock)
    for _ in range(2):
        limiter.record_failure("1.2.3.4")
        assert limiter.retry_after("1.2.3.4") == 0
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 60
    clock.now += 45
    assert limiter.retry_after("1.2.3.4") == 15
    assert limiter.retry_after("5.6.7.8") == 0
    clock.now += 15
    assert limiter.retry_after("1.2.3.4") == 0


def test_limiter_reset_and_eviction() -> None:
    limiter = LoginRateLimiter(max_failures=1, max_keys=2, clock=FakeClock())
    limiter.record_failure("a")
    limiter.reset("a")
    assert limiter.retry_after("a") == 0
    for key in ("a", "b", "c"):
        limiter.record_failure(key)
    assert limiter.retry_after("a") == 0, "oldest key is evicted when the table is full"
    assert limiter.retry_after("c") > 0


SECRET = "s" * 32
OTHER = "o" * 32


def test_tokens_validate_secret_subject_and_expiry() -> None:
    token = issue_token("admin", SECRET, 60)
    assert read_session(token, username="admin", secret=SECRET) == SessionInfo(username="admin")
    assert read_session(token, username="admin", secret=OTHER) is None
    assert read_session(token, username="root", secret=SECRET) is None
    expired = issue_token("admin", SECRET, 60, now=time.time() - 120)
    assert read_session(expired, username="admin", secret=SECRET) is None
    assert read_session(None, username="admin", secret=SECRET) is None
    assert read_session("not-a-jwt", username="admin", secret=SECRET) is None
