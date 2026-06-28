"""Tests for the background-refresh cache engine.

The contract: reads serve last-good and never build (except the cold first-boot
miss); invalidate() flags dirty without dropping the value; the background loop
(run_refresh_cycle) owns rebuilding.
"""

from __future__ import annotations

import pytest

from synclet import maint_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    maint_cache.clear()
    yield
    maint_cache.clear()


class TestGetCached:
    def test_first_call_builds(self):
        calls = []
        assert maint_cache.get_cached("k", lambda: calls.append(1) or "v1") == "v1"
        assert calls == [1]

    def test_subsequent_reads_serve_last_good_without_building(self):
        calls = []

        def _build():
            calls.append(1)
            return "v1"

        maint_cache.get_cached("k", _build)
        # Many reads, never another build — even though there is no TTL, a read
        # must never recompute. This is the whole point: no stall on the read
        # path.
        for _ in range(5):
            assert maint_cache.get_cached("k", _build) == "v1"
        assert calls == [1]

    def test_stale_value_keeps_serving_after_invalidate(self):
        """invalidate() flags dirty but does NOT drop the value; reads keep
        serving last-good until a refresh cycle rebuilds it."""
        responses = iter(["v1", "v2"])
        build_calls = []

        def _build():
            build_calls.append(1)
            return next(responses)

        assert maint_cache.get_cached("k", _build) == "v1"
        maint_cache.invalidate("k")
        # Still v1 — a read never rebuilds, even when dirty.
        assert maint_cache.get_cached("k", _build) == "v1"
        assert build_calls == [1]
        # The background cycle is what rebuilds it.
        maint_cache.run_refresh_cycle(full=False)
        assert maint_cache.get_cached("k", _build) == "v2"

    def test_distinct_keys_are_independent(self):
        maint_cache.get_cached("a", lambda: 1)
        maint_cache.get_cached("b", lambda: 2)
        a_calls, b_calls = [], []
        assert maint_cache.get_cached("a", lambda: a_calls.append(1) or 99) == 1
        assert maint_cache.get_cached("b", lambda: b_calls.append(1) or 99) == 2
        assert a_calls == [] and b_calls == []


class TestPeek:
    def test_absent_returns_default_without_building(self):
        calls = []
        present, value = maint_cache.peek("k", lambda: calls.append(1) or "built")
        assert present is False
        assert value is None
        assert calls == [], "peek must never build, even on first boot"

    def test_absent_flags_dirty_so_loop_fills_it(self):
        maint_cache.peek("k", lambda: "built")
        rebuilt = maint_cache.run_refresh_cycle(full=False)
        assert rebuilt == ["k"]
        present, value = maint_cache.peek("k", lambda: "ignored")
        assert present is True
        assert value == "built"

    def test_present_returns_value(self):
        maint_cache.get_cached("k", lambda: "v1")
        present, value = maint_cache.peek("k", lambda: "ignored")
        assert present is True
        assert value == "v1"


class TestRefreshCycle:
    def test_full_rebuilds_every_registered_key(self):
        seq_a = iter(["a1", "a2"])
        seq_b = iter(["b1", "b2"])
        maint_cache.get_cached("a", lambda: next(seq_a))
        maint_cache.get_cached("b", lambda: next(seq_b))
        rebuilt = maint_cache.run_refresh_cycle(full=True)
        assert rebuilt == ["a", "b"]
        assert maint_cache.get_cached("a", lambda: "x") == "a2"
        assert maint_cache.get_cached("b", lambda: "x") == "b2"

    def test_non_full_rebuilds_only_dirty_keys(self):
        seq_a = iter(["a1", "a2"])
        seq_b = iter(["b1", "b2"])
        maint_cache.get_cached("a", lambda: next(seq_a))
        maint_cache.get_cached("b", lambda: next(seq_b))
        maint_cache.invalidate("a")
        rebuilt = maint_cache.run_refresh_cycle(full=False)
        assert rebuilt == ["a"]
        assert maint_cache.get_cached("a", lambda: "x") == "a2"
        assert maint_cache.get_cached("b", lambda: "x") == "b1"

    def test_build_error_keeps_last_good(self):
        state = {"fail": False}

        def _build():
            if state["fail"]:
                raise RuntimeError("boom")
            return "good"

        assert maint_cache.get_cached("k", _build) == "good"
        state["fail"] = True
        maint_cache.invalidate("k")
        # Rebuild raises; the engine logs and keeps the last-good value.
        maint_cache.run_refresh_cycle(full=False)
        assert maint_cache.get_cached("k", _build) == "good"

    def test_register_lets_full_refresh_build_untouched_key(self):
        calls = []
        maint_cache.register("k", lambda: calls.append(1) or "v")
        # No read has touched "k", but a full refresh still builds it because it
        # is registered.
        rebuilt = maint_cache.run_refresh_cycle(full=True)
        assert rebuilt == ["k"]
        assert calls == [1]


class TestInvalidate:
    def test_none_flags_all_registered_keys(self):
        seq_a = iter(["a1", "a2"])
        seq_b = iter(["b1", "b2"])
        maint_cache.get_cached("a", lambda: next(seq_a))
        maint_cache.get_cached("b", lambda: next(seq_b))
        maint_cache.invalidate()
        rebuilt = maint_cache.run_refresh_cycle(full=False)
        assert rebuilt == ["a", "b"]


class TestClear:
    def test_drops_values_and_builders(self):
        maint_cache.get_cached("k", lambda: "v")
        maint_cache.clear()
        # Builder is gone, so a full refresh rebuilds nothing.
        assert maint_cache.run_refresh_cycle(full=True) == []
        # And the next read is a cold-miss build again.
        calls = []
        assert maint_cache.get_cached("k", lambda: calls.append(1) or "v2") == "v2"
        assert calls == [1]
