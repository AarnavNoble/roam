"""Stage timings per request, the latency aggregator, and span parenting across threads."""

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.core.telemetry import StageAggregator, aggregator, collect_timings, in_context, stage


def fake_span(name: str, ms: float):
    return SimpleNamespace(name=name, start_time=0, end_time=int(ms * 1e6))  # OTel times are in ns


def test_collect_timings_records_each_stage_in_ms():
    with collect_timings() as timings:
        with stage("outer"):
            with stage("inner"):
                time.sleep(0.02)
    assert set(timings) == {"outer", "inner"}
    assert 15 <= timings["inner"] <= timings["outer"] < 2000


def test_repeated_stage_durations_are_summed():
    # The LLM stage can run once per model tried; the request reports the total.
    with collect_timings() as timings:
        for _ in range(3):
            with stage("llm.call"):
                time.sleep(0.01)
    assert list(timings) == ["llm.call"]
    assert timings["llm.call"] >= 25


def test_stage_records_its_duration_even_when_the_block_raises():
    with collect_timings() as timings:
        with pytest.raises(RuntimeError):
            with stage("failing"):
                raise RuntimeError("boom")
    assert "failing" in timings


def test_stage_outside_a_request_collects_nothing_and_does_not_fail():
    with stage("orphan"):
        pass
    with collect_timings() as timings:
        pass
    assert timings == {}


def test_two_requests_do_not_share_timings():
    def request(name):
        with collect_timings() as timings:
            with stage(name):
                time.sleep(0.01)
            return timings
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(request, ["a", "b"])
    assert set(a) == {"a"} and set(b) == {"b"}


def test_aggregator_percentiles():
    agg = StageAggregator()
    for ms in range(1, 101):  # 1..100 ms
        agg.on_end(fake_span("llm", ms))
    agg.on_end(fake_span("geocode", 7))
    summary = agg.summary()
    assert list(summary) == ["geocode", "llm"]  # sorted by stage name
    assert summary["llm"] == {"count": 100, "p50_ms": 50.5, "p95_ms": 95.0, "max_ms": 100.0}
    assert summary["geocode"] == {"count": 1, "p50_ms": 7.0, "p95_ms": 7.0, "max_ms": 7.0}


def test_aggregator_keeps_a_sliding_window():
    agg = StageAggregator(window=3)
    for ms in (1000, 1, 2, 3):  # the slow first sample falls out of the window
        agg.on_end(fake_span("s", ms))
    assert agg.samples("s") == [1.0, 2.0, 3.0]
    assert agg.summary()["s"]["max_ms"] == 3.0
    agg.reset()
    assert agg.summary() == {} and agg.samples("s") == []


def test_real_spans_reach_the_global_aggregator():
    before = len(aggregator.samples("test.telemetry.real"))
    with stage("test.telemetry.real"):
        pass
    assert len(aggregator.samples("test.telemetry.real")) == before + 1


def test_in_context_keeps_worker_spans_parented_and_timed():
    # A stage started on a pool thread through in_context is a child of the request's span
    # and lands in the request's timings.
    def child():
        with stage("child") as span:
            return span.parent, span.get_span_context().trace_id

    with ThreadPoolExecutor(max_workers=1) as pool, collect_timings() as timings:
        with stage("request") as parent:
            child_parent, trace_id = pool.submit(in_context(child)).result()
            parent_ctx = parent.get_span_context()
    assert child_parent is not None and child_parent.span_id == parent_ctx.span_id
    assert trace_id == parent_ctx.trace_id
    assert set(timings) == {"request", "child"}


def test_without_in_context_the_worker_span_is_an_orphan():
    # Control for the test above: this is what ThreadPoolExecutor does by itself.
    def child():
        with stage("child") as span:
            return span.parent

    with ThreadPoolExecutor(max_workers=1) as pool, collect_timings() as timings:
        with stage("request"):
            child_parent = pool.submit(child).result()
    assert child_parent is None
    assert set(timings) == {"request"}


def test_in_context_wrapper_can_be_called_more_than_once():
    # hedged() may run the same wrapped call on several threads; Context.run is not reentrant,
    # so each call has to run in its own copy.
    def child():
        with stage("again") as span:
            time.sleep(0.02)  # long enough for the two calls to overlap
            return span.parent.span_id

    with stage("request") as parent:
        wrapped = in_context(child)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = [f.result() for f in [pool.submit(wrapped), pool.submit(wrapped)]]
    assert ids == [parent.get_span_context().span_id] * 2
