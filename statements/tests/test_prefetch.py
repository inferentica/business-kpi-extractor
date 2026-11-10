"""Submissions downloaded a few at a time while the readers take the filings in order."""
import threading
import time

from statement_extractor.pipeline.prefetch import ahead, load_all


def test_items_come_in_order_each_loaded_and_failures_left_to_the_reader():
    loaded, lock = [], threading.Lock()

    def load(item):
        time.sleep(0.01 * (5 - item))  # later items finish first
        if item == 2:
            raise RuntimeError("SEC busy")
        with lock:
            loaded.append(item)

    assert list(ahead(range(5), load)) == [0, 1, 2, 3, 4]
    assert sorted(loaded) == [0, 1, 3, 4]
    loaded.clear()
    load_all(range(5), load)
    assert sorted(loaded) == [0, 1, 3, 4]


def test_loads_overlap():
    started = time.time()
    load_all(range(8), lambda item: time.sleep(0.1), workers=4)
    assert time.time() - started < 0.5  # eight 0.1 s waits, four at a time
