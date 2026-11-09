"""Network failures are retried; other failures are not."""
import pytest

from statement_extractor.sources.retry import with_network_retries


class ReadTimeout(Exception):
    pass


def test_a_timeout_is_tried_again_and_a_parse_error_is_not():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ReadTimeout("The read operation timed out")
        return "filing"

    assert with_network_retries(flaky, sleep=lambda _: None) == "filing" and len(calls) == 3
    with pytest.raises(ValueError):
        with_network_retries(lambda: (_ for _ in ()).throw(ValueError("bad table")), sleep=lambda _: None)
