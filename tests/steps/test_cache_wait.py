"""A second reading waits for the cache only when the first reading found none of its prompt there (p4-secondproof)."""
import time
from types import SimpleNamespace

from kpi_extractor.ops.control import Reply, cache_share
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.steps.ai_reading import CACHE_FORMS_S


class Control:
    def call(self, operation, **payload):
        return {}


def pipeline_with(share, replied_ago: float):
    pipeline = SymbolPipeline(Control(), "WFC", quarters=4, force=False, log=lambda *_: None)
    slept = []
    pipeline._sleep = slept.append
    first = SimpleNamespace()
    if share is not None:
        pipeline._cached = {id(first): (first, share, time.monotonic() - replied_ago)}
    return pipeline, first, slept


def test_a_second_reading_waits_for_the_cache_only_after_a_first_reading_that_missed_it():
    pipeline, first, slept = pipeline_with(0.05, replied_ago=1.0)
    pipeline._wait_for_cache(first)
    assert len(slept) == 1 and CACHE_FORMS_S - 1.5 < slept[0] <= CACHE_FORMS_S - 1.0 and pipeline.result.cache_waits == 1
    for share, ago in ((0.95, 1.0), (None, 1.0), (0.05, CACHE_FORMS_S + 1)):  # cached already, not known, or long enough
        pipeline, first, slept = pipeline_with(share, replied_ago=ago)
        pipeline._wait_for_cache(first)
        assert slept == [] and pipeline.result.cache_waits == 0


def test_a_reply_carries_the_share_of_its_prompt_the_cache_held():
    assert cache_share({"cacheHitTokens": 2_500, "inputTokens": 10_000}) == 0.25
    assert cache_share({"inputTokens": 10_000}) is None and cache_share({"cacheHitTokens": 0, "inputTokens": 0}) is None
    reply = Reply("{}", "call-1", 0.25)
    assert reply == "{}" and reply.call_id == "call-1" and reply.cache_share == 0.25
