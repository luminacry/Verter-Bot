import logging

from verter.core.log import setup_logging
from verter.core.log_ring import RING, LogRing
from verter.core.metrics import BUCKETS_US, LatencyHistogram


def test_histogram_counts_each_bucket_once() -> None:
    histogram = LatencyHistogram()
    histogram.observe(40 / 1_000_000)
    histogram.observe(80 / 1_000_000)
    histogram.observe(1.0)
    snap = histogram.snapshot()
    assert snap.buckets_us == BUCKETS_US
    assert snap.total == 3
    assert snap.counts[0] == 1
    assert snap.counts[1] == 1
    assert snap.counts[-1] == 1
    assert snap.sum_us > 1_000_000


def test_log_ring_keeps_the_newest_records() -> None:
    ring = LogRing(capacity=2)
    logger = logging.getLogger("verter.test.ring")
    for index in range(3):
        record = logger.makeRecord(logger.name, logging.INFO, __file__, 1, "n=%s", (index,), None)
        ring.append(record)
    assert [entry.message for entry in ring.snapshot(10)] == ["n=1", "n=2"]
    assert ring.snapshot(1)[0].message == "n=2"
    ring.clear()
    assert ring.snapshot(10) == []


def test_setup_logging_copies_records_into_the_ring(restore_logging: None) -> None:
    RING.clear()
    listener = setup_logging("INFO")
    try:
        logging.getLogger("verter.probe").info("hello ring")
        listener.stop()
        messages = [entry.message for entry in RING.snapshot(20)]
    finally:
        RING.clear()
    assert "hello ring" in messages
